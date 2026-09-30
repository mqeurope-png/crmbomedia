"""Pedido — método de pago de WooCommerce (gateway y título).

`payment_method` = id del gateway (`mollie_wc_gateway_creditcard`,
`ppcp-gateway`…) y `payment_method_title` = el texto de la tienda («Carte»,
«PayPal»…). Se guardan al importar / actualizar el pedido web y se usan para
sugerir la contrapartida del cobro (reglas tienda × método de pago). NULL en
los pedidos no-Woo y en los web aún sin rellenar (la puesta al día de estados
Woo los completa).

Revision ID: 20260930_0121
Revises: 20260929_0120
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260930_0121"
down_revision = "20260929_0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("payment_method", sa.String(length=64), nullable=True))
    op.add_column(
        "orders", sa.Column("payment_method_title", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "payment_method_title")
    op.drop_column("orders", "payment_method")
