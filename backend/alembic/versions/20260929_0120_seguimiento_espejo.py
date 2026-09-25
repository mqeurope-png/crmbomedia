"""Seguimiento (app) — espejo bidireccional (Fase 2): snapshot, overrides, manuales.

- `seguimiento_sync_snapshot`: última foto escrita en la hoja por id de fila.
- `seguimiento_overrides`: valores escritos a mano en columnas editables de
  filas de BoHub (nunca se copian a los campos del pedido).
- `seguimiento_manual`: filas tecleadas a mano, validadas e ingeridas con id.
- `seguimiento_legacy.deleted_at`: borrado lógico del histórico.

Sin DEFAULT en columnas TEXT (MySQL lo rechaza, error 1101): el ORM siempre
da valor.

Revision ID: 20260929_0120
Revises: 20260928_0119
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_0120"
down_revision = "20260928_0119"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "seguimiento_sync_snapshot",
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("values_json", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("row_id"),
    )
    op.create_table(
        "seguimiento_overrides",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("column_key", sa.String(length=40), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", "column_key", name="uq_seguimiento_override_col"),
    )
    op.create_index(
        "ix_seguimiento_overrides_order_id", "seguimiento_overrides", ["order_id"],
    )
    op.create_table(
        "seguimiento_manual",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("row_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("values_json", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_id", name="uq_seguimiento_manual_row_id"),
    )
    op.create_index(
        "ix_seguimiento_manual_order_id", "seguimiento_manual", ["order_id"],
    )
    op.add_column(
        "seguimiento_legacy",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("seguimiento_legacy", "deleted_at")
    op.drop_index("ix_seguimiento_manual_order_id", table_name="seguimiento_manual")
    op.drop_table("seguimiento_manual")
    op.drop_index("ix_seguimiento_overrides_order_id", table_name="seguimiento_overrides")
    op.drop_table("seguimiento_overrides")
    op.drop_table("seguimiento_sync_snapshot")
