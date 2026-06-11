"""Migration 0026 backfill — derive contact external dates from the
existing per-system reference timestamps.

The `_backfill_external_dates` helper is plain portable SQL, so we run
it against the SQLite test DB after seeding contacts + external
references through the ORM and assert the aggregates land (oldest
creation, newest modification) and that a contact without dated
references stays NULL.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.crm import Contact, ExternalReference, ExternalSystem


def _load_migration():
    path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "20260611_0026_contacts_external_dates.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0026", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


MIGRATION = _load_migration()


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


def _contact(session, email: str) -> Contact:
    c = Contact(first_name=email.split("@")[0].title(), email=email)
    session.add(c)
    session.flush()
    return c


def _ref(session, contact_id, system, account_id, *, created, updated):
    session.add(
        ExternalReference(
            system=system,
            account_id=account_id,
            external_id=f"{account_id}-{contact_id[:4]}",
            contact_id=contact_id,
            external_created_at=created,
            external_updated_at=updated,
        )
    )


def test_backfill_takes_oldest_creation_newest_update(session_factory):
    with session_factory() as session:
        # A contact in two systems: AgileCRM (older) + Brevo (newer).
        multi = _contact(session, "multi@example.com")
        _ref(
            session, multi.id, ExternalSystem.AGILECRM, "agile-es",
            created=datetime(2025, 3, 1, 9, 0, tzinfo=UTC),
            updated=datetime(2025, 11, 1, 9, 0, tzinfo=UTC),
        )
        _ref(
            session, multi.id, ExternalSystem.BREVO, "default",
            created=datetime(2025, 9, 25, 9, 37, tzinfo=UTC),
            updated=datetime(2025, 12, 3, 11, 2, tzinfo=UTC),
        )
        # A contact with one ref only.
        single = _contact(session, "single@example.com")
        _ref(
            session, single.id, ExternalSystem.BREVO, "default",
            created=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            updated=datetime(2024, 6, 1, 0, 0, tzinfo=UTC),
        )
        # A contact with no dated reference at all.
        bare = _contact(session, "bare@example.com")
        _ref(
            session, bare.id, ExternalSystem.BREVO, "default",
            created=None, updated=None,
        )
        session.commit()

        MIGRATION._backfill_external_dates(session.connection())
        session.commit()

        session.expire_all()
        multi = session.get(Contact, multi.id)
        single = session.get(Contact, single.id)
        bare = session.get(Contact, bare.id)

    def _utc(dt):
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    # Oldest creation = AgileCRM's March; newest update = Brevo's December.
    assert _utc(multi.created_at_external) == datetime(2025, 3, 1, 9, 0, tzinfo=UTC)
    assert _utc(multi.updated_at_external) == datetime(2025, 12, 3, 11, 2, tzinfo=UTC)
    assert _utc(single.created_at_external) == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    assert bare.created_at_external is None
    assert bare.updated_at_external is None


def test_backfill_is_idempotent(session_factory):
    with session_factory() as session:
        c = _contact(session, "x@example.com")
        _ref(
            session, c.id, ExternalSystem.BREVO, "default",
            created=datetime(2025, 2, 2, 2, 2, tzinfo=UTC),
            updated=datetime(2025, 5, 5, 5, 5, tzinfo=UTC),
        )
        session.commit()

        MIGRATION._backfill_external_dates(session.connection())
        session.commit()
        MIGRATION._backfill_external_dates(session.connection())
        session.commit()

        session.expire_all()
        c = session.get(Contact, c.id)

    def _utc(dt):
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

    assert _utc(c.created_at_external) == datetime(2025, 2, 2, 2, 2, tzinfo=UTC)
    assert _utc(c.updated_at_external) == datetime(2025, 5, 5, 5, 5, tzinfo=UTC)
