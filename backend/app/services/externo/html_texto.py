"""De una página HTML al texto que importa.

Sin `bs4` ni `lxml` (no están en el backend): un árbol mínimo con el
`html.parser` de la biblioteca estándar, que aguanta HTML mal cerrado.

- `extraer(html)`: quita lo que no es contenido (scripts, estilos, menús,
  cabecera, pie, barras laterales, formularios, avisos de cookies, la cabecera
  y el pie de Elementor…) y se queda con el bloque principal (`article`,
  `main`, `[role=main]`, el cuerpo de un artículo de Zendesk, la página de
  Elementor…). Devuelve el título y el texto con sus saltos de párrafo, sin
  marcas.
- `limpiar_texto_wp(texto)`: la basura que deja WordPress en el contenido de
  la REST (shortcodes de WPBakery sin pintar, reglas CSS sueltas de un
  carrusel, restos de comentarios rotos).
- `es_cascara(html, texto)`: la página se pinta con JavaScript y el HTML no
  trae el contenido. Si es así, quien llama no debe creer que tiene algo.
- `fragmentos(texto, palabras)`: los párrafos que más tienen que ver con la
  búsqueda, recortados, para no meter la página entera en el prompt.
"""
from __future__ import annotations

import html as html_mod
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

#: Etiquetas cuyo contenido nunca es texto de la página.
_FUERA = frozenset({
    "script", "style", "noscript", "template", "svg", "iframe", "form", "button", "nav",
    "footer", "aside", "select", "input", "textarea", "object", "embed", "canvas", "video",
    "audio", "picture", "head", "link", "meta",
})
#: `header` solo se quita fuera del bloque principal (un `article` puede tener
#: su `header` con el título).
_ROLES_FUERA = frozenset({"navigation", "banner", "contentinfo", "complementary", "search",
                          "dialog", "alert"})
#: Palabras de `class`/`id` que delatan navegación, pie, barras o avisos.
_CLASES_FUERA = frozenset({
    "menu", "nav", "navbar", "navigation", "main-menu", "submenu", "breadcrumb",
    "breadcrumbs", "footer", "site-footer", "sidebar", "widget-area", "widgets", "cookie",
    "cookies", "cookie-notice", "cookie-banner", "cookie-law-info-bar", "share", "sharing",
    "social", "social-share", "related", "related-posts", "comments", "comment-respond",
    "skip-link", "site-header", "topbar", "top-bar", "toolbar", "pagination", "newsletter",
    "popup", "modal", "language-switcher", "lang-switcher", "wpml-ls", "search-form",
    "article-votes", "article-subscribe", "article-relatives", "article-return-to-top",
    "recent-articles", "related-articles", "article-sidebar", "sub-nav",
})
#: Elementor: cabecera, pie y emergentes.
_ELEMENTOR_FUERA = frozenset({"header", "footer", "popup"})
#: Bloque principal, por orden de preferencia.
_ELEMENTOR_PAGINA = frozenset({"wp-page", "single-page", "single-post", "single", "product",
                               "wp-post"})
_CLASES_PRINCIPAL = ("article-body", "entry-content", "post-content", "page-content",
                     "article-content", "content-area", "site-content")
#: Bloques que llevan salto de párrafo.
_BLOQUE = frozenset({
    "p", "div", "section", "article", "main", "header", "li", "ul", "ol", "table", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "dl", "dt", "dd", "figure",
    "figcaption", "br", "hr", "address", "details", "summary",
})
_VACIAS = frozenset({"br", "hr", "img", "input", "meta", "link", "source", "area", "base",
                     "col", "embed", "param", "track", "wbr"})


class _Nodo:
    __slots__ = ("tag", "attrs", "hijos", "padre")

    def __init__(self, tag: str, attrs: dict[str, str], padre: _Nodo | None):
        self.tag = tag
        self.attrs = attrs
        self.hijos: list[_Nodo | str] = []
        self.padre = padre


class _Arbol(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raiz = _Nodo("#raiz", {}, None)
        self.actual = self.raiz
        self.titulo = ""
        self._en_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        nodo = _Nodo(tag, {k.lower(): (v or "") for k, v in attrs}, self.actual)
        self.actual.hijos.append(nodo)
        if tag == "title":
            self._en_title = True
        if tag not in _VACIAS:
            self.actual = nodo

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.actual.hijos.append(_Nodo(tag.lower(), {k.lower(): (v or "") for k, v in attrs},
                                       self.actual))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._en_title = False
        nodo: _Nodo | None = self.actual
        while nodo is not None and nodo.tag != tag:
            nodo = nodo.padre
        if nodo is not None and nodo.padre is not None:
            self.actual = nodo.padre

    def handle_data(self, data: str) -> None:
        if self._en_title:
            self.titulo += data
            return
        self.actual.hijos.append(data)


def _tokens(nodo: _Nodo) -> set[str]:
    valores = f"{nodo.attrs.get('class', '')} {nodo.attrs.get('id', '')}".lower()
    return {t for t in re.split(r"[\s]+", valores) if t}


def _fuera(nodo: _Nodo, *, dentro_principal: bool) -> bool:
    if nodo.tag in _FUERA:
        return True
    if nodo.tag == "header" and not dentro_principal:
        return True
    if nodo.attrs.get("role", "").lower() in _ROLES_FUERA:
        return True
    if nodo.attrs.get("aria-hidden", "").lower() == "true":
        return True
    if nodo.attrs.get("data-elementor-type", "").lower() in _ELEMENTOR_FUERA:
        return True
    tokens = _tokens(nodo)
    if tokens & _CLASES_FUERA:
        return True
    # `menu-principal`, `footer-widgets`, `site-navigation`…
    return any(t.startswith(("menu-", "nav-", "footer-", "sidebar-", "cookie-"))
               or t.endswith(("-menu", "-nav", "-navigation", "-footer", "-sidebar"))
               for t in tokens)


def _buscar(nodo: _Nodo, pred) -> _Nodo | None:  # noqa: ANN001
    pila: list[_Nodo] = [nodo]
    while pila:
        actual = pila.pop(0)
        if actual is not nodo and pred(actual):
            return actual
        pila.extend(h for h in actual.hijos if isinstance(h, _Nodo))
    return None


def _principal(raiz: _Nodo) -> _Nodo:
    for pred in (
        lambda n: n.attrs.get("data-elementor-type", "").lower() in _ELEMENTOR_PAGINA,
        lambda n: n.tag == "article",
        lambda n: n.tag == "main" or n.attrs.get("role", "").lower() == "main",
        lambda n: bool(_tokens(n) & set(_CLASES_PRINCIPAL)),
        lambda n: n.attrs.get("id", "").lower() in ("content", "main-content", "primary"),
        lambda n: n.tag == "body",
    ):
        hallado = _buscar(raiz, pred)
        if hallado is not None:
            return hallado
    return raiz


def _texto(nodo: _Nodo, salida: list[str], *, dentro_principal: bool) -> None:
    for hijo in nodo.hijos:
        if isinstance(hijo, str):
            salida.append(hijo)
            continue
        if _fuera(hijo, dentro_principal=dentro_principal):
            continue
        bloque = hijo.tag in _BLOQUE
        if bloque:
            salida.append("\n")
        if hijo.tag == "li":
            salida.append("- ")
        elif hijo.tag in ("td", "th"):
            salida.append(" | ")
        elif hijo.tag == "img" and hijo.attrs.get("alt"):
            salida.append(f" {hijo.attrs['alt']} ")
        _texto(hijo, salida, dentro_principal=dentro_principal)
        if bloque:
            salida.append("\n")


def normalizar_espacios(texto: str) -> str:
    texto = texto.replace("\r", "").replace("\xa0", " ")
    lineas = [re.sub(r"[ \t​]+", " ", linea).strip() for linea in texto.split("\n")]
    salida: list[str] = []
    for linea in lineas:
        if linea in ("|", "-"):
            continue
        if not linea:
            if salida and salida[-1] != "":
                salida.append("")
            continue
        salida.append(linea)
    return "\n".join(salida).strip()


@dataclass
class Extraccion:
    titulo: str
    texto: str
    #: El HTML traía más texto fuera del bloque principal que dentro (pista
    #: de una extracción mala o de una cáscara).
    principal_encontrado: bool


def extraer(html: str, *, todo: bool = False) -> Extraccion:
    """El título y el texto principal de una página. Con `todo=True`, todo el
    contenido (sin menús, pies ni laterales) en vez del bloque principal: para
    un fragmento de HTML que ya es contenido (la REST de WordPress, el cuerpo
    de un artículo de Zendesk)."""
    arbol = _Arbol()
    try:
        arbol.feed(html or "")
        arbol.close()
    except Exception:  # noqa: BLE001 — HTML imposible: se devuelve lo que haya
        pass
    nodo = arbol.raiz if todo else _principal(arbol.raiz)
    salida: list[str] = []
    _texto(nodo, salida, dentro_principal=not todo and nodo is not arbol.raiz)
    texto = normalizar_espacios(html_mod.unescape("".join(salida)))
    titulo = normalizar_espacios(html_mod.unescape(arbol.titulo))
    return Extraccion(titulo=titulo, texto=texto, principal_encontrado=nodo is not arbol.raiz)


def html_a_texto(html: str) -> str:
    """El texto de un fragmento de HTML de contenido (sin buscar bloque)."""
    return extraer(html, todo=True).texto


#: `[vc_row][vc_column width=»1/3″]…`: shortcodes que la REST deja sin pintar.
_SHORTCODE_RE = re.compile(r"\[/?[a-z][a-z0-9_-]*(?:\s[^\]]{0,400})?\]", re.IGNORECASE)
#: `#wpcpro-wrapper-2990 .x {color:#333;font-size:14px}`: CSS suelto como texto.
_CSS_RE = re.compile(r"(?:[#.@][^{}\n]{0,400})?\{[^{}]*:[^{}]*\}")
_COMENTARIO_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
#: Restos de comentarios HTML rotos por `wpautop`: `-->`, `&lt;!--`, `====`.
_RESTOS_RE = re.compile(r"(?:<!--|-->|={3,}|&lt;!--|--&gt;)")


def limpiar_texto_wp(texto: str) -> str:
    texto = _SHORTCODE_RE.sub(" ", texto or "")
    texto = _COMENTARIO_RE.sub(" ", texto)
    texto = _CSS_RE.sub(" ", texto)
    texto = _RESTOS_RE.sub(" ", texto)
    return normalizar_espacios(texto)


_SPA_RE = re.compile(
    r'id=["\'](?:root|app|__next|__nuxt)["\']|ng-app|data-reactroot|window\.__INITIAL_STATE__'
    r"|<noscript>[^<]{0,200}(?:javascript|JavaScript)", re.IGNORECASE)


def es_cascara(html: str, texto: str, *, minimo: int = 200) -> bool:
    """¿El HTML es una cáscara que se pinta con JavaScript? Poco texto y señales
    de una aplicación de una sola página (o muchos scripts y casi nada más)."""
    if len(texto or "") >= minimo:
        return False
    if _SPA_RE.search(html or ""):
        return True
    scripts = len(re.findall(r"<script\b", html or "", re.IGNORECASE))
    return scripts >= 5 or len(texto or "") < 40


def plano(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar."""
    s = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def fragmentos(texto: str, palabras: list[str], *, max_chars: int = 700,
               max_fragmentos: int = 2) -> str:
    """Los párrafos con más palabras de la búsqueda (en su orden), hasta
    `max_chars`. Sin coincidencias, el principio del texto."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n|\n", texto or "") if p.strip()]
    if not parrafos:
        return ""
    claves = [plano(p) for p in palabras if len(plano(p).strip()) >= 2]
    if claves:
        puntuados = sorted(
            ((sum(plano(p).count(k) for k in claves), i) for i, p in enumerate(parrafos)),
            key=lambda x: (-x[0], x[1]))
        elegidos = sorted(i for puntos, i in puntuados[:max_fragmentos] if puntos > 0)
    else:
        elegidos = []
    if not elegidos:
        elegidos = list(range(min(max_fragmentos, len(parrafos))))
    salida = ""
    for i in elegidos:
        trozo = parrafos[i]
        if salida:
            trozo = f"\n…\n{trozo}"
        if len(salida) + len(trozo) > max_chars:
            hueco = max_chars - len(salida)
            if hueco > 80:
                corte = trozo[:hueco].rsplit(" ", 1)[0]
                salida += f"{corte}…"
            break
        salida += trozo
    return salida.strip()
