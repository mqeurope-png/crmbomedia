"""ERP · «No requiere envío» — `orders.shipping_not_required`.

El SAT/envío es OPCIONAL (salió del recorrido obligatorio de la línea de vida).
La Cola SAT acumula pedidos que no se van a enviar nunca (servicios, RMA,
asistencias remotas, tintas ya entregadas…). Este flag permite marcarlos —a
mano o en lote— para sacarlos de la Cola SAT y de la cola «Por enviar»; su
casilla/hito de Envío pasa a «No aplica». No toca pago, factura, cobro ni el
«completado». Reversible.

Columna nueva, aditiva, sin backfill: los pedidos existentes siguen
«requiriendo envío» (0), que es el comportamiento de siempre.

Revision ID: 20260923_0114
Revises: 20260922_0113
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_0114"
down_revision = "20260922_0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "shipping_not_required", sa.Boolean(), nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "shipping_not_required")
