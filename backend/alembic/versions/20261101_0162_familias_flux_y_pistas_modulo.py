"""ERP · Histórico: las tres familias sin clase, y pistas negativas de módulo.

- Las tres familias que estaban SIN CLASE (31/10/2026, medidas en producción):
  - `071` (Fluxlasers, el catálogo Flux de la web): Máquina · Láser;
  - `251` (Flux que no está en la web: filtros, cartuchos, módulos,
    garantías): Consumible · Láser;
  - `118` (GENERAL: desplazamiento, kilometraje, dietas): Otro, sin gama.

  Solo si siguen sin clase: lo que Bart ya clasificó no se toca. La gama solo
  se pone si no tiene. `clase_por` lleva la marca de esta migración (y qué
  puso) para que el downgrade deshaga solo lo suyo, y solo si nadie lo ha
  cambiado después. La familia `UV` y las que no tienen gama NO se tocan:
  ya tienen clase y es de Bart.
- Pistas negativas MODULE, MÓDULO y ROTATORIO (módulos y rotatorios que
  empiezan por BEAMBOX o ADOR): con «positiva y negativa → dudosa» dejan de
  proponerse como máquina. Sin duplicar; `orden` 162 para el downgrade.

No toca las marcas de los artículos.

Revision ID: 20261101_0162
Revises: 20261101_0161
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261101_0162"
down_revision = "20261101_0161"
branch_labels = None
depends_on = None

#: codigo → (clase, gama)
FAMILIAS = {
    "071": ("maquina", "laser"),
    "251": ("consumible", "laser"),
    "118": ("otro", None),
}
MARCA = "(migración 0162)"
MARCA_CON_GAMA = "(migración 0162: clase y gama)"
NEGATIVAS = ("MODULE", "MÓDULO", "ROTATORIO")
ORDEN_0162 = 162


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def upgrade() -> None:
    conn = op.get_bind()
    ahora = datetime.now(UTC).replace(tzinfo=None)
    gamas = set(conn.execute(sa.text("SELECT codigo FROM historico_gamas")).scalars())
    for codigo, (clase, gama) in FAMILIAS.items():
        fila = conn.execute(sa.text(
            "SELECT clase, gama FROM historico_familias WHERE codigo = :c"
        ).bindparams(c=codigo)).first()
        if fila is None or fila[0] is not None:
            continue    # no existe, o ya tiene clase (de Bart): no se toca
        pone_gama = gama is not None and gama in gamas and fila[1] is None
        conn.execute(sa.text(
            "UPDATE historico_familias SET clase = :clase, gama = :gama, "
            "clase_por = :por, clase_at = :at WHERE codigo = :c AND clase IS NULL"
        ).bindparams(clase=clase, gama=gama if pone_gama else fila[1],
                     por=MARCA_CON_GAMA if pone_gama else MARCA, at=ahora, c=codigo))

    existentes = {_plano(p) for p in conn.execute(
        sa.text("SELECT patron FROM historico_reglas_articulo")).scalars()}
    reglas = sa.table("historico_reglas_articulo", sa.column("patron", sa.String),
                      sa.column("orden", sa.Integer), sa.column("tipo", sa.String),
                      sa.column("palabra_entera", sa.Boolean))
    nuevas = [{"patron": p, "orden": ORDEN_0162, "tipo": "no_maquina", "palabra_entera": False}
              for p in NEGATIVAS if _plano(p) not in existentes]
    if nuevas:
        op.bulk_insert(reglas, nuevas)


def downgrade() -> None:
    conn = op.get_bind()
    for codigo, (clase, _gama) in FAMILIAS.items():
        # Solo lo que puso esta migración y nadie ha cambiado después.
        conn.execute(sa.text(
            "UPDATE historico_familias SET clase = NULL, gama = NULL, clase_por = NULL, "
            "clase_at = NULL WHERE codigo = :c AND clase_por = :por AND clase = :clase"
        ).bindparams(c=codigo, por=MARCA_CON_GAMA, clase=clase))
        conn.execute(sa.text(
            "UPDATE historico_familias SET clase = NULL, clase_por = NULL, clase_at = NULL "
            "WHERE codigo = :c AND clase_por = :por AND clase = :clase"
        ).bindparams(c=codigo, por=MARCA, clase=clase))
    op.execute(sa.text(f"DELETE FROM historico_reglas_articulo WHERE orden = {ORDEN_0162}"))
