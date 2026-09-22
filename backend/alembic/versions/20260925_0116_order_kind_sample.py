"""ERP · tipo de pedido — `orders.order_kind` (muestra / no facturable).

Hace falta poder mandar una MUESTRA o un envío de cortesía (una pieza
olvidada, material de prueba) sin que sea un pedido facturable: sin empresa,
sin serie y sin pasar por FACTUSOL. Ese pedido solo se prepara y se envía
desde el taller, y sus pasos fiscales (pago/albarán/factura/cobro) salen
«No aplica».

Mismo patrón que `order_lines.line_kind`:

- NULL     → pedido normal, FACTURABLE (el valor implícito de siempre: los
             pedidos existentes no necesitan backfill).
- 'sample' → muestra / envío no facturable.

Es ORTOGONAL a `external_source`, que sigue diciendo el ORIGEN del pedido
(web / manual / FACTUSOL): una muestra se da de alta a mano, pero no es un
pedido manual corriente, así que mezclar ambas cosas en una sola columna
confundiría origen con facturabilidad.

Columna nueva y nullable: aditiva, sin backfill y reversible.

Revision ID: 20260925_0116
Revises: 20260924_0115
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_0116"
down_revision = "20260924_0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("order_kind", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "order_kind")
