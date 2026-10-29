"""ERP · Conciliación: lo que viene anotado a mano en el extracto.

Los extractos de Bart traen dos columnas que él rellena a mano, `factura` y
`pedido`. Se guardan tal cual en el movimiento (`anotacion_factura`,
`anotacion_pedido`) y, si la anotación nombra facturas, la conciliación nace
de ahí (`bank_reconciliations.origen = 'extracto'`): el recálculo de
propuestas no la pisa.

Tres columnas nuevas, aditivas y sin backfill: hoy no hay ningún movimiento
importado (la pantalla no se había podido usar). No toca FACTUSOL.

Revision ID: 20261029_0151
Revises: 20261028_0150
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0151"
down_revision = "20261028_0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for columna in ("anotacion_factura", "anotacion_pedido"):
        op.add_column(
            "bank_movements", sa.Column(columna, sa.String(length=255), nullable=True),
        )
    op.add_column(
        "bank_reconciliations", sa.Column("origen", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bank_reconciliations", "origen")
    op.drop_column("bank_movements", "anotacion_pedido")
    op.drop_column("bank_movements", "anotacion_factura")
