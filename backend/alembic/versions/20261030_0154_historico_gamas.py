"""ERP · Histórico de ventas: la gama de cada familia y las reglas que proponen
clase y gama.

- `historico_familias`: dos columnas nuevas, vacías: `gama` y
  `propuesta_descartada`. La `clase` que ya puso Bart no se toca (las clases
  nuevas, recambio y accesorio, solo se ponen desde la pantalla).
- `historico_gamas`: la lista de gamas, con las ocho de partida (UV LED, Láser,
  DTF, CD-DVD, Packaging, Textil, CNC, Otros). Ampliable desde la pantalla.
- `historico_reglas_familia`: las reglas de propuesta por código de familia,
  sacadas de los nombres en el encargo del 30/10. Son una PROPUESTA: no
  clasifican nada solas; Bart acepta o corrige en la pantalla, y las edita.

Revision ID: 20261030_0154
Revises: 20261030_0153
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0154"
down_revision = "20261030_0153"
branch_labels = None
depends_on = None

GAMAS = [
    ("uv-led", "UV LED"), ("laser", "Láser"), ("dtf", "DTF"), ("cd-dvd", "CD-DVD"),
    ("packaging", "Packaging"), ("textil", "Textil"), ("cnc", "CNC"), ("otros", "Otros"),
]

#: (gama, clase, códigos de familia). Las que nadie puede adivinar (UV, 118,
#: 251, 071, RIP, 290, 100, 120, 20, 004) no llevan regla.
REGLAS = [
    ("uv-led", "maquina", ["022", "094", "068", "093", "302"]),
    ("uv-led", "consumible", ["501", "701", "702", "703", "705", "706"]),
    ("uv-led", "recambio", ["016", "086", "087", "088", "089", "090", "114", "115", "121",
                            "506", "557", "851"]),
    ("uv-led", "accesorio", ["023", "601"]),
    ("laser", "maquina", ["021", "059"]),
    ("laser", "recambio", ["211", "212"]),
    ("laser", "accesorio", ["201", "202", "203", "204", "205"]),
    ("cd-dvd", "maquina", ["006", "013"]),
    ("cd-dvd", "consumible", ["001", "002", "003", "015", "030", "031", "032", "034", "035",
                              "036", "037"]),
    ("cd-dvd", "recambio", ["119", "850", "999"]),
    ("packaging", "maquina", ["011", "99"]),
    ("packaging", "consumible", ["009", "010", "014", "Bob"]),
    ("packaging", "recambio", ["14", "018", "019", "020", "116", "800"]),
    ("dtf", "maquina", ["060", "301"]),
    ("dtf", "consumible", ["024", "026", "504", "505"]),
    ("textil", "maquina", ["117"]),
    ("textil", "consumible", ["502", "503"]),
]


def upgrade() -> None:
    op.add_column("historico_familias", sa.Column("gama", sa.String(32), nullable=True))
    op.add_column("historico_familias",
                  sa.Column("propuesta_descartada", sa.String(64), nullable=True))

    gamas = op.create_table(
        "historico_gamas",
        sa.Column("codigo", sa.String(32), primary_key=True),
        sa.Column("nombre", sa.String(64), nullable=False),
        sa.Column("orden", sa.Integer, nullable=False, server_default="0"),
    )
    reglas = op.create_table(
        "historico_reglas_familia",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campo", sa.String(16), nullable=False),
        sa.Column("patron", sa.String(64), nullable=False),
        sa.Column("clase", sa.String(16), nullable=True),
        sa.Column("gama", sa.String(32), nullable=True),
        sa.Column("orden", sa.Integer, nullable=False, server_default="100"),
    )
    op.bulk_insert(gamas, [{"codigo": c, "nombre": n, "orden": (i + 1) * 10}
                           for i, (c, n) in enumerate(GAMAS)])
    op.bulk_insert(reglas, [{"campo": "codigo", "patron": codigo, "clase": clase,
                             "gama": gama, "orden": 100}
                            for gama, clase, codigos in REGLAS for codigo in codigos])


def downgrade() -> None:
    op.drop_table("historico_reglas_familia")
    op.drop_table("historico_gamas")
    op.drop_column("historico_familias", "propuesta_descartada")
    op.drop_column("historico_familias", "gama")
