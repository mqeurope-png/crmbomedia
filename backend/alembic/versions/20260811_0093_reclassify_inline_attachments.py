"""CRM · CRM-ADJUNTOS-INLINE-FIX — content_id + re-clasificación inline.

Dos cambios sobre `email_message_attachments`:

1. Columna `content_id` (nullable): el `Content-ID` de la parte MIME (sin
   `<>`). Casa las referencias `cid:...` del HTML con el adjunto para
   reescribirlas a la URL de descarga. NULL en las filas ya importadas (el
   backfill metadata-only no lo guardaba); se rellena go-forward y hay
   fallback por filename para el histórico. NO se re-consulta Gmail en la
   migración (serían miles de llamadas) — decisión documentada en el PR.

2. Re-clasificación retroactiva de inline: la heurística de 0092 solo marcó
   `image%` de < 100 KB, pero las firmas corporativas incrustadas de
   Outlook (`imageNNN.jpg`) suelen pesar 200 KB – 2 MB y seguían saliendo
   como adjunto descargable. Ampliamos a `imageNNN.<ext>` de CUALQUIER
   tamaño (patrón `image` + 3 caracteres + extensión de imagen), portable
   con LIKE (`_` = un carácter) para que corra igual en MySQL y SQLite.
   No usamos la heurística por `content_id` (está NULL en el histórico).

Revision ID: 20260811_0093
Revises: 20260811_0092
Create Date: 2026-08-11 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0093"
down_revision: str | None = "20260811_0092"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "email_message_attachments"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("content_id", sa.String(length=255), nullable=True),
    )
    # `image___.<ext>` = "image" + exactamente 3 caracteres + extensión de
    # imagen. Cubre el patrón `imageNNN.jpg` de Outlook/Word sin tope de
    # tamaño. Idempotente (solo toca is_inline=0). LIKE portable.
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET is_inline = 1
        WHERE is_inline = 0
          AND (
                LOWER(filename) LIKE 'image___.jpg'
             OR LOWER(filename) LIKE 'image___.jpeg'
             OR LOWER(filename) LIKE 'image___.png'
             OR LOWER(filename) LIKE 'image___.gif'
          )
        """
    )


def downgrade() -> None:
    # No revertible con precisión (no guardamos snapshot de qué filas marcó
    # este UPDATE). Solo quitamos la columna; los is_inline marcados se
    # quedan — un adjunto legítimo `imageNNN.jpg` mal marcado se corrige por
    # SQL manual si molesta.
    op.drop_column(_TABLE, "content_id")
