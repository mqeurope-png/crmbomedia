"""Operaciones FACTUSOL de alto nivel (Fase C · C-2-fix1).

Nuevo modelo (2026-08-04): una app externa ya replica cada pedido de
WooCommerce en FACTUSOL como Pedido de Cliente (F_PCL) con el cliente y todos
los importes ya calculados. BoHub ERP **no crea clientes ni recalcula nada**:
`emit_invoice` localiza el F_PCL del pedido y lo **convierte en factura F_FAC**
copiando los datos (+ CODFAC nuevo + link PEDFAC), y copia sus líneas
F_LPC → F_LFA.

Toda escritura FACTUSOL se serializa vía la cola `factusol:writes`
(worker-factusol, concurrency=1) para no pisar la numeración CODFAC.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

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
    lpc_row_to_lfa_payload,
    pcl_row_to_fac_payload,
)
from app.models.crm import User

logger = logging.getLogger(__name__)

#: Serie de factura de Bomedia (F_FAC.PEDFAC = "<serie>-<codpcl_padded_6>").
DEFAULT_SERIE_FAC = "1"


def ejercicio_for(session: Session) -> str:
    """Ejercicio (año fiscal) activo: preferencia al ajuste editable en
    `ErpSettings`, con fallback a la config."""
    from app.core.config import get_settings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is not None and cfg.factusol_default_ejercicio:
        return cfg.factusol_default_ejercicio
    return get_settings().factusol_default_ejercicio


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def next_codfac(client: FactusolClient, ejercicio: str) -> str:
    """Siguiente CODFAC secuencial del ejercicio = max(CODFAC) + 1.

    FACTUSOL numera solo; consultamos F_FAC ordenada DESC y sumamos 1. Se
    llama DENTRO de `emit_invoice`, justo antes de escribir la cabecera. La
    race lectura→escritura la evita el worker serializado (concurrency=1).
    Nota: la API DELSOL no soporta LIMIT en el filtro, así que se pide todo
    ordenado y se toma la primera fila."""
    rows = client.load_table(
        "F_FAC", filtro="1=1 ORDER BY CODFAC DESC", ejercicio=ejercicio,
    )
    if not rows:
        return "1"
    last = _int_or_none(rows[0].get("CODFAC"))
    return str((last or 0) + 1)


def _compose_refpcl(order_number: str, ref_prefix: str | None) -> str:
    """`BOPRIN-99866` (+ prefijo opcional) → `BOP-099866`. El número Woo va con
    padding a 6 dígitos; el prefijo, si no se pasa, se deriva de las 3 primeras
    letras del segmento inicial del order_number."""
    parts = (order_number or "").split("-")
    number = parts[-1] if parts else ""
    prefix = (ref_prefix or (parts[0][:3] if parts and parts[0] else "")).upper()
    n = _int_or_none(number)
    num_str = f"{n:06d}" if n is not None else number
    return f"{prefix}-{num_str}"


def _store_ref_prefix(session: Session, order: Order) -> str | None:
    """Prefijo REFPCL configurado en la tienda (IntegrationAccount.
    metadata_json['factusol_ref_prefix']); None si no está configurado."""
    if not order.store_id:
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    if store is None or not store.metadata_json:
        return None
    try:
        meta = json.loads(store.metadata_json)
    except (TypeError, ValueError):
        return None
    prefix = meta.get("factusol_ref_prefix") if isinstance(meta, dict) else None
    return str(prefix).upper() if prefix else None


def find_pcl_by_order(
    client: FactusolClient, order: Order, ejercicio: str,
    *, ref_prefix: str | None = None,
) -> dict[str, Any] | None:
    """Localiza en F_PCL el pedido de cliente correspondiente al `order` del
    CRM (por REFPCL = prefijo-tienda + nº Woo). None si aún no existe."""
    ref = _compose_refpcl(order.order_number, ref_prefix)
    rows = client.load_table("F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio)
    return rows[0] if rows else None


def emit_invoice(
    session: Session, order_id: str, client: FactusolClient,
    *, actor: User | None = None,
) -> dict[str, Any]:
    """Convierte el F_PCL del pedido en factura F_FAC (cabecera + líneas),
    marca el pedido `invoiced_by_erp`, guarda el CODFAC y escribe el historial.

    NO crea cliente ni recalcula importes: copia de F_PCL/F_LPC. Atómico: si
    falla una línea, borra la factura a medias en FACTUSOL (compensación) y
    hace rollback en la BD.
    """
    order = session.get(Order, order_id, options=[selectinload(Order.lines)])
    if order is None:
        raise FactusolError(f"Order {order_id!r} no existe")
    inv = _status_value(order.invoice_status)
    if order.factusol_invoice_number or inv == InvoiceStatus.INVOICED_BY_ERP.value:
        raise FactusolError("El pedido ya tiene factura en FACTUSOL")
    if inv == InvoiceStatus.ALREADY_INVOICED_EXTERNALLY.value:
        raise FactusolError("El pedido está marcado como facturado fuera del ERP")

    ejercicio = ejercicio_for(session)
    ref_prefix = _store_ref_prefix(session, order)
    pcl = find_pcl_by_order(client, order, ejercicio, ref_prefix=ref_prefix)
    if pcl is None:
        raise FactusolError(
            f"Este pedido ({order.order_number}) aún no está en FACTUSOL. La app "
            "WooCommerce→FACTUSOL debe importarlo antes de facturar."
        )
    codpcl = pcl.get("CODPCL")
    lpc_rows = client.load_table(
        "F_LPC", filtro=f"CODLPC={codpcl}", ejercicio=ejercicio,
    )

    codfac = next_codfac(client, ejercicio)
    pedfac_ref = f"{DEFAULT_SERIE_FAC}-{_pad6(codpcl)}"
    fecha_emision = datetime.now(UTC).date().isoformat()
    cabecera = pcl_row_to_fac_payload(
        pcl, codfac, pedfac_ref, ejercicio, fecha_emision=fecha_emision,
    )
    lineas = [
        lpc_row_to_lfa_payload(row, codfac, i + 1, ejercicio)
        for i, row in enumerate(lpc_rows)
    ]

    client.write_record("F_FAC", cabecera, ejercicio=ejercicio)
    try:
        for linea in lineas:
            client.write_record("F_LFA", linea, ejercicio=ejercicio)
    except FactusolError:
        # Compensación: borra líneas + cabecera para no dejar factura a medias.
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
        from_status=inv, to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        changed_at=now, changed_by_user_id=(actor.id if actor else None),
        reason="Factura emitida en FACTUSOL",
        metadata_json=json.dumps({
            "factusol_codfac": codfac, "factusol_codpcl": str(codpcl),
            "factusol_pedfac": pedfac_ref, "factusol_ejercicio": ejercicio,
        }),
    ))
    _log_sync(session, order, codfac, str(codpcl), ejercicio, len(lineas))
    session.commit()
    return {"codfac": codfac, "codpcl": str(codpcl), "ejercicio": ejercicio,
            "lines": len(lineas)}


def _pad6(codpcl: object) -> str:
    n = _int_or_none(codpcl)
    return f"{n:06d}" if n is not None else str(codpcl)


def _log_sync(
    session: Session, order: Order, codfac: str, codpcl: str,
    ejercicio: str, lines: int,
) -> None:
    from app.models.crm import (  # noqa: PLC0415
        ExternalSystem,
        SyncLog,
        SyncStatus,
        SyncTrigger,
    )

    now = datetime.now(UTC)
    session.add(SyncLog(
        system=ExternalSystem.FACTUSOL,
        account_id=order.store_id,
        operation="factusol_emit_invoice",
        status=SyncStatus.SUCCESS.value,
        started_at=now, finished_at=now,
        records_processed=1,
        triggered_by=SyncTrigger.MANUAL.value,
        message=f"F_PCL {codpcl} → F_FAC {codfac} (ej. {ejercicio}, {lines} líneas)",
    ))


def _status_value(v: object) -> str:
    return getattr(v, "value", v)  # type: ignore[return-value]
