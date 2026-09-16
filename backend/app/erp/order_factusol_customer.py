"""ERP · Lote 4 — resolver del CODCLI (cliente F_CLI) de un pedido, también los
WEB.

Un pedido conoce a su cliente FACTUSOL por tres vías; se prueban en orden de
preferencia y gana la primera que resuelve:

1. la **empresa** CRM vinculada (`Company.factusol_company_id`) — sin tocar
   FACTUSOL (`factusol_pdf.order_customer_code`);
2. el **CLIFAC** de su factura: la cabecera de F_FAC para la serie del pedido
   (`order_invoice_serie`) + `factusol_invoice_number`;
3. el **CLIALB** de su albarán: la cabecera de F_ALB para
   `factusol_albaran_number` (`serie-código`).

Devuelve `(codcli, source)` con `source ∈ {"company","factura","albaran"}`, o
`(None, None)` si no hay CODCLI resoluble. Solo LECTURA en FACTUSOL.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.erp.factusol_cobro import parse_invoice_number
from app.erp.factusol_pdf import order_customer_code, order_invoice_serie
from app.erp.models import Order
from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.documents import get_header


def _header_codcli(header: dict[str, Any] | None) -> str | None:
    """CLIFAC / CLIALB de una cabecera normalizada (`cliente_codigo`)."""
    if not header:
        return None
    return str(header.get("cliente_codigo") or "").strip() or None


def order_has_resolvable_source(session: Session, order: Order) -> bool:
    """¿Hay ALGO por lo que resolver el cliente (empresa vinculada, factura o
    albarán)? Permite responder `found:false` SIN molestar a FACTUSOL cuando no
    hay nada que consultar."""
    if order_customer_code(session, order):
        return True
    _, inv_cod = parse_invoice_number(order.factusol_invoice_number)
    if order_invoice_serie(order) is not None and inv_cod is not None:
        return True
    alb_serie, alb_cod = parse_invoice_number(order.factusol_albaran_number)
    return alb_serie is not None and alb_cod is not None


def resolve_order_codcli(
    session: Session, order: Order, *,
    client: FactusolClient, ejercicio: str,
) -> tuple[str | None, str | None]:
    """`(codcli, source)` del cliente FACTUSOL del pedido, o `(None, None)`."""
    # 1. empresa CRM vinculada (sin leer FACTUSOL).
    code = order_customer_code(session, order)
    if code:
        return code, "company"
    # 2. CLIFAC de la factura del pedido (serie del pedido + CODFAC).
    serie = order_invoice_serie(order)
    _, codigo = parse_invoice_number(order.factusol_invoice_number)
    if serie is not None and codigo is not None:
        header = get_header(
            client, "facturas", serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        code = _header_codcli(header)
        if code:
            return code, "factura"
    # 3. CLIALB del albarán del pedido.
    alb_serie, alb_codigo = parse_invoice_number(order.factusol_albaran_number)
    if alb_serie is not None and alb_codigo is not None:
        header = get_header(
            client, "albaranes", serie=alb_serie, codigo=alb_codigo,
            ejercicio=ejercicio,
        )
        code = _header_codcli(header)
        if code:
            return code, "albaran"
    return None, None
