"""Recuperar las «Fecha recogido» que se perdieron en el deduplicado del 24/09.

Al activar el espejo (Fase 2, #484/#486), la fila del HISTÓRICO de cada pedido
que ya se pintaba arriba (su gemela, casada `confirmed` por el backfill) se
quitó de la hoja sin fusionar: lo que esa fila tenía y la de BoHub no, se
perdió de la hoja. Los trackings se restauraron a mano; las fechas de recogida
no. La fila original sigue entera en `seguimiento_legacy.raw_json`.

`planificar` deduce los casos comparando esa fila con el pedido de hoy:
gemelas `confirmed` de un pedido que se pinta arriba (vivo o completado), con
fecha de recogida en la fila y SIN fecha en el pedido. `aplicar` la guarda en
el pedido (`seguimiento.RECOGIDO_HOJA_KEY`) SOLO si sigue vacía, con rastro en
la auditoría. Nunca pisa una fecha que ya exista. No toca FACTUSOL ni la hoja:
la siguiente «Actualizar hoja» la pinta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, SeguimientoLegacy
from app.erp.seguimiento import RECOGIDO_HOJA_KEY, RECOGIDO_INDEX, fecha_recogido
from app.erp.seguimiento_mirror import (
    _a_iso,
    _json_lista,
    _texto,
    con_hueco_courier,
    fecha_razonable,
    formato_bd_viejo,
)

#: Acción de auditoría de cada fecha recuperada.
RECUPERADA_EVENT = "erp.seguimiento_recogido_recuperado"

RELLENAR = "rellenar"           # el pedido no tiene fecha: se guarda la de la hoja
YA_TIENE = "ya_tiene"           # el pedido ya tiene otra: se queda la suya
IGUAL = "igual"                 # ya coincide: nada que hacer
NO_ES_FECHA = "no_es_fecha"     # la celda no se entiende como fecha: no se inventa


@dataclass(frozen=True)
class Caso:
    order_id: str
    order_number: str
    legacy_id: str
    #: lo que dice la fila original (ISO si se entiende; si no, el texto).
    fecha_hoja: str
    #: la fecha de recogida del pedido hoy (ISO) o None.
    fecha_pedido: str | None
    accion: str


def planificar(session: Session, pintados: set[str]) -> list[Caso]:
    """Los pedidos que se pintan arriba (`pintados`: ids de la zona viva y de
    los completados) cuya gemela del histórico tenía fecha de recogida."""
    viejo = formato_bd_viejo(session)
    #: Un caso por pedido. Si el pedido tiene varias gemelas, manda la primera
    #: con una fecha que se entiende (una con texto no tapa a otra con fecha).
    casos: dict[str, Caso] = {}
    registros = session.scalars(
        select(SeguimientoLegacy)
        .where(SeguimientoLegacy.match_status == "confirmed",
               SeguimientoLegacy.matched_order_id.is_not(None))
        .order_by(SeguimientoLegacy.row_index)
    )
    for rec in registros:
        oid = str(rec.matched_order_id)
        if oid not in pintados or (oid in casos and casos[oid].accion != NO_ES_FECHA):
            continue
        vals = _json_lista(rec.raw_json)
        if viejo:
            vals = con_hueco_courier(vals)
        crudo: Any = vals[RECOGIDO_INDEX] if len(vals) > RECOGIDO_INDEX else ""
        if not _texto(crudo):
            continue
        order = session.get(Order, oid)
        if order is None:
            continue
        iso = _a_iso(crudo)
        actual = fecha_recogido(order)
        if not fecha_razonable(iso):
            if oid in casos:
                continue           # ya hay una gemela apuntada; esta no aporta
            accion = NO_ES_FECHA
        else:
            accion = RELLENAR if not actual else (IGUAL if actual == iso else YA_TIENE)
        casos[oid] = Caso(order_id=oid, order_number=order.order_number or oid,
                          legacy_id=rec.id, fecha_hoja=iso, fecha_pedido=actual,
                          accion=accion)
    return list(casos.values())


def aplicar(session: Session, casos: list[Caso], *, actor_email: str) -> list[Caso]:
    """Guarda la fecha de los casos `RELLENAR` cuyo pedido SIGUE sin fecha (se
    vuelve a mirar: nunca pisa una que haya aparecido entretanto). Deja una
    entrada de auditoría por pedido. No confirma: lo hace quien llama."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factusol_albaran import packing_of, save_packing  # noqa: PLC0415

    hechos: list[Caso] = []
    for caso in casos:
        if caso.accion != RELLENAR:
            continue
        order = session.get(Order, caso.order_id)
        if order is None or fecha_recogido(order):
            continue
        datos = packing_of(order)
        datos[RECOGIDO_HOJA_KEY] = caso.fecha_hoja
        save_packing(order, datos)
        record_event(
            session, action=RECUPERADA_EVENT, target_type="order", target_id=order.id,
            actor=None, actor_email=actor_email,
            metadata={"order_number": order.order_number, "fecha_recogido": caso.fecha_hoja,
                      "legacy_id": caso.legacy_id, "origen": "seguimiento_legacy.raw_json"},
            message=(f"Fecha recogido {caso.fecha_hoja} recuperada de la fila original "
                     "del histórico de la hoja (perdida en el deduplicado del 24/09)."),
        )
        hechos.append(caso)
    session.flush()
    return hechos
