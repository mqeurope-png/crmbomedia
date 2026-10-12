"""Ofertas: presupuestar a un lead sin tocar FACTUSOL.

Cinco tablas nuevas: `ofertas_plantillas` + `ofertas_plantillas_lineas` (las
líneas de un presupuesto real guardadas como plantilla, con sus artículos de
FACTUSOL), `ofertas` + `ofertas_lineas` (la oferta de BoHub: contacto,
empresa, serie emisora, idioma, con/sin IVA, fecha, validez, estado, totales y
el enlace al F_PRE creado al convertir), `ofertas_envios` (historial de envíos)
y `ofertas_contador` (la numeración propia `OF-2026-0001`, un contador por
año). Sin datos que migrar.

Revision ID: 20261012_0146
Revises: 20261012_0145
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0146"
down_revision: str | None = "20261012_0145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ofertas_plantillas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("nombre", sa.String(length=160), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("origen_serie", sa.Integer(), nullable=True),
        sa.Column("origen_codpre", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ofertas_plantillas_lineas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plantilla_id", sa.String(length=36), nullable=False),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("codart", sa.String(length=64), nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("precio", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("descuento_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("iva_pct", sa.Numeric(5, 2), nullable=False, server_default="21"),
        sa.ForeignKeyConstraint(
            ["plantilla_id"], ["ofertas_plantillas.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ofertas_plantillas_lineas_plantilla_id", "ofertas_plantillas_lineas",
        ["plantilla_id"],
    )
    op.create_table(
        "ofertas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("numero", sa.String(length=24), nullable=False),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("secuencia", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("plantilla_id", sa.String(length=36), nullable=True),
        sa.Column("serie", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idioma", sa.String(length=5), nullable=False, server_default="es"),
        sa.Column("con_iva", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("validez_dias", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("valida_hasta", sa.Date(), nullable=False),
        sa.Column("estado", sa.String(length=16), nullable=False, server_default="borrador"),
        sa.Column("referencia", sa.String(length=255), nullable=True),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("base", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("iva_pct", sa.Numeric(5, 2), nullable=False, server_default="21"),
        sa.Column("iva", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("factusol_serie", sa.Integer(), nullable=True),
        sa.Column("factusol_codpre", sa.Integer(), nullable=True),
        sa.Column("factusol_ejercicio", sa.String(length=4), nullable=True),
        sa.Column("convertida_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enviada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("caducada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["plantilla_id"], ["ofertas_plantillas.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("numero", name="uq_ofertas_numero"),
    )
    op.create_index("ix_ofertas_contact_id", "ofertas", ["contact_id"])
    op.create_index("ix_ofertas_company_id", "ofertas", ["company_id"])
    op.create_index("ix_ofertas_estado", "ofertas", ["estado"])
    op.create_table(
        "ofertas_lineas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("oferta_id", sa.String(length=36), nullable=False),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("codart", sa.String(length=64), nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("precio", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("descuento_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("iva_pct", sa.Numeric(5, 2), nullable=False, server_default="21"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["oferta_id"], ["ofertas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ofertas_lineas_oferta_id", "ofertas_lineas", ["oferta_id"])
    op.create_table(
        "ofertas_envios",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("oferta_id", sa.String(length=36), nullable=False),
        sa.Column("enviado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usuario_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fecha_oferta", sa.Date(), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("idioma", sa.String(length=5), nullable=True),
        sa.Column("para_json", sa.Text(), nullable=True),
        sa.Column("cc_json", sa.Text(), nullable=True),
        sa.Column("asunto", sa.String(length=500), nullable=True),
        sa.Column("from_alias", sa.String(length=255), nullable=True),
        sa.Column("adjunto", sa.String(length=255), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["oferta_id"], ["ofertas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ofertas_envios_oferta_id", "ofertas_envios", ["oferta_id"])
    op.create_table(
        "ofertas_contador",
        sa.Column("anio", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("ultimo", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("anio"),
        sa.UniqueConstraint("anio", name="uq_ofertas_contador_anio"),
    )


def downgrade() -> None:
    op.drop_table("ofertas_contador")
    op.drop_index("ix_ofertas_envios_oferta_id", table_name="ofertas_envios")
    op.drop_table("ofertas_envios")
    op.drop_index("ix_ofertas_lineas_oferta_id", table_name="ofertas_lineas")
    op.drop_table("ofertas_lineas")
    op.drop_index("ix_ofertas_estado", table_name="ofertas")
    op.drop_index("ix_ofertas_company_id", table_name="ofertas")
    op.drop_index("ix_ofertas_contact_id", table_name="ofertas")
    op.drop_table("ofertas")
    op.drop_index(
        "ix_ofertas_plantillas_lineas_plantilla_id", table_name="ofertas_plantillas_lineas",
    )
    op.drop_table("ofertas_plantillas_lineas")
    op.drop_table("ofertas_plantillas")
