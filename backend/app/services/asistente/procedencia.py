"""De dónde sale cada ficha de un borrador, para quien lo revisa.

Al cliente una ficha es una ficha. A la persona de BoHub, no: tiene que ver
cuáles salen del catálogo del compositor (precio de venta, características y
traducciones revisadas) y cuáles se han montado desde una página del índice
de las webs (el precio es el de la web, o «a consultar», o el que ella misma
dijo), con su enlace, para revisar precio y descripción antes de enviar. Va en
«Lo que ha usado» (`contexto_json["fichas"]`) y en el aviso del borrador.

Y una comprobación de paso: el enlace de una ficha del catálogo que, siendo de
una web indexada, no lleva a una página de producto (el 28/10, la artisJet
3000Pro Freebird apuntaba a `boprint.net/contacto/`). Se avisa; no se corrige
(el catálogo se edita en el compositor y aquí solo se lee).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.conocimiento import FUENTE_COMPOSITOR
from app.services.correo_compuesto import bloques as bq
from app.services.webs import fichas as fichas_web

NOMBRES_IDIOMA = {
    "es": "castellano", "en": "inglés", "de": "alemán", "fr": "francés", "nl": "neerlandés",
    "pt": "portugués", "it": "italiano", "ca": "catalán",
}


def _idioma(codigo: str | None) -> str:
    return NOMBRES_IDIOMA.get((codigo or "").lower()[:2], codigo or "otro idioma")


def aviso_enlace_no_producto(nombre: str, enlace: str) -> str:
    return (f"El enlace de la ficha «{nombre}» del catálogo ({enlace}) no lleva a una página de "
            "producto de nuestras webs: revísalo en el compositor.")


def fichas_del_correo(session: Session, estructura: dict[str, Any],
                      productos: dict[str, dict[str, Any]], *,
                      idioma: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Las fichas que lleva el correo compuesto, con su procedencia, y los
    avisos para quien lo revisa. `productos`: los datos resueltos
    (`datos.productos`). Sin fichas del índice, el aviso no cambia (solo el de
    un enlace del catálogo que no es un producto, si lo hay)."""
    fichas: list[dict[str, Any]] = []
    avisos: list[str] = []
    mapa: fichas_web.MapaProductos | None = None
    for clave in bq.productos_de(estructura):
        p = productos.get(bq.clave_de(clave))
        if p is None:
            continue
        nombre = str(p.get("nombre") or clave["origen_id"])
        enlace = p.get("enlace")
        if clave["fuente"] == bq.FUENTE_WEB:
            if clave.get("precio"):
                precio, origen = clave["precio"], "usuario"
            elif p.get("precio_texto"):
                precio, origen = p["precio_texto"], "web"
            else:
                precio, origen = None, "consultar"
            galeria = [u for u in [p.get("imagen"), *(p.get("galeria") or [])] if u]
            imagen = clave.get("imagen") if clave.get("imagen") in galeria else p.get("imagen")
            fichas.append({
                "procedencia": "indice", "nombre": nombre, "enlace": enlace,
                "dominio": p.get("dominio"), "sitio": p.get("sitio"),
                "origen_id": clave["origen_id"], "precio_texto": precio, "precio_origen": origen,
                "precio_iva": p.get("precio_iva") if origen == "web" else None,
                "idioma": p.get("idioma_ficha"),
                "sin_idioma_pedido": bool(p.get("sin_idioma_pedido")),
                "imagen": imagen, "galeria": galeria,
            })
        else:
            no_producto = None
            if p.get("fuente") == FUENTE_COMPOSITOR:
                # Solo las del compositor: los consumibles de las tiendas no
                # están en el índice a propósito.
                if mapa is None:
                    mapa = fichas_web.MapaProductos(session)
                no_producto = mapa.no_es_producto(enlace)
            fichas.append({
                "procedencia": "catalogo", "nombre": nombre, "enlace": enlace,
                "marca": p.get("marca"), "origen_id": clave["origen_id"],
                "precio_texto": p.get("precio_texto"), "precio_origen": "catalogo",
                **({"enlace_no_producto": True} if no_producto else {}),
            })
            if no_producto and enlace:
                avisos.append(aviso_enlace_no_producto(nombre, str(enlace)))
    del_indice = [f for f in fichas if f["procedencia"] == "indice"]
    if del_indice:
        partes = []
        for f in fichas:
            if f["procedencia"] == "catalogo":
                partes.append(f"«{f['nombre']}», del catálogo ({f['enlace'] or 'sin enlace'})")
                continue
            precio = {"web": f"precio de la web: {f['precio_texto']}",
                      "usuario": f"precio que me has indicado tú: {f['precio_texto']}",
                      "consultar": "precio a consultar: la web no da precio"}[f["precio_origen"]]
            partes.append(f"«{f['nombre']}», del índice de {f['dominio'] or 'la web'} "
                          f"({f['enlace']}; {precio})")
        avisos.insert(0, "Fichas del correo: " + "; ".join(partes) + ". Las del índice no están "
                      "en el catálogo: revisa su precio y su descripción antes de enviar.")
        for f in del_indice:
            if f["precio_origen"] == "usuario":
                avisos.append(f"«{f['nombre']}»: precio que me has indicado tú, no sale de la web "
                              "ni del catálogo.")
            if f["sin_idioma_pedido"]:
                avisos.append(f"La ficha de «{f['nombre']}» va en {_idioma(f['idioma'])}: la web "
                              f"no la tiene en {_idioma(idioma)}.")
    return fichas, avisos


def enlaces_catalogo_no_producto(session: Session,
                                 productos: list[dict[str, Any]]) -> list[dict[str, str]]:
    """De los productos del catálogo de un borrador (`nombre`, `enlace`), los
    del compositor que llevan un enlace de una web indexada que no es una
    página de producto del índice."""
    salida: list[dict[str, str]] = []
    mapa: fichas_web.MapaProductos | None = None
    vistos: set[str] = set()
    for p in productos:
        enlace = str(p.get("enlace") or "")
        if p.get("fuente") != FUENTE_COMPOSITOR or not enlace or enlace in vistos:
            continue
        vistos.add(enlace)
        if mapa is None:
            mapa = fichas_web.MapaProductos(session)
        if mapa.no_es_producto(enlace):
            salida.append({"nombre": str(p.get("nombre") or ""), "enlace": enlace})
    return salida
