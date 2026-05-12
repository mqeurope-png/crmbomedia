"""Generic webhook intake.

The first iteration logs the raw payload to `sync_logs` and audits the
delivery. Per-system signature validation (Brevo HMAC, Freshdesk
signature, etc.) lands in each connector's own PR; this layer is the
front door.
"""
# ruff: noqa: I001
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import ExternalSystem, SyncLog, SyncStatus, SyncTrigger
from app.repositories.integration_settings import get_integration_account

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Hard cap on persisted payload size. Anything larger gets truncated so
# the audit log doesn't blow up disk on a misbehaving remote.
MAX_RAW_PAYLOAD_BYTES = 64 * 1024


@router.post(
    "/{system}/{account_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_webhook(
    system: ExternalSystem,
    account_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    account = get_integration_account(session, system, account_id)
    if account is None:
        raise not_found("Integration account")

    # NOTE: signature validation is intentionally NOT here yet — each
    # per-system PR adds its own verifier before persisting the payload.
    raw_body = await request.body()
    payload_size = len(raw_body)
    body_snippet = raw_body[:MAX_RAW_PAYLOAD_BYTES]
    try:
        parsed: Any = json.loads(body_snippet.decode("utf-8")) if body_snippet else None
    except (UnicodeDecodeError, ValueError):
        parsed = None

    metadata: dict[str, Any] = {
        "system": system.value,
        "account_id": account_id,
        "payload_size_bytes": payload_size,
        "content_type": request.headers.get("content-type"),
    }
    if parsed is not None:
        metadata["payload"] = parsed
    else:
        # Keep the raw text (truncated) for diagnostics when it isn't JSON.
        try:
            metadata["raw"] = body_snippet.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            metadata["raw"] = None

    sync_log = SyncLog(
        system=system,
        account_id=account_id,
        operation="webhook_received",
        status=SyncStatus.SUCCESS.value,
        triggered_by=SyncTrigger.WEBHOOK.value,
        records_processed=1,
        metadata_json=json.dumps(metadata, default=str),
    )
    session.add(sync_log)
    session.flush()

    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_RECEIVED,
        target_type="integration_account",
        target_id=account.id,
        metadata={
            "system": system.value,
            "account_id": account_id,
            "payload_size_bytes": payload_size,
            "sync_log_id": sync_log.id,
        },
        request=request,
    )
    session.commit()
    return {
        "received": True,
        "sync_log_id": sync_log.id,
        "system": system.value,
        "account_id": account_id,
    }
