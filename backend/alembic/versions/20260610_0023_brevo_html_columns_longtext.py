"""brevo templates + campaigns html_content columns → LONGTEXT

Revision ID: 20260610_0023
Revises: 20260610_0022
Create Date: 2026-06-10 12:30:00

Sprint Brevo follow-up. `brevo_templates_cache.html_content` was
declared as plain `Text` (TINYTEXT for our MySQL 8 default), capped
at 64KB. A real production template weighed 124KB and refreshing
the cache 500'd. Upgrades the column to `LONGTEXT` (4GB cap, in
practice unbounded).

The same trip-up was waiting for campaigns: until this PR the cache
never stored the campaign HTML, but commit 3 of this sprint adds
`brevo_campaigns_cache.html_content_cached` to make the detail page
lazy-load + persist the HTML. That column is added here so the
campaign-detail commit can land without its own migration.

SQLite treats `Text` as unbounded, so the alter is a noop there —
batch_alter_table recreates the table either way.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGTEXT

revision: str = "20260610_0023"
down_revision: str | None = "20260610_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    longtext = LONGTEXT() if bind.dialect.name == "mysql" else sa.Text()

    with op.batch_alter_table("brevo_templates_cache") as batch:
        batch.alter_column(
            "html_content",
            existing_type=sa.Text(),
            type_=longtext,
            existing_nullable=True,
        )

    op.add_column(
        "brevo_campaigns_cache",
        sa.Column("html_content_cached", longtext, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("brevo_campaigns_cache", "html_content_cached")
    with op.batch_alter_table("brevo_templates_cache") as batch:
        batch.alter_column(
            "html_content",
            existing_type=LONGTEXT(),
            type_=sa.Text(),
            existing_nullable=True,
        )
