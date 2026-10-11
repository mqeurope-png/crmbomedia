"""Los enlaces que el asistente puede poner delante de un cliente, resueltos
de una vez: la ruta de soporte de su web en su idioma, la página de producto
de su interés y, si es un cliente de FLUX, el destino que le toca (la KB de
FLUX en su idioma y modelo, o nuestro soporte).

Es la única puerta: el asistente y la pantalla de prueba («Probar con…»)
llaman aquí. Lo que no sale de aquí no se enlaza.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.conocimiento import flux, rutas
from app.services.conocimiento.config import configuracion
from app.services.conocimiento.rutas import Enlace

WEB_FLUX = "fluxlasers"


def enlaces_para(
    session: Session, *, sitio: str | None, idioma: str | None, interes: str | None = None,
    texto: str | None = None, modelo: str | None = None, con_articulos: bool = True,
) -> dict[str, Any]:
    """`{"soporte", "producto", "flux"}`, cada uno un enlace (dict) o None.

    `texto` es lo que escribe el cliente (para la intención y el modelo);
    `modelo` lo que dicen sus pedidos (manda sobre el texto si lo hay)."""
    tabla = rutas.cargar(session)
    categorias = flux.cargar_categorias(session)
    soporte = tabla.soporte(sitio, idioma)
    producto = tabla.producto(interes, idioma) if interes else None

    clave_sitio = (sitio or "").strip()
    # El modelo de FLUX sale de sus pedidos (`modelo`) o del texto; pero el
    # texto solo cuenta si escribe por la web de FLUX o no se sabe por dónde:
    # «el chiller hace ruido» de un cliente de mqeurope no lo convierte en
    # cliente de FLUX.
    categoria = flux.modelo_en_texto(modelo, categorias) if modelo else None
    if categoria is None and clave_sitio in (WEB_FLUX, ""):
        categoria = flux.modelo_en_texto(texto, categorias)
    es_flux = clave_sitio == WEB_FLUX or categoria is not None
    bloque_flux: dict[str, Any] | None = None
    if es_flux:
        intencion = flux.intencion(texto, configuracion(session))
        kb = flux.enlace_kb(categoria, idioma)
        propio = tabla.soporte(WEB_FLUX, idioma)
        nuestro = (
            Enlace(propio.url, flux.ORIGEN_FLUX_SOPORTE, sitio=WEB_FLUX, idioma=propio.idioma)
            if propio is not None else None
        )
        elegido = kb if intencion == flux.INTENCION_KB else nuestro
        articulos = (
            flux.articulos_de(session, categoria_id=categoria.categoria_id, idioma=idioma)
            if (con_articulos and categoria is not None) else []
        )
        bloque_flux = {
            "modelo": categoria.modelo if categoria else None,
            "categoria_id": categoria.categoria_id if categoria else None,
            "intencion": intencion,
            "enlace": elegido.como_dict() if elegido else None,
            "kb": kb.como_dict(),
            "soporte": nuestro.como_dict() if nuestro else None,
            "articulos": [
                {"titulo": a.titulo, "url": a.url, "outdated": bool(a.outdated)}
                for a in articulos
            ],
        }
    return {
        "soporte": soporte.como_dict() if soporte else None,
        "producto": producto.como_dict() if producto else None,
        "flux": bloque_flux,
    }
