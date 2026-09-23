"""ERP · Seguimiento — «forzar en seguimiento» (`orders.seguimiento_forced_*`).

La puerta de entrada web pasa a ser haber PASADO POR CAJA (`processing` /
`completed` / `refunded`): los `pending`, `on-hold`, `cancelled`, `failed` y
borradores quedan OCULTOS POR ESTADO. «Reincluir» no los rescata —eso deshace
la exclusión MANUAL, que es otro eje—, así que hace falta guardar la decisión
explícita de verlo igualmente, por si alguno hay que forzarlo.

Dos columnas nuevas, aditivas, sin backfill: hoy nadie fuerza nada (NULL), que
es el comportamiento de siempre. No toca el pedido ni FACTUSOL: es solo la
vista del seguimiento.

Revision ID: 20260926_0117
Revises: 20260925_0116
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_0117"
down_revision = "20260925_0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("seguimiento_forced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "seguimiento_forced_by_user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "seguimiento_forced_by_user_id")
    op.drop_column("orders", "seguimiento_forced_at")
