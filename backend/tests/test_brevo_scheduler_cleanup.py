"""Periodic scheduler + cleanup script for the Brevo connector."""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.brevo.scheduler import (
    _interval_hours,
    periodic_read_check,
    periodic_segments_check,
)
from app.models.crm import (
    ExternalSystem,
    SyncLog,
    SyncStatus,
)
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
)
from scripts.cleanup_stale_sync_logs import cleanup


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(engine)


def test_interval_hours_reads_env(monkeypatch):
    monkeypatch.setenv("BREVO_SYNC_INTERVAL_HOURS", "24")
    assert _interval_hours("BREVO_SYNC_INTERVAL_HOURS", 12) == 24


def test_interval_hours_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("BREVO_SYNC_INTERVAL_HOURS", "not-a-number")
    assert _interval_hours("BREVO_SYNC_INTERVAL_HOURS", 12) == 12


def test_interval_hours_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("BREVO_SYNC_INTERVAL_HOURS", "0")
    assert _interval_hours("BREVO_SYNC_INTERVAL_HOURS", 12) == 12


def test_periodic_read_enqueues_one_job_per_live_account(session_factory):
    with session_factory() as session:
        session.add_all(
            [
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="live-1",
                    display_name="Live 1",
                    enabled=True,
                    mode=IntegrationMode.LIVE,
                ),
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="live-2",
                    display_name="Live 2",
                    enabled=True,
                    mode=IntegrationMode.LIVE,
                ),
                # Sandbox + disabled accounts are NOT in scope for
                # the heartbeat — operators run those manually.
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="sandbox",
                    display_name="Sandbox",
                    enabled=True,
                    mode=IntegrationMode.SANDBOX,
                ),
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="paused",
                    display_name="Paused",
                    enabled=False,
                    mode=IntegrationMode.LIVE,
                ),
            ]
        )
        session.commit()
        with (
            patch(
                "app.integrations.brevo.scheduler.enqueue_sync_job"
            ) as fake_enqueue,
            patch(
                "app.integrations.brevo.scheduler.schedule_periodic_read"
            ),
        ):
            fake_enqueue.return_value = ("log", "job")
            outcome = periodic_read_check(
                session,
                SyncLog(
                    system="brevo",
                    operation="periodic_read",
                    status="running",
                ),
            )
        assert outcome.records_processed == 2
        account_ids = {
            call.kwargs["account_id"] for call in fake_enqueue.call_args_list
        }
        assert account_ids == {"live-1", "live-2"}


def test_periodic_segments_includes_sandbox_too(session_factory):
    """Segments refresh runs against every enabled account regardless
    of mode — there's no harm in mirroring segments off sandbox."""
    with session_factory() as session:
        session.add_all(
            [
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="live",
                    display_name="Live",
                    enabled=True,
                    mode=IntegrationMode.LIVE,
                ),
                IntegrationAccount(
                    system=ExternalSystem.BREVO,
                    account_id="sandbox",
                    display_name="Sandbox",
                    enabled=True,
                    mode=IntegrationMode.SANDBOX,
                ),
            ]
        )
        session.commit()
        with (
            patch(
                "app.integrations.brevo.scheduler.enqueue_sync_job"
            ) as fake,
            patch(
                "app.integrations.brevo.scheduler.schedule_periodic_segments"
            ),
        ):
            fake.return_value = ("log", "job")
            periodic_segments_check(
                session,
                SyncLog(
                    system="brevo",
                    operation="periodic_segments",
                    status="running",
                ),
            )
        assert fake.call_count == 2


def test_cleanup_flips_only_stale_pending_logs(session_factory):
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add_all(
            [
                SyncLog(
                    system="brevo",
                    operation="sync_contacts",
                    status=SyncStatus.PENDING.value,
                    created_at=now - timedelta(hours=3),
                ),
                SyncLog(
                    system="brevo",
                    operation="sync_contacts",
                    status=SyncStatus.PENDING.value,
                    created_at=now - timedelta(minutes=30),  # recent
                ),
                SyncLog(
                    system="brevo",
                    operation="sync_contacts",
                    status=SyncStatus.SUCCESS.value,
                    created_at=now - timedelta(hours=5),  # not pending
                ),
            ]
        )
        session.commit()
        touched = cleanup(session)
        assert touched == 1
        rows = list(session.scalars(select(SyncLog)))
        statuses = {row.status for row in rows}
        assert SyncStatus.FAILED.value in statuses
        # Idempotent: second run does nothing.
        assert cleanup(session) == 0


def test_arm_periodic_jobs_swallows_redis_failure():
    """API startup must not crash if Redis isn't reachable at boot —
    the next click on Sincronizar ahora still triggers one-shot
    enqueues, and the next API restart re-arms."""
    from app.integrations.brevo.scheduler import arm_periodic_jobs

    with patch(
        "app.integrations.brevo.scheduler.redis_connection",
        side_effect=ConnectionError("redis down"),
    ):
        # Should NOT raise — every schedule_* swallows its own redis
        # error via _arm's try/except.
        try:
            arm_periodic_jobs()
        except ConnectionError:
            pytest.fail("arm_periodic_jobs leaked a redis error")


def test_arm_periodic_jobs_uses_setnx_guard():
    """Two concurrent API processes can't double-arm: the second
    SETNX returns False and the second `enqueue_in` never runs."""
    from app.integrations.brevo.scheduler import schedule_periodic_read

    redis = MagicMock()
    redis.set.return_value = False  # somebody else holds the lock
    with patch(
        "app.integrations.brevo.scheduler.redis_connection",
        return_value=redis,
    ):
        schedule_periodic_read()
    # The Queue/enqueue_in path must NOT be hit.
    assert redis.delete.call_count == 0
