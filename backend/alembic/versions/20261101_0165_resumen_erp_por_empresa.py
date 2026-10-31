"""CRM · Resumen ERP por empresa (tablas derivadas) y etiquetas automáticas.

- `crm_empresa_erp`, `crm_empresa_gama`, `crm_empresa_modelo`,
  `crm_empresa_compra`: lo que tiene y compra cada empresa de BoHub,
  precalculado para que los filtros de contactos sean SQL. Las rellena el
  recálculo (`app/services/empresa_erp.py`), que las borra y rehace enteras;
  no son datos de nadie.
- `crm_resumen_estado`: cómo fue el último recálculo (hora, fallo, recuentos).
- `crm_etiqueta_auto`: qué etiquetas automáticas (YA CLIENTE, ORIGEN WOO) ha
  puesto el recálculo y cuáles quitó alguien a mano.
- El único sector escrito antes de la lista («Fabricante palas Padel») pasa a
  «Industria y fabricación», como pidió Bart.

No toca familias, marcas, series, equivalencias ni alias.

Revision ID: 20261101_0165
Revises: 20261101_0164
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261101_0165"
down_revision = "20261101_0164"
branch_labels = None
depends_on = None

SECTOR_ANTES = "Fabricante palas Padel"
SECTOR_AHORA = "Industria y fabricación"


def upgrade() -> None:
    op.create_table(
        "crm_empresa_erp",
        sa.Column("company_id", sa.String(36), primary_key=True),
        sa.Column("codcli", sa.String(32), nullable=True),
        sa.Column("maquinas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meses_nueva", sa.Integer(), nullable=True),
        sa.Column("meses_vieja", sa.Integer(), nullable=True),
        sa.Column("tiene_baja", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gasto_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("ultima_compra", sa.Date(), nullable=True),
        sa.Column("meses_sin_comprar", sa.Integer(), nullable=True),
        sa.Column("dormido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hueco", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ya_cliente", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "crm_empresa_gama",
        sa.Column("company_id", sa.String(36), primary_key=True),
        sa.Column("gama", sa.String(32), primary_key=True),
        sa.Column("maquinas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meses_nueva", sa.Integer(), nullable=True),
        sa.Column("meses_vieja", sa.Integer(), nullable=True),
        sa.Column("dormido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hueco", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "crm_empresa_modelo",
        sa.Column("company_id", sa.String(36), primary_key=True),
        sa.Column("modelo", sa.String(64), primary_key=True),
    )
    op.create_table(
        "crm_empresa_compra",
        sa.Column("company_id", sa.String(36), primary_key=True),
        sa.Column("codart", sa.String(64), primary_key=True),
        sa.Column("familia", sa.String(32), nullable=True),
        sa.Column("clase", sa.String(16), nullable=True),
    )
    op.create_index("ix_crm_empresa_compra_familia", "crm_empresa_compra", ["familia"])
    op.create_index("ix_crm_empresa_compra_clase", "crm_empresa_compra", ["clase"])
    op.create_table(
        "crm_resumen_estado",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ok_at", sa.DateTime(), nullable=True),
        sa.Column("fallo_at", sa.DateTime(), nullable=True),
        sa.Column("fallo", sa.Text(), nullable=True),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("empresas", sa.Integer(), nullable=True),
        sa.Column("ya_cliente_empresas", sa.Integer(), nullable=True),
        sa.Column("ya_cliente_contactos", sa.Integer(), nullable=True),
        sa.Column("origen_woo_contactos", sa.Integer(), nullable=True),
        sa.Column("quitadas_a_mano", sa.Integer(), nullable=True),
    )
    op.create_table(
        "crm_etiqueta_auto",
        sa.Column("contact_id", sa.String(36), primary_key=True),
        sa.Column("etiqueta", sa.String(32), primary_key=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=True),
    )
    if sa.inspect(op.get_bind()).has_table("companies"):
        op.execute(sa.text("UPDATE companies SET sector = :ahora WHERE TRIM(sector) = :antes")
                   .bindparams(ahora=SECTOR_AHORA, antes=SECTOR_ANTES))


def downgrade() -> None:
    # El sector no se devuelve a texto libre: «Industria y fabricación» es un
    # valor de la lista y pudo ponerlo Bart a cualquier otra empresa.
    op.drop_table("crm_etiqueta_auto")
    op.drop_table("crm_resumen_estado")
    op.drop_index("ix_crm_empresa_compra_clase", table_name="crm_empresa_compra")
    op.drop_index("ix_crm_empresa_compra_familia", table_name="crm_empresa_compra")
    op.drop_table("crm_empresa_compra")
    op.drop_table("crm_empresa_modelo")
    op.drop_table("crm_empresa_gama")
    op.drop_table("crm_empresa_erp")
