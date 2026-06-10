"""HTTP surface for the Brevo integration: sync targets, list/sender
proxies, templates and campaigns.

Auth: read endpoints accept any authenticated user; mutations require
manager+ (same policy as the integration accounts admin)."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_manager, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.brevo import templates as templates_service
from app.integrations.brevo.client import BrevoClient
from app.integrations.brevo.sync_targets import (
    run_brevo_target,
    schedule_heartbeat,
)
from app.integrations.errors import IntegrationError
from app.models.brevo import (
    BrevoSyncTarget,
    BrevoTemplateCache,
    SyncDirection,
    TargetRunStatus,
)
from app.models.crm import ExternalSystem, Segment, User
from app.models.integration_settings import IntegrationAccount
from app.schemas.brevo import (
    BrevoListRead,
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
    return [
        BrevoListRead(
            id=int(row.get("id")),
            name=str(row.get("name") or row.get("id")),
            total_subscribers=int(
                row.get("totalSubscribers") or row.get("uniqueSubscribers") or 0
            ),
            folder_id=row.get("folderId"),
        )
        for row in rows
        if row.get("id") is not None
    ]


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
    _ = current_user
    row = _get_template_or_404(session, template_id)

    async def _send() -> None:
        async with BrevoClient(session, row.brevo_account_id) as client:
            await client.send_test_template(row.brevo_template_id, payload.emails)

    try:
        asyncio.run(_send())
    except IntegrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    return {"message": f"Test enviado a {', '.join(payload.emails)}"}
