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


def sanitize_email_html(html: str | None) -> str | None:
    """Devuelve el HTML con la whitelist aplicada. None/"" pasan tal cual.

    `strip=True`: los tags no permitidos se eliminan pero su TEXTO se
    conserva (un paste con `<script>alert(1)</script>hola` deja `hola`,
    aunque el contenido del script sí se descarta por bleach al ser
    contenido ejecutable)."""
    if not html:
        return html
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
