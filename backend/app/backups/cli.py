"""CLI entry point for cron-triggered backups.

Invocado desde crontab:
    0 3 */3 * * cd /opt/crmbo/backend && \\
        python -m app.backups.cli >> /var/log/crmbo-backup.log 2>&1

Crea una row `triggered_by='cron'` con `created_by_user_id=NULL`, la
deja en estado `RUNNING`, y llama a `run_backup` síncronamente. No
encola job RQ — el cron ya está corriendo en un proceso dedicado.
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy.orm import Session

from app.backups.service import create_backup_row, run_backup
from app.db.session import get_engine
from app.models.crm import BackupTrigger

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s %(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    session = Session(get_engine())
    try:
        backup = create_backup_row(
            session, triggered_by=BackupTrigger.CRON, user_id=None
        )
        session.commit()
        backup_id = backup.id
    finally:
        session.close()

    logger.info("cron backup row created id=%s", backup_id)
    result = run_backup(backup_id)
    logger.info("cron backup done id=%s result=%s", backup_id, result)
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
