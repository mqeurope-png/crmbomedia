"""Background jobs for the email_templates package.

Importar este módulo registra `email_templates:import_gmail` en
`app.workers.jobs.OPERATIONS`. El handler envuelve el servicio
síncrono `import_gmail_templates_with_tpl_prefix` para que corra
en RQ sin bloquear la request HTTP.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models.crm import SyncLog
from app.workers.jobs import OPERATIONS, SyncOutcome

logger = logging.getLogger(__name__)


def run_import_gmail_templates(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Importa los drafts `[TPL] …` del usuario que disparó el job
    a `email_templates`. Lee `user_id` + `delete_after` del payload
    serializado en `sync_log.metadata_json`."""
    from app.integrations.gmail.service import (  # noqa: PLC0415
        import_gmail_templates_with_tpl_prefix,
    )

    raw_payload = sync_log.metadata_json or "{}"
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}

    user_id = payload.get("user_id")
    delete_after = bool(payload.get("delete_after", False))
    if not user_id:
        return SyncOutcome(
            records_failed=1,
            error_summary="payload missing user_id",
        )

    counters = import_gmail_templates_with_tpl_prefix(
        session,
        user_id=user_id,
        created_by_user_id=user_id,
        delete_after=delete_after,
        sync_log=sync_log,
    )

    return SyncOutcome(
        records_processed=counters.get("imported", 0),
        records_skipped=counters.get("skipped", 0),
        records_failed=counters.get("errors", 0),
        metadata={
            "imported": counters.get("imported", 0),
            "skipped": counters.get("skipped", 0),
            "errors": counters.get("errors", 0),
            "deleted": counters.get("deleted", 0),
            "total_drafts_scanned": counters.get("total_drafts_scanned", 0),
            "tpl_drafts_found": counters.get("tpl_drafts_found", 0),
        },
    )


OPERATIONS["email_templates:import_gmail"] = run_import_gmail_templates
