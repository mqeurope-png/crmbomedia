"""Periodic scheduling for the AgileCRM connector.

Sprint Reglas-Assign — PR-Db. Espejo del scheduler Brevo
(`app/integrations/brevo/scheduler.py`). Un solo heartbeat:

- `agilecrm:periodic_read` — encola `sync_contacts` para cada cuenta
  AgileCRM habilitada cada `AGILECRM_SYNC_INTERVAL_HOURS` (default 12).

El scheduler se auto-reschedule en cada tick. `arm_periodic_jobs()`
se llama desde `app.main` startup y es idempotente vía SETNX en Redis
para que múltiples API processes no se pisen. Cuentas `enabled=False`
quedan automáticamente saltadas porque `_load_account` levanta
`IntegrationSkipped` (PR-Da hotfix), así que un eventual disable no
genera ruido en el log de sync.

Webhooks real-time queda como DEUDA #5 — sprint propio.
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

READ_LOCK_KEY = "agilecrm:periodic_read:heartbeat"


def _interval_hours(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _enabled_agile_accounts(session: Session) -> list[IntegrationAccount]:
    return list(
        session.scalars(
            select(IntegrationAccount).where(
                IntegrationAccount.system == ExternalSystem.AGILECRM,
                IntegrationAccount.enabled.is_(True),
            )
        )
    )


def periodic_read_check(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Heartbeat: encola `sync_contacts` para cada cuenta Agile
    habilitada, luego re-arma el próximo tick."""
    _ = sync_log
    accounts = _enabled_agile_accounts(session)
    enqueued = 0
    for account in accounts:
        try:
            enqueue_sync_job(
                session,
                system="agilecrm",
                account_id=account.account_id,
                operation="sync_contacts",
                triggered_by="cron",
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 - keep siblings alive
            logger.warning(
                "agilecrm.periodic_read enqueue failed account=%s: %s",
                account.account_id,
                exc,
            )
    schedule_periodic_read()
    return SyncOutcome(
        records_processed=enqueued,
        metadata={"checked": len(accounts)},
    )


def schedule_periodic_read() -> None:
    hours = _interval_hours(
        "AGILECRM_SYNC_INTERVAL_HOURS", DEFAULT_READ_INTERVAL_HOURS
    )
    _arm(
        lock=READ_LOCK_KEY,
        queue=queue_name("agilecrm", "periodic_read"),
        job=_periodic_read_runner,
        interval=timedelta(hours=hours),
    )


def _periodic_read_runner() -> None:
    _run_heartbeat(periodic_read_check, operation="periodic_read")


def _arm(
    *,
    lock: str,
    queue: str,
    job: Callable[[], None],
    interval: timedelta,
) -> None:
    # Mismo patrón que Brevo: Redis outage al boot no debe tumbar la
    # API; el próximo restart re-arma el heartbeat. TTL más corto que
    # el interval para que un reinicio que perdió el SETNX se re-arme
    # dentro del próximo tick.
    try:
        conn = redis_connection()
        if not conn.set(
            lock, "1", nx=True, ex=int(interval.total_seconds()) - 30
        ):
            return
        try:
            from rq import Queue  # noqa: PLC0415

            Queue(queue, connection=conn).enqueue_in(interval, job)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agilecrm.heartbeat scheduling failed for %s: %s", queue, exc
            )
            conn.delete(lock)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "agilecrm.heartbeat redis unreachable for %s: %s", queue, exc
        )


def _run_heartbeat(
    handler: Callable[[Session, SyncLog], SyncOutcome], *, operation: str
) -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        fake_log = SyncLog(
            system="agilecrm", operation=operation, status="running"
        )
        handler(session, fake_log)


def arm_periodic_jobs() -> None:
    """Llamada una vez en API startup. Idempotente vía SETNX (multiple
    API procs no se pisan). Si una llamada falla, el resto siguen."""
    for label, fn in (("periodic_read", schedule_periodic_read),):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("agilecrm.scheduler %s arm failed: %s", label, exc)


OPERATIONS["agilecrm:periodic_read"] = periodic_read_check
