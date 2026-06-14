"""Sprint Email v2.4c — snooze sweep tests.

The RQ scheduling plumbing (`schedule_snooze_sweep`) sits behind
SETNX + Redis; we exercise it by mocking the Redis connection. The
core `unsnooze_due` helper is tested against a live SQLite session
because that's where the production bug would live (an off-by-one
in the WHERE clause, missing tz handling, etc.).
"""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.email_snooze import (
    arm_snooze_sweep,
    schedule_snooze_sweep,
    unsnooze_due,
)
from app.models.crm import Base, EmailThread, User, UserRole
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


def _seed_thread(
    session: Session,
    *,
    owner_id: str,
    snooze_until: datetime | None,
) -> str:
    now = datetime.now(UTC)
    thread = EmailThread(
        initiated_by_user_id=owner_id,
        gmail_account_user_id=owner_id,
        gmail_thread_id=f"gthr-{snooze_until}-{owner_id[:6]}",
        subject="x",
        first_message_at=now,
        last_message_at=now,
        message_count=1,
        snooze_until=snooze_until,
    )
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return thread.id


def test_unsnooze_due_clears_past_and_keeps_future(db: _Fixture) -> None:
    now = datetime.now(UTC)
    with db.factory() as session:
        uid = session.scalar(
            select(User.id).where(User.role == UserRole.USER)
        )
        past_id = _seed_thread(
            session, owner_id=uid, snooze_until=now - timedelta(hours=1)
        )
        exact_id = _seed_thread(session, owner_id=uid, snooze_until=now)
        future_id = _seed_thread(
            session, owner_id=uid, snooze_until=now + timedelta(hours=4)
        )
        none_id = _seed_thread(session, owner_id=uid, snooze_until=None)

    with patch("app.email_snooze.get_engine", return_value=db.engine):
        affected = unsnooze_due(now=now)
    assert affected == 2  # past + exact

    with db.factory() as session:
        rows = {
            t.id: t.snooze_until
            for t in session.scalars(
                select(EmailThread).where(
                    EmailThread.id.in_([past_id, exact_id, future_id, none_id])
                )
            )
        }
    assert rows[past_id] is None
    assert rows[exact_id] is None
    assert rows[future_id] is not None
    assert rows[none_id] is None


def test_unsnooze_due_no_rows_returns_zero(db: _Fixture) -> None:
    """When nothing is due the sweep should noop and report 0 so
    the heartbeat handler doesn't log a misleading non-zero count."""
    with patch("app.email_snooze.get_engine", return_value=db.engine):
        assert unsnooze_due() == 0


def test_schedule_snooze_sweep_uses_setnx_lock() -> None:
    """The arm helper must take the SETNX lock before enqueuing so
    a second API process doesn't double-arm the heartbeat."""
    fake_conn = MagicMock()
    # First call acquires the lock, second is blocked by it.
    fake_conn.set.side_effect = [True, False]
    with (
        patch("app.email_snooze.redis_connection", return_value=fake_conn),
        patch("rq.Queue") as Queue,
    ):
        schedule_snooze_sweep()
        schedule_snooze_sweep()

    # SETNX was attempted twice…
    assert fake_conn.set.call_count == 2
    # …but enqueue_in only fired on the winning attempt.
    assert Queue.return_value.enqueue_in.call_count == 1


def test_schedule_snooze_sweep_drops_lock_on_enqueue_failure() -> None:
    """If the enqueue raises after the lock is held, the lock has
    to be released so the next arm attempt isn't permanently
    blocked by a stale heartbeat key."""
    fake_conn = MagicMock()
    fake_conn.set.return_value = True
    with (
        patch("app.email_snooze.redis_connection", return_value=fake_conn),
        patch("rq.Queue") as Queue,
    ):
        Queue.return_value.enqueue_in.side_effect = RuntimeError("boom")
        schedule_snooze_sweep()

    fake_conn.delete.assert_called_once()


def test_arm_snooze_sweep_swallows_redis_outage(caplog) -> None:
    """A Redis outage at boot must not take the API down. We log
    a warning instead."""
    with patch(
        "app.email_snooze.schedule_snooze_sweep",
        side_effect=RuntimeError("redis down"),
    ):
        arm_snooze_sweep()
    assert any(
        "snooze_sweep arm failed" in record.message
        for record in caplog.records
    )
