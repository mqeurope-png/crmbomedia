"""CRM-COMPOSITOR-V2.2 — sanitización del HTML del compositor.

El editor (TinyMCE) es cliente: cualquier HTML puede llegar al backend
(paste desde Word, DevTools, un cliente API). Antes de enviar por Gmail y
de persistir, `sanitize_email_html` pasa el body por bleach con whitelist
estricta de tags/atributos/estilos — fuera `<script>`, `<iframe>`,
handlers `on*`, `javascript:` URLs, etc. El contenido de texto se conserva.

`html_to_text` genera el fallback text/plain del MIME multipart/alternative
cuando el caller solo manda HTML (html2text con bodywidth=0 → líneas sin
recortar, legible en clientes de texto).
"""
from __future__ import annotations

import html as html_mod
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer

# bleach con strip=True elimina el TAG pero conserva su texto interior —
# para script/style/iframe/object/embed queremos fuera el bloque ENTERO
# (su "texto" es código, no contenido).
_DROP_WITH_CONTENT = re.compile(
    r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

ALLOWED_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s", "strike",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "a", "img", "span", "div", "blockquote", "code", "pre", "hr",
    "table", "tr", "td", "th", "thead", "tbody", "tfoot",
    "sub", "sup", "small", "font",
]

ALLOWED_ATTRS = {
    "*": ["style", "class"],
    "a": ["href", "target", "rel", "title"],
    "img": ["src", "alt", "width", "height", "title"],
    "td": ["colspan", "rowspan", "align", "valign"],
    "th": ["colspan", "rowspan", "align", "valign"],
    "table": ["border", "cellpadding", "cellspacing", "width", "align"],
    "font": ["color", "face", "size"],
}

ALLOWED_STYLES = [
    "color", "background-color", "font-weight", "font-style",
    "text-decoration", "text-align", "font-size", "font-family",
    "line-height", "margin", "margin-left", "margin-right", "margin-top",
    "margin-bottom", "padding", "padding-left", "padding-right",
    "padding-top", "padding-bottom", "border", "border-collapse", "width",
    "height", "max-width", "vertical-align", "list-style-type",
]

# `http/https/mailto/tel` para <a>; `cid:` y data-images NO se permiten en
# href. Para <img src> bleach usa la misma lista de protocolos — añadimos
# `cid` y `data` (imágenes pegadas como data-URI antes de subirse) porque
# el swap a CID del envío las necesita intactas.
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel", "cid", "data"]

_css_sanitizer = CSSSanitizer(allowed_css_properties=ALLOWED_STYLES)

# --- El correo compuesto de BoHub (`correo_compuesto.generador`) -----------
# Un documento entero maquetado con tablas, con los atributos de maquetación
# de correo (`role`, `bgcolor`, anchos de celda) y una hoja de estilo: la de
# las columnas que se apilan en el móvil. Con la lista de arriba saldría
# deshecho, así que se reconoce por su marca y pasa por su propia lista: la
# de siempre más lo que el generador usa. La cabecera y la hoja de estilo las
# vuelve a poner el generador (nunca las del HTML que llega): forjar la marca
# no da más que esta lista, que tampoco deja scripts, manejadores ni URL
# `javascript:`.
_COMPUESTO_RE = re.compile(
    r'<meta\s+name="generator"\s+content="BoHub correo compuesto"\s*/?>', re.IGNORECASE)
_IDIOMA_DOC_RE = re.compile(r'<html\b[^>]*\blang="([a-z]{2})"', re.IGNORECASE)
_CUERPO_DOC_RE = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.IGNORECASE | re.DOTALL)
_CABECERA_DOC_RE = re.compile(r"<(head|title)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TITULO_DOC_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
# Los comentarios, fuera, salvo las marcas de la firma (un comentario
# condicional de Outlook podría llevar cualquier cosa).
_COMENTARIO_AJENO_RE = re.compile(
    r"<!--(?!crmbo:signature-->|/crmbo:signature-->).*?-->", re.DOTALL)

ATTRS_COMPUESTO = {
    **ALLOWED_ATTRS,
    "img": [*ALLOWED_ATTRS["img"], "border"],
    "table": [*ALLOWED_ATTRS["table"], "role", "bgcolor"],
    "tr": ["align", "valign", "bgcolor"],
    # `background`: la imagen de fondo del hero (Outlook enseña `bgcolor`).
    "td": [*ALLOWED_ATTRS["td"], "width", "height", "bgcolor", "background"],
    "th": [*ALLOWED_ATTRS["th"], "width", "height", "bgcolor"],
}
STYLES_COMPUESTO = [
    *ALLOWED_STYLES,
    "display", "border-radius", "border-top", "border-bottom", "border-left", "border-right",
    "border-color", "border-style", "border-width", "border-spacing", "letter-spacing",
    "text-transform", "white-space", "overflow", "opacity", "max-height", "min-width",
    "outline", "box-sizing", "word-break", "table-layout", "mso-table-lspace",
    "mso-table-rspace", "mso-line-height-rule", "background-image", "background-size",
    "background-position", "background-repeat",
]
_css_compuesto = CSSSanitizer(allowed_css_properties=STYLES_COMPUESTO)


def es_correo_compuesto(html: str | None) -> bool:
    """El HTML es un correo compuesto de BoHub (lleva su marca)."""
    return bool(html) and bool(_COMPUESTO_RE.search(html or ""))


def sanitize_correo_compuesto(html: str) -> str:
    """El correo compuesto con su lista blanca: el cuerpo limpio dentro del
    documento que pone el generador (cabecera, marca y hoja de estilo
    propias). Aplicarlo dos veces da lo mismo que una."""
    from app.services.correo_compuesto.generador import documento  # noqa: PLC0415

    idioma = _IDIOMA_DOC_RE.search(html)
    titulo = _TITULO_DOC_RE.search(html)
    cuerpo = _CUERPO_DOC_RE.search(html)
    contenido = cuerpo.group(1) if cuerpo else html
    contenido = _CABECERA_DOC_RE.sub("", _DROP_WITH_CONTENT.sub("", contenido))
    limpio = bleach.clean(
        contenido,
        tags=ALLOWED_TAGS,
        attributes=ATTRS_COMPUESTO,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_compuesto,
        strip=True,
        strip_comments=False,
    )
    limpio = _COMENTARIO_AJENO_RE.sub("", limpio)
    return documento(limpio, idioma=(idioma.group(1).lower() if idioma else ""),
                     asunto=html_mod.unescape(titulo.group(1)).strip() if titulo else None)


def sanitize_email_html(html: str | None) -> str | None:
    """Devuelve el HTML con la whitelist aplicada. None/"" pasan tal cual.

    `strip=True`: los tags no permitidos se eliminan pero su TEXTO se
    conserva (un paste con `<script>alert(1)</script>hola` deja `hola`,
    aunque el contenido del script sí se descarta por bleach al ser
    contenido ejecutable). Un correo compuesto de BoHub pasa por la suya
    (`sanitize_correo_compuesto`)."""
    if not html:
        return html
    if es_correo_compuesto(html):
        return sanitize_correo_compuesto(html)
    html = _DROP_WITH_CONTENT.sub("", html)
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=True,
        strip_comments=False,  # <!--crmbo:signature--> delimita la firma
    )


def html_to_text(html: str) -> str:
    """Fallback text/plain legible a partir del HTML (para el
    multipart/alternative). bodywidth=0 = sin hard-wrap."""
    import html2text  # noqa: PLC0415 — import perezoso, solo en envíos

    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_emphasis = False
    return converter.handle(html).strip()
