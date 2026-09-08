"""ERP-F4-A — API de conciliación bancaria. NO escribe en FACTUSOL.

Permisos: ver (`require_erp_view`), decidir/importar (`require_erp_edit` —
conciliar es contable), gestionar cuentas (`require_erp_admin`).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_erp_admin, require_erp_edit, require_erp_view
from app.erp.bank import service
from app.erp.bank.parsing import ParseError
from app.models.crm import User

router = APIRouter(prefix="/api/erp/bank", tags=["erp-bank"])

_MAX_UPLOAD = 25 * 1024 * 1024


def _factusol(session: Session):
    from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415

    return _client_and_ejercicio(session)


# --- cuentas ---------------------------------------------------------------------


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    iban: str = Field(min_length=15, max_length=40)
    bank_name: str | None = Field(default=None, max_length=120)
    bic: str | None = Field(default=None, max_length=11)
    currency: str = Field(default="EUR", max_length=3)
    serie: int | None = Field(default=None, ge=1, le=9)
    column_mapping: dict[str, str] | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    iban: str | None = Field(default=None, max_length=40)
    bank_name: str | None = Field(default=None, max_length=120)
    bic: str | None = Field(default=None, max_length=11)
    currency: str | None = Field(default=None, max_length=3)
    serie: int | None = Field(default=None, ge=1, le=9)
    column_mapping: dict[str, str] | None = None


@router.get("/accounts")
def list_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    return {"items": service.list_accounts(session)}


@router.get("/accounts/suggested")
def suggested_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Las 2 cuentas de F_BAN como SUGERENCIA (no se imponen)."""
    _ = current_user
    try:
        client, ejercicio = _factusol(session)
    except Exception:  # noqa: BLE001 — sin FACTUSOL, sin sugerencias
        return {"items": []}
    known = {a["iban"] for a in service.list_accounts(session)}
    items = [
        s for s in service.suggested_accounts_from_fban(client, ejercicio) if s["iban"] not in known
    ]
    return {"items": items}


@router.post("/accounts", status_code=201)
def create_account(
    payload: AccountIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    try:
        return service.account_to_dict(service.create_account(session, payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "account_invalid", "detail": str(exc)}
        ) from exc


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    payload: AccountPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user
    try:
        return service.account_to_dict(
            service.update_account(session, account_id, payload.model_dump(exclude_unset=True)),
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> Response:
    _ = current_user
    try:
        service.delete_account(session, account_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=204)


# --- importación + casado -------------------------------------------------------------


@router.post("/import", status_code=201)
async def import_statement(
    file: UploadFile = File(...),
    account_id: str | None = Form(default=None),
    run_match: bool = Form(default=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Sube el extracto (.xlsx/.csv). Identifica la cuenta por el IBAN de la
    cabecera (avisa si no está dada de alta), deduplica y, si `run_match`,
    genera las PROPUESTAS. Nunca confirma nada."""
    _ = current_user
    content = await file.read()
    if not content or len(content) > _MAX_UPLOAD:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "file_size",
                "detail": "El fichero debe pesar entre 1 byte y 25 MB.",
            },
        )
    try:
        summary = service.import_statement(
            session,
            content=content,
            filename=file.filename or "extracto",
            account_id=account_id,
        )
    except ParseError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "parse_error",
                "row": exc.row,
                "detail": str(exc),
            },
        ) from exc
    if not summary.get("ok"):
        # Cuenta desconocida / IBAN distinto: se informa, no se importa a ciegas.
        raise HTTPException(status.HTTP_409_CONFLICT, summary)
    if run_match and summary["imported"]:
        summary["matching"] = _run_match(session, summary["account_id"])
    return summary


def _run_match(session: Session, account_id: str | None) -> dict[str, Any]:
    try:
        client, ejercicio = _factusol(session)
        invoices = service.pending_invoices(client, ejercicio)
    except Exception as exc:  # noqa: BLE001 — sin FACTUSOL no hay facturas contra las que casar
        return {"ok": False, "detail": f"FACTUSOL no disponible: {str(exc)[:160]}"}
    stats = service.run_matching(session, invoices, account_id=account_id)
    stats["ok"] = True
    stats["pending_invoices"] = len(invoices)
    return stats


@router.post("/match")
def run_match(
    account_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Recalcula las propuestas de los movimientos pendientes (p. ej. tras
    aprender una regla). Solo propone."""
    _ = current_user
    return _run_match(session, account_id)


# --- listado -----------------------------------------------------------------------------


@router.get("/movements")
def list_movements(
    account_id: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    confidence: str | None = Query(default=None, pattern="^(alta|media|baja|none)$"),
    status_: str | None = Query(
        default=None, alias="status", pattern="^(pending|reconciled|discarded)$"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    return service.list_movements(
        session,
        account_id=account_id,
        desde=desde,
        hasta=hasta,
        confidence=confidence,
        status=status_,
        limit=limit,
        offset=offset,
    )


# --- decisiones ---------------------------------------------------------------------------


class ReassignTarget(BaseModel):
    serie: int = Field(ge=1, le=9)
    codigo: int = Field(ge=1)
    numero: str | None = None
    importe: float
    cliente_nombre: str | None = None
    cliente_codigo: str | None = None


class ReassignIn(BaseModel):
    targets: list[ReassignTarget] = Field(min_length=1, max_length=10)
    learn_payer: bool = False


class DiscardIn(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    learn: bool = False


@router.post("/movements/{movement_id}/confirm")
def confirm_movement(
    movement_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    try:
        return service.confirm_movement(session, movement_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "no_proposal", "detail": str(exc)}
        ) from exc


@router.post("/movements/{movement_id}/reassign")
def reassign_movement(
    movement_id: str,
    payload: ReassignIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    try:
        return service.reassign_movement(
            session,
            movement_id,
            [t.model_dump() for t in payload.targets],
            current_user.id,
            learn_payer=payload.learn_payer,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "split_invalid", "detail": str(exc)}
        ) from exc


@router.post("/movements/{movement_id}/discard")
def discard_movement(
    movement_id: str,
    payload: DiscardIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    try:
        return service.discard_movement(
            session, movement_id, payload.reason, current_user.id, learn=payload.learn
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/movements/{movement_id}/reopen")
def reopen_movement(
    movement_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    _ = current_user
    try:
        return service.reopen_movement(session, movement_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/movements/confirm-high")
def confirm_all_high(
    account_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Acción en BLOQUE (un clic de Bart): confirma las de confianza alta."""
    return {"confirmed": service.confirm_all_high(session, current_user.id, account_id=account_id)}


# --- reglas aprendidas ---------------------------------------------------------------


class RuleIn(BaseModel):
    kind: str = Field(pattern="^(exclude_pattern|payer_to_client)$")
    pattern: str = Field(min_length=1, max_length=255)
    client_codcli: str | None = None
    client_nombre: str | None = None
    note: str | None = Field(default=None, max_length=255)


@router.get("/rules")
def list_rules(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    service.ensure_default_rules(session)
    return {"items": service.list_rules(session)}


@router.post("/rules", status_code=201)
def create_rule(
    payload: RuleIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    try:
        rule = service.create_rule(session, payload.model_dump(), current_user.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"id": rule.id, "kind": rule.kind, "pattern": rule.pattern}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> Response:
    _ = current_user
    try:
        service.delete_rule(session, rule_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=204)


# --- exportación ------------------------------------------------------------------------


@router.get("/export")
def export_xlsx(
    account_id: str = Query(...),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    """Excel NUEVO con el formato del extracto y FACTURA/PRESUPUESTO/PEDIDO
    rellenas con lo confirmado. El original de Bart no se toca."""
    _ = current_user
    try:
        data = service.export_statement_xlsx(
            session, account_id=account_id, desde=desde, hasta=hasta
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    suffix = f"_{desde or ''}_{hasta or ''}".rstrip("_")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="extracto_conciliado{suffix}.xlsx"'},
    )
