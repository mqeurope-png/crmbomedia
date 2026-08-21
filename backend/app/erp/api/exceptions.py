"""BoHub ERP — bandeja de excepciones + settings (Fase A PR 6).

Cierra la Fase A. Endpoints:
  - GET   /api/erp/exceptions?type=&status=&assigned=me|<uid>
  - POST  /api/erp/exceptions/{id}/assign     {assigned_to_user_id}
  - POST  /api/erp/exceptions/{id}/status     {status}   (open/in_progress/dismissed)
  - POST  /api/erp/exceptions/{id}/resolve    {resolution_note}
  - GET   /api/erp/settings
  - PATCH /api/erp/settings                    (solo ADMIN)

El chip de alerta ETA vencida es de presentación: el backend expone
`eta_overdue` (bool) por excepción comparando `metadata.eta_date <= hoy`;
NO cambia el estado (decisión del catálogo).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_admin, require_erp_edit, require_erp_view
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpException,
    ErpSettings,
    ExceptionStatus,
    InvoiceMode,
)
from app.models.crm import User

router = APIRouter(prefix="/api/erp", tags=["erp-exceptions"])


# --- schemas -----------------------------------------------------------------


class AssignIn(BaseModel):
    assigned_to_user_id: str | None = None


class StatusIn(BaseModel):
    status: str


class ResolveIn(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=2000)


class SettingsIn(BaseModel):
    default_invoice_mode: str | None = None
    auto_invoice_max_amount_eur: float | None = None
    default_carrier_id: str | None = None
    factusol_default_ejercicio: str | None = None
    factusol_live: bool | None = None
    # C-2: serie de facturación (global + override por origen/tienda).
    factusol_series_default: str | None = None
    factusol_series_by_source: dict[str, str] | None = None
    #: ERP-E2 — nombre de la empresa emisora de cada serie: {"5": "Streamtec"}.
    factusol_series_names: dict[str, str] | None = None
    #: ERP-E2-fix2 — valor de F_PCL.ESTPCL que FACTUSOL usa para «Enviado»
    #: (= pedido facturado). Confirmado en vivo: "2".
    factusol_estpcl_invoiced: str | None = None


# --- helpers -----------------------------------------------------------------


def _parse_eta(metadata: dict[str, Any]) -> date | None:
    raw = metadata.get("eta_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            return None


def _serialise(
    exc: ErpException, order_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = json.loads(exc.metadata_json) if exc.metadata_json else {}
    eta = _parse_eta(metadata)
    order_info = order_info or {}
    return {
        "id": exc.id,
        "type": getattr(exc.type, "value", exc.type),
        "subtype": exc.subtype,
        "status": getattr(exc.status, "value", exc.status),
        "order_id": exc.order_id,
        # D-2: identificar el pedido/cliente sin abrir la ficha.
        "order_number": order_info.get("order_number"),
        "contact_name": order_info.get("contact_name"),
        "company_name": order_info.get("company_name"),
        "metadata": metadata,
        "eta_date": eta.isoformat() if eta else None,
        # Chip de alerta: ETA vencida y la excepción sigue abierta.
        "eta_overdue": bool(
            eta and eta <= datetime.now(UTC).date()
            and exc.status in (ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS)
        ),
        "assigned_to_user_id": exc.assigned_to_user_id,
        "reported_by_user_id": exc.reported_by_user_id,
        "resolution_note": exc.resolution_note,
        "resolved_at": exc.resolved_at.isoformat() if exc.resolved_at else None,
        "created_at": exc.created_at.isoformat(),
    }


def _orders_info(
    session: Session, rows: list[ErpException],
) -> dict[str, dict[str, Any]]:
    """D-2: `{order_id: {order_number, contact_name, company_name}}` para las
    excepciones listadas — batch, sin N+1."""
    from app.erp.api.orders import customer_names  # noqa: PLC0415
    from app.erp.models import Order  # noqa: PLC0415

    order_ids = {e.order_id for e in rows if e.order_id}
    if not order_ids:
        return {}
    orders = list(session.scalars(select(Order).where(Order.id.in_(order_ids))))
    names = customer_names(session, orders)
    return {
        o.id: {"order_number": o.order_number, **(names.get(o.id) or {})}
        for o in orders
    }


def _get(session: Session, exc_id: str) -> ErpException:
    exc = session.get(ErpException, exc_id)
    if exc is None:
        raise not_found("Exception")
    return exc


# --- excepciones -------------------------------------------------------------


@router.get("/exceptions")
def list_exceptions(
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    assigned: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    stmt = select(ErpException)
    if type:
        stmt = stmt.where(ErpException.type == type)
    if status:
        stmt = stmt.where(ErpException.status == status)
    if assigned == "me":
        stmt = stmt.where(ErpException.assigned_to_user_id == current_user.id)
    elif assigned:
        stmt = stmt.where(ErpException.assigned_to_user_id == assigned)
    rows = list(session.scalars(
        stmt.order_by(ErpException.created_at.desc()).limit(limit)
    ))
    info = _orders_info(session, rows)
    return {"items": [_serialise(e, info.get(e.order_id)) for e in rows]}


@router.post("/exceptions/{exc_id}/assign")
def assign_exception(
    exc_id: str,
    payload: AssignIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    exc = _get(session, exc_id)
    if payload.assigned_to_user_id and not session.get(User, payload.assigned_to_user_id):
        raise HTTPException(400, "assigned_to_user_id no existe")
    exc.assigned_to_user_id = payload.assigned_to_user_id
    _audit(session, exc, "assigned", current_user,
           {"assigned_to": payload.assigned_to_user_id})
    session.commit()
    return _serialise(exc)


@router.post("/exceptions/{exc_id}/status")
def update_status(
    exc_id: str,
    payload: StatusIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Marcar como vista» (in_progress) / descartar (dismissed) / reabrir."""
    exc = _get(session, exc_id)
    try:
        new_status = ExceptionStatus(payload.status)
    except ValueError as e:
        raise HTTPException(400, f"status inválido: {payload.status!r}") from e
    if new_status == ExceptionStatus.RESOLVED:
        raise HTTPException(400, "Usa /resolve para cerrar con nota.")
    exc.status = new_status
    _audit(session, exc, "status_changed", current_user, {"to": new_status.value})
    session.commit()
    return _serialise(exc)


@router.post("/exceptions/{exc_id}/resolve")
def resolve_exception(
    exc_id: str,
    payload: ResolveIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    exc = _get(session, exc_id)
    exc.status = ExceptionStatus.RESOLVED
    exc.resolution_note = payload.resolution_note
    exc.resolved_at = datetime.now(UTC)
    exc.resolved_by_user_id = current_user.id
    _audit(session, exc, "resolved", current_user, {"note": payload.resolution_note})
    session.commit()
    return _serialise(exc)


def _audit(
    session: Session, exc: ErpException, verb: str, actor: User, extra: dict[str, Any]
) -> None:
    try:
        record_event(
            session, action=f"erp.exception_{verb}", target_type="exception",
            target_id=exc.id, actor=actor,
            metadata={"order_id": exc.order_id,
                      "type": getattr(exc.type, "value", exc.type), **extra},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass


# --- settings ----------------------------------------------------------------


def _get_or_create_settings(session: Session) -> ErpSettings:
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        session.add(cfg)
        session.flush()
    return cfg


def _serialise_settings(cfg: ErpSettings) -> dict[str, Any]:
    mode = getattr(cfg.default_invoice_mode, "value", cfg.default_invoice_mode)
    return {
        "default_invoice_mode": mode,
        "auto_invoice_max_amount_eur": (
            float(cfg.auto_invoice_max_amount_eur)
            if cfg.auto_invoice_max_amount_eur is not None else None
        ),
        "default_carrier_id": cfg.default_carrier_id,
        "factusol_default_ejercicio": cfg.factusol_default_ejercicio,
        "factusol_live": bool(cfg.factusol_live),
        # C-2 + ERP-E2: serie = empresa emisora (default global + override por
        # origen + nombres). Ya no es una letra que se escriba en una columna:
        # es el número que decide el rango de numeración del CODFAC.
        "factusol_series_default": _series(cfg).get("default") or "",
        "factusol_series_by_source": _series(cfg).get("by_source") or {},
        "factusol_series_names": _series(cfg).get("names") or {},
        "factusol_estpcl_invoiced": _series(cfg).get("estpcl_invoiced") or "",
    }


def _series(cfg: ErpSettings) -> dict[str, Any]:
    """Blob `factusol_series_json` decodificado (vacío si falta o es ilegible)."""
    if not cfg.factusol_series_json:
        return {}
    try:
        data = json.loads(cfg.factusol_series_json)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/settings")
def get_settings_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    cfg = _get_or_create_settings(session)
    session.commit()
    return _serialise_settings(cfg)


@router.patch("/settings")
def update_settings(
    payload: SettingsIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    cfg = _get_or_create_settings(session)
    if payload.default_invoice_mode is not None:
        try:
            cfg.default_invoice_mode = InvoiceMode(payload.default_invoice_mode)
        except ValueError as e:
            raise HTTPException(
                400, f"default_invoice_mode inválido: {payload.default_invoice_mode!r}"
            ) from e
    if payload.auto_invoice_max_amount_eur is not None:
        cfg.auto_invoice_max_amount_eur = payload.auto_invoice_max_amount_eur
    if payload.default_carrier_id is not None:
        cfg.default_carrier_id = payload.default_carrier_id or None
    if payload.factusol_default_ejercicio is not None:
        cfg.factusol_default_ejercicio = payload.factusol_default_ejercicio or None
    if payload.factusol_live is not None:
        cfg.factusol_live = payload.factusol_live
    # C-2: serie de facturación — se reescribe el blob completo con lo que
    # llegue, conservando la parte que el PATCH no toque.
    if (payload.factusol_series_default is not None
            or payload.factusol_series_by_source is not None
            or payload.factusol_series_names is not None
            or payload.factusol_estpcl_invoiced is not None):
        series = _series(cfg)
        if payload.factusol_series_default is not None:
            series["default"] = payload.factusol_series_default.strip()
        if payload.factusol_series_by_source is not None:
            # Las series vacías se descartan: «vacío = usa la por defecto».
            series["by_source"] = {
                k: v.strip()
                for k, v in payload.factusol_series_by_source.items()
                if v and v.strip()
            }
        if payload.factusol_estpcl_invoiced is not None:
            series["estpcl_invoiced"] = payload.factusol_estpcl_invoiced.strip()
        if payload.factusol_series_names is not None:
            # ERP-E2: {"5": "Streamtec", …}. Claves como string por JSON.
            series["names"] = {
                str(k).strip(): v.strip()
                for k, v in payload.factusol_series_names.items()
                if v and v.strip()
            }
        cfg.factusol_series_json = json.dumps(series)
    _audit_settings(session, current_user)
    session.commit()
    return _serialise_settings(cfg)


def _audit_settings(session: Session, actor: User) -> None:
    try:
        record_event(
            session, action="erp.settings_updated", target_type="erp_settings",
            target_id=ERP_SETTINGS_SINGLETON_ID, actor=actor,
        )
    except Exception:  # noqa: BLE001
        pass
