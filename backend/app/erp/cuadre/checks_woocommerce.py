"""Comprobaciones del Cuadre que preguntan a las tiendas WooCommerce (fuente
`woocommerce`).

Como las de FACTUSOL, corren en el worker (nocturno y «Comprobar ahora»),
nunca en la petición web. SOLO LECTURA: listan pedidos de la tienda y los
cruzan con BoHub; no importan nada (eso lo hacen el repaso periódico y
«Poner al día estados Woo…»). Por eso es la RED: el aviso salta aunque la
importación falle.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_PEDIDO_WOO,
    FUENTE_WOOCOMMERCE,
    Hallazgo,
    comprobacion,
)

SEGUIMIENTO = "/erp/seguimiento"
PAGADO_SIN_BOHUB = "pedido_woo_pagado_sin_bohub"


def _fecha(iso: str | None) -> str:
    if not iso:
        return "fecha desconocida"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M UTC")
    except ValueError:
        return iso


@comprobacion(
    id=PAGADO_SIN_BOHUB, orden=14,
    titulo="Pedido pagado en WooCommerce que no está en BoHub",
    descripcion="Pedido pagado en la tienda (procesando, completado o reembolsado) en los "
                "últimos N días que no ha llegado a BoHub: la tienda no disparó el webhook.",
    severidad="alta", fuente=FUENTE_WOOCOMMERCE, grupo="integraciones",
    dias_defecto=90, dias_texto="Mirar los pedidos pagados de los últimos N días",
)
def pedido_woo_pagado_sin_bohub(ctx: Contexto) -> Iterator[Hallazgo]:
    from app.integrations.woocommerce.client import WooError, WooHTTPClient  # noqa: PLC0415
    from app.integrations.woocommerce.missing import (  # noqa: PLC0415
        pagados_que_faltan,
        woo_stores,
    )

    dias = ctx.dias(PAGADO_SIN_BOHUB, 90)
    no_mirados: set[str] = set()
    ctx.no_mirados[PAGADO_SIN_BOHUB] = no_mirados
    tiendas = woo_stores(ctx.session)
    fallos: list[str] = []
    for store in tiendas:
        try:
            vista = pagados_que_faltan(
                ctx.session, store, WooHTTPClient(store), days=dias, now=ctx.ahora,
            )
        except WooError as exc:
            no_mirados.add(store.account_id)
            fallos.append(f"{store.account_id}: {str(exc)[:120]}")
            continue
        if vista["con_tope"]:
            # Listado incompleto: lo no visto de esta tienda no se da por resuelto.
            no_mirados.add(store.account_id)
        for p, _payload in vista["faltan"]:
            yield Hallazgo(
                entidad_tipo=ENTIDAD_PEDIDO_WOO, entidad_id=f"{p.tienda}:{p.woo_id}",
                etiqueta=f"{p.tienda_nombre} #{p.numero}",
                detalle=(f"Pagado en la tienda ({p.estado}) el {_fecha(p.pagado_el)} — "
                         f"{p.cliente}, {p.importe} {p.moneda} — y no está en BoHub."),
                pista_de_arreglo="El repaso automático lo importa en su siguiente pasada; "
                                 "para no esperar, «Poner al día estados Woo…» en "
                                 "Seguimiento lo importa como si hubiera llegado el webhook.",
                enlace=p.enlace,
                arreglo_enlace=SEGUIMIENTO, arreglo_boton="Poner al día estados Woo",
                huella_datos={"estado": p.estado, "importe": p.importe},
                datos={"tienda": p.tienda, "numero": p.numero, "cliente": p.cliente,
                       "importe": p.importe, "moneda": p.moneda, "pagado_el": p.pagado_el,
                       "woo_id": p.woo_id},
                ambito=p.tienda,
            )
    if tiendas and len(fallos) == len(tiendas):
        # Ninguna tienda ha respondido: la comprobación queda con error (y sus
        # descuadres, como estaban), no «sin descuadres».
        raise WooError("Ninguna tienda ha respondido: " + " · ".join(fallos))
