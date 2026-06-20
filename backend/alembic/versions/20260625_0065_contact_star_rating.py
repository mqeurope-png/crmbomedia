"""PR-Consolidado — Star Rating: contacts.star_rating column.

Revision ID: 20260625_0065
Revises: 20260624_0064
Create Date: 2026-06-25 09:00:00

Bart quiere replicar el "Star Value" nativo de AgileCRM (1-5) en el
CRM con un campo dedicado, completamente independiente del
`lead_score` existente. La columna se rellena vía:

- Sync de AgileCRM (`mapper.py` lee `star_value` del payload).
- PATCH /api/contacts/{id} desde la UI (click directo en las
  estrellas, modal Editar, o cualquier widget que lo exponga).

Rango válido: 0-5. NULL = "sin valorar". 0 también = "sin valorar"
(equivalencia semántica para que el frontend pueda "desmarcar" todas
las estrellas con un click sobre la primera ya marcada).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_0065"
down_revision: str | None = "20260624_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "star_rating",
            sa.SmallInteger(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("contacts", "star_rating")
