"""ERP · documentos FACTUSOL VINCULADOS a un pedido: listar, vincular a una
muestra (cargando sus datos) y desvincular — sin escribir NUNCA en FACTUSOL.

Un pedido puede apuntar a tres clases de documento:

- su **factura** (`invoice_status` + `factusol_invoice_number` + serie, la
  regla única de #493, `app.erp.linked_invoice`);
- su **albarán** (`factusol_albaran_number` + `packing_json.factusol_albaran`);
- su **documento de origen** proforma / pedido de cliente (`external_source`
  `factusol_proforma` / `factusol_pedido` + `external_id` +
  `packing_json.factusol_source`).

**Vincular a una muestra** (rev. 30/09/2026 — MUESTRA-000003 ↔ 2-526110): la
muestra carga los datos del documento (cliente → empresa CRM, líneas e
importes, serie, forma de pago y el vínculo) y pasa a comportarse como un
pedido creado desde ese documento (`order_kind = sample_converted`). Guarda
una foto de la muestra original para poder volver a ella.

**Desvincular** (a mano, o al ANULAR el pedido): el documento sigue en
FACTUSOL tal cual; el pedido deja de apuntarlo y queda rastro en el
historial. Una muestra convertida que se queda sin documentos vuelve al modo
muestra (no facturable, sus líneas originales).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.erp.factusol_albaran import ALBARAN_KEY, packing_of, save_packing
from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    StatusDomain,
)
from app.erp.sample_orders import (
    ORDER_KIND_SAMPLE,
    ORDER_KIND_SAMPLE_CONVERTED,
    is_sample_order,
)
from app.integrations.factusol.documents import visible_number

logger = logging.getLogger(__name__)

KIND_FACTURA = "factura"
KIND_ALBARAN = "albaran"
KIND_ORIGEN = "origen"
KINDS = (KIND_FACTURA, KIND_ALBARAN, KIND_ORIGEN)

#: Tipos de documento que se pueden vincular a una muestra.
LINKABLE_DOC_TYPES = ("albaranes", "presupuestos", "facturas")

#: Foto de la muestra original (para volver al modo muestra al desvincular).
SAMPLE_SNAPSHOT_KEY = "sample_original"
#: Documentos que se desvincularon al ANULAR (para re-vincularlos al restaurar).
CANCEL_UNLINKED_KEY = "cancel_unlinked"
#: Rastro de los documentos de origen desvinculados.
UNLINKED_KEY = "unlinked_documents"

LINKED_EVENT = "erp.sample_document_linked"
UNLINKED_EVENT = "erp.order_document_unlinked"

_ORIGIN_SOURCES = {
    OrderSource.FACTUSOL_PROFORMA.value: "presupuestos",
    OrderSource.FACTUSOL_PEDIDO.value: "pedidos",
}
_NOUN = {
    "facturas": ("factura", "a"),
    "albaranes": ("albarán", "o"),
    "presupuestos": ("proforma", "a"),
    "pedidos": ("pedido de cliente", "o"),
}
_INVOICED = {
    InvoiceStatus.GENERATED.value,
    InvoiceStatus.INVOICED_BY_ERP.value,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value,
    InvoiceStatus.CREDIT_NOTE.value,
}


class DocumentLinkError(Exception):
    """Error de negocio al vincular (código + detalle para el 409/400)."""

    def __init__(self, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra


def _v(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def doc_label(doc_type: str, numero: str) -> str:
    """«factura 2-526110», «albarán 5-000123», «proforma 1-000045»."""
    return f"{_NOUN.get(doc_type, ('documento', 'o'))[0]} {numero}"


def _unlinked_text(doc_type: str, numero: str) -> str:
    noun, g = _NOUN.get(doc_type, ("documento", "o"))
    return f"{noun} {numero} desvinculad{g} (sigue en FACTUSOL)"


def _entry(kind: str, doc_type: str, serie: int | None, codigo: int | None,
           numero: str) -> dict[str, Any]:
    return {
        "kind": kind, "doc_type": doc_type, "serie": serie, "codigo": codigo,
        "numero": numero, "label": doc_label(doc_type, numero),
    }


# --- qué documentos tiene vinculados ---------------------------------------------


def _invoice_entry(order: Order) -> dict[str, Any] | None:
    from app.erp.linked_invoice import invoice_label, invoice_parts  # noqa: PLC0415

    if not str(order.factusol_invoice_number or "").strip():
        return None
    serie, codigo = invoice_parts(order)
    return _entry(KIND_FACTURA, "facturas", serie, codigo, invoice_label(order))


def _albaran_entry(order: Order) -> dict[str, Any] | None:
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415

    numero = str(order.factusol_albaran_number or "").strip()
    if not numero:
        return None
    serie, codigo = parse_invoice_number(numero)
    return _entry(KIND_ALBARAN, "albaranes", serie, codigo, numero)


def _origin_entry(order: Order) -> dict[str, Any] | None:
    doc_type = _ORIGIN_SOURCES.get(_v(order.external_source))
    if doc_type is None or not order.external_id:
        return None
    src = packing_of(order).get("factusol_source")
    src = src if isinstance(src, dict) else {}
    serie, codigo = _int(src.get("serie")), _int(src.get("codigo"))
    numero = (
        visible_number(serie, codigo) if serie is not None and codigo is not None
        else str(order.external_id)
    )
    return _entry(KIND_ORIGEN, doc_type, serie, codigo, numero)


def linked_documents(order: Order) -> list[dict[str, Any]]:
    """Documentos FACTUSOL vinculados al pedido: origen (proforma / pedido de
    cliente), albarán y factura, en ese orden."""
    return [
        e for e in (_origin_entry(order), _albaran_entry(order), _invoice_entry(order))
        if e is not None
    ]


# --- desvincular -------------------------------------------------------------------


def _clear_cobro(order: Order) -> None:
    from app.erp.factusol_cobro import COBRO_KEY  # noqa: PLC0415

    order.factusol_cobro_status = None
    order.factusol_cobro_checked_at = None
    packing = packing_of(order)
    if COBRO_KEY in packing:
        packing.pop(COBRO_KEY, None)
        save_packing(order, packing)


def _drop_source_if(order: Order, doc_type: str, serie: int | None,
                    codigo: int | None) -> dict[str, Any] | None:
    """Si el documento de ORIGEN del pedido es este, deja de apuntarlo
    (`external_id` a NULL) y aparta su bloque `factusol_source` al rastro.
    Devuelve lo que había (para poder restaurarlo)."""
    from app.erp.orders_from_factusol import SOURCE_BY_DOC_TYPE  # noqa: PLC0415

    packing = packing_of(order)
    src = packing.get("factusol_source")
    same_source = _v(order.external_source) == SOURCE_BY_DOC_TYPE[doc_type].value
    src_matches = (
        isinstance(src, dict) and src.get("doc_type") == doc_type
        and _int(src.get("serie")) == serie and _int(src.get("codigo")) == codigo
    )
    if not (same_source and (src_matches or not isinstance(src, dict))):
        return None
    before = {"external_id": order.external_id, "factusol_source": src}
    order.external_id = None
    if isinstance(src, dict):
        packing.pop("factusol_source", None)
        rastro = packing.get(UNLINKED_KEY)
        rastro = rastro if isinstance(rastro, list) else []
        rastro.append({**src, "unlinked_at": datetime.now(UTC).isoformat()})
        packing[UNLINKED_KEY] = rastro[-20:]
        save_packing(order, packing)
    return before


def _unlink_fields(order: Order, entry: dict[str, Any]) -> dict[str, Any]:
    """Limpia en el pedido los campos de ESE documento. Devuelve lo que había
    (para `restore`). No escribe historial."""
    kind, doc_type = entry["kind"], entry["doc_type"]
    serie, codigo = entry["serie"], entry["codigo"]
    restore: dict[str, Any] = {}
    if kind == KIND_FACTURA:
        restore = {
            "factusol_invoice_number": order.factusol_invoice_number,
            "factusol_invoice_serie": order.factusol_invoice_serie,
            "invoice_status": _v(order.invoice_status),
        }
        order.factusol_invoice_number = None
        order.factusol_invoice_serie = None
        if _v(order.invoice_status) in _INVOICED:
            order.invoice_status = InvoiceStatus.NOT_INVOICED.value
        _clear_cobro(order)
    elif kind == KIND_ALBARAN:
        packing = packing_of(order)
        restore = {
            "factusol_albaran_number": order.factusol_albaran_number,
            "albaran_block": packing.get(ALBARAN_KEY),
        }
        order.factusol_albaran_number = None
        if ALBARAN_KEY in packing:
            packing.pop(ALBARAN_KEY, None)
            save_packing(order, packing)
    source = _drop_source_if(order, doc_type, serie, codigo)
    if source is not None:
        restore["source"] = source
    return restore


def _history(session: Session, order: Order, *, reason: str, actor_user_id: str | None,
             metadata: dict[str, Any], domain: StatusDomain = StatusDomain.PREPARATION,
             from_status: str | None = None, to_status: str | None = None) -> None:
    prep = _v(order.preparation_status)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=domain,
        from_status=from_status if from_status is not None else prep,
        to_status=to_status if to_status is not None else prep,
        changed_at=datetime.now(UTC), changed_by_user_id=actor_user_id,
        reason=reason[:255], metadata_json=json.dumps(metadata, default=str),
    ))


def find_entry(order: Order, kind: str) -> dict[str, Any] | None:
    for entry in linked_documents(order):
        if entry["kind"] == kind:
            return entry
    return None


def unlink_document(
    session: Session, order: Order, kind: str, *, actor: Any = None,
) -> dict[str, Any]:
    """«Desvincular documento» (sin anular): el pedido deja de apuntar el
    documento, que sigue en FACTUSOL. Rastro en el historial y la auditoría.
    Una muestra convertida sin más documentos vuelve al modo muestra. No hace
    commit. `DocumentLinkError('not_linked')` si no lo tiene."""
    from app.core.audit import record_event  # noqa: PLC0415

    entry = find_entry(order, kind)
    if entry is None:
        raise DocumentLinkError("not_linked", "El pedido no tiene ese documento vinculado.")
    actor_id = getattr(actor, "id", None)
    status_before = _v(order.invoice_status)
    restore = _unlink_fields(order, entry)
    text = _unlinked_text(entry["doc_type"], entry["numero"])
    reason = text[:1].upper() + text[1:]
    back_to_sample = restore_sample_mode(session, order)
    if back_to_sample:
        reason += "; la muestra vuelve a modo muestra (no facturable)"
    meta = {"event": "document_unlinked", "document": entry, "antes": restore,
            "back_to_sample": back_to_sample}
    _history(
        session, order, reason=reason, actor_user_id=actor_id, metadata=meta,
        **({"domain": StatusDomain.INVOICE, "from_status": status_before,
            "to_status": _v(order.invoice_status)} if kind == KIND_FACTURA else {}),
    )
    record_event(
        session, action=UNLINKED_EVENT, target_type="order", target_id=order.id,
        actor=actor, message=f"{order.order_number}: {reason}",
        metadata={"order_number": order.order_number, "document": entry,
                  "back_to_sample": back_to_sample},
    )
    return {"document": entry, "back_to_sample": back_to_sample}


def unlink_for_cancel(
    session: Session, order: Order, *,
    keep: set[tuple[str, int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Al ANULAR: desvincula todos los documentos (salvo los de `keep`, que el
    job de borrado en FACTUSOL todavía necesita) y guarda qué había para
    re-vincularlos si se restaura. No escribe historial (lo hace el caller en
    una sola línea). Devuelve los desvinculados."""
    keep = keep or set()
    done: list[dict[str, Any]] = []
    for entry in linked_documents(order):
        key = (entry["doc_type"], entry["serie"], entry["codigo"])
        if key in keep:
            continue
        restore = _unlink_fields(order, entry)
        done.append({**entry, "restore": restore})
    if done:
        packing = packing_of(order)
        previos = packing.get(CANCEL_UNLINKED_KEY)
        previos = previos if isinstance(previos, list) else []
        packing[CANCEL_UNLINKED_KEY] = [*previos, *done]
        save_packing(order, packing)
    return done


def cancel_history_reason(unlinked: list[dict[str, Any]], reason: str | None = None) -> str:
    """«Anulado; factura 2-526110 desvinculada (sigue en FACTUSOL)»."""
    text = "Anulado"
    if reason:
        text += f" ({reason.strip()})"
    if unlinked:
        text += "; " + ", ".join(_unlinked_text(e["doc_type"], e["numero"]) for e in unlinked)
    return text


def cancel_warnings(order: Order) -> list[dict[str, Any]]:
    """Qué documentos se desvincularán al anular, con el aviso de cada uno
    (para el modal): siguen en FACTUSOL y, si hay que anularlos o abonarlos,
    se hace allí."""
    out: list[dict[str, Any]] = []
    for entry in linked_documents(order):
        noun, g = _NOUN.get(entry["doc_type"], ("documento", "o"))
        text = (
            f"La {noun} {entry['numero']} seguirá existiendo en FACTUSOL y dejará "
            f"de estar vinculad{g} a este pedido."
            if g == "a" else
            f"El {noun} {entry['numero']} seguirá existiendo en FACTUSOL y dejará "
            f"de estar vinculad{g} a este pedido."
        )
        if entry["kind"] == KIND_FACTURA:
            text += " Si hay que anularla o abonarla, hazlo en FACTUSOL."
        out.append({**entry, "message": text})
    return out


def _holder_of(session: Session, entry: dict[str, Any], *, exclude_id: str) -> Order | None:
    """Otro pedido VIVO que ya apunta a ese documento (o None)."""
    return document_holder(
        session, entry["doc_type"], entry["serie"], entry["codigo"], exclude_order_id=exclude_id,
    )


def restore_after_uncancel(session: Session, order: Order, *, actor: Any = None) -> dict[str, Any]:
    """«Restaurar» un pedido anulado: re-vincula los documentos que se
    desvincularon al anular, salvo los que ya tiene otro pedido. No hace commit."""
    packing = packing_of(order)
    saved = packing.get(CANCEL_UNLINKED_KEY)
    if not isinstance(saved, list) or not saved:
        return {"restored": [], "skipped": []}
    restored: list[str] = []
    skipped: list[dict[str, Any]] = []
    for item in saved:
        if not isinstance(item, dict):
            continue
        r_src = (item.get("restore") or {}).get("source") or {}
        if isinstance(r_src.get("factusol_source"), dict) and r_src["factusol_source"].get(
            "deleted_at",
        ):
            skipped.append({"numero": item.get("numero"), "reason": "se borró en FACTUSOL"})
            continue
        holder = _holder_of(session, item, exclude_id=order.id)
        if holder is not None:
            skipped.append({"numero": item.get("numero"),
                            "reason": f"ya está vinculado a {holder.order_number}"})
            continue
        r = item.get("restore") or {}
        if item.get("kind") == KIND_FACTURA and r.get("factusol_invoice_number"):
            order.factusol_invoice_number = r["factusol_invoice_number"]
            order.factusol_invoice_serie = r.get("factusol_invoice_serie")
            order.invoice_status = r.get("invoice_status") or InvoiceStatus.INVOICED_BY_ERP.value
        elif item.get("kind") == KIND_ALBARAN and r.get("factusol_albaran_number"):
            order.factusol_albaran_number = r["factusol_albaran_number"]
            if isinstance(r.get("albaran_block"), dict):
                p = packing_of(order)
                p[ALBARAN_KEY] = r["albaran_block"]
                save_packing(order, p)
        src = r.get("source")
        if isinstance(src, dict) and src.get("external_id"):
            order.external_id = src["external_id"]
            if isinstance(src.get("factusol_source"), dict):
                p = packing_of(order)
                p["factusol_source"] = src["factusol_source"]
                save_packing(order, p)
        restored.append(str(item.get("label") or item.get("numero")))
    packing = packing_of(order)
    packing.pop(CANCEL_UNLINKED_KEY, None)
    save_packing(order, packing)
    if restored or skipped:
        reason = "Restaurado" + (
            "; vuelve a vincular: " + ", ".join(restored) if restored else ""
        ) + (
            "; no se re-vincula: " + ", ".join(f"{s['numero']} ({s['reason']})" for s in skipped)
            if skipped else ""
        )
        _history(session, order, reason=reason, actor_user_id=getattr(actor, "id", None),
                 metadata={"event": "uncancel_relink", "restored": restored,
                           "skipped": skipped})
    return {"restored": restored, "skipped": skipped}


# --- vincular un documento a una muestra --------------------------------------------


def document_holder(
    session: Session, doc_type: str, serie: int | None, codigo: int | None,
    *, exclude_order_id: str | None = None,
) -> Order | None:
    """El pedido VIVO (no anulado) que ya apunta a ese documento, o None."""
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        SOURCE_BY_DOC_TYPE,
        external_id_for,
        find_quote_order,
    )

    if serie is None or codigo is None:
        return None
    base = select(Order).where(Order.cancelled_at.is_(None))
    if exclude_order_id:
        base = base.where(Order.id != exclude_order_id)
    numero = visible_number(serie, codigo)
    if doc_type == "facturas":
        stmt = base.where(or_(
            (Order.factusol_invoice_number == str(int(codigo)))
            & (Order.factusol_invoice_serie == int(serie)),
            Order.factusol_invoice_number == numero,
        ))
    elif doc_type == "albaranes":
        stmt = base.where(Order.factusol_albaran_number == numero)
    elif doc_type == "presupuestos":
        holder = find_quote_order(session, int(serie), int(codigo))
        if holder is not None and holder.cancelled_at is None and holder.id != exclude_order_id:
            return holder
        return None
    else:
        stmt = base.where(
            Order.external_source == SOURCE_BY_DOC_TYPE[doc_type],
            Order.external_id == external_id_for(doc_type, serie, codigo),
        )
    return session.scalars(stmt).first()


def _sample_snapshot(order: Order) -> dict[str, Any]:
    return {
        "total_amount": float(order.total_amount or 0),
        "company_id": order.company_id,
        "contact_id": order.contact_id,
        "notes": order.notes,
        "lines": [
            {
                "position": ln.position, "product_sku": ln.product_sku,
                "product_codart": ln.product_codart, "description": ln.description,
                "quantity": float(ln.quantity), "unit_price": float(ln.unit_price),
                "tax_rate": float(ln.tax_rate), "line_total": float(ln.line_total),
                "notes": ln.notes, "is_shipping": bool(ln.is_shipping),
                "line_kind": ln.line_kind,
            }
            for ln in order.lines
        ],
        "at": datetime.now(UTC).isoformat(),
    }


def _replace_lines(session: Session, order: Order) -> None:
    for line in list(order.lines):
        session.delete(line)
    session.flush()
    session.expire(order, ["lines"])


def link_document_to_sample(
    session: Session, order: Order, preview: dict[str, Any], *,
    company_id: str | None = None, forma_pago_nombre: str | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    """Vincula el documento (ya leído con `preview_factusol_document`) a la
    MUESTRA y le carga sus datos: la muestra pasa a comportarse como un pedido
    creado desde ese documento. Solo lectura en FACTUSOL. No hace commit.

    Errores (`DocumentLinkError`): `not_a_sample` (solo muestras sin
    convertir), `unsupported_doc_type`, `document_linked_elsewhere` (otro
    pedido vivo ya lo tiene) y `factusol_customer_unlinked` (el cliente del
    documento no tiene empresa en el CRM: hay que vincularla o crearla)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.orders_from_factusol import (  # noqa: PLC0415
        DOC_LABEL,
        SOURCE_BY_DOC_TYPE,
        add_document_lines,
        document_lines_or_total,
        factusol_source_block,
        packing_con_destino,
    )

    doc_type = preview["doc_type"]
    serie, codigo = int(preview["serie"]), int(preview["codigo"])
    numero = preview["numero"]
    if not is_sample_order(order):
        raise DocumentLinkError(
            "not_a_sample",
            "Solo se vincula un documento así a una muestra. Si ya tiene uno, "
            "desvincúlalo primero.",
        )
    if doc_type not in LINKABLE_DOC_TYPES:
        raise DocumentLinkError(
            "unsupported_doc_type",
            "A una muestra se le vincula un albarán, una proforma o una factura.",
        )
    holder = document_holder(session, doc_type, serie, codigo, exclude_order_id=order.id)
    if holder is not None:
        raise DocumentLinkError(
            "document_linked_elsewhere",
            f"La {DOC_LABEL[doc_type]} {numero} ya está vinculada al pedido "
            f"{holder.order_number}. Desvincúlala allí primero.",
            order_id=holder.id, order_number=holder.order_number,
        )
    company_id = company_id or preview.get("company_id")
    if not company_id:
        raise DocumentLinkError(
            "factusol_customer_unlinked",
            f"El cliente FACTUSOL {preview.get('cliente_codigo') or '—'} "
            f"({preview.get('cliente_nombre') or '—'}) no tiene empresa en el CRM: "
            "vincúlalo a una empresa existente o crea la empresa con sus datos.",
            codcli=preview.get("cliente_codigo"), cliente_nombre=preview.get("cliente_nombre"),
        )

    packing = packing_of(order)
    if SAMPLE_SNAPSHOT_KEY not in packing:
        packing[SAMPLE_SNAPSHOT_KEY] = _sample_snapshot(order)
    packing["factusol_source"] = factusol_source_block(
        doc_type=doc_type, serie=serie, codigo=codigo, referencia=preview.get("referencia"),
        forma_pago=preview.get("forma_pago"), forma_pago_nombre=forma_pago_nombre,
        cliente_codigo=preview.get("cliente_codigo"), total=preview.get("total"),
    )
    if not packing.get("shipping_contact"):
        packing = packing_con_destino(packing, preview.get("entrega"))
    if doc_type == "albaranes":
        packing[ALBARAN_KEY] = {
            "numero": numero, "how": "vinculado a la muestra",
            "at": datetime.now(UTC).isoformat(),
        }
    save_packing(order, packing)

    _replace_lines(session, order)
    total_tax = add_document_lines(session, order, document_lines_or_total(preview))
    total = preview.get("total")
    order.total_amount = round(
        float(total) if total is not None and float(total) > 0 else total_tax, 2,
    )
    order.external_source = SOURCE_BY_DOC_TYPE[doc_type]
    order.external_id = preview["external_id"]
    order.order_kind = ORDER_KIND_SAMPLE_CONVERTED
    order.company_id = company_id
    order.factusol_manual_serie = serie
    if doc_type == "albaranes":
        order.factusol_albaran_number = numero
    elif doc_type == "facturas":
        order.factusol_invoice_number = str(codigo)
        order.factusol_invoice_serie = serie
        order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
        _clear_cobro(order)
    session.flush()
    session.expire(order, ["lines"])

    label = DOC_LABEL[doc_type]
    cliente = preview.get("cliente_nombre") or preview.get("cliente_codigo") or "—"
    reason = (
        f"Muestra vinculada a {'la' if doc_type != 'albaranes' else 'el'} {label} "
        f"FACTUSOL {numero} ({cliente}): cliente, líneas e importes cargados"
    )
    actor_id = getattr(actor, "id", None)
    meta = {"event": "sample_document_linked", "doc_type": doc_type, "serie": serie,
            "codigo": codigo, "numero": numero, "company_id": company_id,
            "total": order.total_amount}
    _history(session, order, reason=reason, actor_user_id=actor_id, metadata=meta)
    record_event(
        session, action=LINKED_EVENT, target_type="order", target_id=order.id,
        actor=actor, message=f"{order.order_number}: {reason}",
        metadata={"order_number": order.order_number, **meta},
    )
    logger.info("erp: muestra %s vinculada a %s %s", order.order_number, label, numero)
    return {"document": _entry(
        KIND_FACTURA if doc_type == "facturas" else
        KIND_ALBARAN if doc_type == "albaranes" else KIND_ORIGEN,
        doc_type, serie, codigo, numero,
    )}


def restore_sample_mode(session: Session, order: Order) -> bool:
    """Una muestra CONVERTIDA que se ha quedado sin documentos vuelve a ser
    muestra: líneas e importe originales (0 €), sin serie ni factura, origen a
    mano. Devuelve si lo hizo. No hace commit."""
    if str(order.order_kind or "") != ORDER_KIND_SAMPLE_CONVERTED or linked_documents(order):
        return False
    packing = packing_of(order)
    snap = packing.get(SAMPLE_SNAPSHOT_KEY)
    snap = snap if isinstance(snap, dict) else {}
    _replace_lines(session, order)
    for i, ln in enumerate(snap.get("lines") or []):
        session.add(OrderLine(
            order_id=order.id, position=int(ln.get("position", i)),
            product_sku=ln.get("product_sku") or "", product_codart=ln.get("product_codart"),
            description=ln.get("description") or "Línea",
            quantity=ln.get("quantity") or 1, unit_price=ln.get("unit_price") or 0,
            tax_rate=ln.get("tax_rate") or 0, line_total=ln.get("line_total") or 0,
            notes=ln.get("notes"), is_shipping=bool(ln.get("is_shipping")),
            line_kind=ln.get("line_kind"),
        ))
    order.total_amount = float(snap.get("total_amount") or 0)
    order.company_id = snap.get("company_id")
    order.contact_id = snap.get("contact_id", order.contact_id)
    order.external_source = OrderSource.MANUAL
    order.external_id = None
    order.factusol_manual_serie = None
    order.invoice_status = InvoiceStatus.NOT_INVOICED.value
    order.order_kind = ORDER_KIND_SAMPLE
    packing = packing_of(order)
    packing.pop("factusol_source", None)
    packing.pop(SAMPLE_SNAPSHOT_KEY, None)
    save_packing(order, packing)
    session.flush()
    session.expire(order, ["lines"])
    return True


def sample_pending_link(order: Order) -> dict[str, Any] | None:
    """Muestra SIN convertir que ya apunta a un documento (vinculado antes por
    ERP · Documentos, sin cargar sus datos — caso MUESTRA-000003): la ficha
    ofrece «Reprocesar vínculo» con él."""
    if not is_sample_order(order):
        return None
    for entry in linked_documents(order):
        if entry["serie"] is not None and entry["codigo"] is not None:
            return entry
    return None


__all__ = [
    "CANCEL_UNLINKED_KEY",
    "KINDS",
    "KIND_ALBARAN",
    "KIND_FACTURA",
    "KIND_ORIGEN",
    "LINKABLE_DOC_TYPES",
    "DocumentLinkError",
    "cancel_history_reason",
    "cancel_warnings",
    "doc_label",
    "document_holder",
    "link_document_to_sample",
    "linked_documents",
    "restore_after_uncancel",
    "restore_sample_mode",
    "sample_pending_link",
    "unlink_document",
    "unlink_for_cancel",
]
