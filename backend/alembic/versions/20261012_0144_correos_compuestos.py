"""Correos compuestos: el formato del turno del asistente y la estructura.

- `ia_borradores.modo`: `normal` (el cuerpo es texto para el editor, como
  hasta ahora; es el valor de todos los turnos que ya hay) o `compuesto` (el
  turno es la estructura de un correo con fichas, tira de marca, vídeo y
  botones, y `html` es el correo que genera BoHub).
- `ia_borradores.estructura_json`: la estructura (JSON de bloques).
- `email_drafts.estructura_json`: la estructura de un borrador guardado que es
  un correo compuesto, para que vuelva a la vista previa.

LONGTEXT en MySQL, como los cuerpos de los correos. Sin datos que migrar.

Revision ID: 20261012_0144
Revises: 20261012_0143
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261012_0144"
down_revision: str | None = "20261012_0143"
branch_labels = None
depends_on = None

_TEXTO_LARGO = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    with op.batch_alter_table("ia_borradores") as batch:
        batch.add_column(sa.Column("modo", sa.String(length=12), nullable=False,
                                   server_default="normal"))
        batch.add_column(sa.Column("estructura_json", _TEXTO_LARGO, nullable=True))
    with op.batch_alter_table("email_drafts") as batch:
        batch.add_column(sa.Column("estructura_json", _TEXTO_LARGO, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("email_drafts") as batch:
        batch.drop_column("estructura_json")
    with op.batch_alter_table("ia_borradores") as batch:
        batch.drop_column("estructura_json")
        batch.drop_column("modo")
