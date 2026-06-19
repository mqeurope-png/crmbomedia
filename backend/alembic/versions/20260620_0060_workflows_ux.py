"""Sprint Workflows — UX editor: display_name + definition_hash.

Revision ID: 20260620_0060
Revises: 20260620_0059
Create Date: 2026-06-20 13:00:00

Dos columnas nuevas para soportar las mejoras de UX del editor:

- `workflow_steps.display_name` — nombre custom que el operador asigna
  al hacer doble-click sobre un nodo. NULL → el frontend calcula el
  nombre vía `humanizeStepConfig()` automáticamente.

- `workflows.definition_hash` — SHA-256 (truncado a 16 bytes hex = 32
  chars) de la definición estructural (trigger + steps + edges). Lo
  usamos para detectar duplicados exactos al guardar/activar sin tener
  que comparar JSONs enteros. Recalculado en cada save estructural.

Sin DEFAULT en columnas (lección del PR-204: MySQL no admite default en
TEXT). Ambas columnas nullable porque legacy rows ya existentes no
tendrán ni nombre custom ni hash hasta que se editen.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260620_0060"
down_revision: str | None = "20260620_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_steps",
        sa.Column("display_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("definition_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_workflows_definition_hash",
        "workflows",
        ["definition_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflows_definition_hash", table_name="workflows"
    )
    op.drop_column("workflows", "definition_hash")
    op.drop_column("workflow_steps", "display_name")
