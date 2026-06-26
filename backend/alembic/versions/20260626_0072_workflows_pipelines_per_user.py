"""PR-Workflows-Pipelines-Per-User — owner_user_id nullable + user_template_folder_prefs.

Revision ID: 20260626_0072
Revises: 20260626_0071
Create Date: 2026-06-26 13:00:00

Tres cambios coordinados para que cada user pueda crear sus propios
workflows y pipelines sin mezclarse con los del equipo, y para que
cada user pueda fijar su carpeta de plantillas predeterminada al
abrir el modal "Nuevo email":

1. `workflows`: nueva columna `owner_user_id VARCHAR(36) NULL` con FK
   a `users(id) ON DELETE SET NULL`. Index para los filtros típicos
   ("mis workflows" + "los del equipo"). Backfill: todos los rows
   existentes quedan con `owner_user_id = NULL` (globales del
   equipo) para no romper flujos en producción.

2. `pipelines`: la columna `owner_user_id` ya existía pero era NOT
   NULL (legacy: marcaba al creador, no implicaba privacidad).
   La alteramos a NULL para que NULL signifique "global del
   equipo". Backfill: SET owner_user_id = NULL en todas las rows
   existentes — los pipelines en producción pasan a ser globales,
   admin puede reasignar después. (El flag legacy `is_shared` se
   queda en la tabla pero no se usa para el filtro nuevo.)

3. Nueva tabla `user_template_folder_prefs(user_id UNIQUE, folder_id)`
   para el mini-fix del selector de plantillas en el modal Nuevo
   email. Reusa el patrón del PR #249 sin tocar
   `user_default_view_prefs` (mantiene las dos tablas en sus
   responsabilidades para no añadir entity_type a una preferencia
   que solo aplica a un dominio).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0072"
down_revision: str | None = "20260626_0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- 1. workflows.owner_user_id ----
    op.add_column(
        "workflows",
        sa.Column(
            "owner_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workflows_owner_user_id",
        "workflows",
        ["owner_user_id"],
    )
    # No backfill explícito — add_column con nullable=True deja
    # owner_user_id = NULL para todas las rows existentes. Eso es
    # exactamente lo que pide el spec (workflows existentes pasan a
    # globales).

    # ---- 2. pipelines.owner_user_id → nullable + reset ----
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            "pipelines",
            "owner_user_id",
            existing_type=sa.String(36),
            nullable=True,
        )
    else:
        # SQLite no soporta ALTER COLUMN nullable de forma portable —
        # batch_alter_table emula con copy-and-replace.
        with op.batch_alter_table("pipelines") as batch:
            batch.alter_column(
                "owner_user_id",
                existing_type=sa.String(36),
                nullable=True,
            )
    op.execute(sa.text("UPDATE pipelines SET owner_user_id = NULL"))

    # ---- 3. user_template_folder_prefs ----
    op.create_table(
        "user_template_folder_prefs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            sa.String(36),
            sa.ForeignKey(
                "email_template_folders.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", name="uq_user_template_folder_prefs_user"
        ),
    )
    op.create_index(
        "ix_user_template_folder_prefs_user_id",
        "user_template_folder_prefs",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_template_folder_prefs_user_id",
        table_name="user_template_folder_prefs",
    )
    op.drop_table("user_template_folder_prefs")

    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            "pipelines",
            "owner_user_id",
            existing_type=sa.String(36),
            nullable=False,
        )
    else:
        with op.batch_alter_table("pipelines") as batch:
            batch.alter_column(
                "owner_user_id",
                existing_type=sa.String(36),
                nullable=False,
            )

    op.drop_index("ix_workflows_owner_user_id", table_name="workflows")
    op.drop_column("workflows", "owner_user_id")
