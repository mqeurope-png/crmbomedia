"""Elegir el formulario de una marca según el idioma de la página.

Cada web del grupo lleva UN solo código de inserción
(`/forms/embed/mbolasers.js`): el navegador dice en qué idioma está la
página y aquí se elige el formulario de esa marca en ese idioma. Si no hay
uno en ese idioma se usa el de respaldo de la marca (`is_brand_default`), y
si tampoco, el castellano, el inglés o el primero por idioma — pero nunca
se deja la página sin formulario mientras la marca tenga alguno activo.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.web_forms import WebForm
from app.services.web_forms.textos import IDIOMA_BASE, normalizar_idioma

#: Orden de preferencia cuando la marca no tiene el idioma pedido ni
#: formulario de respaldo marcado.
IDIOMAS_DE_RESPALDO = (IDIOMA_BASE, "en")


def formularios_de_marca(session: Session, marca: str) -> list[WebForm]:
    """Los formularios activos de una marca, por idioma (orden estable)."""
    marca = (marca or "").strip()
    if not marca:
        return []
    filas = session.scalars(
        select(WebForm)
        .where(func.lower(WebForm.brand) == marca.lower(), WebForm.is_active.is_(True))
        .order_by(WebForm.language, WebForm.slug)
    ).all()
    return list(filas)


def formulario_de_marca(
    session: Session, marca: str, idioma: str | None
) -> WebForm | None:
    """El formulario que toca servir, o None si la marca no tiene ninguno
    activo (la página lo dirá en la consola, no se queda muda)."""
    activos = formularios_de_marca(session, marca)
    if not activos:
        return None
    pedido = normalizar_idioma(idioma)
    por_idioma = {normalizar_idioma(f.language): f for f in activos}
    if pedido in por_idioma:
        return por_idioma[pedido]
    respaldo = next((f for f in activos if f.is_brand_default), None)
    if respaldo is not None:
        return respaldo
    for codigo in IDIOMAS_DE_RESPALDO:
        if codigo in por_idioma:
            return por_idioma[codigo]
    return activos[0]
