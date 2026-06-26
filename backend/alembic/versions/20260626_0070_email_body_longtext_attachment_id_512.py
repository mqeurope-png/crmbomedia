"""PR-Fix-Backfill-Gmail-Tras-Validación — body LONGTEXT + gmail_attachment_id VARCHAR(512).

Revision ID: 20260626_0070
Revises: 20260625_0069
Create Date: 2026-06-26 08:00:00

Bart aplicó manualmente estos ALTERs en producción 2026-06-26 tras
errores reales durante el backfill:

  ALTER TABLE email_messages
    MODIFY body_html LONGTEXT,
    MODIFY body_text LONGTEXT;
  ALTER TABLE email_message_attachments
    MODIFY gmail_attachment_id VARCHAR(512);

Síntomas:

- pymysql.err.DataError: (1406, "Data too long for column 'body_html'
  at row 1") — emails con firma corporativa (logo inline base64) +
  thread acumulado superan los 65 KB del TEXT default.
- pymysql.err.DataError: (1406, "Data too long for column
  'gmail_attachment_id' at row 1") — IDs de attachments Gmail son
  base64 de ~350-450 chars; VARCHAR(255) los trunca.

Esta migración formaliza esos workarounds. Diseñada idempotente:
si el operador ya aplicó los ALTERs manualmente, `op.alter_column`
emite el mismo `ALTER TABLE … MODIFY` que MySQL acepta sin error.

Bajo SQLite (tests) Text/LONGTEXT son ambos `TEXT`; el cambio de
String(255)→String(512) en SQLite tampoco requiere reescritura
porque no enforcea el length. Por eso la migración solo aplica en
MySQL — bajo SQLite el bloque `if dialect.name == "mysql"` es un
no-op.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260626_0070"
down_revision: str | None = "20260625_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        # SQLite (tests) no diferencia entre TEXT y LONGTEXT y trata
        # VARCHAR(255) y VARCHAR(512) como el mismo TEXT. El modelo
        # SQLAlchemy ya refleja los tipos correctos; no hay nada que
        # reescribir aquí en dialectos no-MySQL.
        return

    op.alter_column(
        "email_messages",
        "body_html",
        existing_type=sa.Text(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "email_messages",
        "body_text",
        existing_type=sa.Text(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "email_message_attachments",
        "gmail_attachment_id",
        existing_type=sa.String(255),
        type_=sa.String(512),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    # Riesgoso pero documentado — si algún row supera 64 KB el
    # downgrade fallará con el mismo 1406 que motivó el upgrade.
    op.alter_column(
        "email_message_attachments",
        "gmail_attachment_id",
        existing_type=sa.String(512),
        type_=sa.String(255),
        existing_nullable=True,
    )
    op.alter_column(
        "email_messages",
        "body_text",
        existing_type=mysql.LONGTEXT(),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "email_messages",
        "body_html",
        existing_type=mysql.LONGTEXT(),
        type_=sa.Text(),
        existing_nullable=True,
    )
