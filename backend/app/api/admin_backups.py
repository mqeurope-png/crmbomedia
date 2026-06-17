"""Sprint Backup. Endpoints admin para gestionar los backups.

Mounted at `/api/admin/backups`. Todo el surface es admin-only —
`require_admin` rechaza manager, user, viewer.

Endpoints:
    GET  /api/admin/backups               — lista, paginada simple.
    POST /api/admin/backups/create        — encola backup manual.
    GET  /api/admin/backups/{id}/download — stream del .tar.gz.gpg.
    DELETE /api/admin/backups/{id}        — borra archivo + row.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backups.service import enqueue_manual_backup
from app.core.audit import Action, record_event
from app.core.auth import require_admin
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import Backup, BackupStatus, User
from app.schemas.backups import BackupCreateResponse, BackupRead

router = APIRouter(prefix="/api/admin/backups", tags=["admin-backups"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[BackupRead])
def list_backups(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[BackupRead]:
    """Lista todos los backups conocidos, más reciente primero. No
    pagina — la rotación FIFO mantiene 3 archivos en VPS, así que la
    UI nunca renderiza miles de filas. El histórico de rows borradas
    sí queda (la rotación solo borra el binario), pero a un par de
    centenares por año cabe en una sola fetch."""
    _ = current_user
    rows = list(
        session.scalars(
            select(Backup).order_by(Backup.started_at.desc())
        )
    )
    return [BackupRead.model_validate(r) for r in rows]


@router.post(
    "/create",
    response_model=BackupCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_backup(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> BackupCreateResponse:
    """Encola un backup manual. Devuelve `(backup_id, job_id)`
    inmediatamente; la UI hace polling sobre `/api/admin/backups` para
    ver la transición running → success/failed.

    Concurrencia: si ya hay un backup `RUNNING` rechazamos con 409.
    Correr dos mysqldumps en paralelo bloquea el InnoDB durante
    minutos y satura la red de rclone — no merece la pena."""
    running = session.scalar(
        select(Backup).where(Backup.status == BackupStatus.RUNNING.value)
    )
    if running is not None:
        # Si el "running" lleva más de 1 h colgado, asumimos zombie y
        # lo limpiamos. Esto cubre el caso de un worker muerto en
        # mitad del job, evita atrancar la UI.
        age = datetime.now(UTC) - (
            running.started_at
            if running.started_at.tzinfo
            else running.started_at.replace(tzinfo=UTC)
        )
        if age.total_seconds() > 60 * 60:
            running.status = BackupStatus.FAILED.value
            running.error_summary = (
                "Marcado como FAILED por timeout (sin transición tras 1 h). "
                "Probable worker muerto antes de poder cerrar la row."
            )
            running.finished_at = datetime.now(UTC)
            session.commit()
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Ya hay un backup en curso. Espera a que termine antes "
                    "de disparar uno nuevo."
                ),
            )

    backup_id, job_id = enqueue_manual_backup(session, user_id=current_user.id)
    record_event(
        session,
        action=Action.BACKUP_TRIGGERED,
        target_type="backup",
        target_id=backup_id,
        actor=current_user,
        metadata={"triggered_by": "manual", "job_id": job_id},
        request=request,
    )
    session.commit()
    return BackupCreateResponse(
        backup_id=backup_id,
        job_id=job_id,
        status=BackupStatus.RUNNING.value,  # type: ignore[arg-type]
    )


# Sprint Backup-Hardening. Cache-Control NO-store en TODA respuesta
# del download — sin ello el navegador cachea agresivamente:
#   - Un 410 (archivo rotado) se sirve desde caché durante varios
#     minutos aunque el backup esté de vuelta.
#   - Un 200 con `Content-Disposition: attachment` se reusa para el
#     siguiente backup distinto (mismo URL pattern), entregando el
#     binario equivocado.
# El header lo aplicamos en TODAS las salidas (FileResponse + las
# excepciones HTTP) para que el browser nunca cachee este endpoint.
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@router.get("/{backup_id}/download")
def download_backup(
    backup_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> FileResponse:
    """Stream del archivo `.tar.gz.gpg` al navegador. La passphrase
    NO se transmite — el admin la descifra local con `gpg --decrypt`
    siguiendo `docs/backup-restore.md`."""
    _ = current_user
    backup = session.get(Backup, backup_id)
    if backup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup not found.",
            headers=_NO_STORE_HEADERS,
        )
    if backup.status != BackupStatus.SUCCESS.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Este backup no terminó con éxito; no hay archivo para "
                "descargar."
            ),
            headers=_NO_STORE_HEADERS,
        )
    path = Path(backup.filepath)
    if not path.exists():
        # La row sobrevive pero el binario se borró (rotación,
        # `rm` manual, fallo de disco). Devolvemos 410 para que la UI
        # sepa que el row está obsoleto y pueda limpiarlo.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                f"El archivo {backup.filename} ya no existe en disco. "
                "Probable rotación FIFO (3 más recientes). Mira tu Google "
                "Drive si necesitas uno antiguo."
            ),
            headers=_NO_STORE_HEADERS,
        )
    return FileResponse(
        path=str(path),
        filename=backup.filename,
        media_type="application/octet-stream",
        headers=_NO_STORE_HEADERS,
    )


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(
    backup_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    """Borra row + archivo en disco. No toca Google Drive — el binario
    de Drive es la copia off-site, queda intacto a propósito (Bart lo
    elimina manualmente si quiere reclamar espacio)."""
    backup = session.get(Backup, backup_id)
    if backup is None:
        raise not_found("Backup")
    path = Path(backup.filepath) if backup.filepath else None
    if path is not None and path.exists():
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning(
                "backup.delete file_remove_failed id=%s path=%s err=%s",
                backup_id,
                path,
                exc,
            )
    session.delete(backup)
    record_event(
        session,
        action=Action.BACKUP_DELETED,
        target_type="backup",
        target_id=backup_id,
        actor=current_user,
        metadata={"filename": backup.filename},
        request=request,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
