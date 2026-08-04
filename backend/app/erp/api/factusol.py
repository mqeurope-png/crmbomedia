"""BoHub ERP Fase C PR C-1 — endpoint admin de smoke-test FACTUSOL.

Temporal (sin UI final): permite a un admin validar desde el CRM en prod que
(a) las credenciales autentican, (b) la lectura de tablas funciona, (c) el
mapper produce el payload correcto — SIN escribir a FACTUSOL. Se retira en
C-2 cuando la UI conecte los flujos reales.

  POST /api/erp/factusol/smoke-test?mode=login
  POST /api/erp/factusol/smoke-test?mode=read_customers
  POST /api/erp/factusol/smoke-test?mode=dry_run_invoice&order_id=<id>
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db.session import get_session
from app.erp.api.deps import require_erp_admin
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings, Order
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.mapper import order_to_factusol_invoice
from app.models.crm import User

router = APIRouter(prefix="/api/erp/factusol", tags=["erp-factusol"])

_ROLE_CLAIM_KEYS = ("role", "rol", "Role", "roles", "unique_name")


def _ejercicio(session: Session) -> str:
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is not None and cfg.factusol_default_ejercicio:
        return cfg.factusol_default_ejercicio
    return get_settings().factusol_default_ejercicio


@router.post("/smoke-test")
def smoke_test(
    mode: str = Query(...),
    order_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = current_user

    if mode == "login":
        client = FactusolClient.from_settings()
        try:
            client.authenticate()
        except FactusolError as exc:
            raise HTTPException(502, {"code": "factusol_login_failed",
                                      "detail": str(exc), "body": exc.body[:500]}) from exc
        claims = client.token_claims()
        role = next((claims[k] for k in _ROLE_CLAIM_KEYS if claims.get(k)), None)
        return {
            "ok": True,
            "token_valid_seconds": client.token_valid_seconds(),
            "role": role,
        }

    if mode == "read_customers":
        client = FactusolClient.from_settings()
        try:
            rows = client.load_table(
                "F_CLI", filtro="1=1 ORDER BY CODCLI LIMIT 5",
            )
        except FactusolError as exc:
            raise HTTPException(502, {"code": "factusol_read_failed",
                                      "detail": str(exc), "body": exc.body[:500]}) from exc
        return {"ok": True, "count": len(rows), "customers": rows}

    if mode == "dry_run_invoice":
        if not order_id:
            raise HTTPException(400, "dry_run_invoice requiere order_id")
        order = session.get(Order, order_id, options=[selectinload(Order.lines)])
        if order is None:
            raise HTTPException(404, "Order no encontrado")
        codcli = order.company_id and _linked_codcli(session, order) or "<sin-vincular>"
        ejercicio = _ejercicio(session)
        cabecera, lineas = order_to_factusol_invoice(order, codcli, ejercicio)
        # Dry-run: NO se escribe nada en FACTUSOL.
        return {
            "ok": True, "dry_run": True, "ejercicio": ejercicio,
            "codcli": codcli, "cabecera": cabecera, "lineas": lineas,
        }

    raise HTTPException(400, f"mode inválido: {mode!r}")


def _linked_codcli(session: Session, order: Order) -> str | None:
    from app.models.crm import Company  # noqa: PLC0415

    if not order.company_id:
        return None
    company = session.get(Company, order.company_id)
    return company.factusol_company_id if company else None
