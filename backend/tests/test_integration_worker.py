"""Tests for the integration worker entrypoint.

We don't spin up a real RQ worker — instead we mock the `Queue.enqueue`
call so the enqueue path is exercised end-to-end (sync_log row created,
audit event recorded, job_id stamped) and we invoke `run_sync_job`
directly to drive the lifecycle (PENDING → RUNNING → terminal status).
"""
from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import Action
from app.models.crm import AuditLog, Base, ExternalSystem, SyncLog, SyncStatus
from app.models.integration_settings import IntegrationAccount
from app.workers.jobs import (
    OPERATIONS,
    SyncOutcome,
    enqueue_sync_job,
    is_operation_registered,
    run_sync_job,
)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as session:
        session.add(
            IntegrationAccount(
                system=ExternalSystem.AGILECRM,
                account_id="es",
                display_name="AgileCRM España",
            )
        )
        session.commit()
    # Patch get_engine so `run_sync_job` (which opens its own Session)
    # reuses our in-memory database.
    with patch("app.workers.jobs.get_engine", return_value=engine):
        yield sf
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_operation_registry_starts_empty():
    """Sprint A ships the infrastructure only — no concrete operations
    are registered yet, so the API must surface this clearly."""
    assert not is_operation_registered("agilecrm", "sync_contacts")


# ---------------------------------------------------------------------------
# Enqueue path
# ---------------------------------------------------------------------------


def test_enqueue_creates_sync_log_and_audit(factory: sessionmaker):
    with factory() as session:
        with patch("app.workers.jobs.queue_for") as queue_for_mock:
            queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-xyz")
            sync_log_id, job_id = enqueue_sync_job(
                session,
                system="agilecrm",
                account_id="es",
                operation="sync_contacts",
            )
        assert job_id == "job-xyz"

        row = session.get(SyncLog, sync_log_id)
        assert row is not None
        assert row.status == SyncStatus.PENDING.value
        assert row.system == ExternalSystem.AGILECRM
        assert row.account_id == "es"
        assert row.operation == "sync_contacts"
        assert row.job_id == "job-xyz"

        actions = {a.action for a in session.query(AuditLog).all()}
        assert Action.INTEGRATION_SYNC_TRIGGERED in actions


# ---------------------------------------------------------------------------
# run_sync_job: no handler → FAILED with clear error_summary
# ---------------------------------------------------------------------------


def test_run_sync_job_marks_failed_when_no_handler(factory: sessionmaker):
    with factory() as session:
        with patch("app.workers.jobs.queue_for") as queue_for_mock:
            queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-1")
            sync_log_id, _ = enqueue_sync_job(
                session,
                system="agilecrm",
                account_id="es",
                operation="unknown_op",
            )

    result = run_sync_job(
        sync_log_id, system="agilecrm", account_id="es", operation="unknown_op"
    )
    assert result["status"] == SyncStatus.FAILED.value

    with factory() as check:
        row = check.get(SyncLog, sync_log_id)
        assert row is not None
        assert row.status == SyncStatus.FAILED.value
        assert row.error_summary
        assert "unknown_op" in row.error_summary
        actions = {a.action for a in check.query(AuditLog).all()}
        assert Action.INTEGRATION_SYNC_STARTED in actions
        assert Action.INTEGRATION_SYNC_FAILED in actions


# ---------------------------------------------------------------------------
# run_sync_job: registered handler with success outcome
# ---------------------------------------------------------------------------


def test_run_sync_job_calls_registered_handler(factory: sessionmaker):
    calls = {"count": 0}

    def fake_handler(session: Session, sync_log: SyncLog) -> SyncOutcome:
        calls["count"] += 1
        return SyncOutcome(records_processed=42, metadata={"hello": "world"})

    OPERATIONS["agilecrm:demo"] = fake_handler
    try:
        with factory() as session:
            with patch("app.workers.jobs.queue_for") as queue_for_mock:
                queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-2")
                sync_log_id, _ = enqueue_sync_job(
                    session,
                    system="agilecrm",
                    account_id="es",
                    operation="demo",
                )
        result = run_sync_job(
            sync_log_id, system="agilecrm", account_id="es", operation="demo"
        )
        assert result["status"] == SyncStatus.SUCCESS.value
        assert result["records_processed"] == 42
        assert calls["count"] == 1

        with factory() as check:
            row = check.get(SyncLog, sync_log_id)
            assert row is not None
            assert row.status == SyncStatus.SUCCESS.value
            assert row.records_processed == 42
            actions = {a.action for a in check.query(AuditLog).all()}
            assert Action.INTEGRATION_SYNC_SUCCEEDED in actions
    finally:
        OPERATIONS.pop("agilecrm:demo", None)


# ---------------------------------------------------------------------------
# run_sync_job: handler raises → captured + marked failed
# ---------------------------------------------------------------------------


def test_run_sync_job_captures_handler_exception(factory: sessionmaker):
    def explode(session: Session, sync_log: SyncLog) -> SyncOutcome:
        raise RuntimeError("kaboom")

    OPERATIONS["agilecrm:explodes"] = explode
    try:
        with factory() as session:
            with patch("app.workers.jobs.queue_for") as queue_for_mock:
                queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-3")
                sync_log_id, _ = enqueue_sync_job(
                    session,
                    system="agilecrm",
                    account_id="es",
                    operation="explodes",
                )
        run_sync_job(
            sync_log_id, system="agilecrm", account_id="es", operation="explodes"
        )
        with factory() as check:
            row = check.get(SyncLog, sync_log_id)
            assert row is not None
            assert row.status == SyncStatus.FAILED.value
            assert row.error_summary and "kaboom" in row.error_summary
    finally:
        OPERATIONS.pop("agilecrm:explodes", None)


# ---------------------------------------------------------------------------
# Partial success: records_failed > 0 but records_processed > 0
# ---------------------------------------------------------------------------


def test_run_sync_job_partial_success(factory: sessionmaker):
    def partial(session: Session, sync_log: SyncLog) -> SyncOutcome:
        return SyncOutcome(records_processed=10, records_failed=2)

    OPERATIONS["agilecrm:partial"] = partial
    try:
        with factory() as session:
            with patch("app.workers.jobs.queue_for") as queue_for_mock:
                queue_for_mock.return_value.enqueue.return_value = SimpleNamespace(id="job-4")
                sync_log_id, _ = enqueue_sync_job(
                    session,
                    system="agilecrm",
                    account_id="es",
                    operation="partial",
                )
        result = run_sync_job(
            sync_log_id, system="agilecrm", account_id="es", operation="partial"
        )
        assert result["status"] == SyncStatus.PARTIAL_SUCCESS.value
        with factory() as check:
            actions = {a.action for a in check.query(AuditLog).all()}
            assert Action.INTEGRATION_SYNC_PARTIAL in actions
    finally:
        OPERATIONS.pop("agilecrm:partial", None)
