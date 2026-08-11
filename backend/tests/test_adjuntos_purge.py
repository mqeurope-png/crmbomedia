"""CRM-ADJUNTOS-PURGE — gmail_status + --purge-not-found + vistas.

Los mails «huérfanos» (importados pero ya borrados de Gmail) se marcan
`gmail_status='deleted_gmail'`, se ocultan de las vistas generales cuando
TODOS los mensajes del hilo son huérfanos, y viven en `state=deleted`
(«Papelera Gmail»). La ficha del contacto los mantiene visibles.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Registra TODOS los modelos en Base.metadata (templates/folders viven
# fuera de app.models.crm y create_all los necesita).
import app.main  # noqa: F401
from app.core import crypto
from app.db.session import get_session
from app.integrations.gmail import backfill_attachments as ba_module
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.backfill import is_not_found_error
from app.integrations.gmail.backfill_attachments import (
    run_backfill_attachments,
)
from app.integrations.gmail.backfill_universal import run_backfill_universal
from app.main import app
from app.models.crm import (
    ORG_GOOGLE_SINGLETON_ID,
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    OrgGoogleIntegration,
    User,
    UserEmailAlias,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users

ALIAS = "norma@bomedia.net"


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
        owner = seed.scalar(select(User.id).where(User.role == UserRole.USER))
        seed.add(UserEmailAlias(user_id=owner, alias_email=ALIAS, active=True))
        seed.add(
            OrgGoogleIntegration(
                id=ORG_GOOGLE_SINGLETON_ID,
                google_email="mqeurope@gmail.com",
                access_token_encrypted=crypto.encrypt("fake-access"),
                refresh_token_encrypted=crypto.encrypt("fake-refresh"),
                scopes="https://www.googleapis.com/auth/gmail.send",
                token_expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                connected_at=datetime.now(UTC),
                connected_by_user_id=owner,
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


def _uid(session: Session, role: UserRole = UserRole.USER) -> str:
    uid = session.scalar(select(User.id).where(User.role == role))
    assert uid
    return uid


def _seed_thread(
    session: Session,
    *,
    uid: str,
    gid: str,
    messages: list[dict[str, Any]],
    contact_id: str | None = None,
) -> str:
    """Thread + N mensajes; cada dict acepta gmail_status/direction."""
    thread = EmailThread(
        initiated_by_user_id=uid,
        gmail_thread_id=gid,
        gmail_account_user_id=uid,
        subject=f"Asunto {gid}",
        first_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_message_at=datetime(2026, 6, 1, tzinfo=UTC),
        message_count=len(messages),
        contact_id=contact_id,
    )
    session.add(thread)
    session.flush()
    for idx, spec in enumerate(messages):
        session.add(
            EmailMessage(
                thread_id=thread.id,
                gmail_message_id=spec.get("gmail_message_id", f"{gid}-m{idx}"),
                gmail_account_user_id=uid,
                direction=EmailDirection(spec.get("direction", "inbound")),
                from_email=spec.get("from_email", "cliente@fuera.com"),
                to_emails_json='["norma@bomedia.net"]',
                delivered_to=spec.get("delivered_to", ALIAS),
                sent_at=datetime(2026, 6, 1, tzinfo=UTC),
                gmail_status=spec.get("gmail_status", "active"),
                contact_id=spec.get("contact_id"),
            )
        )
    session.flush()
    return thread.id


def _not_found() -> Exception:
    exc = RuntimeError("Requested entity was not found (404)")
    exc.resp = type("Resp", (), {"status": 404})()  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Modelo / migración
# ---------------------------------------------------------------------------


def test_gmail_status_defaults_to_active(factory: sessionmaker) -> None:
    """Migración 0094: default 'active' (create_all refleja el modelo; el
    ALTER real lo valida el job backend-mysql con alembic upgrade head)."""
    with factory() as session:
        uid = _uid(session)
        _seed_thread(session, uid=uid, gid="def", messages=[{}])
        session.commit()
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        assert msg.gmail_status == "active"


def test_is_not_found_error_detects_404_and_410() -> None:
    assert is_not_found_error(_not_found())
    exc410 = RuntimeError("gone")
    exc410.resp = type("Resp", (), {"status": 410})()  # type: ignore[attr-defined]
    assert is_not_found_error(exc410)
    exc500 = RuntimeError("boom")
    exc500.resp = type("Resp", (), {"status": 500})()  # type: ignore[attr-defined]
    assert not is_not_found_error(exc500)


# ---------------------------------------------------------------------------
# backfill_attachments --purge-not-found (el purge efectivo: itera la BD)
# ---------------------------------------------------------------------------


class _GoneClient:
    def get_message(self, message_id: str):  # noqa: ANN201
        raise _not_found()


def test_backfill_attachments_purge_not_found_marks_deleted_gmail(
    factory: sessionmaker,
) -> None:
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(
            session, uid=uid, gid="orphan",
            messages=[{"gmail_message_id": "g-orphan"}],
        )
        session.commit()
        with patch.object(ba_module, "_client_for", return_value=_GoneClient()):
            report = run_backfill_attachments(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 11).date(),
                purge_not_found=True,
                progress=lambda _line: None,
            )
        assert report.purged_not_found == 1
        assert report.errors == 0
        assert "Marcados como borrados en Gmail" in report.render()
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.thread_id == thread_id)
        )
        assert msg is not None
        assert msg.gmail_status == "deleted_gmail"


def test_backfill_attachments_no_flag_keeps_active(
    factory: sessionmaker,
) -> None:
    """Regresión: sin el flag, el 404 cuenta como error y no toca la BD."""
    with factory() as session:
        uid = _uid(session)
        _seed_thread(
            session, uid=uid, gid="orphan2",
            messages=[{"gmail_message_id": "g-orphan2"}],
        )
        session.commit()
        with patch.object(ba_module, "_client_for", return_value=_GoneClient()):
            report = run_backfill_attachments(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 11).date(),
                progress=lambda _line: None,
            )
        assert report.errors == 1
        assert report.purged_not_found == 0
        msg = session.scalar(select(EmailMessage))
        assert msg is not None
        assert msg.gmail_status == "active"


# ---------------------------------------------------------------------------
# backfill_universal --purge-not-found (solo cubre la carrera list→get:
# un mensaje YA en la BD de esta cuenta se dedupea antes de get_message)
# ---------------------------------------------------------------------------


def test_backfill_universal_purge_not_found_marks_deleted_gmail(
    factory: sessionmaker,
) -> None:
    class _Fake:
        def list_messages(self, **_kw):  # noqa: ANN201
            return {"messages": [{"id": "u404", "threadId": "t404"}]}

        def get_message(self, _mid):  # noqa: ANN201
            raise _not_found()

    with factory() as session:
        uid = _uid(session)
        admin = _uid(session, UserRole.ADMIN)
        # Fila en BD bajo OTRA cuenta (no entra en el dedupe `seen` del
        # runner, así que el 404 la alcanza y el flag la marca).
        _seed_thread(
            session, uid=admin, gid="t404",
            messages=[{"gmail_message_id": "u404"}],
        )
        session.commit()
        with patch.object(gmail_service, "_client_for", return_value=_Fake()):
            report = run_backfill_universal(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 11).date(),
                purge_not_found=True,
                progress=lambda _line: None,
            )
        assert report.purged_not_found == 1
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "u404")
        )
        assert msg is not None
        assert msg.gmail_status == "deleted_gmail"


def test_backfill_universal_no_flag_keeps_active(
    factory: sessionmaker,
) -> None:
    class _Fake:
        def list_messages(self, **_kw):  # noqa: ANN201
            return {"messages": [{"id": "u405", "threadId": "t405"}]}

        def get_message(self, _mid):  # noqa: ANN201
            raise _not_found()

    with factory() as session:
        uid = _uid(session)
        admin = _uid(session, UserRole.ADMIN)
        _seed_thread(
            session, uid=admin, gid="t405",
            messages=[{"gmail_message_id": "u405"}],
        )
        session.commit()
        with patch.object(gmail_service, "_client_for", return_value=_Fake()):
            report = run_backfill_universal(
                session,
                user_id=uid,
                since=datetime(2026, 2, 7).date(),
                until=datetime(2026, 8, 11).date(),
                progress=lambda _line: None,
            )
        assert report.errors >= 1
        assert report.purged_not_found == 0
        msg = session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "u405")
        )
        assert msg is not None
        assert msg.gmail_status == "active"


# ---------------------------------------------------------------------------
# Vistas: bandeja excluye huérfanos completos; state=deleted los lista;
# la ficha del contacto los mantiene
# ---------------------------------------------------------------------------


def _seed_views_fixture(session: Session) -> str:
    """3 hilos del user: normal, huérfano completo, mixto. Devuelve el
    contact_id usado por el huérfano."""
    uid = _uid(session)
    contact = Contact(first_name="Eva", email="eva@cliente.com")
    session.add(contact)
    session.flush()
    _seed_thread(session, uid=uid, gid="normal", messages=[{}])
    _seed_thread(
        session, uid=uid, gid="orphan-full", contact_id=contact.id,
        messages=[{"gmail_status": "deleted_gmail"}],
    )
    _seed_thread(
        session, uid=uid, gid="mixed",
        messages=[{"gmail_status": "deleted_gmail"}, {}],
    )
    session.commit()
    return contact.id


def test_list_threads_inbox_excludes_deleted_gmail_by_default(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        _seed_views_fixture(session)

    resp = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert resp.status_code == 200
    got = {t["gmail_thread_id"] for t in resp.json()["items"]}
    # El huérfano COMPLETO se oculta; el mixto (aún tiene un mensaje vivo)
    # se mantiene.
    assert got == {"normal", "mixed"}


def test_list_threads_state_deleted_returns_only_deleted_gmail(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        _seed_views_fixture(session)

    resp = client.get(
        "/api/emails/threads?state=deleted",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    got = {t["gmail_thread_id"] for t in resp.json()["items"]}
    # «Papelera Gmail»: cualquier hilo con ≥1 mensaje borrado en Gmail.
    assert got == {"orphan-full", "mixed"}
    assert "normal" not in got


def test_contact_ficha_still_shows_deleted_gmail(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        contact_id = _seed_views_fixture(session)

    resp = client.get(
        f"/api/emails/threads?contact_id={contact_id}",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 200
    got = {t["gmail_thread_id"] for t in resp.json()["items"]}
    # La ficha no corta el histórico: el hilo huérfano sigue visible.
    assert "orphan-full" in got


def test_thread_detail_exposes_gmail_status(
    client: TestClient, factory: sessionmaker
) -> None:
    with factory() as session:
        uid = _uid(session)
        thread_id = _seed_thread(
            session, uid=uid, gid="detail-orphan",
            messages=[{"gmail_status": "deleted_gmail"}],
        )
        session.commit()

    resp = client.get(
        f"/api/emails/threads/{thread_id}",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert resp.json()["messages"][0]["gmail_status"] == "deleted_gmail"
