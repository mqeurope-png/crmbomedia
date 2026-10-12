"""Base de conocimiento — API (capacidad `erp.config`, la misma que
Configuración ERP, donde vive la pantalla).

- GET   /api/erp/conocimiento                       todo lo de la pantalla
- POST  /api/erp/conocimiento/sitios                una web o dominio nuevo
- PATCH /api/erp/conocimiento/sitios/{clave}        editar, desactivar
- POST  /api/erp/conocimiento/rutas                 una ruta (soporte o producto)
- PATCH /api/erp/conocimiento/rutas/{id}            URL, nota, activo, orden
- POST  /api/erp/conocimiento/rutas/comprobar       que cada URL devuelva 200
- POST  /api/erp/conocimiento/flux/categorias       una categoría de la KB de FLUX
- PATCH /api/erp/conocimiento/flux/categorias/{id}  modelo, palabras, activo
- POST  /api/erp/conocimiento/flux/sincronizar      «Sincronizar ahora» (en la petición)
- POST  /api/erp/conocimiento/resolver              qué enlaces (y qué catálogo) saldrían

Fase 2, el catálogo y los precios:

- GET    /api/erp/conocimiento/catalogo/productos         la tabla, con filtros
- POST   /api/erp/conocimiento/catalogo/sincronizar       {fuente} → una pasada (en la petición)
- PATCH  /api/erp/conocimiento/catalogo/marcas/{nombre}   idioma por defecto de la marca
- POST   /api/erp/conocimiento/catalogo/intereses         una fila interés → máquinas
- PATCH  /api/erp/conocimiento/catalogo/intereses/{id}    marca, patrón, nota, activo, orden
- DELETE /api/erp/conocimiento/catalogo/intereses/{id}

Fase 3, las imágenes y los vídeos de los correos compuestos:

- GET    /api/erp/conocimiento/imagenes                 el índice, con filtros
- PATCH  /api/erp/conocimiento/imagenes/{id}            favorita, oculta
- POST   /api/erp/conocimiento/imagenes/sincronizar     una pasada (en la petición)
- GET    /api/erp/conocimiento/videos                   la tabla, con filtros
- POST   /api/erp/conocimiento/videos                   {url} → un vídeo pegado a mano
- PATCH  /api/erp/conocimiento/videos/{id}              idioma, tema, marca, productos, visible
- POST   /api/erp/conocimiento/videos/sincronizar       una pasada (en la petición)

La configuración (sincronizaciones diarias, palabras de FLUX, meses hasta
«orientativo», IVA por fuente, canales de YouTube) va por `GET/PATCH
/api/erp/settings` (`conocimiento`), con el resto de ajustes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.db.session import get_session
from app.erp.api.deps import require_config
from app.models.conocimiento import FUENTE_COMPOSITOR, FUENTE_WOOCOMMERCE, FUENTES, SYNC_MANUAL
from app.models.crm import User
from app.services.conocimiento import (
    catalogo,
    catalogo_compositor,
    catalogo_tiendas,
    comprobar,
    enlaces,
    flux,
    imagenes,
    rutas,
    videos,
    zendesk,
)
from app.services.conocimiento.config import configuracion as config_kb
from app.services.conocimiento.config import guardar_palabras_interes as config_kb_palabras
from app.services.web_forms import sitios as registro_sitios

router = APIRouter(prefix="/api/erp/conocimiento", tags=["erp-conocimiento"])


class SitioIn(BaseModel):
    clave: str = Field(min_length=2, max_length=40)
    dominio: str = Field(min_length=3, max_length=120)
    host: str | None = Field(default=None, max_length=120)
    dominios_extra: list[str] = Field(default_factory=list, max_length=20)
    marca: str = Field(min_length=1, max_length=80)
    empresa: str = Field(min_length=1, max_length=80)
    tipo: str = Field(default="web", max_length=10)
    idioma_defecto: str = Field(default="es", max_length=5)
    idiomas: list[str] = Field(default_factory=lambda: ["es"], max_length=20)
    plugin_traduccion: str | None = Field(default=None, max_length=20)
    prefijo_idioma_defecto: bool = False
    slugs_traducidos: bool = False
    soporte_propio: bool = False
    remitente_defecto: str | None = Field(default=None, max_length=255)
    posicion: int | None = Field(default=None, ge=0, le=10000)


class SitioCambiosIn(BaseModel):
    dominio: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=120)
    dominios_extra: list[str] | None = Field(default=None, max_length=20)
    marca: str | None = Field(default=None, max_length=80)
    empresa: str | None = Field(default=None, max_length=80)
    tipo: str | None = Field(default=None, max_length=10)
    idioma_defecto: str | None = Field(default=None, max_length=5)
    idiomas: list[str] | None = Field(default=None, max_length=20)
    plugin_traduccion: str | None = Field(default=None, max_length=20)
    prefijo_idioma_defecto: bool | None = None
    slugs_traducidos: bool | None = None
    soporte_propio: bool | None = None
    remitente_defecto: str | None = Field(default=None, max_length=255)
    posicion: int | None = Field(default=None, ge=0, le=10000)
    activo: bool | None = None


class RutaIn(BaseModel):
    tipo: str = Field(max_length=10)
    sitio: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=8, max_length=500)
    idioma: str | None = Field(default=None, max_length=5)
    interes: str | None = Field(default=None, max_length=40)
    nota: str | None = Field(default=None, max_length=255)
    posicion: int | None = Field(default=None, ge=0, le=10000)


class RutaCambiosIn(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    nota: str | None = Field(default=None, max_length=255)
    activo: bool | None = None
    posicion: int | None = Field(default=None, ge=0, le=10000)


class CategoriaIn(BaseModel):
    categoria_id: str = Field(min_length=1, max_length=30)
    modelo: str = Field(min_length=1, max_length=80)
    palabras: list[str] | None = Field(default=None, max_length=30)
    posicion: int | None = Field(default=None, ge=0, le=10000)


class CategoriaCambiosIn(BaseModel):
    modelo: str | None = Field(default=None, max_length=80)
    palabras: list[str] | None = Field(default=None, max_length=30)
    activo: bool | None = None
    posicion: int | None = Field(default=None, ge=0, le=10000)


class ResolverIn(BaseModel):
    sitio: str | None = Field(default=None, max_length=40)
    idioma: str | None = Field(default=None, max_length=5)
    interes: str | None = Field(default=None, max_length=40)
    texto: str | None = Field(default=None, max_length=4000)
    modelo: str | None = Field(default=None, max_length=200)
    #: La máquina que tiene el cliente (lo que dicen sus pedidos): para probar
    #: qué consumibles le saldrían.
    maquina: str | None = Field(default=None, max_length=200)


class SincronizarCatalogoIn(BaseModel):
    #: `compositor`, `woocommerce` o vacío (las dos).
    fuente: str | None = Field(default=None, max_length=12)


class MarcaCambiosIn(BaseModel):
    idioma_defecto: str = Field(min_length=2, max_length=5)


class InteresProductoIn(BaseModel):
    interes: str = Field(min_length=2, max_length=40)
    marca: str = Field(default="", max_length=80)
    patron: str = Field(default="", max_length=80)
    nota: str | None = Field(default=None, max_length=255)
    posicion: int | None = Field(default=None, ge=0, le=10000)


class PalabrasInteresIn(BaseModel):
    #: Las palabras del interés; `null` vuelve a las de partida.
    palabras: list[str] | None = Field(default=None, max_length=200)


class ImagenCambiosIn(BaseModel):
    favorita: bool | None = None
    oculta: bool | None = None


class VideoIn(BaseModel):
    url: str = Field(min_length=10, max_length=500)


class ProductoRefIn(BaseModel):
    origen_id: str = Field(min_length=1, max_length=80)


class VideoCambiosIn(BaseModel):
    idioma: str | None = Field(default=None, max_length=5)
    tema: str | None = Field(default=None, max_length=80)
    marca: str | None = Field(default=None, max_length=80)
    productos: list[ProductoRefIn] | None = Field(default=None, max_length=20)
    visible: bool | None = None
    #: Los campos que vuelven a lo automático (idioma, tema, marca, productos).
    automatico: list[str] | None = Field(default=None, max_length=4)


class InteresProductoCambiosIn(BaseModel):
    interes: str | None = Field(default=None, max_length=40)
    marca: str | None = Field(default=None, max_length=80)
    patron: str | None = Field(default=None, max_length=80)
    nota: str | None = Field(default=None, max_length=255)
    activo: bool | None = None
    posicion: int | None = Field(default=None, ge=0, le=10000)


def _auditar(
    session: Session, actor: User, *, tipo: str, clave: str, accion: str,
    cambios: dict[str, Any] | None,
) -> None:
    record_event(
        session, action=Action.KB_CHANGED, target_type=f"kb_{tipo}", target_id=clave,
        actor=actor, metadata={"accion": accion, "cambios": cambios or {}},
    )


def _cambios(payload: BaseModel) -> dict[str, Any]:
    """Un `null` explícito no es «no» ni «vacío»: se ignora como si no viniera."""
    return {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}


def _todo(session: Session) -> dict[str, Any]:
    reg = registro_sitios.cargar(session)
    idiomas: set[str] = set()
    for s in reg.sitios:
        idiomas.update(s.idiomas)
    cfg = config_kb(session)
    return {
        "sitios": reg.como_lista(),
        "sitios_de_la_base": reg.de_la_base,
        "rutas": rutas.cargar(session).como_lista(),
        "flux": {
            "categorias": [c.como_dict() for c in flux.cargar_categorias(session)],
            "locales": dict(flux.LOCALES),
            **flux.estado_indice(session),
        },
        "catalogo": catalogo.estado(session, cfg),
        "interes_productos": catalogo.mapa_resuelto(session, cfg),
        "medios": _medios(session, cfg),
        "idiomas": sorted(idiomas),
    }


def _medios(session: Session, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "imagenes": imagenes.estado(session), "videos": videos.estado(session),
        "canales": list(cfg.get("youtube_canales") or []),
        "sincronizacion_diaria": bool(cfg.get("medios_sync_activa", True)),
    }


@router.get("")
def todo(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Sitios, rutas, categorías de FLUX y el estado del índice: lo que pinta
    la sección «Base de conocimiento» de Configuración ERP."""
    _ = current_user
    return _todo(session)


# --- sitios --------------------------------------------------------------------------


@router.post("/sitios", status_code=status.HTTP_201_CREATED)
def crear_sitio(
    payload: SitioIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Una web (o dominio de correo) nueva: desde ese momento su correo se
    captura y sus formularios y rutas la conocen, sin desplegar."""
    try:
        fila = registro_sitios.crear(session, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="sitio", clave=fila.clave, accion="alta",
             cambios=payload.model_dump())
    session.commit()
    return _todo(session)


@router.patch("/sitios/{clave}")
def editar_sitio(
    clave: str,
    payload: SitioCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    cambios = _cambios(payload)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        registro_sitios.actualizar(session, clave, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="sitio", clave=clave, accion="cambio", cambios=cambios)
    session.commit()
    return _todo(session)


# --- rutas ---------------------------------------------------------------------------


@router.post("/rutas", status_code=status.HTTP_201_CREATED)
def crear_ruta(
    payload: RutaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Una ruta nueva (soporte web × idioma, o página de producto por
    interés): el asistente la usa en el siguiente borrador."""
    try:
        fila = rutas.crear(session, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="ruta", clave=fila.id, accion="alta",
             cambios=payload.model_dump())
    session.commit()
    return _todo(session)


@router.patch("/rutas/{ruta_id}")
def editar_ruta(
    ruta_id: str,
    payload: RutaCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    cambios = _cambios(payload)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        rutas.actualizar(session, ruta_id, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="ruta", clave=ruta_id, accion="cambio", cambios=cambios)
    session.commit()
    return _todo(session)


@router.post("/rutas/comprobar")
def comprobar_rutas(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Pide cada URL activa y guarda el código que devuelve. Tarda lo que
    tarden las webs (unos segundos)."""
    _ = current_user
    resultados = comprobar.comprobar(session)
    session.commit()
    return {"resultados": resultados, "rutas": rutas.cargar(session).como_lista()}


# --- FLUX ----------------------------------------------------------------------------


@router.post("/flux/categorias", status_code=status.HTTP_201_CREATED)
def crear_categoria(
    payload: CategoriaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        fila = flux.crear_categoria(session, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="flux_categoria", clave=fila.categoria_id,
             accion="alta", cambios=payload.model_dump())
    session.commit()
    return _todo(session)


@router.patch("/flux/categorias/{categoria_id}")
def editar_categoria(
    categoria_id: str,
    payload: CategoriaCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    cambios = _cambios(payload)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        flux.actualizar_categoria(session, categoria_id, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="flux_categoria", clave=categoria_id, accion="cambio",
             cambios=cambios)
    session.commit()
    return _todo(session)


@router.post("/flux/sincronizar")
def sincronizar_flux(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Sincronizar ahora»: una pasada completa en la petición (seis locales,
    unas decenas de páginas). La caída de FLUX deja el índice anterior y lo
    dice en el resultado."""
    pasada = zendesk.sincronizar(session, origen=SYNC_MANUAL)
    _auditar(session, current_user, tipo="flux_sync", clave=pasada.id, accion="manual",
             cambios={"estado": pasada.estado, "articulos": pasada.articulos})
    session.commit()
    return {"sincronizacion": flux.sincronizacion_como_dict(pasada),
            **flux.estado_indice(session)}


# --- probar --------------------------------------------------------------------------


@router.post("/resolver")
def resolver(
    payload: ResolverIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Qué enlaces le saldrían al asistente para esa web, idioma, interés y
    texto, y qué máquinas y consumibles del catálogo: para probar una ruta o
    una fila nueva sin esperar a un lead."""
    _ = current_user
    salida = enlaces.enlaces_para(
        session, sitio=payload.sitio, idioma=payload.idioma, interes=payload.interes,
        texto=payload.texto, modelo=payload.modelo,
    )
    salida["catalogo"] = catalogo.para_asistente(
        session, intereses=[payload.interes] if payload.interes else [],
        idioma=(payload.idioma or "es").strip().lower()[:2], maquina=payload.maquina,
        modelo_flux=payload.modelo, texto_cliente=payload.texto,
    )
    return salida


# --- catálogo (Fase 2) ------------------------------------------------------------------


@router.get("/catalogo/productos")
def productos_catalogo(
    fuente: str | None = Query(default=None, max_length=12),
    tipo: str | None = Query(default=None, max_length=12),
    idioma: str | None = Query(default=None, max_length=5),
    marca: str | None = Query(default=None, max_length=80),
    sitio: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=120),
    con_idos: bool = Query(default=False),
    con_descartados: bool = Query(default=False),
    limite: int = Query(default=500, ge=1, le=5000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """La tabla del catálogo, filtrada (los idos y los descartados solo si se
    piden)."""
    _ = current_user
    filas = catalogo.listar(
        session, fuente=fuente or None, tipo=tipo or None, idioma=idioma or None,
        marca=marca or None, sitio=sitio or None, texto=q, con_idos=con_idos,
        con_descartados=con_descartados, limite=limite,
    )
    return {"productos": [catalogo.producto_como_dict(p) for p in filas], "total": len(filas),
            "limite": limite}


@router.post("/catalogo/sincronizar")
def sincronizar_catalogo(
    payload: SincronizarCatalogoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Sincronizar ahora»: el compositor (una petición a Supabase), las
    tiendas (unas decenas de páginas) o las dos, en la petición. Un origen
    caído deja el catálogo anterior y lo dice en el resultado."""
    fuente = (payload.fuente or "").strip().lower() or None
    if fuente is not None and fuente not in FUENTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Fuente desconocida: {fuente!r} ({' o '.join(FUENTES)}).")
    ahora = datetime.now(UTC)
    pasadas = []
    if fuente in (None, FUENTE_COMPOSITOR):
        pasadas.append(catalogo_compositor.sincronizar(session, origen=SYNC_MANUAL, ahora=ahora))
    if fuente in (None, FUENTE_WOOCOMMERCE):
        pasadas.append(catalogo_tiendas.sincronizar(session, origen=SYNC_MANUAL, ahora=ahora))
    for pasada in pasadas:
        _auditar(session, current_user, tipo="catalogo_sync", clave=pasada.id, accion="manual",
                 cambios={"fuente": pasada.fuente, "estado": pasada.estado,
                          "productos": pasada.productos})
    session.commit()
    cfg = config_kb(session)
    return {
        "sincronizaciones": [catalogo.sincronizacion_como_dict(p) for p in pasadas],
        "catalogo": catalogo.estado(session, cfg),
        "interes_productos": catalogo.mapa_resuelto(session, cfg),
    }


@router.patch("/catalogo/marcas/{nombre}")
def editar_marca(
    nombre: str,
    payload: MarcaCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        catalogo.actualizar_marca(session, nombre, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="catalogo_marca", clave=nombre, accion="cambio",
             cambios=payload.model_dump())
    session.commit()
    return _todo(session)


@router.post("/catalogo/intereses", status_code=status.HTTP_201_CREATED)
def crear_interes_producto(
    payload: InteresProductoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Una fila interés → máquinas nueva: el asistente la usa en el siguiente
    borrador, sin desplegar."""
    try:
        fila = catalogo.crear_mapa(session, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="interes_producto", clave=fila.id, accion="alta",
             cambios=payload.model_dump())
    session.commit()
    return _todo(session)


@router.patch("/catalogo/intereses/{fila_id}")
def editar_interes_producto(
    fila_id: str,
    payload: InteresProductoCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    cambios = _cambios(payload)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        catalogo.actualizar_mapa(session, fila_id, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="interes_producto", clave=fila_id, accion="cambio",
             cambios=cambios)
    session.commit()
    return _todo(session)


@router.delete("/catalogo/intereses/{fila_id}")
def borrar_interes_producto(
    fila_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        catalogo.borrar_mapa(session, fila_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _auditar(session, current_user, tipo="interes_producto", clave=fila_id, accion="baja",
             cambios=None)
    session.commit()
    return _todo(session)


@router.put("/catalogo/intereses/palabras/{interes}")
def guardar_palabras_interes(
    interes: str,
    payload: PalabrasInteresIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Las palabras que señalan un interés en la consulta o en la instrucción
    (`uv` = la UV de cualquier tamaño). `palabras: null` vuelve a las de
    partida. Se usan desde el siguiente borrador, sin desplegar."""
    try:
        guardadas = config_kb_palabras(session, interes, payload.palabras)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="interes_palabras", clave=interes.strip().lower(),
             accion="cambio", cambios={"palabras": guardadas})
    session.commit()
    return _todo(session)


# --- imágenes y vídeos (Fase 3) ---------------------------------------------------------------


@router.get("/imagenes")
def listar_imagenes(
    sitio: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=120),
    favoritas: bool = Query(default=False),
    con_ocultas: bool = Query(default=True),
    limite: int = Query(default=600, ge=1, le=5000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """El índice de imágenes de las webs (favoritas primero), buscando por el título."""
    _ = current_user
    filas = imagenes.listar(session, sitio=sitio or None, texto=q, favoritas=favoritas,
                            con_ocultas=con_ocultas, limite=limite)
    return {"imagenes": [imagenes.como_dict(f) for f in filas], "total": len(filas),
            "limite": limite}


@router.patch("/imagenes/{imagen_id}")
def editar_imagen(
    imagen_id: str,
    payload: ImagenCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Favorita (va primero en lo que se le ofrece a la IA) u oculta (no se le
    ofrece nunca)."""
    cambios = _cambios(payload)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        fila = imagenes.actualizar(session, imagen_id, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="imagen", clave=imagen_id, accion="cambio",
             cambios=cambios)
    session.commit()
    return {"imagen": imagenes.como_dict(fila), "estado": imagenes.estado(session)}


@router.post("/imagenes/sincronizar")
def sincronizar_imagenes(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Sincronizar ahora»: la biblioteca de medios de las ocho webs, en la
    petición (medio minuto). Una web caída deja su índice como estaba."""
    pasada = imagenes.sincronizar(session, origen=SYNC_MANUAL)
    _auditar(session, current_user, tipo="imagenes_sync", clave=pasada.id, accion="manual",
             cambios={"estado": pasada.estado, "imagenes": pasada.productos})
    session.commit()
    return {"sincronizacion": catalogo.sincronizacion_como_dict(pasada),
            "estado": imagenes.estado(session)}


@router.get("/videos")
def listar_videos(
    q: str | None = Query(default=None, max_length=120),
    idioma: str | None = Query(default=None, max_length=5),
    canal: str | None = Query(default=None, max_length=120),
    origen: str | None = Query(default=None, max_length=10),
    tema: str | None = Query(default=None, max_length=80),
    con_ocultos: bool = Query(default=True),
    con_cortos: bool = Query(default=True),
    limite: int = Query(default=1000, ge=1, le=5000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """La tabla de vídeos, los más recientes primero."""
    _ = current_user
    filas = videos.listar(session, texto=q, idioma=idioma or None, canal=canal or None,
                          origen=origen or None, tema=tema or None, con_ocultos=con_ocultos,
                          con_cortos=con_cortos, limite=limite)
    return {"videos": [videos.como_dict(f) for f in filas], "total": len(filas),
            "limite": limite}


@router.post("/videos", status_code=status.HTTP_201_CREATED)
def anadir_video(
    payload: VideoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Un vídeo de YouTube pegado a mano: entra en la tabla (con sus datos de
    YouTube si hay clave) y no se va aunque no esté en ningún canal."""
    try:
        fila = videos.anadir_por_url(session, payload.url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="video", clave=fila.id, accion="alta",
             cambios={"url": payload.url})
    session.commit()
    return {"video": videos.como_dict(fila), "estado": videos.estado(session)}


@router.patch("/videos/{fila_id}")
def editar_video(
    fila_id: str,
    payload: VideoCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Idioma, tema, marca, productos y si es visible. Lo corregido queda
    fijado: la sincronización no lo pisa (salvo «volver a lo automático»).
    Un texto vacío en idioma, tema o marca lo deja sin valor."""
    cambios = payload.model_dump(exclude_unset=True)
    if "productos" in cambios and cambios["productos"] is not None:
        cambios["productos"] = [p["origen_id"] for p in cambios["productos"]]
    cambios = {k: v for k, v in cambios.items()
               if v is not None or k in ("idioma", "tema", "marca")}
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        fila = videos.actualizar(session, fila_id, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar(session, current_user, tipo="video", clave=fila_id, accion="cambio",
             cambios=cambios)
    session.commit()
    return {"video": videos.como_dict(fila), "estado": videos.estado(session)}


@router.post("/videos/sincronizar")
def sincronizar_videos(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Sincronizar ahora»: los canales de YouTube, los vídeos enlazados en las
    webs y las miniaturas, en la petición (medio minuto). Sin clave o con la
    API caída, la tabla anterior queda intacta y lo dice."""
    pasada = videos.sincronizar(session, origen=SYNC_MANUAL)
    _auditar(session, current_user, tipo="videos_sync", clave=pasada.id, accion="manual",
             cambios={"estado": pasada.estado, "videos": pasada.productos})
    session.commit()
    return {"sincronizacion": catalogo.sincronizacion_como_dict(pasada),
            "estado": videos.estado(session)}
