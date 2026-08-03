"""BoHub ERP Fase B (B-2-fix4) — factusol_live + «procesado externamente».

Columnas nuevas, todas nullable / con default → cero efecto en filas
existentes. Los valores de enum nuevos (already_*_externally) NO requieren
migración: las columnas de estado son VARCHAR (StrEnum native_enum=False).

Revision ID: 20260803_0082
Revises: 20260801_0081
Create Date: 2026-08-03 15:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0082"
down_revision: str | None = "20260801_0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los estados «already_*_externally» (hasta 28 chars) no caben en el
    # VARCHAR(24) original — ampliar los 4 dominios de estado a 40.
    for col in ("payment_status", "preparation_status", "transport_status",
                "invoice_status"):
        op.alter_column(
            "orders", col,
            existing_type=sa.String(24), type_=sa.String(40),
            existing_nullable=False,
        )

    op.add_column(
        "erp_settings",
        sa.Column(
            "factusol_live", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "orders",
        sa.Column("externally_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("externally_processed_by_user_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("externally_processed_note", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_externally_processed_by",
        "orders", "users",
        ["externally_processed_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_orders_externally_processed_by", "orders", type_="foreignkey"
    )
    op.drop_column("orders", "externally_processed_note")
    op.drop_column("orders", "externally_processed_by_user_id")
    op.drop_column("orders", "externally_processed_at")
    op.drop_column("erp_settings", "factusol_live")
