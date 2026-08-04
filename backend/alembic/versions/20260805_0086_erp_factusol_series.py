"""BoHub ERP Fase C (C-2) — serie de facturación FACTUSOL configurable.

Añade `erp_settings.factusol_series_json`: un blob JSON
`{"default": "A", "by_source": {"manual": "M", …}}` con la serie por defecto y
los overrides por origen del pedido. Un solo campo en vez de una columna por
tienda — el catálogo de orígenes cambia al añadir tiendas.

Revision ID: 20260805_0086
Revises: 20260805_0085
Create Date: 2026-08-05 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0086"
down_revision: str | None = "20260805_0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "erp_settings",
        sa.Column("factusol_series_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("erp_settings", "factusol_series_json")
