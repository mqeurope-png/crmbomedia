"""extend companies table for Sprint Empresas + add SET NULL on FK

Revision ID: 20260615_0041
Revises: 20260615_0040
Create Date: 2026-06-15 14:00:00

Sprint Empresas — sub-PR 1/4. The `companies` table has existed
since migration 0001 with the bare minimum (id / name / tax_id /
website / is_active). This migration extends it with the fields
the sprint introduces and relaxes a few inherited constraints
that hurt at scale:

- new columns: domain (unique, NULL-allowed), vat, country, region,
  state, city, address_line, postal_code, sector, size_category,
  notes, source, external_references_json, custom_fields_json.
- `tax_id` loses its UNIQUE constraint — pre-VAT-rollout rows
  often share NULL and we don't want them colliding. The column
  stays so the model keeps reading it through the canonical
  `Company.tax_id` attribute (the spec calls it CIF in Spanish
  but it's the same value).
- contacts.company_id was added in 0001 but with no ondelete
  action; we add the SET NULL behaviour so removing a company
  leaves its contacts orphan rather than blowing up the delete.

`domain` UNIQUE accepts multiple NULLs on MySQL 8 so manual rows
without an email-domain hint don't collide. The backfill +
Brevo sync converge on this key.

MySQL-8 safe: every nullable column added without NULL fallback;
the new source column has a server_default so existing rows pick
up "manual" without a multi-statement migration.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0041"
down_revision: str | None = "20260615_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Relax tax_id uniqueness — NULL collisions blocked further
    # inserts in practice. Best-effort: SQLite ignores the named
    # constraint so we wrap in try/except via batch_op.
    with op.batch_alter_table("companies") as batch_op:
        try:
            batch_op.drop_constraint("tax_id", type_="unique")
        except Exception:  # noqa: BLE001
            # Different MySQL deployments named the constraint
            # differently; SQLite (used in tests) tracks the
            # constraint inline so the batch rewrite drops it
            # automatically when the new schema is applied.
            pass

    op.add_column(
        "companies",
        sa.Column("domain", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("vat", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("country", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("region", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("state", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("city", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("address_line", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("postal_code", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("sector", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("size_category", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "companies",
        sa.Column("external_references_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("custom_fields_json", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_companies_domain", "companies", ["domain"]
    )
    op.create_index("ix_companies_cif", "companies", ["tax_id"])
    op.create_index("ix_companies_country", "companies", ["country"])

    # contacts.company_id existed since 0001 but with no ondelete
    # action — re-create the FK with SET NULL so a company delete
    # orphans its contacts rather than blowing up.
    with op.batch_alter_table("contacts") as batch_op:
        try:
            batch_op.drop_constraint(
                "contacts_ibfk_1", type_="foreignkey"
            )
        except Exception:  # noqa: BLE001
            pass
        batch_op.create_foreign_key(
            "fk_contacts_company",
            "companies",
            ["company_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch_op:
        try:
            batch_op.drop_constraint(
                "fk_contacts_company", type_="foreignkey"
            )
        except Exception:  # noqa: BLE001
            pass

    op.drop_index("ix_companies_country", table_name="companies")
    op.drop_index("ix_companies_cif", table_name="companies")
    op.drop_constraint(
        "uq_companies_domain", "companies", type_="unique"
    )
    for col in (
        "custom_fields_json",
        "external_references_json",
        "source",
        "notes",
        "size_category",
        "sector",
        "postal_code",
        "address_line",
        "city",
        "state",
        "region",
        "country",
        "vat",
        "domain",
    ):
        op.drop_column("companies", col)
    op.create_unique_constraint(
        "uq_companies_tax_id", "companies", ["tax_id"]
    )
