"""ERP · FIX bandeja — `orders.total_amount` es el importe FINAL (con IVA).

Los pedidos creados desde un documento de FACTUSOL (Fase 1: proforma / pedido
de cliente) y los manuales guardaban en `total_amount` la SUMA DE LÍNEAS sin
impuestos (la base imponible), así que la columna TOTAL de la bandeja (y la
cabecera de la ficha, la Cola SAT, la limpieza Woo…) enseñaba la base. Los
pedidos web ya traían el total de WooCommerce (con IVA).

Recalcula los pedidos NO web:

1. si el pedido guarda el total del documento de origen
   (`packing_json.factusol_source.total` = TOTPRE / TOTPCL, con IVA), ese es
   el importe final (los exentos / intracomunitarios quedan igual: base =
   total);
2. si no, Σ `line_total × (1 + tax_rate/100)` de sus líneas (los manuales
   llevan el IVA por línea).

Sin líneas ni total de origen no se toca nada. `downgrade` vuelve a la base
(Σ `line_total`).

Revision ID: 20260915_0106
Revises: 20260915_0105
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "20260915_0106"
down_revision = "20260915_0105"
branch_labels = None
depends_on = None

_SELECT_ORDERS = sa.text(
    "SELECT id, packing_json FROM orders WHERE external_source != 'woocommerce'"
)
_SELECT_LINES = sa.text(
    "SELECT line_total, tax_rate FROM order_lines WHERE order_id = :order_id"
)
_UPDATE_TOTAL = sa.text("UPDATE orders SET total_amount = :total WHERE id = :order_id")


def _source_total(packing_json: str | None) -> float | None:
    if not packing_json:
        return None
    try:
        packing = json.loads(packing_json)
    except (TypeError, ValueError):
        return None
    source = packing.get("factusol_source") if isinstance(packing, dict) else None
    if not isinstance(source, dict) or source.get("total") is None:
        return None
    try:
        total = float(source["total"])
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


def upgrade() -> None:
    bind = op.get_bind()
    for row in bind.execute(_SELECT_ORDERS).mappings().all():
        total = _source_total(row["packing_json"])
        if total is None:
            lines = bind.execute(_SELECT_LINES, {"order_id": row["id"]}).all()
            if not lines:
                continue
            total = sum(
                float(line_total or 0) * (1 + float(tax_rate or 0) / 100)
                for line_total, tax_rate in lines
            )
        bind.execute(_UPDATE_TOTAL, {"total": round(total, 2), "order_id": row["id"]})


def downgrade() -> None:
    bind = op.get_bind()
    for row in bind.execute(_SELECT_ORDERS).mappings().all():
        lines = bind.execute(_SELECT_LINES, {"order_id": row["id"]}).all()
        if not lines:
            continue
        base = sum(float(line_total or 0) for line_total, _ in lines)
        bind.execute(_UPDATE_TOTAL, {"total": round(base, 2), "order_id": row["id"]})
