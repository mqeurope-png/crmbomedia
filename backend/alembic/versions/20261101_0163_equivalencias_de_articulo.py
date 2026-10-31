"""ERP · Histórico: códigos de artículo equivalentes (el mismo modelo con dos
códigos).

- `historico_equivalencias` (codart → canonico): el código interno de FACTUSOL
  y el de la web son la misma máquina. Sirve para emparejar un albarán y su
  factura con códigos distintos, contar una máquina en el parque y encontrar
  las ventas de los dos en «Quién compró qué». Se edita desde la pantalla.
- Siembra los cinco pares de Flux conocidos (canónico ← web): 60995 ← 34,
  61000 ← 234, 60997 ← 1398, BT2EYB ← 4173, 61403 ← 4174.

- Las familias que usan los artículos y faltan en `historico_familias` (p.
  ej. una «19» que solo sale en el catálogo de un año) entran SIN clase, para
  que Bart pueda clasificarlas. La carga ya lo hacía, salvo cuando fallaba
  `F_FAM`. Los códigos tal cual: «014» y «14» son familias distintas.

No toca las marcas de los artículos, ni las familias que ya existen (ni su
clase ni su gama), ni las series.

Revision ID: 20261101_0163
Revises: 20261101_0162
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261101_0163"
down_revision = "20261101_0162"
branch_labels = None
depends_on = None

PARES = (("60995", "34"), ("61000", "234"), ("60997", "1398"), ("BT2EYB", "4173"),
         ("61403", "4174"))
POR = "(migración 0163)"


def upgrade() -> None:
    op.create_table(
        "historico_equivalencias",
        sa.Column("codart", sa.String(64), primary_key=True),
        sa.Column("canonico", sa.String(64), nullable=False),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_historico_equivalencias_canonico", "historico_equivalencias",
                    ["canonico"])
    tabla = sa.table("historico_equivalencias", sa.column("codart", sa.String),
                     sa.column("canonico", sa.String), sa.column("por", sa.String),
                     sa.column("at", sa.DateTime))
    ahora = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(tabla, [{"codart": web, "canonico": interno, "por": POR, "at": ahora}
                           for interno, web in PARES])

    conn = op.get_bind()
    usadas = set(conn.execute(sa.text(
        "SELECT DISTINCT familia FROM historico_articulos WHERE familia IS NOT NULL")).scalars())
    existen = set(conn.execute(sa.text("SELECT codigo FROM historico_familias")).scalars())
    # Sin distinguir mayúsculas (como la clave en MySQL); los ceros sí cuentan.
    vistas = {c.strip().upper() for c in existen if c}
    faltan = []
    for c in sorted(u for u in usadas if u and u.strip()):
        if c.strip().upper() not in vistas:
            vistas.add(c.strip().upper())
            faltan.append(c)
    if faltan:
        familias = sa.table("historico_familias", sa.column("codigo", sa.String),
                            sa.column("clase_por", sa.String))
        op.bulk_insert(familias, [{"codigo": c, "clase_por": POR} for c in faltan])


def downgrade() -> None:
    # Solo las familias que metió esta migración y nadie ha clasificado.
    op.execute(sa.text("DELETE FROM historico_familias WHERE clase_por = :por "
                       "AND clase IS NULL AND gama IS NULL").bindparams(por=POR))
    op.drop_index("ix_historico_equivalencias_canonico", table_name="historico_equivalencias")
    op.drop_table("historico_equivalencias")
