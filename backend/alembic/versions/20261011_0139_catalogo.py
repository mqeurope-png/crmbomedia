"""Base de conocimiento, Fase 2: el catálogo y los precios verificados.

Cuatro tablas: `kb_catalogo` (una fila por producto × idioma: máquinas del
compositor de correos de Bomedia y consumibles/repuestos de las tiendas
WooCommerce, con el precio TAL CUAL está escrito, su moneda, enlace, idioma,
web, fuente y fecha de actualización del origen), `kb_catalogo_marcas` (las
marcas del compositor y su idioma por defecto), `kb_catalogo_sincronizaciones`
(el histórico de pasadas por fuente) y `kb_interes_productos` (interés del
clasificador → máquinas, por marca y patrón del nombre). Siembra la propuesta
de interés → máquinas del 11/10/2026 (15 filas, a revisar). El catálogo lo
llena la primera sincronización.

Revision ID: 20261011_0139
Revises: 20261011_0138
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0139"
down_revision: str | None = "20261011_0138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_catalogo",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("fuente", sa.String(length=12), nullable=False),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("marca", sa.String(length=80), nullable=False),
        sa.Column("origen_id", sa.String(length=80), nullable=False),
        sa.Column("nombre", sa.String(length=255), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("caracteristicas_json", sa.Text(), nullable=True),
        sa.Column("categoria", sa.String(length=255), nullable=True),
        sa.Column("imagen", sa.String(length=500), nullable=True),
        sa.Column("precio_texto", sa.String(length=80), nullable=True),
        sa.Column("precio_num", sa.Float(), nullable=True),
        sa.Column("moneda", sa.String(length=3), nullable=True),
        sa.Column("enlace", sa.String(length=500), nullable=True),
        sa.Column("idioma", sa.String(length=5), nullable=False),
        sa.Column("sitio", sa.String(length=40), nullable=False),
        sa.Column("actualizado_origen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("datos_json", sa.Text(), nullable=True),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fuente", "origen_id", "idioma", name="uq_kb_catalogo_clave"),
    )
    op.create_index("ix_kb_catalogo_tipo_marca", "kb_catalogo", ["tipo", "marca"])
    op.create_index("ix_kb_catalogo_idioma", "kb_catalogo", ["idioma"])
    op.create_index("ix_kb_catalogo_sitio", "kb_catalogo", ["sitio"])
    op.create_table(
        "kb_catalogo_marcas",
        sa.Column("nombre", sa.String(length=80), nullable=False),
        sa.Column("logo", sa.String(length=500), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("urls_json", sa.Text(), nullable=True),
        sa.Column("idioma_defecto", sa.String(length=5), nullable=False),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("nombre"),
    )
    op.create_table(
        "kb_catalogo_sincronizaciones",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("fuente", sa.String(length=12), nullable=False),
        sa.Column("origen", sa.String(length=20), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estado", sa.String(length=10), nullable=False),
        sa.Column("productos", sa.Integer(), nullable=False),
        sa.Column("nuevos", sa.Integer(), nullable=False),
        sa.Column("actualizados", sa.Integer(), nullable=False),
        sa.Column("idos", sa.Integer(), nullable=False),
        sa.Column("origen_actualizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumen_json", sa.Text(), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_catalogo_sinc_fuente_inicio", "kb_catalogo_sincronizaciones",
                    ["fuente", "inicio"])
    op.create_table(
        "kb_interes_productos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("interes", sa.String(length=40), nullable=False),
        sa.Column("marca", sa.String(length=80), nullable=False),
        sa.Column("patron", sa.String(length=80), nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interes", "marca", "patron", name="uq_kb_interes_productos_clave"),
    )

    from app.services.conocimiento.catalogo import MAPA_DE_PARTIDA  # noqa: PLC0415

    conn = op.get_bind()
    ahora = datetime.now(UTC)
    for posicion, (interes, marca, patron, nota) in enumerate(MAPA_DE_PARTIDA):
        conn.execute(
            sa.text(
                "INSERT INTO kb_interes_productos (id, interes, marca, patron, nota, posicion, "
                "activo, created_at, updated_at) VALUES (:id, :interes, :marca, :patron, :nota, "
                ":posicion, 1, :t, :t)"
            ),
            {
                "id": str(uuid4()), "interes": interes, "marca": marca, "patron": patron,
                "nota": nota, "posicion": posicion, "t": ahora,
            },
        )


def downgrade() -> None:
    op.drop_table("kb_interes_productos")
    op.drop_index("ix_kb_catalogo_sinc_fuente_inicio", table_name="kb_catalogo_sincronizaciones")
    op.drop_table("kb_catalogo_sincronizaciones")
    op.drop_table("kb_catalogo_marcas")
    op.drop_index("ix_kb_catalogo_sitio", table_name="kb_catalogo")
    op.drop_index("ix_kb_catalogo_idioma", table_name="kb_catalogo")
    op.drop_index("ix_kb_catalogo_tipo_marca", table_name="kb_catalogo")
    op.drop_table("kb_catalogo")
