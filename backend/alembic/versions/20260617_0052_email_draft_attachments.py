"""email_draft_attachments — adjuntos regulares de drafts.

Revision ID: 20260617_0052
Revises: 20260617_0051
Create Date: 2026-06-17 17:30:00

Sprint Email v2.5 — A: archivo adjuntos en envío. El draft puede
tener N archivos pegados al body que viajan como
`multipart/mixed > Content-Disposition: attachment` cuando Gmail
manda el mail. Distinto de `email_template_attachments` (PR-167) que
guarda binarios para `<img src="cid:...">` inline.

Cap operativo (Gmail): 25 MB total por mensaje (suma de attachments
+ body + headers). Lo enforce-amos en el endpoint POST a nivel draft
para fallar temprano; MEDIUMBLOB (16 MB / fila) cabe 1 attachment
grande individual y 25 MB sumando varios.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260617_0052"
down_revision: str | None = "20260616_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_mysql = bind.dialect.name == "mysql"

    op.create_table(
        "email_draft_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(length=36),
            sa.ForeignKey("email_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "data",
            mysql.MEDIUMBLOB() if is_mysql else sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_email_draft_attachments_draft_id",
        "email_draft_attachments",
        ["draft_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_draft_attachments_draft_id",
        table_name="email_draft_attachments",
    )
    op.drop_table("email_draft_attachments")
