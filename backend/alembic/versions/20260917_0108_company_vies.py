"""Fase VIES — validación del NIF-IVA intracomunitario en VIES, guardada en la
empresa: `vies_status` (valido / no_valido / desconocido), `vies_checked_at`,
`vies_vat` (el NIF-IVA validado, para detectar cambios) y el nombre /
dirección que devuelve VIES.

Sin backfill: las empresas se validan al editarlas, con «Revalidar en VIES»
o cuando la ficha lo pide; hasta entonces el chip dice «pendiente».

Revision ID: 20260917_0108
Revises: 20260916_0107
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_0108"
down_revision = "20260916_0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("vies_status", sa.String(length=16), nullable=True))
    op.add_column(
        "companies", sa.Column("vies_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("companies", sa.Column("vies_vat", sa.String(length=40), nullable=True))
    op.add_column("companies", sa.Column("vies_name", sa.String(length=255), nullable=True))
    op.add_column("companies", sa.Column("vies_address", sa.String(length=500), nullable=True))


def downgrade() -> None:
    for column in ("vies_address", "vies_name", "vies_vat", "vies_checked_at", "vies_status"):
        op.drop_column("companies", column)
