"""Sprint Email v2.4e — scheduled send tests.

Three angles:
- POST /send with `scheduled_for` in the future creates a pending
  row without touching Gmail.
- The scheduled-message CRUD endpoints (list / cancel / update)
  enforce ownership + only act on pending rows.
- The sweep flips pending rows whose time has arrived through the
  Gmail send path, mirroring the immediate-send behaviour.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt
from app.db.session import get_session
from app.email_scheduled_sweep import scheduled_send_sweep
from app.main import app
from app.models.crm import (
    Base,
    EmailMessage,
    EmailScheduledStatus,
    EmailThread,
    User,
    UserEmailAliasPref,
    UserGoogleIntegration,
    UserRole,
)
from tests._test_helpers import auth_headers, seed_test_users


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker


@pytest.fixture()
def db() -> Generator[_Fixture, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: _Fixture) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with db.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail(
    factory: sessionmaker,
    *,
    user_id: str,
    allowed_aliases: tuple[str, ...] = ("info@bomedia.net",),
) -> None:
    with factory() as session:
        session.add(
            UserGoogleIntegration(
                user_id=user_id,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes=(
                    "https://www.googleapis.com/auth/calendar.events "
                    "https://www.googleapis.com/auth/gmail.send "
                    "https://www.googleapis.com/auth/gmail.modify"
                ),
                connected_at=datetime.now(UTC),
            )
        )
        for idx, alias in enumerate(allowed_aliases):
            session.add(
                UserEmailAliasPref(
                    user_id=user_id,
                    alias_email=alias,
                    is_allowed=True,
                    is_default=idx == 0,
                )
            )
        session.commit()


def _seed_pending_message(
    factory: sessionmaker,
    *,
    user_id: str,
    scheduled_for: datetime,
    subject: str = "Programado",
) -> str:
    """Insert a pending scheduled message + its sentinel thread.
    Returns the message id."""
    with factory() as session:
        thread = EmailThread(
            initiated_by_user_id=user_id,
            gmail_account_user_id=user_id,
            gmail_thread_id=f"pending:thread-{subject}",
            subject=subject,
            first_message_at=scheduled_for,
            last_message_at=scheduled_for,
            message_count=0,
        )
        session.add(thread)
        session.flush()
        msg = EmailMessage(
            thread_id=thread.id,
            gmail_account_user_id=user_id,
            direction="outbound",
            from_email="info@bomedia.net",
            to_emails_json=json.dumps(["lead@example.com"]),
            subject=subject,
            body_text="Cuerpo programado",
            sent_at=None,
            scheduled_for=scheduled_for,
            scheduled_status=EmailScheduledStatus.PENDING.value,
            created_by_user_id=user_id,
        )
        session.add(msg)
        session.commit()
        return msg.id


# -- POST /send branching -------------------------------------------


def test_send_with_future_scheduled_for_persists_pending_row(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)

    target = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    res = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Hola",
            "body_text": "Cuerpo",
            "scheduled_for": target,
        },
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["scheduled_status"] == "pending"
    assert body["sent_at"] is None
    assert body["gmail_message_id"] is None

    with db.factory() as session:
        # Exactly one EmailMessage exists and its thread is the
        # sentinel — Gmail wasn't touched.
        msgs = list(session.scalars(select(EmailMessage)))
        assert len(msgs) == 1
        thread = session.get(EmailThread, msgs[0].thread_id)
        assert thread.gmail_thread_id.startswith("pending:")


def test_send_with_past_scheduled_for_falls_back_to_immediate(
    client: TestClient,
    db: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`scheduled_for` in the past is interpreted as send-now —
    we don't 400 because client clock skew of a few seconds is
    a real thing in the wild."""
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kw: Any) -> dict[str, Any]:
            return {"id": "gmail-msg-1", "threadId": "gmail-thr-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    res = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Inmediato",
            "body_text": "Cuerpo",
            "scheduled_for": past,
        },
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["scheduled_status"] is None
    assert body["gmail_message_id"] == "gmail-msg-1"


# -- /scheduled CRUD -------------------------------------------------


def test_list_scheduled_returns_only_own_pending(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
        mgr_id = _user_id(session, UserRole.MANAGER)
    mine = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
        subject="Mio",
    )
    _seed_pending_message(
        db.factory,
        user_id=mgr_id,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
        subject="Otro",
    )

    res = client.get(
        "/api/emails/scheduled", headers=auth_headers(client, "user")
    )
    assert res.status_code == 200
    body = res.json()
    assert [m["id"] for m in body] == [mine]


def test_cancel_scheduled_flips_status_and_blocks_repeat(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )

    res = client.post(
        f"/api/emails/scheduled/{msg_id}/cancel",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200
    assert res.json()["scheduled_status"] == "cancelled"

    # A cancelled row is no longer "pending" — another cancel
    # returns 404 (the route only acts on pending state).
    again = client.post(
        f"/api/emails/scheduled/{msg_id}/cancel",
        headers=auth_headers(client, "user"),
    )
    assert again.status_code == 404


def test_update_scheduled_rewrites_time_and_body(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )

    new_time = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    res = client.put(
        f"/api/emails/scheduled/{msg_id}",
        json={"scheduled_for": new_time, "subject": "Editado"},
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200, res.text
    assert res.json()["subject"] == "Editado"
    assert res.json()["scheduled_for"].startswith(new_time[:16])


def test_update_scheduled_rejects_past_time(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    res = client.put(
        f"/api/emails/scheduled/{msg_id}",
        json={"scheduled_for": past},
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 400


def test_scheduled_endpoints_hide_other_users_rows(
    client: TestClient, db: _Fixture
) -> None:
    with db.factory() as session:
        mgr_id = _user_id(session, UserRole.MANAGER)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=mgr_id,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )

    for path in (
        f"/api/emails/scheduled/{msg_id}/cancel",
        f"/api/emails/scheduled/{msg_id}",
    ):
        res = client.request(
            "POST" if path.endswith("/cancel") else "PUT",
            path,
            json={"scheduled_for": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
            if path.endswith(msg_id)
            else None,
            headers=auth_headers(client, "user"),
        )
        assert res.status_code == 404


# -- sweep -----------------------------------------------------------


def test_sweep_skips_pending_rows_in_the_future(db: _Fixture) -> None:
    """No pending row whose target is in the future should be
    touched. We don't seed a Gmail integration since the sweep
    must never reach the Gmail path for these."""
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=2),
    )

    with patch("app.email_scheduled_sweep.get_engine", return_value=db.engine):
        summary = scheduled_send_sweep()
    assert summary == {"sent": 0, "failed": 0}


def test_sweep_marks_row_failed_when_gmail_raises(
    db: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) - timedelta(minutes=1),
    )

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("gmail down")

    monkeypatch.setattr(
        "app.email_scheduled_sweep._send_one", _boom
    )

    with patch("app.email_scheduled_sweep.get_engine", return_value=db.engine):
        summary = scheduled_send_sweep()
    assert summary == {"sent": 0, "failed": 1}

    with db.factory() as session:
        msg = session.get(EmailMessage, msg_id)
        assert msg.scheduled_status == "failed"


def test_sweep_purges_orphan_pending_thread(db: _Fixture) -> None:
    """A sentinel thread whose only message was cancelled should
    be cleaned out by the sweep so the inbox doesn't accumulate
    ghost rows."""
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    msg_id = _seed_pending_message(
        db.factory,
        user_id=uid,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )
    with db.factory() as session:
        msg = session.get(EmailMessage, msg_id)
        msg.scheduled_status = EmailScheduledStatus.CANCELLED.value
        session.commit()
        sentinel_thread_id = msg.thread_id

    with patch("app.email_scheduled_sweep.get_engine", return_value=db.engine):
        scheduled_send_sweep()

    with db.factory() as session:
        assert session.get(EmailThread, sentinel_thread_id) is None
        assert session.get(EmailMessage, msg_id) is None
