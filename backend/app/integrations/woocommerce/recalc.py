"""Recálculo / diagnóstico de importes de pedidos web tras el fix del IVA
fabricado y del envío/comisiones no importados en `mapper.py`.

Dos usos, ambos apoyados en el MISMO desglose real de Woo:

- Recálculo (pedidos aún NO facturados): `remap_web_order_lines` (en el mapper)
  rehace las líneas desde el payload de Woo. Aquí vive cómo RECUPERAR ese
  payload — el último guardado en `integration_events` o, si no hay, en vivo.
- Diagnóstico (pedidos YA facturados): se comparan los importes que tiene el
  pedido en BoHub con el desglose REAL de Woo; los que no cuadran se listan
  para revisar a mano la factura de FACTUSOL. Aquí NO se re-factura nada.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models.integration_events import IntegrationEvent
from app.integrations.woocommerce.mapper import (
    _fee_line_specs,
    _shipping_line_specs,
    _woo_num,
)
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Tope de webhooks a escanear por tienda al buscar el payload de un pedido
#: (los backfill se localizan por clave directa; esto acota el fallback).
_SCAN_LIMIT = 8000

#: Tolerancia (€) para considerar que dos importes «cuadran».
EPS = 0.01


def _load(payload_json: str | None) -> dict[str, Any] | None:
    if not payload_json:
        return None
    try:
        data = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def store_for_order(session: Session, order: Any) -> IntegrationAccount | None:
    """La tienda (IntegrationAccount) del pedido web, o None."""
    if not order.store_id:
        return None
    return session.get(IntegrationAccount, order.store_id)


def latest_stored_payload(
    session: Session, order: Any, store: IntegrationAccount,
) -> dict[str, Any] | None:
    """Payload de Woo más reciente guardado para este pedido en
    `integration_events`: primero por la clave de backfill (`backfill:{id}`,
    el payload completo), luego escaneando los webhooks recientes de la tienda
    cuyo `payload["id"]` coincide. None si no hay ninguno (retención)."""
    slug = store.account_id
    ext = str(order.external_id or "")
    if not ext:
        return None
    ev = session.scalar(
        select(IntegrationEvent).where(
            IntegrationEvent.system == "woocommerce",
            IntegrationEvent.account_id == slug,
            IntegrationEvent.external_event_id == f"backfill:{ext}",
        ).order_by(IntegrationEvent.created_at.desc())
    )
    if ev is not None:
        data = _load(ev.payload_json)
        if data is not None:
            return data
    rows = session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.system == "woocommerce",
            IntegrationEvent.account_id == slug,
            IntegrationEvent.event_type.like("order.%"),
        ).order_by(IntegrationEvent.created_at.desc()).limit(_SCAN_LIMIT)
    )
    for ev in rows:
        data = _load(ev.payload_json)
        if isinstance(data, dict) and str(data.get("id") or "") == ext:
            return data
    return None


def fetch_live_payload(
    store: IntegrationAccount, order: Any,
) -> dict[str, Any] | None:
    """Pedido en vivo desde la REST API de Woo (fallback si no hay payload
    guardado). None si no hay `external_id`."""
    from app.integrations.woocommerce.client import WooHTTPClient  # noqa: PLC0415

    ext = str(order.external_id or "").strip()
    if not ext:
        return None
    data = WooHTTPClient(store).get_order(int(ext))
    return data if isinstance(data, dict) else None


def payload_for_order(
    session: Session, order: Any, store: IntegrationAccount, *, live: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """`(payload, fuente)` del pedido: el guardado más reciente o, si no hay y
    `live`, el de la REST API. `fuente` ∈ {'stored','live','none'}."""
    data = latest_stored_payload(session, order, store)
    if data is not None:
        data.setdefault("_store_slug", store.account_id)
        return data, "stored"
    if live:
        data = fetch_live_payload(store, order)
        if data is not None:
            data.setdefault("_store_slug", store.account_id)
            return data, "live"
    return None, "none"


def woo_economics(woo: dict[str, Any]) -> dict[str, float]:
    """Desglose REAL del pedido web LEÍDO de la fuente (nunca derivado): base
    imponible (mercancía), envío, comisiones, IVA (impuesto total del pedido) y
    total (lo que pagó el cliente)."""
    line_items = woo.get("line_items") or []
    base = sum(_woo_num(li.get("total")) for li in line_items)
    envio = sum(s["line_total"] for s in _shipping_line_specs(woo))
    cargos = sum(f["line_total"] for f in _fee_line_specs(woo))
    iva = _woo_num(woo.get("total_tax"))
    return {
        "base": round(base, 2), "envio": round(envio, 2),
        "cargos": round(cargos, 2), "iva": round(iva, 2),
        "total": round(_woo_num(woo.get("total")), 2),
    }


def order_economics(order: Any) -> dict[str, float]:
    """Desglose que el pedido tiene AHORA en BoHub (sus líneas), con la misma
    partición que el resumen económico de la ficha."""
    base = envio = cargos = iva = 0.0
    for ln in order.lines:
        total = float(ln.line_total or 0)
        rate = float(ln.tax_rate or 0)
        iva += total * rate / 100
        if ln.line_kind == "fee":
            cargos += total
        elif ln.is_shipping or ln.line_kind == "shipping":
            envio += total
        else:
            base += total
    return {
        "base": round(base, 2), "envio": round(envio, 2),
        "cargos": round(cargos, 2), "iva": round(iva, 2),
        "total": round(float(order.total_amount or 0), 2),
    }


def economics_mismatch(order_eco: dict[str, float], woo_eco: dict[str, float]) -> list[str]:
    """Diferencias (≥ EPS) entre lo que BoHub tiene y lo real de Woo. Lista
    vacía = cuadra. El IVA fabricado y el envío/comisiones no importados salen
    aquí. Solo diagnóstico; no cambia nada."""
    out: list[str] = []
    for key, label in (
        ("iva", "IVA"), ("base", "base"), ("envio", "envío"), ("cargos", "comisiones"),
    ):
        ours = order_eco.get(key, 0.0)
        theirs = woo_eco.get(key, 0.0)
        if abs(ours - theirs) >= EPS:
            out.append(f"{label}: BoHub {ours:.2f} ≠ Woo {theirs:.2f}")
    return out


__all__ = [
    "EPS",
    "economics_mismatch",
    "fetch_live_payload",
    "latest_stored_payload",
    "order_economics",
    "payload_for_order",
    "store_for_order",
    "woo_economics",
]
