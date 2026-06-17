"""backups — tabla de auditoría de backups cifrados.

Revision ID: 20260617_0054
Revises: 20260617_0053
Create Date: 2026-06-17 20:30:00

Sprint Backup: orquestación del backup cron + manual desde UI admin.
Cada ejecución (cron 72h o disparo desde `/admin/backups`) crea una row
en `backups` que la página admin lista. El binario vive en
`/var/backups/crmbo/<filename>` (encrypted .tar.gz.gpg); la row guarda
solo metadata + ruta + URL Drive opcional.

ENUMs como VARCHAR (no native MySQL ENUM) para mantener la convención
del modelo (`Enum(..., native_enum=False, values_callable=enum_values)`).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260617_0054"
down_revision: str | None = "20260617_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backups",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("filepath", sa.String(length=500), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("drive_url", sa.String(length=500)),
        sa.Column("error_summary", sa.Text()),
        sa.Column(
            "triggered_by",
            sa.String(length=40),
            nullable=False,
            server_default="cron",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_backups_started_at", "backups", ["started_at"])
    op.create_index("ix_backups_status", "backups", ["status"])


def downgrade() -> None:
    op.drop_index("ix_backups_status", table_name="backups")
    op.drop_index("ix_backups_started_at", table_name="backups")
    op.drop_table("backups")
