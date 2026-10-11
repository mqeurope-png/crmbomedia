"""Consumibles y repuestos: las tiendas WooCommerce de las webs del grupo,
por la API pública de la tienda (sin credenciales):

    GET https://{dominio}/wp-json/wc/store/products?per_page=100&page=N

Los dominios salen de la tabla de sitios (#544), y las variantes de idioma
siguen el mismo criterio que las páginas: el prefijo del idioma de la web
(`Sitio.url_base(idioma)`) más `lang=` por si el plugin lo prefiere. De cada
producto se indexan nombre, precio (formateado como lo enseña la tienda, con
su moneda), enlace, categorías, marca, web e idioma. El tipo (máquina,
consumible o repuesto) se deduce de las categorías de la tienda: es una
heurística y se ve en la pantalla.

Cada tienda e idioma se descarga entera antes de escribir: una tienda caída
deja una línea en el histórico y los demás siguen. Una web sin tienda
(`rest_no_route`) no es un error: se anota «sin tienda» y se salta.
"""
from __future__ import annotations

import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.conocimiento import (
    FUENTE_WOOCOMMERCE,
    SYNC_PROGRAMADA,
    TIPO_CONSUMIBLE,
    TIPO_MAQUINA,
    TIPO_REPUESTO,
    CatalogoSincronizacion,
)
from app.services.conocimiento import catalogo
from app.services.web_forms import sitios as registro_sitios
from app.services.web_forms.sitios import Sitio

logger = logging.getLogger(__name__)

RUTA_STORE_API = "wp-json/wc/store/products"
POR_PAGINA = 100
#: 50 páginas de 100 son 5.000 productos: ninguna tienda del grupo llega.
MAX_PAGINAS = 50
TIMEOUT_SEGUNDOS = 25.0
EN_PARALELO = 4
MAX_DESCRIPCION = 600

#: Palabras de las CATEGORÍAS que dicen «máquina» (no del nombre: «lente para
#: láser» es un repuesto; y «láser» a secas tampoco cuenta: «tubos láser» son
#: repuestos).
PALABRAS_MAQUINA: tuple[str, ...] = (
    "máquina", "maquina", "máquinas", "maquinas", "machine", "machines", "maschine", "maschinen",
    "impresora", "impresoras", "printer", "printers", "drucker", "imprimante", "imprimantes",
    "cortadora", "cortadoras", "grabadora", "grabadoras", "expendedora", "expendedoras", "vending",
)
#: Palabras (categorías o nombre) que dicen «repuesto».
PALABRAS_REPUESTO: tuple[str, ...] = (
    "repuesto", "repuestos", "recambio", "recambios", "spare", "spares", "spare part",
    "spare parts", "ersatzteil", "ersatzteile", "pièce", "pièces", "pieces detachees",
    "pièces détachées", "onderdeel", "onderdelen", "reserveonderdelen", "tubo", "tubos", "tube",
    "tubes", "laserröhre", "laserröhren",
)


class TiendaError(RuntimeError):
    """La tienda no ha respondido bien (red, 5xx, JSON ilegible)."""


class SinTienda(Exception):
    """La web no tiene la API de la tienda (no es WooCommerce): no es un error."""


def _cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
        headers={"User-Agent": "BoHub/kb-catalogo", "Accept": "application/json"},
    )


def url_tienda(sitio: Sitio, idioma: str) -> str:
    return f"{sitio.url_base(idioma)}{RUTA_STORE_API}"


def listar_productos(http: httpx.Client, sitio: Sitio, idioma: str) -> list[dict[str, Any]]:
    """Todas las páginas de productos de la tienda en ese idioma."""
    url = url_tienda(sitio, idioma)
    salida: list[dict[str, Any]] = []
    pagina = 1
    while pagina <= MAX_PAGINAS:
        try:
            respuesta = http.get(url, params={"per_page": POR_PAGINA, "page": pagina,
                                              "lang": idioma})
        except httpx.HTTPError as exc:
            raise TiendaError(f"{sitio.clave} {idioma}: {type(exc).__name__}: {exc}") from exc
        if respuesta.status_code == 404:
            raise SinTienda(f"{sitio.clave} {idioma}: sin tienda (HTTP 404)")
        if respuesta.status_code != 200:
            raise TiendaError(f"{sitio.clave} {idioma}: HTTP {respuesta.status_code}")
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise TiendaError(f"{sitio.clave} {idioma}: respuesta ilegible") from exc
        if isinstance(datos, dict) and datos.get("code"):
            raise SinTienda(f"{sitio.clave} {idioma}: {datos.get('code')}")
        if not isinstance(datos, list):
            raise TiendaError(f"{sitio.clave} {idioma}: la respuesta no es una lista")
        salida.extend(p for p in datos if isinstance(p, dict))
        try:
            total_paginas = int(respuesta.headers.get("X-WP-TotalPages") or 0)
        except ValueError:
            total_paginas = 0
        if len(datos) < POR_PAGINA or (total_paginas and pagina >= total_paginas):
            break
        pagina += 1
    else:
        logger.warning("kb.catalogo: %s %s cortado en %d páginas", sitio.clave, idioma,
                       MAX_PAGINAS)
    return salida


# --- de un producto de la tienda a una fila ---------------------------------------------------


def _entero(valor: Any) -> int | None:
    try:
        return int(str(valor).strip()) if valor not in (None, "") else None
    except ValueError:
        return None


def formatear_importe(valor: Any, precios: dict[str, Any]) -> tuple[str | None, float | None]:
    """`prices.price` viene en unidades menores («12900» con `currency_minor_unit`
    2 → 129,00): se formatea como lo enseña la tienda (separadores, prefijo y
    sufijo) y se devuelve también el número."""
    entero = _entero(valor)
    if entero is None:
        return None, None
    menores = _entero(precios.get("currency_minor_unit"))
    menores = 2 if menores is None else max(0, min(menores, 6))
    numero = entero / (10 ** menores)
    sep_miles = str(precios.get("currency_thousand_separator") or "")
    sep_decimal = str(precios.get("currency_decimal_separator") or ",")
    parte_entera, _, parte_decimal = f"{numero:.{menores}f}".partition(".")
    grupos = []
    while parte_entera:
        grupos.insert(0, parte_entera[-3:])
        parte_entera = parte_entera[:-3]
    texto = sep_miles.join(grupos)
    if menores:
        texto = f"{texto}{sep_decimal}{parte_decimal}"
    prefijo = html.unescape(str(precios.get("currency_prefix") or ""))
    sufijo = html.unescape(str(precios.get("currency_suffix") or ""))
    # El espacio duro de la tienda («12,50&nbsp;€») se guarda como espacio normal.
    completo = f"{prefijo}{texto}{sufijo}".replace(" ", " ").strip()
    return completo[:80], numero


def precio_de(p: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    """`(texto, número, moneda)` de un producto de la Store API. Un producto
    variable con rango → «desde» el mínimo hasta el máximo."""
    precios = p.get("prices") if isinstance(p.get("prices"), dict) else {}
    moneda = str(precios.get("currency_code") or "").strip().upper()[:3] or None
    rango = precios.get("price_range")
    if isinstance(rango, dict) and rango.get("min_amount") not in (None, ""):
        minimo, num_min = formatear_importe(rango.get("min_amount"), precios)
        maximo, _ = formatear_importe(rango.get("max_amount"), precios)
        if minimo and maximo and minimo != maximo:
            return f"{minimo} – {maximo}"[:80], num_min, moneda
        return minimo, num_min, moneda
    texto, numero = formatear_importe(precios.get("price"), precios)
    return texto, numero, moneda


def clasificar(nombre: str, categorias: list[str]) -> str:
    """Máquina, consumible o repuesto, por las categorías (y el nombre para
    los repuestos). Heurística: se enseña en la pantalla."""
    cats = " | ".join(categorias).lower()
    texto = f"{cats} | {nombre.lower()}"
    if any(re.search(rf"(?<![a-zà-ÿ]){re.escape(p)}(?![a-zà-ÿ])", texto)
           for p in PALABRAS_REPUESTO):
        return TIPO_REPUESTO
    if any(re.search(rf"(?<![a-zà-ÿ]){re.escape(p)}(?![a-zà-ÿ])", cats)
           for p in PALABRAS_MAQUINA):
        return TIPO_MAQUINA
    return TIPO_CONSUMIBLE


def fila_de(p: dict[str, Any], *, sitio: Sitio, idioma: str, ahora: datetime,
            ) -> dict[str, Any] | None:
    from app.services.html_sanitizer import html_to_text  # noqa: PLC0415

    pid = _entero(p.get("id"))
    nombre = html.unescape(str(p.get("name") or "")).strip()
    if pid is None or not nombre:
        return None
    categorias = [
        html.unescape(str(c.get("name") or "")).strip()
        for c in (p.get("categories") or []) if isinstance(c, dict) and c.get("name")
    ]
    marcas = [
        html.unescape(str(b.get("name") or "")).strip()
        for b in (p.get("brands") or []) if isinstance(b, dict) and b.get("name")
    ]
    precio_texto, precio_num, moneda = precio_de(p)
    imagenes = p.get("images") if isinstance(p.get("images"), list) else []
    imagen = imagenes[0].get("src") if imagenes and isinstance(imagenes[0], dict) else None
    descripcion = html_to_text(str(p.get("short_description") or "")).strip()[:MAX_DESCRIPCION]
    marca = marcas[0] if marcas else sitio.marca
    # La columna lleva la categoría principal (la primera): un producto en nueve
    # categorías (290 caracteres, 11/10/2026) desbordaba la columna y tiraba la
    # pasada. La lista entera va en `datos_json.categorias_nombres`.
    return {
        "origen_id": f"{sitio.clave}:{pid}", "idioma": idioma,
        "tipo": clasificar(nombre, categorias), "marca": marca[:80],
        "nombre": nombre[:255], "descripcion": descripcion or None,
        "caracteristicas_json": "[]",
        "categoria": (catalogo.recortar_a_columna("categoria", categorias[0])
                      if categorias else None),
        "imagen": str(imagen)[:500] if imagen else None,
        "precio_texto": precio_texto, "precio_num": precio_num, "moneda": moneda,
        "precio_publico": True, "descartado": False, "motivo_descarte": None,
        "enlace": str(p.get("permalink") or "").strip()[:500] or None,
        "sitio": sitio.clave, "actualizado_origen": ahora,
        "visible": p.get("is_purchasable") is not False,
        "datos_json": json.dumps({
            "sku": str(p.get("sku") or "") or None, "en_stock": p.get("is_in_stock"),
            "oferta": bool(p.get("on_sale")), "tipo_woo": p.get("type"),
            "categorias": [str(c.get("slug")) for c in (p.get("categories") or [])
                           if isinstance(c, dict) and c.get("slug")],
            "categorias_nombres": categorias,
        }, ensure_ascii=False),
    }


# --- la pasada ---------------------------------------------------------------------------------


def objetivos(session: Session, *, sitios: list[str] | None = None) -> list[tuple[Sitio, str]]:
    """`(web, idioma)` de cada web activa (o de las pedidas), en todos sus idiomas."""
    reg = registro_sitios.cargar(session)
    salida: list[tuple[Sitio, str]] = []
    for sitio in reg.webs:
        if sitios is not None and sitio.clave not in sitios:
            continue
        idiomas = list(sitio.idiomas) or [sitio.idioma_defecto]
        if sitio.idioma_defecto in idiomas:
            idiomas.remove(sitio.idioma_defecto)
            idiomas.insert(0, sitio.idioma_defecto)
        salida.extend((sitio, idioma) for idioma in idiomas)
    return salida


def sincronizar(
    session: Session, *, origen: str = SYNC_PROGRAMADA, http: httpx.Client | None = None,
    ahora: datetime | None = None, sitios: list[str] | None = None,
) -> CatalogoSincronizacion:
    """Una pasada por todas las tiendas (o las webs dadas) y sus idiomas. La red
    en paralelo, la base después y en orden. Devuelve la fila del histórico, ya
    en la sesión (el que llama hace commit)."""
    ahora = ahora or datetime.now(UTC)
    pasada = catalogo.abrir_pasada(session, fuente=FUENTE_WOOCOMMERCE, origen=origen, ahora=ahora)
    pares = objetivos(session, sitios=sitios)
    resumen: dict[str, dict[str, Any]] = {}
    errores: list[str] = []
    propio = http is None
    cliente = http or _cliente()
    try:
        def _bajar(par: tuple[Sitio, str]) -> Any:
            try:
                return listar_productos(cliente, par[0], par[1])
            except Exception as exc:  # noqa: BLE001 — una tienda caída no tira las demás
                return exc

        with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
            descargas = list(pool.map(_bajar, pares))
    finally:
        if propio:
            cliente.close()
    sin_tienda: list[str] = []
    for (sitio, idioma), bajada in zip(pares, descargas, strict=True):
        clave = f"{sitio.clave}:{idioma}"
        if isinstance(bajada, SinTienda):
            # No es un error: esa web (o esa variante de idioma) no tiene tienda.
            # Lo que hubiera indexado de ahí ya no está en ninguna tienda: ido.
            idos = catalogo.escribir_filas(
                session, [], fuente=FUENTE_WOOCOMMERCE, ahora=ahora, sitio=sitio.clave,
                idioma=idioma)["idos"]
            resumen[clave] = {"productos": 0, "nuevos": 0, "actualizados": 0, "idos": idos,
                              "error": None, "sin_tienda": True}
            sin_tienda.append(clave)
            continue
        if isinstance(bajada, Exception):
            logger.warning("kb.catalogo: %s sin sincronizar, índice anterior intacto: %s",
                           clave, bajada)
            resumen[clave] = {"productos": 0, "nuevos": 0, "actualizados": 0, "idos": 0,
                              "error": str(bajada)[:300]}
            errores.append(f"{clave}: {str(bajada)[:200]}")
            continue
        filas = [f for p in bajada if (f := fila_de(p, sitio=sitio, idioma=idioma, ahora=ahora))]
        if not filas:
            # Una tienda que responde 200 con cero productos es rara (un plugin a
            # medias, un filtro): se anota y no se marca ido lo indexado.
            resumen[clave] = {"productos": 0, "nuevos": 0, "actualizados": 0, "idos": 0,
                              "error": None, "sin_productos": True}
            continue
        resumen[clave] = catalogo.escribir_filas(
            session, filas, fuente=FUENTE_WOOCOMMERCE, ahora=ahora, sitio=sitio.clave,
            idioma=idioma)
    if sitios is None:
        # Una pasada completa: lo indexado de webs que ya no están activas (o
        # ya no están en la tabla) deja de ser vigente.
        idos_webs = catalogo.marcar_idos_fuera_de(
            session, fuente=FUENTE_WOOCOMMERCE, sitios={s.clave for s, _ in pares}, ahora=ahora)
        if idos_webs:
            resumen["webs_inactivas"] = {"productos": 0, "nuevos": 0, "actualizados": 0,
                                         "idos": idos_webs, "error": None}
    # El estado se mide sobre las tiendas e idiomas que SÍ tienen tienda: todas
    # caídas → error; alguna → parcial; ninguna → ok.
    intentados = len(pares) - len(sin_tienda)
    catalogo.cerrar_pasada(pasada, resumen, errores, total=intentados)
    session.flush()
    saltadas = sum(int(r.get("saltadas") or 0) for r in resumen.values())
    logger.info(
        "kb.catalogo: tiendas %s — %d productos, %d nuevos, %d actualizados, %d idos, "
        "%d saltados%s%s",
        pasada.estado, pasada.productos, pasada.nuevos, pasada.actualizados, pasada.idos,
        saltadas,
        f", {len(errores)} tiendas/idiomas con error" if errores else "",
        f", sin tienda: {len(sin_tienda)} webs/idiomas" if sin_tienda else "",
    )
    return pasada
