"""Sprint-Backfill-Gmail — admin endpoints + attachment download.

5 endpoints:

- `POST /api/admin/gmail/backfill/estimate` — crea row mode=estimate,
  encola, devuelve row para polling.
- `POST /api/admin/gmail/backfill/execute` — crea row mode=execute,
  encola, devuelve row.
- `GET  /api/admin/gmail/backfill/{job_id}` — poll status + progreso.
- `POST /api/admin/gmail/backfill/{job_id}/cancel` — flag
  cancelling, worker termina limpio.
- `GET  /api/email-messages/{message_id}/attachments/{attachment_id}/download`
  — sirve el binario del adjunto descargado.

El estimate y el execute usan la misma cola; el endpoint de download
no encola, solo lee del disco."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.gmail.backfill import (
    ATTACHMENT_ROOT,
    enqueue_backfill,
)
from app.models.crm import (
    Contact,
    EmailMessage,
    EmailMessageAttachment,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
    UserRole,
)
from app.schemas.gmail_backfill import (
    BackfillEstimateRequest,
    BackfillExecuteRequest,
    BackfillJobRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["gmail-backfill"])


def _job_to_read(job: GmailBackfillJob) -> BackfillJobRead:
    config = json.loads(job.config_json) if job.config_json else None
    result = json.loads(job.result_json) if job.result_json else None
    return BackfillJobRead(
        id=job.id,
        mode=job.mode,
        status=job.status,
        initiated_by_user_id=job.initiated_by_user_id,
        total_estimated=job.total_estimated,
        total_processed=job.total_processed,
        total_imported=job.total_imported,
        total_skipped=job.total_skipped,
        total_errors=job.total_errors,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_summary=job.error_summary,
        config=config,
        result=result,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _create_job(
    session: Session,
    *,
    mode: GmailBackfillMode,
    config: dict,
    user: User,
) -> GmailBackfillJob:
    now = datetime.now(UTC)
    job = GmailBackfillJob(
        mode=mode.value,
        status=GmailBackfillStatus.QUEUED.value,
        initiated_by_user_id=user.id,
        config_json=json.dumps(config),
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


@router.post(
    "/admin/gmail/backfill/estimate",
    response_model=BackfillJobRead,
)
def gmail_backfill_estimate(
    payload: BackfillEstimateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackfillJobRead:
    """Crea un job en modo `estimate`. La UI poll
    `GET /api/admin/gmail/backfill/{job_id}` hasta que `status` sea
    terminal y `result` muestre el desglose `per_user_breakdown`."""
    job = _create_job(
        session,
        mode=GmailBackfillMode.ESTIMATE,
        config={"months_back": payload.months_back},
        user=current_user,
    )
    record_event(
        session,
        action=Action.GMAIL_BACKFILL_ESTIMATED,
        target_type="gmail_backfill_job",
        target_id=job.id,
        actor=current_user,
        metadata={"months_back": payload.months_back},
        request=request,
    )
    session.commit()
    try:
        enqueue_backfill(job.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.backfill.estimate enqueue failed: %s", exc)
    return _job_to_read(job)


@router.post(
    "/admin/gmail/backfill/execute",
    response_model=BackfillJobRead,
)
def gmail_backfill_execute(
    payload: BackfillExecuteRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackfillJobRead:
    """Crea un job en modo `execute` con la config del admin (months_back,
    include_attachments, max_attachment_size_mb). El worker procesa
    todo el universo (users con Gmail × aliases × contactos) en
    `gmail:backfill_historic`."""
    job = _create_job(
        session,
        mode=GmailBackfillMode.EXECUTE,
        config={
            "months_back": payload.months_back,
            "include_attachments": payload.include_attachments,
            "max_attachment_size_mb": payload.max_attachment_size_mb,
        },
        user=current_user,
    )
    record_event(
        session,
        action=Action.GMAIL_BACKFILL_TRIGGERED,
        target_type="gmail_backfill_job",
        target_id=job.id,
        actor=current_user,
        metadata={
            "months_back": payload.months_back,
            "include_attachments": payload.include_attachments,
            "max_attachment_size_mb": payload.max_attachment_size_mb,
        },
        request=request,
    )
    session.commit()
    try:
        enqueue_backfill(job.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail.backfill.execute enqueue failed: %s", exc)
    return _job_to_read(job)


@router.get(
    "/admin/gmail/backfill/{job_id}",
    response_model=BackfillJobRead,
)
def gmail_backfill_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackfillJobRead:
    _ = current_user
    job = session.get(GmailBackfillJob, job_id)
    if job is None:
        raise not_found("Gmail backfill job")
    return _job_to_read(job)


@router.post(
    "/admin/gmail/backfill/{job_id}/cancel",
    response_model=BackfillJobRead,
)
def gmail_backfill_cancel(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackfillJobRead:
    """Set status='cancelling'. El worker lo lee en el siguiente
    chequeo (cada 100 mensajes) y finaliza limpio. Si el job ya está
    en estado terminal, 409."""
    job = session.get(GmailBackfillJob, job_id)
    if job is None:
        raise not_found("Gmail backfill job")
    if job.status in {
        GmailBackfillStatus.COMPLETED.value,
        GmailBackfillStatus.FAILED.value,
        GmailBackfillStatus.CANCELLED.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job ya está en estado terminal: {job.status}",
        )
    job.status = GmailBackfillStatus.CANCELLING.value
    record_event(
        session,
        action=Action.GMAIL_BACKFILL_CANCELLED,
        target_type="gmail_backfill_job",
        target_id=job.id,
        actor=current_user,
        metadata={"previous_status": job.status},
        request=request,
    )
    session.commit()
    return _job_to_read(job)


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------


@router.get(
    "/email-messages/{message_id}/attachments/{attachment_id}/download",
)
def download_attachment(
    message_id: str,
    attachment_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> FileResponse:
    """Sirve el binario desde disco. Permisos: el contacto del mensaje
    debe ser accesible al user actual (admin/manager pueden todos;
    user/viewer solo si son el owner del contacto). Cada download
    emite `email.attachment.downloaded` en audit log."""
    attachment = session.get(EmailMessageAttachment, attachment_id)
    if attachment is None or attachment.message_id != message_id:
        raise not_found("Attachment")
    message = session.get(EmailMessage, message_id)
    if message is None:
        raise not_found("Email message")

    # Authorization: admin/manager → cualquier contacto.
    # user/viewer → solo si son el owner del contacto.
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        contact = (
            session.get(Contact, message.contact_id)
            if message.contact_id else None
        )
        if contact is None or contact.owner_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso sobre el contacto de este adjunto.",
            )

    if not attachment.storage_path:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="El binario de este adjunto no está disponible en disco.",
        )
    full_path = (ATTACHMENT_ROOT / attachment.storage_path).resolve()
    # Defensa contra path traversal: storage_path debe estar bajo root.
    try:
        full_path.relative_to(ATTACHMENT_ROOT.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Storage path inválido.",
        ) from None
    if not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="El archivo del adjunto no existe en disco.",
        )

    record_event(
        session,
        action=Action.EMAIL_ATTACHMENT_DOWNLOADED,
        target_type="email_message_attachment",
        target_id=attachment.id,
        actor=current_user,
        metadata={
            "filename": attachment.filename,
            "size_bytes": attachment.size_bytes,
            "message_id": message_id,
            "contact_id": message.contact_id,
        },
        request=request,
    )
    session.commit()

    return FileResponse(
        path=str(full_path),
        media_type=attachment.mime_type or "application/octet-stream",
        filename=attachment.filename,
    )
