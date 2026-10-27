"""Índice de las webs propias — la API (`/api/erp/webs`).

Buscar en el índice (para el redactor), el estado por web y tipo, «Reindexar
ahora» (en cola, en worker-web) y las reglas de qué entra y qué no. Solo lee
de las webs, por sus endpoints públicos. Las formas de las respuestas son las
de `frontend/src/app/lib/websApi.ts`.

- GET    /buscar?q=&idioma=&sitio=&tipo=   fragmentos (cualquiera con ERP: el redactor)
- GET    /estado                           por web: vivo por tipo, idos, idiomas, último
- POST   /reindexar                        encola un recorrido (o devuelve el que hay)
- GET    /reglas                           las reglas (con la tabla vacía, las de partida)
- POST   /reglas                           una regla nueva
- PATCH  /reglas/{id}                      patrón, web, nota, activo, orden
- DELETE /reglas/{id}

Todo menos `buscar` es de Configuración ERP (`erp.config`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_config, require_erp_view
from app.models.crm import User
from app.models.fuentes_externas import REGLA_TIPOS, WEB_TIPOS, WebIndiceRegla
from app.services.web_forms import sitios as registro_sitios
from app.services.webs import busqueda, indice, pasadas
from app.services.webs import reglas as reglas_mod

router = APIRouter(prefix="/api/erp/webs", tags=["erp-webs"])

#: Lo más largo que se acepta como búsqueda.
MAX_CONSULTA = 200


def _error(codigo: str, mensaje: str, http: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    """El error del ERP de siempre: `{"code", "detail"}` (la pantalla enseña
    `detail`)."""
    return HTTPException(http, {"code": codigo, "detail": mensaje})


@router.get("/buscar")
def buscar(
    q: str = Query(default="", max_length=MAX_CONSULTA),
    idioma: str | None = Query(default=None, max_length=5),
    sitio: str | None = Query(default=None, max_length=40),
    tipo: str | None = Query(default=None, max_length=12),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    if tipo and tipo not in WEB_TIPOS:
        raise _error("tipo_no_valido", f"Tipo no válido: {tipo} ({', '.join(WEB_TIPOS)}).")
    items = busqueda.buscar(session, q, idioma=(idioma or None), sitio=(sitio or None),
                            tipo=(tipo or None))
    return {"items": [busqueda.fragmento_como_dict(f) for f in items]}


@router.get("/estado")
def estado(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    webs = [(s.clave, host) for s, host in pasadas.webs_a_indexar(session)]
    datos = indice.estado_por_web(session, webs)
    en_curso = pasadas.en_curso(session)
    session.commit()
    return {
        **datos,
        "ultima_pasada": pasadas.pasada_como_dict(pasadas.ultima(session)),
        "en_curso": pasadas.pasada_como_dict(en_curso),
    }


@router.post("/reindexar")
def reindexar(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Encola un recorrido de todas las webs (o devuelve el que ya está en cola
    o en curso). No recorre en la petición: la pantalla consulta `/estado`."""
    pasada = pasadas.encolar_pasada(session, pasadas.ORIGEN_MANUAL, current_user.id)
    return pasadas.pasada_como_dict(pasada) or {}


# --- reglas ----------------------------------------------------------------------------------


class ReglaIn(BaseModel):
    tipo: str | None = Field(default=None, max_length=30)
    patron: str | None = Field(default=None, max_length=255)
    sitio: str | None = Field(default=None, max_length=40)
    activo: bool | None = None
    nota: str | None = Field(default=None, max_length=255)
    posicion: int | None = Field(default=None, ge=0, le=100000)


def _sitio_valido(session: Session, sitio: str | None) -> str | None:
    clave = (sitio or "").strip()
    if not clave:
        return None
    conocidas = {s.clave for s in registro_sitios.cargar(session).sitios
                 if s.tipo == registro_sitios.TIPO_WEB}
    if clave not in conocidas:
        raise _error("sitio_no_valido", f"«{clave}» no es ninguna web de la tabla de sitios.")
    return clave


def _exigir_sitio(tipo: str, sitio: str | None) -> None:
    """Las reglas por id llevan su web: los ids de WordPress son de cada web, y
    sin web la regla valdría para todas (tocaría lo que no es)."""
    if tipo in reglas_mod.TIPOS_ID and not sitio:
        raise _error("sitio_obligatorio",
                     "Una regla por id necesita su web: los ids de WordPress son de cada web.",
                     status.HTTP_422_UNPROCESSABLE_ENTITY)


def _regla(session: Session, regla_id: int) -> WebIndiceRegla:
    fila = session.get(WebIndiceRegla, regla_id)
    if fila is None:
        raise _error("regla_no_encontrada", "Esa regla no existe.", status.HTTP_404_NOT_FOUND)
    return fila


@router.get("/reglas")
def listar_reglas(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    if reglas_mod.asegurar_sembradas(session):
        session.commit()
    filas = session.scalars(select(WebIndiceRegla).order_by(
        WebIndiceRegla.tipo, WebIndiceRegla.posicion, WebIndiceRegla.id))
    return {"items": [reglas_mod.regla_como_dict(f) for f in filas]}


@router.post("/reglas", status_code=status.HTTP_201_CREATED)
def crear_regla(
    payload: ReglaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    tipo = (payload.tipo or "").strip()
    if tipo not in REGLA_TIPOS:
        raise _error("tipo_no_valido",
                     f"Tipo de regla no válido: {tipo or '—'} ({', '.join(REGLA_TIPOS)}).")
    try:
        patron = reglas_mod.validar_patron(tipo, payload.patron)
    except ValueError as exc:
        raise _error("patron_no_valido", str(exc)) from exc
    sitio = _sitio_valido(session, payload.sitio)
    _exigir_sitio(tipo, sitio)
    # Con la tabla vacía valían las de partida: se siembran antes, para que la
    # regla nueva no las deje sin efecto.
    reglas_mod.asegurar_sembradas(session)
    fila = WebIndiceRegla(tipo=tipo, patron=patron, sitio=sitio,
                          activo=True if payload.activo is None else payload.activo,
                          nota=(payload.nota or "").strip() or None,
                          posicion=payload.posicion or 0)
    session.add(fila)
    session.commit()
    return reglas_mod.regla_como_dict(fila)


@router.patch("/reglas/{regla_id}")
def actualizar_regla(
    regla_id: int,
    payload: ReglaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    fila = _regla(session, regla_id)
    cambios = payload.model_dump(exclude_unset=True)
    tipo = (cambios.get("tipo") or fila.tipo).strip()
    if tipo not in REGLA_TIPOS:
        raise _error("tipo_no_valido",
                     f"Tipo de regla no válido: {tipo} ({', '.join(REGLA_TIPOS)}).")
    # Lo que quedaría tras el cambio: una regla por id no se queda sin web.
    sitio = _sitio_valido(session, cambios["sitio"]) if "sitio" in cambios else fila.sitio
    _exigir_sitio(tipo, sitio)
    if "tipo" in cambios or "patron" in cambios:
        try:
            fila.patron = reglas_mod.validar_patron(tipo, cambios.get("patron", fila.patron))
        except ValueError as exc:
            raise _error("patron_no_valido", str(exc)) from exc
        fila.tipo = tipo
    if "sitio" in cambios:
        fila.sitio = sitio
    if cambios.get("activo") is not None:
        fila.activo = bool(cambios["activo"])
    if "nota" in cambios:
        fila.nota = (cambios["nota"] or "").strip() or None
    if cambios.get("posicion") is not None:
        fila.posicion = int(cambios["posicion"])
    session.commit()
    return reglas_mod.regla_como_dict(fila)


@router.delete("/reglas/{regla_id}")
def borrar_regla(
    regla_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    _ = current_user
    session.delete(_regla(session, regla_id))
    session.commit()
    return {"ok": True}
