"""BoHub ERP Fase C (C-3) — índices para el cross-check de clientes FACTUSOL.

C-3 busca clientes en FACTUSOL y cruza los CODCLI encontrados contra el CRM
(`companies.factusol_company_id`, `contacts.factusol_contact_id`) en cada
búsqueda del autocomplete. Esas columnas existen desde 0080 pero **sin índice**;
con 4531 clientes y un autocomplete que dispara por cada tecleo, el full scan se
nota. Esta migración solo añade los índices — no hay columnas nuevas.

Revision ID: 20260805_0087
Revises: 20260805_0086
Create Date: 2026-08-05 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_0087"
down_revision: str | None = "20260805_0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_companies_factusol_company_id", "companies", ["factusol_company_id"],
    )
    op.create_index(
        "idx_contacts_factusol_contact_id", "contacts", ["factusol_contact_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_contacts_factusol_contact_id", table_name="contacts")
    op.drop_index("idx_companies_factusol_company_id", table_name="companies")
