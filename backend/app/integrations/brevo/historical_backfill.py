"""Materialise past Brevo campaign events into `activity_events`.

The live webhook only fires from the moment Bart sets it up. Every
campaign sent before that day has no granular trail in the CRM — the
campaign detail page shows aggregated stats but the contact page's
"Actividad email" section stays empty for pre-webhook deliveries.
Brevo's API exposes the recipients per event on every past
campaign, so this module walks the cached `brevo_campaigns_cache`
rows and back-fills the missing `activity_events` rows from the
remote.

Idempotency rides on the existing `activity_events` UNIQUE
constraint `(system, account_id, external_id)`. The `external_id`
we synthesise is deterministic per (campaign, recipient, event):

    backfill:{brevo_campaign_id}:{email_normalised}:{event_type}

A second run hits the same key → IntegrityError on insert → we
swallow it and count the row as "already there". No upfront SELECT
per row needed.

Webhooks NEVER create contacts; the backfill follows the same rule.
A recipient email that doesn't match any CRM contact is logged as
`contacts_unknown` and skipped.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.brevo.client import BrevoClient
from app.integrations.errors import (
    IntegrationClientError,
    IntegrationError,
)
from app.models.brevo import BrevoCampaignCache
from app.models.crm import ActivityEvent, Contact, SyncLog
from app.workers.jobs import OPERATIONS, SyncOutcome

logger = logging.getLogger(__name__)

#: Brevo event-type segment (URL path) → internal `email.*` event.
EVENT_TYPE_MAP: dict[str, str] = {
    "delivered": "email.delivered",
    "opened": "email.opened",
    "clicked": "email.clicked",
    "bounced": "email.bounced_hard",
    "soft-bounce": "email.bounced_soft",
    "unsubscribed": "email.unsubscribed",
    "complaints": "email.spam_complaint",
}

#: Brevo's per-event recipient response sometimes carries the
#: precise timestamp under a different key. Order matters — we pick
#: the first that exists.
TIMESTAMP_KEYS = ("openedAt", "clickedAt", "eventTime", "date", "deliveredAt")

PAGE_SIZE = 500
SENT_STATUSES = {"sent", "archive"}

#: Brevo throttles campaign-data endpoints to ~100 req/min — much
#: stricter than the 400 req/min on contacts. Production runs of the
#: backfill saturated the bucket within seconds at concurrency=2 +
#: 200 ms sleep, so we drop to serial requests with a 1 s gap. The
#: rewrite (commit 4) uses asynchronous CSV exports anyway: there is
#: at most one HTTP call in flight per account, the sleep just paces
#: the polling loop and the post-CSV materialisation calls.
_CONCURRENCY = 1
_INTER_CALL_SLEEP_SECONDS = 1.0


def _normalise_email(value: Any) -> str | None:
    if not value:
        return None
    return str(value).strip().lower() or None


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _occurred_at(
    entry: dict[str, Any], fallback: datetime | None
) -> datetime:
    for key in TIMESTAMP_KEYS:
        parsed = _coerce_dt(entry.get(key))
        if parsed is not None:
            return parsed
    if fallback is not None:
        if fallback.tzinfo is None:
            fallback = fallback.replace(tzinfo=UTC)
        return fallback
    return datetime.now(UTC)


def _resolve_emails_to_contact_ids(
    session: Session, emails: list[str]
) -> dict[str, str]:
    """Bulk lookup → `{normalised_email: contact_id}` for the emails
    we actually have a CRM row for. Unknown emails are simply absent
    from the map — caller counts them as `contacts_unknown`."""
    if not emails:
        return {}
    rows = session.execute(
        select(Contact.id, Contact.email).where(
            func.lower(Contact.email).in_(emails)
        )
    ).all()
    return {email.lower(): cid for cid, email in rows if email}


def _external_id(campaign_id: int, email: str, event_type: str) -> str:
    """Deterministic dedup key. Capped at the column's 255-char
    limit; in practice campaign ids + emails stay well below."""
    raw = f"backfill:{campaign_id}:{email}:{event_type}"
    return raw[:255]


async def _fetch_event_recipients(
    client: BrevoClient,
    campaign_id: int,
    brevo_event: str,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Paginate `/emailCampaigns/{id}/{event}` until empty. The
    semaphore caps concurrent calls across event types; the
    `asyncio.sleep` between pages paces successive calls so a tenant
    with hundreds of past campaigns doesn't burn the Brevo quota."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        async with semaphore:
            try:
                body = await client.get_campaign_recipients_stats(
                    campaign_id, brevo_event, limit=PAGE_SIZE, offset=offset
                )
            except IntegrationClientError as exc:
                # 404 on an old campaign happens — skip the event,
                # keep the rest of the backfill running.
                logger.warning(
                    "brevo.backfill recipients %s/%s status=%s — skipping",
                    campaign_id,
                    brevo_event,
                    exc.status_code,
                )
                break
        recipients = body.get("recipients") or body.get("contacts") or []
        if not recipients:
            break
        out.extend(recipients)
        if len(recipients) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        await asyncio.sleep(_INTER_CALL_SLEEP_SECONDS)
    return out


def backfill_campaign_events(
    session: Session,
    *,
    account_id: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Back-fill every supported event of one cached campaign.

    Returns a stats dict. Re-runnable: the second run inserts zero
    new rows (every event hits the UNIQUE constraint and is counted
    as already-present)."""
    row = session.get(BrevoCampaignCache, campaign_id)
    if row is None:
        return {"campaign_id": campaign_id, "skipped": True, "reason": "not_cached"}
    if row.status not in SENT_STATUSES:
        return {
            "campaign_id": campaign_id,
            "brevo_campaign_id": row.brevo_campaign_id,
            "skipped": True,
            "reason": "not_sent",
        }

    stats: dict[str, Any] = {
        "campaign_id": campaign_id,
        "brevo_campaign_id": row.brevo_campaign_id,
        "campaign_name": row.name,
        "events_inserted": 0,
        "events_skipped_existing": 0,
        "contacts_unknown": 0,
        "errors": [],
    }
    sent_at_fallback = row.sent_at or row.created_at_brevo

    async def _drive() -> None:
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        async with BrevoClient(session, account_id) as client:
            for brevo_event, internal_event in EVENT_TYPE_MAP.items():
                try:
                    recipients = await _fetch_event_recipients(
                        client, row.brevo_campaign_id, brevo_event, semaphore
                    )
                except IntegrationError as exc:
                    stats["errors"].append(
                        f"{brevo_event}: {exc.message}"
                    )
                    continue
                _materialise_event(
                    session,
                    account_id=account_id,
                    row=row,
                    brevo_event=brevo_event,
                    internal_event=internal_event,
                    recipients=recipients,
                    fallback_dt=sent_at_fallback,
                    stats=stats,
                )

    asyncio.run(_drive())
    return stats


def _materialise_event(
    session: Session,
    *,
    account_id: str,
    row: BrevoCampaignCache,
    brevo_event: str,
    internal_event: str,
    recipients: list[dict[str, Any]],
    fallback_dt: datetime | None,
    stats: dict[str, Any],
) -> None:
    """Resolve emails → CRM contacts in one batch, then insert each
    event row inside its own SAVEPOINT so the UNIQUE-key collision
    on duplicates doesn't poison the surrounding transaction."""
    normalised: list[tuple[dict[str, Any], str]] = []
    for entry in recipients:
        email = _normalise_email(entry.get("email"))
        if not email:
            continue
        normalised.append((entry, email))
    if not normalised:
        return

    email_to_contact = _resolve_emails_to_contact_ids(
        session, [email for _, email in normalised]
    )
    for entry, email in normalised:
        contact_id = email_to_contact.get(email)
        if contact_id is None:
            stats["contacts_unknown"] += 1
            continue
        external_id = _external_id(
            row.brevo_campaign_id, email, brevo_event
        )
        payload = {
            "campaign_id": row.id,
            "campaign_brevo_id": row.brevo_campaign_id,
            "campaign_name": row.name,
            "recipient_email": email,
            "brevo_event": brevo_event,
            "source": "historical_backfill",
            "raw_event": entry,
        }
        savepoint = session.begin_nested()
        session.add(
            ActivityEvent(
                contact_id=contact_id,
                system="brevo",
                account_id=account_id,
                external_id=external_id,
                event_type=internal_event,
                subject=row.subject,
                body=str(entry.get("url") or entry.get("link") or "") or None,
                metadata_json=json.dumps(payload, default=str),
                occurred_at=_occurred_at(entry, fallback_dt),
                synced_at=datetime.now(UTC),
            )
        )
        try:
            session.flush()
            savepoint.commit()
            stats["events_inserted"] += 1
        except IntegrityError:
            savepoint.rollback()
            stats["events_skipped_existing"] += 1


def backfill_account_campaigns(
    session: Session,
    *,
    account_id: str,
    max_campaigns: int | None = None,
) -> dict[str, Any]:
    """Iterate every sent/archived campaign in the local cache (most
    recent first) and call `backfill_campaign_events`.

    Operates on the CACHE only — campaigns missing from
    `brevo_campaigns_cache` aren't fetched here (that's
    `refresh_campaigns_cache`'s job). Bart's runbook is: refresh
    first, backfill second."""
    statement = (
        select(BrevoCampaignCache)
        .where(BrevoCampaignCache.brevo_account_id == account_id)
        .where(BrevoCampaignCache.status.in_(SENT_STATUSES))
        # MySQL 8 doesn't support `NULLS LAST`; bare `.desc()` already
        # pushes NULLs to the end on a DESC sort, which is what we want
        # (campaigns without a `sent_at` are odd outliers and don't
        # need to jump the queue).
        .order_by(BrevoCampaignCache.sent_at.desc())
    )
    if max_campaigns is not None:
        statement = statement.limit(max_campaigns)
    rows = list(session.scalars(statement))

    aggregate: dict[str, Any] = {
        "campaigns_processed": 0,
        "campaigns_skipped": 0,
        "events_inserted_total": 0,
        "events_skipped_total": 0,
        "contacts_unknown_total": 0,
        "errors": [],
        "per_campaign": [],
    }
    for cached in rows:
        try:
            result = backfill_campaign_events(
                session,
                account_id=account_id,
                campaign_id=cached.id,
            )
        except Exception as exc:  # noqa: BLE001 — keep siblings running
            aggregate["errors"].append(
                f"campaign={cached.brevo_campaign_id}: {exc}"
            )
            session.rollback()
            continue
        if result.get("skipped"):
            aggregate["campaigns_skipped"] += 1
        else:
            aggregate["campaigns_processed"] += 1
            aggregate["events_inserted_total"] += result["events_inserted"]
            aggregate["events_skipped_total"] += result["events_skipped_existing"]
            aggregate["contacts_unknown_total"] += result["contacts_unknown"]
            aggregate["errors"].extend(result["errors"])
        aggregate["per_campaign"].append(result)
        session.commit()
    return aggregate


# ---------------------------------------------------------------------------
# Worker handler
# ---------------------------------------------------------------------------


def run_historical_backfill(
    session: Session, sync_log: SyncLog
) -> SyncOutcome:
    """`brevo:historical_backfill` worker entry. Payload may carry
    `{max_campaigns: N}` to bound the run."""
    account_id = sync_log.account_id or ""
    payload: dict[str, Any] = {}
    if sync_log.metadata_json:
        try:
            decoded = json.loads(sync_log.metadata_json)
            payload = (
                decoded.get("payload") or decoded
                if isinstance(decoded, dict)
                else {}
            )
        except (ValueError, TypeError):
            payload = {}
    max_campaigns = payload.get("max_campaigns")
    try:
        max_campaigns = int(max_campaigns) if max_campaigns else None
    except (TypeError, ValueError):
        max_campaigns = None

    try:
        stats = backfill_account_campaigns(
            session,
            account_id=account_id,
            max_campaigns=max_campaigns,
        )
    except Exception as exc:  # noqa: BLE001
        return SyncOutcome(records_failed=1, error_summary=str(exc))

    error_summary = "\n".join(stats["errors"]) if stats["errors"] else None
    return SyncOutcome(
        records_processed=stats["events_inserted_total"],
        records_skipped=stats["events_skipped_total"]
        + stats["contacts_unknown_total"],
        records_failed=len(stats["errors"]),
        error_summary=error_summary,
        metadata={
            "campaigns_processed": stats["campaigns_processed"],
            "campaigns_skipped": stats["campaigns_skipped"],
            "events_inserted_total": stats["events_inserted_total"],
            "events_skipped_total": stats["events_skipped_total"],
            "contacts_unknown_total": stats["contacts_unknown_total"],
            "max_campaigns": max_campaigns,
        },
    )


OPERATIONS["brevo:historical_backfill"] = run_historical_backfill
