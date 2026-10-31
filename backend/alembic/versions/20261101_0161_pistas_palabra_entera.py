"""ERP · Máquinas: pistas «al principio de palabra» y «palabra entera».

- `historico_reglas_articulo.palabra_entera`: la pista casa solo como palabra
  entera. Sin ella, casa al principio de una palabra (el cambio de casar «por
  trozo» queda acotado en el código). Se marca hoy en HEXA y LAMINA, que
  abren la palabra que ensucian (HEXAGONAL, LAMINADORA).
- Positivas que faltaban: LAMINADORA y ENCELOFANADORA (sin duplicar las que
  ya existan; `orden` 161 para que el downgrade borre solo estas).

No toca las marcas de los artículos.

Revision ID: 20261101_0161
Revises: 20261101_0160
"""

from __future__ import annotations

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "20261101_0161"
down_revision = "20261101_0160"
branch_labels = None
depends_on = None

POSITIVAS = ("LAMINADORA", "ENCELOFANADORA")
PALABRA_ENTERA = ("HEXA", "LAMINA")
ORDEN_0161 = 161


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def upgrade() -> None:
    op.add_column("historico_reglas_articulo", sa.Column(
        "palabra_entera", sa.Boolean(), nullable=False, server_default=sa.false()))
    conn = op.get_bind()
    filas = conn.execute(sa.text("SELECT id, patron FROM historico_reglas_articulo")).all()
    enteras = {_plano(p) for p in PALABRA_ENTERA}
    for rid, patron in filas:
        if _plano(patron) in enteras:
            conn.execute(sa.text("UPDATE historico_reglas_articulo SET palabra_entera = :v "
                                 "WHERE id = :i").bindparams(v=True, i=rid))
    existentes = {_plano(p) for _, p in filas}
    reglas = sa.table("historico_reglas_articulo", sa.column("patron", sa.String),
                      sa.column("orden", sa.Integer), sa.column("tipo", sa.String))
    nuevas = [{"patron": p, "orden": ORDEN_0161, "tipo": "maquina"}
              for p in POSITIVAS if _plano(p) not in existentes]
    if nuevas:
        op.bulk_insert(reglas, nuevas)


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM historico_reglas_articulo WHERE orden = {ORDEN_0161}"))
    op.drop_column("historico_reglas_articulo", "palabra_entera")
