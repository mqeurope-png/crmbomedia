"""Diagnóstico SOLO LECTURA del vínculo factura FACTUSOL → pedido del CRM que
usa «Enviar factura al cliente» (destinatario, tienda, idioma, timeline).

Incidencia (#426): la factura 5-260090 (fluxlasers, ref FLE-005784, Escola La
Muntanyeta) proponía como «Para» `info@neonled.es` (otro cliente) y decía
«remitente de la tienda boprint». Causa: el pedido guarda el CODFAC DESNUDO
(`factusol_invoice_number = "260090"`) y el mismo número existe en varias
series (TIPFAC 1/2/5); la búsqueda era solo por número y se quedaba con el
PRIMER pedido homónimo. Este diagnóstico enseña, para una factura:

- la cabecera de F_FAC (serie, número, CLIFAC + nombre, REFFAC);
- la empresa del CRM enlazada a ese CLIFAC;
- TODOS los pedidos con ese número de factura (tienda, serie guardada,
  empresa + su CODCLI, email del contacto, referencia común) y el veredicto
  de cada uno (serie coincide / referencia coincide / cliente coincide /
  descartado);
- qué pedido devolvía la búsqueda ANTIGUA (solo por número) y cuál devuelve
  la NUEVA (serie + referencia + cliente, sin adivinar), con el destinatario
  que saldría en la previsualización.

NO escribe nada: ni en FACTUSOL ni en la BD del CRM.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.factusol_pdf import (
    extract_document_data,
    find_order_for_invoice,
    load_raw_document,
    order_composed_ref,
    order_customer_code,
    order_invoice_serie,
)
from app.erp.models import Order
from app.models.crm import Company, Contact
from app.models.integration_settings import IntegrationAccount


@dataclass
class Candidate:
    order_id: str
    order_number: str
    tienda: str | None
    factura_guardada: str | None
    serie_guardada: int | None
    empresa: str | None
    empresa_codcli: str | None
    contacto_email: str | None
    referencia: str
    veredicto: str


def _verdict(
    order: Order, *, serie: int, ref: str, cli: str, o_serie: int | None,
    o_ref: str, o_cli: str | None, total: int,
) -> str:
    _ = order
    if o_serie is not None:
        return "serie coincide" if o_serie == serie else f"DESCARTADO: serie {o_serie} ≠ {serie}"
    if ref and o_ref == ref:
        return "referencia coincide (serie no guardada)"
    if cli and o_cli == cli:
        return "cliente coincide (serie y referencia no)"
    if (ref and o_ref and o_ref != ref) or (cli and o_cli and o_cli != cli):
        return "DESCARTADO: la referencia o el cliente contradicen"
    if total == 1:
        return "único pedido con ese nº y nada lo contradice (sin serie guardada)"
    return "DESCARTADO: homónimo sin serie, referencia ni cliente que lo confirmen"


def diagnose_invoice_link(
    session: Session, client: Any, *, serie: int, codigo: int, ejercicio: str,
) -> dict[str, Any]:
    """Todo lo que hay que saber del vínculo de la factura (serie, código)."""
    raw = load_raw_document(
        client, "facturas", serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if raw is None:
        return {"error": f"No existe la factura {serie}-{codigo} en el ejercicio {ejercicio}"}
    data = extract_document_data(client, "facturas", raw[0], raw[1], ejercicio=ejercicio)
    cliente = data.get("cliente") or {}
    cli = str(cliente.get("codigo") or "").strip()
    ref = str(data.get("referencia") or "").strip().upper()

    company = session.scalar(
        select(Company).where(Company.factusol_company_id == cli)
    ) if cli else None

    numbers = [str(codigo), f"{serie}-{codigo}", f"{serie}-{codigo:06d}"]
    candidates = session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(numbers))
        .order_by(Order.created_at)
    ).all()
    out: list[Candidate] = []
    for order in candidates:
        store = session.get(IntegrationAccount, order.store_id) if order.store_id else None
        o_company = session.get(Company, order.company_id) if order.company_id else None
        contact = session.get(Contact, order.contact_id) if order.contact_id else None
        o_serie = order_invoice_serie(order)
        o_ref = order_composed_ref(session, order)
        o_cli = order_customer_code(session, order)
        out.append(Candidate(
            order_id=order.id, order_number=order.order_number,
            tienda=store.account_id if store is not None else None,
            factura_guardada=order.factusol_invoice_number,
            serie_guardada=o_serie,
            empresa=o_company.name if o_company is not None else None,
            empresa_codcli=o_cli,
            contacto_email=contact.email if contact is not None else None,
            referencia=o_ref,
            veredicto=_verdict(order, serie=serie, ref=ref, cli=cli, o_serie=o_serie,
                               o_ref=o_ref, o_cli=o_cli, total=len(candidates)),
        ))

    # Búsqueda ANTIGUA (#426 y anteriores): primer pedido con ese número desnudo.
    antiguo = session.scalar(
        select(Order).where(Order.factusol_invoice_number == str(codigo))
        .order_by(Order.created_at)
    )
    nuevo = find_order_for_invoice(
        session, serie=serie, codigo=codigo, referencia=ref, cliente_codigo=cli,
    )
    destinatario = None
    if nuevo is not None and nuevo.contact_id:
        contact = session.get(Contact, nuevo.contact_id)
        destinatario = contact.email if contact is not None else None
    return {
        "factura": {
            "numero": data.get("numero"), "serie": serie, "codigo": codigo,
            "cliente_codigo": cli, "cliente_nombre": cliente.get("nombre"),
            "referencia": ref,
        },
        "empresa_crm": (
            {"id": company.id, "nombre": company.name} if company is not None else None
        ),
        "candidatos": [asdict(c) for c in out],
        "antiguo": antiguo.order_number if antiguo is not None else None,
        "nuevo": nuevo.order_number if nuevo is not None else None,
        "destinatario": destinatario,
    }


def format_report(diag: dict[str, Any]) -> str:
    if diag.get("error"):
        return str(diag["error"])
    f = diag["factura"]
    lines = [
        f"Factura {f['numero']}  cliente CLIFAC={f['cliente_codigo']} «{f['cliente_nombre']}»"
        f"  REFFAC={f['referencia'] or '—'}",
        "Empresa CRM por CLIFAC: "
        + (f"{diag['empresa_crm']['nombre']} ({diag['empresa_crm']['id']})"
           if diag.get("empresa_crm") else "— (sin enlazar)"),
        f"Pedidos con ese nº de factura: {len(diag['candidatos'])}",
    ]
    for c in diag["candidatos"]:
        serie = c["serie_guardada"] if c["serie_guardada"] is not None else "?"
        lines.append(
            f"  - {c['order_number']:<14} tienda={c['tienda'] or '—':<10} "
            f"factura={c['factura_guardada']} serie={serie} "
            f"ref={c['referencia'] or '—':<12} empresa={c['empresa'] or '—'} "
            f"(CODCLI {c['empresa_codcli'] or '—'}) contacto={c['contacto_email'] or '—'}"
        )
        lines.append(f"      → {c['veredicto']}")
    lines.append(f"Búsqueda ANTIGUA (solo nº): {diag['antiguo'] or '— (ninguno)'}")
    lines.append(
        "Búsqueda NUEVA (serie+ref+cliente): "
        f"{diag['nuevo'] or '— (ninguno: no se adivina)'}"
    )
    lines.append(
        f"Destinatario propuesto: {diag['destinatario'] or '— (el operador lo escribe)'}"
    )
    return "\n".join(lines)
