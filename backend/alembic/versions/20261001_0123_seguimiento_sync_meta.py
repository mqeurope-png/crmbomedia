"""Seguimiento (app) — estado propio del espejo: `seguimiento_sync_meta`.

Tabla clave → valor del espejo BoHub ↔ hoja. La primera clave es
`formato_bd`: el nº de columnas con el que están escritas las filas que el
espejo guarda (`seguimiento_sync_snapshot`, `seguimiento_manual`,
`seguimiento_legacy`). Con la columna «Courier» la hoja pasa de 19 a 20
columnas; el espejo pone al día esas filas en su propia pasada (bajo el mismo
cerrojo que la escritura de la hoja, y en la misma transacción que deja la
marca), no aquí: así nunca conviven una pasada vieja y datos ya convertidos.

Sin DEFAULT en la columna TEXT (MySQL lo rechaza, error 1101): el ORM siempre
da valor.

Revision ID: 20261001_0123
Revises: 20260930_0122
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_0123"
down_revision = "20260930_0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seguimiento_sync_meta",
        sa.Column("clave", sa.String(length=40), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("clave"),
    )


def downgrade() -> None:
    op.drop_table("seguimiento_sync_meta")
