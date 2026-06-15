"""revert contact_emails table + socials columns

Revision ID: 20260615_0044
Revises: 20260615_0043
Create Date: 2026-06-15 11:42:00

Sprint Empresas — sub-PR 3 follow-up. After landing v3, Bart
flagged that:

- The CRM hasn't ever used socials (Twitter / Facebook /
  Skype / …). Twitter + Facebook columns + the
  `social_profiles_json` bucket were dead weight on every
  serializer / mapper / UI render.
- Contacts only ever have one email in practice — the canonical
  `contacts.email` UNIQUE column is enough. Brevo's
  `EMAIL_SECUNDARIO` / `EMAIL2` should land in `custom_fields`
  as informational, NOT spawn a parallel `contact_emails` row
  the operator would have to keep in sync.

This migration:
- Drops `contact_emails` (table + indexes).
- Drops `contacts.twitter_url`, `contacts.facebook_url`,
  `contacts.social_profiles_json`.

`contact_phones` survives — multi-teléfono is real CRM territory
the operator uses every day.

Forward path for Brevo's secondary email attributes: the v3 fix
in this PR extends `CUSTOM_FIELDS_WHITELIST` so they appear in
the ficha's "Datos adicionales" section without materialising a
managed row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0044"
down_revision: str | None = "20260615_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MySQL refuses to drop the per-FK index while the foreign-key
    # constraint still references it (error 1553). Drop the FK
    # explicitly first (best-effort: the constraint name differs
    # between deployments — `batch_alter_table` lets us
    # tolerate the rename without a hard failure). After the FK
    # is gone, `drop_table` sweeps the indexes implicitly so the
    # two `drop_index` calls are no longer needed.
    with op.batch_alter_table("contact_emails") as batch_op:
        try:
            batch_op.drop_constraint("contact_emails_ibfk_1", type_="foreignkey")
        except Exception:  # noqa: BLE001
            pass
    op.drop_table("contact_emails")

    op.drop_column("contacts", "social_profiles_json")
    op.drop_column("contacts", "facebook_url")
    op.drop_column("contacts", "twitter_url")


def downgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("twitter_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("facebook_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("social_profiles_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "contact_emails",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_contact_emails_contact", "contact_emails", ["contact_id"]
    )
    op.create_index(
        "ix_contact_emails_primary",
        "contact_emails",
        ["contact_id", "is_primary"],
    )
