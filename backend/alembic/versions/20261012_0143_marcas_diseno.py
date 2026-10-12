"""Los datos de diseño de cada marca del compositor.

`kb_catalogo_marcas.datos_json`: lo que trae el compositor para pintar la
tira de marca de un correo y no tiene columna (la etiqueta visible, el texto
del enlace por idioma, el alto y el ancho máximo del logo, el fondo del logo y
el color de la línea divisoria). Lo rellena la siguiente sincronización del
compositor; mientras, el correo usa valores por defecto. Sin datos que migrar.

Revision ID: 20261012_0143
Revises: 20261011_0142
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0143"
down_revision: str | None = "20261011_0142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kb_catalogo_marcas") as batch:
        batch.add_column(sa.Column("datos_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kb_catalogo_marcas") as batch:
        batch.drop_column("datos_json")
