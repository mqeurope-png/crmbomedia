"""BoHub ERP Fase D (D-1) — expedición manual: bultos + ficheros de envío.

Crea `shipment_packages` (multi-bulto por pedido) y `shipment_files` (albaranes
y etiquetas: descargados de Woo o subidos a mano; solo la ruta relativa vive en
BD, los bytes en el storage). Ambas con cascada al borrar el pedido.

Revision ID: 20260805_0085
Revises: 20260804_0084
Create Date: 2026-08-05 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0085"
down_revision: str | None = "20260804_0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipment_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id", sa.String(36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("weight_kg", sa.Numeric(6, 2), nullable=False),
        sa.Column("height_cm", sa.Integer(), nullable=False),
        sa.Column("width_cm", sa.Integer(), nullable=False),
        sa.Column("depth_cm", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_shipment_packages_order_id", "shipment_packages", ["order_id"],
    )

    op.create_table(
        "shipment_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id", sa.String(36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column(
            "uploaded_by_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_shipment_files_order_id", "shipment_files", ["order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_shipment_files_order_id", table_name="shipment_files")
    op.drop_table("shipment_files")
    op.drop_index("ix_shipment_packages_order_id", table_name="shipment_packages")
    op.drop_table("shipment_packages")
