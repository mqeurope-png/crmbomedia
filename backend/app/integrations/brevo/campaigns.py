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
from app.integrations.brevo.historical_backfill import SENT_STATUSES
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


def _sum_stat_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-list `campaignStats` rows into campaign totals."""
    totals: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in STAT_KEYS:
            value = row.get(key)
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
    return totals


def _block_weight(block: dict[str, Any]) -> int:
    """How much signal a stats block carries — used to pick between
    globalStats and the campaignStats aggregate."""
    return sum(
        int(block.get(key) or 0)
        for key in ("sent", "delivered", "uniqueViews", "viewed")
    )


def _extract_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick the stats block that actually carries data.

    Production lesson (debt-closure Bug 6): for sent campaigns Brevo
    frequently returns `statistics.globalStats` PRESENT but all-zero
    while the real numbers live in the per-list `campaignStats` rows.
    The previous "globalStats if non-empty dict" check happily served
    the zeros, so the detail page showed 0 Enviados on a campaign
    with thousands of sends. Now both candidates are computed and the
    one with actual signal wins."""
    statistics = payload.get("statistics") or {}
    global_block = {
        key: statistics.get("globalStats", {}).get(key, 0)
        for key in STAT_KEYS
        if isinstance(statistics.get("globalStats"), dict)
        and key in statistics["globalStats"]
    }
    summed_block = _sum_stat_rows(statistics.get("campaignStats") or [])

    if _block_weight(summed_block) > _block_weight(global_block):
        return summed_block
    if global_block:
        return global_block
    if summed_block:
        return summed_block
    # Legacy shape: counters directly under `statistics`.
    return {key: statistics[key] for key in STAT_KEYS if key in statistics}


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


def _log_brevo_campaign_payload(
    brevo_campaign_id: int, payload: dict[str, Any]
) -> None:
    """PR-Fix-Sincronizar-Stats-Brevo. INFO-level dump of the raw Brevo
    response so future "stats=0 but Brevo dashboard shows real numbers"
    cases can be diagnosed without reproducing.

    `htmlContent` is stripped (often many KB and not relevant for
    stats); everything else fits in the log line and reveals which
    `statistics.*` path Brevo actually populated for this campaign.
    """
    safe = {k: v for k, v in payload.items() if k != "htmlContent"}
    if payload.get("htmlContent"):
        safe["_htmlContent_chars"] = len(payload["htmlContent"])
    try:
        body = json.dumps(safe, default=str)
    except (TypeError, ValueError):
        logger.info(
            "brevo.refresh_stats campaign_id=%s payload_keys=%s "
            "(unserialisable)",
            brevo_campaign_id,
            list(safe.keys()),
        )
        return
    logger.info(
        "brevo.refresh_stats campaign_id=%s payload=%s",
        brevo_campaign_id,
        body[:4096],
    )


async def refresh_campaign_row(
    session: Session, row: BrevoCampaignCache
) -> BrevoCampaignCache:
    async with BrevoClient(session, row.brevo_account_id) as client:
        payload = await client.get_email_campaign(row.brevo_campaign_id)
    _log_brevo_campaign_payload(row.brevo_campaign_id, payload)
    extracted = _extract_stats(payload)
    # PR-Fix-Sincronizar-Stats-3a-Vez. Loggea el bloque finalmente
    # extraído, alineado con el raw payload de la línea anterior. Si
    # los dos no coinciden (raw tiene 90 pero `extracted=0`), el bug
    # vive en `_extract_stats`. Si los dos son 0 y Brevo dashboard
    # tiene datos, el bug vive en el query param o en la API.
    logger.info(
        "brevo.refresh_stats campaign_id=%s extracted_stats=%s",
        row.brevo_campaign_id,
        json.dumps(extracted, default=str),
    )
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


def find_sent_campaigns_without_events(
    session: Session, *, account_id: str, max_campaigns: int = 100
) -> list[int]:
    """Devuelve los `brevo_campaign_id` de campañas en estado sent/
    archive cuya tabla `activity_events` está vacía. Útil para tirar
    backfill sólo del hueco histórico (campañas enviadas ANTES del
    webhook que no quedaron capturadas).

    Se queda en `max_campaigns` para no ahogar el worker en una sola
    pasada cuando el hueco es grande.
    """
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    cached = list(
        session.scalars(
            select(BrevoCampaignCache)
            .where(BrevoCampaignCache.brevo_account_id == account_id)
            .where(BrevoCampaignCache.status.in_(SENT_STATUSES))
            .order_by(BrevoCampaignCache.sent_at.desc())
            .limit(max_campaigns)
        )
    )
    if not cached:
        return []
    candidate_ids = [c.brevo_campaign_id for c in cached]
    rows_with_events = set(
        session.scalars(
            select(ActivityEvent.campaign_brevo_id)
            .where(ActivityEvent.campaign_brevo_id.in_(candidate_ids))
            .distinct()
        )
    )
    return [cid for cid in candidate_ids if cid not in rows_with_events]


def _auto_enqueue_backfill_for_gaps(
    session: Session, *, account_id: str
) -> int:
    """Tras refrescar el cache, busca campañas sent sin events y encola
    un job de backfill para cubrirlas. Devuelve cuántas se metieron en
    la cola (0 si no hay huecos).

    Idempotente — el handler de historical_backfill ya salta campañas
    que ya tienen events (resultado `skipped`), así que un re-enqueue
    accidental no duplica trabajo.
    """
    from app.models.crm import SyncTrigger  # noqa: PLC0415
    from app.workers.jobs import enqueue_sync_job  # noqa: PLC0415

    gap_ids = find_sent_campaigns_without_events(
        session, account_id=account_id, max_campaigns=50
    )
    if not gap_ids:
        return 0
    try:
        enqueue_sync_job(
            session,
            system=ExternalSystem.BREVO,
            account_id=account_id,
            operation="historical_backfill",
            triggered_by=SyncTrigger.CRON,
            payload={"campaign_brevo_ids": gap_ids},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "brevo.auto_backfill.enqueue_failed account_id=%s err=%s",
            account_id,
            exc,
        )
        return 0
    return len(gap_ids)


def refresh_brevo_campaigns(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker handler: refresh the campaign cache of every enabled
    Brevo account, then re-arm the 15-minute heartbeat.

    Sprint Brevo Backfill (post #54-56): tras el refresh, busca
    campañas sent SIN events en `activity_events` y encola
    automáticamente un `brevo:historical_backfill` con esa lista. Así
    las campañas enviadas antes del webhook se rellenan sin que el
    operador tenga que pulsar nada.
    """
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
    backfilled_auto = 0
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
            continue
        try:
            backfilled_auto += _auto_enqueue_backfill_for_gaps(
                session, account_id=account.account_id
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"{account.account_id} auto-backfill: {exc}"
            )

    schedule_campaign_refresh()
    return SyncOutcome(
        records_processed=refreshed,
        records_failed=failed,
        metadata={"auto_backfilled_campaigns": backfilled_auto},
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
