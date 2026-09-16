"""Lote 7 · P1 — serie (empresa emisora) elegida a mano para un pedido MANUAL.

`factusol_manual_serie` (entero, nullable): 1 Bomedia / 2 MQ Europe / 4 Lambert
/ 5 Streamtec. Se fija al crear el pedido y se puede cambiar desde la ficha;
manda en `service.resolve_serie` (por encima del `by_source`/default) para que
todo documento que BoHub emita desde el pedido salga en esa serie. NULL = sin
elección explícita.

Revision ID: 20260921_0112
Revises: 20260920_0111
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_0112"
down_revision = "20260920_0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("factusol_manual_serie", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "factusol_manual_serie")
