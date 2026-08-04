"""BoHub ERP — API auxiliar de FACTUSOL (Fase C · C-2-fix2).

Catálogos de solo lectura que alimentan el modal de emisión de factura (formas
de pago). Se cachean en proceso unos minutos para no re-autenticar en DELSOL en
cada apertura del modal. Las escrituras NO viven aquí (van por la cola
serializada `factusol:writes`).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_erp_view
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/factusol", tags=["erp-factusol"])

#: Cache en proceso {ejercicio: (expira_epoch, items)} de formas de pago.
_FOP_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FOP_CACHE_TTL_SECONDS = 300  # 5 min


def _first(row: dict[str, Any], *cols: str) -> Any:
    for c in cols:
        v = row.get(c)
        if v not in (None, ""):
            return v
    return None


def _normalise_fop(row: dict[str, Any]) -> dict[str, Any]:
    """F_FOP → {codigo, nombre}. Los nombres exactos de columna se confirman
    con la validación de Bart; se prueban varios candidatos habituales."""
    codigo = _first(row, "CODFOP", "COFOP")
    nombre = _first(row, "DESFOP", "NOMFOP", "TITFOP", "NORFOP")
    return {"codigo": str(codigo) if codigo is not None else None,
            "nombre": nombre or (str(codigo) if codigo is not None else "")}


@router.get("/formas-pago")
def formas_pago(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Catálogo de formas de pago (F_FOP) para el desplegable del modal de
    emisión. Best-effort: si FACTUSOL no responde, devuelve lista vacía (el
    modal permite entonces dejarlo en blanco). Cache en proceso de 5 min."""
    _ = current_user
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    ejercicio = ejercicio_for(session)
    cached = _FOP_CACHE.get(ejercicio)
    now = time.time()
    if cached and cached[0] > now:
        return {"items": cached[1], "ejercicio": ejercicio, "cached": True}

    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

    try:
        client = FactusolClient.from_settings()
        rows = client.load_table("F_FOP", ejercicio=ejercicio)
        items = [_normalise_fop(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído / sin credenciales
        logger.warning("factusol formas-pago falló: %s", exc)
        return {"items": [], "ejercicio": ejercicio, "error": "factusol_unreachable"}

    _FOP_CACHE[ejercicio] = (now + _FOP_CACHE_TTL_SECONDS, items)
    return {"items": items, "ejercicio": ejercicio, "cached": False}
