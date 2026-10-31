"""ERP · Histórico: alias de código de artículo de las líneas.

`historico_alias_articulo` (codigo → codart): una línea tecleada con un
código que no está en ningún catálogo y que es un artículo del catálogo con
otro código (los ceros a la izquierda: `053332` es `53332`, `8` es `00008`).
Se compara con él; no se reescribe ni la línea ni el catálogo.

Siembra los nueve que se miraron uno a uno el 1/11/2026. Los dudosos (`1` y
`4`, con dos candidatos cada uno) NO: los decide Bart desde la pantalla.

No toca las marcas de los artículos, ni las familias, ni las series.

Revision ID: 20261101_0164
Revises: 20261101_0163
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261101_0164"
down_revision = "20261101_0163"
branch_labels = None
depends_on = None

ALIAS = (("053332", "53332"), ("8", "00008"), ("7", "00007"), ("80", "00080"),
         ("100", "00100"), ("020", "00020"), ("010", "00010"), ("030", "00030"),
         ("040", "00040"))
POR = "(migración 0164)"


def upgrade() -> None:
    op.create_table(
        "historico_alias_articulo",
        sa.Column("codigo", sa.String(64), primary_key=True),
        sa.Column("codart", sa.String(64), nullable=False),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    tabla = sa.table("historico_alias_articulo", sa.column("codigo", sa.String),
                     sa.column("codart", sa.String), sa.column("por", sa.String),
                     sa.column("at", sa.DateTime))
    ahora = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(tabla, [{"codigo": c, "codart": a, "por": POR, "at": ahora}
                           for c, a in ALIAS])


def downgrade() -> None:
    op.drop_table("historico_alias_articulo")
