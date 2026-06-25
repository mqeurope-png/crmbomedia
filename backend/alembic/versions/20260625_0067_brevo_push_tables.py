"""Sprint-Push-CRM-Brevo — brevo_user_list_mappings + contact tracking.

Revision ID: 20260625_0067
Revises: 20260626_0066
Create Date: 2026-06-25 11:00:00

Bart pidió 2026-06-25 el reverso del sync Brevo: contactos del CRM con
owner asignado se suben a Brevo en la lista del owner. Para eso:

- `brevo_user_list_mappings`: pivote owner_user_id -> brevo_list_id. PK
  por user_id (un user = una lista) como pidió Bart. ON DELETE CASCADE
  con users porque el mapping no tiene sentido sin el user.

- `contacts.brevo_contact_id`: id que devuelve Brevo (string porque
  Brevo lo da como número grande). NULL = no subido todavía. El
  periodic push runner filtra exactamente por `IS NULL`.

- `contacts.brevo_last_synced_at`: último push exitoso. Sirve para
  auditoría + para decidir si re-pushear si el contacto cambia (futuro).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_0067"
down_revision: str | None = "20260626_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brevo_user_list_mappings",
        sa.Column("user_id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("brevo_list_id", sa.Integer(), nullable=False),
        sa.Column("brevo_list_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_brevo_user_list_mappings_user_id",
        ),
    )
    op.add_column(
        "contacts",
        sa.Column("brevo_contact_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column(
            "brevo_last_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("contacts", "brevo_last_synced_at")
    op.drop_column("contacts", "brevo_contact_id")
    op.drop_table("brevo_user_list_mappings")
