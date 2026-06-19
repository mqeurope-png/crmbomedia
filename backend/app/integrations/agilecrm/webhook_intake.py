"""HTTP intake side of the AgileCRM webhook.

Lives in the integrations package (rather than `app/api/`) so the
rest of the connector can import the same helpers — token verifier,
event-type sniff, rate limiter — without dragging the entire FastAPI
router in.

Flow:

1. `agilecrm_account_for_webhook` resolves `(system, account_id)` and
   guards against missing / disabled / no-secret accounts. Either of
   those returns None; the caller answers 200 `skipped` so AgileCRM
   does not retry forever.
2. `verify_webhook_token` constant-time compares the URL token with
   the stored secret.
3. `enqueue_agilecrm_webhook_job` pushes processing off the request
   thread; falls back to inline if Redis is down.
"""
from __future__ import annotations

import hmac as _hmac
import logging
import os
import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
from app.repositories.integration_settings import get_integration_account

logger = logging.getLogger(__name__)

# Same alphabet `secrets.token_urlsafe` produces; we slice the
# 32-byte token down to 43 chars to keep the column predictable
# (column width is 64 to leave room for a future scheme tag).
SECRET_BYTES = 32
SECRET_LENGTH = 43


def generate_webhook_secret() -> str:
    """Cryptographically random URL-safe token (43 chars)."""
    return secrets.token_urlsafe(SECRET_BYTES)[:SECRET_LENGTH]


def agilecrm_account_for_webhook(
    session: Session, account_id: str
) -> IntegrationAccount | None:
    """Return the account if it should accept the webhook, else None.

    None covers three cases — unknown id, disabled flag, or no secret
    configured — that the route handles identically (200 + skipped)
    so a misconfigured Agile webhook doesn't put the URL on Agile's
    retry blacklist."""
    account = get_integration_account(
        session, ExternalSystem.AGILECRM, account_id
    )
    if account is None:
        return None
    if not account.enabled:
        return None
    if not account.webhook_secret:
        return None
    return account


def verify_webhook_token(account: IntegrationAccount, token: str) -> bool:
    """Constant-time comparison. Returns False on missing/blank
    secret to avoid accepting a forged blank token."""
    secret = account.webhook_secret or ""
    if not secret or not token:
        return False
    return _hmac.compare_digest(secret, token)


def enqueue_agilecrm_webhook_job(webhook_event_id: str) -> None:
    """Push to RQ. On Redis failure, fall back to inline processing —
    the row is already persisted so we can replay it from the admin
    audit UI either way."""
    from app.integrations.agilecrm.webhooks import (  # noqa: PLC0415
        process_agilecrm_webhook_job,
    )

    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import (  # noqa: PLC0415
            queue_name,
            redis_connection,
        )

        queue = Queue(
            queue_name("agilecrm", "webhook"),
            connection=redis_connection(),
        )
        queue.enqueue(process_agilecrm_webhook_job, webhook_event_id)
    except Exception:  # noqa: BLE001 - degraded inline path
        logger.exception(
            "agilecrm.webhook enqueue failed; processing inline %s",
            webhook_event_id,
        )
        process_agilecrm_webhook_job(webhook_event_id)


# ---------------------------------------------------------------------
# Rate limit (Redis token bucket per source IP).
# ---------------------------------------------------------------------


DEFAULT_RATE_LIMIT_PER_MIN = 500


def _rate_limit_threshold() -> int:
    raw = os.environ.get("WEBHOOK_RATE_LIMIT_PER_MIN")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_RATE_LIMIT_PER_MIN


def webhook_rate_limit_exceeded(*, ip: str | None) -> bool:
    """Returns True when the IP has exceeded the per-minute budget.

    Best-effort: a Redis outage skips the check so a transient
    infrastructure problem doesn't drop legitimate webhooks. The
    audit log still records every delivery so an abusive client is
    visible offline."""
    if not ip:
        return False
    try:
        import time  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        bucket = int(time.time() // 60)
        key = f"webhook:agilecrm:rl:{ip}:{bucket}"
        client = redis_connection()
        count = client.incr(key)
        if count == 1:
            # First hit in this minute — set a short TTL so the
            # key disappears the moment the window rolls.
            client.expire(key, 90)
        return count > _rate_limit_threshold()
    except Exception:  # noqa: BLE001
        logger.debug(
            "agilecrm.webhook rate-limit check failed; bypassing",
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------
# Event type sniff (used by the intake to stamp the row eagerly so an
# admin querying "give me every contact.delete" doesn't have to crack
# the JSON open server-side).
# ---------------------------------------------------------------------


def sniff_event_type(payload: Any) -> str:
    """Best-effort guess at the AgileCRM event name. Returns an empty
    string when the body shape is unfamiliar; the worker re-derives
    it before processing so this is purely a hint for the row."""
    if not isinstance(payload, dict):
        return ""
    for key in ("event", "type", "notification_type", "eventType"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()[:80]
    return ""
