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

The current PR (Sprint A) is infrastructure only — no connector
implementations are registered yet. Each per-system PR adds a handler
to the `OPERATIONS` registry below.
"""
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
