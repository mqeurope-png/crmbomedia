"""CRM · CRM-ADJUNTOS-PURGE — columna `gmail_status` en email_messages.

Marca los mails «huérfanos»: importados en su día (backfill #246/#331) pero
cuyo `gmail_message_id` ya no existe en Gmail (Bart vació la papelera). Hoy
aparecen como mails normales y sus chips de descarga dan 410 en runtime.

Valores: 'active' (default) | 'deleted_gmail'. Los marca el flag
`--purge-not-found` de los backfills al recibir 404 de Gmail. Los hilos
completamente huérfanos se ocultan de las vistas generales y viven en la
vista `state=deleted` («Papelera Gmail»). No se borra nada del CRM.

Revision ID: 20260811_0094
Revises: 20260811_0093
Create Date: 2026-08-11 15:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0094"
down_revision: str | None = "20260811_0093"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "email_messages"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "gmail_status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index("ix_email_messages_gmail_status", _TABLE, ["gmail_status"])


def downgrade() -> None:
    op.drop_index("ix_email_messages_gmail_status", table_name=_TABLE)
    op.drop_column(_TABLE, "gmail_status")
