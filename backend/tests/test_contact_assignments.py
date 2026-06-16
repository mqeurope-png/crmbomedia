"""Sprint Reglas-Assign — PR-A tests.

Schema + repository invariants for `contact_assignments`:
- one-primary-per-contact (set_primary clears siblings)
- owner_user_id cache stays in sync with the primary
- add/remove/find idempotency
- backfill script idempotent + mirrors owner → primary
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.crm import (
    AssignmentRule,
    Base,
    Contact,
    ContactAssignment,
    User,
    UserRole,
)
from app.repositories import assignments as repo
from tests._test_helpers import seed_test_users


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


def _seed_contact(factory: sessionmaker) -> str:
    with factory() as session:
        contact = Contact(
            first_name="Bart",
            email="bart@bomedia.net",
            tags="",
            commercial_status="new",
        )
        session.add(contact)
        session.commit()
        return contact.id


def _user_ids(factory: sessionmaker) -> dict[str, str]:
    with factory() as session:
        return {
            role.value: session.scalar(select(User.id).where(User.role == role))
            for role in UserRole
        }


# -- model ----------------------------------------------------------


def test_models_create() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)  # must not raise
    assert ContactAssignment.__tablename__ == "contact_assignments"
    assert AssignmentRule.__tablename__ == "assignment_rules"


# -- repository: add / primary / cache ------------------------------


def test_add_primary_sets_owner_cache(db: _Fixture) -> None:
    cid = _seed_contact(db.factory)
    users = _user_ids(db.factory)
    with db.factory() as session:
        repo.add_assignment(
            session, contact_id=cid, user_id=users["manager"], is_primary=True
        )
        session.commit()
    with db.factory() as session:
        contact = session.get(Contact, cid)
        assert contact.owner_user_id == users["manager"]


def test_secondary_does_not_touch_owner_cache(db: _Fixture) -> None:
    cid = _seed_contact(db.factory)
    users = _user_ids(db.factory)
    with db.factory() as session:
        repo.add_assignment(
            session, contact_id=cid, user_id=users["manager"], is_primary=True
        )
        repo.add_assignment(
            session, contact_id=cid, user_id=users["user"], is_primary=False
        )
        session.commit()
    with db.factory() as session:
        contact = session.get(Contact, cid)
        # Primary unchanged; the watcher didn't steal the cache.
        assert contact.owner_user_id == users["manager"]
        rows = repo.list_for_contact(session, cid)
        assert len(rows) == 2
        assert rows[0].is_primary is True  # ordered primary-first
        assert rows[1].is_primary is False


def test_set_primary_clears_previous(db: _Fixture) -> None:
    cid = _seed_contact(db.factory)
    users = _user_ids(db.factory)
    with db.factory() as session:
        repo.add_assignment(
            session, contact_id=cid, user_id=users["manager"], is_primary=True
        )
        watcher = repo.add_assignment(
            session, contact_id=cid, user_id=users["user"], is_primary=False
        )
        session.commit()
        watcher_id = watcher.id
    # Promote the watcher to primary.
    with db.factory() as session:
        repo.set_primary(session, contact_id=cid, assignment_id=watcher_id)
        session.commit()
    with db.factory() as session:
        rows = {r.user_id: r.is_primary for r in repo.list_for_contact(session, cid)}
        assert rows[users["user"]] is True
        assert rows[users["manager"]] is False
        # exactly one primary
        primaries = [r for r in repo.list_for_contact(session, cid) if r.is_primary]
        assert len(primaries) == 1
        assert session.get(Contact, cid).owner_user_id == users["user"]


def test_add_is_idempotent_on_contact_user(db: _Fixture) -> None:
    cid = _seed_contact(db.factory)
    users = _user_ids(db.factory)
    with db.factory() as session:
        repo.add_assignment(
            session, contact_id=cid, user_id=users["user"], is_primary=False
        )
        # Re-add same pair as primary → updates existing, no UNIQUE error.
        repo.add_assignment(
            session, contact_id=cid, user_id=users["user"], is_primary=True
        )
        session.commit()
    with db.factory() as session:
        rows = repo.list_for_contact(session, cid)
        assert len(rows) == 1
        assert rows[0].is_primary is True
        assert session.get(Contact, cid).owner_user_id == users["user"]


def test_remove_primary_clears_owner_cache(db: _Fixture) -> None:
    cid = _seed_contact(db.factory)
    users = _user_ids(db.factory)
    with db.factory() as session:
        a = repo.add_assignment(
            session, contact_id=cid, user_id=users["manager"], is_primary=True
        )
        session.commit()
        aid = a.id
    with db.factory() as session:
        assignment = repo.get_assignment(session, aid)
        repo.remove_assignment(session, assignment)
        session.commit()
    with db.factory() as session:
        assert session.get(Contact, cid).owner_user_id is None
        assert repo.list_for_contact(session, cid) == []


# -- backfill script ------------------------------------------------


def test_backfill_mirrors_owner_then_idempotent(db: _Fixture) -> None:
    from unittest.mock import patch  # noqa: PLC0415

    from scripts.backfill_assignments_from_owner import backfill  # noqa: PLC0415

    users = _user_ids(db.factory)
    # Two contacts with an owner, one without.
    with db.factory() as session:
        c1 = Contact(first_name="A", email="a@x.com", tags="", commercial_status="new")
        c1.owner_user_id = users["manager"]
        c2 = Contact(first_name="B", email="b@x.com", tags="", commercial_status="new")
        c2.owner_user_id = users["user"]
        c3 = Contact(first_name="C", email="c@x.com", tags="", commercial_status="new")
        session.add_all([c1, c2, c3])
        session.commit()
        c1_id, c2_id = c1.id, c2.id

    with patch(
        "scripts.backfill_assignments_from_owner.get_engine",
        return_value=db.engine,
    ):
        first = backfill(dry_run=False)
        second = backfill(dry_run=False)

    assert first["scanned"] == 2
    assert first["assignments_added"] == 2
    # Re-run: nothing new.
    assert second["assignments_added"] == 0
    assert second["skipped_existing"] == 2

    with db.factory() as session:
        rows = list(session.scalars(select(ContactAssignment)))
        assert len(rows) == 2
        by_contact = {r.contact_id: r for r in rows}
        assert by_contact[c1_id].user_id == users["manager"]
        assert by_contact[c1_id].is_primary is True
        assert by_contact[c1_id].source == "backfill"
        assert by_contact[c2_id].user_id == users["user"]
