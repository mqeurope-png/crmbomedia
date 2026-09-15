"""Lote ERP — «Anular pedido» (REVERSIBLE, distinto de «quitar/ocultar») y
nombre de envío (dropshipping) del pedido manual.

- `cancelled_at` / `cancelled_by_user_id` / `cancelled_reason`: el pedido
  anulado sale de bandeja, colas SAT y seguimiento (mismo corte que los
  quitados a mano) y queda como estado final con su historial; «Restaurar»
  limpia los tres campos. Nunca se borra el pedido de BoHub.
- `shipping_name`: destinatario del envío cuando NO es la empresa cliente
  (dropshipping); la dirección de envío ya vivía en `packing_json`.

Revision ID: 20260920_0111
Revises: 20260919_0110
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0111"
down_revision = "20260919_0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "cancelled_by_user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.add_column("orders", sa.Column("cancelled_reason", sa.String(length=255), nullable=True))
    op.create_index("ix_orders_cancelled_at", "orders", ["cancelled_at"])
    op.add_column("orders", sa.Column("shipping_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "shipping_name")
    op.drop_index("ix_orders_cancelled_at", table_name="orders")
    op.drop_column("orders", "cancelled_reason")
    op.drop_column("orders", "cancelled_by_user_id")
    op.drop_column("orders", "cancelled_at")
