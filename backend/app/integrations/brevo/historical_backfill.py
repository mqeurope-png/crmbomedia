"""Materialise past Brevo campaign events into `activity_events`.

The live webhook only fires from the moment Bart sets it up. Every
campaign sent before that day has no granular trail in the CRM — the
campaign detail page shows aggregated stats but the contact page's
"Actividad email" section stays empty for pre-webhook deliveries.

PR #54 implemented this against `GET /emailCampaigns/{id}/{event_type}`
which **does not exist** in Brevo's API v3 (404 in production). The
supported path is an asynchronous **export job** per campaign and
per recipient bucket:

  POST /emailCampaigns/{id}/exportRecipients  body={recipientsType}
       → {"processId": N}
  GET  /processes/{N}     → {"status": "...", "exportUrl?": "..."}
  GET  exportUrl  (signed)  → CSV with the recipient list

We poll the process until it lands on `completed` or `aborted`, download
the CSV, decode it (utf-8-sig handles Brevo's BOM cleanly), and
materialise one `activity_events` row per known CRM contact. Unknown
emails are counted as `contacts_unknown` — webhooks never create
contacts and the backfill follows the same rule.

Idempotency rides on the `activity_events` UNIQUE constraint
`(system, account_id, external_id)`. We keep PR #54's external_id
pattern so a fresh CSV download doesn't re-create rows already left
behind by the buggy v1 run:

    backfill:{brevo_campaign_id}:{email_normalised}:{event_type}

This module is meant to be run **once per installation**: after the
backfill finishes, the live webhook covers everything going forward.
A second run is harmless (every row hits the UNIQUE key and is counted
as `events_skipped_existing`) but it costs API quota.
"""
from __future__ import annotations

import asyncio
import csv
import io
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

#: Brevo `recipientsType` → internal `email.*` event mapping.
#:
#: Only the buckets that map cleanly to a single observable event are
#: listed. We deliberately skip `all`, `nonClickers` and `nonOpeners`
#: (redundant with the positive buckets) and `delivered`/`complaints`
#: (Brevo does not expose recipient lists for those via the export
#: endpoint).
#:
#: Production lesson: the bounce buckets are spelled `softBounces` /
#: `hardBounces` — NOT `softBouncers`/`hardBouncers` as the docs'
#: enum suggested. Brevo replies 400 `invalid_parameter` on the
#: latter.
EVENT_TYPE_MAP: dict[str, str] = {
    "openers": "email.opened",
    "clickers": "email.clicked",
    "softBounces": "email.bounced_soft",
    "hardBounces": "email.bounced_hard",
    "unsubscribed": "email.unsubscribed",
}

SENT_STATUSES = {"sent", "archive"}

#: Adaptive polling schedule for the process endpoint, in seconds. We
#: start tight because most exports finish under a minute, then back
#: off so a long-running job doesn't waste 360 polls/hour. The last
#: value repeats once the schedule is exhausted.
_POLL_SCHEDULE_SECONDS: tuple[float, ...] = (5, 10, 15, 30, 30, 60, 60, 120)

#: Hard wall on how long we wait for a single export. Brevo runs the
#: export queue with no SLA — observed real-world latency stays
#: comfortably under 5 minutes per campaign but a stuck export must
#: NOT block the worker forever.
_EXPORT_TIMEOUT_SECONDS = 1800.0

#: Brevo throttles campaign-data endpoints to ~100 req/min — much
#: stricter than the 400 req/min on contacts. With the export flow we
#: only fire 1-2 API calls per polling tick (start_export + status
#: polls); a 1 s gap between calls keeps the bucket healthy and
#: matches the rate-limit cushion the live integration runs with.
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


def _fallback_occurred_at(fallback: datetime | None) -> datetime:
    """The CSV export only carries the recipient list — there's no
    per-event timestamp. We anchor every row to the campaign's
    `sent_at` so the timeline shows the right calendar day."""
    if fallback is None:
        return datetime.now(UTC)
    if fallback.tzinfo is None:
        return fallback.replace(tzinfo=UTC)
    return fallback


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
    limit; in practice campaign ids + emails stay well below.

    Kept stable across PR #54 → this PR so a partial run from the
    legacy flow doesn't re-insert rows that already landed."""
    raw = f"backfill:{campaign_id}:{email}:{event_type}"
    return raw[:255]


# ---------------------------------------------------------------------------
# Export + polling primitives
# ---------------------------------------------------------------------------


async def _wait_for_export(
    client: BrevoClient,
    process_id: int,
    *,
    timeout_seconds: float = _EXPORT_TIMEOUT_SECONDS,
    poll_schedule: tuple[float, ...] = _POLL_SCHEDULE_SECONDS,
    sleeper: Any = None,
) -> dict[str, Any]:
    """Block until a Brevo export `processId` reaches a terminal state.

    Returns the last `get_process_status` body (status, exportUrl, ...).
    Raises `TimeoutError` if the process is still queued/in_process
    after `timeout_seconds`. The `sleeper` hook lets tests fast-forward
    the schedule without monkeypatching `asyncio.sleep` globally."""
    sleep = sleeper or asyncio.sleep
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    schedule_iter = iter(poll_schedule)
    last_wait = poll_schedule[-1]
    while True:
        body = await client.get_process_status(process_id)
        status = str(body.get("status") or "").lower()
        if status in {"completed", "aborted", "failed", "error"}:
            return body
        now = asyncio.get_event_loop().time()
        if now >= deadline:
            raise TimeoutError(
                f"Brevo export process {process_id} did not finish "
                f"within {timeout_seconds:.0f}s (last status={status!r})"
            )
        wait = next(schedule_iter, last_wait)
        # Don't oversleep past the deadline.
        wait = min(wait, max(deadline - now, 0.0))
        await sleep(wait)


def _parse_export_csv(csv_bytes: bytes) -> list[str]:
    """Decode a Brevo recipients CSV → list of normalised emails.

    Production lessons (the assumptions in the first version were
    both wrong):

    - The delimiter is a SEMICOLON, not a comma. `dialect=csv.excel`
      silently parsed each row into a single mega-column and zero
      emails matched.
    - The address lives under `Email_ID`, not `email`.

    Brevo writes UTF-8 with a BOM, so `utf-8-sig` is the right codec.
    Rows without an email value are silently dropped — those rare
    cases (e.g. anonymised recipients) wouldn't match a CRM contact
    anyway."""
    if not csv_bytes:
        return []
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    emails: list[str] = []
    seen: set[str] = set()
    for row in reader:
        if not row:
            continue
        normalised = _normalise_email(row.get("Email_ID"))
        if not normalised or normalised in seen:
            continue
        seen.add(normalised)
        emails.append(normalised)
    return emails


async def _fetch_recipients_via_export(
    client: BrevoClient,
    campaign_id: int,
    recipients_type: str,
    *,
    timeout_seconds: float = _EXPORT_TIMEOUT_SECONDS,
) -> tuple[list[str], str | None]:
    """Start an export, poll until done, download and parse the CSV.

    Returns `(emails, error)`. On success `error` is None. A non-fatal
    Brevo response (process aborted, export endpoint 4xx, signed URL
    404) returns an empty list + a human-readable error so the caller
    can skip the bucket and keep the rest of the run alive."""
    try:
        process_id = await client.start_recipients_export(
            campaign_id, recipients_type
        )
    except IntegrationClientError as exc:
        # 400/404 on an old campaign happens — skip the bucket but
        # keep the rest of the run alive.
        logger.warning(
            "brevo.backfill export %s/%s status=%s — skipping",
            campaign_id,
            recipients_type,
            exc.status_code,
        )
        return [], f"{recipients_type}: start_export status={exc.status_code}"

    try:
        body = await _wait_for_export(
            client, process_id, timeout_seconds=timeout_seconds
        )
    except TimeoutError as exc:
        logger.warning(
            "brevo.backfill export %s/%s timed out (process=%s)",
            campaign_id,
            recipients_type,
            process_id,
        )
        return [], f"{recipients_type}: {exc}"

    status = str(body.get("status") or "").lower()
    if status != "completed":
        logger.warning(
            "brevo.backfill export %s/%s ended status=%s — skipping",
            campaign_id,
            recipients_type,
            status,
        )
        return [], f"{recipients_type}: process status={status!r}"

    export_url = body.get("exportUrl") or body.get("export_url")
    if not export_url:
        return [], f"{recipients_type}: completed without exportUrl"

    try:
        csv_bytes = await client.download_csv_export(str(export_url))
    except IntegrationError as exc:
        logger.warning(
            "brevo.backfill download %s/%s failed: %s",
            campaign_id,
            recipients_type,
            exc.message,
        )
        return [], f"{recipients_type}: download {exc.message}"

    return _parse_export_csv(csv_bytes), None


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def backfill_campaign_events(
    session: Session,
    *,
    account_id: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Back-fill every supported event of one cached campaign.

    Drives the export flow once per `recipientsType`, serially —
    Brevo enqueues exports per account and parallel jobs only add
    latency without lifting the throughput ceiling.

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
        async with BrevoClient(session, account_id) as client:
            for recipients_type, internal_event in EVENT_TYPE_MAP.items():
                try:
                    emails, fetch_error = await _fetch_recipients_via_export(
                        client, row.brevo_campaign_id, recipients_type
                    )
                except IntegrationError as exc:
                    stats["errors"].append(
                        f"{recipients_type}: {exc.message}"
                    )
                    continue
                if fetch_error:
                    stats["errors"].append(fetch_error)
                if not emails:
                    continue
                _materialise_emails(
                    session,
                    account_id=account_id,
                    row=row,
                    recipients_type=recipients_type,
                    internal_event=internal_event,
                    emails=emails,
                    fallback_dt=sent_at_fallback,
                    stats=stats,
                )
                # Pace the materialisation step so we don't pile back
                # onto the rate-limit bucket immediately after the CSV
                # download.
                await asyncio.sleep(_INTER_CALL_SLEEP_SECONDS)

    asyncio.run(_drive())
    return stats


def _materialise_emails(
    session: Session,
    *,
    account_id: str,
    row: BrevoCampaignCache,
    recipients_type: str,
    internal_event: str,
    emails: list[str],
    fallback_dt: datetime | None,
    stats: dict[str, Any],
) -> None:
    """Resolve emails → CRM contacts in one batch, then insert each
    event row inside its own SAVEPOINT so the UNIQUE-key collision
    on duplicates doesn't poison the surrounding transaction."""
    if not emails:
        return
    email_to_contact = _resolve_emails_to_contact_ids(session, emails)
    occurred_at = _fallback_occurred_at(fallback_dt)
    for email in emails:
        contact_id = email_to_contact.get(email)
        if contact_id is None:
            stats["contacts_unknown"] += 1
            continue
        external_id = _external_id(
            row.brevo_campaign_id, email, recipients_type
        )
        payload = {
            "campaign_id": row.id,
            "campaign_brevo_id": row.brevo_campaign_id,
            "campaign_name": row.name,
            "recipient_email": email,
            "source_export_type": recipients_type,
            "source": "historical_backfill_export",
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
                body=None,
                metadata_json=json.dumps(payload, default=str),
                occurred_at=occurred_at,
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
