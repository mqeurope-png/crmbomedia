"""PR-Fix-Sync-Dispara-Reglas-Workflows — origin_account_id column.

Revision ID: 20260624_0064
Revises: 20260623_0063
Create Date: 2026-06-24 09:00:00

Las reglas de asignación + workflows necesitan filtrar por la cuenta
de origen específica del contacto (`agilecrm:default` vs
`agilecrm:boprint`). Hasta ahora el dato vivía solo en
`external_refs.account_id` y los evaluadores tenían que hacer JOIN
para acceder. Bart confirmó que sus rules con
`field=origin_account_id value=agilecrm:default` no matchean porque
el segments engine devuelve solo el `account_id` ("default") sin el
prefijo del sistema.

Solución: añadir `contacts.origin_account_id` (String(120), nullable)
con formato `{system_label}:{account_id}` (e.g. `"agilecrm:default"`,
`"brevo:gallery-prod"`). La migración hace backfill leyendo
external_refs ordenado por created_at (el primero gana cuando hay
multi-cuenta).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "20260624_0064"
down_revision: str | None = "20260623_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "origin_account_id",
            sa.String(length=120),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_contacts_origin_account_id",
        "contacts",
        ["origin_account_id"],
    )
    # Backfill: el external_ref más antiguo gana cuando un contacto
    # vive en varias cuentas (multi-account dedup). El formato es
    # `{system}:{account_id}` con system en lowercase para que matche
    # los rules ya autorizados (`"agilecrm:default"`).
    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE contacts c
            JOIN (
                SELECT
                    er.contact_id,
                    MIN(er.created_at) AS first_created
                FROM external_references er
                GROUP BY er.contact_id
            ) AS first_ref ON first_ref.contact_id = c.id
            JOIN external_references er2
                ON er2.contact_id = first_ref.contact_id
               AND er2.created_at = first_ref.first_created
            SET c.origin_account_id =
                CONCAT(LOWER(er2.system), ':', er2.account_id)
            WHERE c.origin_account_id IS NULL
            """
        )
        if bind.dialect.name == "mysql"
        else text(
            # SQLite/Postgres: estilo más portable. UPDATE con join via
            # subquery + COALESCE de NULL.
            """
            UPDATE contacts
            SET origin_account_id = (
                SELECT LOWER(er.system) || ':' || er.account_id
                FROM external_references er
                WHERE er.contact_id = contacts.id
                ORDER BY er.created_at ASC
                LIMIT 1
            )
            WHERE origin_account_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_contacts_origin_account_id", table_name="contacts")
    op.drop_column("contacts", "origin_account_id")
