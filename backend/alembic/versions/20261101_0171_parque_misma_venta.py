"""ERP · Parque: «¿misma venta?» con respuesta.

`parque_misma_venta`: la respuesta al aviso de un albarán con una factura (las
dos por la clave de su línea). `misma` verdadero, «Sí, es la misma venta»: el
albarán no cuenta, como duplicado. `misma` falso, «No, son dos compras»: el
aviso no vuelve para ese par.

Es aparte de `parque_bajas` a propósito: un duplicado no es una máquina
retirada, y contestar no toca ni deshace la baja que diera nadie. Solo
esquema: la tabla nace vacía; las bajas que ya hay siguen como estaban (no se
sabe cuáles eran duplicados; la ficha deja contestarlo).

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
    op.create_table(
        "parque_misma_venta",
        sa.Column("albaran", sa.String(160), primary_key=True),
        sa.Column("factura", sa.String(160), primary_key=True),
        sa.Column("misma", sa.Boolean(), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_parque_misma_venta_company_id", "parque_misma_venta", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_parque_misma_venta_company_id", table_name="parque_misma_venta")
    op.drop_table("parque_misma_venta")
