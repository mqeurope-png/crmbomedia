"""ERP · Parque: «¿misma venta?» con respuesta.

- `parque_bajas.tipo`: por qué sale una máquina del parque. NULL, retirada,
  como hasta ahora; `misma_venta`, un albarán que era la misma venta que una
  factura (un duplicado, no una máquina retirada).
- `parque_bajas.duplica`: con `misma_venta`, la línea de la factura (su clave).
- `parque_dos_compras`: los pares albarán–factura de los que alguien dijo «no,
  son dos compras». El aviso no vuelve a salir para ese par.

Solo esquema: las bajas que ya hay siguen como retiradas (no se sabe cuáles
eran duplicados). La ficha deja contestarlo en las que tienen el aviso.

Revision ID: 20261101_0171
Revises: 20261101_0170
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261101_0171"
down_revision = "20261101_0170"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parque_bajas", sa.Column("tipo", sa.String(16), nullable=True))
    op.add_column("parque_bajas", sa.Column("duplica", sa.String(160), nullable=True))
    op.create_table(
        "parque_dos_compras",
        sa.Column("albaran", sa.String(160), primary_key=True),
        sa.Column("factura", sa.String(160), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_parque_dos_compras_company_id", "parque_dos_compras", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_parque_dos_compras_company_id", table_name="parque_dos_compras")
    op.drop_table("parque_dos_compras")
    op.drop_column("parque_bajas", "duplica")
    op.drop_column("parque_bajas", "tipo")
