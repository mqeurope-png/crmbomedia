"""Cómo se lee cada tipo de centro de soporte (`SoporteFuente.tipo`).

- `zendesk` (FLUX): la API pública del Help Center, sin credenciales.
  Búsqueda en `/api/v2/help_center/articles/search.json` (trae título,
  enlace, extracto con la palabra resaltada y el cuerpo) y artículo en
  `/api/v2/help_center/{locale}/articles/{id}.json`. El locale sale de los
  idiomas de la fuente (FLUX: `es` → `es-419`, `en` → `en-us`…); si no hay
  nada en el idioma pedido, se prueba en inglés y se dice.
- `wordpress`: la REST pública (`/wp-json/wp/v2/search` y
  `/wp-json/wp/v2/{tipo}/{id}`), con la limpieza de WordPress (shortcodes de
  WPBakery, CSS suelto) que ya hace `html_texto`.
- `html`: una web sin búsqueda: solo se leen sus artículos por enlace (la
  ruta dentro de su dominio). Del HTML se queda el bloque principal, sin
  menú, pie ni laterales; si la página se pinta con JavaScript (cáscara), es
  un error y nunca un texto vacío que parezca un artículo.

TODA petición pasa por `red.obtener` con `hosts_permitidos={dominio de la
fuente}`: solo HTTPS, nunca la red de dentro, redirecciones revisadas una a
una (a otro dominio, se para), tiempo y tamaño máximos.

Lo descargado es DATO: aquí no se interpreta ni se filtra lo que diga la
página; se limpia de marcas (el texto) y de todo lo ejecutable (el HTML que
se pinta para leer).
"""
from __future__ import annotations

import html as html_mod
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import bleach
from bleach.css_sanitizer import CSSSanitizer

from app.models.fuentes_externas import (
    SOPORTE_HTML,
    SOPORTE_WORDPRESS,
    SOPORTE_ZENDESK,
    SoporteFuente,
)
from app.services.externo import html_texto, red
from app.services.html_sanitizer import ALLOWED_ATTRS, ALLOWED_STYLES, ALLOWED_TAGS
from app.services.soporte.fuentes import locales_de
from app.services.soporte.tipos import SoporteError

#: Segundos por petición. La búsqueda va con quien espera delante (y detrás
#: está el límite de 60 s del nginx de Plesk); el artículo puede pesar más.
TIMEOUT_BUSQUEDA = 8.0
TIMEOUT_ARTICULO = 10.0
#: Tope de lo descargado por petición: un artículo de soporte con su HTML
#: ronda las decenas de KB; 2 MB deja sitio de sobra sin abrir la puerta a
#: descargas enormes.
MAX_BYTES = 2 * 1024 * 1024
#: Largo del extracto de cada resultado (texto plano).
MAX_EXTRACTO = 300
#: Largo máximo del texto de un artículo (lo que llega al redactor ya va
#: recortado; esto solo evita guardar en la caché un texto desmesurado).
MAX_TEXTO = 60_000
#: Texto mínimo para dar una fuente por buena al comprobarla.
MINIMO_TEXTO = 200

#: Sin idioma, el de la casa; sin nada en ese idioma, inglés (la base de
#: todos los centros de soporte de los fabricantes).
IDIOMA_DEFECTO = "es"
IDIOMA_RESPALDO = "en"
_NOMBRE_IDIOMA = {"es": "español", "en": "inglés", "de": "alemán", "fr": "francés",
                  "nl": "neerlandés", "pt": "portugués", "it": "italiano", "pl": "polaco",
                  "ca": "catalán"}
_IDIOMA_RE = re.compile(r"^[a-z]{2}$")

#: Para los tests: un `httpx.Client` con `MockTransport` y un resolutor de DNS
#: falso. En producción, vacío (red de verdad, con los cerrojos de `red`).
_RED_PRUEBAS: dict[str, Any] = {}


def fijar_red_para_tests(*, cliente: Any = None, resolver: Any = None) -> None:
    """Sustituye la red (y el DNS) de todas las peticiones del soporte. Sin
    argumentos, vuelve a la de verdad."""
    _RED_PRUEBAS.clear()
    if cliente is not None:
        _RED_PRUEBAS["cliente"] = cliente
    if resolver is not None:
        _RED_PRUEBAS["resolver"] = resolver


# ---------------------------------------------------------------------------
# Idiomas
# ---------------------------------------------------------------------------


def idioma_de(valor: str | None) -> str | None:
    """`es-ES`, `ES`, `es_419` → `es`. Lo que no parece un idioma, `None`."""
    texto = str(valor or "").strip().lower().replace("_", "-").split("-")[0]
    return texto if _IDIOMA_RE.match(texto) else None


def _nombre_idioma(idioma: str | None) -> str:
    return _NOMBRE_IDIOMA.get(idioma or "", idioma or "ese idioma")


def _idioma_de_locale(locales: dict[str, str], locale: str | None) -> str | None:
    if not locale:
        return None
    for idioma, loc in locales.items():
        if loc.lower() == locale.lower():
            return idioma
    return idioma_de(locale)


# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------

#: HTTP de la API según lo que diga `red`.
_STATUS_RED = {
    "dominio_no_permitido": 403, "red_interna": 403, "no_https": 400, "url_invalida": 400,
    "puerto_no_permitido": 400, "no_resuelve": 502, "tiempo_agotado": 504,
    "sin_conexion": 502, "demasiado_grande": 502, "demasiadas_redirecciones": 502,
    "respuesta_ilegible": 502,
}


def _pedir(fuente: SoporteFuente, url: str, *, timeout: float,
           acepta: str = "application/json") -> red.Respuesta:
    """Una petición a la fuente, con su dominio como única lista blanca."""
    try:
        return red.obtener(
            url, max_bytes=MAX_BYTES, timeout=timeout, cabeceras={"Accept": acepta},
            hosts_permitidos={fuente.dominio.lower()},
            cliente=_RED_PRUEBAS.get("cliente"), resolver=_RED_PRUEBAS.get("resolver"),
        )
    except red.RedError as exc:
        raise SoporteError(exc.codigo, f"{fuente.nombre}: {exc}",
                           _STATUS_RED.get(exc.codigo, 502)) from exc


#: Lo que se pide para buscar (o comprobar): si no existe, la fuente está mal
#: configurada (tipo o dirección base), no es que falte un artículo.
_DE_BUSQUEDA = frozenset({"búsqueda", "listado"})


def _comprobar_status(fuente: SoporteFuente, resp: red.Respuesta, *, que: str) -> None:
    if resp.status == 404 and que in _DE_BUSQUEDA:
        raise SoporteError("respuesta_fuente",
                           f"{fuente.nombre}: la dirección de {que} no existe (HTTP 404); "
                           "revisa el tipo y la dirección base de la fuente.", 502)
    if resp.status == 404:
        raise SoporteError("no_encontrado", f"{fuente.nombre} no tiene {que}.", 404)
    if resp.status != 200:
        raise SoporteError("respuesta_fuente",
                           f"{fuente.nombre} ha respondido HTTP {resp.status}.", 502)


def _json(fuente: SoporteFuente, resp: red.Respuesta, *, que: str) -> Any:
    _comprobar_status(fuente, resp, que=que)
    try:
        return resp.json()
    except red.RedError as exc:
        raise SoporteError("respuesta_ilegible",
                           f"{fuente.nombre} no ha devuelto datos legibles (¿ha cambiado la "
                           "dirección o el tipo de la fuente?).", 502) from exc


def _ilegible(fuente: SoporteFuente) -> SoporteError:
    return SoporteError("respuesta_ilegible",
                        f"{fuente.nombre} ha respondido con un formato inesperado.", 502)


def _base(fuente: SoporteFuente) -> str:
    return (fuente.base_url or f"https://{fuente.dominio}").rstrip("/")


# ---------------------------------------------------------------------------
# Texto y HTML
# ---------------------------------------------------------------------------

#: Bloques cuyo contenido es código o maquinaria, no texto: fuera enteros
#: (bleach con `strip=True` quitaría la etiqueta y dejaría su «texto»).
_FUERA_RE = re.compile(
    r"<(script|style|iframe|object|embed|noscript|template|svg)\b[^>]*>"
    r".*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_CSS = CSSSanitizer(allowed_css_properties=ALLOWED_STYLES)
#: Para leer, enlaces e imágenes de la web; nada de `data:`, `cid:` ni
#: `javascript:`.
_PROTOCOLOS = ["http", "https", "mailto", "tel"]
_ATRIBUTO_URL_RE = re.compile(r'(\s(?:href|src)=")([^"]*)(")', re.IGNORECASE)
_ESQUEMA_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_LANG_RE = re.compile(r"<html\b[^>]*\blang=[\"']?([A-Za-z_-]{2,10})", re.IGNORECASE)


def _absolutas(html: str, base: str) -> str:
    """Las rutas relativas (`/hc/es/articles/…`), absolutas: el HTML se pinta
    en un iframe sin origen y una ruta relativa ahí no lleva a ninguna parte."""
    def cambiar(m: re.Match[str]) -> str:
        valor = html_mod.unescape(m.group(2)).strip()
        if not valor or valor.startswith("#") or _ESQUEMA_RE.match(valor):
            return m.group(0)
        return f"{m.group(1)}{html_mod.escape(urljoin(base, valor), quote=True)}{m.group(3)}"
    return _ATRIBUTO_URL_RE.sub(cambiar, html)


def sanear_html(html: str, base: str) -> str:
    """HTML para leer: la lista blanca del compositor (etiquetas, atributos y
    estilos), sin comentarios, sin scripts ni manejadores y con las rutas
    absolutas. El texto se conserva tal cual (también lo que parezca una
    orden: es contenido de la página)."""
    limpio = bleach.clean(
        _FUERA_RE.sub("", html or ""), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
        protocols=_PROTOCOLOS, css_sanitizer=_CSS, strip=True, strip_comments=True,
    )
    return _absolutas(limpio, base).strip()


def html_de_texto(texto: str) -> str:
    """HTML mínimo (párrafos) a partir del texto limpio: para las páginas de
    las que solo se fía el bloque principal ya extraído."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto or "") if p.strip()]
    return "\n".join(
        "<p>" + "<br>".join(html_mod.escape(linea) for linea in p.split("\n")) + "</p>"
        for p in parrafos)


def _texto_de_html(html: str) -> str:
    return html_texto.extraer(html or "", todo=True).texto[:MAX_TEXTO]


def _una_linea(texto: str, maximo: int) -> str:
    plano = " ".join((texto or "").split())
    if len(plano) <= maximo:
        return plano
    return plano[:maximo].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _extracto(snippet: Any, cuerpo: Any, consulta: str) -> str:
    """El extracto que da la fuente (con la palabra resaltada, sin marcas); si
    no da ninguno, el párrafo del cuerpo que más tiene que ver con la búsqueda."""
    texto = html_texto.html_a_texto(str(snippet or "")) if snippet else ""
    if not texto and cuerpo:
        texto = html_texto.fragmentos(_texto_de_html(str(cuerpo)), consulta.split(),
                                      max_chars=MAX_EXTRACTO, max_fragmentos=1)
    return _una_linea(texto, MAX_EXTRACTO)


def _titulo(valor: Any, *, es_html: bool = True) -> str:
    """Un título que puede traer entidades o etiquetas (`Beambox &#8211; II`,
    WordPress). Zendesk lo da en texto plano: ahí un «<E01>» es texto."""
    texto = str(valor or "")
    return _una_linea(html_texto.html_a_texto(texto) if es_html else texto, 500)


def _url_https(valor: Any, respaldo: str) -> str:
    """El enlace que da la fuente, si es https; si no, el que se construye."""
    texto = str(valor or "").strip()
    return texto if texto.lower().startswith("https://") else respaldo


def _fecha_iso(valor: Any) -> str | None:
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)).isoformat()


# ---------------------------------------------------------------------------
# Zendesk (FLUX)
# ---------------------------------------------------------------------------

_ZENDESK_ID_RE = re.compile(r"/articles/([0-9]{1,20})")


def _zendesk_id(articulo_id: str) -> str:
    """El id del artículo; también vale su enlace (`…/articles/360012-Camara`)."""
    valor = articulo_id.strip()
    if re.fullmatch(r"[0-9]{1,20}", valor):
        return valor
    hallado = _ZENDESK_ID_RE.search(valor)
    if hallado:
        return hallado.group(1)
    raise SoporteError("id_invalido", f"«{articulo_id[:80]}» no es un artículo de Zendesk.",
                       400)


def _zendesk_url(fuente: SoporteFuente, html_url: Any, zid: str, locale: str | None) -> str:
    respaldo = (f"{_base(fuente)}/hc/{locale}/articles/{zid}" if locale
                else f"{_base(fuente)}/hc/articles/{zid}")
    return _url_https(html_url, respaldo)


def _zendesk_buscar_en(fuente: SoporteFuente, consulta: str, locale: str | None,
                       limite: int, locales: dict[str, str]) -> list[dict[str, Any]]:
    parametros: dict[str, Any] = {"query": consulta, "per_page": limite}
    if locale:
        parametros["locale"] = locale
    url = f"{_base(fuente)}/api/v2/help_center/articles/search.json?{urlencode(parametros)}"
    datos = _json(fuente, _pedir(fuente, url, timeout=TIMEOUT_BUSQUEDA), que="búsqueda")
    if not isinstance(datos, dict) or not isinstance(datos.get("results"), list):
        raise _ilegible(fuente)
    salida: list[dict[str, Any]] = []
    for item in datos["results"]:
        if not isinstance(item, dict) or item.get("result_type") not in (None, "article"):
            continue
        zid = str(item.get("id") or "")
        if not zid.isdigit():
            continue
        loc = str(item.get("locale") or locale or "") or None
        salida.append({
            "articulo_id": zid,
            "titulo": _titulo(item.get("title"), es_html=False) or "(sin título)",
            "extracto": _extracto(item.get("snippet"), item.get("body"), consulta),
            "url": _zendesk_url(fuente, item.get("html_url"), zid, loc),
            "idioma": _idioma_de_locale(locales, loc),
        })
        if len(salida) >= limite:
            break
    return salida


def _zendesk_buscar(fuente: SoporteFuente, consulta: str, idioma: str,
                    limite: int) -> dict[str, Any]:
    locales = locales_de(fuente)
    respaldo = locales.get(IDIOMA_RESPALDO)
    avisos: list[str] = []
    if not locales:
        locale = None   # sin idiomas configurados: el que dé la web
    elif idioma in locales:
        locale = locales[idioma]
    else:
        locale = respaldo
        avisos.append(f"{fuente.nombre} no tiene artículos en {_nombre_idioma(idioma)}: "
                      + ("se busca en inglés." if respaldo else "se busca en su idioma por "
                         "defecto."))
    resultados = _zendesk_buscar_en(fuente, consulta, locale, limite, locales)
    if not resultados and respaldo and locale and locale != respaldo:
        resultados = _zendesk_buscar_en(fuente, consulta, respaldo, limite, locales)
        if resultados:
            avisos.append(f"{fuente.nombre}: nada en {_nombre_idioma(idioma)} para "
                          f"«{consulta}»; estos artículos están en inglés.")
    return {"resultados": resultados, "avisos": avisos}


def _zendesk_articulo(fuente: SoporteFuente, articulo_id: str,
                      idioma: str) -> dict[str, Any]:
    zid = _zendesk_id(articulo_id)
    locales = locales_de(fuente)
    intentos: list[str | None] = []
    if locales:
        for loc in (locales.get(idioma), locales.get(IDIOMA_RESPALDO)):
            if loc and loc not in intentos:
                intentos.append(loc)
    if not intentos:
        intentos.append(None)
    for locale in intentos:
        ruta = (f"/api/v2/help_center/{locale}/articles/{zid}.json" if locale
                else f"/api/v2/help_center/articles/{zid}.json")
        resp = _pedir(fuente, _base(fuente) + ruta, timeout=TIMEOUT_ARTICULO)
        if resp.status == 404:
            continue    # no está en ese idioma: el siguiente (inglés)
        datos = _json(fuente, resp, que=f"el artículo {zid}")
        art = datos.get("article") if isinstance(datos, dict) else None
        if not isinstance(art, dict):
            raise _ilegible(fuente)
        cuerpo = str(art.get("body") or "")
        loc = str(art.get("locale") or locale or "") or None
        url = _zendesk_url(fuente, art.get("html_url"), zid, loc)
        texto = _texto_de_html(cuerpo)
        if not texto:
            raise SoporteError("sin_contenido",
                               f"El artículo {zid} de {fuente.nombre} no trae texto.", 422)
        return {
            "articulo_id": zid,
            "titulo": _titulo(art.get("title"), es_html=False) or "(sin título)",
            "url": url, "idioma": _idioma_de_locale(locales, loc), "texto": texto,
            "html": sanear_html(cuerpo, url),
            "actualizado": _fecha_iso(art.get("updated_at") or art.get("edited_at")),
        }
    raise SoporteError("no_encontrado", f"{fuente.nombre} no tiene el artículo {zid}.", 404)


def _zendesk_comprobar(fuente: SoporteFuente) -> tuple[str, str]:
    locales = locales_de(fuente)
    locale = locales.get(IDIOMA_DEFECTO) or locales.get(IDIOMA_RESPALDO)
    prueba = (fuente.marca or (fuente.nombre or "").split(" ")[0] or "help").strip()
    parametros: dict[str, Any] = {"query": prueba, "per_page": 3}
    if locale:
        parametros["locale"] = locale
    url = f"{_base(fuente)}/api/v2/help_center/articles/search.json?{urlencode(parametros)}"
    datos = _json(fuente, _pedir(fuente, url, timeout=TIMEOUT_BUSQUEDA), que="búsqueda")
    if not isinstance(datos, dict) or not isinstance(datos.get("results"), list):
        raise _ilegible(fuente)
    articulos = [r for r in datos["results"] if isinstance(r, dict)]
    origen = f"la búsqueda de prueba («{prueba}»)"
    if not articulos:
        # La palabra de prueba puede no salir: el listado dice si hay algo.
        ruta = (f"/api/v2/help_center/{locale}/articles.json?per_page=3" if locale
                else "/api/v2/help_center/articles.json?per_page=3")
        listado = _json(fuente, _pedir(fuente, _base(fuente) + ruta,
                                       timeout=TIMEOUT_BUSQUEDA), que="listado")
        if not isinstance(listado, dict) or not isinstance(listado.get("articles"), list):
            raise _ilegible(fuente)
        articulos = [r for r in listado["articles"] if isinstance(r, dict)]
        origen = "el listado de artículos"
        if not articulos:
            return "vacio", ("La API responde pero no devuelve ningún artículo público "
                             f"(ni la búsqueda «{prueba}» ni el listado).")
    con_texto = [a for a in articulos if len(_texto_de_html(str(a.get("body") or "")))
                 >= MINIMO_TEXTO]
    if not con_texto:
        return "vacio", (f"{origen.capitalize()} devuelve artículos, pero sin texto: no "
                         "servirían para escribir.")
    primero = con_texto[0]
    return "ok", (f"Responde con contenido: {origen} trae {len(articulos)} artículo(s); "
                  f"«{_titulo(primero.get('title'), es_html=False)}» tiene "
                  f"{len(_texto_de_html(str(primero.get('body') or '')))} caracteres de texto.")


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------

_WP_ID_RE = re.compile(r"^([a-z0-9_-]{1,40})/(\d{1,20})$")
_WP_REST_RE = re.compile(r"/wp/v2/([a-z0-9_-]{1,40})/(\d{1,20})/?$")
#: El tipo de contenido de la búsqueda → su ruta en la REST.
_WP_RUTA = {"post": "posts", "page": "pages"}


def _wp_articulo_id(item: dict[str, Any]) -> str | None:
    """`posts/123`: la ruta de la REST del resultado (de su enlace `self`, que
    trae la ruta exacta, también la de los tipos propios)."""
    try:
        href = str(item["_links"]["self"][0]["href"])
    except (KeyError, IndexError, TypeError):
        href = ""
    hallado = _WP_REST_RE.search(urlsplit(href).path) if href else None
    if hallado:
        return f"{hallado.group(1)}/{hallado.group(2)}"
    wid = str(item.get("id") or "")
    subtipo = str(item.get("subtype") or "")
    ruta = _WP_RUTA.get(subtipo, subtipo)
    if wid.isdigit() and re.fullmatch(r"[a-z0-9_-]{1,40}", ruta or ""):
        return f"{ruta}/{wid}"
    return None


def _wp_extracto(item: dict[str, Any], consulta: str) -> str:
    try:
        embebido = item["_embedded"]["self"][0]
        crudo = embebido.get("excerpt", {}).get("rendered") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        crudo = ""
    texto = html_texto.limpiar_texto_wp(html_texto.html_a_texto(str(crudo)))
    return _una_linea(html_texto.fragmentos(texto, consulta.split(), max_chars=MAX_EXTRACTO,
                                            max_fragmentos=1) if texto else "", MAX_EXTRACTO)


def _wp_buscar_en(fuente: SoporteFuente, consulta: str, lang: str | None, limite: int,
                  idioma: str | None) -> list[dict[str, Any]]:
    parametros: dict[str, Any] = {"search": consulta, "per_page": limite, "_embed": "self"}
    if lang:
        parametros["lang"] = lang
    url = f"{_base(fuente)}/wp-json/wp/v2/search?{urlencode(parametros)}"
    datos = _json(fuente, _pedir(fuente, url, timeout=TIMEOUT_BUSQUEDA), que="búsqueda")
    if not isinstance(datos, list):
        raise SoporteError("respuesta_ilegible",
                           f"{fuente.nombre} no responde como WordPress (/wp-json).", 502)
    salida: list[dict[str, Any]] = []
    for item in datos:
        if not isinstance(item, dict) or item.get("type") not in (None, "post"):
            continue
        articulo_id = _wp_articulo_id(item)
        if not articulo_id:
            continue
        salida.append({
            "articulo_id": articulo_id,
            "titulo": _titulo(item.get("title")) or "(sin título)",
            "extracto": _wp_extracto(item, consulta),
            "url": _url_https(item.get("url"), _base(fuente)),
            "idioma": idioma,
        })
        if len(salida) >= limite:
            break
    return salida


def _wordpress_buscar(fuente: SoporteFuente, consulta: str, idioma: str,
                      limite: int) -> dict[str, Any]:
    locales = locales_de(fuente)
    avisos: list[str] = []
    lang = locales.get(idioma) if locales else None
    if locales and lang is None:
        avisos.append(f"{fuente.nombre} no tiene contenido en {_nombre_idioma(idioma)}: se "
                      "busca sin filtrar por idioma.")
    resultados = _wp_buscar_en(fuente, consulta, lang, limite, idioma if lang else None)
    respaldo = locales.get(IDIOMA_RESPALDO)
    if not resultados and lang and respaldo and respaldo != lang:
        resultados = _wp_buscar_en(fuente, consulta, respaldo, limite, IDIOMA_RESPALDO)
        if resultados:
            avisos.append(f"{fuente.nombre}: nada en {_nombre_idioma(idioma)} para "
                          f"«{consulta}»; estos resultados están en inglés.")
    return {"resultados": resultados, "avisos": avisos}


def _wordpress_articulo(fuente: SoporteFuente, articulo_id: str,
                        idioma: str) -> dict[str, Any]:
    hallado = _WP_ID_RE.match(articulo_id.strip())
    if not hallado:
        raise SoporteError("id_invalido",
                           f"«{articulo_id[:80]}» no es un contenido de WordPress (tipo/id).",
                           400)
    tipo, wid = hallado.groups()
    resp = _pedir(fuente, f"{_base(fuente)}/wp-json/wp/v2/{tipo}/{wid}",
                  timeout=TIMEOUT_ARTICULO)
    datos = _json(fuente, resp, que=f"el contenido {tipo}/{wid}")
    if not isinstance(datos, dict):
        raise _ilegible(fuente)
    contenido = datos.get("content") if isinstance(datos.get("content"), dict) else {}
    crudo = str(contenido.get("rendered") or "")
    bruto = _texto_de_html(crudo)
    texto = html_texto.limpiar_texto_wp(bruto)
    if not texto:
        raise SoporteError("sin_contenido",
                           f"El contenido {tipo}/{wid} de {fuente.nombre} no trae texto.", 422)
    url = _url_https(datos.get("link"), _base(fuente))
    titulo = datos.get("title")
    titulo = titulo.get("rendered") if isinstance(titulo, dict) else titulo
    locales = locales_de(fuente)
    lang = str(datos.get("lang") or "")
    return {
        "articulo_id": f"{tipo}/{wid}", "titulo": _titulo(titulo) or "(sin título)",
        "url": url,
        "idioma": (_idioma_de_locale(locales, lang) if lang
                   else (idioma if locales else None)),
        "texto": texto,
        # Si la REST traía shortcodes o CSS sueltos, el HTML tal cual los
        # enseñaría: entonces se pinta el texto ya limpio.
        "html": sanear_html(crudo, url) if texto == bruto else html_de_texto(texto),
        "actualizado": _fecha_iso(datos.get("modified_gmt") or datos.get("modified")),
    }


def _wordpress_comprobar(fuente: SoporteFuente) -> tuple[str, str]:
    # Sin palabra: la búsqueda de WordPress devuelve lo último publicado.
    url = f"{_base(fuente)}/wp-json/wp/v2/search?{urlencode({'per_page': 3})}"
    datos = _json(fuente, _pedir(fuente, url, timeout=TIMEOUT_BUSQUEDA), que="búsqueda")
    if not isinstance(datos, list):
        return "error", f"{fuente.nombre} no responde como WordPress (/wp-json)."
    ids = [i for i in (_wp_articulo_id(d) for d in datos if isinstance(d, dict)) if i]
    if not ids:
        return "vacio", "La búsqueda de WordPress responde, pero no devuelve ningún contenido."
    try:
        art = _wordpress_articulo(fuente, ids[0], IDIOMA_DEFECTO)
    except SoporteError as exc:
        if exc.codigo == "sin_contenido":
            return "vacio", f"La búsqueda responde, pero el primer contenido viene vacío ({exc})."
        raise
    if len(art["texto"]) < MINIMO_TEXTO:
        return "vacio", (f"La búsqueda responde, pero «{art['titulo']}» solo trae "
                         f"{len(art['texto'])} caracteres de texto.")
    return "ok", (f"Responde con contenido: «{art['titulo']}» tiene {len(art['texto'])} "
                  "caracteres de texto.")


# ---------------------------------------------------------------------------
# HTML (solo por enlace)
# ---------------------------------------------------------------------------


def _ruta_html(fuente: SoporteFuente, articulo_id: str) -> str:
    """La ruta dentro del dominio de la fuente (`/es/ayuda/calibrar`). Vale el
    enlace entero, pero SOLO si es de ese dominio: un enlace a otro sitio no se
    pide (la lista blanca)."""
    valor = articulo_id.strip()
    if not valor or re.search(r"[\s\x00-\x1f\x7f]", valor):
        raise SoporteError("id_invalido", "El enlace del artículo no es válido.", 400)
    if "://" in valor or valor.startswith("//"):
        try:
            partes = urlsplit(valor if "://" in valor else f"https:{valor}")
            host = (partes.hostname or "").lower()
        except ValueError as exc:
            raise SoporteError("id_invalido", "El enlace del artículo no es válido.",
                               400) from exc
        if host != fuente.dominio.lower():
            raise SoporteError(
                "dominio_no_permitido",
                f"{host or valor[:80]} no es {fuente.nombre} ({fuente.dominio}): solo se leen "
                "artículos de los dominios de la lista de centros de soporte.", 403)
        ruta = partes.path or "/"
        if partes.query:
            ruta += f"?{partes.query}"
        return ruta
    ruta = valor.split("#")[0]
    return ruta if ruta.startswith("/") else f"/{ruta}"


def _html_articulo(fuente: SoporteFuente, articulo_id: str, idioma: str) -> dict[str, Any]:
    ruta = _ruta_html(fuente, articulo_id)
    resp = _pedir(fuente, f"https://{fuente.dominio}{ruta}", timeout=TIMEOUT_ARTICULO,
                  acepta="text/html,application/xhtml+xml")
    _comprobar_status(fuente, resp, que="esa página")
    if resp.tipo not in ("", "text/html", "application/xhtml+xml"):
        raise SoporteError("no_es_pagina",
                           f"{resp.url} no es una página web ({resp.tipo}).", 422)
    crudo = resp.texto()
    extraccion = html_texto.extraer(crudo)
    if html_texto.es_cascara(crudo, extraccion.texto):
        raise SoporteError(
            "cascara_vacia",
            f"{resp.url} se pinta con JavaScript: el HTML llega sin el artículo (una cáscara "
            "vacía). No se puede leer desde aquí: ábrelo en el navegador.", 422)
    texto = extraccion.texto[:MAX_TEXTO]
    lang = _LANG_RE.search(crudo[:5000])
    modificado = resp.cabeceras.get("last-modified")
    try:
        actualizado = (parsedate_to_datetime(modificado).astimezone(UTC).isoformat()
                       if modificado else None)
    except (TypeError, ValueError):
        actualizado = None
    url_final = resp.url.split("#")[0]
    partes = urlsplit(url_final)
    return {
        "articulo_id": (partes.path or "/") + (f"?{partes.query}" if partes.query else ""),
        "titulo": _una_linea(extraccion.titulo, 500) or ruta,
        "url": url_final,
        "idioma": idioma_de(lang.group(1)) if lang else None,
        "texto": texto,
        # Del HTML de una web entera solo se fía el bloque principal ya
        # extraído (sin menús ni pies): se pinta ese texto.
        "html": html_de_texto(texto),
        "actualizado": actualizado,
    }


def _html_comprobar(fuente: SoporteFuente) -> tuple[str, str]:
    resp = _pedir(fuente, _base(fuente), timeout=TIMEOUT_ARTICULO,
                  acepta="text/html,application/xhtml+xml")
    _comprobar_status(fuente, resp, que="esa página")
    crudo = resp.texto()
    extraccion = html_texto.extraer(crudo)
    if html_texto.es_cascara(crudo, extraccion.texto):
        return "vacio", ("La página se pinta con JavaScript: el HTML llega sin contenido "
                         f"({len(extraccion.texto)} caracteres de texto). Desde aquí no se "
                         "pueden leer sus artículos.")
    return "ok", (f"La página sirve contenido: «{_una_linea(extraccion.titulo, 120)}», "
                  f"{len(extraccion.texto)} caracteres de texto.")


# ---------------------------------------------------------------------------
# Por tipo
# ---------------------------------------------------------------------------


def normalizar_id(fuente: SoporteFuente, articulo_id: str) -> str:
    """El id con el que se guarda en la caché (y se comprueba el dominio antes
    de mirar nada: un enlace de fuera no llega ni a la caché)."""
    if fuente.tipo == SOPORTE_ZENDESK:
        return _zendesk_id(articulo_id)
    if fuente.tipo == SOPORTE_HTML:
        return _ruta_html(fuente, articulo_id)
    return articulo_id.strip()


def buscar(fuente: SoporteFuente, consulta: str, idioma: str, limite: int) -> dict[str, Any]:
    """`{"resultados": [...], "avisos": [...]}` (lo que se guarda en la caché)."""
    if fuente.tipo == SOPORTE_ZENDESK:
        return _zendesk_buscar(fuente, consulta, idioma, limite)
    if fuente.tipo == SOPORTE_WORDPRESS:
        return _wordpress_buscar(fuente, consulta, idioma, limite)
    return {"resultados": [], "avisos": [
        f"{fuente.nombre} no tiene búsqueda: solo se leen sus artículos por enlace."]}


def articulo(fuente: SoporteFuente, articulo_id: str, idioma: str) -> dict[str, Any]:
    if fuente.tipo == SOPORTE_ZENDESK:
        return _zendesk_articulo(fuente, articulo_id, idioma)
    if fuente.tipo == SOPORTE_WORDPRESS:
        return _wordpress_articulo(fuente, articulo_id, idioma)
    if fuente.tipo == SOPORTE_HTML:
        return _html_articulo(fuente, articulo_id, idioma)
    raise SoporteError("tipo_desconocido", f"{fuente.nombre}: tipo «{fuente.tipo}» desconocido.",
                       400)


def comprobar(fuente: SoporteFuente) -> tuple[str, str]:
    """`(estado, detalle)`: `ok` (sirve contenido), `vacio` (cáscara que se
    pinta con JavaScript, o sin resultados con texto) o `error`."""
    try:
        if fuente.tipo == SOPORTE_ZENDESK:
            return _zendesk_comprobar(fuente)
        if fuente.tipo == SOPORTE_WORDPRESS:
            return _wordpress_comprobar(fuente)
        if fuente.tipo == SOPORTE_HTML:
            return _html_comprobar(fuente)
        return "error", f"Tipo «{fuente.tipo}» desconocido."
    except SoporteError as exc:
        if exc.codigo == "cascara_vacia":
            return "vacio", str(exc)
        return "error", str(exc)
