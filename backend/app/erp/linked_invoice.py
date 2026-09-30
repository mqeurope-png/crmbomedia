"""Factura de FACTUSOL vinculada a un pedido: UNA sola definición para todo BoHub.

Un pedido «tiene factura» cuando las tres cosas constan en el propio pedido:

- `invoice_status` de facturado (emitida desde BoHub, vinculada, generada o
  marcada como facturada fuera);
- `factusol_invoice_number` (CODFAC);
- `factusol_invoice_serie` (TIPFAC) — o, en datos antiguos, la serie dentro
  del propio número (`2-526107`).

No depende del origen del pedido (`external_source`: web, manual, desde
factura / albarán / proforma de FACTUSOL…) ni de `factusol_manual_serie` (la
serie del documento MANUAL de origen, que no tiene nada que ver con la de la
factura). Tampoco busca la factura en FACTUSOL por la referencia del pedido
(REFFAC), que solo llevan los pedidos web.

Sin serie NO se localiza la factura: el número solo es único por serie (las
series 1 y 5 comparten la numeración 260xxx), así que nunca se busca por el
número solo — se avisa de que falta la serie.

Lo usan la ficha (banner, línea de vida, «PDF de la factura», «Enviar factura
al cliente»), «Registrar cobro», ERP · Seguimiento y la hoja «Seguimiento
(app)». Solo lee el pedido: no escribe nada ni consulta FACTUSOL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.erp.models import InvoiceStatus

#: Estados de factura que cuentan como «facturado».
INVOICED_STATUSES: frozenset[str] = frozenset({
    InvoiceStatus.GENERATED.value,
    InvoiceStatus.INVOICED_BY_ERP.value,
    InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value,
    InvoiceStatus.CREDIT_NOTE.value,
})

#: Motivos por los que un pedido no tiene una factura utilizable.
SIN_FACTURA = "sin_factura"
SIN_SERIE = "sin_serie"


@dataclass(frozen=True)
class LinkedInvoice:
    """Clave compuesta de la factura del pedido en FACTUSOL."""

    serie: int
    codigo: int

    @property
    def numero(self) -> str:
        """`serie-número` como lo muestra FACTUSOL (`2-526107`)."""
        return f"{self.serie}-{self.codigo:06d}"

    def as_dict(self) -> dict[str, Any]:
        return {"serie": self.serie, "codigo": self.codigo, "numero": self.numero}


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _serie(value: Any) -> int | None:
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    return coerce_serie(value)


def _status(order: Any) -> str:
    value = getattr(order, "invoice_status", None)
    return str(getattr(value, "value", value) or "")


def invoice_parts(order: Any) -> tuple[int | None, int | None]:
    """`(serie, código)` tal como constan en el pedido. El número puede venir
    desnudo (`526107`, lo normal) o como `serie-código`; la serie guardada
    manda sobre la del número."""
    text = str(getattr(order, "factusol_invoice_number", None) or "").strip()
    serie_num: int | None = None
    if "-" in text:
        head, _, tail = text.partition("-")
        serie_num, codigo = _serie(head), _int(tail)
    else:
        codigo = _int(text) if text else None
    stored = _serie(getattr(order, "factusol_invoice_serie", None))
    return (stored if stored is not None else serie_num), codigo


def is_invoiced_status(order: Any) -> bool:
    return _status(order) in INVOICED_STATUSES


def invoice_link_problem(order: Any) -> str | None:
    """None si el pedido tiene factura utilizable; si no, el motivo:
    `sin_factura` (no facturado o sin número) o `sin_serie` (hay número pero
    no la serie: no se puede localizar sin adivinar)."""
    serie, codigo = invoice_parts(order)
    if not is_invoiced_status(order) or codigo is None:
        return SIN_FACTURA
    if serie is None:
        return SIN_SERIE
    return None


def get_linked_invoice(order: Any) -> LinkedInvoice | None:
    """La factura vinculada al pedido, o None si no la tiene (o le falta la
    serie). Ver `invoice_link_problem` para distinguir los dos casos."""
    if invoice_link_problem(order) is not None:
        return None
    serie, codigo = invoice_parts(order)
    assert serie is not None and codigo is not None  # noqa: S101 — garantizado arriba
    return LinkedInvoice(serie=serie, codigo=codigo)


def invoice_label(order: Any) -> str:
    """Texto de la factura para mostrar: `2-526107`; con número pero sin
    serie, el número tal cual (`260721`); sin factura, vacío."""
    linked = get_linked_invoice(order)
    if linked is not None:
        return linked.numero
    if invoice_link_problem(order) == SIN_SERIE:
        return str(invoice_parts(order)[1])
    return ""


def missing_invoice_detail(order: Any) -> str:
    """Mensaje claro de por qué no se puede usar la factura del pedido."""
    if invoice_link_problem(order) == SIN_SERIE:
        codigo = invoice_parts(order)[1]
        return (
            f"La factura {codigo} de este pedido no tiene la serie guardada en BoHub. "
            "Sin la serie no se puede localizar (el mismo número existe en varias "
            "series de FACTUSOL): falta la serie."
        )
    return "Este pedido no tiene factura vinculada en FACTUSOL."


def linked_invoice_payload(order: Any) -> dict[str, Any]:
    """Para la API: `factusol_invoice` ({serie, codigo, numero} o None) y
    `factusol_invoice_problem` (None, `sin_factura` o `sin_serie`)."""
    linked = get_linked_invoice(order)
    return {
        "factusol_invoice": linked.as_dict() if linked is not None else None,
        "factusol_invoice_problem": invoice_link_problem(order),
    }
