"""Mark `sync_logs` rows orphaned in PENDING for > 2h as FAILED.

A SyncLog can get stuck on `PENDING` when:

- The worker crashed between `enqueue_sync_job`'s commit and picking
  the job up.
- Redis was down and the job evaporated.
- The queue name changed across deploys and the old name's jobs are
  no longer listened to.

Either way, the row stays as "pending forever" and noises the audit
trail. This script flips them to `FAILED` with an explicit
`error_summary` so the operator knows the row was housekeeping, not
the result of a real failure.

Idempotent: only touches rows still on `PENDING` AND older than the
threshold. A second run prints `0`.

    docker compose --env-file .env.production exec api \
        python scripts/cleanup_stale_sync_logs.py
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.models.crm import SyncLog, SyncStatus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("cleanup_stale_sync_logs")

STALE_AFTER_HOURS = 2


def cleanup(session: Session) -> int:
    boundary = datetime.now(UTC) - timedelta(hours=STALE_AFTER_HOURS)
    rows = list(
        session.scalars(
            select(SyncLog).where(
                SyncLog.status == SyncStatus.PENDING.value,
                SyncLog.created_at < boundary,
            )
        )
    )
    for row in rows:
        row.status = SyncStatus.FAILED.value
        row.error_summary = (
            "Stale pending — worker never picked up. Cleaned up by "
            "`scripts/cleanup_stale_sync_logs.py` 2h+ after creation."
        )
        row.finished_at = datetime.now(UTC)
    session.commit()
    return len(rows)


def main() -> int:
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with factory() as session:
        affected = cleanup(session)
    logger.info("Cleanup complete: %d sync_logs huérfanos marcados como failed", affected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
