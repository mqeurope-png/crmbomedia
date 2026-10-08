"""Formularios web: el remitente del acuse de recibo, como dato del formulario.

El acuse que recibe quien rellena el formulario no puede salir del remitente
global de la aplicación (`info@streamtec.es`): un cliente que escribe a
mboprinters.com no entiende una respuesta que llega desde Streamtec.

`web_forms.confirmation_from_email` guarda de quién sale el acuse de cada
formulario, editable desde la pantalla. Vacío = el de su web
(`services/web_forms/sitios.py:REMITENTES`), para que un formulario nuevo de
una web conocida nunca se quede sin poder enviar.

Revision ID: 20261010_0130
Revises: 20261009_0129
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0130"
down_revision: str | None = "20261009_0129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("web_forms") as batch:
        batch.add_column(
            sa.Column("confirmation_from_email", sa.String(length=320), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("web_forms") as batch:
        batch.drop_column("confirmation_from_email")
