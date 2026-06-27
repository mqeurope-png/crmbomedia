"""PR-Hotfix-OAuth-Banner — org_google_integration.refresh_token_expires_at.

Revision ID: 20260627_0075
Revises: 20260627_0074
Create Date: 2026-06-27 12:00:00

Bug 14. El banner "caduca pronto" confundía el access_token (1h, se
refresca solo) con el refresh_token (7 días, sí necesita reconexión). Se
añade una columna dedicada para la caducidad del refresh token. Para las
filas existentes se rellena con connected_at + 7 días (la regla de Google
para apps OAuth no verificadas) como mejor estimación.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0075"
down_revision: str | None = "20260627_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_google_integration",
        sa.Column(
            "refresh_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Backfill: las filas activas existentes se conectaron antes de tener
    # esta columna. Estimamos la caducidad del refresh como
    # connected_at + 7 días (regla apps no verificadas). El admin
    # reconectará pronto de todos modos y la fila se recalculará exacta.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "mysql":
        bind.execute(
            sa.text(
                "UPDATE org_google_integration "
                "SET refresh_token_expires_at = "
                "    DATE_ADD(connected_at, INTERVAL 7 DAY) "
                "WHERE status = 'active' AND refresh_token_expires_at IS NULL"
            )
        )
    else:
        # SQLite (tests) y otros: datetime(connected_at, '+7 days').
        bind.execute(
            sa.text(
                "UPDATE org_google_integration "
                "SET refresh_token_expires_at = "
                "    datetime(connected_at, '+7 days') "
                "WHERE status = 'active' AND refresh_token_expires_at IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_column("org_google_integration", "refresh_token_expires_at")
