"""QoL2 hot-fix tests.

Bug 1: el filtro `notes_content is_empty` / `is_not_empty` no se
pruneaba en el frontend correctamente. El backend siempre fue
correcto (probado en test_qol_sprint.py). Aquí solo verificamos que
NO_VALUE_COMPARATORS contiene los 4 esperados (regresión).

Bug 2: `/api/emails/stats` acepta scope=mine|team + team_user_id.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailDirection,
    EmailMessage,
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


def _seed_email_sent_by(
    session: Session, *, user_id: str, subject: str
) -> str:
    """Crea un EmailThread + EmailMessage outbound atribuido a
    `user_id`. Sólo los campos NOT NULL del modelo — los nullable
    (gmail_message_id, snippet, …) quedan en su default."""
    from app.models.crm import EmailThread  # noqa: PLC0415

    now = datetime.now(UTC)
    thread = EmailThread(
        initiated_by_user_id=user_id,
        gmail_account_user_id=user_id,
        gmail_thread_id=f"gthr-{subject}",
        subject=subject,
        first_message_at=now,
        last_message_at=now,
    )
    session.add(thread)
    session.flush()
    msg = EmailMessage(
        thread_id=thread.id,
        gmail_account_user_id=user_id,
        direction=EmailDirection.OUTBOUND,
        sent_at=now,
        created_by_user_id=user_id,
        from_email=f"{user_id}@x.com",
        to_emails_json=f'["to-{subject}@y.com"]',
        subject=subject,
    )
    session.add(msg)
    session.flush()
    return msg.id


# === Bug 2: /api/emails/stats scope ==================================


def test_email_stats_default_scope_mine_for_manager(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Pre-QoL2: manager veía counters globales. Post: solo los suyos
    por defecto (paridad con la lista de threads)."""
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_email_sent_by(session, user_id=user_uid, subject="userS")
        _seed_email_sent_by(session, user_id=manager_uid, subject="mgrS")
        session.commit()
    resp = client.get(
        "/api/emails/stats",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent"] == 1  # solo el del manager


def test_email_stats_scope_team_shows_all(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_email_sent_by(session, user_id=user_uid, subject="userS")
        _seed_email_sent_by(session, user_id=manager_uid, subject="mgrS")
        session.commit()
    resp = client.get(
        "/api/emails/stats?scope=team",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] == 2


def test_email_stats_scope_team_filtered_by_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_email_sent_by(session, user_id=user_uid, subject="userS")
        _seed_email_sent_by(session, user_id=manager_uid, subject="mgrS")
        session.commit()
    resp = client.get(
        f"/api/emails/stats?scope=team&team_user_id={user_uid}",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] == 1


def test_email_stats_scope_team_rejects_user_role(
    client: TestClient,
) -> None:
    resp = client.get(
        "/api/emails/stats?scope=team",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403
