"""Comprobación del Cuadre: fichas del catálogo cuyo enlace no lleva a la
máquina (fuente `mysql`).

El 28/10/2026, «Lo que ha usado» de un borrador enseñaba la ficha de la
artisJet 3000Pro Freebird con el enlace `https://boprint.net/contacto/`: si
esa ficha va en un correo, el cliente acaba en un formulario en vez de en el
producto. Con el índice de las webs se puede ver solo.

**Vale cualquier página indexada de nuestras webs** (28/10/2026, segunda
vuelta): una página de aterrizaje como `fluxlasers.eu/beambox-ii/` está bien
puesta, aunque exista además la ficha de tienda. Se avisa solo cuando el
enlace (con o sin `www.`, el prefijo de idioma o la barra final, y desde
cualquiera de los dominios de esa web, `webs.fichas.MapaProductos.motivo`):

- va a una **página de servicio**: contacto, categoría, carrito, pago, mi
  cuenta, buscador, legales… (las que el índice excluye por su slug, más
  contacto y buscador);
- **no está en el índice** (ni como producto, ni como página, ni como
  entrada), o el índice ya no lo tiene.

No se corrige nada: el catálogo llega en un solo sentido del compositor, y lo
que se tocara aquí lo borraría la siguiente sincronización. Se arregla en el
compositor. Lo que no se puede juzgar (una web sin nada en el índice; una
ruta de producto de una web sin productos en él) no se da por resuelto: el
ámbito de cada descuadre es su ficha, no su web.
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
         "de la máquina (la de producto o una de aterrizaje). Si ya no está en la web, quítalo "
         "o cámbialo allí.")
PISTA_IDO = ("Mira si el producto sigue en la web: si se quitó, quítalo o cámbialo en el "
             "compositor (BoHub solo lo lee); si sigue, es una regla del índice de las webs la "
             "que lo deja fuera, y el enlace puede estar bien.")
PISTA_FUERA = ("Si la página se acaba de publicar, el índice aún no la tiene: «Reindexar ahora» "
               "en el panel del índice de las webs, y este aviso se va solo. Si no, el enlace "
               "está mal: corrígelo en el compositor (BoHub solo lo lee).")


@comprobacion(
    id=ENLACE_CATALOGO_NO_PRODUCTO, orden=21,
    titulo="Fichas del catálogo cuyo enlace no lleva a un producto",
    descripcion=("Productos del catálogo del compositor con un enlace de una de nuestras webs "
                 "que va a una página de servicio (contacto, categoría, carrito…) o que no está "
                 "en el índice de las webs: en un correo, el cliente no llega a la máquina. "
                 "Cualquier página indexada de la web (la de producto o una de aterrizaje) "
                 "vale."),
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
        # El ámbito es la ficha: lo que no se puede juzgar de una no deja
        # abiertos los descuadres ya arreglados de las demás de su web.
        eid = entidad_id(p.fuente, f"{p.origen_id}:{p.idioma}")
        motivo = mapa.motivo(enlace)
        if motivo is None:
            if not mapa.juzgable(enlace):
                no_mirados.add(eid)
            continue
        titulo = mapa.titulo(enlace) if motivo == fichas.MOTIVO_SERVICIO else None
        if motivo == fichas.MOTIVO_SERVICIO:
            que = (f"va a la página «{titulo}», una página de servicio, no a la de la máquina"
                   if titulo else
                   f"va a una página de servicio de {sitio.dominio} (contacto, categoría, "
                   "carrito…), no a la de la máquina")
        elif motivo == fichas.MOTIVO_IDO:
            que = ("es el de una página que el índice ya no tiene (se quitó de la web o una "
                   "regla del índice la deja fuera)")
        else:
            que = (f"no está en el índice de {sitio.dominio}: ni como producto, ni como página, "
                   "ni como entrada (o es una que aún no se ha leído)")
        yield Hallazgo(
            entidad_tipo=ENTIDAD_PRODUCTO_CATALOGO,
            entidad_id=eid,
            etiqueta=f"«{p.nombre}» ({p.idioma})"[:200],
            detalle=f"Su enlace {enlace} {que}.",
            pista_de_arreglo={fichas.MOTIVO_FUERA: PISTA_FUERA,
                              fichas.MOTIVO_IDO: PISTA_IDO}.get(motivo, PISTA),
            enlace=enlace,
            arreglo_enlace=AJUSTES_ERP,
            arreglo_boton="Abrir la base de conocimiento",
            huella_datos={"fuente": p.fuente, "origen_id": p.origen_id, "idioma": p.idioma,
                          "enlace": fichas.pagina(enlace)},
            datos={"nombre": p.nombre, "marca": p.marca, "idioma": p.idioma, "enlace": enlace,
                   "sitio": sitio.clave, "pagina": titulo, "motivo": motivo},
            ambito=eid,
        )
