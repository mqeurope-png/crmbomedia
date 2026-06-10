"""Brevo write engine — push selected CRM contacts to a Brevo list.

A `BrevoSyncTarget` says: "contacts matching segment S go to Brevo
list L of account A". Running a target:

1. Evaluates the segment (Sprint P.3 engine, reused untouched).
2. Creates/updates each matching contact in Brevo (duplicate 400 →
   update fallback).
3. Adds the batch to the configured list (100 emails per call).
4. Diffs against `brevo_target_memberships` from the previous run:
   contacts that left the segment get REMOVED FROM THE LIST (never
   deleted from Brevo) and their membership row dropped.

Two handlers registered into `OPERATIONS`:

- `brevo:push_target` — run one target (payload: {"target_id": ...}).
- `brevo:auto_sync_check` — the 5-minute heartbeat that enqueues due
  targets and re-schedules itself (requires the RQ worker to run with
  `--with-scheduler`).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.integrations.brevo.client import LIST_BATCH_SIZE, BrevoClient
from app.integrations.brevo.mapper import map_internal_contact_to_brevo
from app.integrations.errors import IntegrationDuplicateError, IntegrationError
from app.models.brevo import (
    BrevoSyncTarget,
    BrevoTargetMembership,
    TargetRunStatus,
)
from app.models.crm import Contact, Segment, SyncLog
from app.repositories import segments as segments_repository
from app.services.segments.engine import SegmentRuleError, build_filter
from app.workers.jobs import OPERATIONS, SyncOutcome
from app.workers.queues import queue_name, redis_connection

logger = logging.getLogger(__name__)

TARGET_LOCK_TTL_SECONDS = 1800
HEARTBEAT_INTERVAL_SECONDS = 300  # 5 min
HEARTBEAT_LOCK_KEY = "brevo:auto_sync_heartbeat"


# ---------------------------------------------------------------------------
# segment resolution
# ---------------------------------------------------------------------------


def resolve_target_contacts(
    session: Session, target: BrevoSyncTarget
) -> list[Contact]:
    """All contacts currently matching the target's segment. Contacts
    without a usable email are dropped here — Brevo upserts by email."""
    segment = session.get(Segment, target.segment_id)
    if segment is None:
        raise ValueError(f"Segment {target.segment_id!r} no longer exists")
    if not segment.is_dynamic:
        ids = segments_repository.decode_static_ids(segment)
        rows = (
            list(session.scalars(select(Contact).where(Contact.id.in_(ids))))
            if ids
            else []
        )
    else:
        rules = segments_repository.decode_rules(segment)
        try:
            condition = build_filter(rules)
        except SegmentRuleError as exc:
            raise ValueError(f"Segment rules invalid: {exc}") from exc
        rows = list(session.scalars(select(Contact).where(condition)))
    return [c for c in rows if c.email and c.is_active]


# ---------------------------------------------------------------------------
# core run
# ---------------------------------------------------------------------------


def run_brevo_target(
    session: Session,
    target: BrevoSyncTarget,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute one push. Returns the stats dict that lands in
    `last_run_stats_json` (and in the dry-run preview API)."""
    contacts = resolve_target_contacts(session, target)
    current_ids = {c.id for c in contacts}

    previous_rows = list(
        session.scalars(
            select(BrevoTargetMembership).where(
                BrevoTargetMembership.target_id == target.id
            )
        )
    )
    previous_ids = {row.contact_id for row in previous_rows}
    departed_ids = previous_ids - current_ids
    departed_contacts = (
        list(session.scalars(select(Contact).where(Contact.id.in_(departed_ids))))
        if departed_ids
        else []
    )
    departed_emails = [c.email for c in departed_contacts if c.email]

    stats: dict[str, Any] = {
        "matched": len(contacts),
        "pushed_new": 0,
        "pushed_updated": 0,
        "added_to_list": 0,
        "removed_from_list": 0,
        "errors": 0,
        "error_lines": [],
        "dry_run": dry_run,
    }

    if dry_run:
        stats["would_push"] = [c.email for c in contacts][:50]
        stats["would_remove_from_list"] = departed_emails[:50]
        stats["removed_from_list"] = len(departed_emails)
        return stats

    async def _drive() -> None:
        async with BrevoClient(session, target.brevo_account_id) as client:
            pushed_emails: list[str] = []
            for contact in contacts:
                payload = map_internal_contact_to_brevo(contact)
                try:
                    try:
                        await client.create_contact(payload)
                        stats["pushed_new"] += 1
                    except IntegrationDuplicateError:
                        await client.update_contact(
                            contact.email, {"attributes": payload["attributes"]}
                        )
                        stats["pushed_updated"] += 1
                    pushed_emails.append(contact.email)
                except IntegrationError as exc:
                    stats["errors"] += 1
                    if len(stats["error_lines"]) < 50:
                        stats["error_lines"].append(
                            f"{contact.email}: {exc.message}"
                        )

            if target.brevo_list_id and pushed_emails:
                list_id = int(target.brevo_list_id)
                for i in range(0, len(pushed_emails), LIST_BATCH_SIZE):
                    batch = pushed_emails[i : i + LIST_BATCH_SIZE]
                    try:
                        await client.add_contacts_to_list(list_id, batch)
                        stats["added_to_list"] += len(batch)
                    except IntegrationError as exc:
                        stats["errors"] += 1
                        if len(stats["error_lines"]) < 50:
                            stats["error_lines"].append(
                                f"add_to_list batch {i}: {exc.message}"
                            )

            if target.brevo_list_id and departed_emails:
                list_id = int(target.brevo_list_id)
                for i in range(0, len(departed_emails), LIST_BATCH_SIZE):
                    batch = departed_emails[i : i + LIST_BATCH_SIZE]
                    try:
                        await client.remove_contacts_from_list(list_id, batch)
                        stats["removed_from_list"] += len(batch)
                    except IntegrationError as exc:
                        stats["errors"] += 1
                        if len(stats["error_lines"]) < 50:
                            stats["error_lines"].append(
                                f"remove_from_list batch {i}: {exc.message}"
                            )

    asyncio.run(_drive())

    # Reconcile the membership ledger to the *current* segment state.
    now = datetime.now(UTC)
    for row in previous_rows:
        if row.contact_id in departed_ids:
            session.delete(row)
    for contact in contacts:
        if contact.id not in previous_ids:
            session.add(
                BrevoTargetMembership(
                    target_id=target.id, contact_id=contact.id, added_at=now
                )
            )
    session.flush()
    return stats


# ---------------------------------------------------------------------------
# worker handlers
# ---------------------------------------------------------------------------


def _target_lock(target_id: str) -> str:
    return f"brevo:push_target:{target_id}"


def push_brevo_target(session: Session, sync_log: SyncLog) -> SyncOutcome:
    payload: dict[str, Any] = {}
    if sync_log.metadata_json:
        try:
            decoded = json.loads(sync_log.metadata_json)
            payload = decoded.get("payload") or decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            payload = {}
    target_id = str(payload.get("target_id") or "")
    target = session.get(BrevoSyncTarget, target_id) if target_id else None
    if target is None:
        return SyncOutcome(
            records_failed=1,
            error_summary=f"Sync target {target_id!r} not found",
        )

    conn = redis_connection()
    lock = _target_lock(target.id)
    if not conn.set(lock, "1", nx=True, ex=TARGET_LOCK_TTL_SECONDS):
        return SyncOutcome(
            records_failed=1,
            error_summary="Este target ya tiene una ejecución en curso.",
        )

    target.last_run_status = TargetRunStatus.RUNNING
    session.commit()
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_STARTED,
        target_type="brevo_sync_target",
        target_id=target.id,
        metadata={"name": target.name, "segment_id": target.segment_id},
    )

    try:
        stats = run_brevo_target(session, target)
    except Exception as exc:  # noqa: BLE001 - surface as failed run
        target.last_run_status = TargetRunStatus.ERROR
        target.last_run_at = datetime.now(UTC)
        target.last_run_stats_json = json.dumps({"fatal": str(exc)})
        session.commit()
        return SyncOutcome(records_failed=1, error_summary=str(exc))
    finally:
        try:
            conn.delete(lock)
        except Exception:  # noqa: BLE001 - TTL expiry is the fallback
            logger.warning("brevo.target lock release failed for %s", lock)

    target.last_run_at = datetime.now(UTC)
    target.last_run_status = (
        TargetRunStatus.SUCCESS
        if not stats["errors"]
        else TargetRunStatus.PARTIAL_ERROR
        if stats["pushed_new"] + stats["pushed_updated"]
        else TargetRunStatus.ERROR
    )
    target.last_run_stats_json = json.dumps(stats, default=str)
    session.commit()

    return SyncOutcome(
        records_processed=stats["pushed_new"] + stats["pushed_updated"],
        records_failed=stats["errors"],
        error_summary=(
            "\n".join(stats["error_lines"]) if stats["error_lines"] else None
        ),
        metadata=stats,
    )


def brevo_auto_sync_check(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Heartbeat: enqueue every active, auto-sync target whose
    interval has elapsed, then re-schedule itself in 5 minutes."""
    _ = sync_log
    now = datetime.now(UTC)
    targets = list(
        session.scalars(
            select(BrevoSyncTarget).where(
                BrevoSyncTarget.is_active.is_(True),
                BrevoSyncTarget.auto_sync_enabled.is_(True),
            )
        )
    )
    due: list[BrevoSyncTarget] = []
    for target in targets:
        last = target.last_run_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or last + timedelta(
            minutes=target.sync_interval_minutes
        ) <= now:
            due.append(target)

    enqueued = 0
    from app.workers.jobs import enqueue_sync_job  # noqa: PLC0415

    for target in due:
        try:
            enqueue_sync_job(
                session,
                system="brevo",
                account_id=target.brevo_account_id,
                operation="push_target",
                triggered_by="cron",
                payload={"target_id": target.id},
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 - keep siblings alive
            logger.warning(
                "brevo.auto_sync enqueue failed target=%s: %s", target.id, exc
            )

    schedule_heartbeat()
    return SyncOutcome(
        records_processed=enqueued,
        metadata={"due": len(due), "checked": len(targets)},
    )


def schedule_heartbeat() -> None:
    """Idempotently arm the next heartbeat. Uses a Redis SETNX with a
    TTL slightly shorter than the interval so at most one heartbeat
    is in flight; the RQ worker must run `--with-scheduler` for
    `enqueue_in` to fire."""
    conn = redis_connection()
    if not conn.set(
        HEARTBEAT_LOCK_KEY, "1", nx=True, ex=HEARTBEAT_INTERVAL_SECONDS - 10
    ):
        return
    try:
        from rq import Queue  # noqa: PLC0415

        queue = Queue(
            queue_name("brevo", "auto_sync_check"), connection=conn
        )
        queue.enqueue_in(
            timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS),
            run_heartbeat_job,
        )
    except Exception as exc:  # noqa: BLE001 - heartbeat re-arms on next API touch
        logger.warning("brevo.heartbeat scheduling failed: %s", exc)
        conn.delete(HEARTBEAT_LOCK_KEY)


def run_heartbeat_job() -> None:
    """Bare RQ entrypoint for the scheduled heartbeat (no sync_log —
    the check itself creates logs only for the targets it enqueues)."""
    from sqlalchemy.orm import Session as _Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415

    with _Session(get_engine()) as session:
        fake_log = SyncLog(system="brevo", operation="auto_sync_check", status="running")
        brevo_auto_sync_check(session, fake_log)


OPERATIONS["brevo:push_target"] = push_brevo_target
OPERATIONS["brevo:auto_sync_check"] = brevo_auto_sync_check
