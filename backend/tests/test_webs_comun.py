"""Lo común de los tests del índice de las webs: un WordPress simulado (por
`httpx.MockTransport`, nada sale a la red) que se porta como las webs medidas
el 27/10/2026 — `?rest_route=`, WPGlobus por prefijo con su respaldo, WPML con
`lang=` e ids propios por idioma y respaldo silencioso, la Store API con sus
filtros, el 500 de Elementor de mbolasers y pimpam sin tienda — y los datos de
partida de cada web. Este fichero no tiene tests: lo importan los demás.
"""
from __future__ import annotations

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
    #: mbolasers: la REST da 500 si se pide el contenido de las páginas.
    contenido_500: bool = False
    #: URL → HTML público de la página.
    html: dict[str, str] = field(default_factory=dict)
    #: Idiomas que dan 500 en todo (una web a medias).
    caidos: set[str] = field(default_factory=set)
    #: WPML: código pedido → idioma de los datos (`pt-pt` → `pt`). Un código
    #: que no está aquí ni en los datos vuelve al de por defecto.
    codigos: dict[str, str] = field(default_factory=dict)
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

    def responder(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(str(request.url))
        if self.rota:
            return httpx.Response(404, text="<html><body>404</body></html>")
        qs = parse_qs(request.url.query.decode())
        ruta = qs.get("rest_route", [None])[0]
        if ruta is None:
            documento = self.html.get(str(request.url))
            if documento is None:
                return httpx.Response(404, text="<html><body>404</body></html>")
            return httpx.Response(200, text=documento,
                                  headers={"content-type": "text/html; charset=UTF-8"})
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
        if ruta == "/wc/store/v1/products/categories":
            # Sin paginar: la Store API la da entera.
            return httpx.Response(200, json=self.categorias.get(
                idioma, self.categorias.get(self.defecto, [])))
        if ruta == "/wc/store/v1/products":
            datos = list(self.productos.get(idioma, []))
            if "include" in qs:
                ids = {int(x) for x in qs["include"][0].split(",")}
                datos = [p for p in datos if p["id"] in ids]
            if "category" in qs and qs.get("category_operator", [""])[0] == "not_in":
                fuera = {int(x) for x in qs["category"][0].split(",")}
                datos = [p for p in datos if not {c["id"] for c in p["categories"]} & fuera]
            return self._lista(datos, qs, [])
        if ruta == "/wp/v2/product":
            ids = {int(x) for x in qs.get("include", [""])[0].split(",") if x}
            datos = [{"id": p["id"], "modified_gmt": "2026-08-15T08:30:00"}
                     for p in self.productos.get(idioma, []) if p["id"] in ids]
            return self._lista(datos, qs, [])
        return httpx.Response(404, json={"code": "rest_no_route", "message": "No route"})

    def _lista(self, datos: list[dict[str, Any]], qs: dict[str, list[str]],
               campos: list[str]) -> httpx.Response:
        por_pagina = int(qs.get("per_page", ["10"])[0])
        numero = int(qs.get("page", ["1"])[0])
        total_paginas = max(1, -(-len(datos) // por_pagina))
        if numero > total_paginas:
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        trozo = datos[(numero - 1) * por_pagina: numero * por_pagina]
        if campos:
            trozo = [{k: v for k, v in d.items() if k in campos} for d in trozo]
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
#: El árbol de categorías de FLUX (medido en fluxlasers.es, espejo de .eu).
FLUX_CATEGORIAS = [
    categoria(20, "Accesorios"), categoria(27, "Accesorios Beambox", 20),
    categoria(26, "Accesorios Beamo", 20), categoria(223, "Accesorios Hexa", 20),
    categoria(229, "Complementos Ador"), categoria(19, "Cortadoras Láser"),
    categoria(228, "Flux Ador"), categoria(227, "Reacondicionadas", 19),
    categoria(22, "Consumibles"), categoria(21, "Repuestos"),
    categoria(24, "Repuestos Beambox", 21), categoria(231, "Repuestos Hexa", 21),
    categoria(15, "Servicios"), categoria(230, "Tintas Ador"),
]


def web_flux() -> WebFalsa:
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
            pagina(3950, "beambox-ii", "Beambox II",
                   largo("La Beambox II corta y graba madera, acrílico y cuero con precisión."),
                   host=h),
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
AJ_CATEGORIAS = [
    categoria(46, "Accessoires et pièces de rechange"),
    categoria(51, "Pièces de rechange A5000", 46),
    categoria(52, "Pièces de rechange Artis Young", 46),
    categoria(130, "Pièces de rechange 6090 / Trust", 46),
    categoria(3, "Imprimantes UV LED"),
    categoria(53, "Encres et prétraitements"), categoria(5, "Encres UV LED", 53),
    categoria(96, "Services"),
]
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


def web_artisjet_eu() -> WebFalsa:
    """artisjet-printers.eu (WPGlobus, fr por defecto): `events` traducida a de y
    en (pt NO: sirve el francés con la URL /pt/); el DIVE igual; repuestos
    colgados de la madre «Accessoires et pièces de rechange» y de su hija."""
    h = AJ_HOST
    paginas: dict[str, list[dict[str, Any]]] = {}
    productos: dict[str, list[dict[str, Any]]] = {}
    for idioma in AJ_IDIOMAS:
        prefijo = "" if idioma == "fr" else f"{idioma}/"
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
            producto(9741, "artisJet DIVE", [(3, "Imprimantes UV LED")], host=h,
                     prefijo=prefijo, texto=texto_dive, slug="artisjet-dive"),
            producto(3850, "Ink Bottle System with Shelf",
                     [(46, "Accessoires et pièces de rechange")], host=h, prefijo=prefijo,
                     precio="9500", price_html="&euro;95,00",
                     texto=largo("Système de bouteilles d'encre avec étagère.")),
            producto(7001, "Capping Artisjet Freebird",
                     [(46, "Accessoires et pièces de rechange"),
                      (51, "Pièces de rechange A5000")], host=h, prefijo=prefijo),
            producto(7002, "Pompe péristaltique pour Artis Young",
                     [(46, "Accessoires et pièces de rechange"),
                      (52, "Pièces de rechange Artis Young")], host=h, prefijo=prefijo),
            producto(7003, "Carte mère Trust", [(130, "Pièces de rechange 6090 / Trust")],
                     host=h, prefijo=prefijo),
            producto(7004, "Encre UV LED cyan 1L", [(5, "Encres UV LED")], host=h,
                     prefijo=prefijo),
            producto(7005, "Services installation", [(96, "Services")], host=h,
                     prefijo=prefijo),
            producto(7006, "Plaque de tête d'impression MBO 3020", [], host=h,
                     prefijo=prefijo),
        ]
    return WebFalsa(host=h, modo="prefijo", defecto="fr", paginas=paginas,
                    entradas={i: [] for i in AJ_IDIOMAS},
                    categorias={"fr": AJ_CATEGORIAS}, productos=productos)


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
_CABECERA_ELEMENTOR = ('<div data-elementor-type="header"><nav>Inicio Tienda Contacto</nav>'
                       '<p>Llámanos al 900 000 000</p></div>')
_PIE_ELEMENTOR = ('<div data-elementor-type="footer"><p>© MBO Lasers · Aviso legal · '
                  'Cookies</p></div>')


def html_elementor(lang: str, cuerpo: str) -> str:
    return (f'<!DOCTYPE html><html lang="{lang}"><head><title>T</title>'
            f"<style>.x{{color:red}}</style></head><body>{_CABECERA_ELEMENTOR}"
            f'<div data-elementor-type="wp-page" data-elementor-id="1"><section><h2>Láser</h2>'
            f"<p>{cuerpo}</p></section></div>{_PIE_ELEMENTOR}<script>var a=1;</script>"
            "</body></html>")


def web_mbolasers() -> WebFalsa:
    """mbolasers.com (WPML, es por defecto): la REST da 500 con el contenido de
    las páginas; de/fr sirven un producto español tal cual (mismo id, enlace
    sin /de/)."""
    h = MBOL_HOST
    texto_es = largo("Láser de fibra MBO para marcado industrial sobre metal y plástico.")
    texto_en = largo("MBO fiber laser for industrial marking on metal and plastic.")
    html = {
        f"https://{h}/fiber-lasers/": html_elementor("es-ES", texto_es),
        f"https://{h}/en/fiber-lasers-en/": html_elementor("en-US", texto_en),
        # Una página de «de» cuyo HTML es el español (respaldo): fuera.
        f"https://{h}/de/faserlaser/": html_elementor("es-ES", texto_es),
    }
    cnc = producto(3201, "CNC de sobremesa Cyborg X-300", [(37, "MBO Fresadoras CNC")],
                   host=h, precio="599000", price_html="5.990,00&euro; <small>+ IVA</small>",
                   permalink=f"https://{h}/producto/cnc-de-sobremesa-cyborg-x-300/")
    return WebFalsa(
        host=h, modo="wpml", defecto="es", contenido_500=True, html=html,
        paginas={
            "es": [pagina(1265, "fiber-lasers", "MBO Fiber Lasers", texto_es, host=h),
                   pagina(1300, "aviso-legal", "Aviso legal", "x", host=h)],
            "en": [pagina(2373, "fiber-lasers-en", "MBO Fiber Lasers", texto_en, host=h,
                          prefijo="en/")],
            "de": [pagina(2866, "faserlaser", "MBO-Faserlaser", texto_es, host=h,
                          prefijo="de/")],
        },
        entradas={"es": [], "en": [], "de": []},
        categorias={"es": [categoria(37, "MBO Fresadoras CNC"), categoria(39, "Accesorios")],
                    "de": [categoria(781, "MBO CNC-Fräsmaschinen"), categoria(765, "Zubehör")]},
        productos={
            "es": [cnc, producto(965, "Dispositivo giratorio de 50 mm", [(39, "Accesorios")],
                                 host=h, precio="52500",
                                 price_html="525,00&euro; <small>+ IVA</small>")],
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
