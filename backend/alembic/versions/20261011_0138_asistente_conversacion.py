"""El asistente de respuesta como conversación, y la caché de prompts.

`ia_borradores`: el hilo pasa a ser opcional (un correo nuevo no tiene), y
cada borrador lleva su conversación (`conversacion_id`, `turno`, `padre_id`),
la instrucción de la persona, el aviso que la IA le dirige a ella (nunca al
cuerpo), el bloque de contexto ya montado que las correcciones reutilizan y
los tokens leídos y escritos en la caché de prompts. `ia_llamadas`: los
mismos tokens de caché. Sin datos que migrar: columnas nuevas a cero o vacías.

Revision ID: 20261011_0138
Revises: 20261011_0137
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0138"
down_revision: str | None = "20261011_0137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ia_llamadas") as batch:
        batch.add_column(sa.Column("tokens_cache_lectura", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("tokens_cache_escritura", sa.Integer(), nullable=False,
                                   server_default="0"))
    with op.batch_alter_table("ia_borradores") as batch:
        batch.alter_column("thread_id", existing_type=sa.String(length=36), nullable=True)
        batch.add_column(sa.Column("conversacion_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("turno", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("padre_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("instruccion", sa.Text(), nullable=True))
        batch.add_column(sa.Column("aviso", sa.Text(), nullable=True))
        batch.add_column(sa.Column("contexto_texto", sa.Text(), nullable=True))
        batch.add_column(sa.Column("tokens_cache_lectura", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("tokens_cache_escritura", sa.Integer(), nullable=False,
                                   server_default="0"))
    op.create_index("ix_ia_borradores_conversacion_id", "ia_borradores", ["conversacion_id"])
    # Los borradores anteriores a la conversación son cada uno su propia
    # conversación de un turno.
    op.execute("UPDATE ia_borradores SET conversacion_id = id WHERE conversacion_id IS NULL")


def downgrade() -> None:
    op.drop_index("ix_ia_borradores_conversacion_id", table_name="ia_borradores")
    # Un correo nuevo no tiene hilo; para volver a la columna obligatoria se
    # deja vacío, no se borra nada.
    op.execute("UPDATE ia_borradores SET thread_id = '' WHERE thread_id IS NULL")
    with op.batch_alter_table("ia_borradores") as batch:
        for columna in ("tokens_cache_escritura", "tokens_cache_lectura", "contexto_texto",
                        "aviso", "instruccion", "padre_id", "turno", "conversacion_id"):
            batch.drop_column(columna)
        batch.alter_column("thread_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("ia_llamadas") as batch:
        batch.drop_column("tokens_cache_escritura")
        batch.drop_column("tokens_cache_lectura")
