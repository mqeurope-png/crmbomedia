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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin
from app.db.session import get_session
from app.erp.api.deps import require_cuadre
from app.erp.cuadre import engine
from app.erp.cuadre.registry import SEVERIDADES, catalogo
from app.models.crm import User
from app.workers import registro_fallidos

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


# --- colas: reencolar y vaciar el registro de fallidos -----------------------
# El panel es de solo lectura y su capacidad `erp.cuadre` la tiene también el
# rol `pedidos`. Esto NO es leer: reencolar mueve trabajo de verdad y vaciar es
# irreversible, así que va aparte, con `require_admin`, vista previa obligada y
# fila de auditoría.


def _colas_conocidas(conn: Any = None) -> list[str]:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    return [c.name for c in Queue.all(connection=conn or redis_connection())]


def _cola_valida(nombre: str) -> str:
    """Una cola que no existe sería un registro vacío y un «hecho» engañoso."""
    try:
        conocidas = _colas_conocidas()
    except Exception:  # noqa: BLE001 — sin Redis el error real es el de abajo
        conocidas = []
    if conocidas and nombre not in conocidas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"La cola «{nombre}» no existe. Colas con trabajos: "
                + ", ".join(sorted(conocidas))
            ),
        )
    return nombre


class ColaReencolarIn(BaseModel):
    cola: str = Field(min_length=1, max_length=120)
    funcion: str | None = Field(default=None, max_length=200)
    limite: int = Field(default=registro_fallidos.TOPE_POR_LLAMADA, ge=1,
                        le=registro_fallidos.TOPE_POR_LLAMADA)
    #: `True` (el defecto) NO toca nada: cuenta y devuelve un ejemplo.
    probar: bool = True


class ColaVaciarIn(BaseModel):
    """Sin `funcion` ni `limite` a propósito: vaciar se lleva el registro
    entero. `extra="forbid"` para que una petición que *parezca* filtrada se
    rechace en vez de ignorarse en silencio y arrasar con todo."""

    model_config = ConfigDict(extra="forbid")

    cola: str = Field(min_length=1, max_length=120)
    probar: bool = True


@router.post("/colas/reencolar")
def colas_reencolar(
    payload: ColaReencolarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Vuelve a encolar trabajos del registro de fallidos de una cola.

    Es lo que recupera lo que se perdió: los lotes de webhook de Brevo
    llevan su payload completo en los argumentos. Con la inserción de
    eventos ya idempotente, reencolar no duplica nada.
    """
    salida = registro_fallidos.reencolar(
        _cola_valida(payload.cola), funcion=payload.funcion,
        limite=payload.limite, probar=payload.probar,
    )
    if not payload.probar:
        record_event(
            session,
            action=Action.QUEUE_FAILED_REQUEUED,
            target_type="rq_queue",
            target_id=payload.cola,
            actor=current_user,
            metadata={
                "funcion": payload.funcion, "tope": salida["tope"],
                "reencolados": salida["reencolados"],
                "fallos": salida["fallos_al_reencolar"],
                "en_registro": salida["en_registro"],
            },
        )
        session.commit()
        logger.warning("cuadre: %s reencoló %d trabajos de %s",
                       current_user.email, salida["reencolados"], payload.cola)
    return salida


@router.post("/colas/vaciar")
def colas_vaciar(
    payload: ColaVaciarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Descarta el registro de fallidos de una cola, con vista previa. Hasta
    ahora la única salida era entrar al contenedor a mano."""
    salida = registro_fallidos.vaciar(
        _cola_valida(payload.cola), probar=payload.probar
    )
    if not payload.probar:
        record_event(
            session,
            action=Action.QUEUE_FAILED_CLEARED,
            target_type="rq_queue",
            target_id=payload.cola,
            actor=current_user,
            metadata={
                "descartados": salida["descartados"],
                "sin_datos": salida["sin_datos"], "fallos": salida["fallos"],
                "en_registro": salida["en_registro"], "quedan": salida["quedan"],
            },
        )
        session.commit()
        logger.warning("cuadre: %s descartó %d trabajos fallidos de %s",
                       current_user.email, salida["descartados"], payload.cola)
    return salida
