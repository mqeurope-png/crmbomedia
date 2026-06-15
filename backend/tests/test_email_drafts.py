"""Sprint Email v2.4d — drafts CRUD + state=sent filter tests.

Drafts CRUD is verified end-to-end through the HTTP layer; the
state=sent virtual view is exercised against the existing list
endpoint with a mix of sent / pending / archived rows so the
filter's "outbound + sent_at NOT NULL + state=inbox" intent
doesn't drift.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailDraft,
    EmailMessage,
    EmailScheduledStatus,
    EmailThread,
    EmailThreadState,
    User,
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


# -- drafts CRUD ---------------------------------------------------


def test_create_list_update_delete_draft_round_trip(
    client: TestClient,
) -> None:
    headers = auth_headers(client, "user")

    # Empty list at the start.
    res = client.get("/api/email-drafts", headers=headers)
    assert res.status_code == 200
    assert res.json() == []

    # Create.
    res = client.post(
        "/api/email-drafts",
        json={"subject": "Borrador 1", "to_emails": ["lead@example.com"]},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    draft_id = res.json()["id"]
    assert res.json()["to_emails"] == ["lead@example.com"]

    # List now has one entry.
    res = client.get("/api/email-drafts", headers=headers)
    assert len(res.json()) == 1

    # Update overwrites snapshot-style — empty cc clears any
    # previous list.
    res = client.put(
        f"/api/email-drafts/{draft_id}",
        json={
            "subject": "Borrador renombrado",
            "to_emails": ["lead2@example.com"],
            "cc_emails": [],
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["subject"] == "Borrador renombrado"
    assert res.json()["cc_emails"] is None  # snapshot overwrites

    # Delete.
    res = client.delete(f"/api/email-drafts/{draft_id}", headers=headers)
    assert res.status_code == 204

    res = client.get("/api/email-drafts", headers=headers)
    assert res.json() == []


def test_draft_endpoints_hide_other_users_rows(
    client: TestClient, db: _Fixture
) -> None:
    """A draft owned by manager must be invisible to user — 404
    on GET / PUT / DELETE so probing by id doesn't leak existence."""
    with db.factory() as session:
        mgr_id = _user_id(session, UserRole.MANAGER)
        now = datetime.now(UTC)
        draft = EmailDraft(
            user_id=mgr_id,
            subject="Foreign",
            created_at=now,
            updated_at=now,
        )
        session.add(draft)
        session.commit()
        draft_id = draft.id

    headers = auth_headers(client, "user")
    for verb, path in (
        ("get", f"/api/email-drafts/{draft_id}"),
        ("delete", f"/api/email-drafts/{draft_id}"),
    ):
        res = client.request(verb.upper(), path, headers=headers)
        assert res.status_code == 404
    res = client.put(
        f"/api/email-drafts/{draft_id}",
        json={"subject": "hijacked"},
        headers=headers,
    )
    assert res.status_code == 404


def test_list_drafts_ordered_by_updated_at_desc(
    client: TestClient, db: _Fixture
) -> None:
    headers = auth_headers(client, "user")
    # Create two drafts, second is more recent (updated_at = now
    # at create time).
    first = client.post(
        "/api/email-drafts",
        json={"subject": "Old"},
        headers=headers,
    ).json()
    # Force a measurable updated_at gap.
    with db.factory() as session:
        row = session.get(EmailDraft, first["id"])
        row.updated_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    second = client.post(
        "/api/email-drafts",
        json={"subject": "New"},
        headers=headers,
    ).json()

    res = client.get("/api/email-drafts", headers=headers)
    ids = [r["id"] for r in res.json()]
    assert ids == [second["id"], first["id"]]


# -- state=sent filter ---------------------------------------------


def test_state_sent_filters_to_user_outbound_inbox_threads(
    client: TestClient, db: _Fixture
) -> None:
    """The Enviados view shows threads where the current user
    initiated at least one sent outbound message, and the thread
    isn't archived/trashed/spam'd. Pending scheduled messages
    must NOT make a thread show up here."""
    now = datetime.now(UTC)
    with db.factory() as session:
        uid = _user_id(session, UserRole.USER)
        mgr_id = _user_id(session, UserRole.MANAGER)

        # A — sent thread by current user → should appear.
        sent_thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_account_user_id=uid,
            gmail_thread_id="thr-sent",
            subject="Sent",
            first_message_at=now,
            last_message_at=now,
            message_count=1,
        )
        # B — pending scheduled only, not yet sent → must NOT appear.
        pending_thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_account_user_id=uid,
            gmail_thread_id="thr-pending",
            subject="Pending",
            first_message_at=now,
            last_message_at=now,
            message_count=0,
        )
        # C — sent but archived → must NOT appear in Enviados.
        archived_thread = EmailThread(
            initiated_by_user_id=uid,
            gmail_account_user_id=uid,
            gmail_thread_id="thr-archived",
            subject="Archived",
            first_message_at=now,
            last_message_at=now,
            message_count=1,
            state=EmailThreadState.ARCHIVED,
            is_archived=True,
        )
        # D — sent by another user, not current → must NOT appear.
        foreign_thread = EmailThread(
            initiated_by_user_id=mgr_id,
            gmail_account_user_id=mgr_id,
            gmail_thread_id="thr-foreign",
            subject="Foreign",
            first_message_at=now,
            last_message_at=now,
            message_count=1,
        )
        session.add_all(
            [sent_thread, pending_thread, archived_thread, foreign_thread]
        )
        session.flush()

        # Real sent outbound for A.
        session.add(
            EmailMessage(
                thread_id=sent_thread.id,
                gmail_message_id="g-1",
                gmail_account_user_id=uid,
                direction="outbound",
                from_email="info@bomedia.net",
                to_emails_json=json.dumps(["lead@example.com"]),
                subject="Sent",
                sent_at=now,
                created_by_user_id=uid,
            )
        )
        # Pending outbound for B.
        session.add(
            EmailMessage(
                thread_id=pending_thread.id,
                gmail_account_user_id=uid,
                direction="outbound",
                from_email="info@bomedia.net",
                to_emails_json=json.dumps(["lead@example.com"]),
                subject="Pending",
                sent_at=None,
                scheduled_for=now + timedelta(hours=1),
                scheduled_status=EmailScheduledStatus.PENDING.value,
                created_by_user_id=uid,
            )
        )
        # Sent outbound for C (archived).
        session.add(
            EmailMessage(
                thread_id=archived_thread.id,
                gmail_message_id="g-2",
                gmail_account_user_id=uid,
                direction="outbound",
                from_email="info@bomedia.net",
                to_emails_json=json.dumps(["lead@example.com"]),
                subject="Archived",
                sent_at=now,
                created_by_user_id=uid,
            )
        )
        # Sent outbound for D — but by the manager.
        session.add(
            EmailMessage(
                thread_id=foreign_thread.id,
                gmail_message_id="g-3",
                gmail_account_user_id=mgr_id,
                direction="outbound",
                from_email="mgr@bomedia.net",
                to_emails_json=json.dumps(["lead@example.com"]),
                subject="Foreign",
                sent_at=now,
                created_by_user_id=mgr_id,
            )
        )
        session.commit()
        sent_id = sent_thread.id

    res = client.get(
        "/api/emails/threads?state=sent",
        headers=auth_headers(client, "user"),
    )
    assert res.status_code == 200, res.text
    ids = {t["id"] for t in res.json()["items"]}
    assert ids == {sent_id}
