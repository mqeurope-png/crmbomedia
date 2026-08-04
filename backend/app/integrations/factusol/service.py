"""Operaciones FACTUSOL de alto nivel (Fase C).

Orquesta cliente + mapper contra la BD del CRM, de forma idempotente y
atómica:
  - `ensure_customer_in_factusol`: garantiza que una `Company` tiene su
    CODCLI en FACTUSOL (reusa el vinculado, o el que ya exista por CIF, o
    crea uno nuevo). Persiste `Company.factusol_company_id`.
  - `emit_invoice`: emite la factura de un `Order` (cabecera F_FAC + líneas
    F_LFA), marca el pedido `invoiced_by_erp` + guarda el CODFAC y escribe el
    historial de estado.

Toda escritura FACTUSOL se serializa vía la cola `factusol:writes`
(worker-factusol, concurrency=1) para no pisar la numeración CODFAC — ver
`jobs.py`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session, selectinload

from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpSettings,
    InvoiceStatus,
    Order,
    OrderStatusHistory,
    StatusDomain,
)
from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.mapper import (
    company_to_factusol_client,
    order_to_factusol_invoice,
)
from app.models.crm import Company, User

logger = logging.getLogger(__name__)


def ejercicio_for(session: Session) -> str:
    """Ejercicio (año fiscal) activo: preferencia al ajuste editable en
    `ErpSettings`, con fallback a la config."""
    from app.core.config import get_settings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is not None and cfg.factusol_default_ejercicio:
        return cfg.factusol_default_ejercicio
    return get_settings().factusol_default_ejercicio


def next_codfac(client: FactusolClient, ejercicio: str) -> str:
    """Siguiente CODFAC secuencial del ejercicio = max(CODFAC) + 1.

    FACTUSOL numera solo; consultamos F_FAC ordenada DESC y sumamos 1. Se
    llama DENTRO de `emit_invoice`, justo antes de escribir la cabecera.
    La race condition lectura→escritura la evita el worker serializado
    (concurrency=1 en la cola `factusol:writes`)."""
    rows = client.load_table(
        "F_FAC", filtro="1=1 ORDER BY CODFAC DESC LIMIT 1", ejercicio=ejercicio,
    )
    if not rows:
        return "1"
    last = _int_or_none(rows[0].get("CODFAC"))
    return str((last or 0) + 1)

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
    rows = client.load_table("F_CLI", filtro="1=1 ORDER BY CODCLI DESC LIMIT 1000")
    max_n = 0
    for r in rows:
        n = _int_or_none(r.get("CODCLI"))
        if n is not None and n > max_n:
            max_n = n
    return str(max(max_n + 1, CODCLI_BASE))


def ensure_customer_in_factusol(
    session: Session, company_id: str, client: FactusolClient,
) -> tuple[str, str]:
    """Garantiza el CODCLI de la empresa en FACTUSOL. Devuelve
    `(codcli, matched_by)` con `matched_by ∈ {already_linked, existing_cif,
    created_new}`. Idempotente: reusa el vínculo, o el cliente que ya exista
    por CIF, o crea uno nuevo."""
    company = session.get(Company, company_id)
    if company is None:
        raise FactusolError(f"Company {company_id!r} no existe en el CRM")

    if company.factusol_company_id:
        return company.factusol_company_id, "already_linked"

    # ¿ya existe en FACTUSOL por CIF? → vincular sin duplicar.
    if company.tax_id:
        existing = client.load_table(
            "F_CLI", filtro=f"CIFCLI='{company.tax_id}' LIMIT 1",
        )
        if existing:
            codcli = str(existing[0].get("CODCLI") or "").strip()
            if codcli:
                company.factusol_company_id = codcli
                session.commit()
                logger.info("factusol: empresa %s vinculada a CODCLI %s (por CIF)",
                            company_id, codcli)
                return codcli, "existing_cif"

    # crear cliente nuevo.
    codcli = _next_codcli(client)
    client.write_record("F_CLI", company_to_factusol_client(company, codcli))
    company.factusol_company_id = codcli
    session.commit()
    logger.info("factusol: empresa %s creada como CODCLI %s", company_id, codcli)
    return codcli, "created_new"


def emit_invoice(
    session: Session, order_id: str, client: FactusolClient,
    *, actor: User | None = None,
) -> dict:
    """Emite la factura del pedido en FACTUSOL (cabecera F_FAC + líneas F_LFA),
    marca el pedido `invoiced_by_erp`, guarda el CODFAC y escribe el historial.

    Atómico: si falla una línea, borra la factura a medias en FACTUSOL
    (compensación) y hace rollback en la BD — sin cabecera huérfana ni estado
    sucio. El CODFAC lo asigna FACTUSOL vía `next_codfac`."""
    order = session.get(Order, order_id, options=[selectinload(Order.lines)])
    if order is None:
        raise FactusolError(f"Order {order_id!r} no existe")
    if not order.company_id:
        raise FactusolError("El pedido no tiene empresa: no se puede facturar en FACTUSOL")

    ejercicio = ejercicio_for(session)
    codcli, _matched = ensure_customer_in_factusol(session, order.company_id, client)
    cabecera, lineas = order_to_factusol_invoice(order, codcli, ejercicio)

    # Numeración: FACTUSOL numera secuencialmente; tomamos el siguiente y lo
    # inyectamos en cabecera (CODFAC) y en cada línea (CODLFA = FK a F_FAC).
    codfac = next_codfac(client, ejercicio)
    cabecera["CODFAC"] = codfac
    for linea in lineas:
        linea["CODLFA"] = codfac

    prev_status = _status_value(order.invoice_status)
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

    now = datetime.now(UTC)
    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    order.factusol_invoice_number = codfac
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=prev_status, to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        changed_at=now, changed_by_user_id=(actor.id if actor else None),
        reason="Factura emitida en FACTUSOL",
        metadata_json=json.dumps({
            "factusol_codfac": codfac, "factusol_ejercicio": ejercicio,
            "factusol_codcli": codcli,
        }),
    ))
    session.commit()
    return {"codfac": codfac, "ejercicio": ejercicio, "codcli": codcli,
            "lines": len(lineas)}


def _status_value(v: object) -> str:
    return getattr(v, "value", v)  # type: ignore[return-value]
