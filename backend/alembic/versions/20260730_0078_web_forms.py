"""Web forms: web_forms, web_form_fields, form_submissions.

Sprint Web-Forms (PR-A). Generador de formularios web propios para
capturar leads directamente en BoHub saltándose AgileCRM.

Revision ID: 20260730_0078
Revises: 20260729_0077
Create Date: 2026-07-30 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0078"
down_revision: str | None = "20260729_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_forms",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(64), nullable=True),
        sa.Column("language", sa.String(8), nullable=False, server_default="es"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "submit_success_mode", sa.String(16), nullable=False,
            server_default="modal",
        ),
        sa.Column("submit_success_message", sa.Text(), nullable=True),
        sa.Column("submit_redirect_url", sa.String(512), nullable=True),
        sa.Column(
            "send_confirmation_email", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("confirmation_email_template_id", sa.String(36), nullable=True),
        sa.Column(
            "assignment_mode", sa.String(16), nullable=False,
            server_default="rules",
        ),
        sa.Column("fixed_owner_user_id", sa.String(36), nullable=True),
        sa.Column(
            "notify_owner_on_new", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "recaptcha_enabled", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["fixed_owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )
    op.create_index("idx_forms_slug", "web_forms", ["slug"])
    op.create_index("idx_forms_brand", "web_forms", ["brand"])

    op.create_table(
        "web_form_fields",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("form_id", sa.String(36), nullable=False),
        sa.Column("field_key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("field_type", sa.String(16), nullable=False),
        sa.Column("placeholder", sa.String(255), nullable=True),
        sa.Column("help_text", sa.String(512), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_value", sa.String(512), nullable=True),
        sa.Column("options_json", sa.Text(), nullable=True),
        sa.Column("validation_pattern", sa.String(255), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("maps_to_contact_field", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["form_id"], ["web_forms.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("idx_form_fields_form", "web_form_fields", ["form_id", "position"])

    op.create_table(
        "form_submissions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("form_id", sa.String(36), nullable=False),
        sa.Column("contact_id", sa.String(36), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=False),
        sa.Column("is_spam", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spam_reason", sa.String(128), nullable=True),
        sa.Column("recaptcha_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("referrer", sa.String(512), nullable=True),
        sa.Column("landing_page", sa.String(512), nullable=True),
        sa.Column("utm_source", sa.String(128), nullable=True),
        sa.Column("utm_medium", sa.String(128), nullable=True),
        sa.Column("utm_campaign", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["form_id"], ["web_forms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_submissions_form_created", "form_submissions", ["form_id", "created_at"]
    )
    op.create_index("idx_submissions_contact", "form_submissions", ["contact_id"])


def downgrade() -> None:
    op.drop_table("form_submissions")
    op.drop_table("web_form_fields")
    op.drop_table("web_forms")
