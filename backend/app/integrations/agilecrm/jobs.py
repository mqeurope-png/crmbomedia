"""AgileCRM worker operations.

Two handlers, both registered into `app.workers.jobs.OPERATIONS`:

- `agilecrm:sync_contacts` — full paginated import. Idempotent across
  re-runs: deduplicates by `(system, account_id, external_id)` and by
  contact email so a contact present in two AgileCRM accounts ends up
  as a single internal contact with two `external_references`.
- `agilecrm:purge_quota` — drives the per-account
  `quota_max_contacts` policy by deleting older/newer contacts on the
  remote until the account is back under the cap. Never deletes from
  the local CRM; only the remote row and a `external_status` flag on
  the matching `external_references` row.

Errors per contact are captured into `error_summary` (truncated at the
first 100 entries) without aborting the job — typical AgileCRM data has
the occasional broken row, and a single bad record shouldn't poison the
whole sync.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.integrations.agilecrm.client import AgileCRMClient
from app.integrations.agilecrm.mapper import (
    agilecrm_account_label,
    agilecrm_external_id,
    map_agilecrm_contact_to_internal,
    map_agilecrm_event_to_internal,
    map_agilecrm_note_to_internal,
    map_agilecrm_task_to_internal,
)
from app.integrations.errors import IntegrationError
from app.models.crm import (
    ActivityEvent,
    Contact,
    ExternalReference,
    ExternalSystem,
    Note,
    SyncLog,
    Task,
)
from app.models.integration_settings import IntegrationAccount, QuotaStrategy
from app.workers.jobs import OPERATIONS, SyncOutcome

logger = logging.getLogger(__name__)

#: How many per-record error messages to keep before truncating.
MAX_PER_RECORD_ERRORS = 100

#: Hard ceiling on the number of contacts processed per sync to keep
#: the runtime bounded; an account holding more contacts than this will
#: simply pick up where it left off on the next run because the import
#: is idempotent.
MAX_CONTACTS_PER_SYNC = 50_000

#: Cap on the number of in-flight sub-resource fetches per contact.
#: AgileCRM's Free tier sits around 200 req/h — 2 concurrent fetches
#: is more than enough to overlap network latency without giving the
#: rate limiter ammunition. The semaphore is per-contact so the worker
#: never has > MAX_SUBSYNC_CONCURRENCY HTTP calls in flight at once.
MAX_SUBSYNC_CONCURRENCY = 2

#: Default throttle target for the inter-contact pacing in
#: `sync_contacts`. Tunable via the `AGILECRM_REQUESTS_PER_SECOND` env
#: var. Each contact already costs 4 calls (contact + notes + tasks +
#: events) so the *real* outbound rate ends up roughly 4x this value.
#: AgileCRM's Free quota (200/h ≈ 0.06 req/s) is the bottleneck in
#: practice — the throttle just stops bursts from triggering 429s.
DEFAULT_REQUESTS_PER_SECOND = 5.0


def _inter_contact_sleep_seconds() -> float:
    """Read `AGILECRM_REQUESTS_PER_SECOND` from the environment and
    convert it into an inter-contact pacing delay. A value <= 0
    disables the pacing."""
    raw = os.environ.get("AGILECRM_REQUESTS_PER_SECOND")
    try:
        rps = float(raw) if raw is not None else DEFAULT_REQUESTS_PER_SECOND
    except ValueError:
        rps = DEFAULT_REQUESTS_PER_SECOND
    if rps <= 0:
        return 0.0
    return 1.0 / rps


def _load_account(session: Session, account_id: str) -> IntegrationAccount:
    """Reload the account row inside the worker session and verify it
    is configured. Raises `IntegrationError` if the account is missing
    or not ready for outbound calls."""
    account = session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.AGILECRM,
            IntegrationAccount.account_id == account_id,
        )
    )
    if account is None:
        raise IntegrationError(
            f"AgileCRM account '{account_id}' not found",
            system="agilecrm",
            account_id=account_id,
        )
    if not account.enabled:
        raise IntegrationError(
            f"AgileCRM account '{account_id}' is disabled",
            system="agilecrm",
            account_id=account_id,
        )
    if account.credential_status != "configured":
        raise IntegrationError(
            f"AgileCRM account '{account_id}' has credential_status='"
            f"{account.credential_status}', expected 'configured'",
            system="agilecrm",
            account_id=account_id,
        )
    return account


def _upsert_contact_for_payload(
    session: Session,
    *,
    account_id: str,
    payload: dict[str, Any],
) -> tuple[str, bool, str, str]:
    """Insert or update one internal contact for an AgileCRM payload.

    Returns `(action, was_consolidated, contact_id, external_id)` where
    `action ∈ {"created", "updated"}` and `was_consolidated` is True when
    the row was matched by email to an existing contact already linked
    from a different AgileCRM account. `contact_id` is the internal
    UUID, returned so the sub-sync helpers can attach notes/tasks/
    activities without having to re-query.
    """
    external_id = agilecrm_external_id(payload)
    if not external_id:
        raise ValueError("AgileCRM payload missing 'id'")

    record, ref_extras = map_agilecrm_contact_to_internal(payload)
    email = record.get("email") or ""
    if not email:
        raise ValueError("AgileCRM payload missing email")
    label = agilecrm_account_label(payload)
    # Strip the hint key so it never lands on the Contact ORM.
    record.pop("company_name", None)

    # 1. Existing reference for THIS account → update.
    ref = session.scalar(
        select(ExternalReference).where(
            ExternalReference.system == ExternalSystem.AGILECRM,
            ExternalReference.account_id == account_id,
            ExternalReference.external_id == external_id,
        )
    )
    if ref is not None:
        contact = session.get(Contact, ref.contact_id)
        if contact is not None:
            _apply_update(contact, record)
            if label and ref.account_label != label:
                ref.account_label = label
            if ref.external_status == "deleted_in_origin":
                # Remote brought it back — clear the marker.
                ref.external_status = None
            _apply_ref_extras(ref, ref_extras)
            session.flush()
            return ("updated", False, contact.id, external_id)

    # 2. No reference for this account, but the email already exists
    # somewhere → consolidate: link the existing contact under this
    # account too. This is the multi-account dedup story.
    existing_contact = session.scalar(
        select(Contact).where(func.lower(Contact.email) == email)
    )
    if existing_contact is not None:
        new_ref = ExternalReference(
            system=ExternalSystem.AGILECRM,
            account_id=account_id,
            external_id=external_id,
            account_label=label,
            contact_id=existing_contact.id,
        )
        _apply_ref_extras(new_ref, ref_extras)
        session.add(new_ref)
        # Refresh editable fields too (phone, tags) so the consolidated
        # contact picks up details that may have been entered in the
        # secondary AgileCRM account.
        _apply_update(existing_contact, record, allow_email_overwrite=False)
        session.flush()
        return ("updated", True, existing_contact.id, external_id)

    # 3. Brand-new contact + reference.
    contact = Contact(**record)
    session.add(contact)
    session.flush()
    new_ref = ExternalReference(
        system=ExternalSystem.AGILECRM,
        account_id=account_id,
        external_id=external_id,
        account_label=label,
        contact_id=contact.id,
    )
    _apply_ref_extras(new_ref, ref_extras)
    session.add(new_ref)
    session.flush()
    return ("created", False, contact.id, external_id)


def _apply_ref_extras(ref: ExternalReference, extras: dict[str, Any]) -> None:
    """Copy mapper output onto the external_references row. JSON-encodes
    `metadata` so it lands as text under the SQL column name `metadata`
    (`ExternalReference.metadata_json` is the Python attribute)."""
    if not extras:
        return
    external_created_at = extras.get("external_created_at")
    if external_created_at is not None:
        ref.external_created_at = external_created_at
    external_updated_at = extras.get("external_updated_at")
    if external_updated_at is not None:
        ref.external_updated_at = external_updated_at
    origin_detail = extras.get("origin_detail")
    if origin_detail:
        ref.origin_detail = origin_detail
    metadata = extras.get("metadata")
    if metadata:
        ref.metadata_json = json.dumps(metadata, default=str)


def _apply_update(
    contact: Contact,
    record: dict[str, Any],
    *,
    allow_email_overwrite: bool = True,
) -> None:
    for key, value in record.items():
        if value in (None, "") and key != "tags":
            continue
        if key == "email" and not allow_email_overwrite:
            continue
        setattr(contact, key, value)


# ---------------------------------------------------------------------------
# sync_contacts
# ---------------------------------------------------------------------------


async def _fetch_subresources(
    client: AgileCRMClient,
    agilecrm_contact_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fan-out the 3 per-contact sub-resource fetches behind an
    `asyncio.Semaphore` so we never have > MAX_SUBSYNC_CONCURRENCY
    HTTP calls in flight at once.

    Latency-wise this still pipelines 2 of the 3 calls; rate-limit-wise
    it stops the worker from bursting 3 simultaneous requests against
    an already-saturated tenant. A single bad sub-resource shouldn't
    abort the others, so failures are downgraded to "empty list" with
    a warning log."""

    async def _guarded(
        fetcher: Any, kind: str
    ) -> tuple[str, list[dict[str, Any]] | Exception]:
        async with semaphore:
            try:
                result = await fetcher(agilecrm_contact_id)
            except Exception as exc:  # noqa: BLE001 - keep sibling fetches alive
                return kind, exc
            return kind, result

    pairs = await asyncio.gather(
        _guarded(client.list_contact_notes, "notes"),
        _guarded(client.list_contact_tasks, "tasks"),
        _guarded(client.list_contact_events, "events"),
    )

    by_kind: dict[str, list[dict[str, Any]]] = {"notes": [], "tasks": [], "events": []}
    for kind, result in pairs:
        if isinstance(result, Exception):
            logger.warning(
                "AgileCRM %s fetch failed for contact_id=%s: %s",
                kind,
                agilecrm_contact_id,
                result,
            )
            continue
        if isinstance(result, list):
            by_kind[kind] = result

    return by_kind["notes"], by_kind["tasks"], by_kind["events"]


def _sync_contact_notes(
    session: Session,
    *,
    contact_id: str,
    account_id: str,
    payloads: list[dict[str, Any]],
) -> int:
    written = 0
    for payload in payloads:
        record = map_agilecrm_note_to_internal(
            payload, contact_id=contact_id, account_id=account_id
        )
        if record is None:
            continue
        existing = None
        if record["external_id"]:
            existing = session.scalar(
                select(Note).where(
                    Note.external_system == record["external_system"],
                    Note.external_account_id == record["external_account_id"],
                    Note.external_id == record["external_id"],
                )
            )
        if existing is not None:
            for field, value in record.items():
                setattr(existing, field, value)
        else:
            session.add(Note(**record))
        written += 1
    if written:
        session.flush()
    return written


def _sync_contact_tasks(
    session: Session,
    *,
    contact_id: str,
    account_id: str,
    payloads: list[dict[str, Any]],
) -> int:
    written = 0
    for payload in payloads:
        record = map_agilecrm_task_to_internal(
            payload, contact_id=contact_id, account_id=account_id
        )
        if record is None:
            continue
        existing = None
        if record["external_id"]:
            existing = session.scalar(
                select(Task).where(
                    Task.external_system == record["external_system"],
                    Task.external_account_id == record["external_account_id"],
                    Task.external_id == record["external_id"],
                )
            )
        if existing is not None:
            for field, value in record.items():
                setattr(existing, field, value)
        else:
            session.add(Task(**record))
        written += 1
    if written:
        session.flush()
    return written


def _sync_contact_events(
    session: Session,
    *,
    contact_id: str,
    account_id: str,
    payloads: list[dict[str, Any]],
) -> int:
    """Upsert AgileCRM timeline events into `activity_events`. The table
    name keeps its original spelling so this PR doesn't ship a no-op
    rename migration; the worker just talks about "events" everywhere
    else to match AgileCRM's own `/contacts/{id}/events` path."""
    written = 0
    for payload in payloads:
        record = map_agilecrm_event_to_internal(
            payload, contact_id=contact_id, account_id=account_id
        )
        if record is None:
            continue
        existing = None
        if record["external_id"]:
            existing = session.scalar(
                select(ActivityEvent).where(
                    ActivityEvent.system == record["system"],
                    ActivityEvent.account_id == record["account_id"],
                    ActivityEvent.external_id == record["external_id"],
                )
            )
        if existing is not None:
            for field, value in record.items():
                setattr(existing, field, value)
        else:
            session.add(ActivityEvent(**record))
        written += 1
    if written:
        session.flush()
    return written


def sync_agilecrm_contacts(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker handler: iterate every AgileCRM contact and upsert it.

    **Sprint A PR-8** — the per-contact notes/tasks/events fetch was
    moved out of this loop to the on-demand refresh endpoint
    (`POST /api/contacts/{id}/refresh-external-data`). The bulk sync
    used to issue 4 outbound calls per contact (contact + 3 sub-
    resources); a tenant with 700+ contacts blew through the Free
    tier's daily quota in minutes. Now the bulk job only paginates
    contacts — ~30 calls for the whole import — and the operator
    pulls the enriched data on demand from the detail screen.

    `_sync_contact_notes` / `_sync_contact_tasks` /
    `_sync_contact_events` / `_fetch_subresources` survive as
    internal helpers used by the on-demand refresh module; their
    signatures are kept stable so a future scheduled "warm the cache
    for VIP contacts" job can reuse them.

    Between contacts we keep `1 / AGILECRM_REQUESTS_PER_SECOND`
    seconds of pacing as a thin safety net in case AgileCRM tightens
    its list-contacts quota; the IntegrationHTTPClient still honours
    `Retry-After` automatically."""
    account_id = sync_log.account_id or ""
    account = _load_account(session, account_id)

    processed = 0
    created = 0
    updated = 0
    consolidated = 0
    skipped = 0
    failed = 0
    error_lines: list[str] = []
    inter_contact_sleep = _inter_contact_sleep_seconds()

    async def _drive() -> None:
        nonlocal processed, created, updated, consolidated, skipped, failed
        async with AgileCRMClient(session, account_id) as client:
            cursor: str | None = None
            while processed < MAX_CONTACTS_PER_SYNC:
                items, cursor = await client.list_contacts(cursor=cursor)
                if not items:
                    break
                for payload in items:
                    try:
                        action, was_consolidated, _internal_id, _ext_id = (
                            _upsert_contact_for_payload(
                                session, account_id=account_id, payload=payload
                            )
                        )
                        if action == "created":
                            created += 1
                        elif action == "updated":
                            updated += 1
                            if was_consolidated:
                                consolidated += 1
                            else:
                                # An update on an already-linked contact
                                # isn't progress as far as records go,
                                # but it isn't an error either; we count
                                # it under skipped to differentiate.
                                skipped += 1
                    except Exception as exc:  # noqa: BLE001 - never abort the whole sync
                        failed += 1
                        if len(error_lines) < MAX_PER_RECORD_ERRORS:
                            ext_payload_id = (
                                payload.get("id") if isinstance(payload, dict) else "?"
                            )
                            error_lines.append(
                                f"contact_id={ext_payload_id}: {exc!s}"
                            )
                        session.rollback()
                    processed += 1
                    if inter_contact_sleep > 0:
                        await asyncio.sleep(inter_contact_sleep)
                # Commit per page so a later failure doesn't lose the
                # earlier pages' work.
                session.commit()
                if cursor is None:
                    break

    asyncio.run(_drive())

    error_summary: str | None = None
    if error_lines:
        truncated = error_lines[:MAX_PER_RECORD_ERRORS]
        suffix = (
            f"\n…and {failed - len(truncated)} more failures truncated."
            if failed > len(truncated)
            else ""
        )
        error_summary = "\n".join(truncated) + suffix

    metadata: dict[str, Any] = {
        "system": "agilecrm",
        "account_id": account_id,
        "created": created,
        "updated_existing": updated - consolidated,
        "consolidated_from_other_account": consolidated,
        "failed": failed,
        # Bulk sub-resource sync was retired in Sprint A PR-8 in favour
        # of the on-demand refresh endpoint. The keys are kept at zero
        # so dashboards / log parsers that grep for them continue to
        # work without a config change.
        "notes_synced": 0,
        "tasks_synced": 0,
        "events_synced": 0,
    }

    # After a successful import, enqueue the quota purge automatically
    # when the account has a quota policy. We commit first so the
    # in-flight handler row is visible to the next job.
    session.commit()
    if account.quota_max_contacts and account.quota_strategy in (
        QuotaStrategy.KEEP_NEWEST,
        QuotaStrategy.KEEP_OLDEST,
    ):
        from app.models.crm import SyncTrigger
        from app.workers.jobs import enqueue_sync_job

        try:
            enqueue_sync_job(
                session,
                system="agilecrm",
                account_id=account_id,
                operation="purge_quota",
                triggered_by=SyncTrigger.CRON,
            )
            metadata["purge_quota_enqueued"] = True
        except Exception as exc:  # noqa: BLE001 - never break the parent job
            logger.warning(
                "Failed to enqueue purge_quota for agilecrm/%s: %s", account_id, exc
            )
            metadata["purge_quota_enqueued"] = False
            metadata["purge_quota_enqueue_error"] = str(exc)

    return SyncOutcome(
        records_processed=processed,
        records_skipped=skipped,
        records_failed=failed,
        error_summary=error_summary,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# purge_quota
# ---------------------------------------------------------------------------


def purge_agilecrm_quota(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker handler: enforce `quota_max_contacts` on the remote.

    `keep_newest` deletes the oldest first; `keep_oldest` deletes the
    newest first. Local contacts are never touched — only the
    AgileCRM-side row and a `external_status='deleted_in_origin'`
    marker on the matching `external_references` row.
    """
    account_id = sync_log.account_id or ""
    account = _load_account(session, account_id)
    if not account.quota_max_contacts or account.quota_max_contacts <= 0:
        return SyncOutcome(
            records_processed=0,
            metadata={
                "system": "agilecrm",
                "account_id": account_id,
                "skip_reason": "no_quota_set",
            },
        )
    strategy = account.quota_strategy or QuotaStrategy.NONE
    if strategy == QuotaStrategy.NONE:
        return SyncOutcome(
            records_processed=0,
            metadata={
                "system": "agilecrm",
                "account_id": account_id,
                "skip_reason": "quota_strategy_none",
            },
        )

    deleted = 0
    failed = 0
    error_lines: list[str] = []
    total_remote: int | None = 0
    count_unavailable = False

    async def _drive() -> None:
        nonlocal deleted, failed, total_remote, count_unavailable
        async with AgileCRMClient(session, account_id) as client:
            total_remote = await client.count_contacts()
            # AgileCRM's count endpoint is not always reachable. When
            # it isn't we can't decide how many contacts to purge —
            # silently skipping is safer than deleting the wrong subset.
            if total_remote is None:
                count_unavailable = True
                logger.warning(
                    "AgileCRM count_contacts unavailable for account=%s; "
                    "purge_quota skipped this run",
                    account_id,
                )
                return
            assert account.quota_max_contacts is not None
            to_delete = max(0, total_remote - account.quota_max_contacts)
            if to_delete == 0:
                return
            # `created_time` ASC ⇒ oldest first (= keep_newest delete order)
            # AgileCRM accepts `-created_time` for desc; if the param is
            # ignored the loop still terminates because we only delete
            # `to_delete` rows.
            order_by = "created_time" if strategy == QuotaStrategy.KEEP_NEWEST else "-created_time"
            cursor: str | None = None
            remaining = to_delete
            while remaining > 0:
                items, cursor = await client.list_contacts(
                    cursor=cursor, order_by=order_by
                )
                if not items:
                    break
                for payload in items:
                    if remaining <= 0:
                        break
                    ext_id = agilecrm_external_id(payload)
                    if not ext_id:
                        continue
                    try:
                        await client.delete_contact(ext_id)
                        _mark_reference_deleted(session, account_id, ext_id)
                        record_event(
                            session,
                            action=Action.INTEGRATION_QUOTA_DELETED,
                            target_type="integration_account",
                            target_id=account.id,
                            metadata={
                                "system": "agilecrm",
                                "account_id": account_id,
                                "external_id": ext_id,
                                "reason": "quota",
                                "strategy": strategy.value,
                            },
                        )
                        session.commit()
                        deleted += 1
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        if len(error_lines) < MAX_PER_RECORD_ERRORS:
                            error_lines.append(f"external_id={ext_id}: {exc!s}")
                        session.rollback()
                    remaining -= 1
                if cursor is None:
                    break

    asyncio.run(_drive())

    error_summary: str | None = None
    if error_lines:
        error_summary = "\n".join(error_lines)
    if count_unavailable and not error_summary:
        error_summary = (
            "AgileCRM count endpoint unavailable for this account; purge "
            "skipped without touching the remote dataset."
        )

    return SyncOutcome(
        records_processed=deleted,
        records_failed=failed,
        error_summary=error_summary,
        metadata={
            "system": "agilecrm",
            "account_id": account_id,
            "strategy": strategy.value,
            "quota_max_contacts": account.quota_max_contacts,
            "remote_total_before": total_remote,
            "deleted": deleted,
            "failed": failed,
            "skip_reason": "count_unavailable" if count_unavailable else None,
        },
    )


def _mark_reference_deleted(session: Session, account_id: str, external_id: str) -> None:
    ref = session.scalar(
        select(ExternalReference).where(
            ExternalReference.system == ExternalSystem.AGILECRM,
            ExternalReference.account_id == account_id,
            ExternalReference.external_id == external_id,
        )
    )
    if ref is not None:
        ref.external_status = "deleted_in_origin"


# ---------------------------------------------------------------------------
# Registration with the worker registry
# ---------------------------------------------------------------------------

OPERATIONS["agilecrm:sync_contacts"] = sync_agilecrm_contacts
OPERATIONS["agilecrm:purge_quota"] = purge_agilecrm_quota
