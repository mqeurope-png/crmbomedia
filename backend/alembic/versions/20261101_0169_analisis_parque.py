"""CRM · El análisis del parque de una empresa (lo que escribe la IA sobre lo
calculado), guardado para no generarlo cada vez.

`crm_analisis_empresa`: uno por empresa, con la huella de los datos de entrada
(si cambian, se regenera), cuándo se generó, con qué modelo, lo que costó y el
contenido. Solo esquema: no toca datos.

Revision ID: 20261101_0169
Revises: 20261101_0168
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20261101_0169"
down_revision = "20261101_0168"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_analisis_empresa",
        sa.Column("company_id", sa.String(36),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("generado_at", sa.DateTime(), nullable=False),
        sa.Column("modelo", sa.String(80), nullable=True),
        sa.Column("contenido_json", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
                  nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=True),
        sa.Column("tokens_salida", sa.Integer(), nullable=True),
        sa.Column("coste_usd", sa.Float(), nullable=True),
        sa.Column("por", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("crm_analisis_empresa")
