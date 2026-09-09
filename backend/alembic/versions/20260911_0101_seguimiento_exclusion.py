"""ERP-F6-fix7 — excluir pedidos del seguimiento.

Campos de EXCLUSIÓN en `orders` (decisión de Bart): un pedido excluido no se
lista, no se inserta en la hoja de Drive, no se actualiza ni se cuenta. Es
reversible y no toca el pedido en BoHub/FACTUSOL. Se registra quién, cuándo y un
motivo opcional.

«Escrito en Drive» NO necesita columna nueva: ya vive en
`erp_drive_sync_rows.synced_at` (migración 0100). La separación entre «pendiente
de escribir» y «excluido» es, por tanto, exclusión (nueva) vs. presencia de
fila sincronizada (existente).

Revision ID: 20260911_0101
Revises: 20260910_0100
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_0101"
down_revision = "20260910_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("seguimiento_excluded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("seguimiento_excluded_by_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("seguimiento_excluded_reason", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_seguimiento_excluded_by_user",
        "orders", "users",
        ["seguimiento_excluded_by_user_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_orders_seguimiento_excluded_by_user", "orders", type_="foreignkey"
    )
    op.drop_column("orders", "seguimiento_excluded_reason")
    op.drop_column("orders", "seguimiento_excluded_by_user_id")
    op.drop_column("orders", "seguimiento_excluded_at")
