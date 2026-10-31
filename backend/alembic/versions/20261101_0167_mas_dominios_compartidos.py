"""CRM · Los dominios que aprobó Bart tras el diagnóstico del 31/10.

Añade a `dominios_compartidos`:

- 33 proveedores de correo (outlook.fr, live.de, gmx.net…). No estaban en la
  lista vieja, así que SÍ se emparejaban por dominio: puede haber empresas
  «Outlook» o «Live» con contactos pegados. El script de despegue los separa
  (de un proveedor de correo, solo de la empresa que lleva ese dominio).
- 16 de administración y educación (gva.es, ub.edu…).
- `bomedia.net`: el dominio de casa. Esos contactos no son de ningún cliente.

Los que ya estén (añadidos a mano en la pantalla) se dejan como están. No
despega nada: eso lo hace `scripts/despegar_dominios_compartidos.py`.

Revision ID: 20261101_0167
Revises: 20261101_0166
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261101_0167"
down_revision = "20261101_0166"
branch_labels = None
depends_on = None

POR = "migración 0167 (aprobado por Bart, 31/10)"

CORREO = (
    "outlook.fr", "outlook.de", "outlook.be", "outlook.pt",
    "live.fr", "live.nl", "live.de", "live.be",
    "hotmail.de", "hotmail.be", "hotmail.ch", "hotmail.co.uk",
    "gmx.at", "gmx.net", "gmx.ch", "yahoo.de", "yahoo.com.ar",
    "t-online.de", "wanadoo.fr", "bluewin.ch", "sapo.pt", "home.nl", "skynet.be",
    "ono.com", "orange.com", "mail.de", "mail.telepac.pt", "laposte.net", "sfr.fr",
    "euskalnet.net", "infonegocio.com", "gmail.de", "gmail.con",
)
ADMINISTRACION = (
    "edu.gva.es", "gva.es", "edu.xunta.es", "canariaseducacion.es", "mecd.es", "mde.es",
    "ub.edu", "upc.edu", "mondragon.edu", "ie.edu", "fundacio.urv.cat", "eua.edu.es",
    "easo.eus", "ac-montpellier.fr", "andorra.ad", "icab.cat",
)
PROPIO = ("bomedia.net",)

NOTA_CORREO = "Proveedor de correo personal"
NOTA_ADMINISTRACION = "Administración o educación: lo comparten muchos centros"
NOTA_PROPIO = "Dominio de Bomedia: los de casa no son de ningún cliente"


def filas() -> list[dict]:
    ahora = datetime.now(UTC).replace(tzinfo=None)
    return [
        *({"dominio": d, "tipo": "correo", "nota": NOTA_CORREO, "por": POR, "at": ahora}
          for d in CORREO),
        *({"dominio": d, "tipo": "compartido", "nota": NOTA_ADMINISTRACION, "por": POR,
           "at": ahora} for d in ADMINISTRACION),
        *({"dominio": d, "tipo": "compartido", "nota": NOTA_PROPIO, "por": POR, "at": ahora}
          for d in PROPIO),
    ]


def upgrade() -> None:
    conn = op.get_bind()
    tabla = sa.table("dominios_compartidos", sa.column("dominio", sa.String),
                     sa.column("tipo", sa.String), sa.column("nota", sa.String),
                     sa.column("por", sa.String), sa.column("at", sa.DateTime))
    # Sin mayúsculas: en MySQL la clave primaria no las distingue.
    ya = {(r[0] or "").strip().lower() for r in conn.execute(sa.select(tabla.c.dominio))}
    nuevas = [f for f in filas() if f["dominio"] not in ya]
    if nuevas:
        op.bulk_insert(tabla, nuevas)


def downgrade() -> None:
    # Solo las que puso esta migración (si alguien las retocó a mano, se quedan).
    tabla = sa.table("dominios_compartidos", sa.column("dominio", sa.String),
                     sa.column("por", sa.String))
    op.get_bind().execute(sa.delete(tabla).where(tabla.c.por == POR))
