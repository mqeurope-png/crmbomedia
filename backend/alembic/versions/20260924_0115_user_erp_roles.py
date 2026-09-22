"""ERP · roles y permisos — `users.erp_roles` (multi-rol).

Roles operativos ADICIONALES del ERP por usuario (multi-rol): una lista JSON de
valores de UserRole del ámbito ERP (comercial/pedidos/sat). El permiso efectivo
es la unión de `role` + estos. Columna nueva, aditiva, sin backfill: los usuarios
existentes siguen con solo su `role` (comportamiento de siempre).

Revision ID: 20260924_0115
Revises: 20260923_0114
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_0115"
down_revision = "20260923_0114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("erp_roles", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "erp_roles")
