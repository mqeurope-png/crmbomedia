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

import unicodedata

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


#: `orden` de las filas que siembra esta migración: el downgrade borra solo esas.
ORDEN_0159 = 159


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def upgrade() -> None:
    op.add_column("historico_reglas_articulo", sa.Column(
        "tipo", sa.String(16), nullable=False, server_default="no_maquina"))
    conn = op.get_bind()
    existentes = {_plano(p): i for i, p in conn.execute(
        sa.text("SELECT id, patron FROM historico_reglas_articulo")).all()}
    positivas = {_plano(p) for p in POSITIVAS}
    # Una pista que alguien ya añadió con una palabra positiva (antes todas
    # eran «no es máquina») pasa a positiva, en vez de duplicarla.
    for plano, rid in existentes.items():
        if plano in positivas:
            conn.execute(sa.text("UPDATE historico_reglas_articulo SET tipo = 'maquina' "
                                 "WHERE id = :i").bindparams(i=rid))
    reglas = sa.table("historico_reglas_articulo", sa.column("patron", sa.String),
                      sa.column("orden", sa.Integer), sa.column("tipo", sa.String))
    nuevas = [{"patron": p, "orden": ORDEN_0159, "tipo": t}
              for t, lista in (("maquina", POSITIVAS), ("no_maquina", NEGATIVAS))
              for p in lista if _plano(p) not in existentes]
    if nuevas:
        op.bulk_insert(reglas, nuevas)


def downgrade() -> None:
    # Solo lo que sembró esta migración; lo añadido a mano se queda (como
    # «no es máquina», que es lo único que había antes de la 0159).
    op.execute(sa.text(f"DELETE FROM historico_reglas_articulo WHERE orden = {ORDEN_0159}"))
    op.drop_column("historico_reglas_articulo", "tipo")
