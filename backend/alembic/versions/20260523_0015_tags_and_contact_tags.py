"""tags + contact_tags tables; backfill from Contact.tags CSV

Revision ID: 20260523_0015
Revises: 20260522_0014
Create Date: 2026-05-23 00:00:00

Sprint P.1 ampliado introduces a real `tags` table and an M:N
`contact_tags` relationship. The CSV column on `contacts.tags` is
kept (for backwards-compat rollback) but new code stops updating it.

The data step parses every `contacts.tags` CSV row into individual
tag names, upserts them case-insensitively into `tags`, and links
them via `contact_tags` with `source='migrated_from_csv'`.

Idempotent: re-running the data step finds existing tags by
`name_normalized` and skips rows already linked.
"""
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260523_0015"
down_revision: str | None = "20260522_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "name_normalized", sa.String(length=100), nullable=False, index=True
        ),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name_normalized", name="uq_tag_name_normalized"),
    )

    op.create_table(
        "contact_tags",
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.String(length=36),
            sa.ForeignKey("tags.id"),
            primary_key=True,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=True),
    )

    _backfill_from_csv()


def downgrade() -> None:
    op.drop_table("contact_tags")
    op.drop_table("tags")


def _backfill_from_csv() -> None:
    """Walk every contact with a non-empty `tags` CSV, split on comma,
    case-insensitively upsert into `tags`, and link via `contact_tags`.
    Tags are created with `created_by_user_id=NULL` so the operator
    knows they came from the legacy column."""
    bind = op.get_bind()
    contacts_table = sa.table(
        "contacts",
        sa.column("id", sa.String),
        sa.column("tags", sa.String),
    )
    tags_table = sa.table(
        "tags",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("name_normalized", sa.String),
        sa.column("color", sa.String),
        sa.column("description", sa.Text),
        sa.column("created_by_user_id", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    contact_tags_table = sa.table(
        "contact_tags",
        sa.column("contact_id", sa.String),
        sa.column("tag_id", sa.String),
        sa.column("assigned_at", sa.DateTime),
        sa.column("assigned_by_user_id", sa.String),
        sa.column("source", sa.String),
    )

    now = datetime.now(timezone.utc)
    rows = bind.execute(
        sa.select(contacts_table.c.id, contacts_table.c.tags).where(
            contacts_table.c.tags.isnot(None),
            contacts_table.c.tags != "",
        )
    ).all()

    # Cache normalized → id for existing/new tags so we don't keep
    # round-tripping during the loop.
    tag_id_by_normalized: dict[str, str] = {}
    existing = bind.execute(
        sa.select(tags_table.c.id, tags_table.c.name_normalized)
    ).all()
    for tag_id, normalized in existing:
        tag_id_by_normalized[normalized] = tag_id

    for contact_id, csv in rows:
        seen_normalized: set[str] = set()
        for raw in csv.split(","):
            cleaned = raw.strip()
            if not cleaned:
                continue
            normalized = cleaned.lower()
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)

            tag_id = tag_id_by_normalized.get(normalized)
            if tag_id is None:
                tag_id = str(uuid4())
                bind.execute(
                    tags_table.insert().values(
                        id=tag_id,
                        name=cleaned,
                        name_normalized=normalized,
                        color=None,
                        description=None,
                        created_by_user_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                tag_id_by_normalized[normalized] = tag_id

            # Idempotent insert: skip if the link already exists.
            already = bind.execute(
                sa.select(contact_tags_table.c.contact_id).where(
                    contact_tags_table.c.contact_id == contact_id,
                    contact_tags_table.c.tag_id == tag_id,
                )
            ).first()
            if already:
                continue
            bind.execute(
                contact_tags_table.insert().values(
                    contact_id=contact_id,
                    tag_id=tag_id,
                    assigned_at=now,
                    assigned_by_user_id=None,
                    source="migrated_from_csv",
                )
            )
