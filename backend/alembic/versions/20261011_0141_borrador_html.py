"""El borrador del asistente en HTML sencillo.

`ia_borradores.html`: el borrador con el formato que admite el editor (listas,
negritas, enlaces sobre el nombre, encabezados, tabla simple), limpiado al
subconjunto de etiquetas. `texto` sigue siendo el texto plano (la guardia del
cuerpo y la comparación de lo enviado). Los borradores anteriores quedan con
`html` vacío y su HTML se deriva de `texto`, como hasta ahora. Sin datos que
migrar.

Revision ID: 20261011_0141
Revises: 20261011_0140
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0141"
down_revision: str | None = "20261011_0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ia_borradores") as batch:
        batch.add_column(sa.Column("html", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ia_borradores") as batch:
        batch.drop_column("html")
