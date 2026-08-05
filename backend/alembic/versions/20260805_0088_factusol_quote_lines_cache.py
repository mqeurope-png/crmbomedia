"""BoHub ERP Fase C (C-4) — caché de líneas de las proformas creadas por el CRM.

**F_PRE es MONO-LÍNEA**: verificado contra la base real de Bomedia (653
presupuestos del ejercicio 2026), cada fila de `F_PRE` es un presupuesto
COMPLETO y **no existe ninguna tabla `F_LPRE` de líneas**. El desglose vive
como texto libre en `REFPRE` (250 caracteres).

Eso rompe el caso «duplicar una proforma» y «cargar sus líneas al pedido»: si
el CRM no guarda el desglose por su cuenta, se pierde en cuanto se escribe en
FACTUSOL. Esta tabla es esa copia local.

Solo se rellena cuando la proforma la crea el CRM. Una proforma hecha en el
FACTUSOL de escritorio no tiene filas aquí → al leerla o duplicarla el modo
degrada a «simple» y se usa `REFPRE` como texto único.

Revision ID: 20260805_0088
Revises: 20260805_0087
Create Date: 2026-08-05 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0088"
down_revision: str | None = "20260805_0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factusol_quote_lines_cache",
        sa.Column("id", sa.String(36), primary_key=True),
        # CODPRE de F_PRE (numérico en FACTUSOL, se guarda como texto).
        sa.Column("factusol_codpre", sa.String(24), nullable=False),
        sa.Column("ejercicio", sa.String(4), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        # ARTLPC: código de artículo F_ART; vacío en líneas libres (servicios).
        sa.Column("artlpc", sa.String(64), nullable=False, server_default=""),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False,
                  server_default="0"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("iva_pct", sa.Numeric(5, 2), nullable=False,
                  server_default="21"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "factusol_codpre", "ejercicio", "position",
            name="uq_factusol_quote_line_position",
        ),
    )
    op.create_index(
        "idx_factusol_quote_lines_codpre",
        "factusol_quote_lines_cache", ["factusol_codpre", "ejercicio"],
    )


def downgrade() -> None:
    op.drop_index("idx_factusol_quote_lines_codpre",
                  table_name="factusol_quote_lines_cache")
    op.drop_table("factusol_quote_lines_cache")
