"""El catálogo contra los datos reales: precio público y entradas descartadas.

`kb_catalogo.precio_publico`: False cuando el origen dice «Consultar» (el
producto no tiene precio público; el correo dice que se prepara a medida).
`kb_catalogo.descartado` y `motivo_descarte`: una entrada del compositor que no
es un producto (un premio, sin precio ni área de impresión) se guarda para que
la pantalla la enseñe, pero no entra en el catálogo del asistente. Sin datos
que migrar: lo que hay queda como precio público y no descartado; la
siguiente sincronización lo recalcula.

Revision ID: 20261011_0142
Revises: 20261011_0141
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0142"
down_revision: str | None = "20261011_0141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kb_catalogo") as batch:
        batch.add_column(sa.Column("precio_publico", sa.Boolean(), nullable=False,
                                   server_default=sa.text("1")))
        batch.add_column(sa.Column("descartado", sa.Boolean(), nullable=False,
                                   server_default=sa.text("0")))
        batch.add_column(sa.Column("motivo_descarte", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kb_catalogo") as batch:
        batch.drop_column("motivo_descarte")
        batch.drop_column("descartado")
        batch.drop_column("precio_publico")
