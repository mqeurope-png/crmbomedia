"""ERP · Máquinas: umbral de precio como dato y más pistas positivas.

- `historico_ajustes` (clave, valor): ajustes del histórico como datos. Se
  siembra `umbral_precio_maquina` = 2000 (€): un artículo SIN pista en el
  texto y con precio unitario máximo por encima se propone como dudoso.
- Pistas positivas que faltaban y se repiten en el catálogo: ADR, TORRE
  DUPLICADORA, PLANCHA, MULTIFUNCION, FIBER LASER (sin duplicar las que
  alguien haya añadido; `orden` 160 para que el downgrade borre solo estas).

No toca las marcas de los artículos (`historico_articulos_clase`).

Revision ID: 20261101_0160
Revises: 20261031_0159
"""

from __future__ import annotations

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "20261101_0160"
down_revision = "20261031_0159"
branch_labels = None
depends_on = None

POSITIVAS = ("ADR", "TORRE DUPLICADORA", "PLANCHA", "MULTIFUNCION", "FIBER LASER")
UMBRAL = "2000"
ORDEN_0160 = 160


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def upgrade() -> None:
    ajustes = op.create_table(
        "historico_ajustes",
        sa.Column("clave", sa.String(64), primary_key=True),
        sa.Column("valor", sa.String(255), nullable=False),
    )
    op.bulk_insert(ajustes, [{"clave": "umbral_precio_maquina", "valor": UMBRAL}])
    conn = op.get_bind()
    existentes = {_plano(p) for (p,) in conn.execute(
        sa.text("SELECT patron FROM historico_reglas_articulo")).all()}
    reglas = sa.table("historico_reglas_articulo", sa.column("patron", sa.String),
                      sa.column("orden", sa.Integer), sa.column("tipo", sa.String))
    nuevas = [{"patron": p, "orden": ORDEN_0160, "tipo": "maquina"}
              for p in POSITIVAS if _plano(p) not in existentes]
    if nuevas:
        op.bulk_insert(reglas, nuevas)


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM historico_reglas_articulo WHERE orden = {ORDEN_0160}"))
    op.drop_table("historico_ajustes")
