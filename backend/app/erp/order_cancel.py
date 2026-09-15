"""Lote ERP · «Anular pedido» (manual / FACTUSOL; nunca web).

Distinto de «quitar/ocultar»: anular es un estado FINAL del pedido en BoHub
(`cancelled_at/by/reason`, reversible con «Restaurar»), y opcionalmente —
con aviso y confirmación explícita — se BORRAN en FACTUSOL el albarán y/o el
presupuesto del pedido si siguen «vivos» (no facturado / no aceptado ni
convertido). La FACTURA nunca se toca: un pedido con factura no se anula desde
BoHub (se anula la factura en FACTUSOL y luego se anula aquí).

Borrar en FACTUSOL usa la ÚNICA primitiva que existe (`delete_records`, hasta
ahora solo compensación de escrituras a medias) con el patrón seguro: filtro
por (serie, código) — el nº solo es único por serie — líneas primero y
cabecera después; en el worker serializado `factusol:writes`, re-comprobando
en vivo que el documento sigue siendo borrable justo antes de borrar.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.factusol_albaran import (
    ALBARAN_KEY,
    is_web_order,
    order_source,
    packing_of,
    save_packing,
)
from app.erp.models import Order
from app.integrations.factusol.chain import find_existing_children
from app.integrations.factusol.documents import DOC_SPECS, get_document, visible_number

logger = logging.getLogger(__name__)

CANCELLED_EVENT = "erp.order_cancelled"
UNCANCELLED_EVENT = "erp.order_uncancelled"
DOCS_DELETED_EVENT = "erp.order_cancel_docs_deleted"

#: Estados de FACTUSOL a partir de los cuales el documento ya no se borra:
#: ESTALB=1 «Facturado»; ESTPRE≠0 «Aceptado» (o cualquier otro valor).
ALBARAN_INVOICED_STATE = 1
QUOTE_PENDING_STATE = 0


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_invoiced_order(order: Order) -> bool:
    from app.erp.workflow import is_invoiced  # noqa: PLC0415

    return is_invoiced(order)


def cancel_blockers(order: Order) -> list[str]:
    """Por qué NO se puede anular (lista vacía = se puede)."""
    out: list[str] = []
    if is_web_order(order):
        out.append("Es un pedido web: se anula en WooCommerce, no desde BoHub.")
    if is_invoiced_order(order):
        numero = order.factusol_invoice_number or "—"
        out.append(
            f"Tiene factura en FACTUSOL ({numero}): la factura se anula desde "
            "FACTUSOL; después se puede anular el pedido aquí."
        )
    return out


def albaran_key(order: Order) -> tuple[int, int] | None:
    """(serie, código) del albarán FACTUSOL del pedido, si consta."""
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415

    serie, codigo = parse_invoice_number(order.factusol_albaran_number)
    if serie is not None and codigo is not None:
        return serie, codigo
    block = packing_of(order).get(ALBARAN_KEY)
    if isinstance(block, dict):
        s, c = _int(block.get("serie")), _int(block.get("codigo"))
        if s is not None and c is not None:
            return s, c
    return None


def quote_key(order: Order) -> tuple[int, int] | None:
    """(serie, código) del presupuesto FACTUSOL del que nació el pedido."""
    src = order_source(order)
    if not src or src.get("doc_type") != "presupuestos":
        return None
    return int(src["serie"]), int(src["codigo"])


def _doc_entry(doc_type: str, serie: int, codigo: int, **over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "doc_type": doc_type, "serie": serie, "codigo": codigo,
        "numero": visible_number(serie, codigo),
        "estado": None, "deletable": False, "reason": None,
    }
    entry.update(over)
    return entry


def factusol_docs_for_cancel(
    client: Any, order: Order, *, ejercicio: str,
) -> list[dict[str, Any]]:
    """Documentos FACTUSOL del pedido y si cada uno se puede BORRAR ahora
    (lectura en vivo): el albarán si no está facturado (ESTALB≠1) y no tiene
    facturas hijas; el presupuesto si sigue pendiente (ESTPRE=0) y no tiene
    más albaranes hijos que el del propio pedido."""
    docs: list[dict[str, Any]] = []
    alb = albaran_key(order)
    if alb is not None:
        serie, codigo = alb
        doc = get_document(client, "albaranes", serie=serie, codigo=codigo, ejercicio=ejercicio)
        if doc is None:
            docs.append(_doc_entry(
                "albaranes", serie, codigo,
                reason=f"No existe en FACTUSOL (ejercicio {ejercicio}).",
            ))
        else:
            estado = _int(doc.get("estado"))
            children = find_existing_children(
                client, "albaranes", "facturas", tip=serie, cod=codigo, ejercicio=ejercicio,
            )
            if estado == ALBARAN_INVOICED_STATE or children:
                reason = (
                    f"Ya está facturado ({', '.join(children)})." if children
                    else "Ya está facturado (ESTALB=1)."
                )
                docs.append(_doc_entry("albaranes", serie, codigo, estado=estado, reason=reason))
            else:
                docs.append(_doc_entry("albaranes", serie, codigo, estado=estado, deletable=True))
    pre = quote_key(order)
    if pre is not None:
        serie, codigo = pre
        doc = get_document(client, "presupuestos", serie=serie, codigo=codigo, ejercicio=ejercicio)
        if doc is None:
            docs.append(_doc_entry(
                "presupuestos", serie, codigo,
                reason=f"No existe en FACTUSOL (ejercicio {ejercicio}).",
            ))
        else:
            estado = _int(doc.get("estado"))
            own_albaran = visible_number(alb[0], alb[1]) if alb else None
            children = [
                c for c in find_existing_children(
                    client, "presupuestos", "albaranes", tip=serie, cod=codigo,
                    ejercicio=ejercicio,
                )
                if c != own_albaran
            ]
            if estado not in (None, QUOTE_PENDING_STATE):
                docs.append(_doc_entry(
                    "presupuestos", serie, codigo, estado=estado,
                    reason=f"Ya no está pendiente en FACTUSOL (ESTPRE={estado}).",
                ))
            elif children:
                docs.append(_doc_entry(
                    "presupuestos", serie, codigo, estado=estado,
                    reason=f"Tiene otros albaranes ({', '.join(children)}).",
                ))
            else:
                docs.append(_doc_entry(
                    "presupuestos", serie, codigo, estado=estado, deletable=True,
                ))
    return docs


def mark_cancelled(session: Session, order: Order, actor: Any, reason: str | None) -> bool:
    """Sella la anulación. Devuelve si YA estaba anulado. No hace commit."""
    _ = session
    if order.cancelled_at is not None:
        return True
    order.cancelled_at = datetime.now(UTC)
    order.cancelled_by_user_id = getattr(actor, "id", None)
    order.cancelled_reason = (reason or "").strip()[:255] or None
    return False


def unmark_cancelled(session: Session, order: Order) -> bool:
    """«Restaurar»: limpia la anulación. Devuelve si YA estaba activo."""
    _ = session
    if order.cancelled_at is None:
        return True
    order.cancelled_at = None
    order.cancelled_by_user_id = None
    order.cancelled_reason = None
    return False


def delete_factusol_document(
    client: Any, doc_type: str, *, serie: int, codigo: int, ejercicio: str,
) -> None:
    """Borra líneas y cabecera por clave COMPUESTA (serie, código)."""
    spec = DOC_SPECS[doc_type]
    client.delete_records(
        spec.lines_table, f"{spec.line_tip}='{serie}' AND {spec.line_fk}='{codigo}'",
        ejercicio=ejercicio,
    )
    client.delete_records(
        spec.table, f"{spec.tip}='{serie}' AND {spec.cod}='{codigo}'",
        ejercicio=ejercicio,
    )


def delete_cancelled_order_documents(
    session: Session, client: Any, order: Order, docs: list[dict[str, Any]], *,
    ejercicio: str, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Borra en FACTUSOL los documentos pedidos (albarán antes que presupuesto),
    RE-COMPROBANDO en vivo que cada uno sigue siendo borrable; desvincula en
    BoHub lo borrado y lo deja en el timeline. Un documento que ya no es
    borrable se salta (con su motivo), nunca se fuerza."""
    from app.core.audit import record_event  # noqa: PLC0415

    live = {
        (d["doc_type"], int(d["serie"]), int(d["codigo"])): d
        for d in factusol_docs_for_cancel(client, order, ejercicio=ejercicio)
    }
    wanted = sorted(
        ((d["doc_type"], int(d["serie"]), int(d["codigo"])) for d in docs),
        key=lambda k: 0 if k[0] == "albaranes" else 1,
    )
    deleted: list[str] = []
    skipped: list[dict[str, Any]] = []
    for doc_type, serie, codigo in wanted:
        numero = visible_number(serie, codigo)
        current = live.get((doc_type, serie, codigo))
        if current is None or not current.get("deletable"):
            skipped.append({
                "doc_type": doc_type, "numero": numero,
                "reason": (current or {}).get("reason") or "Ya no consta en el pedido.",
            })
            continue
        delete_factusol_document(client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio)
        deleted.append(f"{doc_type}:{numero}")
        if doc_type == "albaranes":
            order.factusol_albaran_number = None
            packing = packing_of(order)
            packing.pop(ALBARAN_KEY, None)
            save_packing(order, packing)
        else:
            packing = packing_of(order)
            src = packing.get("factusol_source")
            if isinstance(src, dict):
                src["deleted_at"] = datetime.now(UTC).isoformat()
                packing["factusol_source"] = src
                save_packing(order, packing)
    record_event(
        session,
        action=DOCS_DELETED_EVENT,
        target_type="order",
        target_id=order.id,
        actor=None,
        message=(
            "Borrado en FACTUSOL al anular: " + (", ".join(deleted) or "nada")
            + (f" · omitidos: {len(skipped)}" if skipped else "")
        ),
        metadata={
            "deleted": deleted, "skipped": skipped, "ejercicio": ejercicio,
            "actor_user_id": actor_user_id,
        },
    )
    session.commit()
    return {"deleted": deleted, "skipped": skipped}
