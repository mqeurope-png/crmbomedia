"""ERP · Histórico de ventas: «¿quién compró qué?» (30/10/2026).

Lee lo que BoHub ya ha volcado de FACTUSOL (`app/erp/historico.py`), nunca
FACTUSOL en vivo. Lanzar una carga solo encola un trabajo que LEE FACTUSOL.

Permisos: ver y exportar (`require_office_view`: lleva datos de clientes, el
taller no lo ve), lanzar cargas (`require_documents`), clasificar familias, ampliar
las gamas, editar las reglas que proponen y la clase propia de un artículo
(`require_config`: lo decide Bart).
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp import historico
from app.erp import historico_clasificacion as clasificacion
from app.erp.api.deps import require_config, require_documents, require_office_view
from app.erp.models.historico import CLASES_FAMILIA
from app.models.crm import User


def _recalcular_resumen_tras_escribir(request: Request, tareas: BackgroundTasks) -> None:
    """Tras una escritura (marcas, familias, equivalencias, alias, bajas,
    series, umbrales…) se programa el recálculo del resumen ERP por empresa
    (1/11/2026), como tarea de fondo: va DESPUÉS de responder, y solo si la
    respuesta sale bien (una petición rechazada no lo programa)."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        from app.services.empresa_erp_job import tras_cambio  # noqa: PLC0415

        tareas.add_task(tras_cambio)


router = APIRouter(prefix="/api/erp/historico", tags=["erp-historico"],
                   dependencies=[Depends(_recalcular_resumen_tras_escribir)])


def _en_curso(session: Session) -> str:
    from app.erp.ejercicio import ejercicio_en_curso  # noqa: PLC0415

    return ejercicio_en_curso(session)


@router.get("/ejercicios")
def estado(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Cómo está la carga de cada ejercicio (2014 → en curso) y el total de
    líneas de producto y de nota."""
    en_curso = _en_curso(session)
    return {
        "en_curso": en_curso,
        "ejercicios": historico.estado_ejercicios(session, en_curso),
        "notas": historico.resumen_notas(session),
    }


class CargarIn(BaseModel):
    #: Vacío = todos (2014 → en curso).
    ejercicios: list[str] = Field(default_factory=list, max_length=40)


@router.post("/cargar", status_code=status.HTTP_202_ACCEPTED)
def cargar(
    body: CargarIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_documents),
) -> dict[str, Any]:
    from app.erp.historico_job import encolar_carga  # noqa: PLC0415

    posibles = historico.ejercicios_posibles(_en_curso(session))
    pedidos = [e.strip() for e in body.ejercicios if e.strip()] or posibles
    fuera = [e for e in pedidos if e not in posibles]
    if fuera:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "ejercicio_no_valido",
            "detail": f"Ese ejercicio no existe en FACTUSOL: {', '.join(fuera)}.",
        })
    resultado = encolar_carga(session, pedidos)
    if resultado == "ocupada":
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "carga_en_marcha",
            "detail": "Ya hay una carga en marcha. Espera a que termine y vuelve a lanzarla.",
        })
    if resultado == "sin_cola":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "sin_cola",
            "detail": "No se ha podido encolar la carga. Prueba de nuevo en un momento.",
        })
    return {"encolada": pedidos}


def _por(user: User) -> str:
    return user.email or str(user.id)


def _no_existe(exc: LookupError, que: str = "familia") -> HTTPException:
    detalle = {"familia": "Esa familia no está en el histórico.",
               "regla": "Esa regla ya no existe.",
               "articulo": "Ese artículo no está en ningún catálogo volcado.",
               "equivalencia": "Ese código no tiene ninguna equivalencia declarada.",
               "alias": "Ese código no tiene alias."}[que]
    return HTTPException(status.HTTP_404_NOT_FOUND, {"code": f"{que}_no_existe",
                                                     "detail": detalle})


_NO_VALIDO = {
    "clase": "Esa clase no existe.",
    "gama": "Esa gama no existe: añádela primero a la lista.",
    "regla": "La regla necesita un patrón y una clase o una gama.",
    "vacia": "Escribe el nombre de la gama.",
    "existe": "Esa gama ya está en la lista.",
    "sin_propuesta": "Ninguna regla propone nada para esta familia.",
    "no_choca": "La propuesta no choca con lo que tiene: no hay nada que mantener.",
    "pista": "Escribe la pista (una palabra o unas pocas).",
    "pista_existe": "Esa pista ya está en la lista.",
    "serie": "Falta la serie.",
    "umbral": "El umbral tiene que ser un importe positivo.",
    "meses": "Los meses tienen que ir de 1 a 120.",
    "mismo_codigo": "Escribe dos códigos distintos.",
    "sin_catalogo": "Los dos códigos tienen que estar en algún catálogo volcado.",
    "ya_declarado": "Esos dos códigos ya son equivalentes.",
    "ya_en_otro_grupo": "El segundo código ya va con otro grupo: quítalo de ahí primero.",
    "codigo_en_catalogo": "Ese código ya está en el catálogo: no es un código perdido.",
    "ya_tiene_alias": "Ese código ya tiene alias: quítalo primero.",
}


def _no_valido(exc: ValueError) -> HTTPException:
    motivo = str(exc)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
        "code": f"{motivo}_no_valida" if motivo in ("clase", "gama", "regla") else motivo,
        "detail": _NO_VALIDO.get(motivo, "Dato no válido."),
    })


@router.get("/familias")
def familias(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {"items": historico.familias(session), "clases": list(CLASES_FAMILIA),
            "gamas": clasificacion.gamas(session)}


class ClasificarIn(BaseModel):
    """Lo que venga se cambia (null lo quita); lo que no venga, no se toca."""

    model_config = ConfigDict(extra="forbid")

    clase: str | None = None
    gama: str | None = None


@router.patch("/familias/{codigo}")
def clasificar(
    codigo: str,
    body: ClasificarIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    cambios = {k: (v or None) for k, v in body.model_dump(exclude_unset=True).items()}
    try:
        return clasificacion.clasificar(session, codigo, cambios, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    except LookupError as exc:
        raise _no_existe(exc) from exc


@router.post("/familias/{codigo}/aceptar")
def aceptar(
    codigo: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    """Acepta la propuesta de una familia, también si estaba para revisar."""
    try:
        return clasificacion.aceptar(session, codigo, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    except LookupError as exc:
        raise _no_existe(exc) from exc


@router.post("/familias/{codigo}/mantener")
def mantener(
    codigo: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Mantener la mía»: deja de marcarla para revisar."""
    try:
        return clasificacion.mantener(session, codigo, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    except LookupError as exc:
        raise _no_existe(exc) from exc


@router.post("/familias/aceptar-propuestas")
def aceptar_propuestas(
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, int]:
    """Aplica de golpe las propuestas que no chocan; las de revisar, no."""
    return clasificacion.aceptar_todas(session, por=_por(user))


class GamaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=64)


@router.post("/gamas", status_code=status.HTTP_201_CREATED)
def crear_gama(
    body: GamaIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        return clasificacion.crear_gama(session, body.nombre)
    except ValueError as exc:
        raise _no_valido(exc) from exc


@router.get("/reglas")
def reglas(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {"items": clasificacion.reglas(session)}


class ReglaIn(BaseModel):
    campo: str = Field(pattern="^(codigo|nombre)$")
    patron: str = Field(min_length=1, max_length=64)
    clase: str | None = None
    gama: str | None = None
    orden: int = Field(default=100, ge=0, le=10000)


@router.post("/reglas", status_code=status.HTTP_201_CREATED)
def crear_regla(
    body: ReglaIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        return clasificacion.guardar_regla(session, body.model_dump())
    except ValueError as exc:
        raise _no_valido(exc) from exc


@router.put("/reglas/{regla_id}")
def cambiar_regla(
    regla_id: int,
    body: ReglaIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    try:
        return clasificacion.guardar_regla(session, body.model_dump(), regla_id)
    except ValueError as exc:
        raise _no_valido(exc) from exc
    except LookupError as exc:
        raise _no_existe(exc, "regla") from exc


@router.delete("/reglas/{regla_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_regla(
    regla_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> Response:
    try:
        clasificacion.borrar_regla(session, regla_id)
    except LookupError as exc:
        raise _no_existe(exc, "regla") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/articulos")
def articulos(
    q: str = Query("", max_length=120),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {"items": historico.buscar_articulos(session, q)}


class ClaseArticuloIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: null: vuelve a la clase de su familia.
    clase: str | None = Field(default=None, max_length=16)


@router.get("/articulos-clase")
def articulos_clase(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return {"items": clasificacion.clases_de_articulo(session), "clases": list(CLASES_FAMILIA)}


@router.put("/articulos-clase/{codart:path}")
def clasificar_articulo(
    body: ClaseArticuloIn,
    codart: str = Path(..., max_length=64),  # declarada `{codart:path}`: puede llevar «/»
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    """La clase propia de un artículo, por encima de la de su familia («esto
    no es una máquina», desde el parque o desde Familias)."""
    try:
        clasificacion.clasificar_articulo(session, codart, body.clase or None, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    except LookupError as exc:
        raise _no_existe(exc, "articulo") from exc
    return {"items": clasificacion.clases_de_articulo(session), "clases": list(CLASES_FAMILIA)}


# --- máquinas por artículo y series que cuentan (31/10/2026) ---------------------------


class EsMaquinaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    es_maquina: bool
    #: «No es máquina» de una fila sin revisar: si ya está revisada, no se toca.
    solo_sin_revisar: bool = False


class ReglaArticuloIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patron: str = Field(min_length=1, max_length=64)
    #: `maquina` (el nombre dice que lo es) o `no_maquina` (delata que no).
    tipo: str = Field(default="no_maquina", pattern="^(maquina|no_maquina)$")
    palabra_entera: bool = False


class SerieCuentaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cuenta: bool


def _maquinas(session: Session) -> dict[str, Any]:
    from app.erp import historico_alias, historico_maquinas  # noqa: PLC0415

    return {**historico_maquinas.articulos(session, con_sin_catalogo=True),
            "reglas": historico_maquinas.reglas(session),
            "alias_articulo": historico_alias.listar_articulos(session)}


class AliasArticuloIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(..., max_length=64)
    codart: str = Field(..., max_length=64)


@router.post("/alias-articulo")
def declarar_alias_articulo(
    body: AliasArticuloIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_alias  # noqa: PLC0415

    try:
        historico_alias.declarar_articulo(session, body.codigo, body.codart, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    return _maquinas(session)


@router.delete("/alias-articulo/{codigo:path}")
def quitar_alias_articulo(
    codigo: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_alias  # noqa: PLC0415

    try:
        historico_alias.quitar_articulo(session, codigo)
    except LookupError as exc:
        raise _no_existe(exc, "alias") from exc
    return _maquinas(session)


@router.get("/maquinas")
def maquinas(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Los artículos de las familias de clase máquina (y los marcados como
    máquina en otras), por líneas vendidas, con la propuesta por texto, si
    están marcados y el recuento («104 de 230»)."""
    return _maquinas(session)


@router.put("/maquinas/{codart:path}")
def marcar_maquina(
    body: EsMaquinaIn,
    codart: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_maquinas  # noqa: PLC0415

    try:
        historico_maquinas.marcar(session, codart, body.es_maquina, por=_por(user),
                                  solo_sin_revisar=body.solo_sin_revisar)
    except LookupError as exc:
        raise _no_existe(exc, "articulo") from exc
    return _maquinas(session)


@router.post("/maquinas/aceptar-propuestas")
def aceptar_maquinas(
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    """Aplica la propuesta a lo que nadie ha revisado; lo marcado a mano no
    se toca."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    hechas = historico_maquinas.aceptar_todas(session, por=_por(user))
    return {**_maquinas(session), "aplicadas": hechas}


class UmbralIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    umbral: float = Field(ge=0)


class FamiliaLoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: En el cuerpo (no en la URL): un código con «/» no rompe la ruta.
    familia: str = Field(min_length=1, max_length=32)
    confirm: bool = False


@router.put("/maquinas-umbral")
def cambiar_umbral(
    body: UmbralIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    """El umbral de precio (dato, no código): por encima, un artículo sin
    pista en el texto se propone como dudoso."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    historico_maquinas.guardar_umbral(session, body.umbral)
    return _maquinas(session)


@router.post("/maquinas-familias/revisar")
def dar_por_revisadas(
    body: FamiliaLoteIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    """«Dar por revisadas las que quedan» de una familia: lo que siga sin
    revisar pasa a «no es máquina». Con confirmación (la pantalla dice
    cuántas); se deshace por familia."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required", "detail": "Hace falta confirmarlo."})
    hechas = historico_maquinas.dar_por_revisadas(session, body.familia, por=_por(user))
    return {**_maquinas(session), "revisadas": hechas}


@router.post("/maquinas-familias/deshacer")
def deshacer_revisadas(
    body: FamiliaLoteIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    """Vuelve a «sin revisar» lo que dio por revisado ese botón en esa familia
    (lo marcado después a mano no se toca)."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    if not body.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required", "detail": "Hace falta confirmarlo."})
    vueltas = historico_maquinas.deshacer_revisadas(session, body.familia)
    return {**_maquinas(session), "deshechas": vueltas}


@router.post("/reglas-articulo", status_code=status.HTTP_201_CREATED)
def crear_regla_articulo(
    body: ReglaArticuloIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_maquinas  # noqa: PLC0415

    try:
        historico_maquinas.guardar_regla(session, body.patron, body.tipo,
                                         palabra_entera=body.palabra_entera)
    except ValueError as exc:
        raise _no_valido(exc) from exc
    return _maquinas(session)


class PalabraEnteraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    palabra_entera: bool


@router.put("/reglas-articulo/{regla_id}")
def cambiar_regla_articulo(
    regla_id: int,
    body: PalabraEnteraIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    """La casilla «palabra entera» de una pista."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    try:
        historico_maquinas.cambiar_palabra_entera(session, regla_id, body.palabra_entera)
    except LookupError as exc:
        raise _no_existe(exc, "regla") from exc
    return _maquinas(session)


@router.delete("/reglas-articulo/{regla_id}")
def borrar_regla_articulo(
    regla_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_maquinas  # noqa: PLC0415

    try:
        historico_maquinas.borrar_regla(session, regla_id)
    except LookupError as exc:
        raise _no_existe(exc, "regla") from exc
    return _maquinas(session)


@router.get("/series")
def series_venta(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Las series de las ventas volcadas, si cuentan como venta, y cuántos
    documentos y líneas se quedan fuera con lo marcado."""
    from app.erp import historico_maquinas  # noqa: PLC0415

    return historico_maquinas.series(session)


@router.put("/series/{serie}")
def marcar_serie(
    body: SerieCuentaIn,
    serie: str = Path(..., max_length=8),
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_maquinas  # noqa: PLC0415

    try:
        historico_maquinas.marcar_serie(session, serie, body.cuenta, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    return historico_maquinas.series(session)


class EquivalenciaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonico: str = Field(..., max_length=64)
    codart: str = Field(..., max_length=64)


def _equivalencias(session: Session) -> dict[str, Any]:
    from app.erp import historico_equivalencias  # noqa: PLC0415

    return {"items": historico_equivalencias.listar(session)}


@router.get("/equivalencias")
def equivalencias(
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return _equivalencias(session)


@router.post("/equivalencias")
def declarar_equivalencia(
    body: EquivalenciaIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_equivalencias  # noqa: PLC0415

    try:
        historico_equivalencias.declarar(session, body.canonico, body.codart, por=_por(user))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    return _equivalencias(session)


@router.delete("/equivalencias/{codart:path}")
def quitar_equivalencia(
    codart: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import historico_equivalencias  # noqa: PLC0415

    try:
        historico_equivalencias.quitar(session, codart)
    except LookupError as exc:
        raise _no_existe(exc, "equivalencia") from exc
    return _equivalencias(session)


def _compradores(
    session: Session, codart: str | None, q: str | None, familia: str | None,
    desde: int | None, hasta: int | None, clase: str | None, gama: str | None,
) -> list[dict[str, Any]]:
    return historico.compradores(session, codart=codart or None, q=q or None,
                                 familia=familia or None, desde=desde, hasta=hasta,
                                 clase=clase or None, gama=gama or None)


@router.get("/compradores")
def compradores(
    codart: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=120),
    familia: str | None = Query(None, max_length=32),
    clase: str | None = Query(None, max_length=16),
    gama: str | None = Query(None, max_length=32),
    desde: int | None = Query(None, ge=2000, le=2100),
    hasta: int | None = Query(None, ge=2000, le=2100),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    items = _compradores(session, codart, q, familia, desde, hasta, clase, gama)
    return {"items": items, "total": len(items),
            "sin_empresa": sum(1 for c in items if not c["empresa"])}


#: Las columnas del CSV, en el orden en que salen.
CSV_COLUMNAS = ("Código cliente FACTUSOL", "Cliente", "Empresa en BoHub", "Última compra",
                "Ejercicio", "Documento", "Tipo", "Unidades", "Compras", "Artículos")


@router.get("/compradores.csv")
def compradores_csv(
    codart: str | None = Query(None, max_length=64),
    q: str | None = Query(None, max_length=120),
    familia: str | None = Query(None, max_length=32),
    clase: str | None = Query(None, max_length=16),
    gama: str | None = Query(None, max_length=32),
    desde: int | None = Query(None, ge=2000, le=2100),
    hasta: int | None = Query(None, ge=2000, le=2100),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> Response:
    items = _compradores(session, codart, q, familia, desde, hasta, clase, gama)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(CSV_COLUMNAS)
    for c in items:
        u = c["ultima_compra"] or {}
        w.writerow([
            c["codcli"] or "", c["nombre"] or "",
            c["empresa"]["nombre"] if c["empresa"] else "(sin empresa vinculada)",
            u.get("fecha") or u.get("fecha_original") or "", u.get("ejercicio") or "",
            u.get("documento") or "", u.get("tipo") or "",
            f"{c['unidades']:g}".replace(".", ","), c["compras"],
            " | ".join(f"{a['codart']} {a['descripcion'] or ''}".strip() for a in c["articulos"]),
        ])
    # BOM: que Excel abra los acentos bien.
    return Response(
        content=("﻿" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="quien_compro.csv"'},
    )


# --- ficha de un documento (factura, albarán, proforma) de cualquier año --------------


def _lector_vivo(session: Session):
    """Lee un documento del ejercicio en curso en vivo (lo creado después de la
    última carga, o solo su cabecera para el cobro de hoy). None si no existe;
    `FactusolNoResponde` si FACTUSOL no contesta (no es lo mismo)."""
    from app.erp.historico_documento import FactusolNoResponde  # noqa: PLC0415

    def leer(doc_type: str, serie: str, codigo: int, *, solo_cabecera: bool = False):
        from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415
        from app.integrations.factusol.documents import (  # noqa: PLC0415
            get_document,
            get_header,
        )

        try:
            client, ejercicio = _client_and_ejercicio(session)
            leer_fn = get_header if solo_cabecera else get_document
            return leer_fn(client, doc_type, serie=int(serie), codigo=codigo,
                           ejercicio=ejercicio)
        except Exception as exc:  # noqa: BLE001 — se dice, no se inventa
            raise FactusolNoResponde(type(exc).__name__) from exc
    return leer


@router.get("/documentos/{tipo}/{ejercicio}/{serie}/{codigo}")
def ficha_documento(
    tipo: str,
    ejercicio: str = Path(pattern=r"^\d{4}$"),
    serie: str = Path(max_length=8),
    codigo: int = Path(ge=0),
    session: Session = Depends(get_session),
    user: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Cabecera, líneas (las de nota marcadas), estado, pedido de BoHub y lo
    necesario para el PDF. Solo lectura."""
    from app.erp.historico_documento import (  # noqa: PLC0415
        DocumentoNoEncontrado,
        FactusolNoResponde,
        ficha,
    )

    try:
        return ficha(session, tipo=tipo, ejercicio=ejercicio, serie=serie, codigo=codigo,
                     user=user, en_curso=_en_curso(session),
                     lector_vivo=_lector_vivo(session))
    except FactusolNoResponde as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable",
            "detail": ("Es un documento de este ejercicio que aún no está en la carga de "
                       "anoche, y FACTUSOL no responde. Prueba de nuevo en un momento."),
        }) from exc
    except DocumentoNoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "documento_no_encontrado",
            "detail": ("Ese documento no está en lo volcado. Si es de un año antiguo, "
                       "cárgalo desde Quién compró qué → Carga."),
        }) from exc


# --- Dormidos y huecos (1/11/2026) ------------------------------------------------------


class DormidosAjustesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meses_dormido: int | None = Field(None, ge=1, le=120)
    meses_gracia_hueco: int | None = Field(None, ge=1, le=120)


class FiltroDormidos(BaseModel):
    """Para partir las listas: gama, sector («-» = sin sector), meses con la
    máquina más reciente (mínimo y máximo) y gasto total mínimo."""

    gama: str | None = None
    sector: str | None = None
    meses_min: int | None = None
    meses_max: int | None = None
    gasto_min: float | None = None


def _filtro_dormidos(
    gama: str | None = Query(None, max_length=32),
    sector: str | None = Query(None, max_length=120),
    meses_min: int | None = Query(None, ge=0, le=600),
    meses_max: int | None = Query(None, ge=0, le=600),
    gasto_min: float | None = Query(None, ge=0),
) -> FiltroDormidos:
    return FiltroDormidos(gama=gama or None, sector=sector or None, meses_min=meses_min,
                          meses_max=meses_max, gasto_min=gasto_min)


@router.get("/parque/dormidos-huecos")
def dormidos_huecos(
    filtro: FiltroDormidos = Depends(_filtro_dormidos),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    from app.erp import parque_consultas  # noqa: PLC0415

    return parque_consultas.filtrar(parque_consultas.informe(session), **filtro.model_dump())


@router.put("/parque/dormidos-ajustes")
def dormidos_ajustes(
    body: DormidosAjustesIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_config),
) -> dict[str, Any]:
    from app.erp import parque_consultas  # noqa: PLC0415

    try:
        parque_consultas.guardar_ajustes(session, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise _no_valido(exc) from exc
    return parque_consultas.informe(session)


@router.get("/parque/{tipo}.csv")
def dormidos_huecos_csv(
    tipo: str = Path(..., pattern="^(dormidos|huecos)$"),
    filtro: FiltroDormidos = Depends(_filtro_dormidos),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> Response:
    from app.erp import parque_consultas  # noqa: PLC0415

    # Lo mismo que se ve: el CSV sale con los filtros de la pantalla.
    datos = parque_consultas.filtrar(parque_consultas.informe(session), **filtro.model_dump())
    texto = parque_consultas.csv_de(datos[tipo], tipo)
    return Response(
        content=("﻿" + texto).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{tipo}.csv"'},
    )
