"""ERP-F5 — contrapartida de cobro en la cuenta bancaria de la conciliación.

Cada cuenta de BoHub se enlaza con su contrapartida de FACTUSOL (el destino
donde entra el dinero): Sabadell de Bomedia → 6, Belfius de MQ Europe → 2,
Sabadell de Streamtec → 8. Es lo que permitirá a F-4-B registrar el cobro en
la contrapartida correcta.

Revision ID: 20260909_0099
Revises: 20260908_0098
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0099"
down_revision = "20260908_0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_accounts",
        sa.Column("contrapartida_codigo", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bank_accounts", "contrapartida_codigo")
