"""Sprint Reglas-Assign — PR-B integration tests.

Endpoints CRUD `/api/contacts/{id}/assignments` + bulk via assignments
+ widgets dashboard EXISTS + filter builder con campos `assigned_users`
y `primary_user`.
"""
from __future__ import annotations

import json
from collections.abc import Generator

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
    ContactAssignment,
    ContactPipelineStage,
    Pipeline,
    PipelineStage,
    User,
    UserRole,
)
from app.repositories import assignments as assignments_repo
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
        seed.add(Contact(first_name="Alice", email="alice@example.com"))
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


def _contact_id(factory: sessionmaker) -> str:
    with factory() as session:
        return session.scalar(select(Contact.id))


def _user_id(factory: sessionmaker, role: UserRole) -> str:
    with factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


# -- CRUD endpoints --------------------------------------------------


def test_list_returns_empty_for_unassigned_contact(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    response = client.get(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_create_assignment_returns_full_payload(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    uid = _user_id(session_factory, UserRole.USER)
    response = client.post(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "user"),
        json={"user_id": uid, "is_primary": True, "notes": "Cliente VIP"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == uid
    assert body["is_primary"] is True
    assert body["source"] == "manual"
    assert body["notes"] == "Cliente VIP"
    assert body["user"]["email"] == "user@example.com"
    assert body["user"]["is_active"] is True

    # Caché owner_user_id sincronizado.
    with session_factory() as session:
        contact = session.get(Contact, cid)
        assert contact.owner_user_id == uid


def test_promote_clears_previous_primary(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    primary_uid = _user_id(session_factory, UserRole.MANAGER)
    watcher_uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=primary_uid, is_primary=True
        )
        watcher = assignments_repo.add_assignment(
            session, contact_id=cid, user_id=watcher_uid, is_primary=False
        )
        session.commit()
        watcher_id = watcher.id

    response = client.post(
        f"/api/contacts/{cid}/assignments/{watcher_id}/promote",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_primary"] is True
    assert body["user_id"] == watcher_uid

    with session_factory() as session:
        primaries = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid,
                    ContactAssignment.is_primary.is_(True),
                )
            )
        )
        assert len(primaries) == 1
        assert primaries[0].user_id == watcher_uid
        assert session.get(Contact, cid).owner_user_id == watcher_uid


def test_delete_assignment_recomputes_cache(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        row = assignments_repo.add_assignment(
            session, contact_id=cid, user_id=uid, is_primary=True
        )
        session.commit()
        aid = row.id

    response = client.delete(
        f"/api/contacts/{cid}/assignments/{aid}",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 204, response.text
    with session_factory() as session:
        assert session.get(Contact, cid).owner_user_id is None
        assert (
            list(
                session.scalars(
                    select(ContactAssignment).where(
                        ContactAssignment.contact_id == cid
                    )
                )
            )
            == []
        )


def test_create_rejects_inactive_user(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    with session_factory() as session:
        u = session.scalar(select(User).where(User.role == UserRole.VIEWER))
        u.is_active = False
        inactive_id = u.id
        session.commit()

    response = client.post(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "user"),
        json={"user_id": inactive_id, "is_primary": False},
    )
    assert response.status_code == 400, response.text


def test_viewer_cannot_mutate(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    uid = _user_id(session_factory, UserRole.USER)
    response = client.post(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "viewer"),
        json={"user_id": uid, "is_primary": False},
    )
    assert response.status_code == 403, response.text


def test_audit_row_written_on_create(
    client: TestClient, session_factory: sessionmaker
) -> None:
    from app.models.crm import AuditLog  # noqa: PLC0415

    cid = _contact_id(session_factory)
    uid = _user_id(session_factory, UserRole.USER)
    response = client.post(
        f"/api/contacts/{cid}/assignments",
        headers=auth_headers(client, "user"),
        json={"user_id": uid, "is_primary": True},
    )
    assert response.status_code == 201

    with session_factory() as session:
        actions = list(
            session.scalars(
                select(AuditLog.action).where(AuditLog.target_id == cid)
            )
        )
        assert "contact.assignment_added" in actions
        assert "contact.primary_changed" in actions


# -- bulk assign_owner uses repo (multi-comercial invariant) --------


def test_bulk_assign_owner_creates_assignment_rows(
    client: TestClient, session_factory: sessionmaker
) -> None:
    cid = _contact_id(session_factory)
    owner_uid = _user_id(session_factory, UserRole.USER)

    response = client.post(
        "/api/contacts/bulk-action",
        headers=auth_headers(client, "manager"),
        json={
            "contact_ids": [cid],
            "action": "assign_owner",
            "payload": {"owner_user_id": owner_uid},
        },
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ContactAssignment).where(
                    ContactAssignment.contact_id == cid
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].user_id == owner_uid
        assert rows[0].is_primary is True
        assert rows[0].source == "manual"
        assert session.get(Contact, cid).owner_user_id == owner_uid


# -- dashboard widgets use EXISTS (visible para secundarios también)


def test_pipeline_summary_counts_secondaries(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Un comercial que es watcher (secondary) de un contacto debe ver
    su pipeline-stage en el resumen, no solo si es primary."""
    cid = _contact_id(session_factory)
    primary_uid = _user_id(session_factory, UserRole.MANAGER)
    watcher_uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=primary_uid, is_primary=True
        )
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=watcher_uid, is_primary=False
        )
        pipeline = Pipeline(name="Ventas", owner_user_id=watcher_uid)
        session.add(pipeline)
        session.flush()
        stage = PipelineStage(
            pipeline_id=pipeline.id, name="Lead", position=0
        )
        session.add(stage)
        session.flush()
        session.add(
            ContactPipelineStage(
                contact_id=cid, pipeline_id=pipeline.id, stage_id=stage.id
            )
        )
        session.commit()

    response = client.get(
        "/api/dashboard/pipeline-summary",
        headers=auth_headers(client, "user"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    counts = {s["name"]: s["count"] for s in body[0]["stages"]}
    assert counts == {"Lead": 1}


# -- segment engine compiles new fields -----------------------------


def test_engine_assigned_users_contains_any(
    session_factory: sessionmaker,
) -> None:
    from app.services.segments.engine import build_filter  # noqa: PLC0415

    cid = _contact_id(session_factory)
    target_uid = _user_id(session_factory, UserRole.USER)
    other_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        # Contact 1 has target as watcher only (not primary).
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=other_uid, is_primary=True
        )
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=target_uid, is_primary=False
        )
        # Contact 2 has neither.
        c2 = Contact(first_name="B", email="b@example.com")
        session.add(c2)
        session.commit()
        c2_id = c2.id

    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "assigned_users",
                "comparator": "contains_any",
                "value": [target_uid],
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        matches = {
            r for r in session.scalars(select(Contact.id).where(flt))
        }
        assert cid in matches
        assert c2_id not in matches


def test_engine_primary_user_eq(session_factory: sessionmaker) -> None:
    from app.services.segments.engine import build_filter  # noqa: PLC0415

    cid = _contact_id(session_factory)
    primary_uid = _user_id(session_factory, UserRole.USER)
    watcher_uid = _user_id(session_factory, UserRole.MANAGER)
    with session_factory() as session:
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=primary_uid, is_primary=True
        )
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=watcher_uid, is_primary=False
        )
        session.commit()

    # primary_user eq=watcher → 0 matches (watcher is NOT primary).
    tree_neg = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "primary_user",
                "comparator": "eq",
                "value": watcher_uid,
            }
        ],
    }
    with session_factory() as session:
        flt = build_filter(tree_neg)
        assert session.scalars(select(Contact.id).where(flt)).all() == []

    # primary_user eq=primary → 1 match.
    tree_pos = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "primary_user",
                "comparator": "eq",
                "value": primary_uid,
            }
        ],
    }
    with session_factory() as session:
        flt = build_filter(tree_pos)
        ids = list(session.scalars(select(Contact.id).where(flt)))
        assert ids == [cid]


def test_engine_assigned_users_is_empty(session_factory: sessionmaker) -> None:
    from app.services.segments.engine import build_filter  # noqa: PLC0415

    cid = _contact_id(session_factory)
    uid = _user_id(session_factory, UserRole.USER)
    with session_factory() as session:
        assignments_repo.add_assignment(
            session, contact_id=cid, user_id=uid, is_primary=True
        )
        c2 = Contact(first_name="B", email="b@example.com")
        session.add(c2)
        session.commit()
        c2_id = c2.id

    tree = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "assigned_users",
                "comparator": "is_empty",
            }
        ],
    }
    flt = build_filter(tree)
    with session_factory() as session:
        ids = list(session.scalars(select(Contact.id).where(flt)))
        assert ids == [c2_id]


# Keep `json` import busy — used by audit-row inspection in larger
# follow-ups (PR-C will assert metadata content).
_ = json
