"""Limpieza de empresas — archivado REVERSIBLE (nunca borrado físico):
`is_archived` (fuera de listados / bandejas / buscadores por defecto),
`archived_at` y `archived_reason`. Contactos, pedidos y tareas se conservan;
solo se ocultan con la empresa. «Restaurar» pone `is_archived=0`.

Revision ID: 20260919_0110
Revises: 20260918_0109
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_0110"
down_revision = "20260918_0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "companies", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("companies", sa.Column("archived_reason", sa.String(length=255), nullable=True))
    op.create_index("ix_companies_is_archived", "companies", ["is_archived"])


def downgrade() -> None:
    op.drop_index("ix_companies_is_archived", table_name="companies")
    op.drop_column("companies", "archived_reason")
    op.drop_column("companies", "archived_at")
    op.drop_column("companies", "is_archived")
