"""CRM · CRM-ETIQUETAS-EN-BANDEJA — `text_color` en email_labels.

Gmail define cada label con DOS colores: `color.backgroundColor` (el fondo
del chip) y `color.textColor` (blanco o negro, el que da contraste sobre
ese fondo). El import de PR #342 solo guardó el primero en `color`, así
que los chips de la bandeja no podían replicar el contraste de Gmail
(texto blanco sobre rojo, negro sobre amarillo).

`email_labels.color` sigue siendo el backgroundColor — no se renombra
para no tocar las 368 filas ya importadas ni el resto de call sites.

Tras deployar hay que re-ejecutar `sync_labels` para poblar el nuevo
campo retroactivamente (la API de Gmail lo devuelve en el mismo
`users.labels.list` que ya llamamos).

Revision ID: 20260811_0096
Revises: 20260811_0095
Create Date: 2026-08-11 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0096"
down_revision: str | None = "20260811_0095"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_labels",
        sa.Column("text_color", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_labels", "text_color")
