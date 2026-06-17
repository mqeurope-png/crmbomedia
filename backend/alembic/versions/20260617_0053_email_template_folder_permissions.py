"""email_template_folders.visibility + email_template_folder_shares.

Revision ID: 20260617_0053
Revises: 20260617_0052
Create Date: 2026-06-17 17:32:00

Sprint Email v2.5 — C: visibilidad de carpetas con 3 modos:

- `private` (default): solo `owner_user_id`.
- `team`: cualquier user del CRM con sesión válida puede ver / editar.
- `shared`: lista explícita en `email_template_folder_shares` —
  read+write para los users dentro.

La col legacy `is_global` (Sprint Email v2.2) queda como sombra de
`visibility=team`. La migración hace backfill:

  is_global=True  → visibility="team"
  is_global=False → visibility="private"

Las plantillas heredan permisos de su carpeta — sin carpeta vuelven al
chequeo legacy (owner_user_id == user OR is_global), preservando la
visibilidad de los Gmail Templates importados (PR-167 / v2.4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260617_0053"
down_revision: str | None = "20260617_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_template_folders",
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="private",
        ),
    )
    # Backfill: las carpetas legacy is_global=True pasan a team. El
    # server_default cubre las private; un UPDATE explícito promociona
    # las globals.
    op.execute(
        "UPDATE email_template_folders SET visibility = 'team' "
        "WHERE is_global = 1 OR is_global = TRUE"
    )

    op.create_table(
        "email_template_folder_shares",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "folder_id",
            sa.String(length=36),
            sa.ForeignKey("email_template_folders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "folder_id",
            "user_id",
            name="uq_email_template_folder_share_folder_user",
        ),
    )
    op.create_index(
        "ix_email_template_folder_shares_folder_id",
        "email_template_folder_shares",
        ["folder_id"],
    )
    op.create_index(
        "ix_email_template_folder_shares_user_id",
        "email_template_folder_shares",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_template_folder_shares_user_id",
        table_name="email_template_folder_shares",
    )
    op.drop_index(
        "ix_email_template_folder_shares_folder_id",
        table_name="email_template_folder_shares",
    )
    op.drop_table("email_template_folder_shares")
    op.drop_column("email_template_folders", "visibility")
