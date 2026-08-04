"""BoHub ERP Fase C (C-1) — vínculo pedido ↔ factura FACTUSOL.

Añade `orders.factusol_invoice_number` (nullable) para guardar el CODFAC de
la factura emitida en FACTUSOL. El nuevo valor de enum `invoiced_by_erp` de
`invoice_status` NO requiere cambio de columna: es VARCHAR(40) StrEnum
(native_enum=False) desde 0082, y «invoiced_by_erp» (15 chars) cabe.

Revision ID: 20260804_0084
Revises: 20260803_0083
Create Date: 2026-08-04 06:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0084"
down_revision: str | None = "20260803_0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("factusol_invoice_number", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "factusol_invoice_number")
