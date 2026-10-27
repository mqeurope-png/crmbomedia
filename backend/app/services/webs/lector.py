"""Lectura de una web WordPress para el índice: páginas, entradas y productos,
idioma a idioma, por los endpoints PÚBLICOS (sin credenciales; nada escribe).

Lo medido en las webs del grupo (27 y 28/10/2026) manda aquí:

- **Siempre `?rest_route=`** (`https://web/?rest_route=/wp/v2/pages&per_page=100
  &page=N&_fields=…`), con `&` para los parámetros (un segundo `?` devuelve
  vacío). `mboprinters.com` lo necesita y funciona en todas.
- **Idiomas sin mezclar** (una fila por idioma):
  - WPGlobus (`plugin_traduccion = qtranslate`: artisjet-printers.eu,
    mboprinters.com, www.mqeurope.com): el idioma va en el prefijo de la ruta
    (`/en/?rest_route=…`) y los ids son los mismos en todos, también los de
    las categorías (sus NOMBRES sí se traducen). Si una página no está
    traducida, WPGlobus sirve el texto del idioma por defecto con la URL del
    otro: solo se indexa el idioma xx si `translation.languages.xx.content`
    (de la petición en el idioma por defecto) lo dice. Los productos no traen
    ese campo: el respaldo se reconoce porque el texto es idéntico al del
    idioma por defecto.
  - WPML (mbolasers.com, fluxlasers.eu, pimpam-vending.com): `&lang=xx`, cada
    idioma con sus propios ids y sus propios slugs (páginas, productos Y
    categorías: «Accesorios» de mbolasers es la 39 en es, la 763 «Accessories»
    en en y la 765 «Zubehör» en de), y un idioma sin traducción vuelve EN
    SILENCIO al de por defecto: se descarta lo que repite un id del idioma por
    defecto o no lleva el idioma en el enlace (`/de/…`). pimpam publica el
    portugués como `pt-pt`: si `pt` solo da respaldo, se prueba `pt-pt` (y
    `pt-br`).
- **Productos por la Store API** (`/wc/store/v1/products/categories` y
  `/products`), con las categorías de fuera filtradas ya en el servidor
  (`category=<ids>&category_operator=not_in`) y revisadas otra vez aquí, POR
  ID. Precio «como dice la web» (solo si es mayor que 0) y su IVA según lo que
  pone la web. La fecha de modificación sale de `/wp/v2/product` (la Store API
  no la da); si falla, queda sin fecha.
- **Qué productos entran se decide UNA vez por web, en su idioma por
  defecto, y se lleva a los demás POR ID**; los nombres traducidos no deciden
  nada (el 27/10 fluxlasers.eu metió 1.219 productos en vez de ~166 porque en
  en/de/fr/nl «Spare Parts», «Ersatzteile»… no empiezan por «Repuestos»):
  - Idioma por defecto: fuera las categorías cuyo nombre EMPIEZA por una regla
    `categoria_fuera` (escritas en ese idioma) y todas sus hijas
    (`categorias_fuera`). El árbol se pide con `hide_empty=false` (la Store
    API esconde de partida las vacías, también en `include=`: una de fuera
    vacía en este idioma y con productos en otro se colaría). Y, como red de
    seguridad, solo aquí (donde los nombres están en el idioma de las reglas):
    fuera también el producto con una categoría PROPIA cuyo nombre empieza
    por una regla, aunque falte del árbol (`count` desfasado, `MAX_PAGINAS`).
  - WPGlobus y webs de un idioma: los mismos ids en todos los idiomas → las
    mismas categorías de fuera y las mismas excepciones por id.
  - WPML, otro idioma: WPML «ajusta los ids» de `include=` (pedido en inglés,
    `categories&include=39` devuelve la 763) y de `category=…&not_in`. Se
    piden las traducciones de las de fuera y las de todas; fuera = esas
    traducciones, las del idioma por defecto (los ids de término son únicos
    en toda la instalación) y todas sus hijas en el árbol del idioma. Si el
    idioma tiene categorías PROPIAS (ids que no son del idioma por defecto) y
    NINGUNA es traducción de una del idioma por defecto (WPML no ha ajustado
    los ids), su grupo de productos queda en error: no se escribe ni se marca
    nada ido. Una categoría sin traducir servida con su id del idioma por
    defecto no cuenta como enlace. Una categoría que solo existe
    en ese idioma («sin original») y no cuelga de una de fuera no deja entrar
    a un producto que no tenga otra (`categoria_sin_original`), y se avisa.
    Las excepciones por id de producto se traducen igual
    (`products&include=…`).

Toda petición pasa por `externo.red.obtener` (sin red interna, tiempo y
tamaño máximos). Una web o un idioma que falla (sin red, 5xx tras el
reintento, una respuesta que no es la esperada) deja su grupo con `error` y
quien escribe NO marca nada como ido (se queda lo anterior). Ya no hay camino
por el HTML público: el 500 de mbolasers.com con el contenido de sus páginas
era el límite de memoria de WordPress, arreglado en la web (28/10).
"""
from __future__ import annotations

import html as html_mod
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.models.fuentes_externas import (
    IVA_CON,
    IVA_DESCONOCIDO,
    IVA_SIN,
    WEB_ENTRADA,
    WEB_PAGINA,
    WEB_PRODUCTO,
)
from app.models.sitios import PLUGIN_WPML
from app.services.externo import red
from app.services.externo.html_texto import (
    html_a_texto,
    limpiar_texto_wp,
    normalizar_espacios,
)
from app.services.web_forms.sitios import Sitio
from app.services.webs.reglas import ReglasSitio

logger = logging.getLogger(__name__)

#: Elementos por petición (el máximo de la REST de WordPress).
POR_PAGINA = 100
#: Tope de páginas de resultados por lista (3.000 elementos): una web que
#: pagina mal no deja el trabajo dando vueltas.
MAX_PAGINAS = 30
#: Segundos por petición. El tiempo se va en el PHP de WordPress (de 1,6 a
#: 5,8 s medidos por cada 100 elementos), no en la red: margen de sobra.
TIMEOUT_SEGUNDOS = 45.0
#: Tamaño máximo de una respuesta JSON (100 páginas con su HTML caben de
#: sobra: la mayor medida, unos 2 MB).
MAX_BYTES_JSON = 16 * 1024 * 1024
#: Pausa entre peticiones (segundos): con unas 120 por recorrido no cuesta y
#: no aprieta a las webs (comparten servidor con la tienda).
PAUSA_SEGUNDOS = 0.25
#: Una página o entrada con menos texto limpio que esto no dice nada que
#: sirva al redactor (portadas que pinta el tema, «contacto», «tienda»,
#: «gracias por escribir»): fuera. Medido: las útiles tienen de mediana más
#: de 2.000 caracteres y las vacías se quedan por debajo de 140.
MIN_TEXTO_PAGINA = 150
#: El extracto que se guarda (el texto entero va en `texto`).
MAX_EXTRACTO = 500
#: Páginas de plantilla que nadie ha rellenado (pimpam tiene una «Inicio»
#: con el texto de relleno del tema).
_RELLENO_RE = re.compile(r"\blorem ipsum\b", re.IGNORECASE)

CAMPOS_CONTENIDO = "id,slug,link,modified_gmt,title,content,excerpt"
#: Del árbol de categorías basta con esto: decide el id y el `parent`; el
#: nombre, para aplicar las reglas en el idioma por defecto y para los avisos.
CAMPOS_CATEGORIA = "id,name,parent"
#: La Store API esconde de partida las categorías vacías (`hide_empty`, true),
#: en el árbol y en `include=`: una de fuera vacía en el idioma por defecto
#: pero con productos en otro no estaría ni entre las de fuera ni traducida.
#: Se piden todas.
TODAS_LAS_CATEGORIAS = {"hide_empty": "false"}
#: WPGlobus calcula `translation` a partir del título y el contenido de la
#: respuesta: sin ellos dice que nada está traducido.
CAMPO_TRADUCCION = "translation"

#: Códigos de WPML que se prueban cuando el nuestro solo da respaldo.
ALTERNATIVAS_WPML: dict[str, tuple[str, ...]] = {
    "pt": ("pt-pt", "pt-br"),
    "zh": ("zh-hans", "zh-hant"),
}

_RUTAS = {WEB_PAGINA: "/wp/v2/pages", WEB_ENTRADA: "/wp/v2/posts"}
_RUTA_CATEGORIAS = "/wc/store/v1/products/categories"
_RUTA_PRODUCTOS = "/wc/store/v1/products"
_RUTA_PRODUCTO_WP = "/wp/v2/product"

MODO_UNICO = "unico"
MODO_PREFIJO = "prefijo"
MODO_WPML = "wpml"


class LecturaError(Exception):
    """Una petición que no ha salido bien. El mensaje se puede enseñar."""


class SinRuta(LecturaError):
    """La web no tiene ese endpoint (`rest_no_route`, 404): p. ej. pimpam no
    tiene WooCommerce. No es un error de la web: no hay nada que leer."""


class ErrorServidor(LecturaError):
    """HTTP 5xx tras el reintento: el grupo queda en error, como cualquier
    otro fallo (se queda lo anterior)."""


# --- la red ----------------------------------------------------------------------------------


class Http:
    """Las peticiones de una web, todas por `red.obtener`. Los tests pasan un
    `cliente` con `httpx.MockTransport`, un `resolver` que no sale a la red y
    `pausa=0`."""

    def __init__(self, *, cliente: httpx.Client | None = None,
                 resolver: red.Resolutor | None = None, pausa: float = PAUSA_SEGUNDOS,
                 timeout: float = TIMEOUT_SEGUNDOS,
                 dormir: Callable[[float], None] = time.sleep) -> None:
        self._propio = cliente is None
        self.cliente = cliente or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False,
            headers={"User-Agent": red.USER_AGENT})
        self.resolver = resolver
        self.pausa = pausa
        self.timeout = timeout
        self.dormir = dormir
        #: Lo pedido, en orden (para el resumen y para los tests).
        self.urls: list[str] = []
        self._ultima = 0.0

    def cerrar(self) -> None:
        if self._propio:
            self.cliente.close()

    def _esperar(self) -> None:
        if self.pausa <= 0:
            return
        hueco = self.pausa - (time.monotonic() - self._ultima)
        if hueco > 0:
            self.dormir(hueco)

    def obtener(self, url: str, *, max_bytes: int,
                hosts: set[str] | None = None) -> red.Respuesta:
        """Una petición con un reintento si la red falla o la web da 5xx (WordPress
        compartido falla a ratos). `LecturaError` si no sale."""
        ultimo: Exception | None = None
        for intento in range(2):
            self._esperar()
            self.urls.append(url)
            try:
                respuesta = red.obtener(
                    url, max_bytes=max_bytes, timeout=self.timeout,
                    cabeceras={"Accept": "application/json"}, hosts_permitidos=hosts,
                    resolver=self.resolver, cliente=self.cliente)
            except red.UrlNoPermitida as exc:
                raise LecturaError(str(exc)) from exc
            except red.RedError as exc:
                ultimo = exc
                if exc.codigo in ("tiempo_agotado", "sin_conexion") and intento == 0:
                    continue
                raise LecturaError(str(exc)) from exc
            finally:
                self._ultima = time.monotonic()
            if respuesta.status >= 500:
                ultimo = ErrorServidor(f"HTTP {respuesta.status}")
                if intento == 0:
                    continue
                raise ultimo
            return respuesta
        raise LecturaError(str(ultimo or "sin respuesta"))

    def lista(self, url: str, *, hosts: set[str] | None = None,
              ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Una página de una lista JSON de la REST. `SinRuta` si el endpoint
        no existe; `([], …)` si la página pedida pasa del final."""
        respuesta = self.obtener(url, max_bytes=MAX_BYTES_JSON, hosts=hosts)
        try:
            datos = respuesta.json()
        except red.RedError:
            datos = None
        codigo = datos.get("code") if isinstance(datos, dict) else None
        if respuesta.status == 404 or codigo == "rest_no_route":
            raise SinRuta(f"sin endpoint ({codigo or 'HTTP 404'})")
        if respuesta.status == 400 and codigo == "rest_post_invalid_page_number":
            return [], respuesta.cabeceras
        if respuesta.status != 200:
            raise LecturaError(f"HTTP {respuesta.status}" + (f" ({codigo})" if codigo else ""))
        if codigo:
            raise LecturaError(f"la web respondió «{codigo}»")
        if not isinstance(datos, list):
            raise LecturaError("la respuesta no es una lista")
        return [d for d in datos if isinstance(d, dict)], respuesta.cabeceras


def url_rest(base: str, ruta: str, params: dict[str, Any] | None = None) -> str:
    """`https://web/[xx/]?rest_route=/wp/v2/pages&per_page=100&…`: SIEMPRE con
    `?rest_route=` y el resto de parámetros con `&`."""
    partes = [f"{base}?rest_route={ruta}"]
    for clave, valor in (params or {}).items():
        if valor is None or valor == "":
            continue
        partes.append(f"{clave}={quote(str(valor), safe=',-_.')}")
    return "&".join(partes)


def paginar(http: Http, base: str, ruta: str, params: dict[str, Any], *,
            hosts: set[str] | None = None) -> list[dict[str, Any]]:
    """Todas las páginas de una lista. Para cuando `X-WP-TotalPages` lo dice,
    cuando una página viene corta o cuando una página repite lo ya visto (un
    endpoint que no pagina, como las categorías de la Store API)."""
    salida: list[dict[str, Any]] = []
    vistos: set[Any] = set()
    for pagina in range(1, MAX_PAGINAS + 1):
        # `per_page` y `page` primero, como se pide a mano (`…&per_page=100&page=N&_fields=…`).
        elementos, cabeceras = http.lista(
            url_rest(base, ruta, {"per_page": POR_PAGINA, "page": pagina, **params}),
            hosts=hosts)
        nuevos = [e for e in elementos if e.get("id") not in vistos]
        vistos.update(e.get("id") for e in elementos)
        salida.extend(nuevos)
        try:
            total_paginas = int(cabeceras.get("x-wp-totalpages") or 0)
        except ValueError:
            total_paginas = 0
        if (not nuevos or len(elementos) < POR_PAGINA
                or (total_paginas and pagina >= total_paginas)):
            break
    else:
        logger.warning("webs: %s%s cortado en %d páginas", base, ruta, MAX_PAGINAS)
    return salida


# --- lo leído --------------------------------------------------------------------------------


@dataclass
class Grupo:
    """Lo leído de un tipo en un idioma de una web."""

    tipo: str
    idioma: str
    filas: list[dict[str, Any]] = field(default_factory=list)
    #: La web no respondió bien: no se escribe nada y no se marca nada ido.
    error: str | None = None
    #: La web respondió 200 con la lista vacía (raro: un plugin a medias): no
    #: se marca ido lo anterior (como el catálogo de las tiendas).
    vacio: bool = False
    #: Lo que dio la web antes de filtrar.
    leidos: int = 0
    #: Lo descartado, por motivo (`regla`, `corta`, `respaldo`, `categoria`,
    #: `categoria_sin_original`, `excluido`, `sin_categoria`…).
    descartes: dict[str, int] = field(default_factory=dict)
    #: WPML: TODO lo que dio la web en este idioma era el respaldo del idioma
    #: por defecto (el idioma no está publicado: fluxlasers.es con `lang=en`).
    solo_respaldo: bool = False

    def descartar(self, motivo: str) -> None:
        self.descartes[motivo] = self.descartes.get(motivo, 0) + 1


@dataclass
class LecturaWeb:
    sitio: str
    dominio: str
    idiomas: list[str]
    grupos: list[Grupo] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    peticiones: int = 0
    #: La web no tiene tienda (pimpam): sus productos, si los hubo, se van.
    sin_tienda: bool = False


@dataclass
class _Idioma:
    idioma: str
    base: str
    #: `{"lang": "de"}` en WPML.
    params: dict[str, str]
    defecto: bool
    #: El código de WPML que de verdad responde (pimpam: `pt-pt`).
    codigo: str


def idiomas_de(sitio: Sitio) -> list[str]:
    """Los idiomas a leer, el de por defecto primero."""
    return list(dict.fromkeys([sitio.idioma_defecto, *(sitio.idiomas or ())]))


def modo_de(sitio: Sitio) -> str:
    if len(idiomas_de(sitio)) <= 1:
        return MODO_UNICO
    return MODO_WPML if sitio.plugin == PLUGIN_WPML else MODO_PREFIJO


def hosts_de(sitio: Sitio, host: str) -> set[str]:
    """Los hosts a los que se puede ir (y redirigir) al leer esta web: el que
    se lee y los dominios de la propia web, con y sin `www.`."""
    salida: set[str] = set()
    for h in (host, sitio.web, sitio.dominio, *sitio.dominios_extra):
        h = (h or "").strip().lower().rstrip(".")
        if not h:
            continue
        pelado = h[4:] if h.startswith("www.") else h
        salida.update({pelado, f"www.{pelado}"})
    return salida


def _texto_html(fragmento: Any) -> str:
    """Texto limpio de un trozo de HTML de la REST (WPBakery y CSS sueltos
    fuera)."""
    if isinstance(fragmento, dict):
        fragmento = fragmento.get("rendered")
    return limpiar_texto_wp(html_a_texto(str(fragmento or "")))


def _titulo(item: dict[str, Any], clave: str = "title") -> str:
    valor = item.get(clave)
    if isinstance(valor, dict):
        valor = valor.get("rendered")
    return normalizar_espacios(html_a_texto(str(valor or ""))).replace("\n", " ")[:500]


def _fecha(valor: Any) -> datetime | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def _entero(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _lleva_idioma(enlace: str, codigo: str) -> bool:
    """¿El enlace es del idioma pedido (`/de/…` o `?lang=de`)? En WPML, lo
    que no lo lleva es el respaldo del idioma por defecto."""
    partes = urlsplit(enlace or "")
    segmentos = [s for s in partes.path.split("/") if s]
    return (bool(segmentos) and segmentos[0].lower() == codigo.lower()) or (
        f"lang={codigo.lower()}" in partes.query.lower())


# --- precios ---------------------------------------------------------------------------------

#: Lo que dice la web junto al precio. Primero «sin IVA» («+IVA», «+ IVA»,
#: «excl.», «HT», «zzgl. MwSt», «excl. BTW»…), luego «con IVA».
_SIN_IVA_RE = re.compile(
    r"\+\s*(?:iva|vat|tva|btw|mwst|ust)\b|\bsin\s+iva\b|\bs/\s*iva\b|\bexcl|\bexkl|\bzzgl"
    r"|\bhors\s+tax|\bex\.?\s*vat\b|\bplus\s+vat\b|\bnetto\b|\bexcluding\b|\bno\s+incluido",
    re.IGNORECASE)
_HT_RE = re.compile(r"\bHT\b")        # «HT» en mayúsculas (hors taxes)
_CON_IVA_RE = re.compile(
    r"\biva\s+incl|\bincl\.?\s*(?:iva|vat|tva|btw|mwst|ust)\b|\binkl\b|\binkl\.|\bTTC\b"
    r"|\bcon\s+iva\b|\bincluding\s+vat\b|\bvat\s+incl|\btva\s+incl|\bmwst\.?\s+inkl"
    r"|\bincluido\b|\bincluida\b",
    re.IGNORECASE)
#: Del `price_html` sobra el precio tachado de una rebaja y el texto para
#: lectores de pantalla («El precio original era: …»); lo que ve el cliente va
#: con `aria-hidden`, que el extractor de texto quitaría.
_DEL_RE = re.compile(r"<del\b.*?</del>", re.IGNORECASE | re.DOTALL)
_LECTOR_PANTALLA_RE = re.compile(
    r"<span\b[^>]*screen-reader-text[^>]*>.*?</span>", re.IGNORECASE | re.DOTALL)
_ARIA_OCULTO_RE = re.compile(r"\saria-hidden=([\"']?)true\1", re.IGNORECASE)


def texto_precio(price_html: str) -> str:
    """El precio tal como lo ve el cliente en la web, en una línea."""
    limpio = _ARIA_OCULTO_RE.sub("", _LECTOR_PANTALLA_RE.sub(" ", _DEL_RE.sub(" ", price_html)))
    return normalizar_espacios(html_a_texto(limpio)).replace("\n", " ")


def iva_de(texto: str) -> str:
    """`sin_iva` / `con_iva` / `desconocido` según lo que pone la web junto al
    precio. Nunca se deduce del número."""
    if _SIN_IVA_RE.search(texto or "") or _HT_RE.search(texto or ""):
        return IVA_SIN
    if _CON_IVA_RE.search(texto or ""):
        return IVA_CON
    return IVA_DESCONOCIDO


def precio_de(producto: dict[str, Any]) -> dict[str, Any]:
    """El precio «como dice la web»: solo si es mayor que 0 (los «a consultar»
    están a 0 y no se guardan). Nunca es precio de oferta: ese sale de
    FACTUSOL."""
    vacio = {"precio_web": None, "precio_web_texto": None, "precio_iva": None, "moneda": None}
    precios = producto.get("prices") if isinstance(producto.get("prices"), dict) else {}
    entero = _entero(precios.get("price"))
    if not entero or entero <= 0:
        return vacio
    decimales = _entero(precios.get("currency_minor_unit"))
    decimales = 2 if decimales is None or not 0 <= decimales <= 4 else decimales
    try:
        valor = (Decimal(entero) / (Decimal(10) ** decimales)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return vacio
    texto = texto_precio(str(producto.get("price_html") or ""))
    if not texto:
        prefijo = str(precios.get("currency_prefix") or "")
        sufijo = str(precios.get("currency_suffix") or "")
        texto = f"{prefijo}{valor}{sufijo}".strip()
    return {"precio_web": valor, "precio_web_texto": texto[:80] or None,
            "precio_iva": iva_de(texto), "moneda": (str(precios.get("currency_code") or "")
                                                   .upper()[:3] or None)}


# --- categorías ------------------------------------------------------------------------------


def _ids(elementos: list[dict[str, Any]]) -> set[int]:
    return {i for i in (_entero(e.get("id")) for e in elementos) if i is not None}


def con_hijas(categorias: list[dict[str, Any]], semilla: set[int]) -> set[int]:
    """`semilla` y todas las categorías del árbol que cuelgan de una de ellas,
    a cualquier profundidad (por `parent`)."""
    por_id = {i: c for c in categorias if (i := _entero(c.get("id"))) is not None}
    salida = set(semilla)
    for cid in por_id:
        actual: int | None = cid
        vistos: set[int] = set()
        while actual and actual not in vistos:
            vistos.add(actual)
            if actual in semilla:
                salida.add(cid)
                break
            actual = _entero((por_id.get(actual) or {}).get("parent"))
    return salida


def categorias_fuera(categorias: list[dict[str, Any]], reglas: ReglasSitio) -> set[int]:
    """Los ids de las categorías de fuera DEL IDIOMA POR DEFECTO: las que
    EMPIEZAN por un prefijo de fuera y TODAS sus hijas (herencia por
    `parent`). Solo se llama con el árbol del idioma por defecto: las reglas
    están escritas en ese idioma y los otros idiomas heredan la decisión por id
    (ver el docstring del módulo)."""
    return con_hijas(categorias, {
        i for c in categorias
        if (i := _entero(c.get("id"))) is not None and reglas.categoria_fuera(c.get("name"))})


@dataclass
class DecisionIdioma:
    """Qué queda fuera en UN idioma, ya en ids de ese idioma (de la web).

    - `fuera`: categorías de fuera (con sus hijas).
    - `excluir` / `incluir`: excepciones por id de producto (las de las reglas
      y, en WPML, sus traducciones).
    - `conocidas`: solo en WPML fuera del idioma por defecto (None en lo
      demás): las categorías del idioma que son traducción de una del idioma
      por defecto, o cuelgan de una de fuera. Un producto sin ninguna de ellas
      queda fuera (`categoria_sin_original`): nadie ha decidido sobre él.
    - `nombres`: solo en el idioma por defecto (None en los demás, donde los
      nombres están traducidos y no deciden): las reglas, para sacar también
      el producto con una categoría PROPIA cuyo nombre empieza por una de
      fuera aunque falte del árbol (`count` desfasado, `MAX_PAGINAS`)."""

    fuera: set[int] = field(default_factory=set)
    excluir: set[int] = field(default_factory=set)
    incluir: set[int] = field(default_factory=set)
    conocidas: set[int] | None = None
    nombres: ReglasSitio | None = None

    @classmethod
    def de_reglas(cls, reglas: ReglasSitio, fuera: set[int] | None = None, *,
                  nombres: bool = False) -> DecisionIdioma:
        """La del idioma por defecto (y la de todos con WPGlobus o un solo
        idioma): las excepciones tal cual están escritas. `nombres=True` solo
        en el idioma por defecto (la red de seguridad por nombre)."""
        return cls(fuera=set(fuera or ()), excluir=set(reglas.excluir),
                   incluir=set(reglas.incluir), nombres=reglas if nombres else None)


def producto_dentro(producto: dict[str, Any], decision: DecisionIdioma) -> tuple[bool, str]:
    """¿Entra el producto? `(sí/no, motivo)`, por ids (los nombres de las
    categorías ya decidieron en el idioma por defecto, `categorias_fuera`).
    Las excepciones por id, encima de todo (si está en las dos, gana excluir:
    no ofrecer un repuesto como máquina); sin categoría, fuera; CUALQUIER
    categoría de fuera manda (un repuesto colgado de la madre «Accessoires et
    pièces de rechange» y de su hija «Pièces de rechange A5000» queda fuera,
    y una Open Box en «Cortadoras Láser» y «Reacondicionadas» también); en
    el idioma por defecto (`decision.nombres`), también una categoría propia
    cuyo nombre empieza por una de fuera; en WPML, un producto cuyas
    categorías son todas «sin original», fuera."""
    pid = _entero(producto.get("id")) or 0
    if pid in decision.excluir:
        return False, "excluido"
    if pid in decision.incluir:
        return True, "incluido"
    categorias = [c for c in (producto.get("categories") or []) if isinstance(c, dict)]
    ids = _ids(categorias)
    if not ids:
        return False, "sin_categoria"
    if ids & decision.fuera:
        return False, "categoria"
    if decision.nombres is not None and any(
            decision.nombres.categoria_fuera(c.get("name")) for c in categorias):
        # Red de seguridad: una categoría que falta del árbol (`count`
        # desfasado, árbol cortado) no deja entrar a un repuesto.
        return False, "categoria"
    if decision.conocidas is not None and not ids & decision.conocidas:
        return False, "categoria_sin_original"
    return True, ""


# --- la lectura de una web -------------------------------------------------------------------


class _LectorWeb:
    def __init__(self, sitio: Sitio, reglas: ReglasSitio, host: str, http: Http) -> None:
        self.sitio = sitio
        self.reglas = reglas
        self.host = host
        self.http = http
        self.modo = modo_de(sitio)
        self.hosts = hosts_de(sitio, host)
        self.lectura = LecturaWeb(sitio=sitio.clave, dominio=host, idiomas=idiomas_de(sitio))
        #: Por tipo: lo del idioma por defecto (`{id: {...}}`), para el respaldo.
        self.defecto: dict[str, dict[int, dict[str, Any]]] = {}
        #: El árbol de categorías del idioma por defecto, leído una vez por web:
        #: `(ids de fuera, ids de todas)`. O el fallo al leerlo.
        self._arbol_defecto: tuple[set[int], set[int]] | None = None
        self._arbol_defecto_error: LecturaError | None = None

    # -- idiomas --

    def _idioma(self, idioma: str, codigo: str | None = None) -> _Idioma:
        defecto = idioma == self.sitio.idioma_defecto
        base = f"https://{self.host}/"
        codigo = codigo or idioma
        if self.modo == MODO_WPML:
            return _Idioma(idioma, base, {"lang": codigo}, defecto, codigo)
        if self.modo == MODO_PREFIJO and (not defecto or self.sitio.prefijo_idioma_defecto):
            return _Idioma(idioma, f"{base}{idioma}/", {}, defecto, idioma)
        return _Idioma(idioma, base, {}, defecto, idioma)

    # -- páginas y entradas --

    def _campos(self) -> str:
        if self.modo == MODO_PREFIJO:
            return f"{CAMPOS_CONTENIDO},{CAMPO_TRADUCCION}"
        return CAMPOS_CONTENIDO

    def _lista_contenido(self, tipo: str, idi: _Idioma) -> list[dict[str, Any]]:
        params = {**idi.params, "_fields": self._campos()}
        return paginar(self.http, idi.base, _RUTAS[tipo], params, hosts=self.hosts)

    def _fallo(self, grupo: Grupo, exc: LecturaError) -> None:
        """El grupo queda en error (no se escribe ni se marca nada ido). Un 5xx
        se avisa aparte: es la web la que falla, no la lectura."""
        grupo.error = str(exc)[:300]
        if isinstance(exc, ErrorServidor):
            self.lectura.avisos.append(
                f"{grupo.tipo} {grupo.idioma}: la web da error de servidor ({exc}); "
                "se queda lo anterior")

    def _es_respaldo(self, tipo: str, idi: _Idioma, item: dict[str, Any], texto: str | None,
                     ) -> bool:
        if idi.defecto:
            return False
        wp_id = _entero(item.get("id")) or 0
        if self.modo == MODO_WPML:
            return (wp_id in self.defecto.get(tipo, {})
                    or not _lleva_idioma(str(item.get("link") or item.get("permalink") or ""),
                                         idi.codigo))
        if self.modo == MODO_PREFIJO:
            original = self.defecto.get(tipo, {}).get(wp_id)
            traduccion = (original or {}).get("traduccion")
            if isinstance(traduccion, dict) and isinstance(traduccion.get("languages"), dict):
                datos = traduccion["languages"].get(idi.idioma)
                return not (isinstance(datos, dict) and datos.get("content"))
            if original is not None and texto is not None:
                return texto == original.get("texto")
        return False

    def _resolver_codigo_wpml(self, tipo: str, idi: _Idioma,
                              ) -> tuple[_Idioma, list[dict[str, Any]]]:
        """El código de WPML que de verdad sirve ese idioma: si el nuestro solo
        da respaldo (todo repite ids del idioma por defecto), se prueban las
        alternativas (`pt` → `pt-pt`)."""
        elementos = self._lista_contenido(tipo, idi)
        if self.modo != MODO_WPML or idi.defecto:
            return idi, elementos
        if any(not self._es_respaldo(tipo, idi, e, None) for e in elementos):
            return idi, elementos
        for alternativa in ALTERNATIVAS_WPML.get(idi.idioma, ()):
            otro = self._idioma(idi.idioma, alternativa)
            try:
                otros = self._lista_contenido(tipo, otro)
            except LecturaError:
                continue
            if any(not self._es_respaldo(tipo, otro, e, None) for e in otros):
                return otro, otros
        return idi, elementos

    def leer_contenido(self, tipo: str, idioma: str, codigo: str | None = None,
                       ) -> tuple[Grupo, str]:
        """Las páginas (o entradas) de un idioma. Devuelve el grupo y el código
        de idioma que respondió (para los productos en WPML)."""
        idi = self._idioma(idioma, codigo)
        grupo = Grupo(tipo=tipo, idioma=idioma)
        try:
            if codigo is None:
                idi, elementos = self._resolver_codigo_wpml(tipo, idi)
            else:
                elementos = self._lista_contenido(tipo, idi)
        except LecturaError as exc:
            self._fallo(grupo, exc)
            return grupo, idi.codigo
        grupo.leidos = len(elementos)
        grupo.vacio = not elementos
        grupo.solo_respaldo = (self.modo == MODO_WPML and not idi.defecto and bool(elementos)
                               and all(self._es_respaldo(tipo, idi, e, None) for e in elementos))
        de_este_idioma: dict[int, dict[str, Any]] = {}
        huellas: set[str] = set()
        for item in sorted(elementos, key=lambda e: _entero(e.get("id")) or 0):
            wp_id = _entero(item.get("id"))
            if wp_id is None:
                continue
            slug = str(item.get("slug") or "")
            enlace = str(item.get("link") or "")
            traduccion = item.get(CAMPO_TRADUCCION)
            if self.reglas.pagina_fuera(slug, wp_id):
                grupo.descartar("regla")
                continue
            if self._es_respaldo(tipo, idi, item, None) and self.modo == MODO_WPML:
                grupo.descartar("respaldo")
                continue
            texto = _texto_html(item.get("content"))
            if idi.defecto:
                de_este_idioma[wp_id] = {"texto": texto, "traduccion": traduccion}
            if self._es_respaldo(tipo, idi, item, texto):
                grupo.descartar("respaldo")
                continue
            if len(texto) < MIN_TEXTO_PAGINA:
                grupo.descartar("corta")
                continue
            if _RELLENO_RE.search(texto):
                grupo.descartar("relleno")
                continue
            # Dos páginas con el mismo texto en el mismo idioma (landings
            # duplicadas): se queda la de id más bajo.
            if texto in huellas:
                grupo.descartar("duplicada")
                continue
            huellas.add(texto)
            grupo.filas.append({
                "sitio": self.sitio.clave, "dominio": self.host, "url": enlace,
                "tipo": tipo, "wp_id": wp_id, "idioma": idioma, "slug": slug or None,
                "titulo": _titulo(item) or slug, "texto": texto,
                "extracto": _texto_html(item.get("excerpt"))[:MAX_EXTRACTO] or None,
                "imagen": None, "galeria": [], "categorias": [],
                "precio_web": None, "precio_web_texto": None, "precio_iva": None,
                "moneda": None, "modificado_web": _fecha(item.get("modified_gmt")),
            })
        if idi.defecto:
            # Lo descartado por regla o por corto también cuenta como «existe en
            # el idioma por defecto»: en WPML su id repetido en otro idioma es
            # respaldo.
            todos = {(_entero(e.get("id")) or 0): de_este_idioma.get(
                _entero(e.get("id")) or 0, {"texto": None,
                                            "traduccion": e.get(CAMPO_TRADUCCION)})
                for e in elementos}
            self.defecto[tipo] = todos
        return grupo, idi.codigo

    # -- productos --

    def _rest_responde(self) -> bool:
        """¿La REST de la web respondió bien en esta misma lectura (páginas o
        entradas del idioma por defecto sin error)? Solo entonces un 404 de la
        Store API quiere decir «web sin tienda»."""
        return any(g.error is None and g.idioma == self.sitio.idioma_defecto
                   and g.tipo in _RUTAS for g in self.lectura.grupos)

    def _categorias(self, idi: _Idioma) -> list[dict[str, Any]]:
        return paginar(self.http, idi.base, _RUTA_CATEGORIAS,
                       {**idi.params, **TODAS_LAS_CATEGORIAS, "_fields": CAMPOS_CATEGORIA},
                       hosts=self.hosts)

    def _traducir(self, idi: _Idioma, ruta: str, ids: set[int]) -> set[int]:
        """WPML: los ids (de categorías o de productos) que corresponden a `ids`
        en el idioma `idi`. WPML «ajusta los ids» de `include=`: pedido en
        inglés, `categories&include=39` devuelve la 763 (la traducción de la
        39) y `products&include=965` el 2839. Lo que no tiene traducción no
        vuelve (o vuelve el original, si la web lo enseña como respaldo). Las
        categorías, también las vacías (`TODAS_LAS_CATEGORIAS`)."""
        extra = TODAS_LAS_CATEGORIAS if ruta == _RUTA_CATEGORIAS else {}
        salida: set[int] = set()
        orden = sorted(ids)
        for i in range(0, len(orden), POR_PAGINA):
            salida |= _ids(paginar(self.http, idi.base, ruta, {
                **idi.params, **extra,
                "include": ",".join(str(x) for x in orden[i:i + POR_PAGINA]),
                "_fields": "id"}, hosts=self.hosts))
        return salida

    def _del_defecto(self) -> tuple[set[int], set[int]]:
        """`(fuera, todas)` del idioma por defecto: los ids de sus categorías de
        fuera (regla «empieza por» sobre el nombre en ese idioma, y las hijas) y
        los de todas. Se lee una vez por web; si falla, el mismo error vale
        para todos los idiomas (sin él no se puede decidir)."""
        if self._arbol_defecto_error is not None:
            raise self._arbol_defecto_error
        if self._arbol_defecto is None:
            try:
                arbol = self._categorias(self._idioma(self.sitio.idioma_defecto))
            except LecturaError as exc:
                self._arbol_defecto_error = exc
                raise
            self._arbol_defecto = (categorias_fuera(arbol, self.reglas), _ids(arbol))
        return self._arbol_defecto

    def _decision(self, idi: _Idioma) -> DecisionIdioma:
        """Qué queda fuera en este idioma (ver el docstring del módulo): la
        decisión del idioma por defecto, llevada a este idioma por id."""
        defecto = self.sitio.idioma_defecto
        try:
            fuera_d, todas_d = self._del_defecto()
        except LecturaError as exc:
            if idi.defecto:
                raise
            raise LecturaError(f"no se pudieron leer las categorías del idioma por defecto "
                               f"({defecto}), que deciden qué entra: {exc}") from exc
        # Los nombres de las categorías del producto, solo en el idioma por
        # defecto: en los demás están traducidos y no deciden.
        decision = DecisionIdioma.de_reglas(self.reglas, fuera_d, nombres=idi.defecto)
        if self.modo != MODO_WPML or idi.defecto:
            # WPGlobus y un solo idioma: los mismos ids en todos los idiomas.
            return decision
        arbol = self._categorias(idi)
        ids_idioma = _ids(arbol)
        trad_fuera = self._traducir(idi, _RUTA_CATEGORIAS, fuera_d) if fuera_d else set()
        trad_todas = self._traducir(idi, _RUTA_CATEGORIAS, todas_d) if todas_d else set()
        # El enlace se mide solo con los ids PROPIOS del idioma: una categoría
        # sin traducir que WPML sirve con su id del idioma por defecto no
        # prueba que WPML ajuste los ids. Un idioma cuyas categorías son todas
        # del idioma por defecto sí pasa (decide el id, el mismo).
        propios = ids_idioma - todas_d
        if propios and not propios & trad_todas:
            # Cerrado ante la duda: sin el enlace entre idiomas no se sabe qué
            # es un repuesto, y lo de antes se queda como estaba.
            raise LecturaError(
                f"ninguna categoría de «{idi.idioma}» resulta ser traducción de una del idioma "
                f"por defecto ({defecto}): WPML no ha traducido los ids y no se puede decidir "
                "qué productos entran; se queda lo anterior")
        # Los ids de término son únicos en toda la instalación: los del idioma
        # por defecto que aparezcan aquí son esas mismas categorías.
        decision.fuera = con_hijas(arbol, trad_fuera | fuera_d)
        decision.conocidas = trad_todas | todas_d | decision.fuera
        sin_original = [c for c in arbol if _entero(c.get("id")) not in decision.conocidas]
        if sin_original:
            nombres = ", ".join(
                f"«{normalizar_espacios(html_mod.unescape(str(c.get('name') or '')))}» "
                f"({c.get('id')})" for c in sin_original[:10])
            self.lectura.avisos.append(
                f"producto {idi.idioma}: {len(sin_original)} categoría(s) sin original en "
                f"{defecto} (solo existen en este idioma: {nombres}); sus productos no entran "
                "si no tienen otra categoría")
        if self.reglas.excluir:
            decision.excluir |= self._traducir(idi, _RUTA_PRODUCTOS, self.reglas.excluir)
        return decision

    def _productos(self, idi: _Idioma, decision: DecisionIdioma) -> list[dict[str, Any]]:
        params: dict[str, Any] = dict(idi.params)
        if decision.fuera:
            # Filtrado ya en el servidor: boprint pasa de 330 productos a 36.
            # En WPML el servidor también ajusta estos ids al idioma pedido.
            params["category"] = ",".join(str(i) for i in sorted(decision.fuera))
            params["category_operator"] = "not_in"
        productos = paginar(self.http, idi.base, _RUTA_PRODUCTOS, params, hosts=self.hosts)
        if self.reglas.incluir:
            # Las excepciones «incluir» pueden estar en una categoría de fuera:
            # el filtro del servidor las habría quitado. En WPML lo que vuelve
            # son sus traducciones (ids de este idioma): también son «incluir».
            ya = {p.get("id") for p in productos}
            extra = paginar(self.http, idi.base, _RUTA_PRODUCTOS, {
                **idi.params, "include": ",".join(str(i) for i in sorted(self.reglas.incluir))},
                hosts=self.hosts)
            if self.modo == MODO_WPML and not idi.defecto:
                decision.incluir |= _ids(extra)
            productos += [p for p in extra if p.get("id") not in ya]
        return productos

    def _fechas_productos(self, idi: _Idioma, ids: list[int]) -> dict[int, datetime | None]:
        """`modified_gmt` de `/wp/v2/product` (la Store API no lo da). Si falla,
        sin fecha: no es motivo para dejar la web sin indexar."""
        salida: dict[int, datetime | None] = {}
        for i in range(0, len(ids), POR_PAGINA):
            trozo = ids[i:i + POR_PAGINA]
            try:
                elementos = paginar(self.http, idi.base, _RUTA_PRODUCTO_WP, {
                    **idi.params, "include": ",".join(str(x) for x in trozo),
                    "_fields": "id,modified_gmt"}, hosts=self.hosts)
            except LecturaError as exc:
                self.lectura.avisos.append(f"producto {idi.idioma}: sin fechas ({exc})")
                return {}
            for e in elementos:
                pid = _entero(e.get("id"))
                if pid is not None:
                    salida[pid] = _fecha(e.get("modified_gmt"))
        return salida

    def leer_productos(self, idioma: str, codigo: str | None) -> Grupo:
        """Los productos de un idioma, con la decisión del idioma por defecto
        llevada a este por id (`_decision`)."""
        idi = self._idioma(idioma, codigo)
        grupo = Grupo(tipo=WEB_PRODUCTO, idioma=idioma)
        try:
            decision = self._decision(idi)
            productos = self._productos(idi, decision)
        except SinRuta as exc:
            if idi.defecto and self._rest_responde():
                # Sin WooCommerce (pimpam): no hay productos; lo que hubiera se va.
                self.lectura.sin_tienda = True
                return grupo
            # La tienda existe pero ese idioma no responde, o la web da 404 a
            # todo (REST rota, vhost mal apuntado): se queda lo anterior.
            self._fallo(grupo, exc)
            return grupo
        except LecturaError as exc:
            self._fallo(grupo, exc)
            return grupo
        grupo.leidos = len(productos)
        grupo.vacio = not productos
        originales = self.defecto.setdefault(WEB_PRODUCTO, {})
        for producto in sorted(productos, key=lambda p: _entero(p.get("id")) or 0):
            pid = _entero(producto.get("id"))
            if pid is None or producto.get("type") == "variation":
                continue
            if idi.defecto:
                originales[pid] = {}
            dentro, motivo = producto_dentro(producto, decision)
            if not dentro:
                grupo.descartar(motivo)
                continue
            fila = self._fila_producto(producto, idioma)
            if idi.defecto:
                originales[pid] = {"texto": fila["texto"], "titulo": fila["titulo"]}
            elif self._producto_respaldo(idi, producto, fila):
                grupo.descartar("respaldo")
                continue
            grupo.filas.append(fila)
        if grupo.filas:
            fechas = self._fechas_productos(idi, [f["wp_id"] for f in grupo.filas])
            for fila in grupo.filas:
                fila["modificado_web"] = fechas.get(fila["wp_id"])
        return grupo

    def _producto_respaldo(self, idi: _Idioma, producto: dict[str, Any],
                           fila: dict[str, Any]) -> bool:
        pid = _entero(producto.get("id")) or 0
        if self.modo == MODO_WPML:
            return pid in self.defecto.get(WEB_PRODUCTO, {}) or not _lleva_idioma(
                str(producto.get("permalink") or ""), idi.codigo)
        if self.modo == MODO_PREFIJO:
            # La Store API no trae `translation`: sin traducir, el texto (y el
            # nombre, si no hay texto) es idéntico al del idioma por defecto.
            original = self.defecto.get(WEB_PRODUCTO, {}).get(pid)
            if original and original.get("texto") is not None:
                return fila["texto"] == original["texto"] and bool(
                    fila["texto"] or fila["titulo"] == original.get("titulo"))
        return False

    def _fila_producto(self, producto: dict[str, Any], idioma: str) -> dict[str, Any]:
        corta = _texto_html(producto.get("short_description"))
        larga = _texto_html(producto.get("description"))
        # La descripción corta suele repetirse dentro de la larga.
        texto = larga if corta and corta in larga else "\n\n".join(t for t in (corta, larga) if t)
        imagenes = [str(i.get("src")) for i in (producto.get("images") or [])
                    if isinstance(i, dict) and i.get("src")]
        imagenes = [u for u in imagenes if u.startswith("https://") and len(u) <= 700]
        categorias = [normalizar_espacios(html_mod.unescape(str(c.get("name") or "")))
                      for c in (producto.get("categories") or []) if isinstance(c, dict)]
        return {
            "sitio": self.sitio.clave, "dominio": self.host,
            "url": str(producto.get("permalink") or ""), "tipo": WEB_PRODUCTO,
            "wp_id": _entero(producto.get("id")), "idioma": idioma,
            "slug": str(producto.get("slug") or "") or None,
            "titulo": _titulo(producto, "name"), "texto": texto,
            "extracto": corta[:MAX_EXTRACTO] or None,
            "imagen": imagenes[0] if imagenes else None, "galeria": imagenes[1:],
            "categorias": [c for c in categorias if c],
            **precio_de(producto), "modificado_web": None,
        }

    # -- todo --

    def leer(self) -> LecturaWeb:
        codigos: dict[str, str] = {}
        #: Idiomas de WPML que la web no publica (sus páginas son todas el
        #: respaldo del idioma por defecto): ni entradas ni productos, que
        #: serían respaldo también. Ahorra peticiones (fluxlasers.es: 12).
        sin_publicar: set[str] = set()

        #: WPGlobus: sin la lectura del idioma por defecto no se sabe qué está
        #: traducido y qué es respaldo (mismo id, URL del otro idioma): los
        #: demás idiomas de ese tipo no se tocan (se queda lo anterior).
        defecto_fallido: set[str] = set()
        defecto = self.sitio.idioma_defecto

        def _saltado(tipo: str, idioma: str) -> Grupo:
            grupo = Grupo(tipo=tipo, idioma=idioma)
            grupo.descartar("idioma_sin_publicar")
            return grupo

        def _sin_defecto(tipo: str, idioma: str) -> Grupo:
            return Grupo(tipo=tipo, idioma=idioma,
                         error=f"no se pudo leer el idioma por defecto ({defecto}): sin él no "
                               "se distingue la traducción del respaldo")

        for tipo in (WEB_PAGINA, WEB_ENTRADA):
            for idioma in self.lectura.idiomas:
                if idioma in sin_publicar:
                    self.lectura.grupos.append(_saltado(tipo, idioma))
                    continue
                if tipo in defecto_fallido and idioma != defecto:
                    self.lectura.grupos.append(_sin_defecto(tipo, idioma))
                    continue
                grupo, codigo = self.leer_contenido(tipo, idioma, codigos.get(idioma))
                if grupo.error is not None and idioma == defecto and self.modo == MODO_PREFIJO:
                    defecto_fallido.add(tipo)
                if grupo.error is None:
                    codigos.setdefault(idioma, codigo)
                    if tipo == WEB_PAGINA and grupo.solo_respaldo:
                        sin_publicar.add(idioma)
                        self.lectura.avisos.append(
                            f"{idioma}: la web no lo publica (todo es respaldo del "
                            f"{self.sitio.idioma_defecto}); no se indexa")
                self.lectura.grupos.append(grupo)
        for idioma in self.lectura.idiomas:
            if idioma in sin_publicar:
                self.lectura.grupos.append(_saltado(WEB_PRODUCTO, idioma))
                continue
            if WEB_PRODUCTO in defecto_fallido and idioma != defecto:
                self.lectura.grupos.append(_sin_defecto(WEB_PRODUCTO, idioma))
                continue
            grupo = self.leer_productos(idioma, codigos.get(idioma))
            if grupo.error is not None and idioma == defecto and self.modo == MODO_PREFIJO:
                defecto_fallido.add(WEB_PRODUCTO)
            if self.lectura.sin_tienda:
                # La web no tiene tienda (lo dice el idioma por defecto, el
                # primero): un grupo vacío por idioma para que lo que hubiera se
                # marque ido.
                self.lectura.grupos.extend(Grupo(tipo=WEB_PRODUCTO, idioma=i)
                                           for i in self.lectura.idiomas)
                break
            self.lectura.grupos.append(grupo)
        self.lectura.peticiones = len(self.http.urls)
        return self.lectura


def leer_web(sitio: Sitio, reglas: ReglasSitio, *, host: str | None = None,
             http: Http | None = None) -> LecturaWeb:
    """Lee una web entera (páginas, entradas y productos, idioma a idioma).
    No toca la base: quien llama escribe (`indice.escribir_lectura`)."""
    propio = http is None
    cliente = http or Http()
    try:
        return _LectorWeb(sitio, reglas, (host or sitio.web).strip().lower(), cliente).leer()
    finally:
        if propio:
            cliente.cerrar()


def resumen_lectura(lectura: LecturaWeb) -> dict[str, Any]:
    """Lo leído en cifras (para la sonda y los registros)."""
    return {
        "sitio": lectura.sitio, "dominio": lectura.dominio, "peticiones": lectura.peticiones,
        "sin_tienda": lectura.sin_tienda, "avisos": lectura.avisos[:20],
        "grupos": [{"tipo": g.tipo, "idioma": g.idioma, "filas": len(g.filas),
                    "leidos": g.leidos, "error": g.error, "descartes": g.descartes}
                   for g in lectura.grupos],
    }
