"""Base de conocimiento para la respuesta a leads (Fase 1).

Cuatro tablas: `kb_rutas` (rutas de soporte web × idioma y páginas de
producto por interés), `kb_flux_categorias` (las categorías de la KB de FLUX
por modelo), `kb_flux_articulos` (el índice —título y enlace, nunca el
cuerpo— de la KB de FLUX por idioma) y `kb_flux_sincronizaciones` (el
histórico de pasadas). Siembra las 29 rutas de soporte comprobadas el
11/10/2026, las 7 páginas de producto de mqeurope.com y las 13 categorías de
FLUX. El índice de artículos se llena con la primera sincronización.

Revision ID: 20261011_0136
Revises: 20261011_0135
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0136"
down_revision: str | None = "20261011_0135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_rutas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("sitio", sa.String(length=40), nullable=False),
        sa.Column("idioma", sa.String(length=5), nullable=False),
        sa.Column("interes", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("comprobado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comprobado_http", sa.Integer(), nullable=True),
        sa.Column("comprobado_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tipo", "sitio", "idioma", "interes", name="uq_kb_rutas_clave"),
    )
    op.create_table(
        "kb_flux_categorias",
        sa.Column("categoria_id", sa.String(length=30), nullable=False),
        sa.Column("modelo", sa.String(length=80), nullable=False),
        sa.Column("palabras_json", sa.Text(), nullable=True),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("categoria_id"),
    )
    op.create_table(
        "kb_flux_articulos",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("zendesk_id", sa.BigInteger(), nullable=False),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.Column("idioma", sa.String(length=5), nullable=False),
        sa.Column("titulo", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("section_id", sa.BigInteger(), nullable=True),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("etiquetas_json", sa.Text(), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outdated", sa.Boolean(), nullable=False),
        sa.Column("outdated_locales_json", sa.Text(), nullable=True),
        sa.Column("source_locale", sa.String(length=10), nullable=True),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_flux_articulos_locale_categoria", "kb_flux_articulos",
                    ["locale", "category_id"])
    op.create_index("ix_kb_flux_articulos_idioma", "kb_flux_articulos", ["idioma"])
    op.create_table(
        "kb_flux_sincronizaciones",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("origen", sa.String(length=20), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estado", sa.String(length=10), nullable=False),
        sa.Column("articulos", sa.Integer(), nullable=False),
        sa.Column("nuevos", sa.Integer(), nullable=False),
        sa.Column("actualizados", sa.Integer(), nullable=False),
        sa.Column("idos", sa.Integer(), nullable=False),
        sa.Column("locales_json", sa.Text(), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    from app.services.conocimiento.flux import CATEGORIAS_DE_PARTIDA  # noqa: PLC0415
    from app.services.conocimiento.rutas import RUTAS_DE_PARTIDA  # noqa: PLC0415

    conn = op.get_bind()
    ahora = datetime.now(UTC)
    for ruta in RUTAS_DE_PARTIDA:
        conn.execute(
            sa.text(
                "INSERT INTO kb_rutas (id, tipo, sitio, idioma, interes, url, nota, activo, "
                "posicion, created_at, updated_at) VALUES (:id, :tipo, :sitio, :idioma, "
                ":interes, :url, :nota, 1, :posicion, :t, :t)"
            ),
            {
                "id": str(uuid4()), "tipo": ruta.tipo, "sitio": ruta.sitio,
                "idioma": ruta.idioma, "interes": ruta.interes, "url": ruta.url,
                "nota": ruta.nota, "posicion": ruta.posicion, "t": ahora,
            },
        )
    for cat in CATEGORIAS_DE_PARTIDA:
        conn.execute(
            sa.text(
                "INSERT INTO kb_flux_categorias (categoria_id, modelo, palabras_json, posicion, "
                "activo, created_at, updated_at) VALUES (:id, :modelo, :palabras, :posicion, 1, "
                ":t, :t)"
            ),
            {
                "id": cat.categoria_id, "modelo": cat.modelo,
                "palabras": json.dumps(list(cat.palabras), ensure_ascii=False),
                "posicion": cat.posicion, "t": ahora,
            },
        )


def downgrade() -> None:
    op.drop_table("kb_flux_sincronizaciones")
    op.drop_index("ix_kb_flux_articulos_idioma", table_name="kb_flux_articulos")
    op.drop_index("ix_kb_flux_articulos_locale_categoria", table_name="kb_flux_articulos")
    op.drop_table("kb_flux_articulos")
    op.drop_table("kb_flux_categorias")
    op.drop_table("kb_rutas")
