"""Cobro MANUAL de la factura de un pedido desde la app (ficha y bandeja).

Reutiliza el motor de cobros F-4-B tal cual (`register_invoice_collection`
vía `POST /documents/facturas/{serie}/{codigo}/collection`: solo `F_LCO` +
`ESTFAC=2`, idempotente, cola `factusol:writes`). Aquí solo está lo que
faltaba para dispararlo desde el pedido:

- la FACTURA del pedido con su clave COMPUESTA (serie + código), tal como la
  tiene VINCULADA el pedido (`app.erp.linked_invoice`: estado facturado +
  `factusol_invoice_number` + `factusol_invoice_serie`), sea cual sea su
  origen. Sin la serie NO se registra ni se busca por el número solo (F_FAC
  solo es única por (TIPFAC, CODFAC));
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

from app.erp.factusol_albaran import packing_of, payment_intent, save_packing
from app.erp.linked_invoice import (
    SIN_FACTURA,
    SIN_SERIE,
    invoice_link_problem,
    invoice_parts,
    missing_invoice_detail,
)
from app.erp.models import Order, OrderStatusHistory, StatusDomain
from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.collections import load_collections_index
from app.integrations.factusol.collections_write import collection_status
from app.integrations.factusol.service import (
    _int_or_none,
    coerce_serie,
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


def resolve_invoice_key(
    session: Session, order: Order, *,
    fac_rows: list[dict[str, Any]] | None = None,
    client: FactusolClient | None = None, ejercicio: str | None = None,
) -> dict[str, Any] | None:
    """Clave compuesta (serie, código) de la factura VINCULADA al pedido y su
    fila de F_FAC. `None` si el pedido no tiene factura.

    La serie es la guardada en el pedido (o la del número si va como
    `serie-código`). Si no consta: `serie=None` + `missing_serie=True` y NO se
    consulta F_FAC — nunca se busca la factura por el número solo (las series
    comparten numeración). Con la serie, la fila es la de esa (serie, código)."""
    _ = session
    problem = invoice_link_problem(order)
    if problem == SIN_FACTURA:
        return None
    serie, codigo = invoice_parts(order)
    assert codigo is not None  # noqa: S101 — `sin_factura` cubre el caso
    if problem == SIN_SERIE or serie is None:
        return {
            "serie": None, "codigo": codigo, "numero": str(codigo), "row": None,
            "missing_serie": True, "ambiguous": False, "series": [],
        }
    if fac_rows is None:
        rows = (
            client.load_table("F_FAC", filtro=f"CODFAC={codigo}", ejercicio=ejercicio)
            if client is not None else []
        )
    else:
        rows = fac_rows
    row = next(
        (r for r in rows
         if _int_or_none(r.get("CODFAC")) == codigo and serie_of_row(r, "TIPFAC") == serie),
        None,
    )
    return {
        "serie": serie, "codigo": codigo, "numero": f"{serie}-{codigo:06d}",
        "row": row, "missing_serie": False, "ambiguous": False, "series": [serie],
    }


def unusable_invoice_info(order: Order) -> dict[str, Any] | None:
    """Respuesta de «Registrar cobro» cuando el pedido no tiene una factura
    utilizable, SIN consultar FACTUSOL: `sin_factura` (el botón se
    deshabilita) o `unresolved` con `reason = "sin_serie"` (falta la serie).
    None si la factura está bien vinculada."""
    problem = invoice_link_problem(order)
    if problem == SIN_FACTURA:
        return {
            "status": "sin_factura", "invoice": None,
            "detail": "El pedido aún no tiene factura en FACTUSOL: emite la factura primero.",
        }
    if problem == SIN_SERIE:
        codigo = invoice_parts(order)[1]
        return {
            "status": "unresolved", "reason": SIN_SERIE,
            "invoice": {"serie": None, "codigo": codigo, "numero": str(codigo)},
            "detail": missing_invoice_detail(order),
        }
    return None


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


def suggest_for_order(
    session: Session, order: Order | None, *, serie: int | None, forma_nombre: Any = None,
) -> dict[str, Any]:
    """Cuenta sugerida para el cobro de la factura de un pedido y su porqué:
    lo apuntado a mano en el pedido (pago al dar de alta / convertir) manda; si
    no, las reglas tienda × método de pago de WooCommerce y, en su defecto, la
    cuenta de la serie. Devuelve `{suggested_cuenta, suggested_reason,
    suggested_fecha, forma_pago_nombre}`. Solo una sugerencia (editable)."""
    from app.erp.contrapartidas import suggest_contrapartida_explained  # noqa: PLC0415

    suggested, reason = suggest_contrapartida_explained(
        session, serie=serie, forma_nombre=forma_nombre,
        store=_store_slug(session, order) if order is not None else None,
        payment_method=getattr(order, "payment_method", None),
        payment_method_title=getattr(order, "payment_method_title", None),
    )
    suggested_fecha = None
    intent = payment_intent(order) if order is not None else None
    if intent:
        if intent.get("forma_pago_nombre"):
            forma_nombre = str(intent["forma_pago_nombre"])
        if intent.get("contrapartida"):
            suggested = {
                "codigo": str(intent["contrapartida"]),
                "nombre": str(intent.get("contrapartida_nombre") or intent["contrapartida"]),
            }
            reason = "pago apuntado en el pedido"
        if intent.get("fecha"):
            suggested_fecha = str(intent["fecha"])
    return {
        "suggested_cuenta": suggested, "suggested_reason": reason if suggested else None,
        "suggested_fecha": suggested_fecha, "forma_pago_nombre": forma_nombre,
    }


def suggest_for_invoice(
    session: Session, *, serie: int, codigo: int, referencia: Any = None,
    cliente_codigo: Any = None, forma_nombre: Any = None,
) -> dict[str, Any]:
    """Como `suggest_for_order` pero partiendo de una FACTURA (explorador de
    documentos, lote por CSV): busca el pedido de BoHub vinculado a ella (sin
    adivinar: `find_order_for_invoice`) para aplicar sus reglas tienda × método
    de pago; sin pedido, la cuenta de la serie. Añade `order_number`."""
    from app.erp.factusol_pdf import find_order_for_invoice  # noqa: PLC0415

    try:
        order = find_order_for_invoice(
            session, serie=serie, codigo=codigo, referencia=referencia,
            cliente_codigo=cliente_codigo,
        )
    except Exception:  # noqa: BLE001 — sin pedido, la sugerencia por serie
        logger.warning("cobro: no se pudo localizar el pedido de %s-%s", serie, codigo,
                       exc_info=True)
        order = None
    out = suggest_for_order(session, order, serie=serie, forma_nombre=forma_nombre)
    out["order_number"] = order.order_number if order is not None else None
    return out


def order_cobro_info(
    session: Session, client: FactusolClient, order: Order, ejercicio: str, *,
    fac_rows: list[dict[str, Any]] | None = None,
    index: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
    fop_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Estado de cobro EN VIVO de la factura del pedido, ya persistido:

    - `sin_factura`: el pedido no tiene factura (el botón se deshabilita);
    - `unresolved` (`reason = "sin_serie"`): hay CODFAC pero falta la serie —
      no se busca por el número solo, sin tocar FACTUSOL;
    - `not_found`: la factura (serie + código) ya no está en F_FAC;
    - `pendiente` / `cobrada`: con total, cobrado, saldo, ESTFAC, nº de
      líneas de cobro, forma de pago, cuenta sugerida y avisos (posible doble
      cobro: ya hay líneas de cobro sin llegar al total)."""

    early = unusable_invoice_info(order)
    if early is not None:
        return early
    key = resolve_invoice_key(
        session, order, fac_rows=fac_rows, client=client, ejercicio=ejercicio,
    )
    assert key is not None and key["serie"] is not None  # noqa: S101 — cubierto arriba
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
    if status_info["estfac"] == "2" and not status_info["ya_cobrada"]:
        warnings.append(
            f"FACTUSOL la marca cobrada (ESTFAC=2), pero sus líneas de cobro suman "
            f"{status_info['total_cobrado']:.2f} € de {status_info['total']:.2f} €: "
            "se trata como pendiente."
        )
    # Bloque B: si al dar de alta (o convertir) se apuntó el pago en el pedido
    # —forma, cuenta y fecha—, eso PRELLENA «Registrar cobro» en vez de
    # adivinarlo: lo que tecleó el usuario manda sobre la heurística. Si no, la
    # regla tienda × método de pago de Woo o la cuenta de la serie. Es solo
    # una sugerencia; el operador la cambia si quiere.
    sug = suggest_for_order(
        session, order, serie=key["serie"],
        forma_nombre=_forma_nombre(order, status_info["fopfac"], fop_names),
    )
    forma_nombre = sug["forma_pago_nombre"]
    suggested = sug["suggested_cuenta"]
    suggested_fecha = sug["suggested_fecha"]
    return {
        "status": COBRADA if status_info["ya_cobrada"] else PENDIENTE,
        "invoice": invoice,
        "cliente": status_info["cliente"], "referencia": status_info["referencia"],
        "total": status_info["total"], "total_cobrado": status_info["total_cobrado"],
        "saldo_pendiente": status_info["saldo_pendiente"],
        "estfac": status_info["estfac"], "cobros": status_info["cobros"],
        "fopfac": status_info["fopfac"], "forma_pago_nombre": forma_nombre,
        "suggested_cuenta": suggested, "suggested_fecha": suggested_fecha,
        "suggested_reason": sug["suggested_reason"],
        "payment_method": order.payment_method,
        "payment_method_title": order.payment_method_title,
        "warnings": warnings,
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
    """Tras registrar el cobro (job F-4-B, siempre manual): deja el pedido con
    el estado REAL que queda en FACTUSOL sin releerlo — «cobrada» si el cobro
    llega al total, «pendiente» si fue PARCIAL (queda saldo). `already` también
    cuenta (ya estaba cobrada). False si el resultado no fue un cobro."""
    registered = bool(result.get("registered"))
    if not registered and result.get("status") != "already":
        return False
    numero = f"{int(serie)}-{int(codigo):06d}"
    total = result.get("total")
    if registered:
        saldo = float(result.get("saldo_pendiente") or 0.0)
        cobrada = bool(result.get("cobrada", saldo <= 0.005))
        status_info = {
            "serie": int(serie), "codigo": int(codigo), "numero": numero,
            "total": total,
            "total_cobrado": result.get("total_cobrado", total),
            "saldo_pendiente": saldo,
            "estfac": result.get("estfac") or ("2" if cobrada else "1"),
            "cobros": result.get("cobros"),
            "fopfac": result.get("fopfac"), "ya_cobrada": cobrada,
        }
    else:
        cobrada = True
        status_info = {
            "serie": int(serie), "codigo": int(codigo), "numero": numero,
            "total": total,
            "total_cobrado": result.get("total_cobrado", total),
            "saldo_pendiente": result.get("saldo_pendiente", 0.0),
            "estfac": result.get("estfac") or "2",
            "cobros": result.get("cobros"),
            "fopfac": result.get("fopfac"), "ya_cobrada": True,
        }
    block = persist_cobro(session, order, status_info, source=source)
    paid = _status_value(order.payment_status)
    if registered:
        reason = (
            f"Cobro de {_eur(result.get('importe'))} € registrado en FACTUSOL para la "
            f"factura {numero} ({source})"
        )
        if not cobrada:
            reason += f" — parcial: quedan {_eur(status_info['saldo_pendiente'])} € pendientes"
    else:
        reason = f"La factura {numero} ya constaba cobrada en FACTUSOL ({source})"
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PAYMENT,
        from_status=paid, to_status=paid, changed_at=_now(),
        changed_by_user_id=actor_user_id, reason=reason[:255],
        metadata_json=json.dumps({
            "event": "factusol_cobro", "source": source, **block,
            "linlco": result.get("linlco"), "importe": result.get("importe"),
            "contrapartida": result.get("contrapartida"),
        }),
    ))
    return True


def _eur(value: Any) -> str:
    """333.96 → «333,96» (como en FACTUSOL)."""
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _fecha_es(value: Any) -> str:
    """«2026-09-23» → «23/09/2026»."""
    text = str(value or "")[:10]
    parts = text.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else text


def orders_for_invoice(session: Session, *, serie: int, codigo: int) -> list[Order]:
    """Pedidos cuya factura VINCULADA es exactamente esa (serie + código): un
    homónimo de otra serie o un pedido sin la serie no entran."""
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415

    candidates = session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(
            [str(int(codigo)), f"{int(serie)}-{int(codigo):06d}"],
        ))
    ).all()
    out = []
    for order in candidates:
        linked = get_linked_invoice(order)
        if linked is not None and (linked.serie, linked.codigo) == (int(serie), int(codigo)):
            out.append(order)
    return out


# --- cobros que registró BoHub (anular / corregir) -------------------------------

#: Auditoría del registro de un cobro (lo escribe el job F-4-B) y de su anulación.
REGISTERED_EVENT = "erp.invoice_collection_registered"
ANNULLED_EVENT = "erp.invoice_collection_annulled"


def bohub_collections(session: Session, *, serie: int, codigo: int) -> list[dict[str, Any]]:
    """Cobros de la factura que registró BoHub (según su auditoría), en orden,
    con su línea de F_LCO, fecha, importe y contrapartida, y si ya se anularon.
    Solo estos se pueden anular / corregir desde BoHub: un cobro hecho a mano
    en FACTUSOL no está aquí."""
    from app.erp.contrapartidas import resolve_contrapartida  # noqa: PLC0415
    from app.models.crm import AuditLog  # noqa: PLC0415

    numero = f"{int(serie)}-{int(codigo):06d}"
    rows = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action.in_([REGISTERED_EVENT, ANNULLED_EVENT]),
            AuditLog.metadata_json.like(f"%{numero}%"),
        )
        .order_by(AuditLog.created_at, AuditLog.id)
    ).all()
    registrados: list[dict[str, Any]] = []
    anulados: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            meta = json.loads(r.metadata_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("numero") != numero:
            continue
        if r.action == ANNULLED_EVENT:
            if meta.get("registered_event_id"):
                anulados[str(meta["registered_event_id"])] = {
                    "at": r.created_at.isoformat() if r.created_at else None,
                    "by": r.actor_email,
                }
            continue
        linlco = _int_or_none(meta.get("linlco"))
        codigo_cpa = str(meta.get("contrapartida") or "") or None
        registrados.append({
            "id": r.id, "numero": numero, "serie": int(serie), "codigo": int(codigo),
            "linlco": linlco, "fecha": meta.get("fecha"),
            "importe": meta.get("importe"),
            "contrapartida": codigo_cpa,
            "contrapartida_nombre": (
                resolve_contrapartida(session, codigo_cpa) if codigo_cpa else None
            ),
            "registrado_at": r.created_at.isoformat() if r.created_at else None,
            "registrado_por": r.actor_email,
        })
    for c in registrados:
        anul = anulados.get(c["id"])
        c["anulado"] = anul is not None
        c["anulado_at"] = anul["at"] if anul else None
        # Se puede anular si no se anuló ya y la auditoría trae la línea.
        c["anulable"] = anul is None and c["linlco"] is not None
    return registrados


def bohub_collection(session: Session, event_id: str) -> dict[str, Any] | None:
    """Un cobro registrado por BoHub por el id de su evento de auditoría."""
    from app.models.crm import AuditLog  # noqa: PLC0415

    ev = session.get(AuditLog, event_id)
    if ev is None or ev.action != REGISTERED_EVENT:
        return None
    try:
        meta = json.loads(ev.metadata_json or "{}")
    except (TypeError, ValueError):
        return None
    serie, codigo = parse_invoice_number(meta.get("numero"))
    if serie is None or codigo is None:
        return None
    return next(
        (c for c in bohub_collections(session, serie=serie, codigo=codigo)
         if c["id"] == event_id),
        None,
    )


def annul_reason(cobro: dict[str, Any]) -> str:
    """«Cobro de 333,96 € del 23/09/2026 (contrapartida 8) anulado»."""
    return (
        f"Cobro de {_eur(cobro.get('importe'))} € del {_fecha_es(cobro.get('fecha'))} "
        f"(contrapartida {cobro.get('contrapartida')}) anulado"
    )


def mark_orders_after_annul(
    session: Session, *, cobro: dict[str, Any], result: dict[str, Any],
    actor_user_id: str | None = None,
) -> list[Order]:
    """Tras anular: los pedidos de esa factura quedan con el estado REAL que
    dejan las líneas que quedan (pendiente si no llega al total) y con la
    anotación en su historial."""
    serie, codigo = int(cobro["serie"]), int(cobro["codigo"])
    saldo = float(result.get("saldo_pendiente") or 0.0)
    status_info = {
        "serie": serie, "codigo": codigo, "numero": cobro["numero"],
        "total": result.get("total"),
        "total_cobrado": result.get("total_cobrado"),
        "saldo_pendiente": saldo,
        "estfac": result.get("estfac"),
        "cobros": result.get("cobros"),
        "fopfac": None, "ya_cobrada": saldo <= 0.005,
    }
    reason = annul_reason(cobro)
    if result.get("status") == "line_missing":
        reason += " (la línea ya no estaba en FACTUSOL: solo se corrigió el estado)"
    updated: list[Order] = []
    for order in orders_for_invoice(session, serie=serie, codigo=codigo):
        block = persist_cobro(session, order, status_info, source="anulado")
        paid = _status_value(order.payment_status)
        session.add(OrderStatusHistory(
            order_id=order.id, domain=StatusDomain.PAYMENT,
            from_status=paid, to_status=paid, changed_at=_now(),
            changed_by_user_id=actor_user_id, reason=reason[:255],
            metadata_json=json.dumps({
                "event": "factusol_cobro_anulado", **block,
                "registered_event_id": cobro["id"], "linlco": cobro.get("linlco"),
                "importe": cobro.get("importe"), "fecha": cobro.get("fecha"),
                "contrapartida": cobro.get("contrapartida"),
            }),
        ))
        updated.append(order)
    return updated


def mark_orders_after_collection(
    session: Session, *, serie: int, codigo: int, result: dict[str, Any],
    actor_user_id: str | None = None,
) -> list[Order]:
    """Enganche del job de cobro (`register_invoice_collection_job`): los
    pedidos vinculados a la factura `serie-codigo` quedan con el estado de
    cobro real («cobrada», o «pendiente» si fue parcial) aunque el operador
    cierre el modal antes de que termine."""
    return [
        order for order in orders_for_invoice(session, serie=serie, codigo=codigo)
        if mark_order_from_result(
            session, order, serie=serie, codigo=codigo, result=result,
            source="manual", actor_user_id=actor_user_id,
        )
    ]
