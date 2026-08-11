"""CRM · CRM-ADJUNTOS-UX — columna `is_inline` en email_message_attachments.

Distingue las imágenes embebidas en el cuerpo (Content-Disposition: inline /
referenciadas por Content-ID, p. ej. `image001.jpg` de Outlook) de los
adjuntos reales. Las surfaces de usuario (chips del detalle, filtro
`has_attachments`, clip de la bandeja) ignoran las inline.

Marcado retroactivo NO destructivo de los ~23k adjuntos ya importados por
CRM-ADJUNTOS-BACKFILL: no podemos re-consultar el Content-Disposition en una
migración, así que aplicamos una heurística CONSERVADORA por nombre+tamaño
(imágenes pequeñas tipo `imageNNN.jpg`). Preferimos dejar como adjunto algún
inline dudoso antes que ocultar un adjunto legítimo. Bart puede relanzar el
backfill con la lógica de headers nueva para una clasificación exacta.

Revision ID: 20260811_0092
Revises: 20260810_0091
Create Date: 2026-08-11 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0092"
down_revision: str | None = "20260810_0091"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "email_message_attachments"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "is_inline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Marcado retroactivo (idempotente): imágenes pequeñas con nombre
    # `image…` + extensión de imagen y tamaño < 100 KB → inline probable.
    # LIKE portable MySQL/SQLite; sin REGEXP para no depender del dialecto.
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET is_inline = 1
        WHERE LOWER(filename) LIKE 'image%'
          AND (
                LOWER(filename) LIKE '%.jpg'
             OR LOWER(filename) LIKE '%.jpeg'
             OR LOWER(filename) LIKE '%.png'
             OR LOWER(filename) LIKE '%.gif'
          )
          AND size_bytes IS NOT NULL
          AND size_bytes < 102400
        """
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "is_inline")
