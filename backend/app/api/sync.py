"""Generic sync trigger + sync-logs listing for any integration account.

Routes live under `/integration-accounts/{system}/{account_id}` so they
share the prefix and audit metadata pattern of the multi-account CRUD.
"""
# ruff: noqa: I001
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_manager
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import ExternalSystem, SyncLog, SyncStatus, SyncTrigger, User
from app.models.integration_settings import IntegrationAccount
from app.repositories.integration_settings import get_integration_account
from app.workers.jobs import enqueue_sync_job, is_operation_registered

router = APIRouter(prefix="/integration-accounts", tags=["integration accounts"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class SyncTriggerRequest(BaseModel):
    operation: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] | None = None


class SyncTriggerResponse(BaseModel):
    sync_log_id: str
    job_id: str | None
    operation: str
    status: SyncStatus


class SyncLogRead(BaseModel):
    id: str
    system: ExternalSystem
    account_id: str | None
    operation: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    records_processed: int
    records_skipped: int
    records_failed: int
    error_summary: str | None
    triggered_by: str | None
    triggered_by_user_id: str | None
    job_id: str | None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_row(cls, row: SyncLog) -> SyncLogRead:
        meta: dict[str, Any] | None = None
        if row.metadata_json:
            try:
                parsed = json.loads(row.metadata_json)
                meta = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (ValueError, TypeError):
                meta = {"raw": row.metadata_json}
        return cls(
            id=row.id,
            system=row.system,
            account_id=row.account_id,
            operation=row.operation,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            records_processed=row.records_processed,
            records_skipped=row.records_skipped,
            records_failed=row.records_failed,
            error_summary=row.error_summary,
            triggered_by=row.triggered_by,
            triggered_by_user_id=row.triggered_by_user_id,
            job_id=row.job_id,
            metadata=meta,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


def _trigger(
    *,
    system: ExternalSystem,
    account_id: str,
    operation: str,
    request_payload: dict[str, Any] | None,
    request: Request,
    session: Session,
    current_user: User,
) -> SyncTriggerResponse:
    account = get_integration_account(session, system, account_id)
    if account is None:
        raise not_found("Integration account")
    if not is_operation_registered(system.value, operation):
        # The job would still enqueue and the worker would mark the
        # row as FAILED with a clear error, but we surface a 409 here
        # so the UI can keep "Sincronizar" disabled.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Operation '{operation}' is not implemented yet "
                f"for system '{system.value}'."
            ),
        )
    sync_log_id, job_id = enqueue_sync_job(
        session,
        system=system,
        account_id=account_id,
        operation=operation,
        triggered_by=SyncTrigger.MANUAL,
        triggered_by_user_id=current_user.id,
        payload=request_payload,
        request=request,
    )
    return SyncTriggerResponse(
        sync_log_id=sync_log_id,
        job_id=job_id,
        operation=operation,
        status=SyncStatus.PENDING,
    )


class SyncAllRequest(BaseModel):
    """Body opcional de `POST /_/sync-all`. `full_sync=True` pasa
    `payload={"full_sync": True}` al handler del worker, que se
    interpreta como "ignora la watermark de last finished_at y re-fetch
    todo el universo de Agile/Brevo/…". Útil tras un cambio del mapper
    para recuperar campos que no se materializaron en syncs delta
    anteriores (p.ej. las Note1..Note10 que Bart está rescatando)."""

    full_sync: bool = False


@router.post(
    "/_/sync-all",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync_all(
    request: Request,
    body: SyncAllRequest | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Lanza una sincronización manual a TODAS las cuentas habilitadas
    de TODOS los sistemas con un solo click. Crea N SyncLogs (uno por
    cuenta) y devuelve el resumen. Las cuentas deshabilitadas se
    ignoran silenciosamente.

    QoL post-Notes: si `full_sync=True` se pasa al body, los jobs
    encolados llevan `payload={"full_sync": True}` para forzar un
    re-fetch completo (ignorar watermark delta). El handler en
    `app/integrations/*/jobs.py` ya lo respeta — solo encolar con el
    flag puesto era lo que faltaba.
    """
    full_sync = bool(body and body.full_sync)
    accounts = list(
        session.scalars(
            select(IntegrationAccount).where(
                IntegrationAccount.enabled.is_(True)
            )
        )
    )
    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for acc in accounts:
        operation = _DEFAULT_READ_OPERATION.get(acc.system.value)
        if not operation:
            skipped.append(
                {
                    "system": acc.system.value,
                    "account_id": acc.account_id,
                    "reason": "no_default_operation",
                }
            )
            continue
        if not is_operation_registered(acc.system.value, operation):
            skipped.append(
                {
                    "system": acc.system.value,
                    "account_id": acc.account_id,
                    "reason": "operation_not_registered",
                }
            )
            continue
        try:
            sync_log_id, job_id = enqueue_sync_job(
                session,
                system=acc.system,
                account_id=acc.account_id,
                operation=operation,
                triggered_by=SyncTrigger.MANUAL,
                triggered_by_user_id=current_user.id,
                payload={"full_sync": True} if full_sync else None,
                request=request,
            )
            enqueued.append(
                {
                    "system": acc.system.value,
                    "account_id": acc.account_id,
                    "sync_log_id": sync_log_id,
                    "job_id": job_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep siblings alive
            skipped.append(
                {
                    "system": acc.system.value,
                    "account_id": acc.account_id,
                    "reason": "enqueue_failed",
                    "error": str(exc),
                }
            )
    return {
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
        "full_sync": full_sync,
        "enqueued": enqueued,
        "skipped": skipped,
    }


_DEFAULT_READ_OPERATION: dict[str, str] = {
    "brevo": "sync_contacts",
    "agilecrm": "sync_contacts",
    "freshdesk": "sync_contacts",
    "factusol": "sync_customers",
}


@router.post(
    "/{system}/{account_id}/sync",
    response_model=SyncTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync(
    system: ExternalSystem,
    account_id: str,
    payload: SyncTriggerRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> SyncTriggerResponse:
    return _trigger(
        system=system,
        account_id=account_id,
        operation=payload.operation,
        request_payload=payload.payload,
        request=request,
        session=session,
        current_user=current_user,
    )


@router.post(
    "/{system}/{account_id}/sync/{operation}",
    response_model=SyncTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_sync_operation(
    system: ExternalSystem,
    account_id: str,
    operation: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> SyncTriggerResponse:
    """Path-based variant of `POST /sync` for operators triggering a
    specific operation directly (e.g. `purge_quota`) without crafting a
    JSON body. No payload may be passed this way; callers that need
    extra context use the body variant above."""
    return _trigger(
        system=system,
        account_id=account_id,
        operation=operation,
        request_payload=None,
        request=request,
        session=session,
        current_user=current_user,
    )


@router.get(
    "/{system}/{account_id}/sync-logs",
    response_model=list[SyncLogRead],
)
def list_sync_logs(
    system: ExternalSystem,
    account_id: str,
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status_filter: SyncStatus | None = Query(default=None, alias="status"),
    operation: str | None = Query(default=None),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[SyncLogRead]:
    _ = current_user
    statement = select(SyncLog).where(
        SyncLog.system == system, SyncLog.account_id == account_id
    )
    if status_filter is not None:
        statement = statement.where(SyncLog.status == status_filter.value)
    if operation:
        statement = statement.where(SyncLog.operation == operation)
    if from_date is not None:
        statement = statement.where(SyncLog.created_at >= from_date)
    if to_date is not None:
        statement = statement.where(SyncLog.created_at <= to_date)
    total = int(
        session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    response.headers["X-Total-Count"] = str(total)
    statement = statement.order_by(SyncLog.created_at.desc()).offset(skip).limit(limit)
    rows = list(session.scalars(statement))
    return [SyncLogRead.from_orm_row(row) for row in rows]


@router.get(
    "/{system}/{account_id}/sync-logs/{log_id}",
    response_model=SyncLogRead,
)
def get_sync_log(
    system: ExternalSystem,
    account_id: str,
    log_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> SyncLogRead:
    _ = current_user
    row = session.scalar(
        select(SyncLog).where(
            SyncLog.id == log_id,
            SyncLog.system == system,
            SyncLog.account_id == account_id,
        )
    )
    if row is None:
        raise not_found("Sync log")
    return SyncLogRead.from_orm_row(row)
