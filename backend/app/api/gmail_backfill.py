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

import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
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
    EmailThread,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
)
from app.schemas.gmail_backfill import (
    BackfillEstimateRequest,
    BackfillExecuteRequest,
    BackfillJobRead,
    PerContactBatchRequest,
    PerContactBatchResponse,
    PerContactCandidatesResponse,
    PerContactRequest,
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
        config={
            "months_back": payload.months_back,
            "aliases_scope": payload.aliases_scope,
        },
        user=current_user,
    )
    record_event(
        session,
        action=Action.GMAIL_BACKFILL_ESTIMATED,
        target_type="gmail_backfill_job",
        target_id=job.id,
        actor=current_user,
        metadata={
            "months_back": payload.months_back,
            "aliases_scope": payload.aliases_scope,
        },
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
            "aliases_scope": payload.aliases_scope,
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
            "aliases_scope": payload.aliases_scope,
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


@router.get(
    "/admin/gmail/backfill",
    response_model=list[BackfillJobRead],
)
def gmail_backfill_list(
    limit: int = 10,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[BackfillJobRead]:
    """PR-Fix-Backfill-Gmail-Cero-Importados. Listado de los N jobs
    más recientes, ordenados por `created_at desc`. **Sin filtro por
    `initiated_by_user_id`** — admin debe poder ver y resumir polling
    de un job iniciado por otro user (caso real de Bart 2026-06-25:
    arrancó estimate, hizo logout/login, la UI no mostraba el job en
    marcha aunque seguía vivo via shell).

    El frontend hidrata su estado al montar la sección con los jobs
    `running` o `queued` para resumir el polling sin requerir el
    UUID original.
    """
    _ = current_user
    limit = max(1, min(int(limit), 50))
    rows = list(
        session.scalars(
            select(GmailBackfillJob)
            .order_by(GmailBackfillJob.created_at.desc())
            .limit(limit)
        )
    )
    return [_job_to_read(j) for j in rows]


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


@router.post(
    "/admin/gmail/backfill/{job_id}/force-fail",
    response_model=BackfillJobRead,
)
def gmail_backfill_force_fail(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackfillJobRead:
    """PR-Fix-Backfill-Gmail-Arquitectura. Marca un job colgado como
    `failed` con error_summary específico. Útil cuando el worker se
    cae sin terminar la transición de estado (caso del job
    `0c0d0859-...` reportado por Bart 2026-06-25) — limpia el row sin
    SSH al SQL. No hace falta esperar a un timeout. El job ya en
    estado terminal devuelve el row sin cambios (idempotente)."""
    job = session.get(GmailBackfillJob, job_id)
    if job is None:
        raise not_found("Gmail backfill job")
    if job.status in {
        GmailBackfillStatus.COMPLETED.value,
        GmailBackfillStatus.FAILED.value,
        GmailBackfillStatus.CANCELLED.value,
    }:
        # Idempotente — el operador re-ejecuta sin penalty.
        return _job_to_read(job)
    previous_status = job.status
    job.status = GmailBackfillStatus.FAILED.value
    job.error_summary = (
        f"Forced fail by admin (previous_status={previous_status})."
    )
    job.finished_at = datetime.now(UTC)
    record_event(
        session,
        action=Action.GMAIL_BACKFILL_CANCELLED,
        target_type="gmail_backfill_job",
        target_id=job.id,
        actor=current_user,
        metadata={
            "previous_status": previous_status,
            "force_fail": True,
        },
        request=request,
    )
    session.commit()
    return _job_to_read(job)


# ---------------------------------------------------------------------------
# PR-Auto-Backfill-Gmail-Por-Contacto — batch + individual + candidates
# ---------------------------------------------------------------------------


def _candidate_contact_ids(
    session: Session, *, since: datetime
) -> list[str]:
    """Contactos con email, creados a partir de `since`, que NO tienen
    ningún `email_messages` con `imported_via='per_contact_backfill'`.

    Es la lista que el banner ofrece importar tras un sync masivo."""
    from app.integrations.gmail.backfill import (  # noqa: PLC0415
        IMPORTED_VIA_PER_CONTACT,
    )

    already = select(EmailMessage.contact_id).where(
        EmailMessage.imported_via == IMPORTED_VIA_PER_CONTACT,
        EmailMessage.contact_id.is_not(None),
    )
    rows = session.scalars(
        select(Contact.id)
        .where(
            Contact.email.is_not(None),
            Contact.is_active.is_(True),
            Contact.created_at >= since,
            Contact.id.not_in(already),
        )
        .order_by(Contact.created_at.desc())
    )
    return list(rows)


@router.get(
    "/admin/gmail/backfill-per-contact/candidates",
    response_model=PerContactCandidatesResponse,
)
def gmail_per_contact_candidates(
    hours: int = 24,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> PerContactCandidatesResponse:
    """Banner admin: contactos recientes sin histórico Gmail importado.
    `hours` controla la ventana (default 24h tras el último sync)."""
    _ = current_user
    hours = max(1, min(int(hours), 24 * 30))
    since = datetime.now(UTC) - timedelta(hours=hours)
    ids = _candidate_contact_ids(session, since=since)
    return PerContactCandidatesResponse(count=len(ids), contact_ids=ids)


@router.post(
    "/admin/gmail/backfill-per-contact-batch",
    response_model=PerContactBatchResponse,
)
def gmail_per_contact_batch(
    payload: PerContactBatchRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> PerContactBatchResponse:
    """Encola `gmail:backfill_per_contact` para cada contact_id. Body:
    `contact_ids` explícito o `since_created_at` para coger todos los
    candidatos desde esa fecha."""
    from app.integrations.gmail.backfill import (  # noqa: PLC0415
        enqueue_backfill_per_contact,
    )

    if payload.contact_ids:
        # Filtramos a los que existen + tienen email, para no encolar
        # jobs que harán return inmediato.
        contact_ids = list(
            session.scalars(
                select(Contact.id).where(
                    Contact.id.in_(payload.contact_ids),
                    Contact.email.is_not(None),
                )
            )
        )
    elif payload.since_created_at is not None:
        contact_ids = _candidate_contact_ids(
            session, since=payload.since_created_at
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica contact_ids o since_created_at.",
        )

    for cid in contact_ids:
        enqueue_backfill_per_contact(
            cid,
            months_back=payload.months_back,
            triggered_by_user_id=current_user.id,
        )
    logger.info(
        "gmail.per_contact_backfill batch queued=%d by=%s",
        len(contact_ids), current_user.id,
    )
    return PerContactBatchResponse(queued=len(contact_ids))


@router.post(
    "/contacts/{contact_id}/gmail-backfill",
    response_model=PerContactBatchResponse,
)
def gmail_per_contact_single(
    contact_id: str,
    payload: PerContactRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> PerContactBatchResponse:
    """Acción de la ficha de contacto: "Importar histórico de Gmail".
    Visible para todos los users. Encola el mini-backfill de ESE
    contacto."""
    from app.integrations.gmail.backfill import (  # noqa: PLC0415
        enqueue_backfill_per_contact,
    )

    contact = session.get(Contact, contact_id)
    if contact is None:
        raise not_found("Contact")
    if not contact.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El contacto no tiene email — no se puede buscar histórico.",
        )
    enqueue_backfill_per_contact(
        contact_id,
        months_back=payload.months_back,
        triggered_by_user_id=current_user.id,
    )
    return PerContactBatchResponse(queued=1)


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
) -> Response:
    """Sirve el binario del adjunto. Dos rutas:

    - `storage_path` en disco (legacy, backfill junio) → FileResponse.
    - metadata-only (CRM-ADJUNTOS-BACKFILL, Opción B) → fetch on-demand a
      Gmail y stream directo al navegador; el binario nunca toca disco.

    Permisos (CRM-ADJUNTOS-UX): heredan la VISIBILIDAD DEL THREAD, no el
    owner del contacto. Si el operador ve el email en su bandeja (thread
    con un mensaje entregado a uno de sus alias, o iniciado por él; admin
    ve todo) puede descargar el adjunto. Cada download emite
    `email.attachment.downloaded` en audit log (metadata.source =
    local_disk | gmail_on_demand)."""
    attachment = session.get(EmailMessageAttachment, attachment_id)
    if attachment is None or attachment.message_id != message_id:
        raise not_found("Attachment")
    message = session.get(EmailMessage, message_id)
    if message is None:
        raise not_found("Email message")

    # Authorization: misma regla que ver el hilo (thread_is_visible). El
    # check anterior miraba el owner del contacto, que niega a un comercial
    # que SÍ ve el mail por alias pero cuyo contacto pertenece a otro (o es
    # NULL) — el bug reportado por Bart.
    from app.services.email_aliases import (  # noqa: PLC0415
        thread_is_visible,
    )

    thread = session.get(EmailThread, message.thread_id)
    if thread is None or not thread_is_visible(session, current_user, thread):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "attachment_not_visible",
                "detail": (
                    "No tienes acceso a este email. Pide al admin que te "
                    "asigne el alias correspondiente."
                ),
            },
        )

    def _audit(source: str) -> None:
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
                "source": source,
            },
            request=request,
        )
        session.commit()

    # Ruta legacy: binario en disco (backfill junio con incluir_adjuntos).
    if attachment.storage_path:
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
        _audit("local_disk")
        return FileResponse(
            path=str(full_path),
            media_type=attachment.mime_type or "application/octet-stream",
            filename=attachment.filename,
        )

    # CRM-ADJUNTOS-BACKFILL (Opción B): metadata-only → fetch on-demand
    # desde Gmail. El binario se streamea al navegador sin tocar disco.
    if not attachment.gmail_attachment_id or not message.gmail_message_id:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="El binario de este adjunto no está disponible.",
        )
    binary = _fetch_attachment_from_gmail(session, message, attachment)
    _audit("gmail_on_demand")
    quoted_name = quote(attachment.filename)
    return Response(
        content=binary,
        media_type=attachment.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quoted_name}"
            ),
        },
    )


def _fetch_attachment_from_gmail(
    session: Session,
    message: EmailMessage,
    attachment: EmailMessageAttachment,
) -> bytes:
    """Descarga on-demand del binario vía `messages.attachments.get`.

    Los `attachmentId` de Gmail NO son estables a largo plazo: el que se
    guardó en el backfill puede haber caducado. Si Gmail responde 404 con
    el id guardado, re-pedimos el mensaje, localizamos la parte por
    filename+size, refrescamos el id en BD y reintentamos una vez. Si el
    mensaje ya no existe en Gmail (papelera vaciada), 410 — trade-off
    aceptado de la Opción B."""
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        _client_for,
    )

    try:
        client = _client_for(session, message.gmail_account_user_id)
    except GmailNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail no está conectado — no se puede descargar el adjunto.",
        ) from exc

    def _status_of(exc: BaseException) -> int | None:
        return getattr(getattr(exc, "resp", None), "status", None)

    gmail_message_id = message.gmail_message_id or ""
    try:
        resp = client.get_attachment(
            message_id=gmail_message_id,
            attachment_id=attachment.gmail_attachment_id or "",
        )
        return base64.urlsafe_b64decode((resp.get("data") or "").encode())
    except Exception as exc:  # noqa: BLE001
        if _status_of(exc) != 404:
            logger.warning(
                "adjuntos.on_demand fallo att=%s mid=%s: %s",
                attachment.id, gmail_message_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gmail no respondió al descargar el adjunto. Reintenta.",
            ) from exc

    # 404 → attachmentId caducado o mensaje borrado. Refrescamos el id.
    from app.integrations.gmail.backfill_attachments import (  # noqa: PLC0415
        extract_attachments_from_gmail_payload,
    )

    try:
        raw = client.get_message(gmail_message_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "El mensaje ya no existe en Gmail — el adjunto no se puede "
                "recuperar."
            ),
        ) from exc

    fresh = extract_attachments_from_gmail_payload(raw.get("payload"))
    match = next(
        (
            a for a in fresh
            if a["filename"] == attachment.filename
            and a["size"] == (attachment.size_bytes or a["size"])
        ),
        None,
    ) or next(
        (a for a in fresh if a["filename"] == attachment.filename), None
    )
    if match is None or not match.get("gmail_attachment_id"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="El adjunto ya no existe en el mensaje de Gmail.",
        )
    try:
        resp = client.get_attachment(
            message_id=gmail_message_id,
            attachment_id=match["gmail_attachment_id"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gmail no respondió al descargar el adjunto. Reintenta.",
        ) from exc
    # Persistimos el id fresco para la próxima descarga.
    attachment.gmail_attachment_id = match["gmail_attachment_id"]
    session.commit()
    return base64.urlsafe_b64decode((resp.get("data") or "").encode())
