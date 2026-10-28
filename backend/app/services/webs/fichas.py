"""Fichas de producto montadas con una página del índice de las webs.

El catálogo del compositor manda en sus productos (precio de venta,
características, traducciones revisadas) y llega en un solo sentido: lo que
se añadiera por el lado de BoHub lo borraría la siguiente sincronización. Lo
que el catálogo no tiene y está publicado en una de nuestras webs (el 28/10,
la «MBO 6090 UV Led – Desktop», solo en mboprinters.com) se puede presentar
igual, con una ficha montada desde su fila del índice:

- **Clave**: `{"fuente": "web", "origen_id": "<sitio>:<wp_id>"}` (la misma
  forma que una del catálogo, para que el bloque de ficha no cambie).
- **Idioma**: la fila del idioma del correo; si la web no la tiene en ese
  idioma, la que haya (primero la del idioma por defecto de la web) y se dice.
  WPGlobus (mboprinters) da el mismo id en todos los idiomas; WPML
  (mbolasers, fluxlasers.eu, pimpam) da a cada traducción su id y su slug,
  así que las traducciones se reconocen también por la imagen principal.
- **Precio**: el de la web tal como está guardado (con su IVA: «489,30 € (+
  IVA)»), que NUNCA es precio de oferta; sin precio en la web, «precio a
  consultar». Nunca se deduce de un producto parecido.
- **Imagen**: la principal de la web, enlazada (no se sube a ningún sitio);
  la galería va aparte por si se quiere otra.

Y la comprobación inversa (`MapaProductos.motivo`): un enlace del catálogo que
va a una página de servicio (el 28/10, la ficha de la artisJet 3000Pro
Freebird apuntaba a `boprint.net/contacto/`) o que no está en el índice.
Cualquier página indexada de la web vale, también una de aterrizaje.

Solo lee: ni el índice ni el catálogo se tocan desde aquí.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.models.fuentes_externas import WebIndiceEntrada
from app.services.externo import html_texto

FUENTE_WEB = "web"
TIPO_PRODUCTO = "producto"
#: Lo que entra de la descripción en la ficha (la ficha recorta a 160).
MAX_DESCRIPCION = 300
#: Códigos de idioma de WPML que no son de dos letras (`/pt-pt/…`).
_ALTERNATIVAS = ("pt-pt", "pt-br", "zh-hans", "zh-hant")
#: Las URL que se escriben en una instrucción.
_URL_RE = re.compile(r"https?://[^\s<>\"'«»()]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


def host_de(url: str | None) -> str:
    """El host sin `www.` y en minúsculas ("" si no es una URL)."""
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def sitio_de(url: str | None) -> Any:
    """La web del grupo a la que pertenece la URL (con sus otros dominios:
    fluxlasers.es y fluxlasers.eu son la misma), o None."""
    from app.services.web_forms import sitios as registro_sitios  # noqa: PLC0415

    host = host_de(url)
    return registro_sitios.registro().por_dominio(host) if host else None


def ruta_de(url: str | None, idiomas: tuple[str, ...] | list[str] = ()) -> str:
    """La ruta decodificada, en minúsculas, sin la barra final, sin la consulta
    y sin el prefijo de idioma de la web (`/en/product/mbo-6090/` →
    `/product/mbo-6090`). Solo se quita un prefijo que es un idioma de esa web
    (o su variante de WPML): `/uv/…` no es un idioma."""
    try:
        ruta = unquote(urlsplit(str(url or "").strip()).path or "/").lower()
    except ValueError:
        return ""
    primero = ruta.lstrip("/").split("/", 1)[0]
    if primero and "/" in ruta.lstrip("/") and (
            primero in {i.lower() for i in idiomas} or primero in _ALTERNATIVAS):
        ruta = "/" + ruta.lstrip("/").split("/", 1)[1]
    return ruta.rstrip("/") or "/"


def slug_de(url: str | None) -> str:
    """El último trozo de la ruta (el slug de WordPress)."""
    return ruta_de(url).rsplit("/", 1)[-1]


def pagina(url: str | None) -> str:
    """La misma página aunque cambie el host de la web, el idioma o la barra
    final: `<web>:<ruta>` (o el host si no es de una web del grupo)."""
    sitio = sitio_de(url)
    if sitio is not None:
        return f"{sitio.clave}:{ruta_de(url, sitio.idiomas)}"
    host = host_de(url)
    return f"{host}:{ruta_de(url)}" if host else ""


def urls_de(texto: str | None) -> list[str]:
    """Las URL que trae un texto (una instrucción), sin la puntuación final."""
    salida: list[str] = []
    for m in _URL_RE.finditer(texto or ""):
        url = m.group(0).rstrip(".,;:!?)]»")
        if url not in salida:
            salida.append(url)
    return salida


# ---------------------------------------------------------------------------
# Buscar el producto
# ---------------------------------------------------------------------------


def _vivos(session: Session, *filtros: Any) -> list[WebIndiceEntrada]:
    """Los productos vivos del índice (el texto, que es lo que pesa, solo si
    se lee)."""
    return list(session.scalars(select(WebIndiceEntrada).options(
        defer(WebIndiceEntrada.texto)).where(
        WebIndiceEntrada.ido_en.is_(None), WebIndiceEntrada.tipo == TIPO_PRODUCTO, *filtros)))


def clave_de(fila: WebIndiceEntrada) -> dict[str, str]:
    return {"fuente": FUENTE_WEB, "origen_id": f"{fila.sitio}:{fila.wp_id}"}


def producto_por_url(session: Session, url: str | None) -> WebIndiceEntrada | None:
    """La fila de producto del índice a la que apunta `url` (de cualquier
    idioma y de cualquiera de los dominios de esa web): misma ruta sin el
    prefijo de idioma o, si no, mismo slug. None si no es un producto del
    índice (o la URL no es de una web del grupo)."""
    sitio = sitio_de(url)
    if sitio is None:
        return None
    return MapaProductos(session, sitios=[sitio.clave]).producto(url)


def productos_por_nombre(session: Session, nombre: str, *, sitio: str | None = None,
                         idioma: str | None = None, limite: int = 3) -> list[WebIndiceEntrada]:
    """Los productos del índice cuyo título lleva TODAS las palabras de
    `nombre` (sin tildes), los más ajustados primero: «MBO 6090 desktop» da la
    «MBO 6090 UV Led – Desktop» y no la «MBO 6090 UV Led»."""
    from app.services.webs.busqueda import palabras_de  # noqa: PLC0415

    palabras = [html_texto.plano(p) for p in palabras_de(nombre)]
    if not palabras:
        return []
    # La palabra más larga, en la base (sin tildes en MySQL); el resto, aquí.
    # Con una letra suelta no se busca nada: casaría con cualquier título.
    larga = max(palabras, key=len)
    if len(larga) < 2:
        return []
    filtros: list[Any] = [func.lower(WebIndiceEntrada.titulo).like(f"%{larga}%")]
    if sitio:
        filtros.append(WebIndiceEntrada.sitio == sitio)
    filas = [f for f in _vivos(session, *filtros)
             if all(p in html_texto.plano(f.titulo or "") for p in palabras)]
    # Un producto por id; el más ajustado (el título más corto) primero, y de
    # cada uno la fila del idioma pedido si la hay.
    grupos: dict[tuple[str, int], list[WebIndiceEntrada]] = {}
    for f in filas:
        grupos.setdefault((f.sitio, f.wp_id), []).append(f)
    elegidas = [next((f for f in g if f.idioma == idioma),
                     min(g, key=lambda f: len(f.titulo or ""))) for g in grupos.values()]
    elegidas.sort(key=lambda f: min(len(x.titulo or "") for x in grupos[(f.sitio, f.wp_id)]))
    return elegidas[:limite]


def variantes(session: Session, fila: WebIndiceEntrada) -> list[WebIndiceEntrada]:
    """Las filas del mismo producto en los otros idiomas. WPGlobus da el mismo
    id en todos; con WPML cada traducción tiene su id y su slug, y el índice no
    guarda qué traducción es de qué: se reconocen por la misma imagen principal
    Y el mismo precio (WPML los sincroniza; sin precio en la web, por la
    imagen). Si en un idioma salen dos, no se elige ninguna (podrían ser
    variantes de potencia con la misma foto)."""
    from app.models.sitios import PLUGIN_WPML  # noqa: PLC0415
    from app.services.web_forms import sitios as registro_sitios  # noqa: PLC0415

    filas = _vivos(session, WebIndiceEntrada.sitio == fila.sitio)
    mismas = [f for f in filas if f.wp_id == fila.wp_id]
    if len({f.idioma for f in mismas}) > 1:
        return mismas            # el mismo id en varios idiomas (WPGlobus): es ese
    if not fila.imagen:
        return mismas            # sin imagen no hay con qué reconocerla
    if fila.precio_web is None:
        # Sin precio, solo la imagen; y solo en una web con WPML: en las
        # demás la traducción es el mismo id, y otro id con la misma foto es
        # otro producto (la MBO 6090 y su versión desktop).
        sitio = registro_sitios.registro().sitio(fila.sitio)
        if sitio is None or sitio.plugin != PLUGIN_WPML:
            return mismas
    parecidas = [f for f in filas if f.wp_id != fila.wp_id and f.imagen == fila.imagen
                 and f.precio_web == fila.precio_web]
    if any(f.idioma == fila.idioma for f in parecidas):
        return mismas            # otra del mismo idioma igual: no se sabe cuál es cuál
    por_idioma: dict[str, list[WebIndiceEntrada]] = {}
    for f in parecidas:
        por_idioma.setdefault(f.idioma, []).append(f)
    return mismas + [v[0] for v in por_idioma.values() if len(v) == 1]


def _por_clave(session: Session, origen_id: str) -> list[WebIndiceEntrada]:
    sitio, _, wp_id = str(origen_id or "").partition(":")
    if not sitio or not wp_id.isdigit():
        return []
    base = _vivos(session, WebIndiceEntrada.sitio == sitio, WebIndiceEntrada.wp_id == int(wp_id))
    if not base:
        return []
    return variantes(session, base[0])


# ---------------------------------------------------------------------------
# La ficha
# ---------------------------------------------------------------------------


def _lista(raw: str | None) -> list[str]:
    try:
        datos = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in datos if x] if isinstance(datos, list) else []


def _importe(valor: Decimal | float | None, moneda: str | None) -> str | None:
    if valor is None or float(valor) <= 0:
        return None
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{texto} {'€' if (moneda or 'EUR') == 'EUR' else moneda}"


def precio_de(fila: WebIndiceEntrada) -> str | None:
    """El precio de la web tal como está guardado («489,30 € (+ IVA)»); None
    si la web no da precio (el índice no guarda los que están a 0)."""
    texto = (fila.precio_web_texto or "").strip()
    return texto or _importe(fila.precio_web, fila.moneda)


def elegir(filas: list[WebIndiceEntrada], idioma: str,
           idioma_defecto: str | None) -> WebIndiceEntrada:
    """La fila del idioma pedido; si no, la del idioma por defecto de la web;
    si no, la primera."""
    for candidato in (idioma, idioma_defecto):
        for f in filas:
            if candidato and f.idioma == candidato:
                return f
    return sorted(filas, key=lambda f: f.idioma)[0]


def _descripcion(fila: WebIndiceEntrada) -> str:
    texto = " ".join(str(fila.extracto or "").split()) or " ".join(str(fila.texto or "").split())
    if len(texto) <= MAX_DESCRIPCION:
        return texto
    return texto[:MAX_DESCRIPCION - 1].rsplit(" ", 1)[0] + "…"


def ficha_de(filas: list[WebIndiceEntrada], *, idioma: str,
             idioma_defecto: str | None = None, marca: str | None = None,
             origen_id: str | None = None) -> dict[str, Any]:
    """La ficha de un producto del índice con la forma de las del catálogo
    (`catalogo.producto_para_correo`), para el mismo bloque de ficha. Su
    `origen_id` es el de la clave pedida aunque la fila sea la de otro idioma
    (con WPML, otro id): así la ficha se sigue encontrando por su clave."""
    fila = elegir(filas, idioma, idioma_defecto)
    precio = precio_de(fila)
    return {
        "origen_id": origen_id or f"{fila.sitio}:{fila.wp_id}", "fuente": FUENTE_WEB,
        "tipo": TIPO_PRODUCTO,
        "nombre": fila.titulo, "marca": marca, "descripcion": _descripcion(fila),
        "caracteristicas": [], "categoria": None, "imagen": fila.imagen,
        "galeria": _lista(fila.galeria_json), "categorias": _lista(fila.categorias_json),
        "precio_texto": precio, "precio_publico": precio is not None,
        "precio_consultar": precio is None, "precio_origen": "web" if precio else "consultar",
        "precio_iva": fila.precio_iva, "moneda": fila.moneda if precio else None,
        "enlace": fila.url, "idioma": idioma, "idioma_ficha": fila.idioma,
        "idiomas_web": sorted({f.idioma for f in filas}),
        "sin_idioma_pedido": fila.idioma != idioma, "sitio": fila.sitio,
        "dominio": fila.dominio, "web_id": fila.id, "iva": None,
    }


def en_idioma(session: Session, fila: WebIndiceEntrada, idioma: str) -> WebIndiceEntrada:
    """La misma página de producto en el idioma pedido, si la web lo tiene."""
    from app.services.web_forms import sitios as registro_sitios  # noqa: PLC0415

    sitio = next((s for s in registro_sitios.cargar(session).webs if s.clave == fila.sitio), None)
    return elegir(variantes(session, fila) or [fila], idioma,
                  sitio.idioma_defecto if sitio else None)


def fichas_por_clave(session: Session, claves: list[dict[str, Any]], *,
                     idioma: str) -> dict[str, dict[str, Any]]:
    """`"web|<sitio>:<wp_id>"` → la ficha, para las claves de fuente `web`.
    Lo que ya no está en el índice (se quitó de la web) no sale."""
    from app.services.web_forms import sitios as registro_sitios  # noqa: PLC0415

    sitios = {s.clave: s for s in registro_sitios.cargar(session).webs}
    salida: dict[str, dict[str, Any]] = {}
    for c in claves:
        if str(c.get("fuente") or "") != FUENTE_WEB:
            continue
        origen_id = str(c.get("origen_id") or "")
        filas = _por_clave(session, origen_id)
        if not filas:
            continue
        sitio = sitios.get(filas[0].sitio)
        salida[f"{FUENTE_WEB}|{origen_id}"] = ficha_de(
            filas, idioma=idioma, idioma_defecto=sitio.idioma_defecto if sitio else None,
            marca=sitio.marca if sitio else None, origen_id=origen_id)
    return salida


# ---------------------------------------------------------------------------
# Enlaces del catálogo que no son producto
# ---------------------------------------------------------------------------


#: Las bases de las páginas de producto (`/producto/<slug>`): solo con una de
#: ellas en la ruta se reconoce un producto por su slug (una categoría o una
#: entrada del blog pueden llevar el mismo slug que el producto).
BASES_PRODUCTO = frozenset({"product", "producto", "produkt", "produit", "prodotto", "products",
                            "productos", "produkte", "produits", "shop", "tienda", "boutique",
                            "winkel", "negozio"})
#: Por qué hay que avisar del enlace de una ficha del catálogo (desde el
#: 28/10/2026, segunda vuelta: vale CUALQUIER página indexada de nuestras webs,
#: también una página de aterrizaje como `fluxlasers.eu/beambox-ii/`):
#: - `servicio`: una página de servicio (contacto, categoría, carrito, pago,
#:   mi cuenta, buscador, legales…): no lleva a la máquina;
#: - `ido`: el índice ya no la tiene (se quitó de la web, o una regla del
#:   índice la deja fuera);
#: - `fuera`: no está en el índice, ni como producto, ni como página, ni como
#:   entrada (o es nueva y aún no se ha leído).
MOTIVO_SERVICIO = "servicio"
MOTIVO_IDO = "ido"
MOTIVO_FUERA = "fuera"
#: Las páginas de servicio que el índice SÍ lee (las demás, carrito, pago, mi
#: cuenta, legales…, las excluye por su slug: `webs.reglas`): las de contacto,
#: también con más palabras («contacto-mbo-lasers», «kontakt-mbo-laser»), y el
#: buscador.
_SERVICIO_RE = re.compile(
    r"(^|-)(contact|contacto|contactos|contacta|contactanos|contactar|contacter|contactez"
    r"|contacteer|kontakt|kontaktieren|contatto|contatti)(-|$)"
    r"|^(nous-contacter|neem-contact-op|buscador|buscar|busqueda|search|suche|recherche"
    r"|zoeken|cerca)$", re.IGNORECASE)
#: La consulta de una búsqueda o de la cesta de WordPress (`/?s=…`,
#: `?add-to-cart=…`): tampoco lleva a la máquina.
_CONSULTA_SERVICIO = frozenset({"s", "add-to-cart", "post_type"})
#: Las bases de las categorías y etiquetas (de la tienda o del blog).
BASES_CATEGORIA = frozenset({
    "categoria-producto", "product-category", "produkt-kategorie", "categorie-produit",
    "categoria-prodotto", "productcategorie", "product-categorie", "categoria", "categorias",
    "category", "categories", "categorie", "kategorie", "etiqueta-producto", "product-tag",
    "produkt-schlagwort", "tag", "etiqueta", "schlagwort"})


class MapaProductos:
    """Los productos vivos del índice, cargados una vez, para mirar muchas URL
    seguidas (las fichas de un borrador, el catálogo entero en el Cuadre). Y,
    para decir qué es un enlace que no es un producto, las demás páginas vivas
    y los productos que ya no están."""

    def __init__(self, session: Session, *, sitios: list[str] | None = None) -> None:
        from app.services.web_forms import sitios as registro_sitios  # noqa: PLC0415

        registro = registro_sitios.cargar(session)
        self._idiomas = {s.clave: s.idiomas for s in registro.webs}
        filtros: list[Any] = [WebIndiceEntrada.sitio.in_(sitios)] if sitios else []
        self._por_ruta: dict[tuple[str, str], WebIndiceEntrada] = {}
        self._por_slug: dict[tuple[str, str], WebIndiceEntrada] = {}
        #: Las webs con algún producto en el índice: en las demás no se puede
        #: saber si un enlace es un producto (no tienen tienda, o no se leyeron).
        self.con_productos: set[str] = set()
        #: La base de las páginas de producto de cada web, la de sus productos
        #: del índice (`/producto/…`, `/product/…`).
        self._bases: dict[str, set[str]] = {}
        for f in _vivos(session, *filtros):
            self.con_productos.add(f.sitio)
            idiomas = self._idiomas.get(f.sitio, ())
            ruta = ruta_de(f.url, idiomas)
            self._por_ruta.setdefault((f.sitio, ruta), f)
            tramos = ruta.strip("/").split("/")
            if len(tramos) > 1:
                self._bases.setdefault(f.sitio, set()).add(tramos[0])
            slug = (f.slug or slug_de(f.url) or "").lower()
            if slug:
                self._por_slug.setdefault((f.sitio, unquote(slug)), f)
        self._otras: dict[tuple[str, str], str] = {}
        self._idos: set[tuple[str, str]] = set()
        #: Las webs con algo vivo en el índice (páginas, entradas o productos).
        self.con_indice: set[str] = set(self.con_productos)
        for sitio, url, titulo, ido_en in session.execute(select(
                WebIndiceEntrada.sitio, WebIndiceEntrada.url, WebIndiceEntrada.titulo,
                WebIndiceEntrada.ido_en).where(
                (WebIndiceEntrada.tipo != TIPO_PRODUCTO) | WebIndiceEntrada.ido_en.is_not(None),
                *filtros)):
            ruta = ruta_de(url, self._idiomas.get(sitio, ()))
            if ido_en is None:
                self.con_indice.add(sitio)
                self._otras.setdefault((sitio, ruta), titulo or "")
            else:
                self._idos.add((sitio, ruta))
        self._session = session
        self._slugs_fuera: dict[str, list[re.Pattern[str]]] = {}

    def _ruta(self, url: str | None) -> tuple[str, str] | None:
        sitio = sitio_de(url)
        if sitio is None:
            return None
        return sitio.clave, ruta_de(url, self._idiomas.get(sitio.clave, sitio.idiomas))

    def _de_producto(self, sitio: str, ruta: str) -> bool:
        """¿La ruta es de una página de producto (`/producto/<slug>`, la
        tienda, un producto con la categoría en la ruta)? Una categoría
        (`/categoria-producto/<slug>`) o la de contacto, no."""
        tramos = ruta.strip("/").split("/")
        return len(tramos) > 1 and bool(
            set(tramos[:-1]) & (BASES_PRODUCTO | self._bases.get(sitio, set())))

    def producto(self, url: str | None) -> WebIndiceEntrada | None:
        clave = self._ruta(url)
        if clave is None:
            return None
        fila = self._por_ruta.get(clave)
        if fila is None and clave not in self._otras and (
                self._de_producto(*clave) or clave[1].strip("/").count("/") == 0):
            # Por su slug, si la ruta es de producto o es solo el slug
            # (`/<slug>/`, que WordPress lleva al producto): no si es una
            # página del índice, ni una categoría con el mismo slug.
            fila = self._por_slug.get((clave[0], clave[1].rsplit("/", 1)[-1]))
        return fila

    def _de_servicio(self, sitio: str, ruta: str) -> bool:
        """¿Una página de servicio? Por su slug (contacto, buscador y las que
        el índice excluye por su slug: carrito, pago, mi cuenta, legales…) o
        por estar bajo una categoría o una etiqueta."""
        from app.services.webs import reglas  # noqa: PLC0415

        tramos = [t for t in ruta.strip("/").split("/") if t]
        if not tramos:
            return False
        if set(tramos[:-1]) & BASES_CATEGORIA or tramos[0] in BASES_CATEGORIA:
            return True
        slug = unquote(tramos[-1])
        if _SERVICIO_RE.search(slug):
            return True
        if self._de_producto(sitio, ruta):
            # Las reglas de slug del índice son para páginas y entradas: una
            # máquina «reacondicionada» no es una página de condiciones.
            return False
        if sitio not in self._slugs_fuera:
            try:
                self._slugs_fuera[sitio] = reglas.cargar(self._session).para(sitio).slugs
            except Exception:  # noqa: BLE001 — sin las reglas, las de partida
                self._slugs_fuera[sitio] = reglas.Reglas(
                    reglas=reglas._de_partida()).para(sitio).slugs
        return any(p.search(slug) for p in self._slugs_fuera[sitio])

    def motivo(self, url: str | None) -> str | None:
        """Por qué avisar del enlace `url` de una ficha (`MOTIVO_*`), o None si
        no hay que avisar: lleva a una página viva del índice (producto,
        página o entrada) que no es de servicio, es la portada, o no se puede
        saber (una web sin nada en el índice; una ruta de producto de una web
        sin productos en el índice)."""
        clave = self._ruta(url)
        if clave is None or self.producto(url) is not None:
            return None
        sitio, ruta = clave
        if set(parse_qs(urlsplit(str(url)).query)) & _CONSULTA_SERVICIO:
            return MOTIVO_SERVICIO      # una búsqueda o la cesta
        if not ruta.strip("/"):
            return None                 # la portada
        if self._de_servicio(sitio, ruta):
            return MOTIVO_SERVICIO
        if clave in self._otras:
            return None                 # una página o una entrada de la web: vale
        if not self.juzgable(url):
            return None
        return MOTIVO_IDO if clave in self._idos else MOTIVO_FUERA

    def juzgable(self, url: str | None) -> bool:
        """¿Se puede decir si `url` está en el índice? Hace falta que su web
        tenga algo en él y, para una ruta de producto, productos."""
        clave = self._ruta(url)
        if clave is None or clave[0] not in self.con_indice:
            return False
        return clave[0] in self.con_productos or not self._de_producto(*clave)

    def titulo(self, url: str | None) -> str | None:
        """El título de la página (no producto) del índice a la que lleva `url`."""
        clave = self._ruta(url)
        return (self._otras.get(clave) or None) if clave else None

    def no_es_producto(self, url: str | None) -> bool | None:
        """True si `url` es de una web con productos en el índice y no es
        ninguno; False si lo es; None si no se puede saber."""
        clave = self._ruta(url)
        if clave is None or clave[0] not in self.con_productos:
            return None
        return self.producto(url) is None


def dominios_indexados(session: Session) -> set[str]:
    """Las webs (su clave) con algún producto en el índice."""
    return MapaProductos(session).con_productos


def enlace_no_es_producto(session: Session, url: str | None, *,
                          mapa: MapaProductos | None = None) -> bool | None:
    """¿El enlace de una ficha del catálogo NO es una página de producto del
    índice? True si es de una web con productos indexados y no casa con
    ninguno; False si casa; None si no se puede saber."""
    return (mapa or MapaProductos(session)).no_es_producto(url)
