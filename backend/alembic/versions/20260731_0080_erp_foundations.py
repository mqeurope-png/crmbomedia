"""BoHub ERP Fase A — cimientos: orders, exceptions, carriers, sku mapping,
settings + columnas FACTUSOL en companies/contacts.

Sprint 1 Fase A PR 1. Los "enums" van como VARCHAR (StrEnum
native_enum=False en los modelos) — mismo criterio que el resto del repo
para que SQLite (tests) y MySQL 8 (prod) se comporten igual. Los roles
PEDIDOS/SAT no necesitan migración (users.role ya es VARCHAR).

Revision ID: 20260731_0080
Revises: 20260731_0079
Create Date: 2026-07-31 14:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0080"
down_revision: str | None = "20260731_0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "carriers",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("has_api", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("adapter_class", sa.String(128), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "external_source", sa.String(24), nullable=False,
            server_default="manual",
        ),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("store_id", sa.String(36), nullable=True),
        sa.Column("order_number", sa.String(32), nullable=False),
        sa.Column("contact_id", sa.String(36), nullable=True),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column(
            "total_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column(
            "payment_status", sa.String(24), nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "preparation_status", sa.String(24), nullable=False,
            server_default="pending_review",
        ),
        sa.Column(
            "transport_status", sa.String(24), nullable=False,
            server_default="not_shipped",
        ),
        sa.Column(
            "invoice_status", sa.String(24), nullable=False,
            server_default="not_invoiced",
        ),
        sa.Column("carrier_id", sa.String(36), nullable=True),
        sa.Column("tracking_number", sa.String(64), nullable=True),
        sa.Column("packing_json", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(36), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["integration_accounts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["carrier_id"], ["carriers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("idx_orders_prep_status", "orders", ["preparation_status"])
    op.create_index(
        "idx_orders_source_external", "orders", ["external_source", "external_id"]
    )
    op.create_index("idx_orders_placed", "orders", ["placed_at"])
    op.create_index("ix_orders_order_number", "orders", ["order_number"])

    op.create_table(
        "order_lines",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_sku", sa.String(128), nullable=False),
        sa.Column("product_codart", sa.String(13), nullable=True),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False, server_default="21"),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])

    op.create_table(
        "order_status_history",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("domain", sa.String(24), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by_user_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_osh_order_domain", "order_status_history",
        ["order_id", "domain", "changed_at"],
    )

    op.create_table(
        "exceptions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("subtype", sa.String(32), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("reported_by_user_id", sa.String(36), nullable=True),
        sa.Column("assigned_to_user_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["reported_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("idx_exceptions_status_type", "exceptions", ["status", "type"])
    op.create_index("idx_exceptions_order", "exceptions", ["order_id"])
    op.create_index("idx_exceptions_assigned", "exceptions", ["assigned_to_user_id"])

    op.create_table(
        "product_sku_mapping",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("woo_sku", sa.String(128), nullable=False),
        sa.Column("store_id", sa.String(36), nullable=True),
        sa.Column("factusol_codart", sa.String(13), nullable=False),
        sa.Column("matched_by", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_user_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["integration_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("store_id", "woo_sku", name="uq_sku_mapping_store_sku"),
    )
    op.create_index(
        "idx_sku_mapping_codart", "product_sku_mapping", ["factusol_codart"]
    )

    op.create_table(
        "erp_settings",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "default_invoice_mode", sa.String(16), nullable=False,
            server_default="manual",
        ),
        sa.Column("auto_invoice_max_amount_eur", sa.Numeric(12, 2), nullable=True),
        sa.Column("default_carrier_id", sa.String(36), nullable=True),
        sa.Column("factusol_default_ejercicio", sa.String(4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["default_carrier_id"], ["carriers.id"], ondelete="SET NULL"
        ),
    )

    # Columnas FACTUSOL en companies/contacts (decisión nº4; el botón
    # «Crear en FACTUSOL» llega en Fase C, las columnas nacen ya).
    op.add_column(
        "companies", sa.Column("factusol_company_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "companies",
        sa.Column("factusol_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "companies", sa.Column("factusol_sync_source", sa.String(16), nullable=True)
    )
    op.add_column(
        "contacts", sa.Column("factusol_contact_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column(
            "factusol_is_primary", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("contacts", "factusol_is_primary")
    op.drop_column("contacts", "factusol_contact_id")
    op.drop_column("companies", "factusol_sync_source")
    op.drop_column("companies", "factusol_synced_at")
    op.drop_column("companies", "factusol_company_id")
    op.drop_table("erp_settings")
    op.drop_table("product_sku_mapping")
    op.drop_table("exceptions")
    op.drop_table("order_status_history")
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("carriers")
