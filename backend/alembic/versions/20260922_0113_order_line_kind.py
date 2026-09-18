"""ERP · tipo de línea del pedido — `order_lines.line_kind`.

Un pedido web (WooCommerce) tiene, además de la mercancía, ENVÍO y a veces
COMISIONES (fees, p. ej. «PayPal cost 4%»). Hasta ahora el mapper solo
importaba las líneas de producto, así que el «Resumen económico» de la ficha
inventaba los portes como resto (`total − base − IVA`) y no enseñaba ni el
envío ni la comisión.

Esta columna distingue el TIPO de cada línea para que el resumen sume los
componentes reales en vez de derivarlos:

- NULL  → mercancía (el valor implícito de siempre: no hay backfill).
- 'shipping' → envío (portes). Se marca ADEMÁS `is_shipping=1` para que la
  emisión de albarán/factura lo siga llevando a la banda `IPOR1*` de la
  cabecera, igual que hasta ahora.
- 'fee'  → otros cargos / comisiones.

Columna nueva, sin backfill: NULL ya significa «mercancía», que es lo que
tienen todas las líneas existentes.

Revision ID: 20260922_0113
Revises: 20260921_0112
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_0113"
down_revision = "20260921_0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_lines",
        sa.Column("line_kind", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_lines", "line_kind")
