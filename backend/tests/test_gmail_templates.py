"""Mini-PR Gmail Templates — backend tests for /api/emails/gmail-templates.

Mocks `app.integrations.gmail.client.GmailClient` to skip the real
Google API. Confirms:

- 200 + parsed payload when Gmail returns drafts with `^smartlabel_
  canned_response`.
- 200 + [] when the user has no Gmail integration (graceful, no
  banner spam).
- 403 when the integration row exists but lacks `gmail.send` scope
  (paridad con /aliases).
"""
from __future__ import annotations

import base64
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    User,
    UserGoogleIntegration,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_id(factory: sessionmaker, role: UserRole) -> str:
    with factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail(factory: sessionmaker, *, user_id: str, scopes: str) -> None:
    with factory() as session:
        session.add(
            UserGoogleIntegration(
                user_id=user_id,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=scopes,
                connected_at=datetime.now(UTC),
            )
        )
        session.commit()


def _build_raw_email(*, subject: str, body_html: str) -> str:
    """Construye un RFC822 mínimo en formato urlsafe_b64 que `service.
    list_gmail_templates` parsea con email.message_from_bytes."""
    raw = (
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{body_html}"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


class _FakeGmailClient:
    """Stand-in. Modelo:

    - `draft-tpl1`: template puro ("Pressupost sol·licitat"), pasa
      la heurística.
    - `draft-tpl2`: template ("Adquisición equipos FLUX"), también pasa.
    - `draft-reply`: borrador de respuesta con subject "Re: Tu
      consulta" — NO debería contar como template.
    - `draft-quoted`: borrador con snippet quoted "On Mon, Jun 16
      Bart wrote:" — NO debería contar.
    - `draft-gt`: snippet empieza con `> texto citado` — NO debería
      contar.

    Todos los drafts vienen con `labelIds=["DRAFT"]` para reflejar el
    hallazgo: la API NO expone qué es template.
    """

    def __init__(self, *_a, **_kw) -> None:
        pass

    def list_draft_templates(
        self, *, query: str | None = None, max_results: int = 30
    ) -> list[dict]:
        _ = query
        _ = max_results
        return [
            {"id": "draft-tpl1"},
            {"id": "draft-tpl2"},
            {"id": "draft-reply"},
            {"id": "draft-quoted"},
            {"id": "draft-gt"},
        ]

    def get_draft_metadata(self, draft_id: str) -> dict:
        catalog = {
            "draft-tpl1": {
                "snippet": "Hola, adjuntamos el presupuesto solicitado",
                "internalDate": "1718500000000",  # más reciente
                "labelIds": ["DRAFT"],
                "threadId": "thr-1",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Pressupost sol·licitat"},
                    ]
                },
            },
            "draft-tpl2": {
                "snippet": "Te confirmo la adquisición de equipos",
                "internalDate": "1718000000000",  # más antiguo
                "labelIds": ["DRAFT"],
                "threadId": "thr-2",
                "payload": {
                    "headers": [
                        {
                            "name": "Subject",
                            "value": "Adquisición equipos FLUX",
                        },
                    ]
                },
            },
            "draft-reply": {
                "snippet": "Gracias por la información",
                "internalDate": "1719000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-3",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Re: Tu consulta"}]
                },
            },
            "draft-quoted": {
                "snippet": "On Mon, Jun 16 2026 at 10:00, Bart wrote: hola",
                "internalDate": "1719500000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-4",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Borrador con quote"}
                    ]
                },
            },
            "draft-gt": {
                "snippet": "> Texto citado por completo",
                "internalDate": "1720000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-5",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Reply gt"}]
                },
            },
        }
        return {"id": draft_id, "message": catalog[draft_id]}

    def get_draft_template(self, draft_id: str) -> dict:
        # Match el subject del metadata para no confundir al parser
        # cuando el código intenta llenar el subject desde el raw.
        bodies = {
            "draft-tpl1": (
                "Pressupost sol·licitat",
                "<p>Hola, adjuntamos el presupuesto solicitado.</p>",
            ),
            "draft-tpl2": (
                "Adquisición equipos FLUX",
                "<p>Te confirmo la adquisición de equipos.</p>",
            ),
        }
        subject, body_html = bodies[draft_id]
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "internalDate": "1718500000000",
                "raw": _build_raw_email(
                    subject=subject,
                    body_html=body_html,
                ),
            },
        }


def test_body_html_populated_from_raw_format(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin que body_html NO sale vacío para los templates que pasan
    la heurística. Pre-fix, get_draft_template pedía format=full que
    devuelve payload structured sin `raw`, así que el parseo MIME
    nunca encontraba el body y todos los items salían con
    body_html="". Tras pedir format=raw el cliente devuelve el MIME
    completo y el parseo extrae el cuerpo."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for item in body:
        assert item["body_html"], (
            f"body_html vacío para {item['id']} subject={item['subject']!r}"
        )
    # Sanity sobre el body parseado: el HTML del template 1 debe
    # contener el texto distintivo del fake.
    by_id = {item["id"]: item for item in body}
    assert "presupuesto" in by_id["draft-tpl1"]["body_html"].lower()


def test_heuristic_keeps_templates_drops_replies_and_quoted(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 drafts:
    - 2 templates puros → quedan.
    - 1 con subject 'Re: …' → fuera.
    - 1 con snippet 'On … wrote:' → fuera.
    - 1 con snippet '> citado' → fuera.

    Resultado: solo los 2 templates, ordenados por updated_at DESC."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [item["id"] for item in body]
    # Solo los 2 templates, en orden updated_at desc (tpl1 más reciente).
    assert ids == ["draft-tpl1", "draft-tpl2"], body
    assert body[0]["subject"] == "Pressupost sol·licitat"
    assert body[1]["subject"] == "Adquisición equipos FLUX"


def test_debug_returns_all_drafts_with_summary_and_is_template_flag(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo debug devuelve TODOS los drafts marcados con
    `is_template` boolean + entrada `_summary` con counters."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates?debug=true",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body[0]
    assert summary["id"] == "_summary"
    assert summary["total_drafts"] == 5
    assert summary["detected_templates"] == 2

    drafts = body[1:]
    by_id = {item["id"]: item for item in drafts}
    assert by_id["draft-tpl1"]["is_template"] is True
    assert by_id["draft-tpl2"]["is_template"] is True
    assert by_id["draft-reply"]["is_template"] is False
    assert by_id["draft-quoted"]["is_template"] is False
    assert by_id["draft-gt"]["is_template"] is False
    # label_ids siempre ["DRAFT"] (refleja el hallazgo Bart).
    assert by_id["draft-tpl1"]["label_ids"] == ["DRAFT"]


def test_gmail_templates_returns_empty_when_not_connected(
    client: TestClient,
) -> None:
    """User sin integration row → no banner ni 500: lista vacía."""
    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_gmail_templates_403_when_scope_missing(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Integration sin gmail.send → 403 con detalle (mismo patrón que
    /aliases)."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403
    assert "gmail.send" in resp.json()["detail"]


# -- importador one-shot a email_templates ---------------------------


class _FakeGmailImportClient:
    """Fake distinto del usado por list/heuristic. Modelo: 3 drafts:
    1 con prefijo [TPL] (debería importarse), 1 sin prefijo, 1 con
    prefijo [TPL] pero name duplicado tras stripped (importable pero
    salta en re-run para test idempotencia)."""

    def __init__(self, *_a, **_kw) -> None:
        self.deleted: list[str] = []

    def list_all_drafts(self) -> list[str]:
        return ["draft-tpl-a", "draft-no-tpl", "draft-tpl-b"]

    def get_draft_metadata(self, draft_id: str) -> dict:
        subjects = {
            "draft-tpl-a": "[TPL] Bienvenida CRM",
            "draft-no-tpl": "Re: tu consulta",
            "draft-tpl-b": "[TPL] Adquisición equipos",
        }
        return {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [{"name": "Subject", "value": subjects[draft_id]}]
                },
            },
        }

    def get_draft_template(self, draft_id: str) -> dict:
        bodies = {
            "draft-tpl-a": (
                "[TPL] Bienvenida CRM",
                "<p>Hola, bienvenido al CRM.</p>",
            ),
            "draft-tpl-b": (
                "[TPL] Adquisición equipos",
                "<p>Confirmamos compra.</p>",
            ),
        }
        subject, body_html = bodies[draft_id]
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "raw": _build_raw_email(subject=subject, body_html=body_html),
            },
        }

    def delete_draft(self, draft_id: str) -> None:
        self.deleted.append(draft_id)


def test_import_creates_email_templates_in_dedicated_folder(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    resp = client.post(
        "/api/email-templates/import-gmail",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["imported"] == 2
    assert summary["skipped"] == 0
    assert summary["errors"] == 0
    assert summary["deleted"] == 0
    assert summary["tpl_drafts_found"] == 2
    assert summary["total_drafts_scanned"] == 3

    # Sanity: las plantillas viven en la folder dedicada.
    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
        EmailTemplateFolder,
    )

    with session_factory() as session:
        folder = session.scalar(
            _select(EmailTemplateFolder).where(
                EmailTemplateFolder.name == "Gmail (importadas)"
            )
        )
        assert folder is not None
        rows = list(
            session.scalars(
                _select(EmailTemplate).where(
                    EmailTemplate.folder_id == folder.id
                )
            )
        )
        names = sorted(r.name for r in rows)
        assert names == ["Adquisición equipos", "Bienvenida CRM"]
        # body_html no vacío, source/owner OK.
        for row in rows:
            assert row.body_html
            assert row.is_global is True
            assert row.owner_user_id == uid


def test_import_is_idempotent(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Segundo run no duplica — counters reflejan skipped."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    client.post(
        "/api/email-templates/import-gmail",
        headers=auth_headers(client, "admin"),
    )
    resp = client.post(
        "/api/email-templates/import-gmail",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["imported"] == 0
    assert summary["skipped"] == 2


def test_import_delete_after_removes_drafts(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    resp = client.post(
        "/api/email-templates/import-gmail?delete_after=true",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["imported"] == 2
    assert summary["deleted"] == 2
    assert sorted(fake.deleted) == ["draft-tpl-a", "draft-tpl-b"]


def test_import_requires_admin(client: TestClient) -> None:
    for role in ("manager", "user"):
        resp = client.post(
            "/api/email-templates/import-gmail",
            headers=auth_headers(client, role),
        )
        assert resp.status_code == 403, f"{role}: {resp.text}"
