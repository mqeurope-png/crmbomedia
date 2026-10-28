"""Comprobación del Cuadre: fichas del catálogo cuyo enlace no lleva a un
producto (fuente `mysql`).

El 28/10/2026, «Lo que ha usado» de un borrador enseñaba la ficha de la
artisJet 3000Pro Freebird con el enlace `https://boprint.net/contacto/`: si
esa ficha va en un correo, el cliente acaba en un formulario en vez de en el
producto. Con el índice de las webs se puede ver solo: el enlace de una ficha
del catálogo que es de una web indexada y no casa con ninguna página de
producto del índice (con o sin `www.`, el prefijo de idioma o la barra final,
y desde cualquiera de los dominios de esa web).

No se corrige nada: el catálogo llega en un solo sentido del compositor, y lo
que se tocara aquí lo borraría la siguiente sincronización. Se arregla en el
compositor.

Solo se juzga una web con productos en el índice: una sin tienda, o que no se
ha leído, no se puede juzgar (y lo que se hubiera avisado de ella no se da por
resuelto). Y se dice lo que se sabe: si el enlace es una página del índice
(«Contacto») o un producto que ya no está, es seguro; si no casa con nada,
puede ser un producto recién publicado que el índice (que se lee una vez al
mes) aún no tiene, y la pista lo dice.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import select

from app.erp.cuadre.checks_textos import AJUSTES_ERP, entidad_id
from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import FUENTE_MYSQL, Hallazgo, comprobacion

ENLACE_CATALOGO_NO_PRODUCTO = "enlace_catalogo_no_producto"
ENTIDAD_PRODUCTO_CATALOGO = "producto_catalogo"
PISTA = ("El catálogo se edita en el compositor (BoHub solo lo lee): pon el enlace de la página "
         "del producto. Si el producto ya no está en la web, quítalo o cámbialo allí.")
PISTA_FUERA = ("Si el producto se acaba de publicar, el índice aún no lo tiene: «Reindexar ahora» "
               "en el panel del índice de las webs, y este aviso se va solo. Si no, el enlace "
               "está mal: corrígelo en el compositor (BoHub solo lo lee).")


@comprobacion(
    id=ENLACE_CATALOGO_NO_PRODUCTO, orden=21,
    titulo="Fichas del catálogo cuyo enlace no lleva a un producto",
    descripcion=("Productos del catálogo del compositor con un enlace de una de nuestras webs "
                 "que no es una página de producto del índice de las webs (la de contacto, una "
                 "categoría, un producto que ya no está): en un correo, el cliente no llega al "
                 "producto."),
    severidad="media", fuente=FUENTE_MYSQL, grupo="crm",
)
def enlace_catalogo_no_producto(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.models.conocimiento import FUENTE_COMPOSITOR, ProductoCatalogo  # noqa: PLC0415
    from app.services.webs import fichas  # noqa: PLC0415

    session = ctx.session
    mapa = fichas.MapaProductos(session)
    no_mirados: set[str] = set()
    ctx.no_mirados[ENLACE_CATALOGO_NO_PRODUCTO] = no_mirados
    # Solo las fichas del compositor: los consumibles y repuestos de las tiendas
    # se dejan fuera del índice a propósito, y su enlace nunca casaría.
    productos = session.scalars(select(ProductoCatalogo).where(
        ProductoCatalogo.fuente == FUENTE_COMPOSITOR,
        ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.descartado.is_(False),
        ProductoCatalogo.visible.is_(True), ProductoCatalogo.enlace.is_not(None),
    ).order_by(ProductoCatalogo.nombre, ProductoCatalogo.idioma))
    for p in productos:
        enlace = (p.enlace or "").strip()
        sitio = fichas.sitio_de(enlace) if enlace else None
        if sitio is None:
            continue
        if sitio.clave not in mapa.con_productos:
            no_mirados.add(sitio.clave)
            continue
        motivo = mapa.motivo(enlace)
        if motivo is None:
            continue
        titulo = mapa.titulo(enlace) if motivo == fichas.MOTIVO_PAGINA else None
        if motivo == fichas.MOTIVO_PAGINA:
            que = (f"es la página «{titulo}», no un producto" if titulo else
                   f"es una página de {sitio.dominio} que no es un producto")
        elif motivo == fichas.MOTIVO_IDO:
            que = "es el de un producto que ya no está en la web"
        else:
            que = (f"no está en el índice de {sitio.dominio}: no es ninguna de sus páginas de "
                   "producto (o es una que aún no se ha leído)")
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PRODUCTO_CATALOGO,
            entidad_id=entidad_id(p.fuente, f"{p.origen_id}:{p.idioma}"),
            etiqueta=f"«{p.nombre}» ({p.idioma})"[:200],
            detalle=f"Su enlace {enlace} {que}.",
            pista_de_arreglo=PISTA_FUERA if motivo == fichas.MOTIVO_FUERA else PISTA,
            enlace=enlace,
            arreglo_enlace=AJUSTES_ERP,
            arreglo_boton="Abrir la base de conocimiento",
            huella_datos={"fuente": p.fuente, "origen_id": p.origen_id, "idioma": p.idioma,
                          "enlace": fichas.pagina(enlace)},
            datos={"nombre": p.nombre, "marca": p.marca, "idioma": p.idioma, "enlace": enlace,
                   "sitio": sitio.clave, "pagina": titulo, "motivo": motivo},
            ambito=sitio.clave,
        )
