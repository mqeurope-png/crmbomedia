"""ERP-F6 — seguimiento de pedidos (sustituye el Excel manual).

- `orders`: nº de serie (texto libre, también notas), licencia WhiteRIP y
  origen del envío (el «OFI-TER-SAT» del Excel).
- `erp_settings`: credenciales de la cuenta de servicio de Google (cifradas)
  e ID de la hoja de Drive destino.
- `erp_drive_sync_rows`: última foto por pedido de lo escrito en la hoja,
  para actualizar solo lo que cambia sin pisar ediciones manuales.

Revision ID: 20260910_0100
Revises: 20260909_0099
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_0100"
down_revision = "20260909_0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("serial_number", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("whiterip_license", sa.String(length=64), nullable=True))
    op.add_column("orders", sa.Column("shipping_origin", sa.String(length=40), nullable=True))

    op.add_column(
        "erp_settings",
        sa.Column("drive_service_account_json_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "erp_settings",
        sa.Column("drive_spreadsheet_id", sa.String(length=128), nullable=True),
    )

    op.create_table(
        "erp_drive_sync_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("last_values_json", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_erp_drive_sync_rows_row_key", "erp_drive_sync_rows", ["row_key"])


def downgrade() -> None:
    op.drop_index("ix_erp_drive_sync_rows_row_key", table_name="erp_drive_sync_rows")
    op.drop_table("erp_drive_sync_rows")
    op.drop_column("erp_settings", "drive_spreadsheet_id")
    op.drop_column("erp_settings", "drive_service_account_json_encrypted")
    op.drop_column("orders", "shipping_origin")
    op.drop_column("orders", "whiterip_license")
    op.drop_column("orders", "serial_number")
