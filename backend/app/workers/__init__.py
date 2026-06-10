"""Background worker package.

The Sprint A integration infrastructure runs jobs on an RQ (Redis Queue)
worker that shares its Docker image with the FastAPI service. The
public surface exposes:

- `enqueue_sync_job(system, account_id, operation, ...)` — creates a
  `sync_logs` row in `PENDING` status, enqueues an RQ job that wraps
  the operation, returns `(sync_log_id, rq_job_id)`.
- `run_sync_job(sync_log_id, system, account_id, operation, ...)` —
  the entrypoint that the worker actually runs. Updates the `sync_logs`
  row through the lifecycle and emits `integration.sync_*` audit rows.

Per-connector modules under `app.integrations.<system>` register their
operations into `OPERATIONS` at import time. Importing them from here
keeps the registration deterministic in both the API process and the
RQ worker (both go through this module on startup).
"""
# Connector registrations. Each side-effect import populates entries
# in `OPERATIONS`; failing to import (e.g. missing optional dependency)
# leaves the registry empty for that connector and the API surfaces a
# clear 409 when the operator tries to trigger an unregistered op.
from app.integrations import agilecrm as _agilecrm  # noqa: F401
from app.integrations import brevo as _brevo  # noqa: F401
from app.workers.jobs import OPERATIONS, enqueue_sync_job, run_sync_job
from app.workers.queues import (
    DEFAULT_JOB_TIMEOUT,
    DEFAULT_RESULT_TTL,
    queue_for,
    redis_connection,
)

__all__ = [
    "DEFAULT_JOB_TIMEOUT",
    "DEFAULT_RESULT_TTL",
    "OPERATIONS",
    "enqueue_sync_job",
    "queue_for",
    "redis_connection",
    "run_sync_job",
]
