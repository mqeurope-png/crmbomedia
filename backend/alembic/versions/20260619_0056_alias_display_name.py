"""user_email_alias_prefs: add display name columns.

Revision ID: 20260619_0056
Revises: 20260618_0055
Create Date: 2026-06-19 09:00:00

PR-DisplayName-Remitente. Bart: "Editar el nombre de remite que se
verá al recibir el correo, o que se vea el que viene configurado de
gmail." Añadimos 2 columnas a `user_email_alias_prefs`:

- `gmail_display_name`: cache del `displayName` de Gmail Send-As.
  Se rellena en cada GET /api/emails/aliases (refresh-on-read).
- `display_name_override`: override manual del user desde el card
  /account. NULL → usa `gmail_display_name` como fallback.

Backfill: NO se hace en la migración (no tenemos acceso a Gmail
API desde alembic). El primer GET de aliases tras el deploy
rellenará `gmail_display_name` para cada user.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260619_0056"
down_revision: str | None = "20260618_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_email_alias_prefs",
        sa.Column("gmail_display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_email_alias_prefs",
        sa.Column(
            "display_name_override", sa.String(length=255), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("user_email_alias_prefs", "display_name_override")
    op.drop_column("user_email_alias_prefs", "gmail_display_name")
