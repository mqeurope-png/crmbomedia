"""Sprint Email v2.4a tests — folders, labels, thread mutations.

Covers the new `app/api/emails_mailbox.py` router plus the filter
additions to the existing `/api/emails/threads` endpoint. We seed
threads directly in the DB to keep the suite focused on the
mailbox layer — the send/import flow is exercised by
test_emails.py and would just add noise here.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    EmailFolder,
    EmailMessage,
    EmailThread,
    EmailThreadLabel,
    EmailThreadState,
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


def _user_id(session: Session, role: UserRole) -> str:
    return session.scalar(select(User.id).where(User.role == role))


def _seed_thread(
    session: Session,
    *,
    owner_id: str,
    subject: str = "Subject",
    state: EmailThreadState = EmailThreadState.INBOX,
    folder_id: str | None = None,
    starred: bool = False,
    has_unread: bool = False,
    snooze_until: datetime | None = None,
    last_message_at: datetime | None = None,
    gmail_thread_id: str | None = None,
) -> EmailThread:
    now = datetime.now(UTC)
    thread = EmailThread(
        initiated_by_user_id=owner_id,
        gmail_account_user_id=owner_id,
        gmail_thread_id=gmail_thread_id or f"gthr-{subject}-{owner_id[:6]}",
        subject=subject,
        first_message_at=last_message_at or now,
        last_message_at=last_message_at or now,
        message_count=1,
        has_unread_replies=has_unread,
        state=state,
        folder_id=folder_id,
        is_starred=starred,
        snooze_until=snooze_until,
        is_archived=state == EmailThreadState.ARCHIVED,
    )
    session.add(thread)
    session.commit()
    session.refresh(thread)
    # Detach so the test can read scalar attrs (id, subject, …)
    # after the session context exits without triggering a refresh.
    session.expunge(thread)
    return thread


# -- folders --------------------------------------------------------


def test_folder_crud_round_trip(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")

    # Empty list.
    res = client.get("/api/emails/folders", headers=headers)
    assert res.status_code == 200
    assert res.json() == []

    # Create.
    res = client.post(
        "/api/emails/folders",
        json={"name": "Clientes", "color": "#3366ff", "icon": "users"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    folder = res.json()
    assert folder["name"] == "Clientes"
    assert folder["color"] == "#3366ff"
    assert folder["is_system"] is False
    folder_id = folder["id"]

    # List sees it.
    res = client.get("/api/emails/folders", headers=headers)
    assert len(res.json()) == 1

    # Nested folder.
    res = client.post(
        "/api/emails/folders",
        json={"name": "VIP", "parent_id": folder_id},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["parent_id"] == folder_id

    # Update.
    res = client.put(
        f"/api/emails/folders/{folder_id}",
        json={"name": "Clientes A", "color": "#22aa22"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Clientes A"

    # Delete.
    res = client.delete(f"/api/emails/folders/{folder_id}", headers=headers)
    assert res.status_code == 204


def test_folder_cannot_be_own_parent(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/emails/folders", json={"name": "x"}, headers=headers
    )
    folder_id = res.json()["id"]
    res = client.put(
        f"/api/emails/folders/{folder_id}",
        json={"name": "x", "parent_id": folder_id},
        headers=headers,
    )
    assert res.status_code == 400


def test_folder_isolation_between_users(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """A folder created by `user` must be invisible to `manager`
    even though manager has a privileged role on threads."""
    user_headers = auth_headers(client, "user")
    manager_headers = auth_headers(client, "manager")
    res = client.post(
        "/api/emails/folders", json={"name": "Mio"}, headers=user_headers
    )
    folder_id = res.json()["id"]

    res = client.get("/api/emails/folders", headers=manager_headers)
    assert res.json() == []

    res = client.put(
        f"/api/emails/folders/{folder_id}",
        json={"name": "Hijacked"},
        headers=manager_headers,
    )
    assert res.status_code == 404


def test_folder_system_cannot_be_modified(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """`is_system=true` rows are reserved for built-ins. The CRUD
    routes never produce them — we seed one directly to assert the
    protection."""
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        sys_folder = EmailFolder(
            user_id=uid, name="Bandeja", is_system=True
        )
        session.add(sys_folder)
        session.commit()
        sys_id = sys_folder.id

    res = client.put(
        f"/api/emails/folders/{sys_id}",
        json={"name": "Bandeja modificada"},
        headers=headers,
    )
    assert res.status_code == 400
    res = client.delete(f"/api/emails/folders/{sys_id}", headers=headers)
    assert res.status_code == 400


# -- labels ---------------------------------------------------------


def test_label_crud_and_unique_per_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")

    res = client.post(
        "/api/emails/labels",
        json={"name": "Leads", "color": "#ff8800"},
        headers=headers,
    )
    assert res.status_code == 201
    label = res.json()

    # Duplicate name -> 409.
    res = client.post(
        "/api/emails/labels", json={"name": "Leads"}, headers=headers
    )
    assert res.status_code == 409

    # Same name is allowed for a different user (unique is per-owner).
    other_headers = auth_headers(client, "manager")
    res = client.post(
        "/api/emails/labels", json={"name": "Leads"}, headers=other_headers
    )
    assert res.status_code == 201

    # Update + rename clash.
    res = client.post(
        "/api/emails/labels", json={"name": "Frio"}, headers=headers
    )
    second_id = res.json()["id"]
    res = client.put(
        f"/api/emails/labels/{second_id}",
        json={"name": "Leads"},
        headers=headers,
    )
    assert res.status_code == 409

    # Delete.
    res = client.delete(
        f"/api/emails/labels/{label['id']}", headers=headers
    )
    assert res.status_code == 204


# -- single-thread mutations ----------------------------------------


def test_star_unstar_and_state_transitions(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = _seed_thread(session, owner_id=uid, subject="Hilo 1")
        tid = thread.id

    res = client.post(f"/api/emails/threads/{tid}/star", headers=headers)
    assert res.status_code == 200 and res.json()["is_starred"] is True

    res = client.post(f"/api/emails/threads/{tid}/unstar", headers=headers)
    assert res.json()["is_starred"] is False

    res = client.post(f"/api/emails/threads/{tid}/archive", headers=headers)
    assert res.json()["state"] == "archived"

    res = client.post(f"/api/emails/threads/{tid}/restore", headers=headers)
    assert res.json()["state"] == "inbox"

    res = client.post(f"/api/emails/threads/{tid}/trash", headers=headers)
    assert res.json()["state"] == "trashed"

    res = client.post(f"/api/emails/threads/{tid}/spam", headers=headers)
    assert res.json()["state"] == "spam"


def test_move_to_folder_requires_owned_folder(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    other_headers = auth_headers(client, "manager")

    # Folder owned by manager.
    res = client.post(
        "/api/emails/folders", json={"name": "Mgr"}, headers=other_headers
    )
    other_folder_id = res.json()["id"]
    # Folder owned by user.
    res = client.post(
        "/api/emails/folders", json={"name": "Mine"}, headers=headers
    )
    own_folder_id = res.json()["id"]

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = _seed_thread(session, owner_id=uid, subject="Hilo")
        tid = thread.id

    # Move into someone else's folder = 404 (folder lookup is
    # scoped to the caller).
    res = client.post(
        f"/api/emails/threads/{tid}/move",
        json={"folder_id": other_folder_id},
        headers=headers,
    )
    assert res.status_code == 404

    # Move into own folder = success.
    res = client.post(
        f"/api/emails/threads/{tid}/move",
        json={"folder_id": own_folder_id},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["folder_id"] == own_folder_id

    # NULL folder_id = back to bandeja.
    res = client.post(
        f"/api/emails/threads/{tid}/move",
        json={"folder_id": None},
        headers=headers,
    )
    assert res.json()["folder_id"] is None


def test_snooze_requires_future_date_and_unsnooze_clears(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = _seed_thread(session, owner_id=uid)
        tid = thread.id

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    res = client.post(
        f"/api/emails/threads/{tid}/snooze",
        json={"snooze_until": past},
        headers=headers,
    )
    assert res.status_code == 400

    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    res = client.post(
        f"/api/emails/threads/{tid}/snooze",
        json={"snooze_until": future},
        headers=headers,
    )
    assert res.status_code == 200

    res = client.post(
        f"/api/emails/threads/{tid}/unsnooze", headers=headers
    )
    assert res.status_code == 200 and res.json()["snooze_until"] is None


def test_thread_label_add_remove_idempotent(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/emails/labels", json={"name": "Hot"}, headers=headers
    )
    label_id = res.json()["id"]

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = _seed_thread(session, owner_id=uid)
        tid = thread.id

    # Apply twice → still one row.
    for _ in range(2):
        res = client.post(
            f"/api/emails/threads/{tid}/labels/{label_id}", headers=headers
        )
        assert res.status_code == 200

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(EmailThreadLabel).where(
                    EmailThreadLabel.thread_id == tid
                )
            )
        )
        assert len(rows) == 1

    res = client.delete(
        f"/api/emails/threads/{tid}/labels/{label_id}", headers=headers
    )
    assert res.status_code == 204


def test_other_users_thread_returns_404_to_hide_existence(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """A non-privileged caller hitting someone else's thread gets
    404 (not 403) so probing by id doesn't leak existence."""
    headers = auth_headers(client, "user")
    with session_factory() as session:
        mgr_id = _user_id(session, UserRole.MANAGER)
        thread = _seed_thread(session, owner_id=mgr_id)
        tid = thread.id

    res = client.post(f"/api/emails/threads/{tid}/star", headers=headers)
    assert res.status_code == 404


# -- bulk operations ------------------------------------------------


def test_bulk_archive_skips_threads_caller_does_not_own(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        mgr_id = _user_id(session, UserRole.MANAGER)
        mine = [
            _seed_thread(session, owner_id=uid, subject=f"M{i}").id
            for i in range(3)
        ]
        theirs = _seed_thread(session, owner_id=mgr_id, subject="X").id

    res = client.post(
        "/api/emails/threads-bulk/archive",
        json={"thread_ids": mine + [theirs]},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["affected"] == 3

    with session_factory() as session:
        states = {
            t.id: t.state
            for t in session.scalars(
                select(EmailThread).where(
                    EmailThread.id.in_(mine + [theirs])
                )
            )
        }
        assert all(states[i] == EmailThreadState.ARCHIVED for i in mine)
        assert states[theirs] == EmailThreadState.INBOX


def test_bulk_label_add_and_remove(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    res = client.post(
        "/api/emails/labels", json={"name": "Batch"}, headers=headers
    )
    label_id = res.json()["id"]
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        tids = [
            _seed_thread(session, owner_id=uid, subject=f"L{i}").id
            for i in range(3)
        ]

    # Add to all three.
    res = client.post(
        "/api/emails/threads-bulk/labels/add",
        json={"thread_ids": tids, "label_id": label_id},
        headers=headers,
    )
    assert res.json()["affected"] == 3

    # Re-add is a no-op.
    res = client.post(
        "/api/emails/threads-bulk/labels/add",
        json={"thread_ids": tids, "label_id": label_id},
        headers=headers,
    )
    assert res.json()["affected"] == 0

    # Remove from two.
    res = client.post(
        "/api/emails/threads-bulk/labels/remove",
        json={"thread_ids": tids[:2], "label_id": label_id},
        headers=headers,
    )
    assert res.json()["affected"] == 2

    with session_factory() as session:
        remaining = list(
            session.scalars(
                select(EmailThreadLabel.thread_id).where(
                    EmailThreadLabel.label_id == label_id
                )
            )
        )
        assert remaining == [tids[2]]


def test_bulk_move_with_invalid_folder_returns_404(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        tids = [_seed_thread(session, owner_id=uid).id]
    res = client.post(
        "/api/emails/threads-bulk/move",
        json={"thread_ids": tids, "folder_id": "does-not-exist"},
        headers=headers,
    )
    assert res.status_code == 404


def test_bulk_mark_read_unread(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        tids = [
            _seed_thread(
                session, owner_id=uid, subject=f"R{i}", has_unread=True
            ).id
            for i in range(2)
        ]
        # Add an inbound message per thread so the read_at sweep
        # has something to flip.
        now = datetime.now(UTC)
        for tid in tids:
            session.add(
                EmailMessage(
                    thread_id=tid,
                    gmail_message_id=f"m-{tid}",
                    gmail_account_user_id=uid,
                    direction="inbound",
                    from_email="them@example.com",
                    to_emails_json=json.dumps(["me@example.com"]),
                    subject="r",
                    sent_at=now,
                )
            )
        session.commit()

    res = client.post(
        "/api/emails/threads-bulk/mark-read",
        json={"thread_ids": tids},
        headers=headers,
    )
    assert res.json()["affected"] == 2

    with session_factory() as session:
        for tid in tids:
            thread = session.get(EmailThread, tid)
            assert thread.has_unread_replies is False
            msgs = list(
                session.scalars(
                    select(EmailMessage).where(
                        EmailMessage.thread_id == tid
                    )
                )
            )
            assert all(m.read_at is not None for m in msgs)

    res = client.post(
        "/api/emails/threads-bulk/mark-unread",
        json={"thread_ids": tids},
        headers=headers,
    )
    assert res.status_code == 200
    with session_factory() as session:
        for tid in tids:
            assert session.get(EmailThread, tid).has_unread_replies is True


# -- list filters ---------------------------------------------------


def test_list_threads_default_excludes_non_inbox_states(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        inbox = _seed_thread(session, owner_id=uid, subject="In")
        archived = _seed_thread(
            session,
            owner_id=uid,
            subject="Ar",
            state=EmailThreadState.ARCHIVED,
        )
        trashed = _seed_thread(
            session,
            owner_id=uid,
            subject="Tr",
            state=EmailThreadState.TRASHED,
        )

    res = client.get("/api/emails/threads", headers=headers)
    ids = {t["id"] for t in res.json()["items"]}
    assert inbox.id in ids
    assert archived.id not in ids
    assert trashed.id not in ids

    res = client.get(
        "/api/emails/threads?state=archived", headers=headers
    )
    ids = {t["id"] for t in res.json()["items"]}
    assert ids == {archived.id}


def test_list_threads_filter_by_folder_and_label_and_starred(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    folder_res = client.post(
        "/api/emails/folders", json={"name": "Carpeta"}, headers=headers
    )
    folder_id = folder_res.json()["id"]
    label_res = client.post(
        "/api/emails/labels", json={"name": "Etiqueta"}, headers=headers
    )
    label_id = label_res.json()["id"]

    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        in_folder = _seed_thread(
            session, owner_id=uid, subject="F", folder_id=folder_id
        )
        starred = _seed_thread(
            session, owner_id=uid, subject="S", starred=True
        )
        _seed_thread(session, owner_id=uid, subject="P")
        labelled = _seed_thread(session, owner_id=uid, subject="L")
        session.add(
            EmailThreadLabel(
                thread_id=labelled.id,
                label_id=label_id,
                applied_at=datetime.now(UTC),
            )
        )
        session.commit()

    res = client.get(
        f"/api/emails/threads?folder_id={folder_id}", headers=headers
    )
    assert {t["id"] for t in res.json()["items"]} == {in_folder.id}

    res = client.get(
        "/api/emails/threads?folder_id=inbox", headers=headers
    )
    ids = {t["id"] for t in res.json()["items"]}
    assert in_folder.id not in ids
    assert starred.id in ids

    res = client.get(
        "/api/emails/threads?starred=true", headers=headers
    )
    assert {t["id"] for t in res.json()["items"]} == {starred.id}

    res = client.get(
        f"/api/emails/threads?label_id={label_id}", headers=headers
    )
    assert {t["id"] for t in res.json()["items"]} == {labelled.id}


def test_list_threads_hides_snoozed_by_default(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        future = datetime.now(UTC) + timedelta(hours=4)
        snoozed = _seed_thread(
            session, owner_id=uid, subject="Z", snooze_until=future
        )
        visible = _seed_thread(session, owner_id=uid, subject="V")

    res = client.get("/api/emails/threads", headers=headers)
    ids = {t["id"] for t in res.json()["items"]}
    assert visible.id in ids and snoozed.id not in ids

    res = client.get(
        "/api/emails/threads?include_snoozed=true", headers=headers
    )
    ids = {t["id"] for t in res.json()["items"]}
    assert snoozed.id in ids


def test_list_threads_since_until_window(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        old = _seed_thread(
            session,
            owner_id=uid,
            subject="Old",
            last_message_at=datetime.now(UTC) - timedelta(days=10),
        )
        recent = _seed_thread(
            session,
            owner_id=uid,
            subject="New",
            last_message_at=datetime.now(UTC) - timedelta(hours=1),
        )

    since = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    res = client.get(
        "/api/emails/threads",
        params={"since": since},
        headers=headers,
    )
    ids = {t["id"] for t in res.json()["items"]}
    assert ids == {recent.id}
    _ = old


def test_thread_detail_surfaces_labels(
    client: TestClient, session_factory: sessionmaker
) -> None:
    headers = auth_headers(client, "user")
    label_res = client.post(
        "/api/emails/labels", json={"name": "Inline"}, headers=headers
    )
    label_id = label_res.json()["id"]
    with session_factory() as session:
        uid = _user_id(session, UserRole.USER)
        thread = _seed_thread(session, owner_id=uid)
        session.add(
            EmailThreadLabel(
                thread_id=thread.id,
                label_id=label_id,
                applied_at=datetime.now(UTC),
            )
        )
        session.commit()
        tid = thread.id

    res = client.get(f"/api/emails/threads/{tid}", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert [lbl["id"] for lbl in body["labels"]] == [label_id]
    assert body["state"] == "inbox"
