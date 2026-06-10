"""Brevo campaign cache + periodic refresh.

`brevo_campaigns_cache` mirrors status + aggregated stats so the
/marketing/campaigns list renders instantly. Refresh paths:

- `refresh_campaigns_cache(session, account_id)` — full catalogue
  pull (paginated).
- `refresh_campaign_row(session, row)` — one campaign, used by the
  detail endpoint when `cached_at` is older than 5 minutes.
- `brevo:refresh_campaigns` heartbeat — every 15 minutes refreshes
  every enabled Brevo account and re-schedules itself (RQ worker
  runs `--with-scheduler`).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.brevo.client import BrevoClient
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ExternalSystem, SyncLog
from app.models.integration_settings import IntegrationAccount
from app.workers.jobs import OPERATIONS, SyncOutcome
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

CACHE_STALE_MINUTES = 5
REFRESH_INTERVAL_SECONDS = 900  # 15 min
REFRESH_LOCK_KEY = "brevo:refresh_campaigns_heartbeat"

#: Aggregated counters extracted from Brevo's campaign statistics
#: block (globalStats or the first row of campaignStats).
STAT_KEYS = (
    "sent",
    "delivered",
    "uniqueViews",
    "viewed",
    "uniqueClicks",
    "clickers",
    "hardBounces",
    "softBounces",
    "unsubscriptions",
    "complaints",
    "mirrorClick",
    "mobileOpen",
)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_stats(payload: dict[str, Any]) -> dict[str, Any]:
    statistics = payload.get("statistics") or {}
    block = statistics.get("globalStats") or {}
    if not block:
        rows = statistics.get("campaignStats") or []
        block = rows[0] if rows else {}
    return {key: block.get(key, 0) for key in STAT_KEYS if key in block} or dict(block)


def upsert_campaign_row(
    session: Session, *, account_id: str, payload: dict[str, Any]
) -> BrevoCampaignCache:
    campaign_id = int(payload.get("id"))
    row = session.scalar(
        select(BrevoCampaignCache).where(
            BrevoCampaignCache.brevo_account_id == account_id,
            BrevoCampaignCache.brevo_campaign_id == campaign_id,
        )
    )
    if row is None:
        row = BrevoCampaignCache(
            brevo_account_id=account_id,
            brevo_campaign_id=campaign_id,
            name=str(payload.get("name") or ""),
            cached_at=datetime.now(UTC),
        )
        session.add(row)
    sender = payload.get("sender") or {}
    recipients = payload.get("recipients") or {}
    row.name = str(payload.get("name") or row.name)
    row.subject = payload.get("subject")
    row.status = str(payload.get("status") or row.status or "draft")
    row.type = str(payload.get("type") or row.type or "classic")
    row.sender_name = sender.get("name")
    row.sender_email = sender.get("email")
    row.reply_to = payload.get("replyTo")
    row.created_at_brevo = _parse_dt(payload.get("createdAt"))
    row.modified_at_brevo = _parse_dt(payload.get("modifiedAt"))
    row.scheduled_at = _parse_dt(payload.get("scheduledAt"))
    row.sent_at = _parse_dt(payload.get("sentDate"))
    row.stats_json = json.dumps(_extract_stats(payload), default=str)
    list_ids = recipients.get("lists") or recipients.get("listIds") or []
    row.recipient_list_ids_json = json.dumps(list_ids)
    template_id = payload.get("templateId")
    row.template_id_used = int(template_id) if template_id else None
    # Brevo's GET /emailCampaigns/{id} response sometimes carries the
    # full htmlContent — keep it when present (the list endpoint
    # never does, so the cache stays None until the detail page
    # triggers `ensure_campaign_html`).
    if payload.get("htmlContent"):
        row.html_content_cached = str(payload["htmlContent"])
    row.cached_at = datetime.now(UTC)
    session.flush()
    return row


def campaign_cache_is_stale(row: BrevoCampaignCache) -> bool:
    cached = row.cached_at
    if cached is None:
        return True
    if cached.tzinfo is None:
        cached = cached.replace(tzinfo=UTC)
    return cached < datetime.now(UTC) - timedelta(minutes=CACHE_STALE_MINUTES)


async def refresh_campaigns_cache(session: Session, account_id: str) -> int:
    touched = 0
    async with BrevoClient(session, account_id) as client:
        offset = 0
        while True:
            body = await client.list_email_campaigns(limit=50, offset=offset)
            campaigns = body.get("campaigns") or []
            if not campaigns:
                break
            for payload in campaigns:
                if payload.get("id") is None:
                    continue
                upsert_campaign_row(session, account_id=account_id, payload=payload)
                touched += 1
            if len(campaigns) < 50:
                break
            offset += 50
    session.flush()
    return touched


async def refresh_campaign_row(
    session: Session, row: BrevoCampaignCache
) -> BrevoCampaignCache:
    async with BrevoClient(session, row.brevo_account_id) as client:
        payload = await client.get_email_campaign(row.brevo_campaign_id)
    upsert_campaign_row(
        session, account_id=row.brevo_account_id, payload=payload
    )
    session.refresh(row)
    return row


async def ensure_campaign_html(
    session: Session, row: BrevoCampaignCache
) -> BrevoCampaignCache:
    """Lazy-load the HTML body on first detail open.

    The list refresh paths never store the HTML (Brevo's list endpoint
    doesn't return it), so the detail page calls this once per
    campaign — the result lands in `html_content_cached` and the next
    detail open serves it from the cache without round-tripping
    Brevo. The full GET also returns fresher status/stats, so we
    upsert the whole row from it; treat this as the cheapest path to
    "make the detail page complete"."""
    if row.html_content_cached is not None:
        return row
    return await refresh_campaign_row(session, row)


# ---------------------------------------------------------------------------
# periodic refresh
# ---------------------------------------------------------------------------


def refresh_brevo_campaigns(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker handler: refresh the campaign cache of every enabled
    Brevo account, then re-arm the 15-minute heartbeat."""
    _ = sync_log
    import asyncio  # noqa: PLC0415

    accounts = list(
        session.scalars(
            select(IntegrationAccount).where(
                IntegrationAccount.system == ExternalSystem.BREVO,
                IntegrationAccount.enabled.is_(True),
            )
        )
    )
    refreshed = 0
    failed = 0
    errors: list[str] = []
    for account in accounts:
        try:
            refreshed += asyncio.run(
                refresh_campaigns_cache(session, account.account_id)
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - account-level isolation
            failed += 1
            errors.append(f"{account.account_id}: {exc}")

    schedule_campaign_refresh()
    return SyncOutcome(
        records_processed=refreshed,
        records_failed=failed,
        error_summary="\n".join(errors) if errors else None,
    )


def schedule_campaign_refresh() -> None:
    """Idempotently arm the next refresh (same SETNX pattern as the
    auto-sync heartbeat)."""
    conn = redis_connection()
    if not conn.set(
        REFRESH_LOCK_KEY, "1", nx=True, ex=REFRESH_INTERVAL_SECONDS - 30
    ):
        return
    try:
        from rq import Queue  # noqa: PLC0415

        queue = Queue(
            queue_name("brevo", "refresh_campaigns"), connection=conn
        )
        queue.enqueue_in(
            timedelta(seconds=REFRESH_INTERVAL_SECONDS),
            run_campaign_refresh_job,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("brevo.campaign_refresh scheduling failed: %s", exc)
        conn.delete(REFRESH_LOCK_KEY)


def run_campaign_refresh_job() -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        fake_log = SyncLog(
            system="brevo", operation="refresh_campaigns", status="running"
        )
        refresh_brevo_campaigns(session, fake_log)


OPERATIONS["brevo:refresh_campaigns"] = refresh_brevo_campaigns
