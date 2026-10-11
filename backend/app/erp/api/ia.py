"""La IA en números — API (capacidad `erp.config`, la pantalla ERP · Leads).

- GET /api/erp/ia/uso?dias=30   llamadas, tokens, coste por propósito, modelo y
                                 día, y qué pasó con los borradores del asistente.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_config
from app.models.crm import User
from app.services.ia import uso

router = APIRouter(prefix="/api/erp/ia", tags=["erp-ia"])


@router.get("/uso")
def uso_de_la_ia(
    dias: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Números reales de los últimos `dias` días: nada de estimaciones."""
    _ = current_user
    return uso.resumen(session, dias=dias)
