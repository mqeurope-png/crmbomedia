"""El asistente de respuesta «Ayúdame a contestar» y el registro de la IA.

`ia_llamadas`: cada llamada al modelo (clasificador de leads y asistente) con
tokens, coste y referencia. `ia_borradores`: cada borrador del asistente, con
contexto, enlaces, idioma, coste y qué pasó con él (enviado tal cual, editado,
descartado, regenerado). Nada aquí envía correo: lo envía una persona.

Revision ID: 20261011_0137
Revises: 20261011_0136
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0137"
down_revision: str | None = "20261011_0136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ia_llamadas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposito", sa.String(length=40), nullable=False),
        sa.Column("modelo", sa.String(length=80), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False),
        sa.Column("tokens_salida", sa.Integer(), nullable=False),
        sa.Column("coste_usd", sa.Float(), nullable=False),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("usuario_id", sa.String(length=36), nullable=True),
        sa.Column("referencia_tipo", sa.String(length=30), nullable=True),
        sa.Column("referencia_id", sa.String(length=64), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ia_llamadas_proposito_fecha", "ia_llamadas", ["proposito", "created_at"])
    op.create_index("ix_ia_llamadas_usuario_id", "ia_llamadas", ["usuario_id"])
    op.create_table(
        "ia_borradores",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("usuario_id", sa.String(length=36), nullable=False),
        sa.Column("llamada_id", sa.String(length=36), nullable=True),
        sa.Column("idioma", sa.String(length=5), nullable=True),
        sa.Column("modelo", sa.String(length=80), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False),
        sa.Column("tokens_salida", sa.Integer(), nullable=False),
        sa.Column("coste_usd", sa.Float(), nullable=False),
        sa.Column("destinatario", sa.String(length=255), nullable=True),
        sa.Column("remitente", sa.String(length=255), nullable=True),
        sa.Column("asunto", sa.String(length=500), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("pendiente_json", sa.Text(), nullable=True),
        sa.Column("enlaces_json", sa.Text(), nullable=True),
        sa.Column("contexto_json", sa.Text(), nullable=True),
        sa.Column("alcance", sa.String(length=10), nullable=False),
        sa.Column("mensajes_hilo", sa.Integer(), nullable=False),
        sa.Column("mensajes_historico", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("email_message_id", sa.String(length=36), nullable=True),
        sa.Column("resuelto_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ia_borradores_thread", "ia_borradores", ["thread_id", "created_at"])
    op.create_index("ix_ia_borradores_usuario", "ia_borradores", ["usuario_id", "created_at"])
    op.create_index("ix_ia_borradores_contact_id", "ia_borradores", ["contact_id"])


def downgrade() -> None:
    op.drop_index("ix_ia_borradores_contact_id", table_name="ia_borradores")
    op.drop_index("ix_ia_borradores_usuario", table_name="ia_borradores")
    op.drop_index("ix_ia_borradores_thread", table_name="ia_borradores")
    op.drop_table("ia_borradores")
    op.drop_index("ix_ia_llamadas_usuario_id", table_name="ia_llamadas")
    op.drop_index("ix_ia_llamadas_proposito_fecha", table_name="ia_llamadas")
    op.drop_table("ia_llamadas")
