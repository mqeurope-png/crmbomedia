"""BoHub ERP Fase B (B-2-fix5) — alinear ancho de estados en el historial.

La migración 0082 amplió a VARCHAR(40) los 4 estados de `orders` para
acomodar los valores `already_*_externally` (hasta 28 chars), pero olvidó
las columnas gemelas de `order_status_history` (`from_status` / `to_status`),
que guardan esos mismos valores en cada transición. En prod eso reventaba
con «Data too long for column 'to_status'» al marcar un pedido como
procesado externamente.

Idempotente en el VPS (el ALTER manual ya está aplicado → MySQL detecta
que el tipo coincide y es un no-op instantáneo); en entornos nuevos aplica
el ALTER limpio.

Revision ID: 20260803_0083
Revises: 20260803_0082
Create Date: 2026-08-03 17:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0083"
down_revision: str | None = "20260803_0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "order_status_history", "from_status",
        existing_type=sa.String(24), type_=sa.String(40),
        existing_nullable=True,
    )
    op.alter_column(
        "order_status_history", "to_status",
        existing_type=sa.String(24), type_=sa.String(40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "order_status_history", "to_status",
        existing_type=sa.String(40), type_=sa.String(24),
        existing_nullable=False,
    )
    op.alter_column(
        "order_status_history", "from_status",
        existing_type=sa.String(40), type_=sa.String(24),
        existing_nullable=True,
    )
