"""PR-Fix-Widget-Engagement-Email tests.

Verifica que `GET /api/contacts/{id}/engagement-stats` cuenta
correctamente aperturas / clics / respuestas leyendo directo de
`email_message_events` y `email_messages.direction='inbound'`.

Antes el widget contaba sobre `activity_events`
(EMAIL_OPENED / EMAIL_CLICKED / email.reply_received) — esa tabla
NO se popula automáticamente cuando el tracking de aperturas
escribe en `email_message_events`, por eso el widget mostraba 0
aunque la BD tuviera la apertura registrada (caso Bart).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailEventType,
    EmailMessage,
    EmailMessageEvent,
    EmailThread,
    User,
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


def _seed_contact(session: Session) -> str:
    c = Contact(first_name="TESTT 2121", email="malahierba@elbarquito.net")
    session.add(c)
    session.flush()
    return c.id


def _admin_user_id(session: Session) -> str:
    return session.scalar(
        Base.metadata.bind.dialect.get_default_isolation_level  # type: ignore[arg-type]
        if False  # placeholder, replaced below
        else __import__("sqlalchemy").select(User.id).where(
            User.role == UserRole.ADMIN
        )
    )


def _seed_thread(session: Session, *, contact_id: str, user_id: str) -> str:
    now = datetime.now(UTC)
    thread = EmailThread(
        contact_id=contact_id,
        initiated_by_user_id=user_id,
        gmail_thread_id=f"thread-{contact_id[:6]}",
        gmail_account_user_id=user_id,
        subject="Tedt",
        first_message_at=now,
        last_message_at=now,
    )
    session.add(thread)
    session.flush()
    return thread.id


def _seed_outbound_message(
    session: Session,
    *,
    contact_id: str,
    thread_id: str,
    user_id: str,
    sent_at: datetime | None = None,
    gmail_id: str | None = None,
) -> str:
    msg = EmailMessage(
        thread_id=thread_id,
        gmail_message_id=gmail_id or f"out-{thread_id[:6]}-{user_id[:4]}",
        gmail_account_user_id=user_id,
        direction=EmailDirection.OUTBOUND,
        from_email="ops@example.com",
        to_emails_json='["dest@example.com"]',
        sent_at=sent_at or datetime.now(UTC),
        contact_id=contact_id,
        created_by_user_id=user_id,
    )
    session.add(msg)
    session.flush()
    return msg.id


def _seed_event(
    session: Session,
    *,
    message_id: str,
    event_type: EmailEventType,
    occurred_at: datetime | None = None,
) -> None:
    session.add(
        EmailMessageEvent(
            message_id=message_id,
            event_type=event_type,
            occurred_at=occurred_at or datetime.now(UTC),
        )
    )
    session.flush()


# ---------------------------------------------------------------------
# Bug exacto de Bart
# ---------------------------------------------------------------------


def test_contact_engagement_stats_counts_opens_correctly(
    client: TestClient, session_factory: sessionmaker
):
    """Caso real: contacto TESTT 2121 con 1 email outbound + evento
    OPEN registrado → endpoint devuelve aperturas=1, clics=0,
    respuestas=0. Antes del fix devolvía todo en 0."""
    from sqlalchemy import select

    with session_factory() as session:
        contact_id = _seed_contact(session)
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        thread_id = _seed_thread(session, contact_id=contact_id, user_id=admin_id)
        msg_id = _seed_outbound_message(
            session,
            contact_id=contact_id,
            thread_id=thread_id,
            user_id=admin_id,
        )
        _seed_event(session, message_id=msg_id, event_type=EmailEventType.OPEN)
        session.commit()

    response = client.get(
        f"/api/contacts/{contact_id}/engagement-stats",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"opens": 1, "clicks": 0, "replies": 0}


def test_contact_engagement_stats_counts_all_users_emails_not_only_current(
    client: TestClient, session_factory: sessionmaker
):
    """Emails enviados por DIFERENTES users del CRM al mismo contacto
    se cuentan TODOS (Bart validó con admin + otros users y todos
    deben ver los mismos números — el endpoint no filtra por
    current_user)."""
    from sqlalchemy import select

    with session_factory() as session:
        contact_id = _seed_contact(session)
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        manager_id = session.scalar(
            select(User.id).where(User.role == UserRole.MANAGER)
        )
        thread_admin = _seed_thread(session, contact_id=contact_id, user_id=admin_id)
        thread_mgr = _seed_thread(session, contact_id=contact_id, user_id=manager_id)
        msg1 = _seed_outbound_message(
            session,
            contact_id=contact_id,
            thread_id=thread_admin,
            user_id=admin_id,
            gmail_id="g1",
        )
        msg2 = _seed_outbound_message(
            session,
            contact_id=contact_id,
            thread_id=thread_mgr,
            user_id=manager_id,
            gmail_id="g2",
        )
        _seed_event(session, message_id=msg1, event_type=EmailEventType.OPEN)
        _seed_event(session, message_id=msg2, event_type=EmailEventType.OPEN)
        session.commit()

    # Login como un user de rango bajo — debe ver los 2 opens.
    response = client.get(
        f"/api/contacts/{contact_id}/engagement-stats",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200
    assert response.json()["opens"] == 2


def test_contact_engagement_stats_excludes_events_older_than_30_days(
    client: TestClient, session_factory: sessionmaker
):
    """Evento OPEN ocurrido hace 31 días NO cuenta; evento hace 29
    días SÍ. La ventana es `occurred_at >= now - 30d`."""
    from sqlalchemy import select

    with session_factory() as session:
        contact_id = _seed_contact(session)
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        thread_id = _seed_thread(
            session, contact_id=contact_id, user_id=admin_id
        )
        msg_id = _seed_outbound_message(
            session,
            contact_id=contact_id,
            thread_id=thread_id,
            user_id=admin_id,
        )
        now = datetime.now(UTC)
        # Una apertura DENTRO de la ventana (29 días).
        _seed_event(
            session,
            message_id=msg_id,
            event_type=EmailEventType.OPEN,
            occurred_at=now - timedelta(days=29),
        )
        # Una apertura FUERA de la ventana (31 días).
        _seed_event(
            session,
            message_id=msg_id,
            event_type=EmailEventType.OPEN,
            occurred_at=now - timedelta(days=31),
        )
        session.commit()

    response = client.get(
        f"/api/contacts/{contact_id}/engagement-stats",
        headers=auth_headers(client, "admin"),
    )
    assert response.json()["opens"] == 1


def test_contact_engagement_stats_counts_clicks_replies_separately(
    client: TestClient, session_factory: sessionmaker
):
    """Aperturas, clics y respuestas se cuentan por tipo. Cada uno
    no contamina a los otros."""
    from sqlalchemy import select

    with session_factory() as session:
        contact_id = _seed_contact(session)
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        thread_id = _seed_thread(
            session, contact_id=contact_id, user_id=admin_id
        )
        out_msg = _seed_outbound_message(
            session,
            contact_id=contact_id,
            thread_id=thread_id,
            user_id=admin_id,
        )
        # 2 opens + 1 click sobre el mismo outbound.
        _seed_event(session, message_id=out_msg, event_type=EmailEventType.OPEN)
        _seed_event(session, message_id=out_msg, event_type=EmailEventType.OPEN)
        _seed_event(session, message_id=out_msg, event_type=EmailEventType.CLICK)
        # Una respuesta = mensaje inbound del contacto.
        reply = EmailMessage(
            thread_id=thread_id,
            gmail_message_id="reply-1",
            gmail_account_user_id=admin_id,
            direction=EmailDirection.INBOUND,
            from_email="dest@example.com",
            to_emails_json='["ops@example.com"]',
            sent_at=datetime.now(UTC),
            contact_id=contact_id,
        )
        session.add(reply)
        session.commit()

    response = client.get(
        f"/api/contacts/{contact_id}/engagement-stats",
        headers=auth_headers(client, "admin"),
    )
    body = response.json()
    assert body == {"opens": 2, "clicks": 1, "replies": 1}


def test_contact_engagement_stats_only_counts_target_contact(
    client: TestClient, session_factory: sessionmaker
):
    """Eventos sobre emails de OTROS contactos no se cuentan al
    pedir las stats de un contacto concreto. Sanity de aislamiento."""
    from sqlalchemy import select

    with session_factory() as session:
        c1 = _seed_contact(session)
        c2 = Contact(first_name="Other", email="other@example.com")
        session.add(c2)
        session.flush()
        admin_id = session.scalar(
            select(User.id).where(User.role == UserRole.ADMIN)
        )
        thread_c2 = _seed_thread(session, contact_id=c2.id, user_id=admin_id)
        msg_c2 = _seed_outbound_message(
            session,
            contact_id=c2.id,
            thread_id=thread_c2,
            user_id=admin_id,
            gmail_id="other-1",
        )
        _seed_event(session, message_id=msg_c2, event_type=EmailEventType.OPEN)
        session.commit()

    response = client.get(
        f"/api/contacts/{c1}/engagement-stats",
        headers=auth_headers(client, "admin"),
    )
    assert response.json() == {"opens": 0, "clicks": 0, "replies": 0}


def test_contact_engagement_stats_returns_404_for_missing_contact(
    client: TestClient,
):
    response = client.get(
        "/api/contacts/missing-uuid/engagement-stats",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 404
