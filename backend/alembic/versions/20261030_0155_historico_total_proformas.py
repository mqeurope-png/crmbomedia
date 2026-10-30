"""ERP · Histórico de ventas: total y estado de cada documento (para su
ficha) y sitio para las proformas.

- `historico_documentos`: dos columnas nuevas, vacías, `total` (con
  impuestos) y `estado` (ESTFAC / ESTALB / ESTPRE tal cual). Se rellenan al
  volver a cargar cada ejercicio; hasta entonces la ficha suma las líneas.
- Las proformas (`F_PRE`) se vuelcan en las mismas tablas con
  `tipo = 'proforma'`: no hace falta tabla nueva. No cuentan como venta.

No toca datos existentes.

Revision ID: 20261030_0155
Revises: 20261030_0154
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0155"
down_revision = "20261030_0154"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("historico_documentos", sa.Column("total", sa.Numeric(14, 2), nullable=True))
    op.add_column("historico_documentos", sa.Column("estado", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("historico_documentos", "estado")
    op.drop_column("historico_documentos", "total")
