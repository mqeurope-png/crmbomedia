"""PR-Fixes-Pase-4 Bug 6 — custom field definitions table.

Revision ID: 20260621_0061
Revises: 20260620_0060
Create Date: 2026-06-21 09:00:00

Tabla de definiciones de custom fields nativos del CRM. Hasta ahora el
endpoint `/api/contacts/custom-field-keys` solo inferaba el catálogo
escaneando `Contact.custom_fields` (JSON), por lo que aparecían solo
los campos importados de AgileCRM. Con esta tabla:

- El admin puede crear/borrar campos desde `/admin/custom-fields`.
- El dropdown del workflow muestra tanto fields importados como
  manuales, anotando origen ("manual" / "agilecrm" / "inferred").
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260621_0061"
down_revision: str | None = "20260620_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column(
            "field_type",
            sa.String(length=20),
            nullable=False,
            server_default="text",
        ),
        # `source` distinguishes manually-created from imported
        # definitions. Imported integrations may seed this table to
        # surface their fields even before any contact has them.
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint("key", name="uq_custom_field_definitions_key"),
    )


def downgrade() -> None:
    op.drop_table("custom_field_definitions")
