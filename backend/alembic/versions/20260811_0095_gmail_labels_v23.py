"""CRM · CRM-ETIQUETAS-GMAIL-V2.3 — labels de Gmail en el CRM.

Extiende `email_labels` (v2.4a, per-user) para que también pueda alojar las
labels PERSONALIZADAS de Gmail como etiquetas org-wide:
  - `user_id` pasa a nullable: NULL = etiqueta org (espejo de una label de
    Gmail, visible para todos); no-NULL = etiqueta personal CRM (sin cambio).
  - `gmail_label_id` (unique): id upstream ('Label_123…'). NULL en las
    personales.
  - `is_system` / `is_hidden`: reservadas para marcar labels de sistema y
    ocultar del sidebar sin borrar el mapeo.

Nueva tabla `email_message_labels`: mapeo mensaje↔etiqueta (las labels de
Gmail viven a nivel de MENSAJE, no de hilo — un hilo puede tener un mensaje
etiquetado y otros no). La puebla `sync_labels` (retroactivo desde el JSON
`email_messages.gmail_labels`), el push de Gmail (labelsAdded/Removed) y los
endpoints POST/DELETE /api/emails/messages/{id}/labels.

Revision ID: 20260811_0095
Revises: 20260811_0094
Create Date: 2026-08-11 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0095"
down_revision: str | None = "20260811_0094"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_labels",
        sa.Column("gmail_label_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ux_email_labels_gmail_label_id",
        "email_labels",
        ["gmail_label_id"],
        unique=True,
    )
    op.add_column(
        "email_labels",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "email_labels",
        sa.Column(
            "is_hidden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "email_labels",
        "user_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )

    op.create_table(
        "email_message_labels",
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "label_id",
            sa.String(length=36),
            sa.ForeignKey("email_labels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_email_message_labels_label_id",
        "email_message_labels",
        ["label_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_message_labels_label_id", table_name="email_message_labels"
    )
    op.drop_table("email_message_labels")
    # Antes de devolver user_id a NOT NULL hay que retirar las filas org
    # (user_id NULL) que este sprint introdujo.
    op.execute("DELETE FROM email_labels WHERE user_id IS NULL")
    op.alter_column(
        "email_labels",
        "user_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.drop_column("email_labels", "is_hidden")
    op.drop_column("email_labels", "is_system")
    op.drop_index(
        "ux_email_labels_gmail_label_id", table_name="email_labels"
    )
    op.drop_column("email_labels", "gmail_label_id")
