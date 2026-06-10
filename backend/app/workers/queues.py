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

#: Operations whose jobs legitimately outlive the 10-minute default.
#: Without an override RQ kills the job with JobTimeoutException
#: mid-run — exactly what happened when the historical backfill was
#: first triggered from the UI. The backfill drives one Brevo export
#: per campaign (~40-60 min for a typical account), so 2 h leaves
#: comfortable headroom.
LONG_JOB_TIMEOUTS: dict[str, int] = {
    "brevo:historical_backfill": 7_200,
}


def redis_connection(url: str | None = None) -> Redis:
    """Return a Redis client. Honours `REDIS_URL` if not given explicitly."""
    resolved = url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return Redis.from_url(resolved)


def queue_name(system: str, operation: str) -> str:
    """Build the RQ queue name for `(system, operation)`.

    Production lesson: an orphan queue `rq:queue:brevo:brevo:sync_contacts`
    showed up in Redis after the Sprint B+D deploy carrying one
    stranded job. The cause was a caller that pre-prefixed `operation`
    with `brevo:` before passing it through here, so the resulting
    name doubled the system. We can't change every caller and survive
    a future copy-paste, so the guard belongs at the construction
    site: strip a `<system>:` prefix when the operation already
    carries one.
    """
    if operation.startswith(f"{system}:"):
        operation = operation[len(system) + 1 :]
    return f"{system}:{operation}"


def queue_for(
    system: str, operation: str, *, connection: Redis | None = None
) -> Queue:
    """Build the RQ Queue for the `(system, operation)` pair."""
    name = queue_name(system, operation)
    return Queue(
        name,
        connection=connection or redis_connection(),
        default_timeout=LONG_JOB_TIMEOUTS.get(name, DEFAULT_JOB_TIMEOUT),
    )


def all_queue_names(
    systems: Iterable[str], operations: Iterable[str]
) -> list[str]:
    """Cartesian product of `(system, operation)` names. The worker
    command picks the subset it should listen on; useful for static
    docker-compose entries."""
    return [queue_name(s, op) for s in systems for op in operations]
