"""integration_accounts: auth_identifier

Revision ID: 20260519_0011
Revises: 20260518_0010
Create Date: 2026-05-19 00:00:00

AgileCRM (and a handful of other providers) need both a *user* and an
*API key* to authenticate. The user is **not** secret — only the API
key is. Storing both in the encrypted column made the UI confusing
("where do I put my email?") so we add a sibling column,
`auth_identifier`, kept in plain text alongside `account_label` and
the rest of the operational metadata.

Nullable for every system; AgileCRM is the only one that requires it
today. The connector falls back to splitting the legacy `email:key`
format from the encrypted column for backwards compatibility.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260519_0011"
down_revision: str | None = "20260518_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("integration_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("auth_identifier", sa.String(length=255), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("integration_accounts") as batch_op:
        batch_op.drop_column("auth_identifier")
