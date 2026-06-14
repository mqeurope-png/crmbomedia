"""Snooze worker — periodic sweep that un-pauses due threads.

Sprint Email v2.4c. The `/api/emails/threads` endpoint hides any
thread whose `snooze_until` lies in the future. Without a sweep
the row would only flip back to visible the next time the column
is read against the current clock — perfectly fine when the
operator is staring at the page, but the dashboard widget +
unread-count badges sit on cached views and need an explicit
unsnooze.

The job:
1. Sweeps `email_threads` where `snooze_until IS NOT NULL AND
   snooze_until <= now`.
2. Sets `snooze_until = NULL` so the standard list query starts
   surfacing the rows again.
3. Re-arms itself on `EMAIL_SNOOZE_SWEEP_MINUTES` (default 5 min).

The arming dance follows the existing Brevo scheduler pattern:
SETNX lock guards against multiple API processes double-booking
the heartbeat; a Redis outage at API boot logs a warning but
never takes the API down.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models.crm import EmailThread
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_MINUTES = 5
SNOOZE_LOCK_KEY = "email:snooze_sweep:heartbeat"
SNOOZE_QUEUE = queue_name("emails", "snooze_sweep")


def _interval_minutes() -> int:
    raw = os.environ.get("EMAIL_SNOOZE_SWEEP_MINUTES")
    if not raw:
        return DEFAULT_SWEEP_MINUTES
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_SWEEP_MINUTES
    except ValueError:
        return DEFAULT_SWEEP_MINUTES


def unsnooze_due(*, now: datetime | None = None) -> int:
    """Clear `snooze_until` on every thread whose snooze deadline
    has passed. Returns the count for the caller / logging. The
    `now` argument is the seam tests exercise — production never
    passes it."""
    cutoff = now or datetime.now(UTC)
    with Session(get_engine()) as session:
        result = session.execute(
            update(EmailThread)
            .where(
                EmailThread.snooze_until.is_not(None),
                EmailThread.snooze_until <= cutoff,
            )
            .values(snooze_until=None)
        )
        session.commit()
        affected = result.rowcount or 0
    if affected:
        logger.info("email.snooze_sweep cleared=%d", affected)
    return affected


def _sweep_and_rearm() -> None:
    """The RQ-friendly entry point. Runs the sweep, then arms the
    next tick so the heartbeat self-perpetuates."""
    try:
        unsnooze_due()
    finally:
        schedule_snooze_sweep()


def schedule_snooze_sweep() -> None:
    """Arm a one-shot `_sweep_and_rearm` enqueue N minutes out.
    The SETNX guard makes this safe to call from every API process
    that boots — only the first one wins."""
    interval = timedelta(minutes=_interval_minutes())
    try:
        conn = redis_connection()
        # TTL shorter than the interval so a process that lost the
        # SETNX still re-arms within one tick of the next sweep.
        ttl = max(int(interval.total_seconds()) - 30, 5)
        if not conn.set(SNOOZE_LOCK_KEY, "1", nx=True, ex=ttl):
            return
        try:
            from rq import Queue  # noqa: PLC0415

            Queue(SNOOZE_QUEUE, connection=conn).enqueue_in(
                interval, _sweep_and_rearm
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("email.snooze_sweep scheduling failed: %s", exc)
            conn.delete(SNOOZE_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("email.snooze_sweep redis unreachable: %s", exc)


def arm_snooze_sweep() -> None:
    """Public hook called from `app.main` startup. The try/except
    keeps a Redis outage at boot from blocking the API."""
    try:
        schedule_snooze_sweep()
    except Exception:  # noqa: BLE001
        logger.warning("email.snooze_sweep arm failed at startup", exc_info=True)
