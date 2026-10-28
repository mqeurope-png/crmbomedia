"""Soporte de terceros — la API (`/api/erp/soporte`): la lista blanca.

Aquí solo se administra la lista blanca de centros de soporte
(`soporte_fuentes`): alta, edición y «Comprobar». Buscar y leer artículos ya
no tienen ruta: los hace el propio asistente al redactar, en el servidor y
con su mismo ámbito (`app/services/asistente/consulta_fuentes.py`, casilla
«Soporte» de la franja). La Cola SAT tampoco consulta el soporte (28/10/2026).

- GET   /api/erp/soporte/fuentes                          (`erp.config`)
- POST  /api/erp/soporte/fuentes                          (`erp.config`)
- PATCH /api/erp/soporte/fuentes/{clave}                  (`erp.config`)
- POST  /api/erp/soporte/fuentes/{clave}/comprobar        (`erp.config`)

Es administración (`erp.config`) y queda en la auditoría como el resto de la
base de conocimiento. La forma de cada fuente es la `SoporteFuente` de
`frontend/src/app/lib/soporteApi.ts`; un error del servicio sale como
`{"detail": {"code", "detail"}}` con su HTTP.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.db.session import get_session
from app.erp.api.deps import require_config
from app.models.crm import User
from app.services import soporte
from app.services.soporte import fuentes as lista_blanca

router = APIRouter(prefix="/api/erp/soporte", tags=["erp-soporte"])


class FuenteIn(BaseModel):
    clave: str = Field(min_length=2, max_length=40)
    nombre: str = Field(min_length=1, max_length=120)
    dominio: str | None = Field(default=None, max_length=255)
    tipo: str = Field(default="zendesk", max_length=20)
    base_url: str | None = Field(default=None, max_length=500)
    marca: str | None = Field(default=None, max_length=40)
    locales: dict[str, str] = Field(default_factory=dict)
    activo: bool = True
    posicion: int | None = Field(default=None, ge=0, le=10000)
    notas: str | None = Field(default=None, max_length=2000)


class FuenteCambiosIn(BaseModel):
    nombre: str | None = Field(default=None, max_length=120)
    dominio: str | None = Field(default=None, max_length=255)
    tipo: str | None = Field(default=None, max_length=20)
    base_url: str | None = Field(default=None, max_length=500)
    marca: str | None = Field(default=None, max_length=40)
    locales: dict[str, str] | None = None
    activo: bool | None = None
    posicion: int | None = Field(default=None, ge=0, le=10000)
    notas: str | None = Field(default=None, max_length=2000)


def _http(exc: soporte.SoporteError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.codigo, "detail": str(exc)})


def _auditar(session: Session, actor: User, *, clave: str, accion: str,
             cambios: dict[str, Any]) -> None:
    record_event(
        session, action=Action.KB_CHANGED, target_type="kb_soporte_fuente", target_id=clave,
        actor=actor, metadata={"accion": accion, "cambios": cambios},
    )


# --- la lista blanca (administración) -------------------------------------------------


@router.get("/fuentes")
def listar_fuentes(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    return {"items": [soporte.fuente_como_dict(f) for f in lista_blanca.listar(session)]}


@router.post("/fuentes", status_code=status.HTTP_201_CREATED)
def crear_fuente(
    payload: FuenteIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Un centro de soporte nuevo en la lista blanca: desde ese momento el
    asistente puede buscar y leer en su dominio (y solo en él)."""
    try:
        fila = lista_blanca.crear(session, payload.model_dump())
    except soporte.SoporteError as exc:
        session.rollback()
        raise _http(exc) from exc
    _auditar(session, current_user, clave=fila.clave, accion="alta",
             cambios=payload.model_dump())
    session.commit()
    return soporte.fuente_como_dict(fila)


@router.patch("/fuentes/{clave}")
def editar_fuente(
    clave: str,
    payload: FuenteCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    # `null` explícito: vacía la marca o las notas; en el resto, se ignora.
    cambios = payload.model_dump(exclude_unset=True)
    try:
        fila = lista_blanca.actualizar(session, clave, cambios)
    except soporte.SoporteError as exc:
        session.rollback()
        raise _http(exc) from exc
    _auditar(session, current_user, clave=fila.clave, accion="cambio", cambios=cambios)
    session.commit()
    return soporte.fuente_como_dict(fila)


@router.post("/fuentes/{clave}/comprobar")
def comprobar_fuente(
    clave: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Comprobar»: qué devuelve de verdad la fuente (contenido, cáscara vacía
    que se pinta con JavaScript, o error). Se guarda con la fuente."""
    _ = current_user
    try:
        fila = soporte.comprobar_fuente(session, clave)
    except soporte.SoporteError as exc:
        raise _http(exc) from exc
    session.commit()
    return soporte.fuente_como_dict(fila)
