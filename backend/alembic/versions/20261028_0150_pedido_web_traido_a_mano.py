"""ERP · Pedidos web traídos a mano (`orders.manual_web_import_*`).

Un pedido de la tienda en `on-hold` (transferencia aún sin cobrar) no entra
solo en BoHub; desde ERP · Pedidos se puede TRAER A MANO, uno a uno. Traerlo
es la autorización de trabajarlo como un pedido normal aunque todavía no esté
cobrado: aquí se guarda quién y cuándo lo trajo.

Dos columnas nuevas, aditivas y sin backfill: ningún pedido existente se ha
traído a mano (NULL), que es el comportamiento de siempre. No toca ningún otro
dato ni FACTUSOL.

Revision ID: 20261028_0150
Revises: 20261028_0149
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0150"
down_revision = "20261028_0149"
branch_labels = None
depends_on = None

_FK = "fk_orders_manual_web_import_by_user"


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("manual_web_import_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "manual_web_import_by_user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL", name=_FK), nullable=True,
        ),
    )


def downgrade() -> None:
    # En MySQL una columna con clave foránea no se puede borrar sin quitar
    # antes la clave (SQLite no tiene ALTER de restricciones y no lo necesita).
    if op.get_bind().dialect.name == "mysql":
        op.drop_constraint(_FK, "orders", type_="foreignkey")
    op.drop_column("orders", "manual_web_import_by_user_id")
    op.drop_column("orders", "manual_web_import_at")
