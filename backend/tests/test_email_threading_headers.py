"""Regression: ensure In-Reply-To + References headers fire on every
send path that should carry them.

Bart confirmed in prod that a reply via the v2.4 modal shipped
without In-Reply-To / References. The path was working in PR #104
and broke between then and v2.4d (a wrapper accidentally swallowed
the threading lookup). This file pins the three entry points so
the next refactor can't regress them silently:

- direct POST /api/emails/send with in_reply_to_message_id set
- scheduled send sweep picking a pending row whose parent is a
  real sent message
- send-from-draft (POST /api/email-drafts/{id}/send)
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

from app.db.session import get_session
from app.email_scheduled_sweep import scheduled_send_sweep
from app.main import app
from app.models.crm import (
    Base,
    EmailDirection,
    EmailMessage,
    EmailScheduledStatus,
    EmailThread,
    User,
    UserEmailAliasPref,
    UserRole,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)

PARENT_GMAIL_ID = "gmail-msg-parent"
PARENT_RFC_MID = "<CABc123abc@mail.gmail.com>"


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker
    sent: list[dict[str, Any]]


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
    yield _Fixture(engine=engine, factory=factory, sent=[])
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


def _seed_gmail(factory: sessionmaker, *, user_id: str) -> None:
    # PR-OAuth-Google-Unificado. Cuenta Google org compartida + alias
    # default per-user de `user_id`.
    with factory() as session:
        seed_org_google_integration(session, connected_by_user_id=user_id)
        session.add(
            UserEmailAliasPref(
                user_id=user_id,
                alias_email="info@bomedia.net",
                is_allowed=True,
                is_default=True,
            )
        )
        session.commit()


def _seed_parent_thread_and_message(
    factory: sessionmaker, *, user_id: str
) -> str:
    """Seed a real sent thread + outbound message whose
    `gmail_message_id` matches `PARENT_GMAIL_ID`. Returns the
    EmailMessage.id the composer would pass as `replyTo.messageId`."""
    now = datetime.now(UTC)
    with factory() as session:
        thread = EmailThread(
            initiated_by_user_id=user_id,
            gmail_account_user_id=user_id,
            gmail_thread_id="gmail-thr-parent",
            subject="Hilo padre",
            first_message_at=now,
            last_message_at=now,
            message_count=1,
        )
        session.add(thread)
        session.flush()
        parent = EmailMessage(
            thread_id=thread.id,
            gmail_message_id=PARENT_GMAIL_ID,
            gmail_account_user_id=user_id,
            direction=EmailDirection.OUTBOUND,
            from_email="info@bomedia.net",
            to_emails_json=json.dumps(["lead@example.com"]),
            subject="Hilo padre",
            sent_at=now,
            created_by_user_id=user_id,
        )
        session.add(parent)
        session.commit()
        return parent.id


class _ReplyAwareFakeClient:
    """Drop-in for `GmailClient` that records every send + serves a
    Message-Id header on `get_message` lookups so the threading
    path inside `send_email` actually returns it."""

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        self.sent: list[dict[str, Any]] = []

    def get_message(self, message_id: str) -> dict[str, Any]:
        if message_id == PARENT_GMAIL_ID:
            return {
                "id": PARENT_GMAIL_ID,
                "threadId": "gmail-thr-parent",
                "payload": {
                    "headers": [
                        {"name": "Message-Id", "value": PARENT_RFC_MID},
                        {"name": "Subject", "value": "Hilo padre"},
                    ]
                },
            }
        return {"id": message_id, "payload": {"headers": []}}

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {
            "id": f"gmail-msg-child-{len(self.sent)}",
            "threadId": "gmail-thr-parent",
        }


def _install_fake(monkeypatch: pytest.MonkeyPatch) -> _ReplyAwareFakeClient:
    """Wire the fake so every entry into `_client_for` returns the
    same instance — important for the sweep which constructs the
    client from `_client_for` itself."""
    fake = _ReplyAwareFakeClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )
    return fake


# -- direct send ----------------------------------------------------


def test_direct_send_carries_in_reply_to_and_references(
    client: TestClient,
    db: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)
    parent_id = _seed_parent_thread_and_message(db.factory, user_id=uid)
    fake = _install_fake(monkeypatch)

    res = client.post(
        "/api/emails/send",
        json={
            "from_alias": "info@bomedia.net",
            "to": ["lead@example.com"],
            "subject": "Re: Hilo padre",
            "body_text": "Respuesta",
            "in_reply_to_message_id": parent_id,
        },
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 201, res.text

    assert fake.sent[0]["in_reply_to_message_id"] == PARENT_RFC_MID
    assert fake.sent[0]["references"] == [PARENT_RFC_MID]
    # Thread id must also chain so Gmail attaches to the same
    # conversation in the operator's mailbox.
    assert fake.sent[0]["thread_id"] == "gmail-thr-parent"


# -- send from draft ------------------------------------------------


def test_send_from_draft_carries_in_reply_to_and_references(
    client: TestClient,
    db: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)
    parent_id = _seed_parent_thread_and_message(db.factory, user_id=uid)
    fake = _install_fake(monkeypatch)

    headers = auth_headers(client, "user")
    # Create a draft that targets the parent.
    created = client.post(
        "/api/email-drafts",
        json={
            "from_alias": "info@bomedia.net",
            "subject": "Re: Hilo padre",
            "body_text": "Respuesta desde draft",
            "to_emails": ["lead@example.com"],
            "in_reply_to_message_id": parent_id,
        },
        headers=headers,
    ).json()

    res = client.post(
        f"/api/email-drafts/{created['id']}/send", headers=headers
    )
    assert res.status_code == 201, res.text

    assert fake.sent[0]["in_reply_to_message_id"] == PARENT_RFC_MID
    assert fake.sent[0]["references"] == [PARENT_RFC_MID]
    assert fake.sent[0]["thread_id"] == "gmail-thr-parent"


# -- scheduled send sweep -------------------------------------------


def test_scheduled_send_sweep_carries_in_reply_to_and_references(
    db: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending message scheduled as a reply must still ship the
    threading headers when the sweep actually sends it."""
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
    _seed_gmail(db.factory, user_id=uid)
    parent_id = _seed_parent_thread_and_message(db.factory, user_id=uid)
    fake = _install_fake(monkeypatch)

    # Stage a pending row that points at the parent. The persist
    # helper would normally re-use the parent's thread for a
    # reply, but we replicate the shape by hand to keep the test
    # focused on the threading invariant.
    now = datetime.now(UTC)
    with db.factory() as session:
        parent = session.get(EmailMessage, parent_id)
        msg = EmailMessage(
            thread_id=parent.thread_id,
            gmail_account_user_id=uid,
            direction=EmailDirection.OUTBOUND,
            from_email="info@bomedia.net",
            to_emails_json=json.dumps(["lead@example.com"]),
            subject="Re: Hilo padre",
            body_text="Respuesta programada",
            sent_at=None,
            scheduled_for=now - timedelta(minutes=1),
            scheduled_status=EmailScheduledStatus.PENDING.value,
            created_by_user_id=uid,
        )
        session.add(msg)
        session.commit()

    with patch("app.email_scheduled_sweep.get_engine", return_value=db.engine):
        summary = scheduled_send_sweep()
    assert summary == {"sent": 1, "failed": 0}

    assert fake.sent, "sweep never called Gmail.send_message"
    assert fake.sent[0]["in_reply_to_message_id"] == PARENT_RFC_MID
    assert fake.sent[0]["references"] == [PARENT_RFC_MID]
    assert fake.sent[0]["thread_id"] == "gmail-thr-parent"
