"""drop composer_* tables

Revision ID: 20260612_0035
Revises: 20260612_0034
Create Date: 2026-06-12 15:00:00

Sprint Email v2.2b — third cleanup commit. The Sprint Composer
porting effort is being abandoned; composer.bomedia.net stays as
the source of truth and the CRM consumes it via a read-only proxy
(landing in 2.2b-editor). The 13 tables created by migration
0033_composer_initial are no longer referenced by any code path
in the application, so we drop them to keep the schema clean.

DESTRUCTIVE — there is no downgrade. The seed data the Composer
shipped with (43 products, ~300 reusable text blocks) lives in
Supabase and remains the source of truth for the standalone app.

Drop order respects FK dependencies: leaf tables first, then the
shared catalog tables (`composer_products` → `composer_brands`).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260612_0035"
down_revision: str | None = "20260612_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables ordered so children are dropped before their parents.
_TABLES = (
    "composer_activity_log",
    "composer_user_ai_styles",
    "composer_user_hidden_items",
    "composer_settings",
    "composer_assets",
    "composer_drafts",
    "composer_template_revisions",
    "composer_templates",
    "composer_standalone_blocks",
    "composer_composed_blocks",
    "composer_prewritten_texts",
    "composer_products",
    "composer_brands",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing = set(inspector.get_table_names())
    for table in _TABLES:
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    # No downgrade — the Composer port is abandoned. Restoring the
    # tables wouldn't restore the seed data or the application code
    # that used them.
    raise RuntimeError(
        "Migration 0035 is intentionally one-way: the composer_* "
        "tables were dropped as part of Sprint Email v2.2b cleanup."
    )


# Local helper so the upgrade body stays readable.
def sa_inspect(bind):  # noqa: ANN001, ANN201
    from sqlalchemy import inspect  # noqa: PLC0415

    return inspect(bind)
