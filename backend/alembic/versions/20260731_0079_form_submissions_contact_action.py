"""form_submissions.contact_action (created | updated | spam).

Sprint Web-Forms v3 (Bug 4). Marca visualmente en la vista de Submissions
si un submit creó un contacto nuevo, actualizó uno existente o fue spam.

Nota: es la 0079 (no 0080). La v2 (tipo tags + field_key + idiomas) no
necesitó migración — `field_type` es String(16), no un ENUM —, así que el
siguiente número libre tras la 0078 es la 0079.

Revision ID: 20260731_0079
Revises: 20260730_0078
Create Date: 2026-07-31 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0079"
down_revision: str | None = "20260730_0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "form_submissions",
        sa.Column("contact_action", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("form_submissions", "contact_action")
