"""QoL sprint backend tests — 5 issues:

1. Filtro `notes_content` matchea contactos con notas que contienen X.
2. (UI only, no backend test.)
3. Bulk export CSV abierto a `manager` (antes admin-only o no existía).
4. /api/tasks/my-buckets soporta scope=mine/team + user_id.
5. /api/emails/threads soporta scope=mine/team + team_user_id, default mine.
"""
from __future__ import annotations

import json
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
    Contact,
    ContactNote,
    EmailThread,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.services.segments.engine import build_filter
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


# === Issue 1: notes_content filter ===================================


def test_notes_content_contains_matches_contact_with_note(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        a = Contact(first_name="A", email="a@a.com")
        b = Contact(first_name="B", email="b@b.com")
        c = Contact(first_name="C", email="c@c.com")
        session.add_all([a, b, c])
        session.flush()
        session.add(
            ContactNote(
                contact_id=a.id, content="Cliente Brevo VIP", source="manual"
            )
        )
        session.add(
            ContactNote(
                contact_id=b.id, content="Solo correo Agile, no Brevo aún",
                source="manual",
            )
        )
        # c sin notas.
        session.commit()
        ids = {"a": a.id, "b": b.id, "c": c.id}

    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "notes_content",
                "comparator": "contains",
                "value": "brevo",  # lowercase → la query lo lowercasea también
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        matched = set(session.scalars(select(Contact.id).where(flt)))
        assert matched == {ids["a"], ids["b"]}


def test_notes_content_is_empty_matches_contacts_without_notes(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as session:
        a = Contact(first_name="A", email="a@a.com")
        b = Contact(first_name="B", email="b@b.com")
        session.add_all([a, b])
        session.flush()
        session.add(
            ContactNote(contact_id=a.id, content="con notas", source="manual")
        )
        session.commit()
        ids = {"a": a.id, "b": b.id}

    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "notes_content",
                "comparator": "is_empty",
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        matched = set(session.scalars(select(Contact.id).where(flt)))
        assert matched == {ids["b"]}


# === Issue 3: bulk-export-csv role + content =========================


def test_bulk_export_csv_works_for_manager(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        c = Contact(
            first_name="Export",
            last_name="Lead",
            email="export@x.com",
            commercial_status="new",
        )
        session.add(c)
        session.commit()
        cid = c.id

    resp = client.post(
        "/api/contacts/bulk-export-csv",
        headers=auth_headers(client, "manager"),
        json={"contact_ids": [cid]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    header, row = body.strip().split("\n", 1)
    assert "id" in header and "email" in header
    assert "export@x.com" in row


def test_bulk_export_csv_rejects_user_role(
    client: TestClient, session_factory: sessionmaker
) -> None:
    with session_factory() as session:
        c = Contact(first_name="X", email="x@x.com")
        session.add(c)
        session.commit()
        cid = c.id
    resp = client.post(
        "/api/contacts/bulk-export-csv",
        headers=auth_headers(client, "user"),
        json={"contact_ids": [cid]},
    )
    assert resp.status_code == 403


# === Issue 4: /api/tasks/my-buckets scope ============================


def test_my_buckets_default_scope_mine(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        session.add_all(
            [
                Task(
                    title="own",
                    assigned_user_id=user_uid,
                    created_by_user_id=user_uid,
                    status=TaskStatus.PENDING.value,
                ),
                Task(
                    title="other",
                    assigned_user_id=manager_uid,
                    created_by_user_id=manager_uid,
                    status=TaskStatus.PENDING.value,
                ),
            ]
        )
        session.commit()

    resp = client.get(
        "/api/tasks/my-buckets",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    body = resp.json()
    buckets = ("overdue", "today", "tomorrow", "later", "no_date")
    titles = [t["title"] for b in buckets for t in body[b]]
    assert "own" in titles
    assert "other" not in titles


def test_my_buckets_scope_team_shows_everyone(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        session.add_all(
            [
                Task(
                    title="user task",
                    assigned_user_id=user_uid,
                    created_by_user_id=user_uid,
                    status=TaskStatus.PENDING.value,
                ),
                Task(
                    title="manager task",
                    assigned_user_id=manager_uid,
                    created_by_user_id=manager_uid,
                    status=TaskStatus.PENDING.value,
                ),
            ]
        )
        session.commit()
    resp = client.get(
        "/api/tasks/my-buckets?scope=team",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    titles = []
    for bucket in ("overdue", "today", "tomorrow", "later", "no_date"):
        titles.extend(t["title"] for t in resp.json()[bucket])
    assert {"user task", "manager task"}.issubset(set(titles))


def test_my_buckets_scope_team_rejects_user_role(
    client: TestClient,
) -> None:
    resp = client.get(
        "/api/tasks/my-buckets?scope=team",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403


def test_my_buckets_scope_team_with_user_id_filters(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        session.add_all(
            [
                Task(
                    title="user task",
                    assigned_user_id=user_uid,
                    created_by_user_id=user_uid,
                    status=TaskStatus.PENDING.value,
                ),
                Task(
                    title="manager task",
                    assigned_user_id=manager_uid,
                    created_by_user_id=manager_uid,
                    status=TaskStatus.PENDING.value,
                ),
            ]
        )
        session.commit()
    resp = client.get(
        f"/api/tasks/my-buckets?scope=team&user_id={user_uid}",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    titles = []
    for bucket in ("overdue", "today", "tomorrow", "later", "no_date"):
        titles.extend(t["title"] for t in resp.json()[bucket])
    assert "user task" in titles
    assert "manager task" not in titles


# === Issue 5: /api/emails/threads scope ==============================


def _seed_thread(
    session: Session, *, initiated_by: str, subject: str
) -> str:
    now = datetime.now(UTC)
    thread = EmailThread(
        initiated_by_user_id=initiated_by,
        gmail_account_user_id=initiated_by,
        gmail_thread_id=f"gthread-{subject}",
        subject=subject,
        first_message_at=now,
        last_message_at=now,
    )
    session.add(thread)
    session.flush()
    return thread.id


def test_emails_threads_default_scope_mine_for_manager(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """QoL: el manager por defecto ve solo SUS threads (antes veía
    todos)."""
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_thread(session, initiated_by=user_uid, subject="userT")
        _seed_thread(session, initiated_by=manager_uid, subject="managerT")
        session.commit()
    resp = client.get(
        "/api/emails/threads",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200, resp.text
    subjects = [t["subject"] for t in resp.json()["items"]]
    assert "managerT" in subjects
    assert "userT" not in subjects


def test_emails_threads_scope_team_shows_all(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_thread(session, initiated_by=user_uid, subject="userT")
        _seed_thread(session, initiated_by=manager_uid, subject="managerT")
        session.commit()
    resp = client.get(
        "/api/emails/threads?scope=team",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    subjects = {t["subject"] for t in resp.json()["items"]}
    assert {"userT", "managerT"}.issubset(subjects)


def test_emails_threads_scope_team_rejects_user_role(
    client: TestClient,
) -> None:
    resp = client.get(
        "/api/emails/threads?scope=team",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403


def test_emails_threads_scope_team_with_user_id_filter(
    client: TestClient, session_factory: sessionmaker
) -> None:
    user_uid = _user_id(session_factory, UserRole.USER)
    manager_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        _seed_thread(session, initiated_by=user_uid, subject="userT")
        _seed_thread(session, initiated_by=manager_uid, subject="managerT")
        session.commit()
    resp = client.get(
        f"/api/emails/threads?scope=team&team_user_id={user_uid}",
        headers=auth_headers(client, "manager"),
    )
    assert resp.status_code == 200
    subjects = {t["subject"] for t in resp.json()["items"]}
    assert subjects == {"userT"}


_ = json  # silence linter on unused import (future use)
