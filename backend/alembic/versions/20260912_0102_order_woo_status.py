"""ERP · WooCommerce — guardar el estado crudo del pedido en el origen.

`orders.woo_status` almacena el estado de WooCommerce ("processing",
"completed", "cancelled", "refunded", "failed", "pending", "on-hold",
"trash"…). Es la base para sacar del seguimiento los cancelados/fallidos y los
reembolsos no cumplidos, y para la reconciliación de los ya importados. NULL =
desconocido (pedidos previos a este cambio, o pedidos no-Woo).

Revision ID: 20260912_0102
Revises: 20260911_0101
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260912_0102"
down_revision = "20260911_0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("woo_status", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "woo_status")
