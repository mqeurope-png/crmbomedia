"""PR-Fix-Dedup-Key-Varchar — widen workflow_runs.active_dedup_key.

Revision ID: 20260622_0062
Revises: 20260621_0061
Create Date: 2026-06-22 08:30:00

La columna nació en VARCHAR(80) cubriendo el caso `{workflow_id}:
{contact_id}` (73 chars). Cuando el endpoint `manual_add_contact`
usa la variante con `run_id` extra (`{workflow_id}:{contact_id}:
{run_id}` = 110 chars), MySQL devolvía `Data too long for column
'active_dedup_key'` y el endpoint respondía 500.

Subimos a VARCHAR(120) — margen para cualquier triple-UUID +
prefijo `archived:` del archivado de runs. El índice UNIQUE
`uq_workflow_runs_dedup` se preserva tal cual: MySQL acepta índices
sobre VARCHAR(120) sin problema (sigue por debajo del límite de
767 bytes con utf8mb4 en MySQL ≥ 5.7 + innodb_large_prefix, y muy
por debajo del 3072 que usa Bohub en producción).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_0062"
down_revision: str | None = "20260621_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "workflow_runs",
        "active_dedup_key",
        existing_type=sa.String(length=80),
        type_=sa.String(length=120),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "workflow_runs",
        "active_dedup_key",
        existing_type=sa.String(length=120),
        type_=sa.String(length=80),
        existing_nullable=False,
    )
