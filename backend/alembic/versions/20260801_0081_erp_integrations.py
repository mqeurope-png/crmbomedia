"""BoHub ERP Fase B — integraciones live: integration_events + extensiones
integration_accounts (WooCommerce multi-tienda) y carriers (Genei API).

Sprint 2 Fase B PR B-1. Todas las columnas nuevas son NULLable → cero
efecto sobre filas existentes (AgileCRM/Brevo/Freshdesk siguen igual).

Revision ID: 20260801_0081
Revises: 20260731_0080
Create Date: 2026-08-01 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0081"
down_revision: str | None = "20260731_0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Inbox de webhooks — dedup por (system, account_id, external_event_id).
    op.create_table(
        "integration_events",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="received"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "system", "account_id", "external_event_id",
            name="uq_integration_events_dedup",
        ),
    )
    op.create_index(
        "idx_integration_events_status_retry", "integration_events",
        ["status", "next_retry_at"],
    )

    # 2. integration_accounts: multi-tienda WooCommerce (base_url +
    # consumer_key/secret cifrados) + metadata para saco genérico.
    op.add_column(
        "integration_accounts", sa.Column("base_url", sa.String(255), nullable=True)
    )
    op.add_column(
        "integration_accounts",
        sa.Column("consumer_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("consumer_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "integration_accounts", sa.Column("metadata_json", sa.Text(), nullable=True)
    )

    # 3. carriers: credenciales del adaptador (Genei API), URL base,
    # secret opcional de webhooks firmados y addressId origen.
    op.add_column(
        "carriers",
        sa.Column("api_credentials_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "carriers", sa.Column("api_base_url", sa.String(255), nullable=True)
    )
    op.add_column(
        "carriers",
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "carriers", sa.Column("default_address_id", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("carriers", "default_address_id")
    op.drop_column("carriers", "webhook_secret_encrypted")
    op.drop_column("carriers", "api_base_url")
    op.drop_column("carriers", "api_credentials_encrypted")
    op.drop_column("integration_accounts", "metadata_json")
    op.drop_column("integration_accounts", "consumer_secret_encrypted")
    op.drop_column("integration_accounts", "consumer_key_encrypted")
    op.drop_column("integration_accounts", "base_url")
    op.drop_index(
        "idx_integration_events_status_retry", table_name="integration_events"
    )
    op.drop_table("integration_events")
