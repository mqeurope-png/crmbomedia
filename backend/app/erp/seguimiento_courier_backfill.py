"""Seguimiento — backfill de la columna «Courier» (y del nuevo «Envío»).

Courier y Envío NO se guardan: salen en cada lectura del envío de cada pedido
(`seguimiento.courier_label` / `_envio_label`), así que todos los pedidos que ya
existían los tienen desde el primer momento y la hoja de Drive los recibe en la
siguiente sincronización. Lo único que puede faltar es un DATO de origen: la
agencia de un envío Genei antiguo que nunca la guardó (se tramitó o pagó fuera
de BoHub antes de que el webhook / el sondeo la apuntaran). Esos salen como
«Genei» en Courier hasta que se rellena su agencia.

- `informe(session)`: cuántos pedidos hay de cada tipo (Genei con / sin
  agencia, otro courier con / sin courier, sin envío) y cómo queda Envío.
- `rellenar_agencias_genei(...)`: pide a Genei, uno a uno, la agencia de los
  envíos que no la tienen y la guarda en su bloque (`packing_json.genei`), como
  hace el refresco de siempre. Solo lectura de Genei: no crea, paga ni cancela
  nada. Sin `apply`, solo dice qué haría.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order

logger = logging.getLogger(__name__)


def _tipo(order: Order) -> str:
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.seguimiento import COURIER_SIN_ENVIO, courier_label, is_sin_envio  # noqa: PLC0415
    from app.erp.shipping_courier import OTHER_COURIER_LABEL, is_genei_shipment  # noqa: PLC0415

    if is_sin_envio(order):
        return "sin_envio"
    if is_genei_shipment(order):
        return "genei_con_agencia" if str(genei_state_of(order).get("courier") or "").strip() \
            else "genei_sin_agencia"
    courier = courier_label(order)
    if courier == OTHER_COURIER_LABEL:
        return "otro_courier_sin_apuntar"
    if courier == COURIER_SIN_ENVIO:
        return "sin_envio_aun"
    return "otro_courier_apuntado"


def informe(session: Session) -> dict[str, Any]:
    """Recuento por tipo de envío y por valor de Envío (sin escribir nada), y
    los Nº de los envíos Genei sin agencia guardada."""
    from app.erp.seguimiento import _envio_label  # noqa: PLC0415

    tipos: Counter[str] = Counter()
    envios: Counter[str] = Counter()
    genei_sin_agencia: list[str] = []
    for order in session.scalars(select(Order)):
        tipo = _tipo(order)
        tipos[tipo] += 1
        envios[_envio_label(order)] += 1
        if tipo == "genei_sin_agencia":
            genei_sin_agencia.append(order.order_number)
    return {
        "pedidos": sum(tipos.values()),
        "por_tipo": dict(tipos),
        "por_envio": dict(envios),
        "genei_sin_agencia": sorted(genei_sin_agencia),
    }


def rellenar_agencias_genei(
    session: Session,
    client: Any,
    *,
    apply: bool = False,
    limit: int | None = None,
    pausa: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Pide a Genei la agencia de los envíos que no la tienen guardada.

    `client` es el cliente de Genei (`get_shipment`); `pausa`, lo que se hace
    entre una consulta y la siguiente (para no saturar la API). Con
    `apply=False` no se consulta ni se escribe nada: solo se listan. Unas
    credenciales rechazadas cortan la pasada (no tiene sentido seguir)."""
    from app.erp.integrations.genei.client import GeneiAuthError, GeneiError  # noqa: PLC0415
    from app.erp.integrations.genei.service import (  # noqa: PLC0415
        set_genei_state,
        shipment_code_of_order,
        summarize_shipment,
    )

    pendientes = [o for o in session.scalars(select(Order).where(
        Order.packing_json.like('%"shipment_code"%'),
    )) if _tipo(o) == "genei_sin_agencia"]
    pendientes.sort(key=lambda o: o.order_number or "")
    if limit is not None:
        pendientes = pendientes[:limit]
    resumen: dict[str, Any] = {
        "pendientes": len(pendientes), "rellenados": 0, "sin_agencia_en_genei": 0,
        "errores": 0, "aplicado": apply, "detalle": [],
    }
    if not apply:
        resumen["detalle"] = [{"pedido": o.order_number} for o in pendientes]
        return resumen
    for i, order in enumerate(pendientes):
        if i and pausa is not None:
            pausa()
        code = shipment_code_of_order(order)
        try:
            raw = client.get_shipment(str(code))
        except GeneiAuthError:
            logger.warning("courier backfill: Genei rechaza las credenciales; se corta")
            resumen["errores"] += 1
            resumen["cortado"] = "credenciales rechazadas"
            break
        except GeneiError as exc:
            resumen["errores"] += 1
            resumen["detalle"].append({"pedido": order.order_number, "error": str(exc)[:120]})
            continue
        agencia = str(summarize_shipment(raw).get("courier") or "").strip()
        if not agencia:
            resumen["sin_agencia_en_genei"] += 1
            resumen["detalle"].append({"pedido": order.order_number, "agencia": None})
            continue
        set_genei_state(order, {"courier": agencia})
        session.commit()
        resumen["rellenados"] += 1
        resumen["detalle"].append({"pedido": order.order_number, "agencia": agencia})
    return resumen
