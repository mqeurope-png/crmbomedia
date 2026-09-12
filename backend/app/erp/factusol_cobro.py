"""Cobro MANUAL de la factura de un pedido desde la app (ficha y bandeja).

Reutiliza el motor de cobros F-4-B tal cual (`register_invoice_collection`
vía `POST /documents/facturas/{serie}/{codigo}/collection`: solo `F_LCO` +
`ESTFAC=2`, idempotente, cola `factusol:writes`). Aquí solo está lo que
faltaba para dispararlo desde el pedido:

- resolver la FACTURA del pedido con su clave COMPUESTA (serie + código): el
  pedido guarda el CODFAC desnudo (`factusol_invoice_number`) y F_FAC solo es
  única por (TIPFAC, CODFAC);
- su estado de cobro EN FACTUSOL (ESTFAC / saldo en F_LCO), persistido en el
  pedido (`factusol_cobro_status` + `packing_json.factusol_cobro`) para verlo
  fila a fila en la bandeja y filtrar. Es el estado CONTABLE, distinto del
  «Pagado» del CRM (`payment_status`);
- la cuenta sugerida y los avisos (posible doble cobro) para el modal.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.factusol_albaran import packing_of, save_packing
from app.erp.models import Order, OrderStatusHistory, StatusDomain
from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.collections import load_collections_index
from app.integrations.factusol.collections_write import collection_status
from app.integrations.factusol.service import (
    _int_or_none,
    coerce_serie,
    pcl_ref_for_order,
    serie_of_row,
)

logger = logging.getLogger(__name__)

#: Bloque en `packing_json` con el detalle del último estado comprobado.
COBRO_KEY = "factusol_cobro"
#: Valores de `Order.factusol_cobro_status`.
COBRADA = "cobrada"
PENDIENTE = "pendiente"
#: Filtros de la bandeja: los dos estados + «con factura pero sin comprobar».
COBRO_FILTERS = (COBRADA, PENDIENTE, "sin_comprobar")


def _now() -> datetime:
    return datetime.now(UTC)


def _status_value(v: Any) -> str:
    return getattr(v, "value", v)


def parse_invoice_number(value: Any) -> tuple[int | None, int | None]:
    """`'5-260086'` → `(5, 260086)`; `'260086'` (CODFAC desnudo, como guarda
    la emisión) → `(None, 260086)`."""
    text = str(value or "").strip()
    if not text:
        return None, None
    if "-" in text:
        head, _, tail = text.partition("-")
        return coerce_serie(head), _int_or_none(tail)
    return None, _int_or_none(text)


def history_invoice_serie(session: Session, order: Order) -> int | None:
    """Serie apuntada en el historial al vincular la factura (Fase 2 guarda
    `factusol_serie`); None si ninguna entrada la trae."""
    rows = session.scalars(
        select(OrderStatusHistory)
        .where(
            OrderStatusHistory.order_id == order.id,
            OrderStatusHistory.domain == StatusDomain.INVOICE,
        )
        .order_by(OrderStatusHistory.changed_at.desc())
    )
    for h in rows:
        if not h.metadata_json:
            continue
        try:
            meta = json.loads(h.metadata_json)
        except (TypeError, ValueError):
            continue
        serie = coerce_serie(meta.get("factusol_serie")) if isinstance(meta, dict) else None
        if serie is not None:
            return serie
    return None


def resolve_invoice_key(
    session: Session, order: Order, *,
    fac_rows: list[dict[str, Any]] | None = None,
    client: FactusolClient | None = None, ejercicio: str | None = None,
) -> dict[str, Any] | None:
    """Clave compuesta (serie, código) de la factura del pedido y su fila de
    F_FAC. `None` si el pedido no tiene factura.

    La serie sale, por orden: del propio número si ya va como `serie-código`;
    de `factusol_invoice_serie` (resuelta antes); del historial (Fase 2); y
    si no, de F_FAC: con un solo CODFAC en toda la tabla es esa; con
    homónimos en varias series, la fila cuya REFFAC es la referencia del
    pedido web, o la serie del albarán del que se facturó (Fase 2). Si sigue
    sin saberse, `serie=None` + `ambiguous=True`: NUNCA se adivina. La serie
    resuelta se guarda en el pedido."""
    serie, codigo = parse_invoice_number(order.factusol_invoice_number)
    if codigo is None:
        return None
    if serie is None:
        serie = order.factusol_invoice_serie or history_invoice_serie(session, order)
    if fac_rows is None:
        rows = (
            client.load_table("F_FAC", filtro=f"CODFAC={codigo}", ejercicio=ejercicio)
            if client is not None else []
        )
    else:
        rows = fac_rows
    by_serie: dict[int, dict[str, Any]] = {}
    for row in rows:
        if _int_or_none(row.get("CODFAC")) != codigo:
            continue
        row_serie = serie_of_row(row, "TIPFAC")
        if row_serie is not None:
            by_serie[row_serie] = row
    ambiguous = False
    if serie is None:
        if len(by_serie) == 1:
            serie = next(iter(by_serie))
        elif by_serie:
            ref = pcl_ref_for_order(session, order)
            matches = [
                s for s, row in by_serie.items()
                if str(row.get("REFFAC") or "").strip().upper() == ref
            ]
            alb_serie, _ = parse_invoice_number(order.factusol_albaran_number)
            if len(matches) == 1:
                serie = matches[0]
            elif alb_serie in by_serie:
                serie = alb_serie
            else:
                ambiguous = True
    if serie is not None and order.factusol_invoice_serie != serie:
        order.factusol_invoice_serie = serie
    return {
        "serie": serie, "codigo": codigo,
        "numero": f"{serie}-{codigo:06d}" if serie is not None else str(codigo),
        "row": by_serie.get(serie) if serie is not None else None,
        "ambiguous": ambiguous, "series": sorted(by_serie),
    }


# --- estado persistido ----------------------------------------------------------


def cobro_block(status_info: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "numero": status_info["numero"],
        "serie": int(status_info["serie"]), "codigo": int(status_info["codigo"]),
        "total": status_info.get("total"),
        "total_cobrado": status_info.get("total_cobrado"),
        "saldo_pendiente": status_info.get("saldo_pendiente"),
        "estfac": status_info.get("estfac"),
        "cobros": status_info.get("cobros"),
        "fopfac": status_info.get("fopfac"),
        "cobrada": bool(status_info.get("ya_cobrada")),
        "checked_at": _now().isoformat(),
        "source": source,
    }


def persist_cobro(
    session: Session, order: Order, status_info: dict[str, Any], *, source: str,
) -> dict[str, Any]:
    """Guarda en el pedido el estado de cobro recién comprobado."""
    _ = session
    block = cobro_block(status_info, source=source)
    order.factusol_invoice_serie = block["serie"]
    order.factusol_cobro_status = COBRADA if block["cobrada"] else PENDIENTE
    order.factusol_cobro_checked_at = _now()
    packing = packing_of(order)
    packing[COBRO_KEY] = block
    save_packing(order, packing)
    return block


def cobro_info(order: Order) -> dict[str, Any] | None:
    """Último estado comprobado (para la ficha / la fila de la bandeja)."""
    block = packing_of(order).get(COBRO_KEY)
    return block if isinstance(block, dict) else None


def _store_slug(session: Session, order: Order) -> str | None:
    if not order.store_id:
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    return store.account_id if store is not None else None


def _forma_nombre(
    order: Order, fopfac: str, fop_names: dict[str, str] | None,
) -> str | None:
    names = fop_names or {}
    code = str(fopfac or "").strip()
    if code:
        hit = names.get(code) or names.get(code.lstrip("0") or code)
        if hit:
            return hit
    source = packing_of(order).get("factusol_source")
    if isinstance(source, dict) and source.get("forma_pago_nombre"):
        return str(source["forma_pago_nombre"])
    return None


def order_cobro_info(
    session: Session, client: FactusolClient, order: Order, ejercicio: str, *,
    fac_rows: list[dict[str, Any]] | None = None,
    index: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
    fop_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Estado de cobro EN VIVO de la factura del pedido, ya persistido:

    - `sin_factura`: el pedido no tiene factura (el botón se deshabilita);
    - `unresolved` / `not_found`: hay CODFAC pero no se localiza su fila
      (serie ambigua o factura borrada): sin escribir nada;
    - `pendiente` / `cobrada`: con total, cobrado, saldo, ESTFAC, nº de
      líneas de cobro, forma de pago, cuenta sugerida y avisos (posible doble
      cobro: ya hay líneas de cobro sin llegar al total)."""
    from app.erp.contrapartidas import suggest_contrapartida  # noqa: PLC0415

    if not order.factusol_invoice_number:
        return {
            "status": "sin_factura", "invoice": None,
            "detail": "El pedido aún no tiene factura en FACTUSOL: emite la factura primero.",
        }
    key = resolve_invoice_key(
        session, order, fac_rows=fac_rows, client=client, ejercicio=ejercicio,
    )
    if key is None or key["serie"] is None:
        codigo = key["codigo"] if key else None
        if key and key["ambiguous"]:
            detail = (
                f"La factura {codigo} existe en varias series "
                f"({', '.join(str(s) for s in key['series'])}) y no se puede saber "
                "cuál es la del pedido: regístrala desde ERP · Documentos."
            )
        else:
            detail = (
                f"No se encontró la factura {codigo} del pedido en FACTUSOL "
                f"(ejercicio {ejercicio})."
            )
        return {
            "status": "unresolved",
            "invoice": {"serie": None, "codigo": codigo, "numero": str(codigo)},
            "detail": detail,
        }
    status_info = collection_status(
        client, serie=key["serie"], codigo=key["codigo"], ejercicio=ejercicio,
        row=key["row"], index=index,
    )
    invoice = {"serie": key["serie"], "codigo": key["codigo"], "numero": key["numero"]}
    if status_info is None:
        return {
            "status": "not_found", "invoice": invoice,
            "detail": (
                f"La factura {key['numero']} ya no existe en FACTUSOL "
                f"(ejercicio {ejercicio})."
            ),
        }
    block = persist_cobro(session, order, status_info, source="live")
    warnings: list[str] = []
    if not status_info["ya_cobrada"] and status_info["cobros"] > 0:
        warnings.append(
            f"La factura ya tiene {status_info['cobros']} línea(s) de cobro por "
            f"{status_info['total_cobrado']:.2f} € (saldo {status_info['saldo_pendiente']:.2f} €): "
            "posible doble cobro o anticipo. Revisa antes de registrar."
        )
    if status_info["estfac"] == "1":
        warnings.append("FACTUSOL la marca como cobro parcial (ESTFAC=1).")
    forma_nombre = _forma_nombre(order, status_info["fopfac"], fop_names)
    suggested = suggest_contrapartida(
        session, serie=key["serie"], forma_nombre=forma_nombre,
        store=_store_slug(session, order),
    )
    return {
        "status": COBRADA if status_info["ya_cobrada"] else PENDIENTE,
        "invoice": invoice,
        "cliente": status_info["cliente"], "referencia": status_info["referencia"],
        "total": status_info["total"], "total_cobrado": status_info["total_cobrado"],
        "saldo_pendiente": status_info["saldo_pendiente"],
        "estfac": status_info["estfac"], "cobros": status_info["cobros"],
        "fopfac": status_info["fopfac"], "forma_pago_nombre": forma_nombre,
        "suggested_cuenta": suggested, "warnings": warnings,
        "checked_at": block["checked_at"],
    }


def refresh_orders_cobro(
    session: Session, client: FactusolClient, orders: list[Order], ejercicio: str, *,
    fop_names: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """«Actualizar cobros FACTUSOL» de la bandeja: comprueba de una vez todos
    los pedidos con factura leyendo F_FAC y F_LCO UNA sola vez (sin N+1)."""
    targets = [o for o in orders if o.factusol_invoice_number]
    if not targets:
        return {}
    fac_rows = client.load_table("F_FAC", filtro="1=1", ejercicio=ejercicio)
    index = load_collections_index(client, ejercicio=ejercicio)
    return {
        o.id: order_cobro_info(
            session, client, o, ejercicio, fac_rows=fac_rows, index=index,
            fop_names=fop_names,
        )
        for o in targets
    }


def mark_order_from_result(
    session: Session, order: Order, *, serie: int, codigo: int,
    result: dict[str, Any], source: str, actor_user_id: str | None = None,
) -> bool:
    """Tras registrar el cobro (job F-4-B, manual o Fase 2): deja el pedido
    como «cobrada» sin releer FACTUSOL. `already` también cuenta (ya estaba
    cobrada). False si el resultado no fue un cobro."""
    registered = bool(result.get("registered"))
    if not registered and result.get("status") != "already":
        return False
    numero = f"{int(serie)}-{int(codigo):06d}"
    total = result.get("total")
    status_info = {
        "serie": int(serie), "codigo": int(codigo), "numero": numero,
        "total": total,
        "total_cobrado": total if registered else result.get("total_cobrado", total),
        "saldo_pendiente": 0.0 if registered else result.get("saldo_pendiente", 0.0),
        "estfac": "2" if (not registered or result.get("estfac_marked", True))
        else result.get("estfac"),
        "cobros": (int(result.get("cobros") or 0) + 1) if registered
        else result.get("cobros"),
        "fopfac": result.get("fopfac"), "ya_cobrada": True,
    }
    block = persist_cobro(session, order, status_info, source=source)
    paid = _status_value(order.payment_status)
    reason = (
        f"Cobro de {result.get('importe')} € registrado en FACTUSOL para la "
        f"factura {numero} ({source})"
        if registered else
        f"La factura {numero} ya constaba cobrada en FACTUSOL ({source})"
    )
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PAYMENT,
        from_status=paid, to_status=paid, changed_at=_now(),
        changed_by_user_id=actor_user_id, reason=reason,
        metadata_json=json.dumps({
            "event": "factusol_cobro", "source": source, **block,
            "linlco": result.get("linlco"), "importe": result.get("importe"),
            "contrapartida": result.get("contrapartida"),
        }),
    ))
    return True


def mark_orders_after_collection(
    session: Session, *, serie: int, codigo: int, result: dict[str, Any],
    actor_user_id: str | None = None,
) -> list[Order]:
    """Enganche del job de cobro (`register_invoice_collection_job`): los
    pedidos vinculados a la factura `serie-codigo` quedan «cobrada» en la
    bandeja aunque el operador cierre el modal antes de que termine."""
    candidates = session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(
            [str(int(codigo)), f"{int(serie)}-{int(codigo):06d}"],
        ))
    ).all()
    updated: list[Order] = []
    for order in candidates:
        if order.factusol_invoice_serie not in (None, int(serie)):
            continue  # homónimo de otra serie
        if mark_order_from_result(
            session, order, serie=serie, codigo=codigo, result=result,
            source="manual", actor_user_id=actor_user_id,
        ):
            updated.append(order)
    return updated
