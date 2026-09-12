"""ERP · cobro manual F-4-B desde la app — estado de cobro FACTUSOL por pedido.

Campos en `orders`:

- `factusol_invoice_serie`: serie (TIPFAC) de la factura del pedido. El pedido
  solo guardaba el CODFAC; la clave de F_FAC es COMPUESTA (serie, código) y
  hace falta para leer/registrar su cobro sin volver a adivinarla.
- `factusol_cobro_status`: estado de cobro EN FACTUSOL («cobrada» = ESTFAC=2 o
  saldo 0 en F_LCO; «pendiente» = factura emitida sin cobro completo). Es el
  estado CONTABLE, distinto del «Pagado» del CRM. Indexado para el filtro de
  la bandeja. NULL = sin factura o sin comprobar.
- `factusol_cobro_checked_at`: cuándo se comprobó por última vez.

Revision ID: 20260915_0105
Revises: 20260914_0104
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260915_0105"
down_revision = "20260914_0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("factusol_invoice_serie", sa.Integer(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("factusol_cobro_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("factusol_cobro_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_orders_factusol_cobro_status"),
        "orders", ["factusol_cobro_status"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_factusol_cobro_status"), table_name="orders")
    op.drop_column("orders", "factusol_cobro_checked_at")
    op.drop_column("orders", "factusol_cobro_status")
    op.drop_column("orders", "factusol_invoice_serie")
