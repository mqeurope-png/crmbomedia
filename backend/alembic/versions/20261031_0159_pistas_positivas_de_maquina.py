"""ERP · Parque: pistas positivas de máquina (la propuesta exige evidencia).

- `historico_reglas_articulo.tipo`: `maquina` (el nombre dice que es una
  máquina) o `no_maquina` (delata un recambio, accesorio…). Las 30 de la 0158
  son `no_maquina`.
- Se siembran las positivas de Bart (IMPRESORA, BEAMBOX, HEXA…) y algunas
  negativas más, de los artículos que la primera propuesta daba por máquina
  (MAINBOARD, MOTOR, FAN, SENSOR…). Son datos editables.

No toca las marcas de los artículos (`historico_articulos_clase`).

Revision ID: 20261031_0159
Revises: 20261031_0158
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261031_0159"
down_revision = "20261031_0158"
branch_labels = None
depends_on = None

POSITIVAS = (
    "IMPRESORA", "PRINTER", "DUPLICADORA", "ENCELOFANADORA", "BEAMO", "BEAMBOX", "HEXA",
    "ADOR", "SPEEDSTAR", "CELOPACK", "MBO", "ARTIS", "OMNIPRINTER", "EPSON PP",
)
NEGATIVAS = (
    "MAINBOARD", "MOTOR", "STEPPER", "FAN", "POWER SUPPLY", "CLAMP", "DUCT", "SENSOR",
    "LUBRICANT", "CUCHILLA", "LAMINA", "PLATO", "MESA", "RASPBERRY",
)


def upgrade() -> None:
    op.add_column("historico_reglas_articulo", sa.Column(
        "tipo", sa.String(16), nullable=False, server_default="no_maquina"))
    reglas = sa.table("historico_reglas_articulo", sa.column("patron", sa.String),
                      sa.column("orden", sa.Integer), sa.column("tipo", sa.String))
    op.bulk_insert(reglas, [{"patron": p, "orden": 100, "tipo": "maquina"} for p in POSITIVAS]
                   + [{"patron": p, "orden": 100, "tipo": "no_maquina"} for p in NEGATIVAS])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM historico_reglas_articulo WHERE tipo = 'maquina'"))
    for p in NEGATIVAS:
        op.execute(sa.text("DELETE FROM historico_reglas_articulo WHERE patron = :p")
                   .bindparams(p=p))
    op.drop_column("historico_reglas_articulo", "tipo")
