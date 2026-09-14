"""ERP · portes como línea aparte — `order_lines.is_shipping`.

En FACTUSOL los gastos de envío NO son una línea del documento: viven en la
banda `IPOR1*` de la cabecera (es donde los deja la app Woo→FACTUSOL y de
donde los lee el PDF desde ERP-F1, que los pinta como línea de cargo).

Para poder meterlos en los pedidos MANUALES «como los web», el pedido de
BoHub los lleva como su propia línea (visible y editable en la ficha) marcada
con esta columna; al emitir el albarán / la factura, esa línea NO va a
`F_LAL`/`F_LFA`: su importe va a la banda de portes de la cabecera.

Columna nueva, sin backfill: los pedidos existentes (web incluidos) no tienen
líneas de portes en BoHub — los web los traen ya en el documento de FACTUSOL.

Revision ID: 20260916_0107
Revises: 20260915_0106
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260916_0107"
down_revision = "20260915_0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_lines",
        sa.Column(
            "is_shipping", sa.Boolean(), nullable=False, server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("order_lines", "is_shipping")
