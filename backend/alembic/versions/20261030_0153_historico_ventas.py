"""ERP · Histórico de ventas de FACTUSOL dentro de BoHub.

Cinco tablas nuevas, vacías: la carga las llena leyendo FACTUSOL (solo lee).

- `historico_ejercicios`: el estado de la carga de cada ejercicio.
- `historico_documentos`: cabeceras de factura y albarán (fecha, cliente, serie).
- `historico_lineas`: sus líneas (artículo, descripción, cantidad, precio, total;
  `es_producto` False en las líneas de nota).
- `historico_articulos`: el catálogo de cada ejercicio.
- `historico_familias`: las familias y su clase (máquina / consumible / otro).

No toca ninguna tabla existente.

Revision ID: 20261030_0153
Revises: 20261030_0152
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_0153"
down_revision = "20261030_0152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historico_ejercicios",
        sa.Column("ejercicio", sa.String(4), primary_key=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("cargado_at", sa.DateTime()),
        sa.Column("intento_at", sa.DateTime()),
        sa.Column("detalle", sa.String(255)),
        sa.Column("facturas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("albaranes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lineas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lineas_producto", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("articulos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segundos", sa.Numeric(8, 1)),
    )
    op.create_table(
        "historico_documentos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ejercicio", sa.String(4), nullable=False),
        sa.Column("tipo", sa.String(8), nullable=False),
        sa.Column("serie", sa.String(8), nullable=False),
        sa.Column("codigo", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.Date()),
        sa.Column("fecha_original", sa.String(32)),
        sa.Column("codcli", sa.String(32)),
        sa.Column("cliente_nombre", sa.String(255)),
        sa.Column("referencia", sa.String(120)),
        sa.UniqueConstraint("ejercicio", "tipo", "serie", "codigo", name="uq_historico_documento"),
    )
    op.create_index("ix_historico_documentos_ejercicio", "historico_documentos", ["ejercicio"])
    op.create_index("ix_historico_documentos_fecha", "historico_documentos", ["fecha"])
    op.create_index("ix_historico_documentos_codcli", "historico_documentos", ["codcli"])
    op.create_table(
        "historico_lineas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("documento_id", sa.Integer(),
                  sa.ForeignKey("historico_documentos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ejercicio", sa.String(4), nullable=False),
        sa.Column("posicion", sa.Integer()),
        sa.Column("codart", sa.String(64)),
        sa.Column("descripcion", sa.Text()),
        sa.Column("cantidad", sa.Numeric(14, 3)),
        sa.Column("precio", sa.Numeric(14, 4)),
        sa.Column("total", sa.Numeric(14, 2)),
        sa.Column("es_producto", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_historico_lineas_documento_id", "historico_lineas", ["documento_id"])
    op.create_index("ix_historico_lineas_ejercicio", "historico_lineas", ["ejercicio"])
    op.create_index("ix_historico_lineas_codart", "historico_lineas", ["codart"])
    op.create_index("ix_historico_lineas_es_producto", "historico_lineas", ["es_producto"])
    op.create_table(
        "historico_articulos",
        sa.Column("ejercicio", sa.String(4), primary_key=True),
        sa.Column("codart", sa.String(64), primary_key=True),
        sa.Column("descripcion", sa.String(255)),
        sa.Column("familia", sa.String(32)),
        sa.Column("equart", sa.String(64)),
    )
    op.create_index("ix_historico_articulos_familia", "historico_articulos", ["familia"])
    op.create_table(
        "historico_familias",
        sa.Column("codigo", sa.String(32), primary_key=True),
        sa.Column("descripcion", sa.String(255)),
        sa.Column("clase", sa.String(16)),
        sa.Column("clase_at", sa.DateTime()),
        sa.Column("clase_por", sa.String(255)),
    )


def downgrade() -> None:
    op.drop_table("historico_familias")
    op.drop_table("historico_articulos")
    op.drop_table("historico_lineas")
    op.drop_table("historico_documentos")
    op.drop_table("historico_ejercicios")
