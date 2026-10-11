"""El borrador guardado arrastra la conversación con la IA.

`email_drafts.asistente_borrador_id`: el turno del asistente que estaba en el
editor cuando se guardó el borrador (autoguardado o «Guardar en Borradores»
al cerrar). Al reabrirlo, la franja vuelve con sus turnos y su coste. Sin
datos que migrar.

Revision ID: 20261011_0140
Revises: 20261011_0139
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0140"
down_revision: str | None = "20261011_0139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_drafts") as batch:
        batch.add_column(sa.Column("asistente_borrador_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("email_drafts") as batch:
        batch.drop_column("asistente_borrador_id")
