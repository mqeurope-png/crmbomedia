"""ERP · Cuadre — columnas más anchas: `cuadre_runs.fuente` 10 → 32 y
`cuadre_findings.check_id` 40 → 64.

#516 añadió la fuente `woocommerce` (11 caracteres): con `varchar(10)` «Comprobar
ahora» daba 500 (`Data too long for column 'fuente'`). En producción se amplió a
mano el 07/10/2026 (también `check_id`, por si las comprobaciones nuevas traen
identificadores largos); esta migración lo deja en el repositorio para que un
entorno creado desde cero no vuelva a romperse. Ampliar es idempotente: en
producción no cambia nada.

Revision ID: 20261007_0125
Revises: 20261003_0124
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261007_0125"
down_revision = "20261003_0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cuadre_runs") as batch:
        batch.alter_column("fuente", existing_type=sa.String(length=10),
                           type_=sa.String(length=32), existing_nullable=False)
    with op.batch_alter_table("cuadre_findings") as batch:
        batch.alter_column("check_id", existing_type=sa.String(length=40),
                           type_=sa.String(length=64), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("cuadre_findings") as batch:
        batch.alter_column("check_id", existing_type=sa.String(length=64),
                           type_=sa.String(length=40), existing_nullable=False)
    with op.batch_alter_table("cuadre_runs") as batch:
        batch.alter_column("fuente", existing_type=sa.String(length=32),
                           type_=sa.String(length=10), existing_nullable=False)
