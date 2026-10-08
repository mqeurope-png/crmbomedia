"""De qué web viene un lead, dicho en claro.

Con 25 formularios en ocho webs y seis idiomas, «web_form» no dice nada. El
origen de cada lead lleva la WEB y el idioma del formulario
(`web_form:<sitio>:<idioma>`), y aquí se traduce a algo legible: «Nuevo lead
desde mboprinters.com (alemán)».

La web NO se deduce de `web_forms.brand`: una misma marca puede estar en dos
webs distintas (`artisjet` lo está en artisjet-spain.es y en
artisjet-printers.eu). Se deduce del **slug**, que es la clave pública y
única de cada formulario: lo que va antes de `-contacto`
(`artisjet-es-contacto-de` → `artisjet-es`).

`WEBS` es la lista de webs conocidas. Un sitio que no esté se enseña con su
propia clave, nunca con un dominio inventado: al publicar una web nueva, su
dominio se escribe aquí y aparece en la ficha, en los filtros y en los
avisos.
"""

from __future__ import annotations

from app.services.web_forms.textos import nombre_idioma, normalizar_idioma

#: Clave del sitio (lo que va antes de `-contacto` en el slug) → su web.
WEBS: dict[str, str] = {
    "artisjet-es": "artisjet-spain.es",
    "artisjet-eu": "artisjet-printers.eu",
    "boprint": "boprint.net",
    "fluxlasers": "fluxlasers.es",
    "mboprinters": "mboprinters.com",
    "mbolasers": "mbolasers.com",
    # mqeurope sirve con www: sin www redirige.
    "mqeurope": "www.mqeurope.com",
    "pimpam": "pimpam-vending.com",
}

#: Separador del slug: `<sitio>-contacto[-<idioma>]`.
SEPARADOR = "-contacto"

#: Prefijo del origen de un lead de formulario: `web_form:<sitio>:<idioma>`.
ORIGEN_PREFIJO = "web_form"

#: Lo que se lee en «Origen del lead» de la ficha. El prefijo es estable
#: (una regla o un workflow puede buscar «Formulario web»).
ORIGEN_LEGIBLE_PREFIJO = "Formulario web"


def clave_de_sitio(slug: str | None) -> str:
    """La web a la que pertenece un formulario, a partir de su slug.

    `artisjet-es-contacto-de` → `artisjet-es`; `boprint-contacto` →
    `boprint`. Un slug que no siga el convenio es su propio sitio (se
    enseñará con su nombre, sin dominio inventado)."""
    slug = (slug or "").strip().lower()
    if not slug:
        return "sin-web"
    return slug.split(SEPARADOR, 1)[0] or slug


def web_de_sitio(clave: str | None) -> str:
    """La web de un sitio, o su clave si no está en la lista."""
    clave = (clave or "").strip()
    return WEBS.get(clave, clave) or "web sin identificar"


def web_de_formulario(slug: str | None) -> str:
    return web_de_sitio(clave_de_sitio(slug))


def origen_de(slug: str | None, idioma: str | None) -> str:
    """El `origin_account_id` de un formulario: identifica la web y el
    idioma, y se puede filtrar por cualquiera de los dos."""
    return f"{ORIGEN_PREFIJO}:{clave_de_sitio(slug)}:{normalizar_idioma(idioma)}"


def origen_legible(slug: str | None, idioma: str | None) -> str:
    """`Contact.origin` de un lead: «Formulario web · mboprinters.com
    (alemán)». Cabe en los 120 caracteres de la columna."""
    texto = (f"{ORIGEN_LEGIBLE_PREFIJO} · {web_de_formulario(slug)} "
             f"({nombre_idioma(idioma)})")
    return texto[:120]


def partes_de_origen(origen: str | None) -> tuple[str, str] | None:
    """`web_form:mbolasers:nl` → `("mbolasers", "nl")`. None si no es uno de
    estos orígenes (incluye el formato antiguo `web_form:<slug>`)."""
    trozos = (origen or "").split(":")
    if len(trozos) != 3 or trozos[0] != ORIGEN_PREFIJO:
        return None
    return trozos[1], trozos[2]


def etiqueta_de_origen(origen: str | None) -> str | None:
    """Lo que se lee en la ficha: «mboprinters.com (alemán)». None si el
    origen no es de un formulario con web e idioma."""
    partes = partes_de_origen(origen)
    if partes is None:
        return None
    sitio, idioma = partes
    return f"{web_de_sitio(sitio)} ({nombre_idioma(idioma)})"
