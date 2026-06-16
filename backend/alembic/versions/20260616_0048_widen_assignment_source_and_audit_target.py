"""widen contact_assignments.source + audit_logs.target_id

Revision ID: 20260616_0048
Revises: 20260616_0047
Create Date: 2026-06-16 07:55:00

Sprint Reglas-Assign — PR-Ca hotfix.

Prod (MySQL strict mode) reventó al crear contactos:
    pymysql.err.DataError (1406, "Data too long for column ...")

- `contact_assignments.source` era VARCHAR(40). El motor de reglas
  escribe `source = f"rule:{rule.id}"` que son 41 chars (5 + 36 UUID).
  Ampliamos a 80 — espacio suficiente para cualquier prefijo razonable
  ("rule:<uuid>", "brevo:account:<uuid>", etc).
- `audit_logs.target_id` era VARCHAR(36) (pensado para UUIDs sueltos).
  El error en prod apuntaba a esta columna; sea cual sea el caller que
  pasa algo > 36 chars (composite id, label preformateado), 120 es un
  techo razonable que empareja `target_type` sin coste apreciable en
  MySQL VARCHAR.

Sin pérdida de datos: las columnas crecen, no se truncan.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260616_0048"
down_revision: str | None = "20260616_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "contact_assignments",
        "source",
        existing_type=sa.String(length=40),
        type_=sa.String(length=80),
        existing_nullable=False,
        existing_server_default="manual",
    )
    op.alter_column(
        "audit_logs",
        "target_id",
        existing_type=sa.String(length=36),
        type_=sa.String(length=120),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "target_id",
        existing_type=sa.String(length=120),
        type_=sa.String(length=36),
        existing_nullable=True,
    )
    op.alter_column(
        "contact_assignments",
        "source",
        existing_type=sa.String(length=80),
        type_=sa.String(length=40),
        existing_nullable=False,
        existing_server_default="manual",
    )
