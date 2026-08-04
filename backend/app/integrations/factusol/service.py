"""Operaciones FACTUSOL de alto nivel (Fase C · C-2-fix2).

Modelo (2026-08-04): una app externa ya replica cada pedido de WooCommerce en
FACTUSOL como Pedido de Cliente (F_PCL) con el cliente y todos los importes ya
calculados, y **a veces la factura ya la crea Bart a mano** en el escritorio
FACTUSOL. BoHub ERP **no crea clientes ni recalcula nada**:

- `check_factusol_status`: mira si el pedido ya tiene **factura** (F_FAC) o
  **albarán** (F_ALB) en FACTUSOL, por la referencia común REFFAC/REFALB.
- `get_and_link_factusol_status`: si ya hay factura, la **auto-vincula** al
  pedido (sin volver a crearla) y lo marca `invoiced_by_erp`.
- `emit_invoice`: solo si NO existe factura, localiza el F_PCL y lo **convierte
  en factura F_FAC** copiando los datos (+ CODFAC nuevo), y copia sus líneas
  F_LPC → F_LFA. Vuelve a comprobar la existencia de factura JUSTO antes de
  escribir (protección anti-duplicado ante carreras / creación manual).

Toda escritura FACTUSOL se serializa vía la cola `factusol:writes`
(worker-factusol, concurrency=1) para no pisar la numeración CODFAC.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
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
    FacturaOptions,
    lpc_row_to_lfa_payload,
    pcl_row_to_fac_payload,
)
from app.models.crm import User

logger = logging.getLogger(__name__)


def ejercicio_for(session: Session) -> str:
    """Ejercicio (año fiscal) activo: preferencia al ajuste editable en
    `ErpSettings`, con fallback a la config."""
    from app.core.config import get_settings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is not None and cfg.factusol_default_ejercicio:
        return cfg.factusol_default_ejercicio
    return get_settings().factusol_default_ejercicio


#: Serie de facturación de último recurso si no hay nada configurado (C-2).
FALLBACK_SERFAC = "A"


def series_config(session: Session) -> dict[str, Any]:
    """`{"default": "A", "by_source": {...}}` desde `ErpSettings`. Dict vacío
    si aún no se ha configurado."""
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None or not cfg.factusol_series_json:
        return {}
    try:
        data = json.loads(cfg.factusol_series_json)
    except (TypeError, ValueError):
        logger.warning("factusol: factusol_series_json ilegible; se ignora")
        return {}
    return data if isinstance(data, dict) else {}


def resolve_serfac(session: Session, order: Order) -> str:
    """Serie de la factura para este pedido (C-2):
    `by_source[origen]` → `default` → `"A"`.

    El origen es `order.external_source` (`woocommerce`, `manual`, …). Si la
    tienda concreta necesita serie propia, la clave puede ser el `store_id`,
    que tiene prioridad sobre el origen genérico."""
    conf = series_config(session)
    by_source = conf.get("by_source")
    by_source = by_source if isinstance(by_source, dict) else {}
    source = _status_value(order.external_source)
    for key in (order.store_id, source):
        if key and str(by_source.get(key, "")).strip():
            return str(by_source[key]).strip()
    default = str(conf.get("default") or "").strip()
    if default:
        return default
    logger.warning(
        "factusol: sin serie configurada para origen %r; se usa %r",
        source, FALLBACK_SERFAC,
    )
    return FALLBACK_SERFAC


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


def _compose_ref(order_number: str, ref_prefix: str | None) -> str:
    """`BOPRIN-99866` (+ prefijo opcional) → `BOP-099866`. Es la referencia
    COMÚN que comparten pedido (REFPCL), albarán (REFALB) y factura (REFFAC):
    el número Woo con padding a 6 dígitos precedido del prefijo de la tienda.
    Si no se pasa prefijo, se deriva de las 3 primeras letras del segmento
    inicial del order_number."""
    parts = (order_number or "").split("-")
    number = parts[-1] if parts else ""
    prefix = (ref_prefix or (parts[0][:3] if parts and parts[0] else "")).upper()
    n = _int_or_none(number)
    num_str = f"{n:06d}" if n is not None else number
    return f"{prefix}-{num_str}"


def _store_ref_prefix(session: Session, order: Order) -> str | None:
    """Prefijo de referencia configurado en la tienda (IntegrationAccount.
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
    ref = _compose_ref(order.order_number, ref_prefix)
    rows = client.load_table("F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio)
    return rows[0] if rows else None


def check_factusol_status(
    client: FactusolClient, order: Order, ejercicio: str,
    *, ref_prefix: str | None = None,
) -> dict[str, Any]:
    """¿El pedido ya tiene factura y/o albarán en FACTUSOL?

    Consulta F_FAC (por REFFAC) y F_ALB (por REFALB) usando la referencia
    común del pedido. Devuelve un dict con las filas encontradas (o None) sin
    escribir nada — la decisión de auto-vincular la toma la capa superior.
    """
    ref = _compose_ref(order.order_number, ref_prefix)
    fac_rows = client.load_table("F_FAC", filtro=f"REFFAC='{ref}'", ejercicio=ejercicio)
    alb_rows = client.load_table("F_ALB", filtro=f"REFALB='{ref}'", ejercicio=ejercicio)
    factura = fac_rows[0] if fac_rows else None
    albaran = alb_rows[0] if alb_rows else None
    return {
        "ref": ref,
        "has_factura": factura is not None,
        "factura": factura,
        "has_albaran": albaran is not None,
        "albaran": albaran,
    }


def get_and_link_factusol_status(
    session: Session, order: Order, client: FactusolClient, ejercicio: str,
    *, ref_prefix: str | None = None, actor: User | None = None,
) -> dict[str, Any]:
    """Consulta FACTUSOL y, si el pedido YA tiene factura, la auto-vincula
    (persiste) para no ofrecer «Emitir» sobre algo ya facturado. Devuelve el
    estado para el frontend:

    - `{"status": "invoiced", "codfac", "ref", "auto_linked": bool}`
    - `{"status": "albaran", "ref", "albaran_codigo"}`  (albarán sin factura)
    - `{"status": "pending", "ref"}`                     (ni factura ni albarán)
    """
    info = check_factusol_status(client, order, ejercicio, ref_prefix=ref_prefix)
    if info["has_factura"]:
        codfac = str(info["factura"].get("CODFAC"))
        auto_linked = False
        if not order.factusol_invoice_number:
            _auto_link_factura(
                session, order, codfac, ejercicio, ref=info["ref"], actor=actor,
            )
            session.commit()
            auto_linked = True
        return {"status": "invoiced", "codfac": codfac, "ref": info["ref"],
                "auto_linked": auto_linked}
    if info["has_albaran"]:
        alb = info["albaran"] or {}
        return {"status": "albaran", "ref": info["ref"],
                "albaran_codigo": (str(alb.get("CODALB"))
                                   if alb.get("CODALB") is not None else None)}
    return {"status": "pending", "ref": info["ref"]}


def emit_invoice(
    session: Session, order_id: str, client: FactusolClient,
    *, actor: User | None = None, options: FacturaOptions | None = None,
) -> dict[str, Any]:
    """Convierte el F_PCL del pedido en factura F_FAC (cabecera + líneas),
    marca el pedido `invoiced_by_erp`, guarda el CODFAC y escribe el historial.

    Anti-duplicado: JUSTO antes de escribir vuelve a consultar F_FAC por REFFAC;
    si la factura ya existe (creada a mano por Bart o por una carrera), la
    **auto-vincula** en vez de crear un duplicado.

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

    # Anti-duplicado: ¿existe ya la factura en FACTUSOL? (creada a mano o carrera)
    existing = check_factusol_status(client, order, ejercicio, ref_prefix=ref_prefix)
    if existing["has_factura"]:
        codfac = str(existing["factura"].get("CODFAC"))
        _auto_link_factura(
            session, order, codfac, ejercicio, ref=existing["ref"], actor=actor,
        )
        session.commit()
        logger.info("factusol: factura ya existía, auto-vinculada order=%s codfac=%s",
                    order_id, codfac)
        return {"codfac": codfac, "ejercicio": ejercicio, "lines": 0,
                "already_existed": True}

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
    fecha_emision = datetime.now(UTC).date().isoformat()
    # C-2: si el operador no fijó serie en el modal, se aplica la configurada
    # en /erp/settings (override por origen → default → "A").
    if options is None or options.serfac is None:
        serie = resolve_serfac(session, order)
        options = (
            replace(options, serfac=serie) if options is not None
            else FacturaOptions(serfac=serie)
        )
    cabecera = pcl_row_to_fac_payload(
        pcl, codfac, ejercicio, fecha_emision=fecha_emision, options=options,
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
            "factusol_ref": cabecera.get("REFFAC"), "factusol_ejercicio": ejercicio,
        }),
    ))
    _log_sync(session, order, codfac, str(codpcl), ejercicio, len(lineas))
    session.commit()
    return {"codfac": codfac, "codpcl": str(codpcl), "ejercicio": ejercicio,
            "lines": len(lineas)}


def _auto_link_factura(
    session: Session, order: Order, codfac: str, ejercicio: str,
    *, ref: str | None, actor: User | None = None,
) -> None:
    """Marca el pedido como facturado apuntando a un CODFAC que YA existe en
    FACTUSOL (no escribe nada en FACTUSOL). Escribe historial + SyncLog."""
    inv = _status_value(order.invoice_status)
    now = datetime.now(UTC)
    order.invoice_status = InvoiceStatus.INVOICED_BY_ERP.value
    order.factusol_invoice_number = str(codfac)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.INVOICE,
        from_status=inv, to_status=InvoiceStatus.INVOICED_BY_ERP.value,
        changed_at=now, changed_by_user_id=(actor.id if actor else None),
        reason="Factura localizada en FACTUSOL (vinculada automáticamente)",
        metadata_json=json.dumps({
            "factusol_codfac": str(codfac), "factusol_ref": ref,
            "factusol_ejercicio": ejercicio, "source": "auto_linked_from_factusol",
        }),
    ))
    _log_sync(session, order, str(codfac), "-", ejercicio, 0,
              operation="factusol_link_invoice",
              message=f"Factura {codfac} ya existente en FACTUSOL → vinculada "
                      f"(ref {ref}, ej. {ejercicio})")


def _log_sync(
    session: Session, order: Order, codfac: str, codpcl: str,
    ejercicio: str, lines: int, *,
    operation: str = "factusol_emit_invoice", message: str | None = None,
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
        operation=operation,
        status=SyncStatus.SUCCESS.value,
        started_at=now, finished_at=now,
        records_processed=1,
        triggered_by=SyncTrigger.MANUAL.value,
        message=message or (
            f"F_PCL {codpcl} → F_FAC {codfac} (ej. {ejercicio}, {lines} líneas)"
        ),
    ))


def _status_value(v: object) -> str:
    return getattr(v, "value", v)  # type: ignore[return-value]
