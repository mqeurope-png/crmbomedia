"""brevo sync targets, memberships, webhook dedupe, template/campaign caches

Revision ID: 20260610_0021
Revises: 20260607_0020
Create Date: 2026-06-10 00:00:00

Sprint B+D (Brevo). One migration for the whole sprint:

- `brevo_sync_targets` — push rules (segment → Brevo list).
- `brevo_target_memberships` — who was pushed last run (delta base).
- `webhook_events_seen` — idempotency ledger for webhook deliveries.
- `brevo_templates_cache` / `brevo_campaigns_cache` — local mirrors
  so the /marketing UI renders without waiting on the Brevo API.
- Index on `activity_events(contact_id, event_type, occurred_at)` so
  the contact page's "Actividad email" section stays fast.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0021"
down_revision: str | None = "20260607_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brevo_sync_targets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("brevo_account_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "segment_id",
            sa.String(length=36),
            sa.ForeignKey("segments.id"),
            nullable=False,
        ),
        sa.Column("brevo_list_id", sa.String(length=64), nullable=True),
        sa.Column("sync_direction", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=False),
        sa.Column("last_run_stats_json", sa.Text(), nullable=True),
        sa.Column("auto_sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_brevo_sync_targets_brevo_account_id",
        "brevo_sync_targets",
        ["brevo_account_id"],
    )

    op.create_table(
        "brevo_target_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "target_id",
            sa.String(length=36),
            sa.ForeignKey("brevo_sync_targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "target_id", "contact_id", name="uq_brevo_membership_target_contact"
        ),
    )
    op.create_index(
        "ix_brevo_target_memberships_target_id",
        "brevo_target_memberships",
        ["target_id"],
    )
    op.create_index(
        "ix_brevo_target_memberships_contact_id",
        "brevo_target_memberships",
        ["contact_id"],
    )

    op.create_table(
        "webhook_events_seen",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("system", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("system", "event_key", name="uq_webhook_event_seen"),
    )
    op.create_index(
        "ix_webhook_events_seen_seen_at", "webhook_events_seen", ["seen_at"]
    )

    op.create_table(
        "brevo_templates_cache",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("brevo_account_id", sa.String(length=64), nullable=False),
        sa.Column("brevo_template_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("tag", sa.String(length=100), nullable=True),
        sa.Column("sender_name", sa.String(length=200), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("created_at_brevo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at_brevo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "brevo_account_id",
            "brevo_template_id",
            name="uq_brevo_template_account_template",
        ),
    )
    op.create_index(
        "ix_brevo_templates_cache_brevo_account_id",
        "brevo_templates_cache",
        ["brevo_account_id"],
    )

    op.create_table(
        "brevo_campaigns_cache",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("brevo_account_id", sa.String(length=64), nullable=False),
        sa.Column("brevo_campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("sender_name", sa.String(length=200), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("reply_to", sa.String(length=255), nullable=True),
        sa.Column("created_at_brevo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at_brevo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats_json", sa.Text(), nullable=True),
        sa.Column("recipient_list_ids_json", sa.Text(), nullable=True),
        sa.Column("template_id_used", sa.Integer(), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "brevo_account_id",
            "brevo_campaign_id",
            name="uq_brevo_campaign_account_campaign",
        ),
    )
    op.create_index(
        "ix_brevo_campaigns_cache_brevo_account_id",
        "brevo_campaigns_cache",
        ["brevo_account_id"],
    )

    # Composite index for the contact page's email-activity timeline.
    op.create_index(
        "ix_activity_events_contact_type_occurred",
        "activity_events",
        ["contact_id", "event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_activity_events_contact_type_occurred", table_name="activity_events"
    )
    op.drop_table("brevo_campaigns_cache")
    op.drop_table("brevo_templates_cache")
    op.drop_table("webhook_events_seen")
    op.drop_table("brevo_target_memberships")
    op.drop_table("brevo_sync_targets")
