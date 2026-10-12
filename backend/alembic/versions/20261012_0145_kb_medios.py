"""Base de conocimiento, Fase 3: las imágenes y los vídeos de los correos compuestos.

Dos tablas: `kb_imagenes` (el índice de la biblioteca de medios de las webs:
solo las imágenes anchas y apaisadas que valen para un hero o una galería de
muestras, con sus tamaños de las mismas proporciones, favoritas y ocultas a
mano) y `kb_videos` (los vídeos de YouTube de los canales del grupo, los
enlazados en las webs y los pegados a mano: idioma, tema, marca, productos,
miniatura y cuál se usó, duración, visible). Las llena la primera
sincronización; sin datos que migrar.

Revision ID: 20261012_0145
Revises: 20261012_0144
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0145"
down_revision: str | None = "20261012_0144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_imagenes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sitio", sa.String(length=40), nullable=False),
        sa.Column("media_id", sa.BigInteger(), nullable=False),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("tamanos_json", sa.Text(), nullable=True),
        sa.Column("ancho", sa.Integer(), nullable=False),
        sa.Column("alto", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=40), nullable=True),
        sa.Column("pagina", sa.String(length=500), nullable=True),
        sa.Column("padre_id", sa.BigInteger(), nullable=True),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("favorita", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("oculta", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sitio", "media_id", name="uq_kb_imagenes_clave"),
    )
    op.create_index("ix_kb_imagenes_sitio", "kb_imagenes", ["sitio"])
    op.create_table(
        "kb_videos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plataforma", sa.String(length=10), nullable=False),
        sa.Column("video_id", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("idioma", sa.String(length=5), nullable=True),
        sa.Column("idioma_fuente", sa.String(length=12), nullable=True),
        sa.Column("tema", sa.String(length=80), nullable=True),
        sa.Column("marca", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("productos_json", sa.Text(), nullable=True),
        sa.Column("manual_json", sa.Text(), nullable=True),
        sa.Column("miniatura", sa.String(length=500), nullable=True),
        sa.Column("miniatura_calidad", sa.String(length=20), nullable=True),
        sa.Column("miniatura_comprobada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duracion", sa.Integer(), nullable=True),
        sa.Column("corto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("canal", sa.String(length=120), nullable=True),
        sa.Column("canal_id", sa.String(length=40), nullable=True),
        sa.Column("origen", sa.String(length=10), nullable=False, server_default="canal"),
        sa.Column("webs_json", sa.Text(), nullable=True),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("datos_json", sa.Text(), nullable=True),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plataforma", "video_id", name="uq_kb_videos_clave"),
    )
    op.create_index("ix_kb_videos_tema", "kb_videos", ["tema"])
    op.create_index("ix_kb_videos_idioma", "kb_videos", ["idioma"])


def downgrade() -> None:
    op.drop_index("ix_kb_videos_idioma", table_name="kb_videos")
    op.drop_index("ix_kb_videos_tema", table_name="kb_videos")
    op.drop_table("kb_videos")
    op.drop_index("ix_kb_imagenes_sitio", table_name="kb_imagenes")
    op.drop_table("kb_imagenes")
