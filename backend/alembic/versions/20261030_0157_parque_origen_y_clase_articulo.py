"""ERP · Parque: el origen de cada línea y la clase propia de un artículo.

- `historico_lineas.origen_tipo/origen_serie/origen_codigo`: el documento del
  que sale la línea, como lo apunta FACTUSOL (`DOC*`/`DTP*`/`DCO*`). Con eso
  el parque empareja el albarán con su factura. Vacías hasta que se vuelve a
  cargar el ejercicio; `historico_ejercicios.con_enlaces` dice cuáles ya.
- `historico_articulos_clase`: la clase de un artículo por encima de la de su
  familia («esto no es una máquina»). Tabla nueva y vacía.

No toca ningún dato existente.

Revision ID: 20261030_0157
Revises: 20261030_0156
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0157"
down_revision = "20261030_0156"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("historico_lineas", sa.Column("origen_tipo", sa.String(1), nullable=True))
    op.add_column("historico_lineas", sa.Column("origen_serie", sa.String(8), nullable=True))
    op.add_column("historico_lineas", sa.Column("origen_codigo", sa.Integer(), nullable=True))
    op.add_column("historico_ejercicios", sa.Column(
        "con_enlaces", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "historico_articulos_clase",
        sa.Column("codart", sa.String(64), primary_key=True),
        sa.Column("clase", sa.String(16), nullable=False),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("historico_articulos_clase")
    op.drop_column("historico_ejercicios", "con_enlaces")
    op.drop_column("historico_lineas", "origen_codigo")
    op.drop_column("historico_lineas", "origen_serie")
    op.drop_column("historico_lineas", "origen_tipo")
