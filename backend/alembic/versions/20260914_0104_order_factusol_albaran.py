"""ERP · Fase 2 — nº del albarán FACTUSOL creado al convertir.

Campo en `orders`: `factusol_albaran_number` (`serie-código`, p. ej. `5-500008`),
el albarán que BoHub creó en `F_ALB` al convertir la proforma / pedido de
cliente de FACTUSOL en pedido. Solo altas manuales desde un documento de
FACTUSOL: los pedidos web NO generan albarán en BoHub (lo crea WooCommerce).
Con índice: al facturar ese albarán desde el explorador hay que localizar el
pedido para vincular la factura y registrar el cobro apuntado (opción B).

Revision ID: 20260914_0104
Revises: 20260913_0103
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0104"
down_revision = "20260913_0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("factusol_albaran_number", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_orders_factusol_albaran_number"),
        "orders", ["factusol_albaran_number"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_factusol_albaran_number"), table_name="orders")
    op.drop_column("orders", "factusol_albaran_number")
