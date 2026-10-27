"""Escritura del índice de las webs (`web_indice`) y su estado.

Una fila por (web, tipo, id de WordPress, idioma), con el patrón de
`kb_catalogo`:

- Lo que se ve en un recorrido → `visto_en = ahora`, `ido_en = None` (si
  había vuelto a aparecer, deja de estar ido).
- Lo que en una web + idioma + tipo que SÍ respondió ya no aparece → `ido_en
  = ahora`. No se borra: el histórico se queda y una fila ida no sale en la
  búsqueda.
- Una web, idioma o tipo que FALLA no marca nada ido: se queda lo anterior.
  Una lista que la web devuelve vacía (200 con cero elementos) tampoco (como
  las tiendas del catálogo: un plugin a medias no vacía el índice).
- La `huella` (sha256 del contenido) cuenta los actualizados sin dar por
  cambiado lo que es igual. La fecha de WordPress no entra en la huella: si
  solo cambia la fecha, el contenido es el mismo.

Reindexar dos veces seguidas no duplica nada: la clave única es la de arriba.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.fuentes_externas import (
    WEB_ENTRADA,
    WEB_PAGINA,
    WEB_PRODUCTO,
    WEB_TIPOS,
    WebIndiceEntrada,
)
from app.services.webs.lector import LecturaWeb

logger = logging.getLogger(__name__)

#: Columnas que salen de la web (lo demás es la clave y las fechas del índice).
CAMPOS_CONTENIDO = (
    "dominio", "url", "slug", "titulo", "texto", "extracto", "imagen", "galeria_json",
    "categorias_json", "precio_web", "precio_web_texto", "precio_iva", "moneda",
)
#: Errores que se guardan en el resumen de una web, como mucho.
MAX_ERRORES = 10

_LARGOS: dict[str, int] = {
    c.name: c.type.length for c in WebIndiceEntrada.__table__.columns
    if getattr(c.type, "length", None)
}
#: Columnas que son una URL: recortada no sirve; mejor vacía (o la fila fuera).
_URLS = ("url", "imagen")


def _ahora() -> datetime:
    return datetime.now(UTC)


def _con_zona(fecha: datetime | None) -> datetime | None:
    """SQLite devuelve las fechas sin zona: se leen como UTC."""
    if fecha is None:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def valores_de(fila: dict[str, Any]) -> dict[str, Any]:
    """De lo leído (`lector`) a los valores de las columnas, recortados a su
    largo. `ValueError` si la URL no cabe (la fila no se puede guardar)."""
    valores: dict[str, Any] = {
        "dominio": fila.get("dominio") or "", "url": fila.get("url") or "",
        "slug": fila.get("slug"), "titulo": fila.get("titulo") or "",
        "texto": fila.get("texto") or "", "extracto": fila.get("extracto"),
        "imagen": fila.get("imagen"),
        "galeria_json": (json.dumps(fila["galeria"], ensure_ascii=False)
                         if fila.get("galeria") else None),
        "categorias_json": (json.dumps(fila["categorias"], ensure_ascii=False)
                            if fila.get("categorias") else None),
        "precio_web": (Decimal(str(fila["precio_web"]))
                       if fila.get("precio_web") is not None else None),
        "precio_web_texto": fila.get("precio_web_texto"),
        "precio_iva": fila.get("precio_iva"), "moneda": fila.get("moneda"),
    }
    for campo, valor in list(valores.items()):
        largo = _LARGOS.get(campo)
        if not (largo and isinstance(valor, str) and len(valor) > largo):
            continue
        if campo == "url":
            raise ValueError(f"URL de más de {largo} caracteres")
        valores[campo] = None if campo in _URLS else valor[:largo]
    return valores


def huella(valores: dict[str, Any]) -> str:
    datos = {c: (str(valores.get(c)) if isinstance(valores.get(c), Decimal) else valores.get(c))
             for c in CAMPOS_CONTENIDO}
    return hashlib.sha256(json.dumps(datos, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def _clave(tipo: str, wp_id: int, idioma: str) -> tuple[str, int, str]:
    return tipo, int(wp_id), idioma


def escribir_lectura(session: Session, lectura: LecturaWeb, *,
                     ahora: datetime | None = None) -> dict[str, Any]:
    """Vuelca lo leído de UNA web. Devuelve su resumen para la pasada:
    `{"estado", "paginas", "entradas", "productos", "nuevos", "actualizados",
    "idos", "error", …}` (estado `ok`, `parcial` si algún idioma o tipo falló,
    `error` si fallaron todos)."""
    ahora = ahora or _ahora()
    existentes = {
        _clave(f.tipo, f.wp_id, f.idioma): f
        for f in session.scalars(select(WebIndiceEntrada).where(
            WebIndiceEntrada.sitio == lectura.sitio))
    }
    cuentas = dict.fromkeys(WEB_TIPOS, 0)
    nuevos = actualizados = idos = saltadas = 0
    errores: list[str] = []
    respondieron = 0
    for grupo in lectura.grupos:
        if grupo.error is not None:
            errores.append(f"{grupo.tipo} {grupo.idioma}: {grupo.error}")
            continue
        respondieron += 1
        vistos: set[tuple[str, int, str]] = set()
        for fila in grupo.filas:
            clave = _clave(grupo.tipo, fila["wp_id"], grupo.idioma)
            if clave in vistos:
                continue
            try:
                valores = valores_de(fila)
            except ValueError as exc:
                saltadas += 1
                if len(errores) < MAX_ERRORES:
                    errores.append(f"{grupo.tipo} {grupo.idioma} {fila['wp_id']}: {exc}")
                continue
            vistos.add(clave)
            try:
                with session.begin_nested():
                    n, a = _escribir_una(session, existentes, clave, lectura.sitio, valores,
                                         fila.get("modificado_web"), ahora)
            except SQLAlchemyError as exc:
                vistos.discard(clave)
                saltadas += 1
                motivo = str(getattr(exc, "orig", None) or exc).replace("\n", " ")[:160]
                logger.warning("webs: fila %s %s saltada: %s", lectura.sitio, clave, motivo)
                if len(errores) < MAX_ERRORES:
                    errores.append(f"{grupo.tipo} {grupo.idioma} {fila['wp_id']}: {motivo}")
                continue
            nuevos += n
            actualizados += a
            cuentas[grupo.tipo] += 1
        if grupo.vacio:
            continue
        for (tipo, wp_id, idioma), fila_bd in existentes.items():
            if (tipo == grupo.tipo and idioma == grupo.idioma and fila_bd.ido_en is None
                    and (tipo, wp_id, idioma) not in vistos):
                fila_bd.ido_en = ahora
                idos += 1
    if respondieron:
        # Un idioma que la web ya no tiene en la lista (se quitó de la tabla de
        # sitios): lo suyo se va.
        for (_, _, idioma), fila_bd in existentes.items():
            if idioma not in lectura.idiomas and fila_bd.ido_en is None:
                fila_bd.ido_en = ahora
                idos += 1
    session.flush()
    fallidos = len(lectura.grupos) - respondieron
    if lectura.grupos and fallidos == len(lectura.grupos):
        estado = "error"
    elif fallidos or saltadas:
        estado = "parcial"
    else:
        estado = "ok"
    return {
        "estado": estado, "paginas": cuentas[WEB_PAGINA], "entradas": cuentas[WEB_ENTRADA],
        "productos": cuentas[WEB_PRODUCTO], "nuevos": nuevos, "actualizados": actualizados,
        "idos": idos, "saltadas": saltadas, "dominio": lectura.dominio,
        "peticiones": lectura.peticiones,
        "error": "; ".join(errores[:MAX_ERRORES])[:1000] or None,
        "avisos": lectura.avisos[:MAX_ERRORES],
    }


def _escribir_una(session: Session, existentes: dict[tuple[str, int, str], WebIndiceEntrada],
                  clave: tuple[str, int, str], sitio: str, valores: dict[str, Any],
                  modificado: datetime | None, ahora: datetime) -> tuple[int, int]:
    """`(nuevos, actualizados)` de escribir una fila (0 o 1 cada uno)."""
    nueva_huella = huella(valores)
    fila = existentes.get(clave)
    if fila is None:
        fila = WebIndiceEntrada(sitio=sitio, tipo=clave[0], wp_id=clave[1], idioma=clave[2],
                                visto_en=ahora, huella=nueva_huella, modificado_web=modificado,
                                **valores)
        session.add(fila)
        session.flush()
        existentes[clave] = fila
        return 1, 0
    cambio = fila.huella != nueva_huella or fila.ido_en is not None
    if fila.huella != nueva_huella:
        for campo, valor in valores.items():
            setattr(fila, campo, valor)
        fila.huella = nueva_huella
    fila.modificado_web = modificado
    fila.visto_en = ahora
    fila.ido_en = None
    session.flush()
    return 0, 1 if cambio else 0


def marcar_idos_fuera_de(session: Session, sitios: set[str], *,
                         ahora: datetime | None = None) -> int:
    """Marca idas las filas vivas de webs que ya no se indexan (desactivadas
    o quitadas de la tabla de sitios). Devuelve cuántas."""
    ahora = ahora or _ahora()
    consulta = select(WebIndiceEntrada).where(WebIndiceEntrada.ido_en.is_(None))
    if sitios:
        consulta = consulta.where(WebIndiceEntrada.sitio.not_in(sorted(sitios)))
    idos = 0
    for fila in session.scalars(consulta):
        fila.ido_en = ahora
        idos += 1
    if idos:
        session.flush()
    return idos


def estado_por_web(session: Session, webs: list[tuple[str, str]]) -> dict[str, Any]:
    """Las filas de «Estado» de la pantalla (forma `WebsEstadoFila`): por web
    (en el orden de `webs`, `[(clave, dominio)]`), lo vivo por tipo, los idos,
    los idiomas y el último visto. Las webs que ya no se indexan pero dejaron
    algo vivo salen al final."""
    vivos: dict[str, dict[str, int]] = {}
    for sitio, tipo, n in session.execute(
            select(WebIndiceEntrada.sitio, WebIndiceEntrada.tipo, func.count())
            .where(WebIndiceEntrada.ido_en.is_(None))
            .group_by(WebIndiceEntrada.sitio, WebIndiceEntrada.tipo)):
        vivos.setdefault(sitio, {})[tipo] = int(n)
    idos = dict(session.execute(
        select(WebIndiceEntrada.sitio, func.count()).where(WebIndiceEntrada.ido_en.is_not(None))
        .group_by(WebIndiceEntrada.sitio)).all())
    idiomas: dict[str, set[str]] = {}
    for sitio, idioma in session.execute(
            select(WebIndiceEntrada.sitio, WebIndiceEntrada.idioma).distinct()
            .where(WebIndiceEntrada.ido_en.is_(None))):
        idiomas.setdefault(sitio, set()).add(idioma)
    ultimas = dict(session.execute(
        select(WebIndiceEntrada.sitio, func.max(WebIndiceEntrada.visto_en))
        .group_by(WebIndiceEntrada.sitio)).all())
    dominios = dict(session.execute(
        select(WebIndiceEntrada.sitio, func.max(WebIndiceEntrada.dominio))
        .where(WebIndiceEntrada.ido_en.is_(None)).group_by(WebIndiceEntrada.sitio)).all())
    orden = list(webs) + [(s, dominios.get(s) or "") for s in sorted(vivos)
                          if s not in {c for c, _ in webs}]
    filas = []
    total = dict.fromkeys(WEB_TIPOS, 0)
    for clave, dominio in orden:
        por_tipo = vivos.get(clave, {})
        for tipo in WEB_TIPOS:
            total[tipo] += por_tipo.get(tipo, 0)
        ultima = _con_zona(ultimas.get(clave))
        filas.append({
            "sitio": clave, "dominio": dominio,
            "pagina": por_tipo.get(WEB_PAGINA, 0), "entrada": por_tipo.get(WEB_ENTRADA, 0),
            "producto": por_tipo.get(WEB_PRODUCTO, 0), "idos": int(idos.get(clave, 0)),
            "idiomas": sorted(idiomas.get(clave, set())),
            "ultima": ultima.isoformat() if ultima else None,
        })
    return {"filas": filas, "total": total}
