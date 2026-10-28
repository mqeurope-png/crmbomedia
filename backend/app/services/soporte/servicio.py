"""Buscar y leer en el soporte de terceros (la lista blanca de
`soporte_fuentes`), con caché.

Es CONSULTA: nada de aquí escribe fuera ni manda nada. Lo usan:

- el redactor (la oficina, «Buscar en soporte»), por la API
  `/api/erp/soporte`: buscar → título, extracto y enlace a la vista; leer → el
  artículo limpio;
- el redactor, por `articulo(session, fuente, id)`, para meter el artículo
  elegido en el prompt como material de consulta. Siempre por fuente + id de
  la lista blanca, NUNCA por una URL libre que mande el navegador.

Buscar no va dentro de la generación del borrador (el nginx de Plesk corta a
los 60 s): es una acción aparte, con su tiempo máximo por petición y un tope
para toda la búsqueda.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.fuentes_externas import SOPORTE_HTML, SoporteFuente
from app.services.soporte import adaptadores, cache, fuentes
from app.services.soporte.tipos import Articulo, Busqueda, Resultado, SoporteError

#: Resultados por defecto y como mucho (por fuente y en total).
LIMITE_DEFECTO = 6
MAX_LIMITE = 20
#: Largo de la consulta: lo que se escribe en un buscador (o la descripción
#: de una línea de pedido), no un correo entero.
MAX_CONSULTA = 300
#: Largo de un id de artículo (o del enlace de una fuente sin búsqueda).
MAX_ID = 500
#: Segundos para TODA la búsqueda: con varias fuentes (y el respaldo en
#: inglés), las que quedan tras pasarse se saltan con un aviso, lejos de los
#: 60 s del nginx.
TIEMPO_TOTAL_BUSQUEDA = 25.0


def _consulta(valor: str | None) -> str:
    consulta = " ".join(str(valor or "").split())[:MAX_CONSULTA]
    if len(consulta) < 2:
        raise SoporteError("consulta_vacia", "Escribe qué buscar (al menos dos letras).", 400)
    return consulta


def _limite(valor: int | None) -> int:
    try:
        return max(1, min(int(valor or LIMITE_DEFECTO), MAX_LIMITE))
    except (TypeError, ValueError):
        return LIMITE_DEFECTO


def _intercalar(por_fuente: list[list[Resultado]], limite: int) -> list[Resultado]:
    """De cada fuente, por turnos: que la primera no tape a las demás."""
    salida: list[Resultado] = []
    for i in range(max((len(r) for r in por_fuente), default=0)):
        for resultados in por_fuente:
            if i < len(resultados):
                salida.append(resultados[i])
                if len(salida) >= limite:
                    return salida
    return salida


def _resultado(fuente: SoporteFuente, datos: dict[str, Any]) -> Resultado:
    return Resultado(
        fuente=fuente.clave, fuente_nombre=fuente.nombre,
        articulo_id=str(datos.get("articulo_id") or ""), titulo=str(datos.get("titulo") or ""),
        extracto=str(datos.get("extracto") or ""), url=str(datos.get("url") or ""),
        idioma=datos.get("idioma"),
    )


def _buscar_en(session: Session, fuente: SoporteFuente, consulta: str, idioma: str,
               limite: int) -> tuple[list[Resultado], list[str]]:
    clave = cache.clave(cache.BUSQUEDA, fuente.clave, idioma, f"{consulta.lower()}#{limite}")
    datos = cache.leer(session, clave)
    if not isinstance(datos, dict):
        datos = adaptadores.buscar(fuente, consulta, idioma, limite)
        cache.guardar(session, clave, fuente=fuente.clave, tipo=cache.BUSQUEDA, datos=datos,
                      duracion=cache.DURACION_BUSQUEDA)
    resultados = [_resultado(fuente, r) for r in datos.get("resultados") or []
                  if isinstance(r, dict) and r.get("articulo_id")]
    avisos = [str(a) for a in datos.get("avisos") or []]
    return resultados, avisos


def buscar(session: Session, consulta: str, *, idioma: str | None = None,
           fuente: str | None = None, limite: int = LIMITE_DEFECTO) -> Busqueda:
    """Busca en una fuente (`fuente`) o en todas las activas que tienen
    búsqueda. Lo que no sale bien en una fuente va a `avisos` (y la fuente no
    entra en `fuentes`, que son solo las que han respondido) y se sigue con
    las demás; si NINGUNA ha respondido, es un error (no «sin resultados»: el
    redactor diría que no hay procedimiento cuando no se ha podido mirar)."""
    consulta = _consulta(consulta)
    idioma_pedido = adaptadores.idioma_de(idioma) or adaptadores.IDIOMA_DEFECTO
    limite = _limite(limite)
    candidatas = ([fuentes.obtener_activa(session, fuente)] if fuente
                  else fuentes.activas(session))
    busqueda = Busqueda(consulta=consulta)
    if not candidatas:
        busqueda.avisos.append("No hay ningún centro de soporte activo en la lista.")
        return busqueda
    por_fuente: list[list[Resultado]] = []
    fallos: list[SoporteError] = []
    hasta = time.monotonic() + TIEMPO_TOTAL_BUSQUEDA
    for f in candidatas:
        if f.tipo == SOPORTE_HTML:
            # Sin búsqueda: con todas, se salta callando; pedida a propósito,
            # se dice por qué no sale nada.
            if fuente:
                busqueda.avisos.append(f"{f.nombre} no tiene búsqueda: solo se leen sus "
                                       "artículos por enlace.")
            continue
        if time.monotonic() > hasta:
            busqueda.avisos.append(f"{f.nombre}: no se ha consultado (las anteriores han "
                                   "tardado demasiado).")
            busqueda.fallidas.append(f.clave)
            continue
        try:
            resultados, avisos = _buscar_en(session, f, consulta, idioma_pedido, limite)
        except SoporteError as exc:
            fallos.append(exc)
            busqueda.avisos.append(str(exc))
            busqueda.fallidas.append(f.clave)
            continue
        # Solo las que han respondido: con «sin resultados», el redactor ofrece
        # decir que no hay procedimiento, y en la que falló no se ha mirado.
        busqueda.fuentes.append(f.clave)
        busqueda.avisos.extend(avisos)
        por_fuente.append(resultados)
    if fallos and not por_fuente:
        if len(fallos) == 1:
            raise fallos[0]
        raise SoporteError("sin_respuesta", "Ningún centro de soporte ha respondido: "
                           + " · ".join(str(e) for e in fallos), 502)
    busqueda.resultados = _intercalar(por_fuente, limite)
    if por_fuente and not busqueda.resultados:
        busqueda.avisos.append(f"No se ha encontrado nada para «{consulta}».")
    return busqueda


def articulo(session: Session, fuente: str, articulo_id: str, *,
             idioma: str | None = None) -> Articulo:
    """El artículo `articulo_id` de la fuente `fuente` (de la lista blanca y
    activa), limpio. Lo usa el redactor para meterlo en el prompt: el texto
    es DATO (se encapsula como material de consulta al usarlo)."""
    f = fuentes.obtener_activa(session, fuente)
    crudo = str(articulo_id or "").strip()
    if not crudo or len(crudo) > MAX_ID:
        raise SoporteError("id_invalido", "Falta el artículo (o el enlace es demasiado largo).",
                           400)
    aid = adaptadores.normalizar_id(f, crudo)
    idioma_pedido = adaptadores.idioma_de(idioma) or adaptadores.IDIOMA_DEFECTO
    clave = cache.clave(cache.ARTICULO, f.clave, idioma_pedido, aid)
    datos = cache.leer(session, clave)
    if not isinstance(datos, dict) or not datos.get("texto"):
        datos = adaptadores.articulo(f, aid, idioma_pedido)
        cache.guardar(session, clave, fuente=f.clave, tipo=cache.ARTICULO, datos=datos,
                      duracion=cache.DURACION_ARTICULO)
    return Articulo(
        fuente=f.clave, fuente_nombre=f.nombre,
        articulo_id=str(datos.get("articulo_id") or aid), titulo=str(datos.get("titulo") or ""),
        url=str(datos.get("url") or ""), idioma=datos.get("idioma"),
        texto=str(datos.get("texto") or ""), html=str(datos.get("html") or ""),
        actualizado=datos.get("actualizado"),
    )


def comprobar_fuente(session: Session, clave: str) -> SoporteFuente:
    """«Comprobar»: qué devuelve DE VERDAD la fuente. Una búsqueda de prueba
    (zendesk, wordpress) o la página base (html), y se guarda `ok` (sirve
    contenido), `vacio` (cáscara que se pinta con JavaScript, o sin resultados
    con texto) o `error`, con el detalle. Vale también con la fuente
    desactivada: se comprueba antes de activarla (es de administración)."""
    f = session.get(SoporteFuente, (clave or "").strip().lower())
    if f is None:
        raise SoporteError("fuente_desconocida",
                           f"«{clave}» no está en la lista de centros de soporte.", 404)
    estado, detalle = adaptadores.comprobar(f)
    f.comprobado_en = datetime.now(UTC)
    f.comprobado_estado = estado
    f.comprobado_detalle = detalle[:2000]
    session.flush()
    return f
