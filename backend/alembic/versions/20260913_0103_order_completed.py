"""ERP · «Marcar completado» (solo BoHub, reversible).

Campos en `orders`: `completed_at` (cuándo) y `completed_by_user_id` (quién).
Es el estado FINAL del pedido — facturado y enviado, aunque el envío se
tramite fuera de BoHub. Manual (Bart manda): no exige transporte «enviado» ni
factura; se puede desmarcar. NUNCA se propaga a WooCommerce.

Revision ID: 20260913_0103
Revises: 20260912_0102
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260913_0103"
down_revision = "20260912_0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("completed_by_user_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_completed_by_user",
        "orders", "users",
        ["completed_by_user_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_orders_completed_by_user", "orders", type_="foreignkey")
    op.drop_column("orders", "completed_by_user_id")
    op.drop_column("orders", "completed_at")
