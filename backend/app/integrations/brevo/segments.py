"""Mirror Brevo segments into the CRM as native-looking segments.

Brevo's segment model is rule-based but its API doesn't expose the
rule tree — only `name`, `count`, and the current membership. The
CRM can't reverse-engineer the filter, so it imports each Brevo
segment as a **mirror**: an ordinary `segments` row with
`is_dynamic=False`, `static_contact_ids` periodically refreshed by
this module, and `external_source = "brevo:<account>:<brevo_id>"`
identifying it as externally managed.

The mirror reuses the existing static-segment path of the engine
(no engine change). The UI keys off `external_source` to:
- hide the rule editor,
- show a "Espejo Brevo" badge,
- offer "Refrescar ahora" + "Abrir en Brevo".

Membership uses **existing CRM contacts only** (resolved by email).
A Brevo member that doesn't exist in the CRM is skipped — webhooks
+ sync_contacts feed the CRM, this job only assigns membership.

Two worker operations registered:
- `brevo:refresh_segments` — full catalogue refresh for an account
  (create / update / delete mirrors + refresh member lists).
- `brevo:refresh_segment` — refresh one mirror by `segment_id`
  (used by the "Refrescar ahora" button).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.integrations.brevo.client import BrevoClient
from app.models.crm import Contact, ExternalSystem, Segment, SyncLog
from app.models.integration_settings import IntegrationAccount
from app.workers.jobs import OPERATIONS, SyncOutcome

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
DEFAULT_REFRESH_INTERVAL_MINUTES = 360  # 6h


def _account(session: Session, account_id: str) -> IntegrationAccount:
    account = session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.BREVO,
            IntegrationAccount.account_id == account_id,
        )
    )
    if account is None:
        raise ValueError(f"Brevo account {account_id!r} not configured")
    return account


def _mirror_owner(session: Session, account: IntegrationAccount) -> str:
    """Brevo mirrors need an owner_user_id for the FK. Use the operator
    who configured the account (account.id ↔ user via the audit log
    would be overkill); fall back to any admin so the import doesn't
    fail on a brand-new tenant."""
    from app.models.crm import User, UserRole  # noqa: PLC0415

    admin = session.scalar(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at)
    )
    if admin is None:
        raise ValueError(
            "No admin user to own the Brevo segment mirrors — create one first."
        )
    _ = account
    return admin.id


def _source_key(account_id: str, brevo_segment_id: int) -> str:
    return f"brevo:{account_id}:{brevo_segment_id}"


def upsert_mirror(
    session: Session,
    *,
    account: IntegrationAccount,
    brevo_segment: dict[str, Any],
    member_contact_ids: list[str],
) -> Segment:
    """Create or update one segment mirror with the resolved member
    list. Returns the segment row (committed by the caller)."""
    source = _source_key(account.account_id, int(brevo_segment["id"]))
    row = session.scalar(
        select(Segment).where(Segment.external_source == source)
    )
    if row is None:
        row = Segment(
            name=str(brevo_segment.get("name") or f"Brevo segment {brevo_segment.get('id')}"),
            description=(
                "Segmento gestionado en Brevo. La membresía se refresca "
                "automáticamente desde Brevo."
            ),
            rules_json=None,
            is_dynamic=False,
            owner_user_id=_mirror_owner(session, account),
            is_shared=True,
            external_source=source,
            external_refresh_interval_minutes=DEFAULT_REFRESH_INTERVAL_MINUTES,
        )
        session.add(row)
    row.name = str(brevo_segment.get("name") or row.name)
    row.static_contact_ids = json.dumps(member_contact_ids)
    row.cached_count = len(member_contact_ids)
    now = datetime.now(UTC)
    row.last_evaluated_at = now
    row.external_last_refreshed_at = now
    session.flush()
    return row


def _resolve_member_emails(
    session: Session, emails: list[str]
) -> tuple[list[str], int]:
    """Map Brevo member emails → CRM contact ids. Unknown emails are
    silently skipped; returns `(contact_ids, skipped_count)`."""
    if not emails:
        return [], 0
    normalized = [e.lower().strip() for e in emails if e]
    rows = session.execute(
        select(Contact.id, Contact.email).where(
            func.lower(Contact.email).in_(normalized)
        )
    ).all()
    found = {email.lower(): cid for cid, email in rows if email}
    contact_ids = [found[e] for e in normalized if e in found]
    return contact_ids, len(normalized) - len(contact_ids)


async def refresh_segment_membership(
    session: Session,
    *,
    account: IntegrationAccount,
    brevo_segment: dict[str, Any],
) -> tuple[Segment, dict[str, Any]]:
    """Pull every page of `/contacts/segments/{id}/contacts`, resolve
    against the CRM by email, persist the mirror row. Returns
    `(segment_row, stats)`."""
    segment_id = int(brevo_segment["id"])
    all_emails: list[str] = []
    async with BrevoClient(session, account.account_id) as client:
        offset = 0
        while True:
            page = await client.get_segment_contacts(
                segment_id, limit=PAGE_SIZE, offset=offset
            )
            contacts = page["contacts"]
            if not contacts:
                break
            for entry in contacts:
                email = entry.get("email")
                if email:
                    all_emails.append(str(email))
            if len(contacts) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    contact_ids, skipped = _resolve_member_emails(session, all_emails)
    row = upsert_mirror(
        session,
        account=account,
        brevo_segment=brevo_segment,
        member_contact_ids=contact_ids,
    )
    return row, {
        "brevo_total": len(all_emails),
        "matched": len(contact_ids),
        "unknown_skipped": skipped,
    }


async def sync_brevo_segments(
    session: Session, account_id: str
) -> dict[str, Any]:
    """Public entry point used by the route layer's "Refrescar ahora"
    button and by the periodic worker handler."""
    account = _account(session, account_id)

    remote_segments: list[dict[str, Any]] = []
    async with BrevoClient(session, account_id) as client:
        offset = 0
        while True:
            body = await client.list_segments(limit=PAGE_SIZE, offset=offset)
            chunk = body.get("segments") or []
            if not chunk:
                break
            remote_segments.extend(chunk)
            if len(chunk) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    remote_ids = {int(s["id"]) for s in remote_segments if s.get("id") is not None}

    # Drop mirrors that no longer exist in Brevo (clean removal —
    # static_contact_ids comes with the row, no dangling references).
    existing = list(
        session.scalars(
            select(Segment).where(
                Segment.external_source.like(f"brevo:{account_id}:%")
            )
        )
    )
    removed = 0
    for row in existing:
        try:
            brevo_id = int(str(row.external_source).rsplit(":", 1)[-1])
        except ValueError:
            continue
        if brevo_id not in remote_ids:
            session.delete(row)
            removed += 1

    total = 0
    matched = 0
    skipped = 0
    for brevo_segment in remote_segments:
        try:
            _, stats = await refresh_segment_membership(
                session, account=account, brevo_segment=brevo_segment
            )
            total += 1
            matched += stats["matched"]
            skipped += stats["unknown_skipped"]
            session.commit()
        except Exception as exc:  # noqa: BLE001 - account-level isolation
            logger.warning(
                "brevo.segments refresh failed segment=%s: %s",
                brevo_segment.get("id"),
                exc,
            )
    return {
        "segments_refreshed": total,
        "segments_removed": removed,
        "members_matched": matched,
        "unknown_skipped": skipped,
    }


# ---------------------------------------------------------------------------
# worker handlers
# ---------------------------------------------------------------------------


def refresh_brevo_segments(session: Session, sync_log: SyncLog) -> SyncOutcome:
    """Worker entry: refresh every mirror for the sync_log's account."""
    account_id = sync_log.account_id or ""
    try:
        stats = asyncio.run(sync_brevo_segments(session, account_id))
    except Exception as exc:  # noqa: BLE001
        return SyncOutcome(records_failed=1, error_summary=str(exc))
    return SyncOutcome(
        records_processed=stats["segments_refreshed"],
        records_skipped=stats["unknown_skipped"],
        metadata=stats,
    )


def refresh_one_brevo_segment(
    session: Session, sync_log: SyncLog
) -> SyncOutcome:
    """Worker entry for the "Refrescar ahora" button on a single
    mirror. Payload must carry `{"segment_id": "<crm uuid>"}`."""
    payload: dict[str, Any] = {}
    if sync_log.metadata_json:
        try:
            decoded = json.loads(sync_log.metadata_json)
            payload = decoded.get("payload") or decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            payload = {}
    segment_id = str(payload.get("segment_id") or "")
    row = session.get(Segment, segment_id) if segment_id else None
    if row is None or not row.external_source:
        return SyncOutcome(
            records_failed=1,
            error_summary=f"Segment {segment_id!r} is not a Brevo mirror.",
        )
    parts = row.external_source.split(":")
    if len(parts) != 3 or parts[0] != "brevo":
        return SyncOutcome(
            records_failed=1,
            error_summary=f"Unexpected external_source: {row.external_source!r}",
        )
    _, account_id, brevo_segment_id = parts
    account = _account(session, account_id)

    async def _drive() -> tuple[Segment, dict[str, Any]]:
        return await refresh_segment_membership(
            session,
            account=account,
            brevo_segment={"id": int(brevo_segment_id), "name": row.name},
        )

    try:
        _, stats = asyncio.run(_drive())
        session.commit()
    except Exception as exc:  # noqa: BLE001
        return SyncOutcome(records_failed=1, error_summary=str(exc))
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_SUCCEEDED,
        target_type="segment",
        target_id=row.id,
        metadata={"source": row.external_source, **stats},
    )
    session.commit()
    return SyncOutcome(records_processed=stats["matched"], metadata=stats)


def segment_needs_refresh(row: Segment) -> bool:
    """Periodic-cron predicate: True when the mirror's age exceeds its
    own interval (or the system default when not pinned)."""
    if row.external_source is None:
        return False
    last = row.external_last_refreshed_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    minutes = (
        row.external_refresh_interval_minutes
        or DEFAULT_REFRESH_INTERVAL_MINUTES
    )
    return last + timedelta(minutes=minutes) <= datetime.now(UTC)


OPERATIONS["brevo:refresh_segments"] = refresh_brevo_segments
OPERATIONS["brevo:refresh_segment"] = refresh_one_brevo_segment
