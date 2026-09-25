"""Seguimiento (app) — tabla `seguimiento_legacy` (histórico de la hoja, id estable).

Hito «id de pedido estable», Fase 1. Importa el histórico de la hoja de Drive
(~7792 filas) tal cual, una fila por línea con un id sintético propio, aislada de
`orders` (no contamina contadores/filtros/FACTUSOL/workflows). El backfill casa
cada fila con su pedido real cuando lo hay, y deja las dudosas para revisión.

Revision ID: 20260928_0119
Revises: 20260927_0118
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_0119"
down_revision = "20260927_0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seguimiento_legacy",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("numero_raw", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("cliente_raw", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("fecha_raw", sa.String(length=64), nullable=False, server_default=""),
        # MySQL no admite DEFAULT en columnas TEXT (error 1101): sin server_default.
        # El ORM siempre da valor (el `default="[]"` del modelo) y la tabla nace
        # vacía, así que NOT NULL sin default no rompe ninguna fila.
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("matched_order_id", sa.String(length=36), nullable=True),
        sa.Column("match_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("match_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_seguimiento_legacy_numero_raw", "seguimiento_legacy", ["numero_raw"],
    )
    op.create_index(
        "ix_seguimiento_legacy_matched_order_id", "seguimiento_legacy", ["matched_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_seguimiento_legacy_matched_order_id", table_name="seguimiento_legacy")
    op.drop_index("ix_seguimiento_legacy_numero_raw", table_name="seguimiento_legacy")
    op.drop_table("seguimiento_legacy")
