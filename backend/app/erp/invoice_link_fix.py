"""Lote 2 · Bloque B — corregir vínculos factura↔pedido CRUZADOS (dry-run / apply).

El escaneo (`invoice_link_scan`) detecta pedidos cuyo nº de factura apunta a la
factura de OTRO cliente (herencia del CODFAC desnudo + serie compartida). La
corrección es determinista, nunca adivina:

- Para cada pedido cruzado se busca en F_FAC la factura cuya **REFFAC** es la
  referencia común del pedido (`BOP-099919`, `ART-009537`…).
- **Exactamente una** → se re-vincula a ella guardando **serie + número**
  (`factusol_invoice_serie` + `factusol_invoice_number`).
- **Ninguna** → el pedido queda **sin vínculo** (mejor sin factura que con la
  de otro): nº y serie a NULL, `invoice_status` vuelve a «sin facturar» si lo
  había puesto el vínculo.
- **Varias** → ambiguo: se informa y no se toca.

En ambos casos se limpia el estado de cobro cacheado (era de la factura
equivocada), queda historial en el pedido y evento de auditoría. SOLO escribe
en BoHub (campos del pedido); FACTUSOL solo se lee.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.factusol_albaran import packing_of, save_packing
from app.erp.factusol_cobro import COBRO_KEY, serie_of_row
from app.erp.factusol_pdf import order_composed_ref
from app.erp.invoice_link_scan import scan_invoice_links
from app.erp.models import InvoiceStatus, Order, OrderStatusHistory, StatusDomain
from app.integrations.factusol.documents import visible_number

logger = logging.getLogger(__name__)

RELINK = "relink"
UNLINK = "unlink"
AMBIGUOUS = "ambiguous"

FIXED_EVENT = "erp.invoice_link_fixed"
REMOVED_EVENT = "erp.invoice_link_removed"

#: Estados de facturación que puso el vínculo (y que se deshacen al quitarlo).
_LINK_STATUSES = {InvoiceStatus.INVOICED_BY_ERP.value, InvoiceStatus.GENERATED.value}


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _status_value(v: Any) -> str:
    return getattr(v, "value", v)


def plan_relinks(
    session: Session, client: Any, *, ejercicio: str,
    only: set[str] | None = None, categories: tuple[str, ...] = ("cruzado",),
) -> dict[str, Any]:
    """Plan de corrección (sin escribir nada). `only`: nº de pedido a tratar;
    `categories`: categorías del escaneo a corregir (por defecto solo
    «cruzado»)."""
    f_fac = client.load_table("F_FAC", ejercicio=ejercicio)
    scan = scan_invoice_links(session, client, ejercicio=ejercicio, f_fac_rows=f_fac)
    by_ref: dict[str, list[dict[str, Any]]] = {}
    for row in f_fac:
        ref = str(row.get("REFFAC") or "").strip().upper()
        if ref:
            by_ref.setdefault(ref, []).append(row)

    plans: list[dict[str, Any]] = []
    for r in scan["filas"]:
        if r["categoria"] not in categories:
            continue
        if only and r["order_number"] not in only:
            continue
        order = session.get(Order, r["order_id"])
        if order is None:
            continue
        ref = order_composed_ref(session, order)
        matches = by_ref.get(ref, []) if ref else []
        base = {
            "order_id": order.id, "order_number": order.order_number,
            "referencia": ref, "categoria": r["categoria"],
            "actual": {
                "numero": r["factura_guardada"], "serie": r["serie_resuelta"],
                "clifac": r["clifac"], "cnofac": r["cnofac"],
            },
        }
        if len(matches) == 1:
            row = matches[0]
            serie = serie_of_row(row, "TIPFAC")
            codigo = _int(row.get("CODFAC"))
            plans.append({
                **base, "action": RELINK,
                "nueva": {
                    "serie": serie, "codigo": codigo,
                    "numero": visible_number(serie, codigo),
                    "clifac": str(row.get("CLIFAC") or "").strip(),
                    "cnofac": str(row.get("CNOFAC") or "").strip(),
                },
            })
        elif not matches:
            plans.append({**base, "action": UNLINK, "nueva": None})
        else:
            plans.append({
                **base, "action": AMBIGUOUS,
                "nueva": None,
                "candidatas": [
                    visible_number(serie_of_row(m, "TIPFAC"), _int(m.get("CODFAC")))
                    for m in matches
                ],
            })
    totals = {RELINK: 0, UNLINK: 0, AMBIGUOUS: 0}
    for p in plans:
        totals[p["action"]] += 1
    return {"ejercicio": ejercicio, "plans": plans, "totals": totals,
            "scan_totales": scan["totales"]}


def _clear_cobro(order: Order) -> None:
    order.factusol_cobro_status = None
    order.factusol_cobro_checked_at = None
    packing = packing_of(order)
    if COBRO_KEY in packing:
        packing.pop(COBRO_KEY, None)
        save_packing(order, packing)


def _history(session: Session, order: Order, *, reason: str, metadata: dict[str, Any],
             from_status: str, to_status: str, actor_user_id: str | None) -> None:
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=from_status, to_status=to_status,
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason=reason[:255], metadata_json=json.dumps(metadata, default=str),
    ))


def relink_order(
    session: Session, order: Order, *, serie: int, codigo: int, referencia: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Apunta el pedido a SU factura (serie + número) y limpia el cobro cacheado."""
    from app.core.audit import record_event  # noqa: PLC0415

    before = order.factusol_invoice_number
    before_serie = order.factusol_invoice_serie
    numero = visible_number(serie, codigo)
    status = _status_value(order.invoice_status)
    order.factusol_invoice_number = str(int(codigo))
    order.factusol_invoice_serie = int(serie)
    if status not in _LINK_STATUSES:
        order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    _clear_cobro(order)
    reason = f"Vínculo de factura corregido: {before or '—'} → {numero} (REFFAC {referencia})"
    meta = {
        "antes": {"numero": before, "serie": before_serie},
        "despues": {"numero": str(int(codigo)), "serie": int(serie)},
        "referencia": referencia, "source": "invoice_link_fix",
    }
    _history(session, order, reason=reason, metadata=meta, from_status=status,
             to_status=_status_value(order.invoice_status), actor_user_id=actor_user_id)
    record_event(session, action=FIXED_EVENT, target_type="order", target_id=order.id,
                 actor=None, message=reason, metadata=meta)
    return {"order_number": order.order_number, "action": RELINK, "numero": numero}


def unlink_order(
    session: Session, order: Order, *, referencia: str, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Quita un vínculo a la factura de OTRO cliente sin sustituto: el pedido
    queda sin factura (estado «sin facturar» si lo había puesto el vínculo)."""
    from app.core.audit import record_event  # noqa: PLC0415

    before = order.factusol_invoice_number
    before_serie = order.factusol_invoice_serie
    status = _status_value(order.invoice_status)
    order.factusol_invoice_number = None
    order.factusol_invoice_serie = None
    if status in _LINK_STATUSES:
        order.invoice_status = InvoiceStatus.NOT_INVOICED.value
    _clear_cobro(order)
    reason = (
        f"Vínculo de factura retirado: {before or '—'} era de otro cliente y no "
        f"hay factura con REFFAC {referencia}"
    )
    meta = {
        "antes": {"numero": before, "serie": before_serie}, "despues": None,
        "referencia": referencia, "source": "invoice_link_fix",
    }
    _history(session, order, reason=reason, metadata=meta, from_status=status,
             to_status=_status_value(order.invoice_status), actor_user_id=actor_user_id)
    record_event(session, action=REMOVED_EVENT, target_type="order", target_id=order.id,
                 actor=None, message=reason, metadata=meta)
    return {"order_number": order.order_number, "action": UNLINK, "numero": None}


def apply_relinks(
    session: Session, plan: dict[str, Any], *, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el plan (relink / unlink; los ambiguos se saltan) y hace commit."""
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for p in plan["plans"]:
        order = session.get(Order, p["order_id"])
        if order is None:
            skipped.append({"order_number": p["order_number"], "reason": "no existe"})
            continue
        if p["action"] == RELINK:
            done.append(relink_order(
                session, order, serie=int(p["nueva"]["serie"]), codigo=int(p["nueva"]["codigo"]),
                referencia=p["referencia"], actor_user_id=actor_user_id,
            ))
        elif p["action"] == UNLINK:
            done.append(unlink_order(
                session, order, referencia=p["referencia"], actor_user_id=actor_user_id,
            ))
        else:
            skipped.append({"order_number": p["order_number"],
                            "reason": f"ambiguo: {', '.join(p.get('candidatas') or [])}"})
    session.commit()
    logger.info("invoice_link_fix: %d corregidos, %d omitidos", len(done), len(skipped))
    return {"done": done, "skipped": skipped}


def format_plan(plan: dict[str, Any]) -> str:
    t = plan["totals"]
    lines = [
        f"Ejercicio {plan['ejercicio']} · escaneo: {plan['scan_totales']}",
        f"Plan: re-vincular={t[RELINK]}  quitar vínculo={t[UNLINK]}  ambiguos={t[AMBIGUOUS]}",
        "",
    ]
    for p in plan["plans"]:
        a = p["actual"]
        head = (
            f"{p['order_number']:<14} ref={p['referencia'] or '—':<12} "
            f"ahora → {a['numero']} (serie {a['serie'] if a['serie'] is not None else '?'}) "
            f"CLIFAC {a['clifac'] or '—'} «{a['cnofac']}»"
        )
        if p["action"] == RELINK:
            n = p["nueva"]
            lines.append(f"[RELINK  ] {head}")
            lines.append(
                f"            → {n['numero']} CLIFAC {n['clifac']} «{n['cnofac']}» "
                "(REFFAC = ref)"
            )
        elif p["action"] == UNLINK:
            lines.append(f"[UNLINK  ] {head}")
            lines.append("            → sin vínculo (no hay factura con esa REFFAC)")
        else:
            candidatas = ", ".join(p.get("candidatas") or [])
            lines.append(f"[AMBIGUO ] {head}")
            lines.append(f"            → varias facturas con esa REFFAC: {candidatas}")
    if not plan["plans"]:
        lines.append("(nada que corregir)")
    return "\n".join(lines)
