"""extend contacts with professional + address fields

Revision ID: 20260615_0042
Revises: 20260615_0041
Create Date: 2026-06-15 16:00:00

Sprint Empresas — sub-PR 2/4. Lifts the most-requested-by-the-
business Brevo + Agile attributes from `custom_fields` JSON into
first-class columns on `contacts`:

- `job_title` — Brevo `JOB_TITLE`, Agile `Title`
- `linkedin_url` — Brevo `LINKEDIN`, Agile `LinkedIn`
- `personal_website` — Brevo `WEB`, Agile `Website` (distinct from
  the company's website which now lives on `companies.website`)
- `address_line` — Brevo `ADDRESS`, Agile `Address` (street + nº)
- `address_postal_code` — Brevo `CODIGO_POSTAL`, Agile `Zip Code`
- `address_region` — Brevo `PAIS_REGION` (UE-level supra-state
  grouping that doesn't fit `address_state`)

Existing `address_city` + `address_state` are reused — no rename.
The lift is idempotent because the backfill script reads from
the same `custom_fields` JSON the mappers write to and skips
contacts whose new column already carries a value.

GRADO_DE_INTERES / TIPO_DE_CENTRO / INTERES / PRODUCTOS_DE_INTERES /
EQUIPO_INTERESADO / INTERESADO_EN_DEMO / TITULARITAT_CENTRE /
ESTUDIS_ETIQUETES / FAIG_PPTO_ENVIADO / HORARIO stay in
`custom_fields` JSON — the UI's "Datos adicionales" section
renders them dynamically.

Subscription state (Brevo `BLOCKLISTED` / `EMAILABLE_UNSUBSCRIBED`)
materialises an `email_unsubscribes` row with `source='brevo'`
and an auto-minted token — this is handled by the mapper, no
schema change here.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0042"
down_revision: str | None = "20260615_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("job_title", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("personal_website", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("address_line", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column(
            "address_postal_code", sa.String(length=20), nullable=True
        ),
    )
    op.add_column(
        "contacts",
        sa.Column("address_region", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    for col in (
        "address_region",
        "address_postal_code",
        "address_line",
        "personal_website",
        "linkedin_url",
        "job_title",
    ):
        op.drop_column("contacts", col)
