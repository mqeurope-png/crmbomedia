"""ERP · Conciliación: el extracto del banco, por Drive.

Cada cuenta puede tener su hoja de Drive (la de Bart): BoHub la lee sola
(botón «Traer de Drive» y un tic diario) y, al conciliar, escribe la factura
y el pedido en la fila del movimiento, solo en celdas vacías.

- `bank_accounts`: la hoja (`drive_file_id`, `drive_pestana`), si se escribe
  en ella (`drive_escribir`, encendido de partida), qué es el fichero
  (`drive_formato`: hoja de Google o .xlsx) y la última pasada.
- `bank_movements`: el estado de la escritura de cada movimiento
  (`hoja_estado`) y lo que BoHub escribió (`hoja_factura`, `hoja_pedido`).

Columnas nuevas, aditivas y sin backfill. No toca FACTUSOL.

Revision ID: 20261030_0152
Revises: 20261029_0151
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0152"
down_revision = "20261029_0151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bank_accounts", sa.Column("drive_file_id", sa.String(length=128)))
    op.add_column("bank_accounts", sa.Column("drive_pestana", sa.String(length=120)))
    op.add_column(
        "bank_accounts",
        sa.Column("drive_escribir", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("bank_accounts", sa.Column("drive_formato", sa.String(length=8)))
    op.add_column("bank_accounts", sa.Column("drive_leida_at", sa.DateTime()))
    op.add_column("bank_accounts", sa.Column("drive_resultado_json", sa.Text()))
    op.add_column("bank_movements", sa.Column("hoja_estado", sa.String(length=16)))
    op.add_column("bank_movements", sa.Column("hoja_estado_at", sa.DateTime()))
    op.add_column("bank_movements", sa.Column("hoja_factura", sa.String(length=255)))
    op.add_column("bank_movements", sa.Column("hoja_pedido", sa.String(length=255)))
    op.create_index("ix_bank_movements_hoja_estado", "bank_movements", ["hoja_estado"])


def downgrade() -> None:
    op.drop_index("ix_bank_movements_hoja_estado", table_name="bank_movements")
    for columna in ("hoja_pedido", "hoja_factura", "hoja_estado_at", "hoja_estado"):
        op.drop_column("bank_movements", columna)
    for columna in ("drive_resultado_json", "drive_leida_at", "drive_formato",
                    "drive_escribir", "drive_pestana", "drive_file_id"):
        op.drop_column("bank_accounts", columna)
