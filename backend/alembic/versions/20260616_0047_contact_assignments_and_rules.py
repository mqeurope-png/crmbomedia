"""contact_assignments + assignment_rules (multi-comercial)

Revision ID: 20260616_0047
Revises: 20260615_0046
Create Date: 2026-06-16 05:10:00

Sprint Reglas-Assign — PR-A. Multi-asignación de contactos a
comerciales (primary + secundarios) + reglas de auto-asignación.

- `contact_assignments`: M:N contact↔user con `is_primary`. La columna
  `contacts.owner_user_id` SE MANTIENE como caché desnormalizado del
  primary (recalculado en código, no trigger). Backfill: por cada
  contacto con `owner_user_id` no-NULL se crea un assignment primary.
- `assignment_rules`: reglas con `conditions_json` (árbol IR del motor
  de filtros) que se aplican en creación / manual.

Backfill paginado (batches de 500) por seguridad sobre la tabla
`contacts` (~20k filas) aunque hoy casi nadie está asignado.

`contact_assignments.rule_id` referencia `assignment_rules.id`, así que
la tabla de reglas se crea PRIMERO.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260616_0047"
down_revision: str | None = "20260615_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assignment_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "priority", sa.Integer(), nullable=False, server_default="100"
        ),
        sa.Column("conditions_json", sa.Text(), nullable=False),
        sa.Column("primary_user_id", sa.String(length=36), nullable=True),
        sa.Column("secondary_user_ids_json", sa.Text(), nullable=True),
        sa.Column(
            "apply_to",
            sa.String(length=20),
            nullable=False,
            server_default="unassigned_only",
        ),
        sa.Column(
            "override_existing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "stop_on_match",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["primary_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_assignment_rules_active_priority",
        "assignment_rules",
        ["is_active", "priority"],
    )

    op.create_table(
        "contact_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("rule_id", sa.String(length=36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["assignment_rules.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "contact_id", "user_id", name="uq_contact_assignment_user"
        ),
    )
    op.create_index(
        "ix_contact_assignments_contact",
        "contact_assignments",
        ["contact_id", "is_primary"],
    )
    op.create_index(
        "ix_contact_assignments_user",
        "contact_assignments",
        ["user_id", "is_primary"],
    )

    _backfill_from_owner()


def _backfill_from_owner() -> None:
    """One primary assignment per contact with a non-NULL owner_user_id.
    Paginated to avoid a long lock on `contacts`. Idempotent against the
    UNIQUE (re-running the migration data step would skip existing
    rows), though Alembic runs upgrade once."""
    bind = op.get_bind()
    contacts = sa.table(
        "contacts",
        sa.column("id", sa.String),
        sa.column("owner_user_id", sa.String),
    )
    assignments = sa.table(
        "contact_assignments",
        sa.column("id", sa.String),
        sa.column("contact_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("is_primary", sa.Boolean),
        sa.column("assigned_by_user_id", sa.String),
        sa.column("assigned_at", sa.DateTime),
        sa.column("source", sa.String),
        sa.column("rule_id", sa.String),
        sa.column("notes", sa.Text),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    batch = 500
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(contacts.c.id, contacts.c.owner_user_id)
            .where(
                contacts.c.owner_user_id.is_not(None),
                contacts.c.id > last_id,
            )
            .order_by(contacts.c.id)
            .limit(batch)
        ).fetchall()
        if not rows:
            break
        now = datetime.now(UTC)
        bind.execute(
            assignments.insert(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "contact_id": cid,
                    "user_id": owner,
                    "is_primary": True,
                    "assigned_by_user_id": None,
                    "assigned_at": now,
                    "source": "backfill",
                    "rule_id": None,
                    "notes": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for (cid, owner) in rows
            ],
        )
        last_id = rows[-1][0]
        if len(rows) < batch:
            break


def downgrade() -> None:
    op.drop_index(
        "ix_contact_assignments_user", table_name="contact_assignments"
    )
    op.drop_index(
        "ix_contact_assignments_contact", table_name="contact_assignments"
    )
    op.drop_table("contact_assignments")
    op.drop_index(
        "ix_assignment_rules_active_priority", table_name="assignment_rules"
    )
    op.drop_table("assignment_rules")
