"""composer initial schema

Revision ID: 20260612_0033
Revises: 20260612_0031
Create Date: 2026-06-12 13:00:00

Sprint Composer Fase 1. Adds the eleven tables that back the
Bomedia Email Composer integration:

- `composer_brands` + `composer_products` — catalog hierarchy.
- `composer_prewritten_texts` — reusable text blocks.
- `composer_composed_blocks` — predefined combinations
  (text + N products + optional hero).
- `composer_standalone_blocks` — individual hero/CTA blocks.
- `composer_templates` — saved user-facing templates.
- `composer_template_revisions` — FIFO 20-snapshot history per
  template (managed at the service layer, not DB-enforced).
- `composer_drafts` — per-user canvas autosave (one row each).
- `composer_assets` — uploaded images, sha256-deduped.
- `composer_settings` — singleton config row (OpenAI key,
  global AI styles).
- `composer_user_hidden_items` — per-user hide-from-catalog
  preferences.
- `composer_user_ai_styles` — per-user / per-lang AI tone.
- `composer_activity_log` — append-only audit for the admin
  "Actividad" tab.

JSON columns land as `Text` so SQLite (CI) and MySQL (prod) share
the same shape — the application layer serialises / parses with
`json.dumps` / `json.loads`. Enums likewise land as String with
no DB-side CHECK; the model layer is the gatekeeper.

MySQL note: MySQL 8 rejects literal `DEFAULT` on `TEXT`/`BLOB`
columns. Every NOT NULL `Text` here therefore has NO `server_default`;
the application layer (ORM `default=` + the seed script + every
router call site) is responsible for supplying the empty `{}` / `[]`.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0033"
down_revision: str | None = "20260612_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "composer_brands",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "type", sa.String(length=16), nullable=False, server_default="brand"
        ),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("logo", sa.Text(), nullable=True),
        sa.Column("logo_text", sa.String(length=60), nullable=True),
        sa.Column(
            "color", sa.String(length=20), nullable=False, server_default="#000"
        ),
        sa.Column("divider", sa.String(length=20), nullable=True),
        sa.Column("logo_height", sa.String(length=8), nullable=True),
        sa.Column("logo_max_width", sa.String(length=8), nullable=True),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("i18n_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "composer_products",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("brand_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("badge", sa.String(length=60), nullable=True),
        sa.Column("badge_bg", sa.String(length=20), nullable=True),
        sa.Column("badge_color", sa.String(length=20), nullable=True),
        sa.Column("img", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("area", sa.String(length=60), nullable=True),
        sa.Column("alt", sa.String(length=60), nullable=True),
        sa.Column("feat1", sa.String(length=200), nullable=True),
        sa.Column("feat2", sa.String(length=200), nullable=True),
        sa.Column("price", sa.String(length=80), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("accent", sa.String(length=20), nullable=True),
        sa.Column("gradient", sa.Text(), nullable=True),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("tags", sa.Text(), nullable=False),
        sa.Column("i18n_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["composer_brands.id"]),
    )
    op.create_index(
        "ix_composer_products_brand", "composer_products", ["brand_id"]
    )
    op.create_index(
        "ix_composer_products_visible", "composer_products", ["visible"]
    )

    op.create_table(
        "composer_prewritten_texts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=10), nullable=True),
        sa.Column("brand_id", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("i18n_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["composer_brands.id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "composer_composed_blocks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_range", sa.String(length=120), nullable=True),
        sa.Column("color_tag", sa.String(length=40), nullable=True),
        sa.Column("intro_text", sa.Text(), nullable=True),
        sa.Column("brand_strip", sa.String(length=64), nullable=True),
        sa.Column("block_type", sa.String(length=40), nullable=False),
        sa.Column("products", sa.Text(), nullable=False),
        sa.Column(
            "include_hero",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "include_steps",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("i18n_json", sa.Text(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "composer_standalone_blocks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(length=10), nullable=True),
        sa.Column("icon_bg", sa.String(length=20), nullable=True),
        sa.Column("brand_id", sa.String(length=64), nullable=True),
        sa.Column("section", sa.String(length=60), nullable=True),
        sa.Column("block_type", sa.String(length=40), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("i18n_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["composer_brands.id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "composer_templates",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color_class", sa.String(length=40), nullable=True),
        sa.Column("brand_id", sa.String(length=64), nullable=True),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("compositor_blocks_json", sa.Text(), nullable=True),
        sa.Column(
            "visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "is_global",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["composer_brands.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_composer_templates_owner", "composer_templates", ["owner_user_id"]
    )
    op.create_index(
        "ix_composer_templates_visible", "composer_templates", ["visible"]
    )

    op.create_table(
        "composer_template_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"], ["composer_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_composer_template_revisions_template",
        "composer_template_revisions",
        ["template_id", "created_at"],
    )

    op.create_table(
        "composer_drafts",
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
    )

    op.create_table(
        "composer_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("public_url", sa.String(length=500), nullable=False),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="upload",
        ),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("sha256", name="uq_composer_assets_sha256"),
    )
    op.create_index(
        "ix_composer_assets_user", "composer_assets", ["user_id"]
    )
    op.create_index(
        "ix_composer_assets_source", "composer_assets", ["source"]
    )

    op.create_table(
        "composer_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("ai_styles_json", sa.Text(), nullable=False),
        sa.Column("agent_system_prompt", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "composer_user_hidden_items",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("collection", sa.String(length=40), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "collection", "item_id",
            name="pk_composer_user_hidden_items",
        ),
    )

    op.create_table(
        "composer_user_ai_styles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("lang", sa.String(length=5), nullable=False),
        sa.Column("style_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "lang", name="pk_composer_user_ai_styles"
        ),
    )

    op.create_table(
        "composer_activity_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_composer_activity_user_time",
        "composer_activity_log",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_composer_activity_action_time",
        "composer_activity_log",
        ["action", "created_at"],
    )


def downgrade() -> None:
    for table in (
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
    ):
        op.drop_table(table)
