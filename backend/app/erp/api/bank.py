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
from app.erp.api.deps import require_conciliacion
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
    #: ERP-F5 — contrapartida de cobro de FACTUSOL enlazada (código del
    #: catálogo configurable; vacío = sin enlazar).
    contrapartida_codigo: str | None = Field(default=None, max_length=10)
    column_mapping: dict[str, str] | None = None
    #: La hoja de Drive: su enlace o su identificador, la pestaña (vacía: la
    #: primera) y si BoHub escribe en ella la factura y el pedido.
    drive_file_id: str | None = Field(default=None, max_length=500)
    drive_pestana: str | None = Field(default=None, max_length=120)
    drive_escribir: bool | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    iban: str | None = Field(default=None, max_length=40)
    bank_name: str | None = Field(default=None, max_length=120)
    bic: str | None = Field(default=None, max_length=11)
    currency: str | None = Field(default=None, max_length=3)
    serie: int | None = Field(default=None, ge=1, le=9)
    contrapartida_codigo: str | None = Field(default=None, max_length=10)
    column_mapping: dict[str, str] | None = None
    drive_file_id: str | None = Field(default=None, max_length=500)
    drive_pestana: str | None = Field(default=None, max_length=120)
    drive_escribir: bool | None = None


@router.get("/accounts")
def list_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    _ = current_user
    return {"items": service.list_accounts(session)}


@router.get("/accounts/suggested")
def suggested_accounts(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
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
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    _ = current_user
    try:
        return service.account_dict(session, service.create_account(session, payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "account_invalid", "detail": str(exc)}
        ) from exc


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    payload: AccountPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    _ = current_user
    try:
        return service.account_dict(
            session,
            service.update_account(session, account_id, payload.model_dump(exclude_unset=True)),
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "account_invalid", "detail": str(exc)}
        ) from exc


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> Response:
    _ = current_user
    try:
        service.delete_account(session, account_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=204)


# --- el extracto por Drive ------------------------------------------------------------


def _encolar_hoja(session: Session, account_ids: set[str]) -> None:
    """Tras confirmar: si esas cuentas tienen algo pendiente de escribir en su
    hoja de Drive, se encola la escritura (best-effort: si no, espera a la
    siguiente lectura)."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.bank.drive_job import encolar_escritura  # noqa: PLC0415
    from app.erp.models import BankMovement  # noqa: PLC0415

    if not account_ids:
        return
    con_pendientes = set(session.scalars(
        select(BankMovement.account_id).where(
            BankMovement.account_id.in_(account_ids),
            BankMovement.hoja_estado == service.HOJA_PENDIENTE,
        ).distinct()
    ))
    for account_id in con_pendientes:
        encolar_escritura(account_id)


@router.get("/drive")
def drive_estado(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    """Con qué cuenta de servicio se comparten las hojas (la de Seguimiento,
    en Configuración ERP) y a qué hora es el repaso diario."""
    from app.erp.bank.drive import email_cuenta_de_servicio  # noqa: PLC0415
    from app.erp.bank.drive_job import HORA  # noqa: PLC0415

    _ = current_user
    email = email_cuenta_de_servicio(session)
    return {"configured": email is not None, "service_account_email": email, "hora": HORA}


@router.post("/drive/traer")
def traer_de_drive(
    account_id: str | None = Query(default=None),
    run_match: bool = Query(default=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    """«Traer de Drive»: lee la hoja de la cuenta (o de todas las que la
    tienen) entera, importa lo que falta con el lector de siempre y escribe
    lo pendiente. Un resultado por cuenta: si Drive falla en una, lo dice
    ahí y las demás siguen. No escribe en FACTUSOL."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.bank.drive import DriveBancoError, cerrojo  # noqa: PLC0415
    from app.erp.bank.drive import traer_de_drive as traer  # noqa: PLC0415
    from app.erp.models import BankAccount  # noqa: PLC0415

    q = select(BankAccount).where(BankAccount.drive_file_id.is_not(None))
    if account_id:
        q = q.where(BankAccount.id == account_id)
    cuentas = session.scalars(q.order_by(BankAccount.name)).all()
    if not cuentas:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "sin_hoja",
            "detail": ("Esa cuenta no tiene hoja de Drive." if account_id else
                       "Ninguna cuenta tiene hoja de Drive: ponla en la pestaña Cuentas."),
        })
    resultados: list[dict[str, Any]] = []
    for account in cuentas:
        base = {"account_id": account.id, "account_name": account.name}
        with cerrojo(account.id) as cogido:
            if not cogido:
                resultados.append({**base, "ok": False, "code": "ocupada", "detail": (
                    "Ya hay una lectura de Drive en marcha para esta cuenta: prueba en un minuto."
                )})
                continue
            try:
                resumen = traer(session, account, user_id=current_user.id)
            except DriveBancoError as exc:
                resultados.append({**base, "ok": False, "code": exc.code, "detail": str(exc)})
                continue
            except ParseError as exc:
                resultados.append({**base, "ok": False, "code": "parse_error", "detail": (
                    f"La hoja no tiene la forma de un extracto: {exc}"
                )})
                continue
        if run_match and resumen.get("ok") and resumen.get("imported"):
            resumen["matching"] = _run_match(session, account.id)
        resultados.append({**base, **resumen})
    return {"resultados": resultados}


@router.post("/drive/escribir")
def escribir_en_drive(
    account_id: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    """«Escribir ahora»: escribe ya en la hoja lo conciliado pendiente de esa
    cuenta (sin esperar a la cola). Solo en celdas vacías."""
    from app.erp.bank.drive import cerrojo, escribir_cuenta  # noqa: PLC0415
    from app.erp.models import BankAccount  # noqa: PLC0415

    _ = current_user
    account = session.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cuenta no encontrada")
    with cerrojo(account.id) as cogido:
        if not cogido:
            raise HTTPException(status.HTTP_409_CONFLICT, {"code": "ocupada", "detail": (
                "Ya hay una lectura o escritura de Drive en marcha para esta cuenta: prueba "
                "en un minuto."
            )})
        return escribir_cuenta(session, account, origen="manual")


# --- importación + casado -------------------------------------------------------------


@router.post("/import", status_code=201)
async def import_statement(
    file: UploadFile = File(...),
    account_id: str | None = Form(default=None),
    run_match: bool = Form(default=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    """Sube el extracto (.xlsx/.csv). Identifica la cuenta por el número de
    la cabecera (IBAN o el antiguo) o por la cuenta que el banco repite en
    cada fila (avisa si no está dada de alta), deduplica, importa lo que
    entiende y devuelve lo que no (`no_entendidas`, con fila y motivo). Lo
    anotado a mano en la columna `factura` entra casado a nombre de quien
    importa; si `run_match`, el resto recibe PROPUESTAS."""
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
            user_id=current_user.id,
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
    current_user: User = Depends(require_conciliacion),
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
    #: «Ver el movimiento» desde la ficha del pedido: solo ese movimiento.
    movimiento: str | None = Query(default=None, max_length=36),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
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
        movement_id=movimiento,
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
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    try:
        out = service.confirm_movement(session, movement_id, current_user.id)
        _encolar_hoja(session, {out["account_id"]})
        return out
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
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    try:
        out = service.reassign_movement(
            session,
            movement_id,
            [t.model_dump() for t in payload.targets],
            current_user.id,
            learn_payer=payload.learn_payer,
        )
        _encolar_hoja(session, {out["account_id"]})
        return out
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
    current_user: User = Depends(require_conciliacion),
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
    current_user: User = Depends(require_conciliacion),
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
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    """Acción en BLOQUE (un clic de Bart): confirma las de confianza alta."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import BankAccount  # noqa: PLC0415

    n = service.confirm_all_high(session, current_user.id, account_id=account_id)
    if n:
        _encolar_hoja(session, set(session.scalars(select(BankAccount.id))))
    return {"confirmed": n}


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
    current_user: User = Depends(require_conciliacion),
) -> dict[str, Any]:
    _ = current_user
    service.ensure_default_rules(session)
    return {"items": service.list_rules(session)}


@router.post("/rules", status_code=201)
def create_rule(
    payload: RuleIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
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
    current_user: User = Depends(require_conciliacion),
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
    current_user: User = Depends(require_conciliacion),
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


@router.get("/export-cobros")
def export_cobros_csv(
    account_id: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_conciliacion),
) -> Response:
    """CSV para `scripts/registrar_cobros_lote.ps1` (serie, codigo, cuenta,
    forma, fecha, observaciones) con lo conciliado y no descartado, y la
    contrapartida de cada cuenta en `cuenta`. Solo las facturas cuyo saldo
    pendiente en FACTUSOL cuadra con lo conciliado (el script registra el
    saldo entero): sin FACTUSOL no se puede saber y responde 503. NO escribe en
    FACTUSOL. Las cabeceras `X-Cobros-*` dicen cuántas van y cuántas se
    dejaron fuera y por qué."""
    _ = current_user
    try:
        client, ejercicio = _factusol(session)
        facturas = service.pending_invoices(client, ejercicio)
        saldos = {
            (int(f["serie"]), int(f["codigo"])): float(f["saldo_pendiente"] or 0)
            for f in facturas
        }
        # Con el total se sabe lo ya cobrado (total − saldo): un cobro parcial
        # registrado desde la ficha no vuelve a entrar por el mismo movimiento.
        totales = {
            (int(f["serie"]), int(f["codigo"])): float(f["total"])
            for f in facturas if f.get("total") is not None
        }
    except Exception as exc:  # noqa: BLE001 — sin FACTUSOL no se sabe qué falta por cobrar
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable",
            "detail": ("No se ha podido consultar FACTUSOL: sin el saldo de cada factura no se "
                       "sabe qué falta por cobrar. Prueba de nuevo en un momento."),
        }) from exc
    data, stats = service.export_cobros_csv(
        session, saldos=saldos, totales=totales, account_id=account_id, desde=desde,
        hasta=hasta,
    )
    suffix = f"_{desde or ''}_{hasta or ''}".rstrip("_")
    cabeceras = {
        "X-Cobros-Incluidos": stats["incluidas"],
        "X-Cobros-Parciales": stats["parciales"],
        "X-Cobros-No-Pendientes": stats["no_pendientes"],
        "X-Cobros-No-Cuadran": stats["no_cuadran"],
        "X-Cobros-Sin-Contrapartida": stats["sin_contrapartida"],
    }
    return Response(
        content=data.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="cobros_conciliados{suffix}.csv"',
            **{k: str(v) for k, v in cabeceras.items()},
            "Access-Control-Expose-Headers": ", ".join(cabeceras),
        },
    )
