"""CRM · Un contacto por empresa y el contacto principal.

- `contacts.contacto_principal`: el contacto principal de su empresa, marcado a
  mano en la ficha de empresa (uno por empresa). Arranca a falso para todos:
  no se adivina ninguno.
- `segments.uno_por_empresa` y `segments.orden_contacto`: un segmento guarda
  si va «un solo contacto por empresa» y con qué orden de preferencia, para
  que Brevo y las campañas den lo mismo que la vista de la que salió.

Solo esquema: no toca datos.

Revision ID: 20261101_0168
Revises: 20261101_0167
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261101_0168"
down_revision = "20261101_0167"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("contacto_principal", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.add_column("segments", sa.Column("uno_por_empresa", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.add_column("segments", sa.Column("orden_contacto", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("segments", "orden_contacto")
    op.drop_column("segments", "uno_por_empresa")
    op.drop_column("contacts", "contacto_principal")
