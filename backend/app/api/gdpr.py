# ruff: noqa: I001
"""Admin-only HTTP layer for GDPR / RGPD subject-rights tracking.

The endpoints record requests and dispatch processing through
`app.services.gdpr`. Every state-changing call audits a `gdpr.*` event so
the audit log is the canonical compliance trail.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin
from app.core.config import Settings, get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import GdprRequest, GdprRequestStatus, GdprRequestType, User
from app.schemas.crm import (
    GdprProcessResult,
    GdprRequestCreate,
    GdprRequestRead,
    GdprRequestUpdate,
)
from app.services import gdpr as gdpr_service

router = APIRouter(prefix="/gdpr", tags=["gdpr"])


@router.post(
    "/requests",
    response_model=GdprRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def create_gdpr_request(
    payload: GdprRequestCreate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> GdprRequest:
    subject_email = str(payload.subject_email).lower()
    gdpr_request = GdprRequest(
        subject_email=subject_email,
        request_type=payload.request_type,
        status=GdprRequestStatus.PENDING,
        requester_user_id=current_user.id,
        notes=payload.notes,
    )
    session.add(gdpr_request)
    session.flush()
    record_event(
        session,
        action=Action.GDPR_REQUEST_CREATED,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=current_user,
        metadata={
            "subject_email": subject_email,
            "request_type": payload.request_type.value,
        },
        request=request,
    )
    session.commit()
    session.refresh(gdpr_request)
    return gdpr_request


@router.get("/requests", response_model=list[GdprRequestRead])
def list_gdpr_requests(
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status_filter: GdprRequestStatus | None = Query(default=None, alias="status"),
    request_type: GdprRequestType | None = Query(default=None),
    subject_email: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[GdprRequest]:
    _ = current_user
    statement = select(GdprRequest)
    if status_filter is not None:
        statement = statement.where(GdprRequest.status == status_filter)
    if request_type is not None:
        statement = statement.where(GdprRequest.request_type == request_type)
    if subject_email:
        statement = statement.where(GdprRequest.subject_email == subject_email.lower())
    total = int(
        session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    response.headers["X-Total-Count"] = str(total)
    statement = (
        statement.order_by(GdprRequest.requested_at.desc()).offset(skip).limit(limit)
    )
    return list(session.scalars(statement))


@router.get("/requests/{request_id}", response_model=GdprRequestRead)
def get_gdpr_request(
    request_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> GdprRequest:
    _ = current_user
    gdpr_request = session.get(GdprRequest, request_id)
    if not gdpr_request:
        raise not_found("GDPR request")
    return gdpr_request


@router.patch("/requests/{request_id}", response_model=GdprRequestRead)
def update_gdpr_request(
    request_id: str,
    payload: GdprRequestUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> GdprRequest:
    gdpr_request = session.get(GdprRequest, request_id)
    if not gdpr_request:
        raise not_found("GDPR request")
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is not None:
        gdpr_request.status = changes["status"]
        if changes["status"] == GdprRequestStatus.COMPLETED and not gdpr_request.completed_at:
            gdpr_request.completed_at = datetime.now(UTC)
    if "notes" in changes:
        gdpr_request.notes = changes["notes"]
    record_event(
        session,
        action=Action.GDPR_REQUEST_UPDATED,
        target_type="gdpr_request",
        target_id=gdpr_request.id,
        actor=current_user,
        metadata={
            "subject_email": gdpr_request.subject_email,
            "request_type": gdpr_request.request_type.value,
            "changed_fields": sorted(changes.keys()),
            "status": gdpr_request.status.value,
        },
        request=request,
    )
    session.commit()
    session.refresh(gdpr_request)
    return gdpr_request


@router.post("/requests/{request_id}/process", response_model=GdprProcessResult)
def process_gdpr_request(
    request_id: str,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(require_admin),
) -> GdprProcessResult:
    gdpr_request = session.get(GdprRequest, request_id)
    if not gdpr_request:
        raise not_found("GDPR request")
    try:
        payload = gdpr_service.process_request(
            session,
            gdpr_request=gdpr_request,
            actor=current_user,
            settings=settings,
            request=request,
        )
    except gdpr_service.GdprProcessingError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    session.commit()
    session.refresh(gdpr_request)
    return GdprProcessResult(
        request_id=gdpr_request.id,
        request_type=gdpr_request.request_type,
        status=gdpr_request.status,
        evidence_path=gdpr_request.evidence_path,
        payload=payload,
    )
