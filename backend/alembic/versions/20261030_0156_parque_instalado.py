"""ERP · Parque instalado: máquinas añadidas a mano y bajas.

- `parque_maquinas_manuales`: las máquinas que no salen de las ventas
  volcadas (antes de 2014, vendidas como línea de nota, compradas en otro
  sitio), por empresa, con su propia baja.
- `parque_bajas`: las máquinas de las ventas volcadas dadas de baja, por una
  clave estable a las recargas (`ejercicio|tipo|serie|codigo|posicion|codart`).

Dos tablas nuevas y vacías. No toca ninguna existente.

Revision ID: 20261030_0156
Revises: 20261030_0155
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0156"
down_revision = "20261030_0155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parque_maquinas_manuales",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("codart", sa.String(64), nullable=True),
        sa.Column("descripcion", sa.String(255), nullable=False),
        sa.Column("gama", sa.String(32), nullable=True),
        sa.Column("fecha", sa.Date, nullable=True),
        sa.Column("nota", sa.String(255), nullable=True),
        sa.Column("creado_por", sa.String(255), nullable=True),
        sa.Column("creado_at", sa.DateTime, nullable=True),
        sa.Column("baja_at", sa.DateTime, nullable=True),
        sa.Column("baja_motivo", sa.String(255), nullable=True),
        sa.Column("baja_por", sa.String(255), nullable=True),
    )
    op.create_index("ix_parque_maquinas_manuales_company_id", "parque_maquinas_manuales",
                    ["company_id"])
    op.create_table(
        "parque_bajas",
        sa.Column("clave", sa.String(160), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("motivo", sa.String(255), nullable=True),
        sa.Column("por", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_parque_bajas_company_id", "parque_bajas", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_parque_bajas_company_id", table_name="parque_bajas")
    op.drop_table("parque_bajas")
    op.drop_index("ix_parque_maquinas_manuales_company_id",
                  table_name="parque_maquinas_manuales")
    op.drop_table("parque_maquinas_manuales")
