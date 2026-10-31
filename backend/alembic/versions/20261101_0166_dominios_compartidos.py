"""CRM · Dominios que no emparejan y apuntes del despegue.

- `dominios_compartidos`: los dominios de correo que NUNCA vinculan un
  contacto a una empresa. Hasta ahora era `PERSONAL_DOMAINS`, una lista escrita
  en el código (`company_extraction.py`): pasa a ser un dato editable. Se
  siembra con esa misma lista (tipo `correo`) y con los once dominios de
  consejerías de educación medidos el 31/10 (tipo `compartido`).
- `crm_despegue_dominio`: un apunte por contacto despegado de la empresa a la
  que lo pegó un dominio compartido (y por empresa archivada), con la empresa
  anterior, para poder deshacer un lote entero.

No despega nada: eso lo hace `scripts/despegar_dominios_compartidos.py`, con
`--probar` primero. No toca FACTUSOL.

Revision ID: 20261101_0166
Revises: 20261101_0165
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261101_0166"
down_revision = "20261101_0165"
branch_labels = None
depends_on = None

#: La lista que vivía en `company_extraction.PERSONAL_DOMAINS` (copiada, no
#: importada: una migración no depende del código de la app).
CORREO = (
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.es", "yahoo.co.uk", "yahoo.fr",
    "hotmail.com", "hotmail.es", "hotmail.fr", "outlook.com", "outlook.es", "live.com",
    "msn.com", "icloud.com", "me.com", "protonmail.com", "proton.me", "mail.com", "aol.com",
    "gmx.com", "gmx.es", "gmx.de", "web.de", "free.fr", "orange.fr", "wanadoo.es",
    "telefonica.net", "movistar.es", "vodafone.es", "qq.com", "163.com", "naver.com",
    "yandex.com", "yandex.ru", "tutanota.com",
)

#: La semilla segura del 31/10: dominios de consejerías de educación que
#: comparten cientos de centros.
COMPARTIDOS = (
    "xtec.cat", "juntadeandalucia.es", "edu.xunta.gal", "gobiernodecanarias.org",
    "edu.jccm.es", "educa.aragon.es", "educa.jcyl.es", "educa.madrid.org",
    "hezkuntza.net", "educaib.eu", "educacion.navarra.es",
)


def upgrade() -> None:
    tabla = op.create_table(
        "dominios_compartidos",
        sa.Column("dominio", sa.String(255), primary_key=True),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("nota", sa.String(255), nullable=True),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    ahora = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(tabla, [
        *({"dominio": d, "tipo": "correo", "nota": "Proveedor de correo personal",
           "por": "migración 0166", "at": ahora} for d in CORREO),
        *({"dominio": d, "tipo": "compartido",
           "nota": "Consejería de educación: lo comparten cientos de centros (31/10)",
           "por": "migración 0166", "at": ahora} for d in COMPARTIDOS),
    ])
    op.create_table(
        "crm_despegue_dominio",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lote", sa.String(32), nullable=False),
        sa.Column("tipo", sa.String(16), nullable=False),
        sa.Column("objeto_id", sa.String(36), nullable=False),
        sa.Column("empresa_anterior", sa.String(36), nullable=True),
        sa.Column("dominio", sa.String(255), nullable=True),
        sa.Column("motivo", sa.String(64), nullable=False),
        sa.Column("archivada_antes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=True),
        sa.Column("revertido_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_crm_despegue_dominio_lote", "crm_despegue_dominio", ["lote"])
    op.create_index("ix_crm_despegue_dominio_objeto_id", "crm_despegue_dominio", ["objeto_id"])


def downgrade() -> None:
    op.drop_index("ix_crm_despegue_dominio_objeto_id", table_name="crm_despegue_dominio")
    op.drop_index("ix_crm_despegue_dominio_lote", table_name="crm_despegue_dominio")
    op.drop_table("crm_despegue_dominio")
    op.drop_table("dominios_compartidos")
