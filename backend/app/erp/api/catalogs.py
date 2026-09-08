"""ERP-F5 — catálogos de FACTUSOL de SOLO LECTURA (con cache) + el catálogo
configurable de contrapartidas de cobro. Ver `integrations/factusol/catalogs.py`
para saber qué catálogo vive en qué tabla.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_erp_view
from app.erp.contrapartidas import (
    PAYPAL_STORES,
    contrapartidas,
    paypal_by_store_config,
)
from app.integrations.factusol.catalogs import (
    CATALOGS,
    describe_catalogs,
    is_cached,
    load_catalog,
)
from app.models.crm import User

router = APIRouter(prefix="/api/erp/catalogs", tags=["erp-catalogs"])


@router.get("")
def list_catalogs(current_user: User = Depends(require_erp_view)) -> dict[str, Any]:
    """Qué catálogos hay y en qué tabla de FACTUSOL vive cada uno."""
    _ = current_user
    items = describe_catalogs()
    items.append({
        "name": "contrapartidas",
        "table": None,
        "code_column": None,
        "name_columns": [],
        "description": (
            "Contrapartidas de cobro (CPACOB/CPALCO). Tabla no localizada en FACTUSOL: "
            "catálogo configurable en /erp/settings."
        ),
    })
    return {"items": items}


@router.get("/contrapartidas")
def contrapartidas_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return {
        "items": contrapartidas(session),
        "paypal_by_store": paypal_by_store_config(
            series_config(session).get("paypal_contrapartidas_by_store")
        ),
        "stores": [{"key": k, "label": v} for k, v in PAYPAL_STORES],
        "source": "erp_settings",
    }


@router.get("/{name}")
def catalog_endpoint(
    name: str,
    fresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Catálogo de FACTUSOL (`formas_pago`, `agentes`, `transportistas`,
    `almacenes`, `familias`). `fresh=true` salta la cache."""
    _ = current_user
    spec = CATALOGS.get(name)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"catálogo desconocido: {name}")
    from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    cached = is_cached(name, ejercicio=ejercicio) and not fresh
    try:
        items = load_catalog(client, name, ejercicio=ejercicio, force_refresh=fresh)
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_catalog_failed", "detail": str(exc)[:200],
        }) from exc
    return {
        "name": name,
        "table": spec.table,
        "ejercicio": ejercicio,
        "items": items,
        "count": len(items),
        "cached": cached,
    }
