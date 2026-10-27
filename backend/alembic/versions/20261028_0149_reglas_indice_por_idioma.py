"""Reglas del índice de las webs: se quita el parche de FLUX y entra el WhiteRIP.

Desde ahora el lector decide qué productos entran UNA vez por web, en su
idioma por defecto, y lleva la decisión a los demás idiomas por id
(`app.services.webs.lector`): las reglas `categoria_fuera` se escriben solo en
el idioma por defecto. Por eso:

1. Se BORRAN las reglas `categoria_fuera` de la web `fluxlasers`: el parche
   a mano del 27/10/2026 (29 nombres traducidos exactos, «Spare Parts»,
   «Ersatzteile», «Reserveonderdelen»…) que paró los 1.219 productos de
   fluxlasers.eu. Ya no hacen falta y, como los nombres traducidos no deciden
   nada, tampoco harían nada. Solo esas: las de partida no son de ninguna web
   y ninguna otra regla de categoría es de una web concreta. Se apunta en el
   registro CADA regla borrada (patrón, activo, posición y nota) y cuántas:
   el parche se puede reponer a mano desde el registro del despliegue.
2. Se AÑADE `producto_incluir_id` 493 de `artisjet-eu` (si no está ya): el
   «WhiteRIP LOGICIEL D'IMPRESSION» es software, pero su categoría 19
   «Logiciels» es hija de la 18 «Autres pièces de rechange» y, con la
   herencia de las categorías de fuera (#564), quedaba fuera en todos los
   idiomas. Como el 477, que ya estaba. La fila lleva una nota propia de la
   0149 (`_NOTA_WHITERIP`), que es lo que mira la bajada.

No toca `web_indice`: las 1.053 filas de FLUX que se marcaron idas el 27/10
siguen idas (el siguiente recorrido decide lo que vuelve).

Revision ID: 20261028_0149
Revises: 20261027_0148
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0149"
down_revision: str | None = "20261027_0148"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_SITIO_FLUX = "fluxlasers"
_INCLUIR = "producto_incluir_id"
_SITIO_WHITERIP = "artisjet-eu"
_ID_WHITERIP = "493"
#: Marca la fila que pone la 0149: bajar solo quita esa (una que ya estaba,
#: con otra nota o con la de las reglas de partida, se queda).
_NOTA_WHITERIP = "WhiteRIP: software colgado de «Autres pièces de rechange» (migración 0149)"

_reglas = sa.table(
    "web_indice_reglas",
    sa.column("id", sa.Integer),
    sa.column("tipo", sa.String),
    sa.column("patron", sa.String),
    sa.column("sitio", sa.String),
    sa.column("activo", sa.Boolean),
    sa.column("nota", sa.String),
    sa.column("posicion", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _whiterip() -> sa.ColumnElement[bool]:
    return sa.and_(_reglas.c.tipo == _INCLUIR, _reglas.c.patron == _ID_WHITERIP,
                   _reglas.c.sitio == _SITIO_WHITERIP)


def upgrade() -> None:
    bind = op.get_bind()

    parche = bind.execute(
        sa.select(_reglas.c.id, _reglas.c.patron, _reglas.c.activo, _reglas.c.posicion,
                  _reglas.c.nota)
        .where(_reglas.c.tipo == "categoria_fuera", _reglas.c.sitio == _SITIO_FLUX)
        .order_by(_reglas.c.posicion, _reglas.c.id)).all()
    for regla in parche:
        # Una línea por regla: el parche se puede reponer a mano desde aquí.
        logger.info("0149: borrada la regla categoria_fuera de %s: %r (activo=%s, posicion=%s, "
                    "nota=%r)", _SITIO_FLUX, regla.patron, bool(regla.activo), regla.posicion,
                    regla.nota)
    if parche:
        bind.execute(sa.delete(_reglas).where(_reglas.c.id.in_([r.id for r in parche])))
    logger.info("0149: %s reglas categoria_fuera de %s borradas (el parche del 27/10)",
                len(parche), _SITIO_FLUX)

    if bind.execute(sa.select(sa.func.count()).select_from(_reglas)
                    .where(_whiterip())).scalar():
        logger.info("0149: la regla %s %s de %s ya estaba", _INCLUIR, _ID_WHITERIP,
                    _SITIO_WHITERIP)
        return
    posicion = bind.execute(sa.select(sa.func.max(_reglas.c.posicion))
                            .where(_reglas.c.tipo == _INCLUIR)).scalar()
    ahora = datetime.now(UTC)
    bind.execute(sa.insert(_reglas).values(
        tipo=_INCLUIR, patron=_ID_WHITERIP, sitio=_SITIO_WHITERIP, activo=True,
        nota=_NOTA_WHITERIP, posicion=0 if posicion is None else int(posicion) + 1,
        created_at=ahora, updated_at=ahora))
    logger.info("0149: regla %s %s de %s añadida", _INCLUIR, _ID_WHITERIP, _SITIO_WHITERIP)


def downgrade() -> None:
    """Quita el 493 de `artisjet-eu` SOLO si es el que puso la 0149 (su nota
    propia, `_NOTA_WHITERIP`); si ya estaba antes (la subida no lo puso), se
    queda.

    Las reglas del parche de FLUX NO se reponen: están en el registro del
    despliegue de la 0149 («0149: borrada la regla categoria_fuera de
    fluxlasers: …», una por regla). Ojo: el código de antes decidía cada
    idioma con los nombres de su árbol y, sin ese parche, los repuestos de
    FLUX en en/de/fr/nl («Spare Parts», «Ersatzteile»…) vuelven a entrar. Si
    se vuelve atrás el código, hay que reponerlas a mano desde ese registro.
    """
    op.get_bind().execute(sa.delete(_reglas).where(
        _whiterip(), _reglas.c.nota == _NOTA_WHITERIP))
