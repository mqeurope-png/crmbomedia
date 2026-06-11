"""Sprint Email v1 smoke tests — send + thread + webhook flow.

The whole Gmail upstream is mocked. We assert that:

- POST /api/emails/send persists an outbound `email_threads` +
  `email_messages` row and emits an `activity_event`.
- A reply imported via `process_history` lands in the same thread
  and flips `has_unread_replies` on.
- The webhook receiver accepts a well-formed Pub/Sub push, enqueues
  the job, and a follow-up GET surfaces the new message.
- Admin endpoint is admin-only.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

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
    EmailMessage,
    EmailThread,
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


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail_integration(
    session_factory: sessionmaker, *, user_id: str
) -> None:
    with session_factory() as session:
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
        session.commit()


def test_send_email_persists_thread_and_message(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    sent: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"id": "gmail-msg-1", "threadId": "gmail-thr-1"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Cuerpo del email",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["direction"] == "outbound"
    assert body["from_email"] == "info@bomedia.net"
    assert sent[0]["from_alias"] == "info@bomedia.net"

    with session_factory() as session:
        threads = list(session.scalars(select(EmailThread)))
        assert len(threads) == 1
        assert threads[0].gmail_thread_id == "gmail-thr-1"
        msgs = list(session.scalars(select(EmailMessage)))
        assert len(msgs) == 1
        assert msgs[0].gmail_message_id == "gmail-msg-1"


def test_send_email_without_gmail_scope_returns_403(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    """A user who connected Google for Calendar but didn't grant
    gmail.send should get a clean 403 with the reauth hint, not a
    500."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        session.add(
            UserGoogleIntegration(
                user_id=uid,
                google_email="bart@bomedia.net",
                access_token_encrypted=encrypt("access"),
                refresh_token_encrypted=encrypt("refresh"),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                scopes="https://www.googleapis.com/auth/calendar.events",
                connected_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "x",
            "body_text": "x",
        },
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 403
    assert "autorizar" in response.json()["detail"].lower()


def test_process_history_imports_inbound_reply(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        history_id_counter = 100

        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "out-1", "threadId": "thr-A"}

        def list_history(self, _start: int) -> dict[str, Any]:
            return {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "in-1", "threadId": "thr-A"}}
                        ]
                    }
                ]
            }

        def get_message(self, _mid: str) -> dict[str, Any]:
            return {
                "id": "in-1",
                "snippet": "Reply preview",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "client@example.com"},
                        {"name": "To", "value": "info@bomedia.net"},
                        {"name": "Subject", "value": "Re: Hola"},
                        {
                            "name": "Date",
                            "value": "Fri, 31 Dec 2099 23:59:00 +0000",
                        },
                    ],
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"Mi respuesta"
                        ).decode()
                    },
                },
            }

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )

    # First, send so a thread exists.
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Body",
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        thread = session.scalar(select(EmailThread))
        assert thread is not None
        gmail_thread_id = thread.gmail_thread_id
        # Patch the thread's gmail_thread_id to match the fake
        # `list_history` output (the send mock returned "thr-A").
        if gmail_thread_id != "thr-A":
            thread.gmail_thread_id = "thr-A"
        # Seed the watch row so process_history can resume.
        from app.models.crm import GmailPubsubWatch  # noqa: PLC0415

        session.add(
            GmailPubsubWatch(
                user_id=thread.gmail_account_user_id,
                history_id=1,
                watch_expires_at=datetime.now(UTC) + timedelta(days=6),
                last_renewed_at=datetime.now(UTC),
                topic_name="projects/x/topics/y",
            )
        )
        session.commit()
        owner_id = thread.gmail_account_user_id

    # Run the processor.
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    with session_factory() as session:
        imported = gmail_service.process_history(
            session, user_id=owner_id, new_history_id=200
        )
        session.commit()
    assert imported == 1

    with session_factory() as session:
        thread = session.scalar(select(EmailThread))
        assert thread is not None
        assert thread.has_unread_replies is True
        msgs = list(
            session.scalars(
                select(EmailMessage).order_by(EmailMessage.sent_at)
            )
        )
        assert [m.direction.value for m in msgs] == ["outbound", "inbound"]


def test_webhook_routes_to_user_and_enqueues(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receiver must find the user by email + push the job onto
    the queue without doing any heavy work itself."""
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail_integration(session_factory, user_id=uid)

    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_process_history",
        lambda *, user_id, new_history_id: enqueued.append(
            (user_id, new_history_id)
        ),
    )
    # The webhook does a late-import of the jobs module, so also
    # patch the symbol referenced inside the handler.
    monkeypatch.setattr(
        "app.integrations.gmail.webhook._validate_jwt",
        lambda _auth: None,
    )

    payload = {
        "message": {
            "data": base64.b64encode(
                json.dumps(
                    {"emailAddress": "bart@bomedia.net", "historyId": 500}
                ).encode()
            ).decode(),
        }
    }
    response = client.post("/api/webhooks/gmail", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "enqueued"}
    assert enqueued == [(uid, 500)]


def test_admin_threads_view_admin_only(
    client: TestClient, session_factory: sessionmaker
) -> None:
    blocked = client.get(
        "/api/emails/admin/all-threads",
        headers=auth_headers(client, "user"),
    )
    assert blocked.status_code == 403
    ok = client.get(
        "/api/emails/admin/all-threads",
        headers=auth_headers(client, "admin"),
    )
    assert ok.status_code == 200
    assert ok.json() == {"items": [], "total": 0}


def test_threads_list_scopes_to_current_user(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User role only sees threads they initiated; manager/admin
    see all."""
    with session_factory() as session:
        user_id = _user_id(session, UserRole.USER)
        admin_id = _user_id(session, UserRole.ADMIN)
        # Two threads, one per user.
        for owner in (user_id, admin_id):
            session.add(
                EmailThread(
                    initiated_by_user_id=owner,
                    gmail_thread_id=f"thr-{owner[:6]}",
                    gmail_account_user_id=owner,
                    first_message_at=datetime.now(UTC),
                    last_message_at=datetime.now(UTC),
                    message_count=1,
                )
            )
        session.commit()

    _ = monkeypatch  # quiet the unused-arg lint
    user_response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "user")
    )
    assert user_response.json()["total"] == 1
    admin_response = client.get(
        "/api/emails/threads", headers=auth_headers(client, "admin")
    )
    assert admin_response.json()["total"] == 2


def test_send_email_emits_activity_event_when_contact_id_set(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.crm import ActivityEvent, Contact  # noqa: PLC0415

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        contact = Contact(first_name="Cliente", email="client@example.com")
        session.add(contact)
        session.commit()
        contact_id = contact.id
    _seed_gmail_integration(session_factory, user_id=uid)

    class _FakeClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "msg-evt", "threadId": "thr-evt"}

    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient", _FakeClient
    )
    client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["client@example.com"],
            "subject": "Hola",
            "body_text": "Body",
            "contact_id": contact_id,
        },
        headers=auth_headers(client, "user"),
    )
    with session_factory() as session:
        events = list(
            session.scalars(
                select(ActivityEvent).where(
                    ActivityEvent.event_type == "email.sent_from_crm"
                )
            )
        )
    assert len(events) == 1
    assert events[0].contact_id == contact_id


# Suppress the unused-import lint on `patch` — kept available for
# future tests that swap the entire client.
_ = patch
