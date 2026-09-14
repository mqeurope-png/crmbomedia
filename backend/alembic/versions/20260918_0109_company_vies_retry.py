"""Fase VIES — revalidación en segundo plano de los «pendiente»: control del
reintento por empresa. `vies_next_retry_at` (antes de esa hora el barrido no
vuelve a consultar esa empresa; backoff creciente) y `vies_attempts`
(consultas seguidas sin veredicto; se pone a 0 con un veredicto firme).

Revision ID: 20260918_0109
Revises: 20260917_0108
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_0109"
down_revision = "20260917_0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies", sa.Column("vies_next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("companies", sa.Column("vies_attempts", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "vies_attempts")
    op.drop_column("companies", "vies_next_retry_at")
