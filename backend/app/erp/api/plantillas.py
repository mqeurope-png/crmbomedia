"""Plantillas de documento — la API (`/api/erp/plantillas`).

Plantillas (listar con buscador y filtros, crear desde una proforma, un
albarán o un pedido o a mano, editar, mover, etiquetar, desactivar), sus
líneas en la forma de cada destino (oferta, proforma, pedido), carpetas
(árbol, crear, renombrar, mover, borrar), acceso por carpeta y la carpeta por
defecto de cada usuario.

Permisos: leer, `erp.office.view`; escribir, `erp.proformas` (los mismos que
las ofertas); el acceso por carpeta, `erp.config` (administración). La
visibilidad por carpeta se aplica a TODO: árbol, lista, buscador, detalle,
líneas y crear una oferta con `plantilla_id`.

Nada escribe en FACTUSOL: guardar desde una proforma o un albarán solo LEE.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp import plantillas as svc
from app.erp.api.deps import require_config, require_office_view, require_proformas
from app.erp.capabilities import Cap, has_capability
from app.erp.models import Order, PlantillaCarpeta
from app.erp.ofertas import DESCRIPCION_MAX, OfertaError, normalizar_lineas
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/plantillas", tags=["erp-plantillas"])


def _http(exc: OfertaError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "detail": str(exc), **exc.extra})


class LineaIn(BaseModel):
    codart: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, max_length=64)
    descripcion: str = Field(default="", max_length=DESCRIPCION_MAX)
    cantidad: float = Field(default=1, ge=0)
    precio: float = Field(default=0, ge=0)
    descuento_pct: float = Field(default=0, ge=0, le=100)
    iva_pct: float = Field(default=21, ge=0, le=100)


def _lineas(items: list[LineaIn] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    try:
        return normalizar_lineas([ln.model_dump() for ln in items])
    except OfertaError as exc:
        raise _http(exc) from exc


class OrigenIn(BaseModel):
    """De qué documento se guarda la plantilla."""

    tipo: str = Field(pattern="^(presupuesto|albaran|pedido)$")
    serie: int | None = Field(default=None, ge=1, le=99)
    codigo: int | None = Field(default=None, ge=1)
    #: Ejercicio de FACTUSOL del documento (por defecto, el activo).
    ejercicio: str | None = Field(default=None, pattern=r"^\d{4}$")
    order_id: str | None = Field(default=None, max_length=36)


class PlantillaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=svc.NOMBRE_MAX)
    descripcion: str | None = Field(default=None, max_length=2000)
    idioma: str = Field(default="es", pattern="^(es|en|de|fr|nl)$")
    #: Sin carpeta: la carpeta por defecto del usuario (si la tiene).
    carpeta_id: str | None = Field(default=None, max_length=36)
    etiquetas: list[str] = Field(default_factory=list)
    activa: bool = True
    #: Desde un documento (solo lectura)…
    origen: OrigenIn | None = None
    #: …o con las líneas escritas a mano.
    lineas: list[LineaIn] | None = None


class PlantillaPatch(BaseModel):
    nombre: str | None = Field(default=None, max_length=svc.NOMBRE_MAX)
    descripcion: str | None = Field(default=None, max_length=2000)
    idioma: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")
    #: `null` explícito = a la raíz; sin el campo, no se mueve.
    carpeta_id: str | None = Field(default=None, max_length=36)
    etiquetas: list[str] | None = None
    activa: bool | None = None
    lineas: list[LineaIn] | None = None


class CarpetaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=svc.CARPETA_NOMBRE_MAX)
    parent_id: str | None = Field(default=None, max_length=36)


class CarpetaPatch(BaseModel):
    nombre: str | None = Field(default=None, max_length=svc.CARPETA_NOMBRE_MAX)
    #: `null` explícito = a la raíz; sin el campo, no se mueve.
    parent_id: str | None = Field(default=None, max_length=36)


class AccesoIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


class PreferenciaIn(BaseModel):
    carpeta_id: str | None = Field(default=None, max_length=36)


# --- meta, etiquetas, usuarios ---------------------------------------------------


@router.get("/meta")
def meta_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {
        "idiomas": svc.idiomas(),
        "destinos": list(svc.DESTINOS),
        "max_profundidad": svc.MAX_PROFUNDIDAD,
        "carpeta_defecto": svc.carpeta_defecto(session, current_user),
        "gestiona_acceso": has_capability(current_user, Cap.CONFIG),
        "acceso_restringido": svc.carpetas_permitidas(session, current_user) is not None,
    }


@router.get("/etiquetas")
def etiquetas_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {"items": svc.etiquetas_en_uso(session, current_user)}


@router.get("/usuarios")
def usuarios_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Los usuarios activos que pueden entrar al ERP, para elegir a quién se
    acota el acceso a una carpeta. `restringido`: ya tiene alguna carpeta."""
    _ = current_user
    from app.erp.models import PlantillaCarpetaAcceso  # noqa: PLC0415

    con_acceso = set(session.scalars(select(PlantillaCarpetaAcceso.user_id)))
    filas = session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    return {"items": [{
        "id": u.id, "nombre": u.full_name or u.email, "email": u.email,
        "rol": str(getattr(u.role, "value", u.role)), "restringido": u.id in con_acceso,
    } for u in filas if has_capability(u, Cap.OFFICE_VIEW)]}


# --- carpetas ---------------------------------------------------------------------


@router.get("/carpetas")
def carpetas_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {
        "items": svc.arbol(session, current_user,
                           con_acceso=has_capability(current_user, Cap.CONFIG)),
        "carpeta_defecto": svc.carpeta_defecto(session, current_user),
    }


@router.post("/carpetas", status_code=status.HTTP_201_CREATED)
def crear_carpeta_endpoint(
    payload: CarpetaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    try:
        carpeta = svc.crear_carpeta(session, current_user, nombre=payload.nombre,
                                    parent_id=payload.parent_id)
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return {"id": carpeta.id, "nombre": carpeta.nombre, "parent_id": carpeta.parent_id}


def _carpeta(session: Session, user: User, carpeta_id: str) -> PlantillaCarpeta:
    try:
        return svc.carpeta_visible(session, user, carpeta_id)
    except OfertaError as exc:
        raise _http(exc) from exc


@router.patch("/carpetas/{carpeta_id}")
def patch_carpeta_endpoint(
    carpeta_id: str,
    payload: CarpetaPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    carpeta = _carpeta(session, current_user, carpeta_id)
    try:
        svc.actualizar_carpeta(
            session, current_user, carpeta, nombre=payload.nombre,
            parent_id=(payload.parent_id if "parent_id" in payload.model_fields_set
                       else svc.SIN),
        )
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return {"id": carpeta.id, "nombre": carpeta.nombre, "parent_id": carpeta.parent_id}


@router.delete("/carpetas/{carpeta_id}")
def borrar_carpeta_endpoint(
    carpeta_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """Borra la carpeta, no su contenido: sus plantillas y subcarpetas suben
    a la raíz (como las carpetas de las plantillas de correo)."""
    carpeta = _carpeta(session, current_user, carpeta_id)
    svc.borrar_carpeta(session, carpeta)
    session.commit()
    return {"ok": True}


@router.put("/carpetas/{carpeta_id}/acceso")
def acceso_carpeta_endpoint(
    carpeta_id: str,
    payload: AccesoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Quién tiene acceso a la carpeta. Quien esté en alguna carpeta SOLO ve
    sus carpetas; quien no esté en ninguna, las ve todas."""
    carpeta = _carpeta(session, current_user, carpeta_id)
    try:
        user_ids = svc.fijar_acceso(session, carpeta, payload.user_ids)
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return {"id": carpeta.id, "acceso_user_ids": user_ids}


@router.put("/preferencias/carpeta")
def preferencia_carpeta_endpoint(
    payload: PreferenciaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    """La carpeta por defecto del usuario (o ninguna)."""
    try:
        carpeta_id = svc.fijar_carpeta_defecto(session, current_user, payload.carpeta_id)
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return {"carpeta_defecto": carpeta_id}


# --- plantillas -----------------------------------------------------------------


@router.get("")
def listar_endpoint(
    q: str | None = Query(default=None, max_length=200),
    carpeta_id: str | None = Query(default=None, max_length=36),
    sin_carpeta: bool = Query(default=False),
    etiqueta: list[str] = Query(default_factory=list),
    idioma: str | None = Query(default=None, pattern="^(es|en|de|fr|nl)$"),
    activas: bool = Query(default=False),
    contact_id: str | None = Query(default=None, max_length=36),
    company_id: str | None = Query(default=None, max_length=36),
    order_id: str | None = Query(default=None, max_length=36),
    serie: int | None = Query(default=None, ge=1, le=99),
    limit: int = Query(default=300, ge=1, le=1000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Las plantillas que el usuario ve. Con un cliente (`contact_id`,
    `company_id` u `order_id`) salen primero las de su idioma y cada una dice
    si coincide; las demás siguen ahí."""
    filas = svc.listar(session, current_user, q=q, carpeta_id=carpeta_id,
                       sin_carpeta=sin_carpeta, etiquetas=etiqueta, idioma=idioma,
                       solo_activas=activas, limit=limit)
    idioma_cliente, fuente = svc.idioma_cliente(
        session, contact_id=contact_id, company_id=company_id, order_id=order_id, serie=serie)
    filas = svc.ordenar_por_idioma(filas, idioma_cliente)
    rutas = svc.rutas_carpetas(session)
    palabras = svc.palabras_busqueda(q)
    return {
        "items": [svc.plantilla_como_dict(
            p, con_lineas=False, rutas=rutas,
            coincidencias=svc.coincidencias(p, palabras) if palabras else None,
            idioma_cliente=idioma_cliente,
        ) for p in filas],
        "total": len(filas),
        "idioma_cliente": idioma_cliente,
        "idioma_fuente": fuente,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def crear_endpoint(
    payload: PlantillaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """«Guardar como plantilla»: desde una proforma o un albarán de FACTUSOL
    (copia sus líneas con sus artículos, solo LEYENDO), desde un pedido de
    BoHub, o con líneas a mano."""
    carpeta_id = payload.carpeta_id
    if carpeta_id is None and "carpeta_id" not in payload.model_fields_set:
        carpeta_id = svc.carpeta_defecto(session, current_user)
    comunes: dict[str, Any] = {
        "descripcion": payload.descripcion, "idioma": payload.idioma,
        "carpeta_id": carpeta_id, "etiquetas": payload.etiquetas, "activa": payload.activa,
    }
    try:
        origen = payload.origen
        if origen is not None and origen.tipo == svc.ORIGEN_PEDIDO:
            order = session.get(Order, origen.order_id) if origen.order_id else None
            if order is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, {
                    "code": "order_not_found", "detail": "El pedido no existe."})
            plantilla = svc.plantilla_desde_pedido(session, order, nombre=payload.nombre,
                                                   usuario=current_user, **comunes)
        elif origen is not None:
            if origen.serie is None or origen.codigo is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
                    "code": "origen_incompleto",
                    "detail": "Falta la serie o el número del documento."})
            from app.erp.api.factusol import (  # noqa: PLC0415
                _client_and_ejercicio,
                _factusol_gateway_error,
            )
            from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

            client, ejercicio = _client_and_ejercicio(session)
            try:
                plantilla = svc.plantilla_desde_documento(
                    session, client, tipo=origen.tipo, serie=origen.serie,
                    codigo=origen.codigo, ejercicio=origen.ejercicio or ejercicio,
                    nombre=payload.nombre, usuario=current_user, **comunes)
            except FactusolError as exc:
                raise _factusol_gateway_error(exc, "document_read_failed") from exc
        else:
            plantilla = svc.crear_plantilla(
                session, nombre=payload.nombre, lineas=_lineas(payload.lineas) or [],
                usuario=current_user, **comunes)
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.plantilla_como_dict(plantilla, rutas=svc.rutas_carpetas(session))


def _plantilla(session: Session, user: User, plantilla_id: str):  # noqa: ANN202
    try:
        return svc.plantilla_visible(session, user, plantilla_id)
    except OfertaError as exc:
        raise _http(exc) from exc


@router.get("/{plantilla_id}")
def detalle_endpoint(
    plantilla_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    return svc.plantilla_como_dict(_plantilla(session, current_user, plantilla_id),
                                   rutas=svc.rutas_carpetas(session))


@router.patch("/{plantilla_id}")
def patch_endpoint(
    plantilla_id: str,
    payload: PlantillaPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    plantilla = _plantilla(session, current_user, plantilla_id)
    try:
        svc.actualizar_plantilla(
            session, plantilla, usuario=current_user, nombre=payload.nombre,
            descripcion=payload.descripcion, activa=payload.activa, idioma=payload.idioma,
            lineas=_lineas(payload.lineas), etiquetas=payload.etiquetas,
            carpeta_id=(payload.carpeta_id if "carpeta_id" in payload.model_fields_set
                        else svc.SIN),
        )
    except OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.plantilla_como_dict(plantilla, rutas=svc.rutas_carpetas(session))


@router.get("/{plantilla_id}/lineas")
def lineas_endpoint(
    plantilla_id: str,
    destino: str = Query(pattern="^(oferta|proforma|pedido)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Las líneas de la plantilla en la forma del flujo del destino (la
    plantilla solo aporta las líneas) y los avisos de lo que no cabe tal
    cual (p. ej. el descuento en un pedido)."""
    plantilla = _plantilla(session, current_user, plantilla_id)
    try:
        lineas, avisos = svc.lineas_para(plantilla, destino)
    except OfertaError as exc:
        raise _http(exc) from exc
    return {
        "plantilla": svc.plantilla_como_dict(plantilla, con_lineas=False,
                                             rutas=svc.rutas_carpetas(session)),
        "destino": destino, "lineas": lineas, "avisos": avisos,
    }
