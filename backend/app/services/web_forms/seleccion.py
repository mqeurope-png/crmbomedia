"""Elegir el formulario de una web según el idioma de la página.

Cada web del grupo lleva UN solo código de inserción
(`/forms/embed/mbolasers.js`): el navegador dice en qué idioma está la
página y aquí se elige el formulario de esa web en ese idioma. Si no hay
uno en ese idioma se usa el de respaldo del sitio (`is_site_default`), y si
tampoco, el castellano, el inglés o el primero por idioma — pero nunca se
deja la página sin formulario mientras la web tenga alguno activo.

La web se identifica por la clave del slug, no por la marca: `artisjet` es
la marca de dos webs distintas (ver `sitios`).
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.web_forms import WebForm
from app.services.web_forms.sitios import SEPARADOR, clave_de_sitio
from app.services.web_forms.textos import IDIOMA_BASE, normalizar_idioma

#: Orden de preferencia cuando la web no tiene el idioma pedido ni
#: formulario de respaldo marcado.
IDIOMAS_DE_RESPALDO = (IDIOMA_BASE, "en")


def formularios_de_sitio(session: Session, clave: str) -> list[WebForm]:
    """Los formularios activos de una web, por idioma (orden estable)."""
    clave = (clave or "").strip().lower()
    if not clave:
        return []
    filas = session.scalars(
        select(WebForm)
        .where(
            WebForm.is_active.is_(True),
            or_(WebForm.slug == clave, WebForm.slug.like(f"{clave}{SEPARADOR}%")),
        )
        .order_by(WebForm.language, WebForm.slug)
    ).all()
    # El LIKE es un filtro barato; la clave exacta la decide `clave_de_sitio`
    # (así `artisjet-es` nunca se lleva los de `artisjet-eu`).
    return [f for f in filas if clave_de_sitio(f.slug) == clave]


def formulario_de_sitio(
    session: Session, clave: str, idioma: str | None
) -> WebForm | None:
    """El formulario que toca servir, o None si la web no tiene ninguno
    activo (la página lo dirá en la consola, no se queda muda)."""
    activos = formularios_de_sitio(session, clave)
    if not activos:
        return None
    pedido = normalizar_idioma(idioma)
    por_idioma: dict[str, WebForm] = {}
    for f in activos:
        por_idioma.setdefault(normalizar_idioma(f.language), f)
    if pedido in por_idioma:
        return por_idioma[pedido]
    respaldo = next((f for f in activos if f.is_site_default), None)
    if respaldo is not None:
        return respaldo
    for codigo in IDIOMAS_DE_RESPALDO:
        if codigo in por_idioma:
            return por_idioma[codigo]
    return activos[0]
