"""Operaciones FACTUSOL de alto nivel (Fase C PR C-1).

Orquesta cliente + mapper contra la BD del CRM, de forma idempotente y
atómica:
  - `ensure_customer_in_factusol`: garantiza que una `Company` tiene su
    CODCLI en FACTUSOL (reusa el vinculado, o el que ya exista por CIF, o
    crea uno nuevo). Persiste `Company.factusol_company_id`.
  - `emit_invoice`: emite la factura de un `Order` (cabecera F_FAC + líneas
    F_LFA) y marca el pedido `invoiced_by_erp` + guarda el CODFAC.

C-1 NO conecta esto a ninguna UI ni activa `factusol_live`; se ejerce solo
desde tests (mock del cliente) y desde el endpoint admin de smoke-test
(dry-run). La emisión real llega en C-2.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session, selectinload

from app.erp.models import InvoiceStatus, Order
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.mapper import (
    company_to_factusol_client,
    order_to_factusol_invoice,
)
from app.models.crm import Company

logger = logging.getLogger(__name__)

#: Base del rango de CODCLI que genera el ERP para clientes nuevos —
#: deliberadamente alto para no chocar con la numeración manual existente
#: en FACTUSOL. Confirmar el rango libre con Bart antes de emisión real (C-2).
CODCLI_BASE = 60000


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _next_codcli(client: FactusolClient) -> str:
    """Siguiente CODCLI numérico libre = max(existentes) + 1, con un suelo en
    CODCLI_BASE. Ignora códigos no numéricos."""
    rows = client.load_table("F_CLI", campos=["CODCLI"])
    max_n = 0
    for r in rows:
        n = _int_or_none(r.get("CODCLI"))
        if n is not None and n > max_n:
            max_n = n
    return str(max(max_n + 1, CODCLI_BASE))


def ensure_customer_in_factusol(
    session: Session, company_id: str, client: FactusolClient,
) -> str:
    """Devuelve el CODCLI de la empresa en FACTUSOL, creándolo si hace falta.
    Idempotente: si ya está vinculado lo devuelve; si existe por CIF lo
    vincula sin duplicar; si no, crea el cliente y lo vincula."""
    company = session.get(Company, company_id)
    if company is None:
        raise FactusolError(f"Company {company_id!r} no existe en el CRM")

    if company.factusol_company_id:
        return company.factusol_company_id

    # ¿ya existe en FACTUSOL por CIF? → vincular sin duplicar.
    if company.tax_id:
        existing = client.load_table(
            "F_CLI", filtro=f"CIFCLI='{company.tax_id}'", numero_registros=1,
        )
        if existing:
            codcli = str(existing[0].get("CODCLI") or "").strip()
            if codcli:
                company.factusol_company_id = codcli
                session.commit()
                logger.info("factusol: empresa %s vinculada a CODCLI %s (por CIF)",
                            company_id, codcli)
                return codcli

    # crear cliente nuevo.
    codcli = _next_codcli(client)
    client.write_record("F_CLI", company_to_factusol_client(company, codcli))
    company.factusol_company_id = codcli
    session.commit()
    logger.info("factusol: empresa %s creada como CODCLI %s", company_id, codcli)
    return codcli


def emit_invoice(session: Session, order_id: str, client: FactusolClient) -> dict:
    """Emite la factura del pedido en FACTUSOL (cabecera + líneas) y marca el
    pedido como `invoiced_by_erp`. Atómico: si falla una línea, borra la
    factura a medias en FACTUSOL y hace rollback en la BD (sin cabecera
    huérfana ni estado sucio)."""
    order = session.get(Order, order_id, options=[selectinload(Order.lines)])
    if order is None:
        raise FactusolError(f"Order {order_id!r} no existe")
    if not order.company_id:
        raise FactusolError("El pedido no tiene empresa: no se puede facturar en FACTUSOL")

    ejercicio = client.default_ejercicio
    codcli = ensure_customer_in_factusol(session, order.company_id, client)
    cabecera, lineas = order_to_factusol_invoice(order, codcli, ejercicio)
    codfac = cabecera["CODFAC"]

    client.write_record("F_FAC", cabecera, ejercicio=ejercicio)
    try:
        for linea in lineas:
            client.write_record("F_LFA", linea, ejercicio=ejercicio)
    except FactusolError:
        # Compensación: borra líneas ya escritas + cabecera para no dejar una
        # factura a medias en FACTUSOL, y no toca el estado del pedido.
        try:
            client.delete_records("F_LFA", f"CODLFA='{codfac}'", ejercicio=ejercicio)
            client.delete_records("F_FAC", f"CODFAC='{codfac}'", ejercicio=ejercicio)
        except FactusolError:
            logger.warning(
                "factusol: no se pudo limpiar la factura %s a medias", codfac,
                exc_info=True,
            )
        session.rollback()
        raise

    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    order.factusol_invoice_number = codfac
    session.commit()
    return {
        "factusol_invoice_number": codfac,
        "codcli": codcli,
        "lines": len(lineas),
    }
