"""ERP · Genei — `carriers.config_json` (config no secreta del adaptador).

La config del envío que NO es secreta —couriers preferidos por país de destino
y medidas/peso de bulto por defecto— vive aquí, en JSON, aparte de las
credenciales cifradas (`api_credentials_encrypted`) y del origen
(`default_address_id`). Nullable y aditiva: los carriers existentes (manuales o
Genei sin configurar aún) siguen igual, con la config por defecto en código.

Revision ID: 20260927_0118
Revises: 20260926_0117
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_0118"
down_revision = "20260926_0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("carriers", sa.Column("config_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("carriers", "config_json")
