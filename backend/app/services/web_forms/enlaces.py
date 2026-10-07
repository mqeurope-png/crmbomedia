"""Enlaces en los textos de un formulario (`label` y `help_text`).

Se escriben en formato markdown, `[texto](https://…)` o `[texto](/ruta/)`,
y salen como `<a target="_blank" rel="noopener noreferrer">`. Solo valen
URLs `http`/`https` y rutas que empiecen por `/` (la misma ruta sirve en
cada web de cada marca sin escribir el dominio). Todo lo demás, incluido un
`[x](javascript:…)`, se escapa exactamente como antes.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_ENLACE = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s<>\"']+)\)")


def url_segura(url: str) -> bool:
    if url.startswith("/"):
        # «//host» y «/\host» los navegadores los tratan como otro dominio.
        return not url.startswith(("//", "/\\"))
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)


def texto_con_enlaces(texto: str | None) -> str:
    """El texto escapado como HTML, con los enlaces markdown seguros
    convertidos en `<a>`. Sin enlaces, idéntico a `html.escape(texto)`."""
    texto = texto or ""
    partes: list[str] = []
    pos = 0
    for m in _ENLACE.finditer(texto):
        partes.append(html.escape(texto[pos:m.start()]))
        etiqueta, url = m.group(1), m.group(2)
        if url_segura(url):
            partes.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                          f'rel="noopener noreferrer">{html.escape(etiqueta)}</a>')
        else:
            partes.append(html.escape(m.group(0)))
        pos = m.end()
    partes.append(html.escape(texto[pos:]))
    return "".join(partes)


def texto_plano(texto: str | None) -> str:
    """El texto sin la sintaxis de enlace (`[texto](url)` → `texto`), para
    los sitios donde no cabe un enlace (atributos, títulos)."""
    return _ENLACE.sub(lambda m: m.group(1), texto or "")
