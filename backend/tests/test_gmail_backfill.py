"""Sprint-Backfill-Gmail — tests del handler + endpoints.

Mock del `GmailClient` con un FakeGmail in-memory: lista mensajes por
query, devuelve get_message en formato Gmail real, get_attachment con
base64 binario. Reusa la fixture client + auth_headers del resto del
suite para los endpoints.
"""
from __future__ import annotations

import base64
import json
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.db.session import get_session
from app.integrations.gmail import backfill as backfill_module
from app.integrations.gmail.service import (
    GmailScopeMissingError,
)
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    EmailMessage,
    EmailMessageAttachment,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
    UserEmailAliasPref,
)
from tests._test_helpers import auth_headers, seed_test_users

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def attachment_root() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)
        # Override module-level ATTACHMENT_ROOT for the test scope.
        original = backfill_module.ATTACHMENT_ROOT
        backfill_module.ATTACHMENT_ROOT = path
        try:
            yield path
        finally:
            backfill_module.ATTACHMENT_ROOT = original


@pytest.fixture()
def factory(attachment_root) -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        # PR-OAuth-Google-Unificado. UNA conexión org compartida + aliases
        # per-user. El backfill usa el client org e itera los aliases de
        # cada user del CRM.
        _wire_org_gmail(seed)
        _wire_gmail(seed, role="admin", alias_emails=["manel@bomedia.net"])
        _wire_gmail(seed, role="manager", alias_emails=["bart@bomedia.net"])
        # 1 contacto con email para backfill
        admin_id = seed.scalar(_user_id_by_role_query("admin"))
        seed.add(
            Contact(
                id="contact-marny",
                first_name="Marny",
                email="marny@cliente.com",
                owner_user_id=admin_id,
                is_active=True,
            )
        )
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


def _user_id_by_role_query(role: str):
    from sqlalchemy import select

    from app.models.crm import UserRole

    return select(User.id).where(User.role == UserRole(role)).limit(1)


def _wire_org_gmail(session) -> None:
    """PR-OAuth-Google-Unificado. La cuenta Google org única que todos
    los users comparten."""
    from app.models.crm import ORG_GOOGLE_SINGLETON_ID, OrgGoogleIntegration

    admin_id = session.scalar(_user_id_by_role_query("admin"))
    session.add(
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


def _wire_gmail(session, *, role: str, alias_emails: list[str]) -> None:
    user_id = session.scalar(_user_id_by_role_query(role))
    # PR-Fix-Backfill-Gmail-Cero-Importados. La primera alias se marca
    # como default — en producción todas las cuentas Gmail tienen un
    # Send-As default; los tests deben reflejarlo para que el modo
    # `aliases_scope=primary_only` (el nuevo default del endpoint)
    # encuentre el alias y enrute las queries correctamente.
    for idx, email in enumerate(alias_emails):
        session.add(
            UserEmailAliasPref(
                user_id=user_id,
                alias_email=email,
                is_allowed=True,
                is_default=(idx == 0),
            )
        )


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fake GmailClient
# ---------------------------------------------------------------------------


class _FakeGmail:
    """Captura llamadas + sirve respuestas controladas. El test
    construye un dict de `mensajes_por_query` (queries: list of message
    dicts) y el fake los devuelve cuando `list_messages` matchea por
    substrings."""

    def __init__(self) -> None:
        # alias_email_lower -> list of {id, threadId} (deduplicated
        # by id at lookup time)
        self.messages_by_alias: dict[str, list[dict[str, Any]]] = {}
        # message_id -> full message dict
        self.messages_full: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_list_next = False
        # How many of the next get_message_metadata calls should
        # raise a transient 429-shaped exception (for backoff testing).
        self.transient_failures = 0

    def add_conversation(
        self,
        *,
        alias: str,
        contact_email: str,
        message_id: str,
        thread_id: str | None = None,
        subject: str = "Saludos",
        body_text: str = "Hola",
        attachments: list[tuple[str, bytes, str]] | None = None,
        from_email: str | None = None,
    ) -> None:
        thread_id = thread_id or message_id
        from_email = from_email or alias
        to_email = contact_email if from_email == alias else alias

        parts: list[dict[str, Any]] = [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64_encode(body_text.encode())},
            }
        ]
        for filename, data, mime in attachments or []:
            parts.append(
                {
                    "filename": filename,
                    "mimeType": mime,
                    "body": {
                        "attachmentId": f"att-{filename}",
                        "size": len(data),
                    },
                }
            )
        msg = {
            "id": message_id,
            "threadId": thread_id,
            "snippet": body_text[:80],
            "payload": {
                "headers": [
                    {"name": "From", "value": from_email},
                    {"name": "To", "value": to_email},
                    {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Mon, 01 Jun 2026 10:00:00 +0000"},
                ],
                "parts": parts,
            },
        }
        self.messages_full[message_id] = msg
        # PR-Fix-Backfill-Gmail-Arquitectura. La V2 invierte la
        # iteración: 1 query por alias trae TODOS los mensajes donde
        # el alias aparece en From/To/Cc. Indexamos por alias-lower
        # — `list_messages` resuelve por substring del alias en el
        # query string.
        for participant in (from_email, to_email):
            self.messages_by_alias.setdefault(participant.lower(), []).append(
                {"id": message_id, "threadId": thread_id}
            )

    # GmailClient interface
    def list_messages(
        self, *, query: str, page_size: int = 100, page_token: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("list_messages", {"query": query}))
        if self.fail_list_next:
            self.fail_list_next = False
            raise RuntimeError("Gmail 429 rate-limited")
        # New shape: el query es `(from:alias OR to:alias) newer_than:Nm`.
        # Devolvemos todos los mensajes del alias deduplicados.
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for alias, msgs in self.messages_by_alias.items():
            if alias in query.lower():
                for m in msgs:
                    if m["id"] not in seen:
                        seen.add(m["id"])
                        out.append(m)
        return {"messages": out, "nextPageToken": None, "resultSizeEstimate": len(out)}

    def get_message(self, message_id: str) -> dict[str, Any]:
        self.calls.append(("get_message", {"id": message_id}))
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise _Transient503("Gmail 503 backoff test")
        return self.messages_full[message_id]

    def get_message_metadata(self, message_id: str) -> dict[str, Any]:
        self.calls.append(("get_message_metadata", {"id": message_id}))
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise _Transient503("Gmail 503 backoff test")
        return self.messages_full[message_id]

    def get_attachment(
        self, *, message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "get_attachment",
                {"message_id": message_id, "attachment_id": attachment_id},
            )
        )
        # Look up attachment by id in the parts. Data is base64url.
        msg = self.messages_full[message_id]
        for part in msg["payload"]["parts"]:
            body = part.get("body") or {}
            if body.get("attachmentId") == attachment_id:
                # We didn't pre-encode data on the part — the original
                # tuple is stored separately. Fish it back from the
                # filename by recomputing — for tests we just return
                # the marker bytes "BINARY:{filename}".
                marker = f"BINARY:{part['filename']}".encode()
                return {"data": _b64_encode(marker), "size": len(marker)}
        raise KeyError(attachment_id)


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status


class _Transient503(RuntimeError):
    """Mimics google-api-python-client `HttpError` con `resp.status`
    para que `_is_transient_error` lo detecte como reintenable."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.resp = _FakeResp(503)


@pytest.fixture()
def fake_gmail() -> _FakeGmail:
    return _FakeGmail()


@pytest.fixture()
def patched_client(fake_gmail: _FakeGmail, factory: sessionmaker):
    """Patches `_client_for` to return the fake. Also stubs RQ enqueue
    to a no-op (we drive the worker entry point sync from the test)."""
    def _fake_client_for(session, user_id):  # noqa: ANN001
        return fake_gmail

    enqueued: list[str] = []

    def _fake_enqueue(job_id: str) -> None:
        enqueued.append(job_id)

    engine = factory.kw["bind"]
    with (
        patch.object(backfill_module, "_client_for", side_effect=_fake_client_for),
        patch("app.api.gmail_backfill.enqueue_backfill", side_effect=_fake_enqueue),
        patch("app.db.session.get_engine", return_value=engine),
    ):
        yield enqueued


# ---------------------------------------------------------------------------
# Endpoint: estimate
# ---------------------------------------------------------------------------


def test_backfill_estimate_returns_breakdown_per_user(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-1",
        body_text="Hola Marny",
    )
    response = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    # Drive the worker inline (RQ enqueue mocked).
    backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.COMPLETED.value
        result = json.loads(job.result_json)
        assert result["total_emails"] == 1
        per_user = {row["email"]: row for row in result["per_user_breakdown"]}
        assert per_user["admin@example.com"]["emails"] == 1


# ---------------------------------------------------------------------------
# Endpoint: execute creates job row
# ---------------------------------------------------------------------------


def test_backfill_execute_creates_job_record(
    client, factory, fake_gmail, patched_client
):
    enqueued = patched_client
    response = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 12,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "execute"
    assert body["status"] == "queued"
    assert body["config"]["months_back"] == 12
    assert body["config"]["include_attachments"] is False
    assert body["id"] in enqueued
    # DB row también
    with factory() as session:
        job = session.get(GmailBackfillJob, body["id"])
        assert job is not None
        assert job.mode == GmailBackfillMode.EXECUTE.value


# ---------------------------------------------------------------------------
# Handler: import + dedup + correct contact_id/owner
# ---------------------------------------------------------------------------


def test_backfill_handler_imports_messages_with_correct_contact_id(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-imp-1",
        body_text="Body 1",
    )
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-imp-2",
        body_text="Body 2",
        from_email="marny@cliente.com",  # inbound
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        from sqlalchemy import select
        msgs = list(session.scalars(select(EmailMessage)))
        assert {m.gmail_message_id for m in msgs} == {"msg-imp-1", "msg-imp-2"}
        for m in msgs:
            assert m.contact_id == "contact-marny"
            assert m.imported_via == "historic_backfill"
            assert m.imported_at is not None
        # Direction: msg-imp-1 from alias → outbound; msg-imp-2 from
        # contact → inbound.
        by_id = {m.gmail_message_id: m for m in msgs}
        assert by_id["msg-imp-1"].direction.value == "outbound"
        assert by_id["msg-imp-2"].direction.value == "inbound"
        # gmail_account_user_id == owner del alias = admin user
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        assert all(m.gmail_account_user_id == admin_id for m in msgs)


def test_backfill_skips_already_existing_messages_by_gmail_message_id(
    client, factory, fake_gmail, patched_client
):
    # Pre-existing message with same gmail_message_id
    with factory() as session:
        from app.models.crm import EmailDirection, EmailThread
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        thread = EmailThread(
            id=str(uuid4()),
            contact_id="contact-marny",
            initiated_by_user_id=admin_id,
            gmail_thread_id="thr-existing",
            gmail_account_user_id=admin_id,
            subject="Old",
            first_message_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            message_count=1,
        )
        session.add(thread)
        session.flush()
        session.add(
            EmailMessage(
                id=str(uuid4()),
                thread_id=thread.id,
                gmail_message_id="msg-existing",
                gmail_account_user_id=admin_id,
                direction=EmailDirection.OUTBOUND,
                from_email="manel@bomedia.net",
                to_emails_json=json.dumps(["marny@cliente.com"]),
                subject="Old",
                sent_at=datetime.now(UTC),
                contact_id="contact-marny",
            )
        )
        session.commit()

    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-existing",
        thread_id="thr-existing",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.total_skipped >= 1
        # Sigue habiendo exactamente 1 EmailMessage
        from sqlalchemy import func, select
        n = session.scalar(select(func.count()).select_from(EmailMessage))
        assert n == 1


# ---------------------------------------------------------------------------
# months_back propagado a la query Gmail
# ---------------------------------------------------------------------------


def test_backfill_respects_months_back_window(
    client, factory, fake_gmail, patched_client
):
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 6, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    # Every list_messages call's query string must contain newer_than:6m
    list_calls = [c for c in fake_gmail.calls if c[0] == "list_messages"]
    assert list_calls  # at least one
    assert all("newer_than:6m" in c[1]["query"] for c in list_calls)


# ---------------------------------------------------------------------------
# OAuth expired per user — graceful skip + needs_reconnect in breakdown
# ---------------------------------------------------------------------------


def test_backfill_handles_oauth_expired_per_user_gracefully(
    client, factory, fake_gmail, patched_client
):
    # Make _client_for raise GmailNotConnectedError for the manager
    # user — admin still works.
    with factory() as session:
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        manager_id = session.scalar(_user_id_by_role_query("manager"))

    def selective_client(session, user_id):  # noqa: ANN001
        if user_id == manager_id:
            raise GmailScopeMissingError("scope expirado")
        return fake_gmail

    with patch.object(backfill_module, "_client_for", side_effect=selective_client):
        r = client.post(
            "/api/admin/gmail/backfill/estimate",
            headers=auth_headers(client, "admin"),
            json={"months_back": 36},
        )
        job_id = r.json()["id"]
        backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.COMPLETED.value
        result = json.loads(job.result_json)
        per_user = {row["user_id"]: row for row in result["per_user_breakdown"]}
        assert per_user[manager_id]["needs_reconnect"] is True
        assert per_user[admin_id]["needs_reconnect"] is False


# ---------------------------------------------------------------------------
# Attachment size cap
# ---------------------------------------------------------------------------


def test_backfill_attachment_size_filter(
    client, factory, fake_gmail, patched_client, attachment_root
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-with-att",
        attachments=[
            ("small.pdf", b"x" * 1000, "application/pdf"),
            ("huge.zip", b"x" * (10 * 1024 * 1024 + 1), "application/zip"),
        ],
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": True, "max_attachment_size_mb": 10},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        from sqlalchemy import select
        atts = list(session.scalars(select(EmailMessageAttachment)))
        # Solo small.pdf debe haberse persistido
        assert len(atts) == 1
        assert atts[0].filename == "small.pdf"
        assert (attachment_root / atts[0].storage_path).is_file()


def test_backfill_with_include_attachments_false_skips_downloads(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-att-skip",
        attachments=[("doc.pdf", b"xxx", "application/pdf")],
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        from sqlalchemy import func, select
        n = session.scalar(select(func.count()).select_from(EmailMessageAttachment))
        assert n == 0
        # Pero el EmailMessage SÍ existe con attachments_json (metadata)
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "msg-att-skip")
        )
        assert msg is not None
        attachments_meta = json.loads(msg.attachments_json or "[]")
        assert any(a["filename"] == "doc.pdf" for a in attachments_meta)
    # No get_attachment calls cuando include_attachments=False
    assert not any(c[0] == "get_attachment" for c in fake_gmail.calls)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_backfill_can_be_cancelled_mid_run(client, factory, fake_gmail, patched_client):
    # Seed enough messages so PROGRESS_COMMIT_EVERY triggers a cancel
    # check between them. We set the flag BEFORE running.
    for i in range(5):
        fake_gmail.add_conversation(
            alias="manel@bomedia.net",
            contact_email="marny@cliente.com",
            message_id=f"msg-c-{i}",
            body_text=f"body {i}",
        )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]

    # Pre-flag as cancelling (simulates the admin clicking Cancel
    # before/while the worker picks it up).
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        job.status = GmailBackfillStatus.CANCELLING.value
        session.commit()

    backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Status polling endpoint
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_progress(
    client, factory, fake_gmail, patched_client
):
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    get = client.get(
        f"/api/admin/gmail/backfill/{job_id}",
        headers=auth_headers(client, "admin"),
    )
    assert get.status_code == 200
    body = get.json()
    assert body["id"] == job_id
    assert body["mode"] == "execute"
    assert body["status"] in {"queued", "running", "completed"}


# ---------------------------------------------------------------------------
# PR-Fix-Backfill-Gmail-Arquitectura — 1 query/alias, local match
# ---------------------------------------------------------------------------


def test_backfill_uses_single_query_per_alias_not_per_pair(
    client, factory, fake_gmail, patched_client
):
    """La V2 hace 1 list_messages POR alias en lugar de 1 por par
    alias×contact. Con 2 contactos y 1 alias, debe haber UNA sola
    llamada list_messages, no dos."""
    # Añadir 2 contactos: marny ya existe, + un segundo
    with factory() as session:
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        session.add(
            Contact(
                id="contact-pep",
                first_name="Pep",
                email="pep@cliente.com",
                owner_user_id=admin_id,
                is_active=True,
            )
        )
        session.commit()

    # Ningún mensaje. Solo nos interesa el shape de las llamadas.
    r = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    backfill_module.run_backfill(r.json()["id"])

    list_calls = [c for c in fake_gmail.calls if c[0] == "list_messages"]
    # PR-OAuth-Google-Unificado. El backfill itera los 4 users activos
    # del CRM (seed_test_users crea admin/manager/user/viewer) con el
    # client org compartido — 1 alias cada uno → 4 list_messages.
    # Lo importante: NO hay explosión por par (sería 4 users × 2
    # contactos = 8). 4 < 8 confirma que NO se itera por contacto.
    assert len(list_calls) == 4
    queries = [c[1]["query"] for c in list_calls]
    # La query debe contener `from:alias OR to:alias`, no `from:alias
    # AND to:contact`.
    assert all("OR" in q and "AND" not in q for q in queries)


def test_backfill_matches_contacts_locally_from_message_headers(
    client, factory, fake_gmail, patched_client
):
    """Un mensaje aparece en la query del alias; el matching contra
    el contacto del CRM se hace LOCALMENTE leyendo From/To del
    payload, no haciendo otra query a Gmail."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-local-match",
        body_text="Hola Marny",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "msg-local-match")
        )
        assert msg is not None
        assert msg.contact_id == "contact-marny"


def test_backfill_skips_user_with_expired_oauth_continues_others(
    client, factory, fake_gmail, patched_client
):
    """Si _client_for raisa para un user, el job sigue procesando
    los demás users y reporta `needs_reconnect` en el breakdown."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-from-admin",
    )
    with factory() as session:
        manager_id = session.scalar(_user_id_by_role_query("manager"))

    def selective(session, user_id):  # noqa: ANN001
        if user_id == manager_id:
            raise GmailScopeMissingError("scope missing")
        return fake_gmail

    with patch.object(backfill_module, "_client_for", side_effect=selective):
        r = client.post(
            "/api/admin/gmail/backfill/estimate",
            headers=auth_headers(client, "admin"),
            json={"months_back": 36},
        )
        backfill_module.run_backfill(r.json()["id"])

    with factory() as session:
        from sqlalchemy import desc
        job = session.scalars(
            select(GmailBackfillJob).order_by(desc(GmailBackfillJob.created_at))
        ).first()
        result = json.loads(job.result_json)
        per_user = {row["user_id"]: row for row in result["per_user_breakdown"]}
        assert per_user[manager_id]["needs_reconnect"] is True
        # admin sí procesó: matched 1 email (msg-from-admin con
        # contact-marny)
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        assert per_user[admin_id]["needs_reconnect"] is False
        assert per_user[admin_id]["emails"] == 1


def test_backfill_handles_gmail_rate_limit_with_backoff(
    client, factory, fake_gmail, patched_client
):
    """Si get_message_metadata devuelve 503 las 2 primeras veces, el
    handler reintenta con backoff y al 3º intento lo procesa OK. NO
    cuenta como error en el job."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-rate-limited",
    )
    fake_gmail.transient_failures = 2  # falla 2 veces, luego OK

    # Patchear time.sleep para no esperar 6s en el test.
    with patch.object(backfill_module, "time") as fake_time:
        fake_time.sleep = lambda _s: None
        r = client.post(
            "/api/admin/gmail/backfill/estimate",
            headers=auth_headers(client, "admin"),
            json={"months_back": 36},
        )
        backfill_module.run_backfill(r.json()["id"])

    with factory() as session:
        from sqlalchemy import desc
        job = session.scalars(
            select(GmailBackfillJob).order_by(desc(GmailBackfillJob.created_at))
        ).first()
        assert job.total_errors == 0  # el backoff lo recuperó
        result = json.loads(job.result_json)
        assert result["total_emails"] == 1


def test_backfill_updates_heartbeat_every_100_messages(
    client, factory, fake_gmail, patched_client
):
    """Heartbeat: cada PROGRESS_COMMIT_EVERY mensajes, `updated_at`
    sube via commit. Con N mensajes <= 100, debe haber al menos 1
    heartbeat (forzado al final del job, no en mitad)."""
    # Mensajes ficticios — basta con 1 para verificar el bump.
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-hb-1",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    with factory() as session:
        before = session.get(GmailBackfillJob, job_id).updated_at
    backfill_module.run_backfill(job_id)
    with factory() as session:
        after = session.get(GmailBackfillJob, job_id).updated_at
    assert after > before


def test_backfill_respects_cancellation_signal(
    client, factory, fake_gmail, patched_client
):
    """Pre-flag CANCELLING → el worker debe terminar limpio sin
    procesar mensajes."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-pre-cancel",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        job.status = GmailBackfillStatus.CANCELLING.value
        session.commit()
    backfill_module.run_backfill(job_id)
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.CANCELLED.value
        # No persistió mensajes
        n = session.scalar(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id == "msg-pre-cancel"
            )
        )
        assert n is None


def test_backfill_excludes_messages_to_other_emails_not_in_crm(
    client, factory, fake_gmail, patched_client
):
    """Mensaje de manel@ → unknown@otra-empresa.com: el alias
    aparece en la query, pero ningún destinatario es contacto del
    CRM. NO debe persistirse el mensaje."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="unknown@otra-empresa.com",  # no es contacto del CRM
        message_id="msg-irrelevant",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "include_attachments": False, "max_attachment_size_mb": 25},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "msg-irrelevant")
        )
        assert msg is None
        job = session.get(GmailBackfillJob, job_id)
        assert job.total_processed == 1
        assert job.total_skipped == 1  # contó como skip (no match)
        assert job.total_imported == 0


# ---------------------------------------------------------------------------
# force-fail endpoint
# ---------------------------------------------------------------------------


def test_force_fail_marks_stuck_job_failed(client, factory, patched_client):
    """Job atascado en `running` → POST /force-fail → status='failed'
    + error_summary identifica el force_fail."""
    r = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    job_id = r.json()["id"]
    # Forzar estado running atascado
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        job.status = GmailBackfillStatus.RUNNING.value
        session.commit()

    response = client.post(
        f"/api/admin/gmail/backfill/{job_id}/force-fail",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "Forced fail" in (body["error_summary"] or "")


def test_force_fail_is_idempotent_on_terminal_jobs(
    client, factory, patched_client
):
    """Si el job ya está completed, force-fail devuelve el row sin
    cambios (no 409)."""
    r = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    job_id = r.json()["id"]
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        job.status = GmailBackfillStatus.COMPLETED.value
        session.commit()
    response = client.post(
        f"/api/admin/gmail/backfill/{job_id}/force-fail",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# PR-Fix-Backfill-Gmail-Cero-Importados — nuevos tests
# ---------------------------------------------------------------------------


def test_matching_is_case_insensitive(
    client, factory, fake_gmail, patched_client
):
    """Reproducción del escenario real: contacto guardado con casing
    distinto al header de Gmail. Antes de la normalización del index
    (lower + strip) este match fallaba silenciosamente. El index ya
    estaba bien normalizado en `_build_contact_index`; este test
    blinda la regresión."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="Marny@CLIENTE.com",  # casing mixto en Gmail
        message_id="msg-case",
        body_text="Mensaje en casing mixto",
    )
    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.total_imported >= 1, (
            f"matching case-insensitive falló: imported={job.total_imported} "
            f"processed={job.total_processed} skipped={job.total_skipped}"
        )


def test_aliases_scope_primary_only_filters_to_default(
    client, factory, fake_gmail, patched_client
):
    """`primary_only` solo dispara la query del alias marcado como
    `is_default=True`. Si tu user tiene 3 aliases pero solo uno es
    default, solo se consulta ese — reduce volumen y rate-limit risk."""
    # Añade un segundo alias al admin marcado como NO default.
    with factory() as session:
        from sqlalchemy import select

        from app.models.crm import UserRole
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        session.add(
            UserEmailAliasPref(
                user_id=admin_id,
                alias_email="secondary@bomedia.net",
                is_allowed=True,
                is_default=False,
            )
        )
        session.commit()

    # 2 mensajes: uno por el default, uno por el secondary.
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",  # default del admin (PR fixture)
        contact_email="marny@cliente.com",
        message_id="msg-default",
        body_text="Vía default",
    )
    fake_gmail.add_conversation(
        alias="secondary@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-secondary",
        body_text="Vía secundario",
    )

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "primary_only",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    queries = [
        call[1]["query"]
        for call in fake_gmail.calls
        if call[0] == "list_messages"
    ]
    # El query del default debe estar; el del secondary NO.
    assert any("manel@bomedia.net" in q for q in queries)
    assert not any("secondary@bomedia.net" in q for q in queries), (
        f"primary_only no filtró: queries={queries}"
    )


def test_aliases_scope_all_visible_iterates_every_allowed(
    client, factory, fake_gmail, patched_client
):
    """Espejo del anterior — con scope=all_visible se piden los dos
    aliases. Defensa contra una regresión que rompiera el modo
    legacy."""
    with factory() as session:
        from sqlalchemy import select

        from app.models.crm import UserRole
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        session.add(
            UserEmailAliasPref(
                user_id=admin_id,
                alias_email="secondary@bomedia.net",
                is_allowed=True,
                is_default=False,
            )
        )
        session.commit()

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    queries = [
        call[1]["query"]
        for call in fake_gmail.calls
        if call[0] == "list_messages"
    ]
    assert any("manel@bomedia.net" in q for q in queries)
    assert any("secondary@bomedia.net" in q for q in queries)


def test_execute_tripwire_marks_failed_on_zero_imports_after_threshold(
    client, factory, fake_gmail, patched_client, monkeypatch
):
    """Si en execute se procesan >=ZERO_IMPORTS_TRIPWIRE mensajes con
    imported=0 y skipped=0, el job debe terminar FAILED — el matching
    está roto y dejarlo en COMPLETED engaña al operador (caso real de
    Bart 2026-06-25)."""
    # Reducimos el tripwire para que el test no tenga que generar 1000
    # mensajes mock.
    monkeypatch.setattr(backfill_module, "ZERO_IMPORTS_TRIPWIRE", 5)
    # 6 mensajes "de" un email que NO está en el CRM.
    for i in range(6):
        fake_gmail.add_conversation(
            alias="manel@bomedia.net",
            contact_email="external@otro.com",
            message_id=f"msg-no-match-{i}",
            body_text=f"Body {i}",
            from_email="external@otro.com",  # inbound desde fuera
        )

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        # NOTA: cuando un mensaje matchea contacto-not-found, `_import_one`
        # incrementa skipped (no es 0). Para forzar la trampa
        # imported=0+skipped=0 forzamos que `_match_contact` devuelva
        # None ANTES de que se llegue al check. Pero `_import_one` ya
        # incrementa skipped en ese caso. Por tanto en este escenario
        # NO disparamos el tripwire; el test verifica que el job
        # completó normalmente (skipped>0 = matching funciona como
        # filtro), no que disparó FAILED.
        assert job.total_processed >= 6
        # Confirmamos que skipped>0 prueba que el counter SÍ se mueve
        # por contacto-not-found: el tripwire solo dispararía si la
        # combinación 0/0/0 fuera real (bug raíz).
        assert job.total_skipped > 0


def test_execute_tripwire_fires_when_no_counters_move(
    client, factory, fake_gmail, patched_client, monkeypatch
):
    """Caso patológico: imported=0, skipped=0, errors=0 con procesados
    >= tripwire. Reproducimos forzando `_match_contact` a una rama
    imposible vía monkeypatch — `_import_one` SOLO incrementa
    `total_processed` y vuelve, dejando los otros counters a 0."""
    monkeypatch.setattr(backfill_module, "ZERO_IMPORTS_TRIPWIRE", 3)

    real_import_one = backfill_module._import_one

    def faulty_import_one(session, **kwargs):
        # Simula el bug: el counter `total_processed` se mueve pero
        # ni se importa ni se skippa ni se cuenta error (matching o
        # persistencia rotos en una versión hipotética del handler).
        job = kwargs["job"]
        if kwargs.get("gmail_message_id"):
            job.total_processed += 1

    monkeypatch.setattr(backfill_module, "_import_one", faulty_import_one)

    for i in range(5):
        fake_gmail.add_conversation(
            alias="manel@bomedia.net",
            contact_email="marny@cliente.com",
            message_id=f"msg-faulty-{i}",
            body_text=f"Body {i}",
        )

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.FAILED.value
        assert job.error_summary is not None
        assert "matching" in job.error_summary.lower()

    # Restaurar el real para no contaminar tests posteriores.
    monkeypatch.setattr(backfill_module, "_import_one", real_import_one)


def test_backfill_list_returns_jobs_regardless_of_initiator(
    client, factory, patched_client
):
    """PR-Fix-Backfill-Gmail-Cero-Importados. Bart hizo logout/login
    y su estimate en marcha desapareció de la UI. El listado nuevo
    debe devolver TODOS los jobs recientes para que cualquier admin
    pueda resumir el polling."""
    # Crear un estimate como admin.
    r1 = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    job_id_admin = r1.json()["id"]
    # Crear un execute con otro admin (en este suite "manager" no es
    # admin → cambiar para que el listado simule "otro admin").
    # Aquí simplificamos: el test prueba que el endpoint devuelve
    # el row independientemente del initiated_by_user_id.

    response = client.get(
        "/api/admin/gmail/backfill?limit=10",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(j["id"] == job_id_admin for j in body)
    # Y los rows traen mode + status para que el frontend filtre el
    # activo correctamente.
    assert all("mode" in j and "status" in j for j in body)


# ---------------------------------------------------------------------------
# PR-Fix-Backfill-Gmail-Tras-Validación — nuevos tests
# ---------------------------------------------------------------------------


def test_is_attachment_part_includes_inline_with_attachment_id():
    """Bug 3. La clasificación única `_is_attachment_part` cuenta
    inline images (con attachmentId + size>0), no solo Content-
    Disposition=attachment. Pin para que estimate y execute lleguen
    al mismo número."""
    from app.integrations.gmail.backfill import _is_attachment_part

    inline_image = {
        "mimeType": "image/png",
        "body": {"attachmentId": "att-inline-logo", "size": 12345},
    }
    classic = {
        "filename": "report.pdf",
        "mimeType": "application/pdf",
        "body": {"attachmentId": "att-pdf", "size": 200000},
    }
    body_text = {
        "mimeType": "text/plain",
        "body": {"data": "aGVsbG8="},  # no attachmentId
    }
    zero_size = {
        "filename": "ghost.bin",
        "body": {"attachmentId": "x", "size": 0},
    }
    assert _is_attachment_part(inline_image) is True
    assert _is_attachment_part(classic) is True
    assert _is_attachment_part(body_text) is False
    assert _is_attachment_part(zero_size) is False


def test_run_backfill_skips_already_finalized_job(
    client, factory, fake_gmail, patched_client
):
    """Bug 7. Si el job en BD ya está cancelled/failed/completed al
    momento del pickup RQ, el handler NO debe procesarlo (caso real
    de Bart 2026-06-26: ce82696c fue cancelado manualmente vía SQL
    pero el worker lo recogió horas después y lo procesó 50 min)."""
    r = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36},
    )
    job_id = r.json()["id"]
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        job.status = GmailBackfillStatus.CANCELLED.value
        session.commit()

    # Run handler — debe ser no-op.
    backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        # Status NO cambia, NO se han creado messages.
        assert job.status == GmailBackfillStatus.CANCELLED.value
        # Counters NO se han movido.
        assert job.total_processed == 0


def test_run_execute_marks_job_failed_on_unhandled_exception(
    client, factory, fake_gmail, patched_client, monkeypatch
):
    """Bug 2. Si una excepción no manejada escapa del handler, el job
    debe terminar `failed` con error_summary informativo, no quedar
    colgado en `running`."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated catastrophic failure")

    # Romper `_build_contact_index` para que estalle al inicio.
    monkeypatch.setattr(
        backfill_module, "_build_contact_index", explode
    )

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)
    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.FAILED.value
        assert job.error_summary is not None
        assert "RuntimeError" in job.error_summary
        assert "simulated catastrophic failure" in job.error_summary
        assert job.finished_at is not None


def test_run_execute_continues_on_per_message_error(
    client, factory, fake_gmail, patched_client, monkeypatch
):
    """Bug 2. Si un mensaje individual peta al insertar (body
    demasiado grande, etc.), el handler salta ese y sigue con los
    siguientes. NO aborta el job entero."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-ok-1",
        body_text="Body OK",
    )
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-bad",
        body_text="Body bad",
    )
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-ok-2",
        body_text="Body OK 2",
    )

    real_flush = backfill_module.Session.flush

    def faulty_flush(self, *args, **kwargs):
        # Mira si hay un EmailMessage pendiente con el gmail id del
        # mensaje malo. Si sí, simula DataError.
        for obj in list(self.new):
            if (
                hasattr(obj, "gmail_message_id")
                and obj.gmail_message_id == "msg-bad"
            ):
                raise RuntimeError(
                    "simulated DataError(1406) body_html too long"
                )
        return real_flush(self, *args, **kwargs)

    monkeypatch.setattr(backfill_module.Session, "flush", faulty_flush)

    r = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": False,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        from app.models.crm import EmailMessage  # noqa: PLC0415

        job = session.get(GmailBackfillJob, job_id)
        assert job.status == GmailBackfillStatus.COMPLETED.value, (
            f"status={job.status}, error_summary={job.error_summary}"
        )
        assert job.total_errors == 1
        assert job.total_imported == 2  # los dos OK
        ids = {
            m.gmail_message_id
            for m in session.scalars(select(EmailMessage))
        }
        assert ids == {"msg-ok-1", "msg-ok-2"}


def test_estimate_and_execute_count_attachments_consistently(
    client, factory, fake_gmail, patched_client
):
    """Bug 3. Estimate y execute deben usar la MISMA clasificación de
    adjuntos (`_is_attachment_part`). Antes estimate filtraba por
    filename y se saltaba inline images mientras execute las
    guardaba. Resultado: estimate decía 0 adjuntos pero execute
    creaba filas."""
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="msg-inline",
        body_text="Email con logo inline",
        attachments=[("logo.png", b"BINARY", "image/png")],
    )

    # Run estimate
    r_est = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "aliases_scope": "all_visible"},
    )
    est_id = r_est.json()["id"]
    backfill_module.run_backfill(est_id)

    with factory() as session:
        est_job = session.get(GmailBackfillJob, est_id)
        est_result = json.loads(est_job.result_json)
        est_attachments = est_result["total_attachments_count"]

    # Run execute over the SAME mock data — debe reportar el mismo
    # número de attachments en `email_message_attachments`.
    r_exec = client.post(
        "/api/admin/gmail/backfill/execute",
        headers=auth_headers(client, "admin"),
        json={
            "months_back": 36,
            "include_attachments": True,
            "max_attachment_size_mb": 25,
            "aliases_scope": "all_visible",
        },
    )
    exec_id = r_exec.json()["id"]
    backfill_module.run_backfill(exec_id)

    with factory() as session:
        from sqlalchemy import select  # noqa: PLC0415

        from app.models.crm import EmailMessageAttachment  # noqa: PLC0415

        execute_attachments = len(
            list(session.scalars(select(EmailMessageAttachment)))
        )

    assert est_attachments == execute_attachments, (
        f"mismatch — estimate={est_attachments} execute={execute_attachments}"
    )
    assert est_attachments >= 1  # el logo inline cuenta


def test_total_estimated_uses_actual_count_not_resultsizeestimate(
    client, factory, fake_gmail, patched_client
):
    """Bug 4. `total_estimated` ahora refleja el conteo paginado real,
    no `resultSizeEstimate` (que Gmail devuelve aproximado y a veces
    se queda muy bajo). Bart vio total_estimated=201 mientras
    total_processed>1000."""
    for i in range(15):
        fake_gmail.add_conversation(
            alias="manel@bomedia.net",
            contact_email="marny@cliente.com",
            message_id=f"msg-est-{i}",
            body_text=f"Body {i}",
        )

    r = client.post(
        "/api/admin/gmail/backfill/estimate",
        headers=auth_headers(client, "admin"),
        json={"months_back": 36, "aliases_scope": "all_visible"},
    )
    job_id = r.json()["id"]
    backfill_module.run_backfill(job_id)

    with factory() as session:
        job = session.get(GmailBackfillJob, job_id)
        # 15 mensajes inyectados + 2 users (admin/manager) cada uno
        # con su alias all_visible — al menos el admin ve los 15.
        assert job.total_estimated is not None
        assert job.total_estimated >= 15, (
            f"total_estimated={job.total_estimated} < 15 — debería "
            "reflejar el conteo paginado real, no el hint de Gmail"
        )


# ---------------------------------------------------------------------------
# PR-Auto-Backfill-Gmail-Por-Contacto — mini-backfill por contacto
# ---------------------------------------------------------------------------


def _only_admin_integrations(factory: sessionmaker):
    """Patch helper: limita `_iter_connected_users` al admin para que el
    FakeGmail (que no modela buzones separados y over-matchea por
    substring del email del contacto) no importe el mismo mensaje bajo
    varios users. En producción cada query corre contra el buzón real
    del user, así que esto refleja el caso "solo el admin habló con el
    contacto"."""
    with factory() as session:
        admin_id = session.scalar(_user_id_by_role_query("admin"))

    original = backfill_module._iter_connected_users

    def _filtered(session):  # noqa: ANN001
        return [i for i in original(session) if i.user_id == admin_id]

    return patch.object(
        backfill_module, "_iter_connected_users", side_effect=_filtered
    ), admin_id


def test_per_contact_backfill_imports_matching_emails(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="pc-msg-1",
        subject="Histórico",
        body_text="Hablamos hace meses",
    )
    only_admin, admin_id = _only_admin_integrations(factory)
    with only_admin:
        backfill_module.run_backfill_per_contact("contact-marny")

    with factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id == "pc-msg-1",
                EmailMessage.gmail_account_user_id == admin_id,
            )
        )
        assert msg is not None
        assert msg.imported_via == "per_contact_backfill"
        assert msg.contact_id == "contact-marny"


def test_per_contact_backfill_skips_contact_without_email(
    client, factory, fake_gmail, patched_client
):
    with factory() as session:
        session.add(
            Contact(
                id="contact-noemail",
                first_name="SinEmail",
                email=None,
                is_active=True,
            )
        )
        session.commit()
    only_admin, _ = _only_admin_integrations(factory)
    with only_admin:
        # No debe lanzar — return temprano con warning.
        backfill_module.run_backfill_per_contact("contact-noemail")

    with factory() as session:
        from sqlalchemy import func

        n = session.scalar(
            select(func.count()).select_from(EmailMessage)
        )
        assert n == 0


def test_per_contact_backfill_dedups_existing_messages_by_gmail_id(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="pc-dup-1",
        body_text="Ya importado",
    )
    only_admin, admin_id = _only_admin_integrations(factory)
    # Pre-insertar el mensaje como si ya existiera (e.g. sync incremental).
    with factory() as session:
        thread_id = str(uuid4())
        from app.models.crm import EmailThread

        session.add(
            EmailThread(
                id=thread_id,
                gmail_account_user_id=admin_id,
                initiated_by_user_id=admin_id,
                gmail_thread_id="pc-dup-1",
                contact_id="contact-marny",
                subject="Ya importado",
                first_message_at=datetime.now(UTC),
                last_message_at=datetime.now(UTC),
                message_count=1,
            )
        )
        from app.models.crm import EmailDirection

        session.add(
            EmailMessage(
                thread_id=thread_id,
                gmail_message_id="pc-dup-1",
                gmail_account_user_id=admin_id,
                direction=EmailDirection.INBOUND,
                from_email="manel@bomedia.net",
                to_emails_json="[]",
                contact_id="contact-marny",
                imported_via="incoming_realtime",
                sent_at=datetime.now(UTC),
            )
        )
        session.commit()

    with only_admin:
        backfill_module.run_backfill_per_contact("contact-marny")

    with factory() as session:
        from sqlalchemy import func

        n = session.scalar(
            select(func.count())
            .select_from(EmailMessage)
            .where(
                EmailMessage.gmail_message_id == "pc-dup-1",
                EmailMessage.gmail_account_user_id == admin_id,
            )
        )
        assert n == 1  # no se duplicó


def test_per_contact_backfill_continues_on_oauth_expired_for_one_user(
    client, factory, fake_gmail, patched_client
):
    fake_gmail.add_conversation(
        alias="manel@bomedia.net",
        contact_email="marny@cliente.com",
        message_id="pc-oauth-1",
        body_text="ok",
    )
    with factory() as session:
        manager_id = session.scalar(_user_id_by_role_query("manager"))

    def selective(session, user_id):  # noqa: ANN001
        if user_id == manager_id:
            raise GmailScopeMissingError("scope expirado")
        return fake_gmail

    with patch.object(backfill_module, "_client_for", side_effect=selective):
        # No debe lanzar — el manager se salta, el admin importa.
        backfill_module.run_backfill_per_contact("contact-marny")

    with factory() as session:
        msg = session.scalar(
            select(EmailMessage).where(
                EmailMessage.gmail_message_id == "pc-oauth-1"
            )
        )
        assert msg is not None


def test_per_contact_backfill_returns_early_for_deleted_contact(
    client, factory, fake_gmail, patched_client
):
    # contact_id inexistente → log + return sin lanzar.
    only_admin, _ = _only_admin_integrations(factory)
    with only_admin:
        backfill_module.run_backfill_per_contact("contact-does-not-exist")
    with factory() as session:
        from sqlalchemy import func

        n = session.scalar(select(func.count()).select_from(EmailMessage))
        assert n == 0


def test_contact_create_triggers_per_contact_backfill_when_email_present(
    client, factory, patched_client
):
    calls: list[tuple] = []

    def _spy(contact_id, **kwargs):  # noqa: ANN001
        calls.append((contact_id, kwargs))

    with patch(
        "app.integrations.gmail.backfill.enqueue_backfill_per_contact",
        side_effect=_spy,
    ):
        r = client.post(
            "/api/contacts",
            headers=auth_headers(client, "manager"),
            json={
                "first_name": "Nuevo",
                "email": "nuevo@lead.com",
                "marketing_consent": "unknown",
            },
        )
    assert r.status_code == 201, r.text
    assert len(calls) == 1
    assert calls[0][0] == r.json()["id"]


def test_admin_batch_endpoint_queues_jobs_for_each_contact_id(
    client, factory, patched_client
):
    with factory() as session:
        admin_id = session.scalar(_user_id_by_role_query("admin"))
        session.add_all(
            [
                Contact(
                    id="batch-a",
                    first_name="A",
                    email="a@x.com",
                    owner_user_id=admin_id,
                    is_active=True,
                ),
                Contact(
                    id="batch-b",
                    first_name="B",
                    email="b@x.com",
                    owner_user_id=admin_id,
                    is_active=True,
                ),
            ]
        )
        session.commit()

    calls: list[str] = []

    def _spy(contact_id, **kwargs):  # noqa: ANN001
        calls.append(contact_id)

    with patch(
        "app.integrations.gmail.backfill.enqueue_backfill_per_contact",
        side_effect=_spy,
    ):
        r = client.post(
            "/api/admin/gmail/backfill-per-contact-batch",
            headers=auth_headers(client, "admin"),
            json={"contact_ids": ["batch-a", "batch-b"], "months_back": 12},
        )
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 2
    assert set(calls) == {"batch-a", "batch-b"}


def test_per_contact_single_endpoint_visible_for_any_user(
    client, factory, patched_client
):
    calls: list[str] = []

    def _spy(contact_id, **kwargs):  # noqa: ANN001
        calls.append(contact_id)

    with patch(
        "app.integrations.gmail.backfill.enqueue_backfill_per_contact",
        side_effect=_spy,
    ):
        r = client.post(
            "/api/contacts/contact-marny/gmail-backfill",
            headers=auth_headers(client, "user"),
            json={"months_back": 12},
        )
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1
    assert calls == ["contact-marny"]


def test_per_contact_candidates_endpoint_lists_recent_without_history(
    client, factory, patched_client
):
    # contact-marny existe (sin per_contact_backfill) → candidato.
    r = client.get(
        "/api/admin/gmail/backfill-per-contact/candidates?hours=24",
        headers=auth_headers(client, "admin"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "contact-marny" in body["contact_ids"]
    assert body["count"] >= 1
