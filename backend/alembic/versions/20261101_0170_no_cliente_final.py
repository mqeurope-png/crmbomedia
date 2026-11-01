"""CRM · «No es cliente final» en la empresa.

`companies.no_cliente_final`: la financiera del leasing, un revendedor o
nosotros mismos. Marcada, sus máquinas no cuentan en el parque, ni en dormidos y
huecos, y sus contactos quedan fuera de las campañas. Arranca a falso para
todas. Solo esquema: no toca datos.

Revision ID: 20261101_0170
Revises: 20261101_0169
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261101_0170"
down_revision = "20261101_0169"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("no_cliente_final", sa.Boolean(), nullable=False,
                                         server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("companies", "no_cliente_final")
