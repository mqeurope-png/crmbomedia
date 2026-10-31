"""ERP · Parque: «Máquina» por artículo, sus pistas y las series que cuentan.

- `historico_reglas_articulo`: pistas en la descripción que delatan que un
  artículo NO es una máquina («FILTER», «LENS», «SPARE PARTS»…). Proponen; la
  casilla la marca Bart. Se siembran las de la lista de Bart; son datos.
- `historico_series`: si una serie cuenta como venta. Tabla vacía: todas
  cuentan hasta que se diga lo contrario.

No toca ningún dato existente: la clasificación de familias y las clases
propias de artículo se quedan como están.

Revision ID: 20261031_0158
Revises: 20261030_0157
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261031_0158"
down_revision = "20261030_0157"
branch_labels = None
depends_on = None

#: Las pistas de Bart (31/10/2026).
PISTAS = (
    "SPARE PARTS", "RECAMBIO", "FILTER", "FILTRO", "LENS", "LENTE", "TUBE", "TUBO",
    "ROTARY MODULE", "SOFTWARE", "CARTRIDGE", "CARTUCHO", "ADAPTER", "ADAPTADOR",
    "KIT", "GARANTÍA", "GARANTIE", "WARRANTY", "MANUAL", "PUMP", "BOMBA", "MIRROR",
    "ESPEJO", "LID", "TAPA", "HOSE", "PLATFORM", "DONGLE", "PROBE", "COVER",
)


def upgrade() -> None:
    reglas = op.create_table(
        "historico_reglas_articulo",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patron", sa.String(64), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False, server_default="100"),
    )
    op.create_table(
        "historico_series",
        sa.Column("serie", sa.String(8), primary_key=True),
        sa.Column("cuenta", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    op.bulk_insert(reglas, [{"patron": p, "orden": 100} for p in PISTAS])


def downgrade() -> None:
    op.drop_table("historico_series")
    op.drop_table("historico_reglas_articulo")
