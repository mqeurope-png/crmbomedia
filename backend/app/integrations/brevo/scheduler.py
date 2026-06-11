"""Periodic scheduling for the Brevo connector.

Three self-rescheduling heartbeats, all built on RQ's
`enqueue_in` + a Redis SETNX guard so two API processes can't double-
arm the same heartbeat:

- `brevo:periodic_read` — enqueue `sync_contacts` for every enabled
  live Brevo account every `BREVO_SYNC_INTERVAL_HOURS` (default 12).
- `brevo:periodic_segments` — enqueue `refresh_segments` every
  `BREVO_SEGMENTS_REFRESH_INTERVAL_HOURS` (default 6).
- (Campaign refresh stays in `campaigns.py` — already on 15-min
  interval.)

All three call `schedule_*` after each handler so the next tick is
armed even when the API process restarts. `arm_periodic_jobs()` is
called from `app.main` startup so a fresh deployment doesn't need
manual intervention.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import ExternalSystem, SyncLog
from app.models.integration_settings import IntegrationAccount
from app.workers.jobs import OPERATIONS, SyncOutcome, enqueue_sync_job
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

DEFAULT_READ_INTERVAL_HOURS = 12
DEFAULT_SEGMENTS_INTERVAL_HOURS = 6

READ_LOCK_KEY = "brevo:periodic_read:heartbeat"
SEGMENTS_LOCK_KEY = "brevo:periodic_segments:heartbeat"


def _interval_hours(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _enabled_brevo_accounts(
    session: Session, *, live_only: bool = False
) -> list[IntegrationAccount]:
    from app.models.integration_settings import IntegrationMode  # noqa: PLC0415

    statement = select(IntegrationAccount).where(
        IntegrationAccount.system == ExternalSystem.BREVO,
        IntegrationAccount.enabled.is_(True),
    )
    if live_only:
        statement = statement.where(
            IntegrationAccount.mode == IntegrationMode.LIVE
        )
    return list(session.scalars(statement))


# ---------------------------------------------------------------------------
# read sync heartbeat
# ---------------------------------------------------------------------------


def periodic_read_check(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Heartbeat handler: enqueue `sync_contacts` for every live Brevo
    account, then re-arm. Stats land on the heartbeat's own sync_log."""
    _ = sync_log
    accounts = _enabled_brevo_accounts(session, live_only=True)
    enqueued = 0
    for account in accounts:
        try:
            enqueue_sync_job(
                session,
                system="brevo",
                account_id=account.account_id,
                operation="sync_contacts",
                triggered_by="cron",
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 - keep siblings alive
            logger.warning(
                "brevo.periodic_read enqueue failed account=%s: %s",
                account.account_id,
                exc,
            )
    schedule_periodic_read()
    return SyncOutcome(
        records_processed=enqueued,
        metadata={"checked": len(accounts)},
    )


def schedule_periodic_read() -> None:
    hours = _interval_hours("BREVO_SYNC_INTERVAL_HOURS", DEFAULT_READ_INTERVAL_HOURS)
    _arm(
        lock=READ_LOCK_KEY,
        queue=queue_name("brevo", "periodic_read"),
        job=_periodic_read_runner,
        interval=timedelta(hours=hours),
    )


def _periodic_read_runner() -> None:
    _run_heartbeat(periodic_read_check, operation="periodic_read")


# ---------------------------------------------------------------------------
# segments refresh heartbeat
# ---------------------------------------------------------------------------


def periodic_segments_check(session: Session, sync_log: SyncLog) -> SyncOutcome:
    _ = sync_log
    accounts = _enabled_brevo_accounts(session)
    enqueued = 0
    for account in accounts:
        try:
            enqueue_sync_job(
                session,
                system="brevo",
                account_id=account.account_id,
                operation="refresh_segments",
                triggered_by="cron",
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "brevo.periodic_segments enqueue failed account=%s: %s",
                account.account_id,
                exc,
            )
    schedule_periodic_segments()
    return SyncOutcome(
        records_processed=enqueued,
        metadata={"checked": len(accounts)},
    )


def schedule_periodic_segments() -> None:
    hours = _interval_hours(
        "BREVO_SEGMENTS_REFRESH_INTERVAL_HOURS",
        DEFAULT_SEGMENTS_INTERVAL_HOURS,
    )
    _arm(
        lock=SEGMENTS_LOCK_KEY,
        queue=queue_name("brevo", "periodic_segments"),
        job=_periodic_segments_runner,
        interval=timedelta(hours=hours),
    )


def _periodic_segments_runner() -> None:
    _run_heartbeat(periodic_segments_check, operation="periodic_segments")


# ---------------------------------------------------------------------------
# heartbeat plumbing
# ---------------------------------------------------------------------------


def _arm(
    *,
    lock: str,
    queue: str,
    job: Callable[[], None],
    interval: timedelta,
) -> None:
    # The whole path (connect → SETNX → enqueue_in) sits behind one
    # broad try: a Redis outage at API boot must NOT take the API
    # down. The next click on "Sincronizar ahora" still triggers a
    # one-shot enqueue, and the next API restart re-arms the
    # heartbeat.
    try:
        conn = redis_connection()
        # TTL slightly shorter than the interval so a restart that
        # lost the SETNX guard still re-arms within one tick.
        if not conn.set(lock, "1", nx=True, ex=int(interval.total_seconds()) - 30):
            return
        try:
            from rq import Queue  # noqa: PLC0415

            Queue(queue, connection=conn).enqueue_in(interval, job)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "brevo.heartbeat scheduling failed for %s: %s", queue, exc
            )
            conn.delete(lock)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "brevo.heartbeat redis unreachable for %s: %s", queue, exc
        )


def _run_heartbeat(
    handler: Callable[[Session, SyncLog], SyncOutcome], *, operation: str
) -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        fake_log = SyncLog(system="brevo", operation=operation, status="running")
        handler(session, fake_log)


def arm_periodic_jobs() -> None:
    """Call once at API startup. The SETNX guards make this safe under
    multiple workers / multiple API processes. Each scheduler call is
    wrapped so one outage doesn't skip the rest of the heartbeats."""
    from app.integrations.brevo.campaigns import (  # noqa: PLC0415
        schedule_campaign_refresh,
    )
    from app.integrations.brevo.sync_targets import (  # noqa: PLC0415
        schedule_heartbeat,
    )

    for label, fn in (
        ("periodic_read", schedule_periodic_read),
        ("periodic_segments", schedule_periodic_segments),
        ("sync_targets", schedule_heartbeat),
        ("campaign_refresh", schedule_campaign_refresh),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("brevo.scheduler %s arm failed: %s", label, exc)


OPERATIONS["brevo:periodic_read"] = periodic_read_check
OPERATIONS["brevo:periodic_segments"] = periodic_segments_check
