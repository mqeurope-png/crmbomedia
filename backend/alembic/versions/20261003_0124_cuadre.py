"""ERP · Cuadre — tablas `cuadre_findings` y `cuadre_runs`.

El panel de descuadres entre BoHub, FACTUSOL, Genei y la hoja de Drive es
SOLO LECTURA: estas tablas guardan lo que encuentran las comprobaciones y su
revisión («revisado / no es un descuadre» + motivo), nunca corrigen datos.

- `cuadre_findings`: un descuadre por `check_id + entidad_id` (única). Una
  pasada que ya no lo ve lo pasa a `resuelto` (nunca se borra). `huella` es el
  hash de los valores que lo motivan: un revisado no reaparece mientras no
  cambie.
- `cuadre_runs`: cada pasada (nocturna o «Comprobar ahora») por fuente.

Tablas nuevas, sin datos que migrar. Sin DEFAULT en las columnas TEXT (MySQL
lo rechaza, error 1101): el ORM siempre da valor.

Revision ID: 20261003_0124
Revises: 20261001_0123
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261003_0124"
down_revision = "20261001_0123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cuadre_findings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("check_id", sa.String(length=40), nullable=False),
        sa.Column("entidad_tipo", sa.String(length=20), nullable=False),
        sa.Column("entidad_id", sa.String(length=64), nullable=False),
        sa.Column("huella", sa.String(length=64), nullable=False),
        sa.Column("detalle_json", sa.Text(), nullable=False),
        sa.Column("severidad", sa.String(length=10), nullable=False),
        sa.Column("estado", sa.String(length=12), nullable=False),
        sa.Column("visto_por", sa.String(length=36), nullable=True),
        sa.Column("motivo", sa.String(length=255), nullable=True),
        sa.Column("revisado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primera_vez_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultima_vez_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("abierto_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resuelto_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_id", "entidad_id", name="uq_cuadre_finding_entidad"),
    )
    op.create_index(
        "ix_cuadre_findings_estado_check", "cuadre_findings", ["estado", "check_id"],
    )
    op.create_table(
        "cuadre_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("fuente", sa.String(length=10), nullable=False),
        sa.Column("origen", sa.String(length=10), nullable=False),
        sa.Column("estado", sa.String(length=12), nullable=False),
        sa.Column("lanzado_por", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumen_json", sa.Text(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cuadre_runs_estado", "cuadre_runs", ["estado"])
    op.create_index("ix_cuadre_runs_finished_at", "cuadre_runs", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_cuadre_runs_finished_at", table_name="cuadre_runs")
    op.drop_index("ix_cuadre_runs_estado", table_name="cuadre_runs")
    op.drop_table("cuadre_runs")
    op.drop_index("ix_cuadre_findings_estado_check", table_name="cuadre_findings")
    op.drop_table("cuadre_findings")
