"""Generic webhook intake.

The first iteration logs the raw payload to `sync_logs` and audits the
delivery. Per-system signature validation (Brevo HMAC, Freshdesk
signature, etc.) lands in each connector's own PR; this layer is the
front door.
"""
# ruff: noqa: I001
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import UTC, datetime

from app.core.audit import Action, record_event
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.agilecrm.webhook_intake import (
    agilecrm_account_for_webhook,
    enqueue_agilecrm_webhook_job,
    sniff_event_type,
    verify_webhook_token,
    webhook_rate_limit_exceeded,
)
from app.models.crm import ExternalSystem, SyncLog, SyncStatus, SyncTrigger
from app.models.integration_settings import IntegrationAccount
from app.models.webhook_events import WebhookEvent, WebhookEventStatus
from app.repositories.integration_settings import get_integration_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Hard cap on persisted payload size. Anything larger gets truncated so
# the audit log doesn't blow up disk on a misbehaving remote.
MAX_RAW_PAYLOAD_BYTES = 64 * 1024

#: Header names Brevo deployments use for the optional webhook auth
#: token. Brevo lets the operator attach a custom header when creating
#: the webhook; we accept the conventional spellings.
BREVO_SIGNATURE_HEADERS = (
    "brevo-signature-token",
    "x-brevo-signature",
    "x-sib-signature",
)


@router.post("/brevo", status_code=status.HTTP_200_OK)
async def receive_brevo_webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Dedicated Brevo receiver (no account segment — Brevo can't add
    path params per webhook; the single enabled Brevo account is
    resolved server-side).

    Fast path: validate signature → parse → enqueue to
    `brevo:webhook_process` → 200. Dedupe happens in the worker.
    Brevo retries non-2xx aggressively, so anything recoverable must
    still answer 200 quickly.
    """
    import hmac as _hmac  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    secret = settings.brevo_webhook_secret
    provided = next(
        (
            request.headers.get(header)
            for header in BREVO_SIGNATURE_HEADERS
            if request.headers.get(header)
        ),
        None,
    )
    if secret:
        if not provided or not _hmac.compare_digest(secret, provided):
            logger.warning(
                "brevo.webhook rejected: signature mismatch (header %s)",
                "present" if provided else "missing",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
    else:
        logger.warning(
            "brevo.webhook accepted WITHOUT signature validation — set "
            "BREVO_WEBHOOK_SECRET (and mirror it in Brevo) to harden this "
            "endpoint."
        )

    raw_body = await request.body()
    try:
        parsed: Any = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (UnicodeDecodeError, ValueError):
        parsed = None
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be JSON",
        )
    events: list[dict[str, Any]] = (
        parsed if isinstance(parsed, list) else [parsed]
    )
    events = [e for e in events if isinstance(e, dict)]
    if not events:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No events in payload",
        )

    # Resolve the (single) enabled Brevo account. With several enabled
    # accounts we'd need Brevo to tag the source — log loudly and use
    # the first by sync_priority.
    accounts = list(
        session.scalars(
            select(IntegrationAccount)
            .where(
                IntegrationAccount.system == ExternalSystem.BREVO,
                IntegrationAccount.enabled.is_(True),
            )
            .order_by(IntegrationAccount.sync_priority)
        )
    )
    if not accounts:
        raise not_found("Enabled Brevo account")
    if len(accounts) > 1:
        logger.warning(
            "brevo.webhook %d enabled Brevo accounts; attributing events to %r",
            len(accounts),
            accounts[0].account_id,
        )
    account = accounts[0]

    sync_log = SyncLog(
        system=ExternalSystem.BREVO,
        account_id=account.account_id,
        operation="webhook_received",
        status=SyncStatus.SUCCESS.value,
        triggered_by=SyncTrigger.WEBHOOK.value,
        records_processed=len(events),
        metadata_json=json.dumps(
            {"events": [e.get("event") for e in events]}, default=str
        ),
    )
    session.add(sync_log)
    session.flush()
    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_RECEIVED,
        target_type="integration_account",
        target_id=account.id,
        metadata={
            "system": "brevo",
            "account_id": account.account_id,
            "event_count": len(events),
            "sync_log_id": sync_log.id,
        },
        request=request,
    )
    session.commit()

    _enqueue_brevo_events(events, account.account_id)
    return {"received": True, "events": len(events)}


def _enqueue_brevo_events(events: list[dict[str, Any]], account_id: str) -> None:
    """Push processing off the request thread. On enqueue failure
    (Redis down) fall back to inline processing — slower for Brevo,
    but the event is never silently dropped."""
    from app.integrations.brevo.webhooks import (  # noqa: PLC0415
        process_brevo_webhook_batch,
    )

    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import queue_name, redis_connection  # noqa: PLC0415

        queue = Queue(
            queue_name("brevo", "webhook_process"),
            connection=redis_connection(),
        )
        queue.enqueue(process_brevo_webhook_batch, events, account_id)
    except Exception:  # noqa: BLE001 - degraded inline path
        logger.exception(
            "brevo.webhook enqueue failed; processing %d events inline",
            len(events),
        )
        process_brevo_webhook_batch(events, account_id)


# Hard cap on AgileCRM payload persistence. AgileCRM payloads are
# usually 2-4 KB; the cap is conservative so a misbehaving sender can't
# blow up the `webhook_events` table.
_AGILECRM_MAX_PAYLOAD_BYTES = 64 * 1024


def _client_ip(request: Request) -> str | None:
    """Leftmost X-Forwarded-For (nginx in front of api), else the
    direct peer. The leftmost address is the only one the original
    client controls; anything appended is proxy chain."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first[:45]
    if request.client and request.client.host:
        return request.client.host[:45]
    return None


@router.post(
    "/agilecrm/{account_id}/incoming",
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_agilecrm_webhook(
    account_id: str,
    request: Request,
    response: Response,
    token: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """AgileCRM real-time intake.

    Verifies the per-account `?token=` shared secret, persists the
    delivery to `webhook_events`, and hands off to RQ. AgileCRM
    retries non-2xx aggressively, so anything recoverable (unknown
    account, account disabled, no secret configured) answers 200
    with `status=skipped` instead of an error.

    `token` is mandatory but it's a query param (AgileCRM webhooks
    don't expose header config), so the unknown-account skip path
    runs regardless of token to keep noise low.
    """
    source_ip = _client_ip(request)
    if webhook_rate_limit_exceeded(ip=source_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    account = agilecrm_account_for_webhook(session, account_id)
    if account is None:
        # 200 not 404 so AgileCRM doesn't keep retrying. We still
        # log the rejection for visibility.
        logger.info(
            "agilecrm.webhook unknown/disabled/no-secret account=%s",
            account_id,
        )
        response.status_code = status.HTTP_200_OK
        return {"status": "skipped", "reason": "account_unavailable"}

    if not verify_webhook_token(account, token):
        logger.warning(
            "agilecrm.webhook bad token account=%s ip=%s",
            account_id,
            source_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token",
        )

    raw_body = await request.body()
    payload_snippet = raw_body[:_AGILECRM_MAX_PAYLOAD_BYTES]
    try:
        parsed: Any = (
            json.loads(payload_snippet.decode("utf-8"))
            if payload_snippet
            else None
        )
    except (UnicodeDecodeError, ValueError):
        parsed = None

    # Stamp the row eagerly. Worker re-derives the canonical event
    # type from the body before processing so this is just a hint
    # for the admin filter dropdown.
    event_type_hint = sniff_event_type(parsed) or "unknown"

    payload_text = (
        json.dumps(parsed, default=str)
        if parsed is not None
        else payload_snippet.decode("utf-8", errors="replace")
    )

    now = datetime.now(UTC)
    webhook_event = WebhookEvent(
        system=ExternalSystem.AGILECRM.value,
        account_id=account.account_id,
        event_type=event_type_hint,
        payload_json=payload_text,
        status=WebhookEventStatus.RECEIVED,
        received_at=now,
        source_ip=source_ip,
    )
    session.add(webhook_event)
    account.webhook_last_received_at = now
    session.flush()
    record_event(
        session,
        action=Action.INTEGRATION_WEBHOOK_RECEIVED,
        target_type="webhook_event",
        target_id=webhook_event.id,
        metadata={
            "system": "agilecrm",
            "account_id": account.account_id,
            "event_type": event_type_hint,
            "payload_size_bytes": len(raw_body),
        },
        request=request,
    )
    session.commit()

    enqueue_agilecrm_webhook_job(webhook_event.id)
    return {
        "status": "queued",
        "webhook_event_id": webhook_event.id,
        "event_type": event_type_hint,
    }


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
