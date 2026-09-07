"""ERP-E4-fix1 — idioma persistido por pedido y por empresa cliente.

Dos columnas nuevas (ISO 639-1, NULL = desconocido):

- `orders.language`: detectado al importar de WooCommerce (WPML/locale/país
  de facturación) o corregido a mano en la ficha del pedido.
- `companies.language`: preferencia estable del cliente, editable en su
  ficha del CRM.

Alimentan la cascada de idioma de los PDF del ERP (selector → pedido →
cliente → empresa emisora → español). Sin backfill: el histórico se
resuelve por la propia cascada.

Revision ID: 20260907_0097
Revises: 20260811_0096
Create Date: 2026-09-07 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0097"
down_revision: str | None = "20260811_0096"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("language", sa.String(length=5), nullable=True),
    )
    op.add_column(
        "companies", sa.Column("language", sa.String(length=5), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "language")
    op.drop_column("orders", "language")
