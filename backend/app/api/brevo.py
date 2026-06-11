"""HTTP surface for the Brevo integration: sync targets, list/sender
proxies, templates and campaigns.

Auth: read endpoints accept any authenticated user; mutations require
manager+ (same policy as the integration accounts admin)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_manager, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.brevo import campaigns as campaigns_service
from app.integrations.brevo import templates as templates_service
from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.sync_targets import (
    run_brevo_target,
    schedule_heartbeat,
)
from app.integrations.errors import IntegrationError
from app.models.brevo import (
    BrevoCampaignCache,
    BrevoSyncTarget,
    BrevoTemplateCache,
    SyncDirection,
    TargetRunStatus,
)
from app.models.crm import ExternalSystem, Segment, User
from app.models.integration_settings import IntegrationAccount
from app.schemas.brevo import (
    BrevoCampaignCreate,
    BrevoCampaignRead,
    BrevoCampaignScheduleRequest,
    BrevoCampaignUpdate,
    BrevoListContactItem,
    BrevoListContactsMutation,
    BrevoListContactsMutationResult,
    BrevoListContactsPage,
    BrevoListCreate,
    BrevoListRead,
    BrevoListUpdate,
    BrevoSenderRead,
    BrevoSendTestRequest,
    BrevoSyncTargetCreate,
    BrevoSyncTargetRead,
    BrevoSyncTargetUpdate,
    BrevoTargetRunResponse,
    BrevoTemplateCreate,
    BrevoTemplateRead,
    BrevoTemplateUpdate,
)
from app.workers.jobs import enqueue_sync_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brevo", tags=["brevo"])


def _require_brevo_account(session: Session, account_id: str) -> IntegrationAccount:
    account = session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.system == ExternalSystem.BREVO,
            IntegrationAccount.account_id == account_id,
        )
    )
    if account is None:
        raise not_found("Brevo account")
    return account


def _target_to_read(session: Session, target: BrevoSyncTarget) -> BrevoSyncTargetRead:
    read = BrevoSyncTargetRead.model_validate(target)
    segment = session.get(Segment, target.segment_id)
    read.segment_name = segment.name if segment else None
    return read


# ---------------------------------------------------------------------------
# Sync targets
# ---------------------------------------------------------------------------


@router.get("/sync-targets", response_model=list[BrevoSyncTargetRead])
def list_sync_targets(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[BrevoSyncTargetRead]:
    _ = current_user
    targets = list(
        session.scalars(
            select(BrevoSyncTarget)
            .where(BrevoSyncTarget.brevo_account_id == account_id)
            .order_by(BrevoSyncTarget.name)
        )
    )
    return [_target_to_read(session, t) for t in targets]


@router.post(
    "/sync-targets",
    response_model=BrevoSyncTargetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sync_target(
    payload: BrevoSyncTargetCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoSyncTargetRead:
    _require_brevo_account(session, payload.brevo_account_id)
    if session.get(Segment, payload.segment_id) is None:
        raise not_found("Segment")
    target = BrevoSyncTarget(
        brevo_account_id=payload.brevo_account_id,
        name=payload.name,
        description=payload.description,
        segment_id=payload.segment_id,
        brevo_list_id=payload.brevo_list_id,
        sync_direction=SyncDirection(payload.sync_direction),
        auto_sync_enabled=payload.auto_sync_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    session.add(target)
    session.flush()
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_sync_target",
        target_id=target.id,
        actor=current_user,
        metadata={"event": "target_created", "name": target.name},
        request=request,
    )
    session.commit()
    session.refresh(target)
    # Arm the auto-sync heartbeat so a fresh deployment starts
    # scheduling without manual intervention.
    schedule_heartbeat()
    return _target_to_read(session, target)


@router.patch("/sync-targets/{target_id}", response_model=BrevoSyncTargetRead)
def update_sync_target(
    target_id: str,
    payload: BrevoSyncTargetUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoSyncTargetRead:
    target = session.get(BrevoSyncTarget, target_id)
    if target is None:
        raise not_found("Sync target")
    changes = payload.model_dump(exclude_unset=True)
    if "segment_id" in changes and session.get(Segment, changes["segment_id"]) is None:
        raise not_found("Segment")
    for key, value in changes.items():
        if key == "sync_direction" and value is not None:
            value = SyncDirection(value)
        setattr(target, key, value)
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_sync_target",
        target_id=target.id,
        actor=current_user,
        metadata={"event": "target_updated", "changed": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(target)
    return _target_to_read(session, target)


@router.delete("/sync-targets/{target_id}")
def delete_sync_target(
    target_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    target = session.get(BrevoSyncTarget, target_id)
    if target is None:
        raise not_found("Sync target")
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_sync_target",
        target_id=target.id,
        actor=current_user,
        metadata={"event": "target_deleted", "name": target.name},
        request=request,
    )
    session.delete(target)
    session.commit()
    return {"message": "Sync target eliminado"}


@router.post("/sync-targets/{target_id}/run", response_model=BrevoTargetRunResponse)
def run_sync_target(
    target_id: str,
    request: Request,
    dry_run: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoTargetRunResponse:
    target = session.get(BrevoSyncTarget, target_id)
    if target is None:
        raise not_found("Sync target")

    if dry_run:
        # Dry runs evaluate the segment + membership delta inline and
        # never touch Brevo, so they're safe on the request thread.
        try:
            stats = run_brevo_target(session, target, dry_run=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return BrevoTargetRunResponse(dry_run=True, stats=stats)

    if target.last_run_status == TargetRunStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este target ya tiene una ejecución en curso.",
        )
    sync_log_id, job_id = enqueue_sync_job(
        session,
        system="brevo",
        account_id=target.brevo_account_id,
        operation="push_target",
        triggered_by="manual",
        triggered_by_user_id=current_user.id,
        payload={"target_id": target.id},
        request=request,
    )
    session.commit()
    return BrevoTargetRunResponse(sync_log_id=sync_log_id, job_id=job_id)


# ---------------------------------------------------------------------------
# Lists + senders proxies
# ---------------------------------------------------------------------------


@router.get("/lists", response_model=list[BrevoListRead])
def list_brevo_lists(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[BrevoListRead]:
    _ = current_user
    _require_brevo_account(session, account_id)

    async def _fetch() -> list[dict[str, Any]]:
        async with BrevoClient(session, account_id) as client:
            body = await client.list_lists(limit=50, offset=0)
            return body.get("lists") or []

    try:
        rows = asyncio.run(_fetch())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return [_list_row_to_read(row) for row in rows if row.get("id") is not None]


def _list_row_to_read(row: dict[str, Any]) -> BrevoListRead:
    """Normalise a Brevo `/contacts/lists` row into our `BrevoListRead`.

    Brevo's response shape varies slightly between the index call
    (returns `totalSubscribers` / `uniqueSubscribers`) and the detail
    call (adds `totalBlacklisted`). We surface the superset so the UI
    has every counter without re-fetching."""
    return BrevoListRead(
        id=int(row.get("id") or 0),
        name=str(row.get("name") or row.get("id") or ""),
        total_subscribers=int(
            row.get("totalSubscribers") or row.get("uniqueSubscribers") or 0
        ),
        unique_subscribers=(
            int(row["uniqueSubscribers"])
            if row.get("uniqueSubscribers") is not None
            else None
        ),
        total_blacklisted=(
            int(row["totalBlacklisted"])
            if row.get("totalBlacklisted") is not None
            else None
        ),
        folder_id=row.get("folderId"),
    )


@router.get("/lists/{list_id}", response_model=BrevoListRead)
def get_brevo_list(
    list_id: int,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> BrevoListRead:
    _ = current_user
    _require_brevo_account(session, account_id)

    async def _fetch() -> dict[str, Any]:
        async with BrevoClient(session, account_id) as client:
            return await client.get_list(list_id)

    try:
        row = asyncio.run(_fetch())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    if not row or row.get("id") is None:
        raise not_found("Brevo list")
    return _list_row_to_read(row)


@router.post(
    "/lists",
    response_model=BrevoListRead,
    status_code=status.HTTP_201_CREATED,
)
def create_brevo_list(
    payload: BrevoListCreate,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoListRead:
    _ = current_user
    _require_brevo_account(session, account_id)

    async def _create() -> dict[str, Any]:
        async with BrevoClient(session, account_id) as client:
            return await client.create_list(payload.name, folder_id=payload.folder_id)

    try:
        created = asyncio.run(_create())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    if created.get("id") is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Brevo no devolvió un id de lista al crear",
        )
    # Re-fetch the detail so counters are populated for the UI in one
    # round-trip after creation.
    async def _detail() -> dict[str, Any]:
        async with BrevoClient(session, account_id) as client:
            return await client.get_list(int(created["id"]))

    try:
        detail = asyncio.run(_detail())
    except IntegrationError:
        detail = created
    return _list_row_to_read(detail)


@router.patch("/lists/{list_id}", response_model=BrevoListRead)
def update_brevo_list(
    list_id: int,
    payload: BrevoListUpdate,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoListRead:
    _ = current_user
    if payload.name is None and payload.folder_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pasa al menos `name` o `folder_id`",
        )
    _require_brevo_account(session, account_id)

    async def _run() -> dict[str, Any]:
        async with BrevoClient(session, account_id) as client:
            await client.update_list(
                list_id, name=payload.name, folder_id=payload.folder_id
            )
            return await client.get_list(list_id)

    try:
        detail = asyncio.run(_run())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return _list_row_to_read(detail)


@router.delete("/lists/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brevo_list(
    list_id: int,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> Response:
    _ = current_user
    _require_brevo_account(session, account_id)

    async def _run() -> None:
        async with BrevoClient(session, account_id) as client:
            await client.delete_list(list_id)

    try:
        asyncio.run(_run())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/lists/{list_id}/contacts", response_model=BrevoListContactsPage)
def list_brevo_list_contacts(
    list_id: int,
    account_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> BrevoListContactsPage:
    """Paginated subscribers of a Brevo list, mapped onto our CRM
    contacts where possible.

    Returns one row per Brevo subscriber: `contact_id` + `first_name`
    /`last_name` populate when we already have a CRM contact for that
    email (case-insensitive match); `contact_known=False` flags
    addresses we don't manage so the UI can highlight them.
    """
    from app.models.crm import Contact  # noqa: PLC0415

    _ = current_user
    _require_brevo_account(session, account_id)

    async def _fetch() -> dict[str, Any]:
        async with BrevoClient(session, account_id) as client:
            return await client.list_list_contacts(
                list_id, limit=limit, offset=offset
            )

    try:
        body = asyncio.run(_fetch())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc

    raw_rows = body.get("contacts") or []
    emails = [
        str(row.get("email") or "").strip().lower()
        for row in raw_rows
        if row.get("email")
    ]
    contact_map: dict[str, Contact] = {}
    if emails:
        for contact in session.scalars(
            select(Contact).where(func.lower(Contact.email).in_(emails))
        ):
            contact_map[(contact.email or "").lower()] = contact

    items = [
        BrevoListContactItem(
            email=str(row.get("email") or ""),
            contact_id=(c.id if (c := contact_map.get((row.get("email") or "").lower())) else None),
            first_name=(c.first_name if c else None),
            last_name=(c.last_name if c else None),
            contact_known=c is not None,
        )
        for row in raw_rows
    ]
    return BrevoListContactsPage(
        items=items,
        total=int(body.get("count") or 0),
        limit=limit,
        offset=offset,
    )


def _resolve_mutation_emails(
    session: Session, payload: BrevoListContactsMutation
) -> tuple[list[str], int, int]:
    """Combine the `emails` + `contact_ids` inputs into a single email
    list, deduped, lowercased. Returns `(emails, unknown_contacts,
    contacts_without_email)` so the caller can report skipped
    counters."""
    from app.models.crm import Contact  # noqa: PLC0415

    out: dict[str, None] = {}
    unknown_contacts = 0
    contacts_without_email = 0
    for raw in payload.emails or []:
        normalised = str(raw or "").strip().lower()
        if normalised:
            out.setdefault(normalised, None)
    if payload.contact_ids:
        rows = list(
            session.scalars(
                select(Contact).where(Contact.id.in_(payload.contact_ids))
            )
        )
        by_id = {c.id: c for c in rows}
        for cid in payload.contact_ids:
            contact = by_id.get(cid)
            if contact is None:
                unknown_contacts += 1
                continue
            if not contact.email:
                contacts_without_email += 1
                continue
            out.setdefault(contact.email.lower(), None)
    return list(out.keys()), unknown_contacts, contacts_without_email


@router.post(
    "/lists/{list_id}/contacts/add",
    response_model=BrevoListContactsMutationResult,
)
def add_contacts_to_brevo_list(
    list_id: int,
    payload: BrevoListContactsMutation,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoListContactsMutationResult:
    _ = current_user
    _require_brevo_account(session, account_id)
    emails, unknown, no_email = _resolve_mutation_emails(session, payload)
    if not emails:
        return BrevoListContactsMutationResult(
            requested=len(payload.emails or []) + len(payload.contact_ids or []),
            sent=0,
            skipped_unknown_contact=unknown,
            skipped_missing_email=no_email,
        )

    async def _run() -> None:
        async with BrevoClient(session, account_id) as client:
            # Brevo accepts up to ~150 per call; batch to stay safe.
            for chunk_start in range(0, len(emails), 100):
                chunk = emails[chunk_start : chunk_start + 100]
                await client.add_contacts_to_list(list_id, chunk)

    try:
        asyncio.run(_run())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return BrevoListContactsMutationResult(
        requested=len(payload.emails or []) + len(payload.contact_ids or []),
        sent=len(emails),
        skipped_unknown_contact=unknown,
        skipped_missing_email=no_email,
    )


@router.post(
    "/lists/{list_id}/contacts/remove",
    response_model=BrevoListContactsMutationResult,
)
def remove_contacts_from_brevo_list(
    list_id: int,
    payload: BrevoListContactsMutation,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoListContactsMutationResult:
    _ = current_user
    _require_brevo_account(session, account_id)
    emails, unknown, no_email = _resolve_mutation_emails(session, payload)
    if not emails:
        return BrevoListContactsMutationResult(
            requested=len(payload.emails or []) + len(payload.contact_ids or []),
            sent=0,
            skipped_unknown_contact=unknown,
            skipped_missing_email=no_email,
        )

    async def _run() -> None:
        async with BrevoClient(session, account_id) as client:
            for chunk_start in range(0, len(emails), 100):
                chunk = emails[chunk_start : chunk_start + 100]
                await client.remove_contacts_from_list(list_id, chunk)

    try:
        asyncio.run(_run())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return BrevoListContactsMutationResult(
        requested=len(payload.emails or []) + len(payload.contact_ids or []),
        sent=len(emails),
        skipped_unknown_contact=unknown,
        skipped_missing_email=no_email,
    )


@router.get("/senders", response_model=list[BrevoSenderRead])
def list_brevo_senders(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[BrevoSenderRead]:
    _ = current_user
    _require_brevo_account(session, account_id)

    async def _fetch() -> list[dict[str, Any]]:
        async with BrevoClient(session, account_id) as client:
            return await client.list_senders()

    try:
        rows = asyncio.run(_fetch())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return [
        BrevoSenderRead(
            id=int(row.get("id") or 0),
            name=str(row.get("name") or ""),
            email=str(row.get("email") or ""),
            active=bool(row.get("active", False)),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Webhook stats (counters for the integrations panel)
# ---------------------------------------------------------------------------


@router.post("/segments/refresh-all")
def refresh_all_brevo_segments(
    request: Request,
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, Any]:
    """Enqueue a full Brevo-segments refresh for one account.

    Mirrors the wider "Sincronizar ahora" pattern: the route returns
    `(sync_log_id, job_id)` and the worker picks it up. The dashboard
    panel uses this to give the operator a manual "Importar ahora"
    button next to the periodic 6h cron.
    """
    _require_brevo_account(session, account_id)
    sync_log_id, job_id = enqueue_sync_job(
        session,
        system="brevo",
        account_id=account_id,
        operation="refresh_segments",
        triggered_by="manual",
        triggered_by_user_id=current_user.id,
        request=request,
    )
    session.commit()
    return {"sync_log_id": sync_log_id, "job_id": job_id}


@router.post("/segments/{segment_id}/refresh")
def refresh_one_brevo_segment(
    segment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, Any]:
    """Refresh one Brevo mirror — the segment detail page's
    "Refrescar ahora desde Brevo" button calls this."""
    segment = session.get(Segment, segment_id)
    if segment is None or not segment.external_source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Segmento Brevo no encontrado",
        )
    if not segment.external_source.startswith("brevo:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No es un segmento gestionado por Brevo",
        )
    account_id = segment.external_source.split(":")[1]
    sync_log_id, job_id = enqueue_sync_job(
        session,
        system="brevo",
        account_id=account_id,
        operation="refresh_segment",
        triggered_by="manual",
        triggered_by_user_id=current_user.id,
        payload={"segment_id": segment.id},
        request=request,
    )
    session.commit()
    return {"sync_log_id": sync_log_id, "job_id": job_id}


@router.get("/webhook-stats")
def brevo_webhook_stats(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Events materialised as activity_events in the last 24h, grouped
    by type. Powers the "Webhooks" section of the Brevo card."""
    _ = current_user
    from datetime import timedelta  # noqa: PLC0415

    from sqlalchemy import func  # noqa: PLC0415

    from app.models.crm import ActivityEvent  # noqa: PLC0415

    since = datetime.now(UTC) - timedelta(hours=24)
    rows = session.execute(
        select(ActivityEvent.event_type, func.count(ActivityEvent.id))
        .where(
            ActivityEvent.system == "brevo",
            ActivityEvent.account_id == account_id,
            ActivityEvent.occurred_at >= since,
        )
        .group_by(ActivityEvent.event_type)
    ).all()
    by_type = {str(event_type): int(count) for event_type, count in rows}
    return {"total": sum(by_type.values()), "by_type": by_type}


# ---------------------------------------------------------------------------
# Historical events backfill
# ---------------------------------------------------------------------------


@router.post("/historical-backfill")
def trigger_historical_backfill(
    request: Request,
    account_id: str = Query(...),
    max_campaigns: int | None = Query(default=None, ge=1, le=2000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Enqueue a historical events backfill for one Brevo account.

    Admin-only — the job iterates every cached sent/archive campaign
    and pulls per-event recipients from Brevo; on a tenant with
    hundreds of campaigns it can run for 10-30 minutes and dominate
    the connector's request budget. The UI surfaces a confirmation
    before firing.
    """
    _require_brevo_account(session, account_id)
    payload = {"max_campaigns": max_campaigns} if max_campaigns else None
    sync_log_id, job_id = enqueue_sync_job(
        session,
        system="brevo",
        account_id=account_id,
        operation="historical_backfill",
        triggered_by="manual",
        triggered_by_user_id=current_user.id,
        payload=payload,
        request=request,
    )
    session.commit()
    return {"sync_log_id": sync_log_id, "job_id": job_id}


@router.get("/historical-backfill/status")
def historical_backfill_status(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Last backfill run for the account — drives the "Último backfill"
    line in the Brevo integration panel."""
    _ = current_user
    from app.models.crm import SyncLog  # noqa: PLC0415

    row = session.scalar(
        select(SyncLog)
        .where(
            SyncLog.system == ExternalSystem.BREVO,
            SyncLog.account_id == account_id,
            SyncLog.operation == "historical_backfill",
        )
        .order_by(SyncLog.created_at.desc())
        .limit(1)
    )
    if row is None:
        return {"status": "never"}
    metadata: dict[str, Any] = {}
    if row.metadata_json:
        try:
            decoded = json.loads(row.metadata_json)
            if isinstance(decoded, dict):
                metadata = decoded
        except (ValueError, TypeError):
            metadata = {}
    # The handler stores its aggregate summary in `outcome.metadata`,
    # which the worker persists under SyncLog.metadata['outcome'].
    outcome = metadata.get("outcome") or metadata
    return {
        "sync_log_id": row.id,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "records_processed": row.records_processed,
        "records_skipped": row.records_skipped,
        "records_failed": row.records_failed,
        "error_summary": row.error_summary,
        "campaigns_processed": outcome.get("campaigns_processed"),
        "campaigns_skipped": outcome.get("campaigns_skipped"),
        "events_inserted_total": outcome.get("events_inserted_total"),
        "events_skipped_total": outcome.get("events_skipped_total"),
        "contacts_unknown_total": outcome.get("contacts_unknown_total"),
        "max_campaigns": outcome.get("max_campaigns"),
    }


def _decode_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Templates (cache-backed CRUD)
# ---------------------------------------------------------------------------


def _get_template_or_404(session: Session, template_id: str) -> BrevoTemplateCache:
    row = session.get(BrevoTemplateCache, template_id)
    if row is None:
        raise not_found("Brevo template")
    return row


@router.get("/templates", response_model=list[BrevoTemplateRead])
def list_templates(
    account_id: str = Query(...),
    refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[BrevoTemplateRead]:
    _ = current_user
    _require_brevo_account(session, account_id)
    if refresh:
        try:
            asyncio.run(templates_service.refresh_templates_cache(session, account_id))
            session.commit()
        except IntegrationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
            ) from exc
    rows = list(
        session.scalars(
            select(BrevoTemplateCache)
            .where(BrevoTemplateCache.brevo_account_id == account_id)
            .order_by(BrevoTemplateCache.name)
        )
    )
    # List view excludes the heavy HTML body.
    reads = []
    for row in rows:
        read = BrevoTemplateRead.model_validate(row)
        read.html_content = None
        reads.append(read)
    return reads


@router.get("/templates/{template_id}", response_model=BrevoTemplateRead)
def get_template(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> BrevoTemplateRead:
    _ = current_user
    row = _get_template_or_404(session, template_id)
    if row.html_content is None:
        try:
            asyncio.run(templates_service.ensure_template_html(session, row))
            session.commit()
        except IntegrationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
            ) from exc
    return BrevoTemplateRead.model_validate(row)


@router.post(
    "/templates",
    response_model=BrevoTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    payload: BrevoTemplateCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoTemplateRead:
    _require_brevo_account(session, payload.brevo_account_id)

    async def _create() -> dict[str, Any]:
        async with BrevoClient(session, payload.brevo_account_id) as client:
            return await client.create_email_template(
                {
                    "templateName": payload.name,
                    "subject": payload.subject,
                    "htmlContent": payload.html_content,
                    "sender": {
                        "name": payload.sender_name,
                        "email": payload.sender_email,
                    },
                    "isActive": payload.is_active,
                    **({"tag": payload.tag} if payload.tag else {}),
                }
            )

    try:
        created = asyncio.run(_create())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc

    row = templates_service.upsert_template_row(
        session,
        account_id=payload.brevo_account_id,
        payload={
            "id": created.get("id"),
            "name": payload.name,
            "subject": payload.subject,
            "isActive": payload.is_active,
            "tag": payload.tag,
            "sender": {
                "name": payload.sender_name,
                "email": payload.sender_email,
            },
        },
        html_content=payload.html_content,
    )
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_template",
        target_id=row.id,
        actor=current_user,
        metadata={"event": "template_created", "name": payload.name},
        request=request,
    )
    session.commit()
    session.refresh(row)
    return BrevoTemplateRead.model_validate(row)


@router.patch("/templates/{template_id}", response_model=BrevoTemplateRead)
def update_template(
    template_id: str,
    payload: BrevoTemplateUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoTemplateRead:
    row = _get_template_or_404(session, template_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return BrevoTemplateRead.model_validate(row)

    brevo_payload: dict[str, Any] = {}
    if "name" in changes:
        brevo_payload["templateName"] = changes["name"]
    if "subject" in changes:
        brevo_payload["subject"] = changes["subject"]
    if "html_content" in changes:
        brevo_payload["htmlContent"] = changes["html_content"]
    if "is_active" in changes:
        brevo_payload["isActive"] = changes["is_active"]
    if "tag" in changes:
        brevo_payload["tag"] = changes["tag"]
    if "sender_name" in changes or "sender_email" in changes:
        brevo_payload["sender"] = {
            "name": changes.get("sender_name", row.sender_name),
            "email": changes.get("sender_email", row.sender_email),
        }

    async def _update() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.update_email_template(row.brevo_template_id, brevo_payload)

    try:
        asyncio.run(_update())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc

    for key, value in changes.items():
        setattr(row, key, value)
    row.cached_at = datetime.now(UTC)
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_template",
        target_id=row.id,
        actor=current_user,
        metadata={"event": "template_updated", "changed": sorted(changes.keys())},
        request=request,
    )
    session.commit()
    session.refresh(row)
    return BrevoTemplateRead.model_validate(row)


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    row = _get_template_or_404(session, template_id)

    async def _delete() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.delete_email_template(row.brevo_template_id)

    try:
        asyncio.run(_delete())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_template",
        target_id=row.id,
        actor=current_user,
        metadata={"event": "template_deleted", "name": row.name},
        request=request,
    )
    session.delete(row)
    session.commit()
    return {"message": "Plantilla eliminada"}


@router.post("/templates/{template_id}/send-test")
def send_template_test(
    template_id: str,
    payload: BrevoSendTestRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    """Send a template test, honouring the editor's sender selection.

    Brevo's `sendTest` endpoint has NO per-request sender override —
    it always uses the sender stored on the template. Production bug:
    the operator picked "Artisjet Europe" in the dropdown, the test
    arrived from Brevo's `*.brevosend.com` fallback because the
    template still carried the stale (unverified) sender. When the
    request carries a sender different from the cached one, we
    persist it on the template FIRST (PUT), mirror the cache, then
    fire the test."""
    _ = current_user
    row = _get_template_or_404(session, template_id)

    sender_changed = bool(
        payload.sender_email
        and (
            payload.sender_email != row.sender_email
            or (payload.sender_name or "") != (row.sender_name or "")
        )
    )

    async def _send() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            if sender_changed:
                await client.update_email_template(
                    row.brevo_template_id,
                    {
                        "sender": {
                            "name": payload.sender_name or row.sender_name,
                            "email": payload.sender_email,
                        }
                    },
                )
            await client.send_test_template(row.brevo_template_id, payload.emails)

    try:
        asyncio.run(_send())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    if sender_changed:
        row.sender_email = payload.sender_email
        row.sender_name = payload.sender_name or row.sender_name
        row.cached_at = datetime.now(UTC)
        session.commit()
    return {"message": f"Test enviado a {', '.join(payload.emails)}"}


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

#: Statuses the operator can still edit / delete / send from.
EDITABLE_CAMPAIGN_STATUSES = {"draft", "suspended"}
SENDABLE_CAMPAIGN_STATUSES = {"draft", "queued", "suspended"}


def _get_campaign_or_404(session: Session, campaign_id: str) -> BrevoCampaignCache:
    row = session.get(BrevoCampaignCache, campaign_id)
    if row is None:
        raise not_found("Brevo campaign")
    return row


def _campaign_error_detail(exc: IntegrationError) -> str:
    """Operator-facing detail for campaign-call failures.

    A 405 from Brevo on a documented route is almost always an
    account-side restriction (API key created without Marketing
    permissions, or a plan that doesn't expose the campaigns API) —
    not a wrong URL. The raw "405 from brevo/default" gave the
    operator nothing to act on; this points at the two checks that
    actually resolve it."""
    status_code = getattr(exc, "status_code", None)
    if status_code == 405:
        return (
            "Brevo rechazó la operación de campañas (405 Method Not "
            "Allowed). Esto suele indicar que la API key no tiene "
            "permisos de Marketing/Campañas o que el plan de la cuenta "
            "no permite gestionar campañas vía API. Revisa en Brevo → "
            "Settings → SMTP & API que la key tenga acceso completo, o "
            "genera una nueva sin restricciones de permisos."
        )
    return exc.message


@router.get("/campaigns", response_model=list[BrevoCampaignRead])
def list_campaigns(
    account_id: str = Query(...),
    status_filter: str | None = Query(default=None, alias="status"),
    refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[BrevoCampaignRead]:
    _ = current_user
    _require_brevo_account(session, account_id)
    if refresh:
        try:
            asyncio.run(campaigns_service.refresh_campaigns_cache(session, account_id))
            session.commit()
        except IntegrationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
            ) from exc
    statement = select(BrevoCampaignCache).where(
        BrevoCampaignCache.brevo_account_id == account_id
    )
    if status_filter:
        statement = statement.where(BrevoCampaignCache.status == status_filter)
    rows = list(
        session.scalars(statement.order_by(BrevoCampaignCache.created_at_brevo.desc()))
    )
    return [BrevoCampaignRead.model_validate(row) for row in rows]


@router.get("/campaigns/{campaign_id}", response_model=BrevoCampaignRead)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> BrevoCampaignRead:
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)
    is_stale = campaigns_service.campaign_cache_is_stale(row)
    needs_html = row.html_content_cached is None
    if is_stale or needs_html:
        try:
            # `ensure_campaign_html` short-circuits when the HTML is
            # already cached AND the row is fresh; when either is
            # missing it goes through the full GET that returns both
            # the latest stats and the HTML body.
            if needs_html:
                asyncio.run(
                    campaigns_service.ensure_campaign_html(session, row)
                )
            else:
                asyncio.run(
                    campaigns_service.refresh_campaign_row(session, row)
                )
            session.commit()
        except IntegrationError as exc:
            # Serve the stale copy rather than failing the page; the
            # operator sees cached_at and can retry.
            logger.warning(
                "brevo.campaign refresh failed id=%s: %s",
                row.brevo_campaign_id,
                exc.message,
            )
    read = BrevoCampaignRead.model_validate(row)
    read.html_content = row.html_content_cached
    return read


@router.post(
    "/campaigns",
    response_model=BrevoCampaignRead,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign(
    payload: BrevoCampaignCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoCampaignRead:
    _require_brevo_account(session, payload.brevo_account_id)
    if not payload.html_content and not payload.template_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aporta html_content o template_id",
        )
    if not payload.list_ids and not payload.segment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aporta list_ids o segment_id",
        )

    list_ids = list(payload.list_ids or [])

    async def _create() -> dict[str, Any]:
        nonlocal list_ids
        async with BrevoClient(session, payload.brevo_account_id) as client:
            if payload.segment_id and not list_ids:
                # Materialise the CRM segment into a fresh Brevo list.
                segment = session.get(Segment, payload.segment_id)
                if segment is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Segment not found",
                    )
                from app.integrations.brevo.sync_targets import (  # noqa: PLC0415
                    resolve_target_contacts,
                )

                class _FakeTarget:
                    segment_id = payload.segment_id

                contacts = resolve_target_contacts(session, _FakeTarget())  # type: ignore[arg-type]
                stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                created_list = await client.create_list(
                    f"crm-campaign-{stamp}"
                )
                list_id = int(created_list.get("id"))
                emails = [c.email for c in contacts if c.email]
                for i in range(0, len(emails), 100):
                    await client.add_contacts_to_list(
                        list_id, emails[i : i + 100]
                    )
                list_ids = [list_id]

            body: dict[str, Any] = {
                "name": payload.name,
                "subject": payload.subject,
                "sender": {
                    "name": payload.sender_name,
                    "email": payload.sender_email,
                },
                "type": "classic",
                "recipients": {"listIds": list_ids},
                "inlineImageActivation": True,
                "mirrorActive": True,
            }
            if payload.reply_to:
                body["replyTo"] = payload.reply_to
            if payload.template_id:
                body["templateId"] = payload.template_id
            else:
                body["htmlContent"] = payload.html_content
            created = await client.create_email_campaign(body)
            # Scheduling is a SECOND call on the draft (documented
            # `PUT /emailCampaigns/{id}` with `scheduledAt`) instead of
            # riding on the create body. Production hit 405s on the
            # combined create; splitting means a scheduling rejection
            # leaves a usable draft behind instead of nothing, and the
            # error message can point at the scheduling step
            # specifically.
            if payload.scheduled_at:
                campaign_id = created.get("id")
                if campaign_id is not None:
                    await client.schedule_email_campaign(
                        int(campaign_id), payload.scheduled_at.isoformat()
                    )
            return created

    try:
        created = asyncio.run(_create())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_campaign_error_detail(exc),
        ) from exc

    row = campaigns_service.upsert_campaign_row(
        session,
        account_id=payload.brevo_account_id,
        payload={
            "id": created.get("id"),
            "name": payload.name,
            "subject": payload.subject,
            "status": "queued" if payload.scheduled_at else "draft",
            "type": "classic",
            "sender": {
                "name": payload.sender_name,
                "email": payload.sender_email,
            },
            "replyTo": payload.reply_to,
            "scheduledAt": (
                payload.scheduled_at.isoformat() if payload.scheduled_at else None
            ),
            "recipients": {"listIds": list_ids},
            "templateId": payload.template_id,
        },
    )
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_campaign",
        target_id=row.id,
        actor=current_user,
        metadata={
            "event": "campaign_created",
            "name": payload.name,
            "scheduled": payload.scheduled_at is not None,
        },
        request=request,
    )
    session.commit()
    session.refresh(row)
    return BrevoCampaignRead.model_validate(row)


@router.patch("/campaigns/{campaign_id}", response_model=BrevoCampaignRead)
def update_campaign(
    campaign_id: str,
    payload: BrevoCampaignUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> BrevoCampaignRead:
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)
    if row.status not in EDITABLE_CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"La campaña en estado '{row.status}' no es editable.",
        )
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return BrevoCampaignRead.model_validate(row)

    body: dict[str, Any] = {}
    if "name" in changes:
        body["name"] = changes["name"]
    if "subject" in changes:
        body["subject"] = changes["subject"]
    if "html_content" in changes:
        body["htmlContent"] = changes["html_content"]
    if "reply_to" in changes:
        body["replyTo"] = changes["reply_to"]
    if "sender_name" in changes or "sender_email" in changes:
        body["sender"] = {
            "name": changes.get("sender_name", row.sender_name),
            "email": changes.get("sender_email", row.sender_email),
        }

    async def _update() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.update_email_campaign(row.brevo_campaign_id, body)

    try:
        asyncio.run(_update())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    for key in ("name", "subject", "sender_name", "sender_email", "reply_to"):
        if key in changes:
            setattr(row, key, changes[key])
    row.cached_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return BrevoCampaignRead.model_validate(row)


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(
    campaign_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)

    async def _delete() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.delete_email_campaign(row.brevo_campaign_id)

    try:
        asyncio.run(_delete())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    session.delete(row)
    session.commit()
    return {"message": "Campaña eliminada"}


@router.post("/campaigns/{campaign_id}/send-now")
def send_campaign_now(
    campaign_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    row = _get_campaign_or_404(session, campaign_id)
    if row.status not in SENDABLE_CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Solo se puede enviar una campaña en draft o programada; "
                f"estado actual: '{row.status}'."
            ),
        )

    async def _send() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.send_email_campaign_now(row.brevo_campaign_id)

    try:
        asyncio.run(_send())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    row.status = "in_process"
    row.cached_at = datetime.now(UTC)
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_campaign",
        target_id=row.id,
        actor=current_user,
        metadata={"event": "campaign_send_now", "name": row.name},
        request=request,
    )
    session.commit()
    return {"message": "Campaña en proceso de envío"}


@router.post("/campaigns/{campaign_id}/schedule")
def schedule_campaign(
    campaign_id: str,
    payload: BrevoCampaignScheduleRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    row = _get_campaign_or_404(session, campaign_id)
    if row.status not in EDITABLE_CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede programar una campaña en estado '{row.status}'.",
        )
    scheduled_at = payload.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    if scheduled_at < datetime.now(UTC) + timedelta(hours=1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La programación debe ser al menos 1 hora en el futuro.",
        )

    async def _schedule() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.schedule_email_campaign(
                row.brevo_campaign_id, scheduled_at.isoformat()
            )

    try:
        asyncio.run(_schedule())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_campaign_error_detail(exc),
        ) from exc
    row.status = "queued"
    row.scheduled_at = scheduled_at
    row.cached_at = datetime.now(UTC)
    record_event(
        session,
        action=Action.INTEGRATION_SYNC_TRIGGERED,
        target_type="brevo_campaign",
        target_id=row.id,
        actor=current_user,
        metadata={
            "event": "campaign_scheduled",
            "name": row.name,
            "scheduled_at": scheduled_at.isoformat(),
        },
        request=request,
    )
    session.commit()
    return {"message": f"Campaña programada para {scheduled_at.isoformat()}"}


@router.post("/campaigns/{campaign_id}/cancel-schedule")
def cancel_campaign_schedule(
    campaign_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)
    if row.status != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solo se puede cancelar una campaña programada (queued).",
        )

    async def _cancel() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.update_campaign_status(row.brevo_campaign_id, "draft")

    try:
        asyncio.run(_cancel())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    row.status = "draft"
    row.scheduled_at = None
    row.cached_at = datetime.now(UTC)
    session.commit()
    return {"message": "Programación cancelada; la campaña vuelve a borrador"}


@router.post("/campaigns/{campaign_id}/send-test")
def send_campaign_test(
    campaign_id: str,
    payload: BrevoSendTestRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)

    async def _send() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.send_test_email_campaign(
                row.brevo_campaign_id, payload.emails
            )

    try:
        asyncio.run(_send())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return {"message": f"Test enviado a {', '.join(payload.emails)}"}


@router.get("/campaigns/{campaign_id}/timeline")
def campaign_timeline(
    campaign_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Per-day opens/clicks since the send plus the most-clicked URLs,
    aggregated from webhook-fed activity_events. Powers the detail
    page chart without touching the Brevo API."""
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)
    from sqlalchemy import func  # noqa: PLC0415

    from app.models.crm import ActivityEvent  # noqa: PLC0415

    since = row.sent_at or row.created_at_brevo
    statement = select(
        func.date(ActivityEvent.occurred_at),
        ActivityEvent.event_type,
        func.count(ActivityEvent.id),
    ).where(
        ActivityEvent.system == "brevo",
        ActivityEvent.account_id == row.brevo_account_id,
        ActivityEvent.event_type.in_(("email.opened", "email.clicked")),
    )
    if since is not None:
        statement = statement.where(ActivityEvent.occurred_at >= since)
    statement = statement.group_by(
        func.date(ActivityEvent.occurred_at), ActivityEvent.event_type
    ).order_by(func.date(ActivityEvent.occurred_at))

    days: dict[str, dict[str, int]] = {}
    for day, event_type, count in session.execute(statement):
        bucket = days.setdefault(str(day), {"opened": 0, "clicked": 0})
        key = "opened" if event_type == "email.opened" else "clicked"
        bucket[key] = int(count)

    clicks_statement = (
        select(ActivityEvent.body, func.count(ActivityEvent.id))
        .where(
            ActivityEvent.system == "brevo",
            ActivityEvent.account_id == row.brevo_account_id,
            ActivityEvent.event_type == "email.clicked",
            ActivityEvent.body.is_not(None),
        )
        .group_by(ActivityEvent.body)
        .order_by(func.count(ActivityEvent.id).desc())
        .limit(10)
    )
    if since is not None:
        clicks_statement = clicks_statement.where(
            ActivityEvent.occurred_at >= since
        )
    top_clicks = [
        {"url": url, "count": int(count)}
        for url, count in session.execute(clicks_statement)
    ]
    return {
        "timeline": [
            {"day": day, **counts} for day, counts in sorted(days.items())
        ],
        "top_clicks": top_clicks,
    }


@router.get("/campaigns/{campaign_id}/recipients/{event_type}")
def campaign_recipients_by_event(
    campaign_id: str,
    event_type: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Resolve the event recipients against CRM contacts. Backed by the
    webhook-fed activity_events (no Brevo round-trip): faster, richer
    (links straight to the contact page) and works offline from Brevo."""
    _ = current_user
    row = _get_campaign_or_404(session, campaign_id)
    allowed = {
        "delivered": "email.delivered",
        "opened": "email.opened",
        "clicked": "email.clicked",
        "bounces": ("email.bounced_hard", "email.bounced_soft"),
        "unsubscribed": "email.unsubscribed",
    }
    mapped = allowed.get(event_type)
    if mapped is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"event_type debe ser uno de {sorted(allowed)}",
        )
    from app.models.crm import ActivityEvent, Contact  # noqa: PLC0415

    types = mapped if isinstance(mapped, tuple) else (mapped,)
    # Primary filter is the indexed `campaign_brevo_id` column added
    # in migration 0025. Pre-0025 rows where the backfill couldn't
    # resolve a campaign id stay accessible through the legacy
    # `external_id LIKE 'backfill:{id}:%'` substring scan — that path
    # only ever matched historical-backfill writes, so adding it as a
    # belt-and-braces fallback doesn't change behaviour. The OR
    # short-circuits on the indexed equality whenever possible.
    backfill_prefix = f"backfill:{row.brevo_campaign_id}:%"
    statement = (
        select(ActivityEvent, Contact)
        .join(Contact, Contact.id == ActivityEvent.contact_id)
        .where(
            ActivityEvent.system == "brevo",
            ActivityEvent.account_id == row.brevo_account_id,
            ActivityEvent.event_type.in_(types),
            or_(
                ActivityEvent.campaign_brevo_id == row.brevo_campaign_id,
                and_(
                    ActivityEvent.campaign_brevo_id.is_(None),
                    ActivityEvent.external_id.like(backfill_prefix),
                ),
            ),
        )
        .order_by(ActivityEvent.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )
    results = session.execute(statement).all()
    return {
        "items": [
            {
                "contact_id": contact.id,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "detail": event.body,
            }
            for event, contact in results
        ],
        "limit": limit,
        "offset": offset,
    }
