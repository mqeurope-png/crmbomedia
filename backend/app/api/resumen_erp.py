"""CRM · el estado del resumen ERP por empresa (1/11/2026): cuándo se
recalculó, si falló, los recuentos de las etiquetas automáticas, y el botón
«Recalcular ahora»."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.db.session import get_session
from app.models.crm import User

router = APIRouter(prefix="/api/crm/resumen-erp", tags=["crm-resumen-erp"])


@router.get("")
def estado_resumen(
    session: Session = Depends(get_session),
    _: User = Depends(require_user),
) -> dict[str, Any]:
    from app.services.empresa_erp import estado  # noqa: PLC0415

    return estado(session)


@router.post("/recalcular")
def recalcular_resumen(
    session: Session = Depends(get_session),
    _: User = Depends(require_user),
) -> dict[str, Any]:
    """Programa un recálculo ya (va por la cola; tarda unos segundos)."""
    from app.services.empresa_erp import estado  # noqa: PLC0415
    from app.services.empresa_erp_job import encolar  # noqa: PLC0415

    return {**estado(session), "programado": encolar("botón", ya=True)}
