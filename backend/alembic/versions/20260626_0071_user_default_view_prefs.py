"""PR-Backlog-3-5-7 (item 5) — user_default_view_prefs.

Revision ID: 20260626_0071
Revises: 20260626_0070
Create Date: 2026-06-26 12:30:00

Nueva tabla `user_default_view_prefs(user_id, entity_type, view_id)`
con UNIQUE (user_id, entity_type). Permite que CADA user marque su
propia vista predeterminada por entidad, sin importar quién sea el
owner de la vista. Antes el flag global `contact_views.is_default`
solo dejaba al owner marcar default; users que veían la vista
compartida no podían tener su propia preferencia.

Migración de datos: para cada `contact_views.is_default = True` se
inserta una fila `(owner_user_id, entity_type, view_id)` en la nueva
tabla. Así nadie pierde su default existente.

El flag `contact_views.is_default` se mantiene en el schema (lo leen
endpoints legacy). El frontend nuevo lee `is_default_for_me` que se
calcula con esta tabla.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0071"
down_revision: str | None = "20260626_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_default_view_prefs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column(
            "view_id",
            sa.String(36),
            sa.ForeignKey("contact_views.id", ondelete="CASCADE"),
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
            "user_id",
            "entity_type",
            name="uq_user_default_view_prefs_user_entity",
        ),
    )
    op.create_index(
        "ix_user_default_view_prefs_user_id",
        "user_default_view_prefs",
        ["user_id"],
    )
    op.create_index(
        "ix_user_default_view_prefs_view_id",
        "user_default_view_prefs",
        ["view_id"],
    )

    # Backfill: por cada contact_views.is_default=True copiamos al
    # owner_user_id en la nueva tabla — preserva los defaults
    # existentes. Usamos `uuid()` de Python a través de un
    # connection.execute en vez de raw SQL para que sea portable
    # entre MySQL/SQLite.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, owner_user_id, entity_type "
            "FROM contact_views WHERE is_default = 1"
        )
    ).fetchall()
    if rows:
        import uuid as _uuid  # noqa: PLC0415

        bind.execute(
            sa.text(
                "INSERT INTO user_default_view_prefs "
                "(id, user_id, entity_type, view_id, created_at, updated_at) "
                "VALUES (:id, :user_id, :entity_type, :view_id, "
                "       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            [
                {
                    "id": str(_uuid.uuid4()),
                    "user_id": row.owner_user_id,
                    "entity_type": row.entity_type or "contact",
                    "view_id": row.id,
                }
                for row in rows
            ],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_user_default_view_prefs_view_id",
        table_name="user_default_view_prefs",
    )
    op.drop_index(
        "ix_user_default_view_prefs_user_id",
        table_name="user_default_view_prefs",
    )
    op.drop_table("user_default_view_prefs")
