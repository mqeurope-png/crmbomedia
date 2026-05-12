"""Job lifecycle wrapper for the integration worker.

The pattern: every operation that runs on the worker is identified by a
`(system, operation)` string pair. To register a new operation, import
`OPERATIONS` and append to it from the per-connector PR:
`OPERATIONS["agilecrm:sync_contacts"] = handler`.

The handler signature is:

    def handler(
        session: sqlalchemy.orm.Session,
        sync_log: SyncLog,
        *,
        payload: dict[str, Any] | None,
    ) -> SyncOutcome: ...

`enqueue_sync_job` creates the `sync_logs` row up-front (status
`PENDING`) so the operator immediately sees the job in the UI; the
worker picks the row up, flips it to `RUNNING`, calls the handler and
records the outcome. Each lifecycle transition emits an `integration.*`
audit event.
"""
from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.db.session import get_engine
from app.models.crm import ExternalSystem, SyncLog, SyncStatus, SyncTrigger
from app.workers.queues import queue_for, redis_connection

logger = logging.getLogger(__name__)


@dataclass
class SyncOutcome:
    """Return type for a connector handler. The worker uses it to
    finalise the `sync_logs` row."""

    records_processed: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    error_summary: str | None = None
    metadata: dict[str, Any] | None = None

    def status(self) -> SyncStatus:
        if self.records_failed and not self.records_processed:
            return SyncStatus.FAILED
        if self.records_failed:
            return SyncStatus.PARTIAL_SUCCESS
        return SyncStatus.SUCCESS


SyncHandler = Callable[[Session, SyncLog], SyncOutcome]

#: Registry of `(system:operation) -> handler` populated by each
#: connector PR. Empty in Sprint A; the API still accepts triggering an
#: operation without a handler — the job runs, marks itself as
#: `FAILED` with a clear error_summary so the operator sees the gap.
OPERATIONS: dict[str, SyncHandler] = {}


def _key(system: str, operation: str) -> str:
    return f"{system}:{operation}"


def enqueue_sync_job(
    session: Session,
    *,
    system: str | ExternalSystem,
    account_id: str,
    operation: str,
    triggered_by: SyncTrigger | str = SyncTrigger.MANUAL,
    triggered_by_user_id: str | None = None,
    payload: dict[str, Any] | None = None,
    request: Any | None = None,
) -> tuple[str, str]:
    """Create a `sync_logs` row, enqueue the RQ job, return `(sync_log_id, rq_job_id)`."""
    system_value = system.value if isinstance(system, ExternalSystem) else system
    trigger_value = (
        triggered_by.value if isinstance(triggered_by, SyncTrigger) else triggered_by
    )

    sync_log = SyncLog(
        system=ExternalSystem(system_value),
        account_id=account_id,
        operation=operation,
        status=SyncStatus.PENDING.value,
        triggered_by=trigger_value,
        triggered_by_user_id=triggered_by_user_id,
        metadata_json=json.dumps(payload) if payload else None,
    )
    session.add(sync_log)
    session.flush()

    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="sync_log",
        target_id=sync_log.id,
        actor_email=None,
        metadata={
            "system": system_value,
            "account_id": account_id,
            "operation": operation,
            "triggered_by": trigger_value,
        },
        request=request,
    )
    session.commit()

    queue = queue_for(system_value, operation)
    job = queue.enqueue(
        run_sync_job,
        sync_log_id=sync_log.id,
        system=system_value,
        account_id=account_id,
        operation=operation,
        payload=payload,
        retry=None,
    )

    # Persist the RQ job id so the UI can poll it.
    sync_log.job_id = job.id
    session.commit()
    return sync_log.id, job.id


def run_sync_job(
    sync_log_id: str,
    system: str,
    account_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entry point invoked by the RQ worker. Opens its own SQLAlchemy
    session, runs the registered handler if any, finalises the sync_log
    row and returns a small dict so the RQ result store has something
    useful.

    NOTE: the worker process must be able to import this module — that
    is why the function is defined at module top level (RQ pickles by
    reference)."""
    session = Session(get_engine())
    try:
        sync_log = session.get(SyncLog, sync_log_id)
        if sync_log is None:
            logger.warning("sync_log_id=%s missing; nothing to do", sync_log_id)
            return {"status": "missing"}

        sync_log.status = SyncStatus.RUNNING.value
        sync_log.started_at = datetime.now(UTC)
        session.flush()
        record_event(
            session,
            action=Action.INTEGRATION_SYNC_STARTED,
            target_type="sync_log",
            target_id=sync_log.id,
            metadata={
                "system": system,
                "account_id": account_id,
                "operation": operation,
            },
        )
        session.commit()

        handler = OPERATIONS.get(_key(system, operation))
        try:
            if handler is None:
                outcome = SyncOutcome(
                    records_failed=0,
                    error_summary=(
                        f"No handler registered for {system}:{operation}. "
                        "This operation is declared in the API but the connector "
                        "implementation has not landed yet."
                    ),
                )
                final_status = SyncStatus.FAILED
            else:
                outcome = handler(session, sync_log)
                if payload:
                    outcome.metadata = (outcome.metadata or {}) | {"payload": payload}
                final_status = outcome.status()
        except Exception as exc:  # noqa: BLE001 - we capture *anything* the handler raises
            tb = traceback.format_exc(limit=10)
            logger.exception(
                "sync_log_id=%s failed for %s:%s", sync_log_id, system, operation
            )
            outcome = SyncOutcome(error_summary=f"{exc!s}\n{tb}")
            final_status = SyncStatus.FAILED

        sync_log.records_processed = outcome.records_processed
        sync_log.records_skipped = outcome.records_skipped
        sync_log.records_failed = outcome.records_failed
        sync_log.error_summary = outcome.error_summary
        if outcome.metadata is not None:
            sync_log.metadata_json = json.dumps(outcome.metadata, default=str)
        sync_log.status = final_status.value
        sync_log.finished_at = datetime.now(UTC)
        session.flush()

        action = {
            SyncStatus.SUCCESS: Action.INTEGRATION_SYNC_SUCCEEDED,
            SyncStatus.PARTIAL_SUCCESS: Action.INTEGRATION_SYNC_PARTIAL,
            SyncStatus.FAILED: Action.INTEGRATION_SYNC_FAILED,
        }.get(final_status, Action.INTEGRATION_SYNC_FAILED)
        record_event(
            session,
            action=action,
            target_type="sync_log",
            target_id=sync_log.id,
            metadata={
                "system": system,
                "account_id": account_id,
                "operation": operation,
                "records_processed": outcome.records_processed,
                "records_skipped": outcome.records_skipped,
                "records_failed": outcome.records_failed,
                "status": final_status.value,
            },
        )
        session.commit()

        return {
            "sync_log_id": sync_log.id,
            "status": final_status.value,
            "records_processed": outcome.records_processed,
            "records_skipped": outcome.records_skipped,
            "records_failed": outcome.records_failed,
        }
    finally:
        session.close()


def is_operation_registered(system: str, operation: str) -> bool:
    return _key(system, operation) in OPERATIONS


# Re-export for callers that just want the Redis connection without
# building a Queue (e.g. health checks).
__all__ = [
    "OPERATIONS",
    "SyncHandler",
    "SyncOutcome",
    "enqueue_sync_job",
    "is_operation_registered",
    "redis_connection",
    "run_sync_job",
]
