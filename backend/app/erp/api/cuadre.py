"""ERP · Cuadre — API del panel de descuadres (capacidad `erp.cuadre`).

SOLO LECTURA sobre los datos: lo único que se escribe es el estado del propio
descuadre («revisado / no es un descuadre» con motivo, «volver a incluir») y
las pasadas. Nada de FACTUSOL, nada de pedidos.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_cuadre
from app.erp.cuadre import engine
from app.erp.cuadre.registry import SEVERIDADES, catalogo
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/cuadre", tags=["erp-cuadre"])


class RevisarIn(BaseModel):
    motivo: str = Field(min_length=1, max_length=engine.MOTIVO_MAX)


@router.get("/resumen")
def resumen(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> dict[str, Any]:
    """Contadores por severidad, una tarjeta por comprobación activa, la última
    pasada y lo que se está comprobando ahora."""
    _ = current_user
    return engine.resumen(session)


@router.get("/comprobaciones")
def comprobaciones(current_user: User = Depends(require_cuadre)) -> dict[str, Any]:
    """Catálogo de comprobaciones registradas (para Configuración y filtros)."""
    _ = current_user
    return {"items": catalogo()}


@router.get("/hallazgos")
def hallazgos(
    check_id: str | None = Query(default=None, max_length=40),
    severidad: str | None = Query(default=None, pattern="^(alta|media|baja)$"),
    solo_nuevos: bool = Query(default=False),
    incluir_revisados: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> dict[str, Any]:
    """Descuadres abiertos (y revisados con `incluir_revisados`)."""
    _ = current_user
    items = engine.listar(
        session, check_id=check_id, severidad=severidad, solo_nuevos=solo_nuevos,
        incluir_revisados=incluir_revisados,
    )
    return {"items": items, "total": len(items), "severidades": list(SEVERIDADES)}


def _finding(session: Session, finding_id: str) -> dict[str, Any]:
    from app.erp.cuadre.config import cuadre_config  # noqa: PLC0415
    from app.erp.models import CuadreFinding  # noqa: PLC0415

    f = session.get(CuadreFinding, finding_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Descuadre no encontrado.")
    reg = {c.id: c for c in engine.comprobaciones_activas(cuadre_config(session))}
    return engine.finding_dict(f, reg.get(f.check_id), nuevo=False)


@router.post("/hallazgos/{finding_id}/revisar")
def revisar(
    finding_id: str,
    payload: RevisarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> dict[str, Any]:
    """«Revisado / no es un descuadre», con motivo corto obligatorio. No vuelve
    a aparecer mientras no cambien los valores que lo motivan."""
    try:
        engine.revisar(session, finding_id, motivo=payload.motivo, user_id=current_user.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Descuadre no encontrado.") from exc
    except engine.CuadreError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    session.commit()
    return _finding(session, finding_id)


@router.post("/hallazgos/{finding_id}/reincluir")
def reincluir(
    finding_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> dict[str, Any]:
    """«Volver a incluir»: el descuadre revisado vuelve a contar como abierto."""
    _ = current_user
    try:
        engine.reincluir(session, finding_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Descuadre no encontrado.") from exc
    except engine.CuadreError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    session.commit()
    return _finding(session, finding_id)


@router.post("/comprobar")
def comprobar(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> dict[str, Any]:
    """«Comprobar ahora»: las comprobaciones de BoHub corren al momento; las de
    FACTUSOL se encolan en worker-sync (la pantalla enseña «comprobando…»)."""
    from app.erp.cuadre.job import comprobar_ahora  # noqa: PLC0415

    lanzadas = comprobar_ahora(session, user_id=current_user.id)
    return {"lanzadas": lanzadas, "resumen": engine.resumen(session)}


@router.get("/export")
def export(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_cuadre),
) -> Response:
    """Excel con TODOS los descuadres abiertos."""
    from app.core.config import get_settings  # noqa: PLC0415

    _ = current_user
    content = engine.exportar_xlsx(session, base_url=get_settings().frontend_base_url)
    filename = f"cuadre_descuadres_{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
