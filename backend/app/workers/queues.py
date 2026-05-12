"""RQ queue helpers.

Queue names follow `{system}:{operation}` (e.g. `agilecrm:sync_contacts`,
`brevo:push_contact`). Naming this way keeps Redis output legible
(`rq info`) and lets operators throttle/pause one system independently.

The connection helper accepts an explicit `REDIS_URL` so tests can swap
to `fakeredis` without touching the real settings.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

from redis import Redis
from rq import Queue

DEFAULT_JOB_TIMEOUT = 600  # 10 minutes; long enough for full-contact syncs.
DEFAULT_RESULT_TTL = 86_400  # keep the result around for one day for the UI.


def redis_connection(url: str | None = None) -> Redis:
    """Return a Redis client. Honours `REDIS_URL` if not given explicitly."""
    resolved = url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return Redis.from_url(resolved)


def queue_name(system: str, operation: str) -> str:
    return f"{system}:{operation}"


def queue_for(
    system: str, operation: str, *, connection: Redis | None = None
) -> Queue:
    """Build the RQ Queue for the `(system, operation)` pair."""
    return Queue(
        queue_name(system, operation),
        connection=connection or redis_connection(),
        default_timeout=DEFAULT_JOB_TIMEOUT,
    )


def all_queue_names(
    systems: Iterable[str], operations: Iterable[str]
) -> list[str]:
    """Cartesian product of `(system, operation)` names. The worker
    command picks the subset it should listen on; useful for static
    docker-compose entries."""
    return [queue_name(s, op) for s in systems for op in operations]
