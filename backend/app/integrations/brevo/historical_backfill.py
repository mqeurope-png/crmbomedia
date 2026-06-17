"""Materialise past Brevo campaign events into `activity_events`.

The live webhook only fires from the moment Bart sets it up. Every
campaign sent before that day has no granular trail in the CRM — the
campaign detail page shows aggregated stats but the contact page's
"Actividad email" section stays empty for pre-webhook deliveries.
Brevo's only supported way to read per-recipient history on past
campaigns is an asynchronous **export job**:

  POST /emailCampaigns/{id}/exportRecipients  body={recipientsType}
       → {"processId": N}
  GET  /processes/{N}     → {"status": "...", "exportUrl?": "..."}
  GET  exportUrl  (signed)  → semicolon-delimited CSV

**Production lesson that forced this rewrite (v3)**: the
`recipientsType` filter on the export request is effectively
cosmetic — the CSV ALWAYS contains every recipient of the campaign,
whatever bucket you ask for. The v2 flow (PR #55) requested 5
exports per campaign (openers, clickers, …) and inserted one event
per CSV row per bucket, so every recipient ended up with 5 fake
events (a contact who merely received the campaign got marked as
unsubscribed + hard-bounced + soft-bounced). 76k+ contaminated rows
had to be wiped from production.

The correct contract: request ONE export per campaign with
`recipientsType="all"` and derive the events from the CSV columns —
a recipient opened iff `Open_Date` carries a date, bounced iff
`Hard_Bounce_Date`/`Soft_Bounce_Date` does, clicked iff
`Clicked_Links_Count > 0`, and so on. Bonus: the columns carry REAL
per-event timestamps (`DD-MM-YYYY HH:MM:SS`), so `occurred_at` is no
longer approximated with the campaign's `sent_at`.

Idempotency rides on the `activity_events` UNIQUE constraint
`(system, account_id, external_id)`:

    backfill:{brevo_campaign_id}:{email_normalised}:{event_type}

A second run hits the same key → IntegrityError inside a SAVEPOINT →
counted as `events_skipped_existing`. Re-running after an
interruption is safe.

This module is meant to be run **once per installation**: after the
backfill finishes, the live webhook covers everything going forward.
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

#: The one bucket we request. Brevo returns every recipient in the
#: CSV regardless of `recipientsType`, so asking for anything more
#: specific only wastes export quota (and misled v2 into the
#: contamination bug described in the module docstring).
EXPORT_RECIPIENTS_TYPE = "all"

#: CSV date-column → internal event. A row produces the event iff the
#: column holds a parseable timestamp. Column names are verbatim from
#: Brevo's export header (note `Complaint_date` with a lowercase d —
#: that's Brevo, not a typo).
CSV_COLUMN_TO_EVENT: dict[str, str] = {
    "Delivered_Date": "email.delivered",
    "Open_Date": "email.opened",
    "Unsubscribe_Date": "email.unsubscribed",
    "Hard_Bounce_Date": "email.bounced_hard",
    "Soft_Bounce_Date": "email.bounced_soft",
    "Complaint_date": "email.spam_complaint",
}

#: `email.clicked` is special: there's no `Click_Date` column, only a
#: `Clicked_Links_Count` counter (plus one dynamic column per
#: clickable link, which we ignore). Count > 0 → clicked.
CLICKS_COUNT_COLUMN = "Clicked_Links_Count"

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
#: stricter than the 400 req/min on contacts. With one export per
#: campaign we only fire 1-2 API calls per polling tick; a 1 s gap
#: between calls keeps the bucket healthy.
_INTER_CALL_SLEEP_SECONDS = 1.0

#: Commit the session every N CSV rows so a 18k-recipient campaign
#: doesn't accumulate one giant transaction (and a crash mid-campaign
#: keeps the rows already processed).
_COMMIT_BATCH_ROWS = 500


def _normalise_email(value: Any) -> str | None:
    if not value:
        return None
    return str(value).strip().lower() or None


def _parse_brevo_csv_date(value: str | None) -> datetime | None:
    """Parse Brevo's export timestamp (`DD-MM-YYYY HH:MM:SS`,
    European day-first order). Returns None on empty or malformed
    input — an empty cell means "this event didn't happen for this
    recipient", which is load-bearing for the column-based detection.
    Brevo doesn't expose a timezone; we assume UTC."""
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%m-%Y %H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def _parse_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _fallback_occurred_at(fallback: datetime | None) -> datetime:
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
    limit; in practice campaign ids + emails stay well below."""
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


def _parse_export_rows(csv_bytes: bytes) -> list[dict[str, str]]:
    """Decode a Brevo `all`-recipients CSV → list of raw row dicts.

    Production lessons baked in: semicolon delimiter (NOT comma — the
    excel dialect parsed each row into a single mega-column), the
    address lives under `Email_ID`, and the file opens with a UTF-8
    BOM (`utf-8-sig` strips it). Dynamic per-link columns ride along
    in each dict but are never read — only the known column names in
    `CSV_COLUMN_TO_EVENT` + `CLICKS_COUNT_COLUMN` matter."""
    if not csv_bytes:
        return []
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [row for row in reader if row]


def _extract_events_from_row(
    row: dict[str, str],
    *,
    fallback_dt: datetime | None,
) -> list[tuple[str, datetime, dict[str, Any]]]:
    """Derive the events one CSV row encodes.

    Returns `[(event_type, occurred_at, extra_metadata), ...]` — zero
    entries when nothing happened to this recipient (rare: a row with
    no delivered date and no bounce), several when multiple things
    did (delivered + opened + clicked is the common engaged case).

    Detection is column-based: a date column with a parseable value
    means the event happened AT that timestamp. `email.clicked` has
    no date column — `Clicked_Links_Count > 0` flags it and the open
    timestamp doubles as the best approximation (a click implies an
    open right before it), degrading to the delivery, send, or the
    campaign-level fallback.

    `Total Opens` can be 0 even with `Open_Date` set (Brevo is
    occasionally inconsistent); the date column is the source of
    truth, the counter only travels as metadata."""
    events: list[tuple[str, datetime, dict[str, Any]]] = []
    parsed_dates: dict[str, datetime] = {}
    for column, event_type in CSV_COLUMN_TO_EVENT.items():
        occurred_at = _parse_brevo_csv_date(row.get(column))
        if occurred_at is None:
            continue
        parsed_dates[column] = occurred_at
        extra: dict[str, Any] = {}
        if column == "Open_Date":
            extra["total_opens"] = _parse_int(row.get("Total Opens"))
        events.append((event_type, occurred_at, extra))

    clicks = _parse_int(row.get(CLICKS_COUNT_COLUMN))
    if clicks > 0:
        occurred_at = (
            parsed_dates.get("Open_Date")
            or parsed_dates.get("Delivered_Date")
            or _parse_brevo_csv_date(row.get("Send_Date"))
            or _fallback_occurred_at(fallback_dt)
        )
        events.append(
            ("email.clicked", occurred_at, {"clicked_links_count": clicks})
        )
    return events


async def _fetch_campaign_export(
    client: BrevoClient,
    campaign_id: int,
    *,
    timeout_seconds: float = _EXPORT_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, str]], str | None]:
    """Run ONE `recipientsType=all` export for a campaign: start →
    poll → download → parse.

    Returns `(rows, error)`. On success `error` is None. A non-fatal
    Brevo response (export endpoint 4xx, process aborted, signed URL
    404) returns an empty list + a human-readable error so the caller
    can skip the campaign and keep the rest of the run alive."""
    try:
        process_id = await client.start_recipients_export(
            campaign_id, EXPORT_RECIPIENTS_TYPE
        )
    except IntegrationClientError as exc:
        logger.warning(
            "brevo.backfill export %s status=%s — skipping campaign",
            campaign_id,
            exc.status_code,
        )
        return [], f"start_export status={exc.status_code}"

    try:
        body = await _wait_for_export(
            client, process_id, timeout_seconds=timeout_seconds
        )
    except TimeoutError as exc:
        logger.warning(
            "brevo.backfill export %s timed out (process=%s)",
            campaign_id,
            process_id,
        )
        return [], str(exc)

    status = str(body.get("status") or "").lower()
    if status != "completed":
        logger.warning(
            "brevo.backfill export %s ended status=%s — skipping campaign",
            campaign_id,
            status,
        )
        return [], f"process status={status!r}"

    export_url = body.get("exportUrl") or body.get("export_url")
    if not export_url:
        return [], "completed without exportUrl"

    try:
        csv_bytes = await client.download_csv_export(str(export_url))
    except IntegrationError as exc:
        logger.warning(
            "brevo.backfill download %s failed: %s",
            campaign_id,
            exc.message,
        )
        return [], f"download {exc.message}"

    return _parse_export_rows(csv_bytes), None


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def backfill_campaign_events(
    session: Session,
    *,
    account_id: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Back-fill one cached campaign from a single `all` export.

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
        "rows_without_email": 0,
        "errors": [],
    }

    async def _drive() -> list[dict[str, str]]:
        async with BrevoClient(session, account_id) as client:
            rows, fetch_error = await _fetch_campaign_export(
                client, row.brevo_campaign_id
            )
            if fetch_error:
                stats["errors"].append(fetch_error)
            # Pace before the next campaign's start_export call lands
            # on the rate-limit bucket.
            await asyncio.sleep(_INTER_CALL_SLEEP_SECONDS)
            return rows

    try:
        csv_rows = asyncio.run(_drive())
    except IntegrationError as exc:
        stats["errors"].append(exc.message)
        return stats

    if csv_rows:
        _materialise_rows(
            session,
            account_id=account_id,
            row=row,
            csv_rows=csv_rows,
            stats=stats,
        )
    return stats


def _materialise_rows(
    session: Session,
    *,
    account_id: str,
    row: BrevoCampaignCache,
    csv_rows: list[dict[str, str]],
    stats: dict[str, Any],
) -> None:
    """Resolve emails → CRM contacts in one batch, then walk the CSV
    deriving 0-N events per row. Each insert lives in its own
    SAVEPOINT so the UNIQUE-key collision on duplicates doesn't
    poison the surrounding transaction; the session commits every
    `_COMMIT_BATCH_ROWS` rows so big campaigns don't build one giant
    transaction."""
    sent_at_fallback = row.sent_at or row.created_at_brevo
    normalised: list[tuple[dict[str, str], str]] = []
    for csv_row in csv_rows:
        email = _normalise_email(csv_row.get("Email_ID"))
        if not email:
            stats["rows_without_email"] += 1
            continue
        normalised.append((csv_row, email))
    if stats["rows_without_email"]:
        logger.warning(
            "brevo.backfill campaign=%s rows_without_email=%d",
            row.brevo_campaign_id,
            stats["rows_without_email"],
        )
    if not normalised:
        return

    email_to_contact = _resolve_emails_to_contact_ids(
        session, list({email for _, email in normalised})
    )
    processed = 0
    for csv_row, email in normalised:
        contact_id = email_to_contact.get(email)
        if contact_id is None:
            stats["contacts_unknown"] += 1
            continue
        for event_type, occurred_at, extra in _extract_events_from_row(
            csv_row, fallback_dt=sent_at_fallback
        ):
            payload = {
                "campaign_id": row.id,
                "campaign_brevo_id": row.brevo_campaign_id,
                "campaign_name": row.name,
                "recipient_email": email,
                "source": "historical_backfill_export",
                **extra,
            }
            savepoint = session.begin_nested()
            session.add(
                ActivityEvent(
                    contact_id=contact_id,
                    system="brevo",
                    account_id=account_id,
                    external_id=_external_id(
                        row.brevo_campaign_id, email, event_type
                    ),
                    event_type=event_type,
                    campaign_brevo_id=row.brevo_campaign_id,
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
        processed += 1
        if processed % _COMMIT_BATCH_ROWS == 0:
            session.commit()


def backfill_account_campaigns(
    session: Session,
    *,
    account_id: str,
    max_campaigns: int | None = None,
    campaign_brevo_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Iterate every sent/archived campaign in the local cache (most
    recent first) and call `backfill_campaign_events` — one export
    per campaign, serially (Brevo runs the export queue per account;
    parallel processes only add latency).

    Operates on the CACHE only — campaigns missing from
    `brevo_campaigns_cache` aren't fetched here (that's
    `refresh_campaigns_cache`'s job). Bart's runbook is: refresh
    first, backfill second.

    `campaign_brevo_ids` (opt): cuando se pasa, sólo procesamos las
    campañas con `brevo_campaign_id IN (…)` — usado por el botón
    "Sincronizar destinatarios" para tirar de UNA campaña suelta sin
    re-procesar las 60 anteriores."""
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
    if campaign_brevo_ids:
        statement = statement.where(
            BrevoCampaignCache.brevo_campaign_id.in_(campaign_brevo_ids)
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
            aggregate["errors"].extend(
                f"campaign={cached.brevo_campaign_id}: {err}"
                for err in result["errors"]
            )
        aggregate["per_campaign"].append(result)
        session.commit()
    return aggregate


# ---------------------------------------------------------------------------
# Worker handler
# ---------------------------------------------------------------------------


def run_historical_backfill(
    session: Session, sync_log: SyncLog
) -> SyncOutcome:
    """`brevo:historical_backfill` worker entry. Payload puede llevar:

    - `max_campaigns` (int) — bound del run completo.
    - `campaign_brevo_ids` (list[int]) — sólo procesar esas campañas
      (botón "Sincronizar destinatarios" / auto-disparo desde refresh).
    """
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

    raw_ids = payload.get("campaign_brevo_ids")
    campaign_brevo_ids: list[int] | None = None
    if isinstance(raw_ids, list) and raw_ids:
        campaign_brevo_ids = []
        for item in raw_ids:
            try:
                campaign_brevo_ids.append(int(item))
            except (TypeError, ValueError):
                continue
        if not campaign_brevo_ids:
            campaign_brevo_ids = None

    try:
        stats = backfill_account_campaigns(
            session,
            account_id=account_id,
            max_campaigns=max_campaigns,
            campaign_brevo_ids=campaign_brevo_ids,
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
