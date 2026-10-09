"""El remitente del correo preparado: la dirección de la web por la que entró
el lead, la misma que firma el acuse de recibo (`sitios.REMITENTES`).

La web sale de la clave del slug del formulario, nunca del campo `brand`
(`artisjet` es la marca de dos webs distintas). Para los leads de Agile, la
cuenta de origen indica la marca: la configuración mapea cuenta → web.
"""
from __future__ import annotations

from typing import Any

from app.services.web_forms.sitios import REMITENTES, marca_de_sitio


def sitio_de_lead(
    *, sitio: str | None, cuenta_agile: str | None, config: dict[str, Any] | None = None,
) -> str | None:
    """La clave de la web del lead: la del formulario, o la que la
    configuración asigna a la cuenta de Agile."""
    if sitio:
        return sitio
    if cuenta_agile:
        por_cuenta = (config or {}).get("por_cuenta_agile") or {}
        return por_cuenta.get(cuenta_agile) or None
    return None


def remitente_de_sitio(sitio: str | None, config: dict[str, Any] | None = None) -> str | None:
    """El remitente de una web: el de la configuración si lo hay, y si no el
    de `sitios.REMITENTES`. `None` si la web no se conoce: inventarse un
    `info@` de un dominio que no firma lo mandaría a spam."""
    if not sitio:
        return None
    por_web = (config or {}).get("por_web") or {}
    return (por_web.get(sitio) or REMITENTES.get(sitio) or "").strip() or None


def remitente_para(
    *, sitio: str | None, cuenta_agile: str | None, config: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """`(remitente, nombre_de_marca)` del lead."""
    clave = sitio_de_lead(sitio=sitio, cuenta_agile=cuenta_agile, config=config)
    correo = remitente_de_sitio(clave, config)
    return correo, (marca_de_sitio(clave) if clave else None)
