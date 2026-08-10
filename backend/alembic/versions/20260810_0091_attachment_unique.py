"""CRM · CRM-ADJUNTOS-BACKFILL — UNIQUE (message_id, gmail_attachment_id).

Idempotencia del backfill metadata-only de adjuntos (Opción B): un adjunto
de Gmail solo se registra una vez por mensaje. Antes de crear la constraint
se eliminan posibles duplicados históricos (conserva la fila con menor id);
en base vacía el DELETE es un no-op.

MySQL: la clave compuesta ocupa (36+512)×4 = 2192 bytes < 3072 (límite
InnoDB DYNAMIC) — cabe sin prefijos.

Revision ID: 20260810_0091
Revises: 20260807_0090
Create Date: 2026-08-10 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_0091"
down_revision: str | None = "20260807_0090"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "email_message_attachments"
_CONSTRAINT = "uq_message_attachment"


def upgrade() -> None:
    # Dedupe previo (portable MySQL/SQLite): conserva MIN(id) por pareja
    # (message_id, gmail_attachment_id); las filas con gmail_attachment_id
    # NULL no participan (la constraint UNIQUE ignora NULLs).
    op.execute(
        f"""
        DELETE FROM {_TABLE}
        WHERE gmail_attachment_id IS NOT NULL
          AND id NOT IN (
            SELECT keep_id FROM (
                SELECT MIN(id) AS keep_id
                FROM {_TABLE}
                WHERE gmail_attachment_id IS NOT NULL
                GROUP BY message_id, gmail_attachment_id
            ) AS keepers
          )
        """
    )
    op.create_unique_constraint(
        _CONSTRAINT, _TABLE, ["message_id", "gmail_attachment_id"]
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="unique")
