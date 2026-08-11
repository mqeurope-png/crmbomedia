"""CRM-ADJUNTOS-BACKFILL — backfill metadata-only + descarga on-demand.

Cubre: el extractor de partes del payload Gmail, el dry-run sin escrituras,
la idempotencia (mensajes ya con adjuntos se saltan), y el endpoint de
descarga con sus dos rutas (disco legacy / fetch on-demand a Gmail con
refresh del attachmentId caducado).
"""
from __future__ import annotations

import base64
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.core.audit import Action
from app.db.session import get_session
from app.integrations.gmail import backfill_attachments as ba_module
from app.integrations.gmail.backfill_attachments import (
    extract_attachments_from_gmail_payload,
    run_backfill_attachments,
)
from app.main import app
from app.models.crm import (
    ORG_GOOGLE_SINGLETON_ID,
    AuditLog,
    Base,
    EmailDirection,
    EmailMessage,
    EmailMessageAttachment,
    EmailThread,
    OrgGoogleIntegration,
    User,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        admin_id = seed.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        seed.add(
            OrgGoogleIntegration(
                id=ORG_GOOGLE_SINGLETON_ID,
                google_email="mqeurope@gmail.com",
                access_token_encrypted=crypto.encrypt("fake-access"),
                refresh_token_encrypted=crypto.encrypt("fake-refresh"),
                scopes="https://www.googleapis.com/auth/gmail.send",
                token_expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                connected_at=datetime.now(UTC),
                connected_by_user_id=admin_id,
                status="active",
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_id(session: Session) -> str:
    uid = session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    assert uid
    return uid


def _seed_message(
    session: Session,
    *,
    uid: str,
    gmail_message_id: str,
    sent_at: datetime | None = None,
) -> str:
    thread = EmailThread(
        initiated_by_user_id=uid,
        gmail_thread_id=f"thr-{gmail_message_id}",
        gmail_account_user_id=uid,
        subject=f"Asunto {gmail_message_id}",
        first_message_at=sent_at or datetime(2026, 6, 1, tzinfo=UTC),
        last_message_at=sent_at or datetime(2026, 6, 1, tzinfo=UTC),
        message_count=1,
    )
    session.add(thread)
    session.flush()
    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=gmail_message_id,
        gmail_account_user_id=uid,
        direction=EmailDirection.OUTBOUND,
        from_email="info@bomedia.net",
        to_emails_json='["dest@example.com"]',
        sent_at=sent_at or datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add(message)
    session.flush()
    return message.id


def _payload_with_attachments(*atts: tuple[str, str, int]) -> dict[str, Any]:
    """(filename, attachment_id, size) → payload Gmail multipart anidado."""
    parts: list[dict[str, Any]] = [
        {"mimeType": "text/plain", "body": {"data": "aGVsbG8="}},
        # Parte técnica: attachmentId presente pero SIN filename → excluida.
        {
            "mimeType": "application/pkcs7-signature",
            "filename": "",
            "body": {"attachmentId": "att-technical", "size": 128},
        },
    ]
    for filename, att_id, size in atts:
        parts.append(
            {
                "filename": filename,
                "mimeType": "application/pdf",
                "body": {"attachmentId": att_id, "size": size},
            }
        )
    # Anidamos en multipart/mixed → multipart/alternative para ejercitar
    # el walk recursivo.
    return {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "multipart/alternative", "parts": parts}],
    }


class _FakeClient:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.attachment_errors: dict[str, Exception] = {}

    def get_message(self, message_id: str) -> dict[str, Any]:
        self.calls.append(("get_message", message_id))
        return self.messages[message_id]

    def get_attachment(
        self, *, message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        self.calls.append(("get_attachment", attachment_id))
        if attachment_id in self.attachment_errors:
            raise self.attachment_errors[attachment_id]
        marker = f"BINARY:{attachment_id}".encode()
        return {
            "data": base64.urlsafe_b64encode(marker).decode(),
            "size": len(marker),
        }


def _not_found_error() -> Exception:
    exc = RuntimeError("Gmail 404 attachment not found")
    exc.resp = type("Resp", (), {"status": 404})()  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


def test_backfill_attachments_extract_from_multipart_payload() -> None:
    payload = _payload_with_attachments(
        ("oferta.pdf", "att-1", 2048), ("logo.png", "att-2", 512)
    )
    out = extract_attachments_from_gmail_payload(payload)
    assert {a["filename"] for a in out} == {"oferta.pdf", "logo.png"}
    by_name = {a["filename"]: a for a in out}
    assert by_name["oferta.pdf"]["gmail_attachment_id"] == "att-1"
    assert by_name["oferta.pdf"]["size"] == 2048


def test_backfill_attachments_ignores_inline_without_attachment_id() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            # Body HTML sin attachmentId → NO es adjunto.
            {"mimeType": "text/html", "filename": "", "body": {"data": "eA=="}},
            # Parte con filename pero sin attachmentId → NO es adjunto.
            {
                "mimeType": "text/calendar",
                "filename": "invite.ics",
                "body": {"data": "eA==", "size": 90},
            },
            # Técnica: attachmentId sin filename → excluida.
            {
                "mimeType": "application/pkcs7-signature",
                "filename": "",
                "body": {"attachmentId": "att-x", "size": 100},
            },
        ],
    }
    assert extract_attachments_from_gmail_payload(payload) == []


# ---------------------------------------------------------------------------
# Backfill runner
# ---------------------------------------------------------------------------


def test_backfill_attachments_dry_run_no_writes(factory: sessionmaker) -> None:
    fake = _FakeClient()
    with factory() as session:
        uid = _admin_id(session)
        _seed_message(session, uid=uid, gmail_message_id="g-dry")
        session.commit()
        fake.messages["g-dry"] = {
            "payload": _payload_with_attachments(("factura.pdf", "att-f", 4096))
        }
        with patch.object(ba_module, "_client_for", return_value=fake):
            report = run_backfill_attachments(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 10).date(),
                dry_run=True,
                progress=lambda _line: None,
            )
        assert report.processed == 1
        assert report.attachments_total == 1
        assert report.total_size_bytes == 4096
        assert report.imported == 0

        rows = session.scalars(select(EmailMessageAttachment)).all()
        assert rows == []


def test_backfill_attachments_saves_metadata_only(
    factory: sessionmaker,
) -> None:
    fake = _FakeClient()
    with factory() as session:
        uid = _admin_id(session)
        _seed_message(session, uid=uid, gmail_message_id="g-meta")
        session.commit()
        fake.messages["g-meta"] = {
            "payload": _payload_with_attachments(
                ("contrato.pdf", "att-c", 1234), ("foto.jpg", "att-j", 999)
            )
        }
        with patch.object(ba_module, "_client_for", return_value=fake):
            report = run_backfill_attachments(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 10).date(),
                progress=lambda _line: None,
            )
        assert report.imported == 2
        assert report.with_attachments == 1

        rows = session.scalars(select(EmailMessageAttachment)).all()
        assert len(rows) == 2
        # Opción B: metadata sin binario — storage_path NULL, id de Gmail sí.
        assert all(r.storage_path is None for r in rows)
        assert {r.gmail_attachment_id for r in rows} == {"att-c", "att-j"}


def test_backfill_attachments_skip_already_imported(
    factory: sessionmaker,
) -> None:
    fake = _FakeClient()
    with factory() as session:
        uid = _admin_id(session)
        done_id = _seed_message(session, uid=uid, gmail_message_id="g-done")
        _seed_message(session, uid=uid, gmail_message_id="g-pending")
        session.add(
            EmailMessageAttachment(
                message_id=done_id,
                filename="ya-importado.pdf",
                mime_type="application/pdf",
                size_bytes=10,
                gmail_attachment_id="att-old",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
        fake.messages["g-pending"] = {
            "payload": _payload_with_attachments(("nuevo.pdf", "att-n", 55))
        }
        with patch.object(ba_module, "_client_for", return_value=fake):
            report = run_backfill_attachments(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 10).date(),
                progress=lambda _line: None,
            )
        # Solo el mensaje pendiente se consulta a Gmail.
        assert report.processed == 1
        assert ("get_message", "g-done") not in fake.calls
        assert ("get_message", "g-pending") in fake.calls


# ---------------------------------------------------------------------------
# Endpoint de descarga — on-demand vs disco
# ---------------------------------------------------------------------------


def _seed_attachment(
    session: Session,
    *,
    uid: str,
    gmail_message_id: str,
    storage_path: str | None,
    gmail_attachment_id: str | None,
) -> tuple[str, str]:
    message_id = _seed_message(
        session, uid=uid, gmail_message_id=gmail_message_id
    )
    att = EmailMessageAttachment(
        message_id=message_id,
        filename="informe.pdf",
        mime_type="application/pdf",
        size_bytes=17,
        storage_path=storage_path,
        gmail_attachment_id=gmail_attachment_id,
        created_at=datetime.now(UTC),
    )
    session.add(att)
    session.flush()
    return message_id, att.id


def test_attachment_download_falls_back_to_gmail_when_stored_path_null(
    client: TestClient, factory: sessionmaker
) -> None:
    fake = _FakeClient()
    with factory() as session:
        uid = _admin_id(session)
        message_id, att_id = _seed_attachment(
            session,
            uid=uid,
            gmail_message_id="g-ondemand",
            storage_path=None,
            gmail_attachment_id="att-live",
        )
        session.commit()

    with patch(
        "app.integrations.gmail.service._client_for", return_value=fake
    ):
        response = client.get(
            f"/api/email-messages/{message_id}/attachments/{att_id}/download",
            headers=auth_headers(client, "admin"),
        )
    assert response.status_code == 200, response.text
    assert response.content == b"BINARY:att-live"
    assert response.headers["content-type"].startswith("application/pdf")
    assert "informe.pdf" in response.headers["content-disposition"]

    # Auditado con source=gmail_on_demand.
    with factory() as session:
        row = session.scalar(
            select(AuditLog).where(
                AuditLog.action == Action.EMAIL_ATTACHMENT_DOWNLOADED
            )
        )
        assert row is not None
        assert "gmail_on_demand" in (row.metadata_json or "")


def test_attachment_download_uses_local_file_when_stored_path_set(
    client: TestClient, factory: sessionmaker
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "sub").mkdir()
        (root / "sub" / "informe.pdf").write_bytes(b"DISK-BYTES")
        with factory() as session:
            uid = _admin_id(session)
            message_id, att_id = _seed_attachment(
                session,
                uid=uid,
                gmail_message_id="g-disk",
                storage_path="sub/informe.pdf",
                gmail_attachment_id="att-legacy",
            )
            session.commit()

        with patch("app.api.gmail_backfill.ATTACHMENT_ROOT", root):
            response = client.get(
                f"/api/email-messages/{message_id}/attachments/{att_id}/download",
                headers=auth_headers(client, "admin"),
            )
        assert response.status_code == 200, response.text
        # Sirvió el binario del disco, NO llamó a Gmail.
        assert response.content == b"DISK-BYTES"


def test_attachment_download_refreshes_expired_attachment_id(
    client: TestClient, factory: sessionmaker
) -> None:
    """El attachmentId de Gmail caduca: 404 con el id guardado → re-pide el
    mensaje, casa la parte por filename+size, refresca el id y reintenta."""
    fake = _FakeClient()
    with factory() as session:
        uid = _admin_id(session)
        message_id, att_id = _seed_attachment(
            session,
            uid=uid,
            gmail_message_id="g-expired",
            storage_path=None,
            gmail_attachment_id="att-stale",
        )
        session.commit()

    fake.attachment_errors["att-stale"] = _not_found_error()
    fake.messages["g-expired"] = {
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "informe.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-fresh", "size": 17},
                }
            ],
        }
    }

    with patch(
        "app.integrations.gmail.service._client_for", return_value=fake
    ):
        response = client.get(
            f"/api/email-messages/{message_id}/attachments/{att_id}/download",
            headers=auth_headers(client, "admin"),
        )
    assert response.status_code == 200, response.text
    assert response.content == b"BINARY:att-fresh"

    # El id fresco queda persistido para la próxima descarga.
    with factory() as session:
        att = session.get(EmailMessageAttachment, att_id)
        assert att is not None
        assert att.gmail_attachment_id == "att-fresh"


def test_attachment_download_410_when_message_gone_from_gmail(
    client: TestClient, factory: sessionmaker
) -> None:
    """Papelera vaciada en Gmail → 410 (trade-off aceptado de la Opción B)."""

    class _GoneClient(_FakeClient):
        def get_message(self, message_id: str) -> dict[str, Any]:
            raise _not_found_error()

    fake = _GoneClient()
    with factory() as session:
        uid = _admin_id(session)
        message_id, att_id = _seed_attachment(
            session,
            uid=uid,
            gmail_message_id="g-gone",
            storage_path=None,
            gmail_attachment_id="att-gone",
        )
        session.commit()

    fake.attachment_errors["att-gone"] = _not_found_error()
    with patch(
        "app.integrations.gmail.service._client_for", return_value=fake
    ):
        response = client.get(
            f"/api/email-messages/{message_id}/attachments/{att_id}/download",
            headers=auth_headers(client, "admin"),
        )
    assert response.status_code == 410


# ---------------------------------------------------------------------------
# CRM-ADJUNTOS-UX — inline vs adjunto real + permisos por visibilidad de hilo
# ---------------------------------------------------------------------------


def _part(
    filename: str,
    att_id: str,
    size: int,
    *,
    headers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "mimeType": "image/jpeg",
        "headers": headers or [],
        "body": {"attachmentId": att_id, "size": size},
    }


def test_extract_attachments_excludes_inline_by_disposition() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            _part(
                "factura.pdf", "att-real", 2048,
                headers=[{"name": "Content-Disposition",
                          "value": "attachment; filename=factura.pdf"}],
            ),
            _part(
                "image001.jpg", "att-inline", 3072,
                headers=[{"name": "Content-Disposition", "value": "inline"}],
            ),
        ],
    }
    out = extract_attachments_from_gmail_payload(payload)
    by_name = {a["filename"]: a for a in out}
    # Ambas se devuelven (no perdemos el binario) pero marcadas.
    assert by_name["factura.pdf"]["is_inline"] is False
    assert by_name["image001.jpg"]["is_inline"] is True


def test_extract_attachments_excludes_cid_referenced_parts() -> None:
    payload = {
        "mimeType": "multipart/related",
        "parts": [
            # Sin Content-Disposition pero con Content-ID → inline (cid:).
            _part(
                "logo.png", "att-cid", 1500,
                headers=[{"name": "Content-ID", "value": "<logo@firma>"}],
            ),
            # Content-Disposition: attachment aunque tenga Content-ID → real.
            _part(
                "adjunto.png", "att-real2", 5000,
                headers=[
                    {"name": "Content-ID", "value": "<x@y>"},
                    {"name": "Content-Disposition", "value": "attachment"},
                ],
            ),
        ],
    }
    by_name = {
        a["filename"]: a
        for a in extract_attachments_from_gmail_payload(payload)
    }
    assert by_name["logo.png"]["is_inline"] is True
    assert by_name["adjunto.png"]["is_inline"] is False


def _seed_visible_attachment(
    session: Session,
    *,
    owner_id: str,
    delivered_to: str,
    gmail_message_id: str = "vis-msg",
) -> tuple[str, str]:
    """Thread INBOUND entregado a `delivered_to` (alias del comercial) con
    un adjunto real. Devuelve (message_id, attachment_id)."""
    thread = EmailThread(
        initiated_by_user_id=owner_id,
        gmail_thread_id=f"thr-{gmail_message_id}",
        gmail_account_user_id=owner_id,
        subject="Con adjunto",
        first_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        message_count=1,
    )
    session.add(thread)
    session.flush()
    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=gmail_message_id,
        gmail_account_user_id=owner_id,
        direction=EmailDirection.INBOUND,
        from_email="cliente@fuera.com",
        to_emails_json=f'["{delivered_to}"]',
        delivered_to=delivered_to,
        sent_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add(message)
    session.flush()
    att = EmailMessageAttachment(
        message_id=message.id,
        filename="informe.pdf",
        mime_type="application/pdf",
        size_bytes=17,
        storage_path=None,
        gmail_attachment_id="att-live",
        created_at=datetime.now(UTC),
    )
    session.add(att)
    session.flush()
    return message.id, att.id


def test_download_attachment_uses_thread_visibility_not_contact_owner(
    client: TestClient, factory: sessionmaker
) -> None:
    """El comercial ve el mail por su alias (delivered_to) aunque el
    contacto no sea suyo → puede descargar. (Antes fallaba por el check de
    owner del contacto.)"""
    from app.models.crm import UserEmailAlias  # noqa: PLC0415

    fake = _FakeClient()
    with factory() as session:
        admin = _admin_id(session)  # cuenta org = admin
        user = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
        session.add(
            UserEmailAlias(
                user_id=user, alias_email="norma@bomedia.net", active=True
            )
        )
        session.flush()
        message_id, att_id = _seed_visible_attachment(
            session, owner_id=admin, delivered_to="norma@bomedia.net",
        )
        session.commit()

    with patch(
        "app.integrations.gmail.service._client_for", return_value=fake
    ):
        resp = client.get(
            f"/api/email-messages/{message_id}/attachments/{att_id}/download",
            headers=auth_headers(client, "user"),
        )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"BINARY:att-live"


def test_download_attachment_403_when_user_cannot_see_thread(
    client: TestClient, factory: sessionmaker
) -> None:
    """Un comercial sin el alias del mail (no lo ve en su bandeja) → 403
    con code=attachment_not_visible y mensaje sobre el email, no el
    contacto."""
    with factory() as session:
        admin = _admin_id(session)
        # El user NO tiene ningún alias activo → no ve el hilo (pero sí
        # pasa require_user, a diferencia de viewer).
        message_id, att_id = _seed_visible_attachment(
            session, owner_id=admin, delivered_to="otra@bomedia.net",
        )
        session.commit()

    resp = client.get(
        f"/api/email-messages/{message_id}/attachments/{att_id}/download",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["detail"]["code"] == "attachment_not_visible"
    assert "email" in body["detail"]["detail"].lower()
