"""Lo común de los tests del índice de las webs: un WordPress simulado (por
`httpx.MockTransport`, nada sale a la red) que se porta como las webs medidas
el 27 y el 28/10/2026 — `?rest_route=`, WPGlobus por prefijo con su respaldo
(mismos ids en todos los idiomas, nombres de categoría traducidos), WPML con
`lang=`, ids propios por idioma (también los de las categorías), respaldo
silencioso y los ids «ajustados» al idioma pedido en `include=` y en
`category=…&not_in`, la Store API con sus filtros y `_fields`, y pimpam sin
tienda — y los datos de partida de cada web (FLUX, con su árbol y sus
productos reales). Este fichero no tiene tests: lo importan los demás.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, Sitio
from app.services.webs import lector

PUBLICA = "93.184.216.34"


def resolver_publico(host, port, **_kw):  # noqa: ANN001, ANN201
    return [(2, 1, 6, "", (PUBLICA, port))]


def sitio(clave: str) -> Sitio:
    return next(s for s in SITIOS_DE_PARTIDA if s.clave == clave)


def registro_con(*claves: str) -> Registro:
    return Registro(sitios=tuple(sitio(c) for c in claves))


def largo(frase: str, veces: int = 4) -> str:
    """Un párrafo que pasa del mínimo de texto de una página."""
    return " ".join([frase] * veces)


def pagina(wp_id: int, slug: str, titulo: str, texto: str, *, host: str, prefijo: str = "",
           traduccion: dict[str, Any] | None = None, link: str | None = None,
           html: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": wp_id, "slug": slug, "link": link or f"https://{host}/{prefijo}{slug}/",
        "modified_gmt": "2026-09-01T10:00:00",
        "title": {"rendered": titulo},
        "content": {"rendered": html if html is not None else f"<p>{texto}</p>"},
        "excerpt": {"rendered": f"<p>{texto[:80]}</p>"},
    }
    if traduccion is not None:
        item["translation"] = traduccion
    return item


def traduccion_wpglobus(defecto: str, con_contenido: list[str], todos: list[str],
                        ) -> dict[str, Any]:
    return {"provider": "WPGlobus", "language": defecto, "enabled_languages": todos,
            "languages": {i: {"title": i in con_contenido, "content": i in con_contenido,
                              "excerpt": False} for i in todos}}


def categoria(cid: int, nombre: str, padre: int = 0) -> dict[str, Any]:
    return {"id": cid, "name": nombre, "slug": nombre.lower().replace(" ", "-"),
            "parent": padre, "count": 1}


def producto(pid: int, nombre: str, cats: list[tuple[int, str]], *, host: str,
             prefijo: str = "", texto: str = "", precio: str = "0", price_html: str = "",
             imagenes: int = 2, permalink: str | None = None, minor: int = 2,
             slug: str | None = None) -> dict[str, Any]:
    slug = slug or nombre.lower().replace(" ", "-")
    return {
        "id": pid, "name": nombre, "slug": slug, "type": "simple", "sku": "",
        "permalink": permalink or f"https://{host}/{prefijo}producto/{slug}/",
        "short_description": "",
        "description": f"<p>{texto or largo(nombre + ' para el taller.')}</p>",
        "prices": {"price": precio, "regular_price": precio, "sale_price": precio,
                   "currency_code": "EUR", "currency_minor_unit": minor,
                   "currency_prefix": "", "currency_suffix": "€"},
        "price_html": price_html,
        "categories": [{"id": c, "name": n, "slug": n.lower()} for c, n in cats],
        "images": [{"id": i, "src": f"https://{host}/wp-content/uploads/{slug}-{i}.jpg",
                    "thumbnail": "", "alt": ""} for i in range(imagenes)],
    }


def _ids_de(valor: str) -> set[int]:
    return {int(x) for x in valor.split(",") if x.strip()}


@dataclass
class WebFalsa:
    """Un WordPress simulado. Los datos van por idioma (`paginas["de"]`…): lo
    que el test pone es lo que la web «sirve» en ese idioma, respaldo incluido
    (WPGlobus: la lista del idioma trae el texto del de por defecto donde no hay
    traducción; WPML: un idioma o código desconocido sirve el de por defecto)."""

    host: str
    modo: str                      # "unico" | "prefijo" | "wpml"
    defecto: str = "es"
    paginas: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    entradas: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    categorias: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    productos: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    sin_tienda: bool = False
    #: La REST da 500 si se piden las páginas con su contenido (mbolasers
    #: hasta el 28/10: el límite de memoria de WordPress).
    contenido_500: bool = False
    #: Idiomas que dan 500 en todo (una web a medias).
    caidos: set[str] = field(default_factory=set)
    #: WPML: código pedido → idioma de los datos (`pt-pt` → `pt`). Un código
    #: que no está aquí ni en los datos vuelve al de por defecto.
    codigos: dict[str, str] = field(default_factory=dict)
    #: WPML: id de una categoría (o de un producto) en cualquier idioma → el
    #: id de su original en el idioma por defecto (WPML no lo da por la REST;
    #: aquí sirve para «ajustar los ids» como hace WPML). Los del idioma por
    #: defecto no hace falta ponerlos.
    originales_cat: dict[int, int] = field(default_factory=dict)
    originales_prod: dict[int, int] = field(default_factory=dict)
    #: WPML ajusta los ids de `include=` y de `category=` al idioma pedido
    #: (`lang=en&include=39` → la 763). False: una WPML que no lo hace.
    ajusta_ids: bool = True
    #: La Store API no hace caso de `category_operator=not_in` (para ver que
    #: el lector lo revisa otra vez, por ids).
    ignora_not_in: bool = False
    #: Rutas que dan 500 (p. ej. "/wp/v2/product").
    rutas_500: set[str] = field(default_factory=set)
    #: La web da 404 a todo (REST rota por un plugin, vhost mal apuntado).
    rota: bool = False
    peticiones: list[str] = field(default_factory=list)

    def _idioma(self, request: httpx.Request, qs: dict[str, list[str]]) -> str:
        if self.modo == "prefijo":
            primero = request.url.path.strip("/").split("/")[0]
            return primero if primero and primero != self.defecto and len(primero) <= 5 \
                else self.defecto
        if self.modo == "wpml":
            pedido = qs.get("lang", [self.defecto])[0]
            idioma = self.codigos.get(pedido)
            if idioma is None and pedido not in self.codigos.values():
                idioma = pedido     # el código es el propio idioma (sin alias)
            conocidos = set(self.paginas) | set(self.productos) | set(self.entradas)
            # Lo desconocido vuelve EN SILENCIO al idioma por defecto (WPML).
            return idioma if idioma in conocidos else self.defecto
        return self.defecto

    def _ajustar(self, ids: set[int], originales: dict[int, int],
                 del_idioma: list[dict[str, Any]]) -> set[int]:
        """WPML: cada id pedido (de cualquier idioma) pasa a ser el de su
        traducción en el idioma de la petición; lo que no tiene traducción
        no está."""
        if self.modo != "wpml" or not self.ajusta_ids:
            return ids
        aqui = {originales.get(d["id"], d["id"]): d["id"] for d in del_idioma}
        return {aqui[o] for i in ids if (o := originales.get(i, i)) in aqui}

    def responder(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(str(request.url))
        if self.rota:
            return httpx.Response(404, text="<html><body>404</body></html>")
        qs = parse_qs(request.url.query.decode())
        ruta = qs.get("rest_route", [None])[0]
        if ruta is None:
            # El HTML público no se lee nunca: si alguien lo pide, 404.
            return httpx.Response(404, text="<html><body>404</body></html>")
        idioma = self._idioma(request, qs)
        if idioma in self.caidos or ruta in self.rutas_500:
            return httpx.Response(500, text="<html>Ha habido un error crítico</html>")
        campos = [c for c in (qs.get("_fields", [""])[0]).split(",") if c]
        if ruta in ("/wp/v2/pages", "/wp/v2/posts"):
            if self.contenido_500 and ruta == "/wp/v2/pages" and "content" in campos:
                return httpx.Response(500, text="<html>Ha habido un error crítico</html>")
            datos = (self.paginas if ruta == "/wp/v2/pages" else self.entradas).get(idioma, [])
            return self._lista(datos, qs, campos)
        if ruta.startswith("/wc/") and self.sin_tienda:
            return httpx.Response(404, json={"code": "rest_no_route", "message": "No route"})
        arbol = self.categorias.get(idioma, self.categorias.get(self.defecto, []))
        if ruta == "/wc/store/v1/products/categories":
            if "include" not in qs:
                # Sin paginar: la Store API la da entera (en cada página).
                return httpx.Response(200, json=self._campos(arbol, campos))
            ids = self._ajustar(_ids_de(qs["include"][0]), self.originales_cat, arbol)
            return self._lista([c for c in arbol if c["id"] in ids], qs, campos)
        if ruta == "/wc/store/v1/products":
            datos = list(self.productos.get(idioma, []))
            if "include" in qs:
                ids = self._ajustar(_ids_de(qs["include"][0]), self.originales_prod, datos)
                datos = [p for p in datos if p["id"] in ids]
            if ("category" in qs and qs.get("category_operator", [""])[0] == "not_in"
                    and not self.ignora_not_in):
                fuera = self._ajustar(_ids_de(qs["category"][0]), self.originales_cat, arbol)
                datos = [p for p in datos if not {c["id"] for c in p["categories"]} & fuera]
            return self._lista(datos, qs, campos)
        if ruta == "/wp/v2/product":
            ids = _ids_de(qs.get("include", [""])[0])
            datos = [{"id": p["id"], "modified_gmt": "2026-08-15T08:30:00"}
                     for p in self.productos.get(idioma, []) if p["id"] in ids]
            return self._lista(datos, qs, [])
        return httpx.Response(404, json={"code": "rest_no_route", "message": "No route"})

    @staticmethod
    def _campos(datos: list[dict[str, Any]], campos: list[str]) -> list[dict[str, Any]]:
        if not campos:
            return datos
        return [{k: v for k, v in d.items() if k in campos} for d in datos]

    def _lista(self, datos: list[dict[str, Any]], qs: dict[str, list[str]],
               campos: list[str]) -> httpx.Response:
        por_pagina = int(qs.get("per_page", ["10"])[0])
        numero = int(qs.get("page", ["1"])[0])
        total_paginas = max(1, -(-len(datos) // por_pagina))
        if numero > total_paginas:
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        trozo = self._campos(datos[(numero - 1) * por_pagina: numero * por_pagina], campos)
        return httpx.Response(200, content=json.dumps(trozo).encode(), headers={
            "content-type": "application/json; charset=UTF-8",
            "x-wp-total": str(len(datos)), "x-wp-totalpages": str(total_paginas)})


@dataclass
class Internet:
    webs: dict[str, WebFalsa] = field(default_factory=dict)

    def poner(self, *webs: WebFalsa) -> Internet:
        for web in webs:
            self.webs[web.host] = web
        return self

    def responder(self, request: httpx.Request) -> httpx.Response:
        web = self.webs.get(request.url.host)
        if web is None:
            raise httpx.ConnectError(f"{request.url.host} no responde", request=request)
        return web.responder(request)

    def http(self) -> lector.Http:
        return lector.Http(cliente=httpx.Client(transport=httpx.MockTransport(self.responder)),
                           resolver=resolver_publico, pausa=0)

    def fabrica(self) -> Callable[[], lector.Http]:
        return self.http


# --- las webs de partida (datos realistas, recortados) -----------------------------------------

FLUX_HOST = "fluxlasers.eu"
#: El árbol REAL de categorías de FLUX en español, el idioma por defecto
#: (Store API de fluxlasers.es, espejo de .eu, 27/10/2026): (id, nombre, madre).
FLUX_ARBOL_ES: tuple[tuple[int, str, int], ...] = (
    (20, "Accesorios", 0), (27, "Accesorios Beambox", 20), (26, "Accesorios Beamo", 20),
    (223, "Accesorios Hexa", 20), (229, "Complementos Ador", 0), (22, "Consumibles", 0),
    (19, "Cortadoras Láser", 0), (228, "Flux Ador", 0), (245, "Láser de fibra", 0),
    (227, "Reacondicionadas", 19), (21, "Repuestos", 0), (246, "Repuestos Ador", 21),
    (24, "Repuestos Beambox", 21), (257, "Repuestos Beambox II", 21),
    (23, "Repuestos Beamo", 21), (216, "Repuestos Complementos", 21),
    (231, "Repuestos Hexa", 21), (15, "Servicios", 0), (230, "Tintas Ador", 0),
)
FLUX_CATEGORIAS = [categoria(i, n, p) for i, n, p in FLUX_ARBOL_ES]
#: Las de fuera con las reglas de partida (ninguna regla es de FLUX): las que
#: empiezan por Repuestos, Consumibles, Servicios, Tintas o Reacondicionadas,
#: y las hijas de Repuestos.
FLUX_FUERA_ES = frozenset({15, 21, 22, 23, 24, 216, 227, 230, 231, 246, 257})

#: Los nombres de cada categoría en los otros idiomas, como los da WPML (cada
#: traducción con su id y su slug). «it» no está en la web: sirve para ver que
#: un idioma nuevo hereda la decisión del español sin escribir nada.
FLUX_NOMBRES: dict[str, dict[int, str]] = {
    "es": {i: n for i, n, _ in FLUX_ARBOL_ES},
    "en": {20: "Accessories", 27: "Beambox Add-ons", 26: "Beamo Add-ons", 223: "Hexa Add-ons",
           229: "Ador Add-ons", 22: "Consumables", 19: "Laser Cutters", 228: "Flux Ador",
           245: "Fiber Laser", 227: "Refurbished", 21: "Spare Parts", 246: "Ador Spare Parts",
           24: "Beambox Spare Parts", 257: "Beambox II Spare Parts", 23: "Beamo Spare Parts",
           216: "Add-on Spare Parts", 231: "Hexa Spare Parts", 15: "Services", 230: "Ador Inks"},
    "de": {20: "Zubehör", 27: "Beambox-Zubehör", 26: "Beamo-Zubehör", 223: "Hexa-Zubehör",
           229: "Ador-Zubehör", 22: "Verbrauchsmaterial", 19: "Laserschneider",
           228: "Flux Ador", 245: "Faserlaser", 227: "Generalüberholt", 21: "Ersatzteile",
           246: "Ador Ersatzteile", 24: "Beambox-Ersatzteile", 257: "Beambox II Ersatzteile",
           23: "Beamo-Ersatzteile", 216: "Zubehör-Ersatzteile", 231: "Hexa Ersatzteile",
           15: "Dienstleistungen", 230: "Ador Tinten"},
    "fr": {20: "Accessoires", 27: "Accessoires Beambox", 26: "Accessoires Beamo",
           223: "Accessoires Hexa", 229: "Compléments Ador", 22: "Consommables",
           19: "Découpeuses laser", 228: "Flux Ador", 245: "Laser à fibre",
           227: "Reconditionnées", 21: "Pièces de rechange", 246: "Pièces de rechange Ador",
           24: "Pièces de rechange Beambox", 257: "Pièces de rechange Beambox II",
           23: "Pièces détachées Beamo", 216: "Pièces de rechange compléments",
           231: "Pièces de rechange Hexa", 15: "Services", 230: "Encres Ador"},
    "nl": {20: "Accessoires", 27: "Beambox-accessoires", 26: "Beamo-accessoires",
           223: "Hexa-accessoires", 229: "Ador-accessoires", 22: "Verbruiksartikelen",
           19: "Lasersnijders", 228: "Flux Ador", 245: "Fiberlaser", 227: "Gereviseerd",
           21: "Reserveonderdelen", 246: "Ador Onderdelen", 24: "Beambox-reserveonderdelen",
           257: "Beambox II reserveonderdelen", 23: "Beamo reserveonderdelen",
           216: "Add-on reserveonderdelen", 231: "Hexa reserveonderdelen", 15: "Diensten",
           230: "Ador inkt"},
    "it": {20: "Accessori", 27: "Accessori Beambox", 26: "Accessori Beamo",
           223: "Accessori Hexa", 229: "Complementi Ador", 22: "Materiali di consumo",
           19: "Taglierine laser", 228: "Flux Ador", 245: "Laser a fibra",
           227: "Ricondizionate", 21: "Ricambi", 246: "Ricambi Ador", 24: "Ricambi Beambox",
           257: "Ricambi Beambox II", 23: "Ricambi Beamo", 216: "Ricambi complementi",
           231: "Ricambi Hexa", 15: "Servizi", 230: "Inchiostri Ador"},
}
FLUX_IDIOMAS = ("es", "en", "de", "fr", "nl")
#: Cuánto se mueve el id de cada traducción (WPML da uno nuevo por idioma).
_FLUX_SALTO = {"es": 0, "en": 1000, "de": 2000, "fr": 3000, "nl": 4000, "it": 5000}


def flux_cat(idioma: str, cid: int) -> int:
    """El id de la categoría `cid` (española) en ese idioma."""
    return cid + _FLUX_SALTO[idioma]


def flux_prod(idioma: str, pid: int) -> int:
    """El id del producto `pid` (español) en ese idioma."""
    return pid + 10 * _FLUX_SALTO[idioma]


#: Los 34 productos REALES que entran en español con las reglas de partida
#: (Store API de fluxlasers.es, 27/10/2026): (id, nombre, categorías).
FLUX_DENTRO_ES: tuple[tuple[int, str, tuple[int, ...]], ...] = (
    (5797, "Promark Mopa Safe+ 20W", (245,)),
    (4318, "Chuck Rotary Add-on – Beamo", (20, 26)),
    (4314, "Chuck Rotary Add-on – Hexa", (20, 223)),
    (4309, "Chuck Rotary Add-on – Ador", (228, 229)),
    (4303, "Módulo de cámara gran angular – Beambox II", (20, 27)),
    (4302, "Rotary Support Tower Add-on — Chuck Rotary", (229, 20)),
    (4301, "Riser Stands", (229, 20)),
    (4291, "Chuck Rotary Add-on – Beambox II", (20, 27)),
    (4256, "Auto Feeder Add-on – Beambox II", (20, 27)),
    (4233, "Extension Pocket Add-on – Beambox II", (20, 27)),
    (4216, "Sistema Passthrough y Base Abierta — Beambox II", (20, 27)),
    (4170, "Promark Desktop Safe+", (245,)),
    (3949, "Beambox II 55W", (19,)),
    (3845, "Base de extensión Ador", (229,)),
    (3813, "Módulo rotatorio para Flux Ador", (229,)),
    (3675, "Cable Lightburn &#8211; FLUX", (20,)),
    (3641, "BEAM AIR PRO", (20, 223, 27)),
    (3598, "Módulo rotatorio 2.0 Hexa", (20, 223)),
    (3569, "Módulo rotatorio 2.0 Beambox", (20, 27)),
    (3384, "Soporte de módulo rotatorio FLUX para HEXA", (223,)),
    (3303, "MÓDULO DE IMPRESIÓN FLUX ADOR", (228, 229)),
    (3296, "MÓDULO LÁSER INFRARROJO FLUX ADOR 2 W", (228, 229)),
    (3291, "MÓDULO FLUX ADOR 10W (SÓLO MÓDULO)", (228, 229)),
    (3286, "MÓDULO FLUX ADOR 20W (SÓLO MÓDULO)", (228, 229)),
    (3270, "FLUX Ador", (228, 19)),
    (2355, "Modulo Lente 1000 dpi Hexa", (223,)),
    (2350, "Modulo rotatorio para Flux Hexa", (223,)),
    (2334, "Espaciadores metálicos Flux", (20,)),
    (1901, "Flux Hexa &#8211; Cortadora láser CO2 (60W)", (19,)),
    (33, "Beamo &#8211; Cortadora láser portátil (30W)", (19,)),
    (1447, "Módulo de autofoco para Beamo", (20, 26)),
    (1418, "Láser híbrido de diodo", (20, 26)),
    (1293, "Filtro Beam Air 2.0 &#8211; Conexión automática", (20, 27, 26)),
    (268, "Módulo rotatorio para Beamo", (20, 26)),
)
#: En una categoría de fuera Y en una de dentro (reales): fuera, en todos los
#: idiomas (las Open Box en «Cortadoras Láser» y «Reacondicionadas», los
#: cartuchos en «Flux Ador» y «Tintas Ador»).
FLUX_MEZCLA_ES: tuple[tuple[int, str, tuple[int, ...]], ...] = (
    (5734, "Beambox II 55W &#8211; Open Box", (19, 227)),
    (3324, "Amarillo &#8211; Cartucho Tinta Solvente Ador", (228, 230)),
    (3323, "Magenta &#8211; Cartucho Tinta Solvente Ador", (228, 230)),
    (3310, "Cyan &#8211; Cartucho Tinta Solvente Ador", (228, 230)),
    (3305, "Negro &#8211; Cartucho Tinta Solvente Ador", (228, 230)),
    (2844, "Beamo 30W &#8211; OpenBox", (19, 227)),
)
#: Repuestos y consumibles reales, uno por cada combinación de categorías que
#: hay en la web (de los 346 que quedan fuera); varios solo en una hija de
#: «Repuestos» (2866, 3737, 2036).
FLUX_FUERA_PRODUCTOS_ES: tuple[tuple[int, str, tuple[int, ...]], ...] = (
    (5485, "Ador Ink Extractor Conjunto", (21, 246)),
    (5580, "BB2 Water Pump TDC Conjunto", (21, 24)),
    (5626, "HDMI Cable 0.3m B100084", (21, 231)),
    (5588, "12038 Fan con Longer Cable (Exhaust fan)", (21, 24, 231)),
    (3931, "Tubo retráctil de ventilación (aluminio 200 cm)", (21, 23, 24)),
    (3469, "CABLE DE SEÑAL FLUX BM &#8211; B100096", (21, 23)),
    (5313, "Tornillo M5×12 hexagonal avellanado", (21, 24, 246)),
    (4069, "Prefiltro FLUX Beam air pro (4 piezas)", (21, 216)),
    (4332, "Pack 5 Láminas Metacrilato Color 3 mm (20×30 cm)", (22,)),
    (2866, "FLUX BB CAM LED BOARD B400044", (24,)),
    (3737, "Kit de espejos para Hexa", (231,)),
    (2003, "AIR PUMP MOTOR B100082", (21,)),
    (5586, "BB2 3rd Mirror Soporte &#038; Laser Head Conjunto", (257, 21)),
    (5158, "Duct Clamp", (21, 24, 246, 231)),
    (2036, "Accesorio dentado recto B100058", (23,)),
    (1994, "Flux ventilador de extracción B100017", (21, 23, 24, 231)),
)
#: Sin categoría (reales): fuera.
FLUX_SIN_CATEGORIA_ES: tuple[tuple[int, str, tuple[int, ...]], ...] = (
    (5831, "Servicio técnico (TSM)", ()),
    (3452, "Pago de RMA", ()),
    (3450, "Asistencia Servicio Técnico a distancia", ()),
)
#: El producto de dentro que no está traducido en cada idioma (uno por
#: idioma). En inglés WPML sirve el español tal cual (mismo id, enlace sin
#: /en/: respaldo); en los demás no sale.
FLUX_SIN_TRADUCIR = {"en": 4233, "de": 3675, "fr": 2334, "nl": 33, "it": 1418}
_FLUX_BEAMBOX = {
    "es": "La Beambox II corta y graba madera, acrílico y cuero con precisión.",
    "en": "The Beambox II cuts and engraves wood, acrylic and leather with precision.",
    "de": "Die Beambox II schneidet und graviert Holz, Acryl und Leder präzise.",
    "fr": "La Beambox II découpe et grave le bois, l'acrylique et le cuir avec précision.",
    "nl": "De Beambox II snijdt en graveert hout, acryl en leer nauwkeurig.",
    "it": "La Beambox II taglia e incide legno, acrilico e pelle con precisione.",
}


def web_flux_wpml(idiomas: tuple[str, ...] = FLUX_IDIOMAS) -> WebFalsa:
    """fluxlasers.eu (WPML, es por defecto) como es: cinco idiomas, el árbol
    real de categorías en español y en los demás con OTROS ids y los nombres
    traducidos («Spare Parts» y su hija «Beambox Spare Parts», «Ersatzteile»,
    «Reserveonderdelen» y su hija «Ador Onderdelen»…), y los productos reales
    (los 34 que entran en español, los 6 que mezclan una categoría de dentro
    con una de fuera, una muestra de repuestos y consumibles y los 3 sin
    categoría) traducidos con otro id, salvo uno de dentro por idioma."""
    h = FLUX_HOST
    web = WebFalsa(host=h, modo="wpml", defecto="es")
    todos = FLUX_DENTRO_ES + FLUX_MEZCLA_ES + FLUX_FUERA_PRODUCTOS_ES + FLUX_SIN_CATEGORIA_ES
    espanol: dict[int, dict[str, Any]] = {}
    for idioma in idiomas:
        prefijo = "" if idioma == "es" else f"{idioma}/"
        nombres = FLUX_NOMBRES[idioma]
        web.categorias[idioma] = [
            categoria(flux_cat(idioma, i), nombres[i], flux_cat(idioma, p) if p else 0)
            for i, _, p in FLUX_ARBOL_ES]
        if idioma != "es":
            web.originales_cat.update({flux_cat(idioma, i): i for i, _, _ in FLUX_ARBOL_ES})
        productos: list[dict[str, Any]] = []
        for pid, nombre, cats in todos:
            if FLUX_SIN_TRADUCIR.get(idioma) == pid:
                if idioma == "en":
                    productos.append(copy.deepcopy(espanol[pid]))
                continue
            nuevo = flux_prod(idioma, pid)
            if idioma != "es":
                web.originales_prod[nuevo] = pid
            hecho = producto(nuevo, nombre, [(flux_cat(idioma, c), nombres[c]) for c in cats],
                             host=h, prefijo=prefijo, slug=f"p{pid}-{idioma}",
                             texto=largo(f"{nombre} ({idioma}): para el taller."))
            if idioma == "es":
                espanol[pid] = hecho
            productos.append(hecho)
        web.productos[idioma] = productos
        web.paginas[idioma] = [pagina(flux_prod(idioma, 3950), f"beambox-ii-{idioma}",
                                      "Beambox II", largo(_FLUX_BEAMBOX[idioma]), host=h,
                                      prefijo=prefijo)]
        web.entradas[idioma] = []
    return web


def web_flux() -> WebFalsa:
    """FLUX recortado y SOLO en español (como lo sirve fluxlasers.es, donde
    WPML no hace caso de `lang`: los demás idiomas son el respaldo y no se
    indexan), para los tests que no van de idiomas. El de verdad, con sus
    cinco idiomas, es `web_flux_wpml`."""
    h = FLUX_HOST
    productos = [
        producto(3949, "Beambox II 55W", [(19, "Cortadoras Láser")], host=h, precio="304959",
                 price_html='<span class="woocommerce-Price-amount amount">3,049.59'
                            '<span class="woocommerce-Price-currencySymbol">&euro;</span></span>'
                            ' <small class="woocommerce-price-suffix">(+ IVA)</small>',
                 texto=largo("Cortadora láser CO2 Beambox II de 55W con cámara y autofoco.")),
        producto(1901, "Flux Hexa 60W", [(19, "Cortadoras Láser")], host=h, precio="449000",
                 price_html="4,490.00&euro; <small>(+ IVA)</small>"),
        producto(3569, "Módulo rotatorio 2.0 Beambox", [(20, "Accesorios"),
                                                       (27, "Accesorios Beambox")], host=h,
                 precio="29500", price_html="295.00&euro; <small>(+ IVA)</small>"),
        producto(2350, "Modulo rotatorio para Flux Hexa", [(223, "Accesorios Hexa")], host=h,
                 precio="13650",
                 price_html='<del aria-hidden="true">195.00&euro;</del> <span class='
                            '"screen-reader-text">El precio original era: 195.00&euro;.</span>'
                            '<ins aria-hidden="true">136.50&euro;</ins><span class="screen-reader'
                            '-text">El precio actual es: 136.50&euro;.</span> <small>(+ IVA)'
                            '</small>'),
        producto(4318, "Chuck Rotary Add-on Beamo", [(20, "Accesorios"),
                                                     (26, "Accesorios Beamo")], host=h),
        # Fuera: repuestos, consumibles, servicios, tintas, reacondicionadas.
        producto(5001, "Tubo láser Beambox 40W", [(21, "Repuestos"),
                                                   (24, "Repuestos Beambox")], host=h,
                 precio="39000"),
        producto(5002, "Espejo Hexa", [(231, "Repuestos Hexa")], host=h, precio="4500"),
        producto(5003, "Lente de repuesto", [(20, "Accesorios"), (21, "Repuestos")], host=h),
        producto(5004, "Panal de abeja consumible", [(22, "Consumibles")], host=h),
        producto(5005, "Cartucho de tinta Ador negro", [(228, "Flux Ador"),
                                                         (230, "Tintas Ador")], host=h),
        producto(5006, "Beambox Open Box", [(19, "Cortadoras Láser"),
                                             (227, "Reacondicionadas")], host=h),
        producto(5007, "Servicio de instalación", [(15, "Servicios")], host=h),
        producto(5008, "Servicio técnico (TSM)", [], host=h),
    ]
    return WebFalsa(
        host=h, modo="wpml", defecto="es",
        paginas={"es": [
            pagina(3950, "beambox-ii", "Beambox II", largo(_FLUX_BEAMBOX["es"]), host=h),
            pagina(11, "aviso-legal", "Aviso legal", largo("Texto legal de la empresa."), host=h),
            pagina(12, "carrito", "Carrito", largo("Su carrito está vacío ahora mismo."), host=h),
            pagina(13, "politica-privacidad", "Privacidad", largo("Datos personales."), host=h),
            pagina(14, "contacto", "Contacto", "Escríbenos.", host=h),
        ]},
        entradas={"es": [pagina(1891, "hexa", "HEXA",
                                largo("La Hexa es la cortadora láser CO2 de 60W de FLUX."),
                                host=h)]},
        categorias={"es": FLUX_CATEGORIAS},
        productos={"es": productos},
    )


AJ_HOST = "artisjet-printers.eu"
AJ_IDIOMAS = ["fr", "de", "pt", "en"]
#: WPGlobus: los MISMOS ids en todos los idiomas y los nombres traducidos
#: (medido el 28/10: cambian las 19). (id, madre, {idioma: nombre}).
_AJ_ARBOL: tuple[tuple[int, int, dict[str, str]], ...] = (
    (46, 0, {"fr": "Accessoires et pièces de rechange", "de": "Zubehör und Ersatzteile",
             "en": "Accessories and spare parts", "pt": "Acessórios e peças de reposição"}),
    (51, 46, {"fr": "Pièces de rechange A5000", "de": "Ersatzteile A5000",
              "en": "Spare parts A5000", "pt": "Peças de reposição A5000"}),
    (52, 46, {"fr": "Pièces de rechange Artis Young", "de": "Ersatzteile Artis Young",
              "en": "Spare parts Artis Young", "pt": "Peças de reposição Artis Young"}),
    (130, 46, {"fr": "Pièces de rechange 6090 / Trust", "de": "Ersatzteile 6090 / Trust",
               "en": "Spare parts 6090 / Trust", "pt": "Peças de reposição 6090 / Trust"}),
    (3, 0, {"fr": "Imprimantes UV LED", "de": "UV-LED-Drucker", "en": "UV LED printers",
            "pt": "Impressoras UV LED"}),
    (53, 0, {"fr": "Encres et prétraitements", "de": "Tinten und Vorbehandlungen",
             "en": "Inks and pre-treatments", "pt": "Tintas e pré-tratamentos"}),
    (5, 53, {"fr": "Encres UV LED", "de": "UV-LED-Tinten", "en": "UV LED inks",
             "pt": "Tintas UV LED"}),
    (96, 0, {"fr": "Services", "de": "Dienstleistungen", "en": "Services", "pt": "Serviços"}),
    (18, 0, {"fr": "Autres pièces de rechange", "de": "Andere Ersatzteile",
             "en": "Other spare parts", "pt": "Outras peças de reposição"}),
    (19, 18, {"fr": "Logiciels", "de": "Software", "en": "Software", "pt": "Software"}),
)
AJ_CATEGORIAS = [categoria(i, n["fr"], p) for i, p, n in _AJ_ARBOL]
_AJ_NOMBRE = {i: n for i, _, n in _AJ_ARBOL}
_TEXTOS_DIVE = {
    "fr": largo("Imprimante UV LED artisJet DIVE pour personnaliser des objets en couleur."),
    "de": largo("UV-LED-Drucker artisJet DIVE zum Personalisieren von Objekten in Farbe."),
    "en": largo("UV LED printer artisJet DIVE for personalising objects in colour."),
}
_TEXTOS_EVENTS = {
    "fr": largo("Retrouvez artisJet aux salons professionnels de l'impression."),
    "de": largo("Treffen Sie artisJet auf den Fachmessen für Druck."),
    "en": largo("Meet artisJet at the professional printing trade shows."),
}
_TEXTOS_WHITERIP = {
    "fr": largo("WhiteRIP, le logiciel d'impression pour les imprimantes artisJet."),
    "de": largo("WhiteRIP, die Drucksoftware für artisJet-Drucker."),
    "en": largo("WhiteRIP, the printing software for artisJet printers."),
}


def web_artisjet_eu() -> WebFalsa:
    """artisjet-printers.eu (WPGlobus, fr por defecto): `events` traducida a de y
    en (pt NO: sirve el francés con la URL /pt/); el DIVE igual; repuestos
    colgados de la madre «Accessoires et pièces de rechange» y de su hija; el
    WhiteRIP (493) en «Logiciels», hija de «Autres pièces de rechange». Las
    categorías tienen los mismos ids en todos los idiomas y los nombres
    traducidos, también dentro de cada producto."""
    h = AJ_HOST
    paginas: dict[str, list[dict[str, Any]]] = {}
    productos: dict[str, list[dict[str, Any]]] = {}
    for idioma in AJ_IDIOMAS:
        prefijo = "" if idioma == "fr" else f"{idioma}/"

        def cats(*ids: int, idioma: str = idioma) -> list[tuple[int, str]]:
            return [(i, _AJ_NOMBRE[i][idioma]) for i in ids]

        texto = _TEXTOS_EVENTS.get(idioma, _TEXTOS_EVENTS["fr"])   # pt → respaldo fr
        traduccion = (traduccion_wpglobus("fr", ["fr", "de", "en"], AJ_IDIOMAS)
                      if idioma == "fr" else None)
        paginas[idioma] = [
            pagina(9011, "events", {"fr": "Salons", "de": "Messen", "en": "Events"}.get(
                idioma, "Salons"), texto, host=h, prefijo=prefijo, traduccion=traduccion),
            pagina(9020, "mentions-legales", "Mentions légales", largo("Texte légal."), host=h,
                   prefijo=prefijo, traduccion=traduccion),
            pagina(9021, "cookie-policy", "Cookies", largo("Politique de cookies."), host=h,
                   prefijo=prefijo, traduccion=traduccion),
            pagina(9022, "basket", "Panier", largo("Votre panier est vide."), host=h,
                   prefijo=prefijo, traduccion=traduccion),
        ]
        texto_dive = _TEXTOS_DIVE.get(idioma, _TEXTOS_DIVE["fr"])
        productos[idioma] = [
            producto(9741, "artisJet DIVE", cats(3), host=h,
                     prefijo=prefijo, texto=texto_dive, slug="artisjet-dive"),
            producto(3850, "Ink Bottle System with Shelf", cats(46), host=h, prefijo=prefijo,
                     precio="9500", price_html="&euro;95,00",
                     texto=largo("Système de bouteilles d'encre avec étagère.")),
            producto(7001, "Capping Artisjet Freebird", cats(46, 51), host=h, prefijo=prefijo),
            producto(7002, "Pompe péristaltique pour Artis Young", cats(46, 52), host=h,
                     prefijo=prefijo),
            producto(7003, "Carte mère Trust", cats(130), host=h, prefijo=prefijo),
            producto(7004, "Encre UV LED cyan 1L", cats(5), host=h, prefijo=prefijo),
            producto(7005, "Services installation", cats(96), host=h, prefijo=prefijo),
            producto(7006, "Plaque de tête d'impression MBO 3020", [], host=h,
                     prefijo=prefijo),
            producto(493, "WhiteRIP LOGICIEL D'IMPRESSION", cats(19), host=h, prefijo=prefijo,
                     texto=_TEXTOS_WHITERIP.get(idioma, _TEXTOS_WHITERIP["fr"])),
            producto(7008, "Courroie d'entraînement Freebird", cats(18), host=h,
                     prefijo=prefijo),
        ]
    return WebFalsa(host=h, modo="prefijo", defecto="fr", paginas=paginas,
                    entradas={i: [] for i in AJ_IDIOMAS},
                    categorias={i: [categoria(c, n[i], p) for c, p, n in _AJ_ARBOL]
                                for i in AJ_IDIOMAS},
                    productos=productos)


MBO_HOST = "mboprinters.com"
#: WPBakery sin pintar y CSS suelto del carrusel, como en mboprinters.
BASURA_WPBAKERY = (
    "[vc_row][vc_column width=»1/3″][vc_column_text]"
    "#wpcpro-wrapper-2990 .wpcp-carousel-section {color:#333;font-size:14px;}"
    " Impresora MBO 6090 UV Led de sobremesa con 40 cm de altura útil para objetos grandes."
    " Estructura de acero y motor lineal de levitación magnética.[/vc_column_text]"
    "[/vc_column][/vc_row]"
)


def web_mboprinters() -> WebFalsa:
    h = MBO_HOST
    idiomas = ["es", "en", "de", "fr"]
    paginas = {}
    for idioma in idiomas:
        prefijo = "" if idioma == "es" else f"{idioma}/"
        paginas[idioma] = [
            pagina(3004, "uv-led-digital-printing", "UV LED",
                   largo(f"Impresión digital UV LED ({idioma}) sobre objetos y materiales."),
                   host=h, prefijo=prefijo,
                   html=f"<p>{BASURA_WPBAKERY}</p><p>{largo('Texto ' + idioma + ' útil.')}</p>",
                   traduccion=(traduccion_wpglobus("es", idiomas, idiomas)
                               if idioma == "es" else None)),
        ]
    return WebFalsa(
        host=h, modo="prefijo", defecto="es", paginas=paginas,
        entradas={i: [] for i in idiomas},
        categorias={"es": [categoria(70, "Impresoras UV Led"), categoria(78, "Sistemas DTF")]},
        productos={i: [producto(4066, f"MBO 6090 UV Led Desktop {i}", [(70, "Impresoras UV Led")],
                                host=h, prefijo="" if i == "es" else f"{i}/",
                                texto=largo(f"Impresora UV de sobremesa ({i})."))]
                   for i in idiomas})


MBOL_HOST = "mbolasers.com"


def web_mbolasers() -> WebFalsa:
    """mbolasers.com (WPML, es por defecto), todo por la REST (desde el 28/10
    la lista de páginas con su contenido da 200 en los cinco idiomas). Las
    categorías con su id en cada idioma, como en la web (Accesorios 39 / 763 /
    765); fr y nl no publicados (todo es el español); en de sirve un producto
    español tal cual (mismo id, enlace sin /de/)."""
    h = MBOL_HOST
    texto_es = largo("Láser de fibra MBO para marcado industrial sobre metal y plástico.")
    texto_en = largo("MBO fiber laser for industrial marking on metal and plastic.")
    texto_de = largo("MBO-Faserlaser für die industrielle Markierung von Metall und Kunststoff.")
    cnc = producto(3201, "CNC de sobremesa Cyborg X-300", [(37, "MBO Fresadoras CNC")],
                   host=h, precio="599000", price_html="5.990,00&euro; <small>+ IVA</small>",
                   permalink=f"https://{h}/producto/cnc-de-sobremesa-cyborg-x-300/")
    return WebFalsa(
        host=h, modo="wpml", defecto="es",
        paginas={
            "es": [pagina(1265, "fiber-lasers", "MBO Fiber Lasers", texto_es, host=h),
                   pagina(1300, "aviso-legal", "Aviso legal", "x", host=h)],
            "en": [pagina(2373, "fiber-lasers-en", "MBO Fiber Lasers", texto_en, host=h,
                          prefijo="en/")],
            "de": [pagina(2866, "faserlaser", "MBO-Faserlaser", texto_de, host=h,
                          prefijo="de/")],
        },
        entradas={"es": [], "en": [], "de": []},
        categorias={"es": [categoria(37, "MBO Fresadoras CNC"), categoria(39, "Accesorios")],
                    "en": [categoria(779, "MBO CNC Milling Machines"),
                           categoria(763, "Accessories")],
                    "de": [categoria(781, "MBO CNC-Fräsmaschinen"), categoria(765, "Zubehör")]},
        originales_cat={779: 37, 763: 39, 781: 37, 765: 39},
        originales_prod={2839: 965, 2821: 965},
        productos={
            "es": [cnc, producto(965, "Dispositivo giratorio de 50 mm", [(39, "Accesorios")],
                                 host=h, precio="52500",
                                 price_html="525,00&euro; <small>+ IVA</small>")],
            "en": [producto(2839, "50mm rotary device", [(763, "Accessories")], host=h,
                            prefijo="en/", precio="52500",
                            price_html="525,00&euro; <small>+ VAT</small>")],
            "de": [cnc,   # el español tal cual: mismo id y enlace sin /de/
                   producto(2821, "Drehteller 50 mm", [(765, "Zubehör")], host=h,
                            prefijo="de/", precio="52500",
                            price_html="525,00&euro; <small>zzgl. MwSt</small>")],
        })


PIMPAM_HOST = "pimpam-vending.com"


def web_pimpam() -> WebFalsa:
    """pimpam (WPML sin WooCommerce): el portugués se publica como `pt-pt`;
    `lang=pt` vuelve en silencio al español."""
    h = PIMPAM_HOST
    return WebFalsa(
        host=h, modo="wpml", defecto="es", sin_tienda=True, codigos={"pt-pt": "pt"},
        paginas={
            "es": [pagina(268, "casebox-vending", "Pimpam Casebox",
                          largo("Máquina expendedora Casebox para tiendas y oficinas."), host=h)],
            "pt": [pagina(616, "pimpam-casebox", "Pimpam Casebox",
                          largo("Máquina de venda automática Casebox para lojas."), host=h,
                          prefijo="pt-pt/")],
        },
        entradas={"es": [], "pt": []}, productos={})
