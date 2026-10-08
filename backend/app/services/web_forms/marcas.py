"""Marcas de los formularios: de dónde viene un lead, dicho en claro.

Con 25 formularios repartidos entre ocho webs y seis idiomas, «web_form» no
dice nada. El origen de cada lead lleva la marca y el idioma del formulario
(`web_form:<marca>:<idioma>`), y aquí se traduce a algo legible: «Nuevo lead
desde mboprinters.com (alemán)».

`WEBS` es la lista de webs conocidas. Una marca que no esté se enseña tal
cual (nunca un código inventado): al añadir una web nueva, su dominio se
escribe aquí y aparece en la ficha, en los filtros y en los avisos.
"""

from __future__ import annotations

from app.services.web_forms.textos import nombre_idioma, normalizar_idioma

#: Marca (`web_forms.brand`) → la web donde está publicado el formulario.
WEBS: dict[str, str] = {
    "boprint": "boprint.net",
    "mboprinters": "mboprinters.com",
    "mbolasers": "mbolasers.com",
    "pimpam-vending": "pimpam-vending.com",
    "artisjet-printers.eu": "artisjet-printers.eu",
    "artisjet-spain": "artisjet (España)",
    "fluxlasers": "fluxlasers",
    "mqeurope": "mqeurope",
}

#: Prefijo del origen de un lead de formulario: `web_form:<marca>:<idioma>`.
ORIGEN_PREFIJO = "web_form"


def web_de_marca(marca: str | None) -> str:
    """La web de una marca, o la marca misma si no está en la lista."""
    marca = (marca or "").strip()
    return WEBS.get(marca, marca) or "web sin marca"


def origen_de(marca: str | None, idioma: str | None) -> str:
    """El `origin_account_id` de un formulario: identifica marca e idioma, y
    se puede filtrar por cualquiera de los dos (`web_form:mbolasers:*`)."""
    marca = (marca or "").strip().lower() or "sin-marca"
    return f"{ORIGEN_PREFIJO}:{marca}:{normalizar_idioma(idioma)}"


#: Lo que se lee en «Origen del lead» de la ficha. El prefijo es estable
#: (una regla o un workflow puede buscar «Formulario web»).
ORIGEN_LEGIBLE_PREFIJO = "Formulario web"


def origen_legible(marca: str | None, idioma: str | None) -> str:
    """`Contact.origin` de un lead de formulario: «Formulario web ·
    mboprinters.com (alemán)». Cabe en los 120 caracteres de la columna."""
    texto = f"{ORIGEN_LEGIBLE_PREFIJO} · {web_de_marca(marca)} ({nombre_idioma(idioma)})"
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
    origen no es de un formulario con marca e idioma."""
    partes = partes_de_origen(origen)
    if partes is None:
        return None
    marca, idioma = partes
    return f"{web_de_marca(marca)} ({nombre_idioma(idioma)})"
