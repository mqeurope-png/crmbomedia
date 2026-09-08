"""ERP-F4-A — conciliación bancaria: cuentas, movimientos, conciliaciones y
reglas aprendidas.

Cuatro tablas nuevas. Este PR NO escribe en FACTUSOL (registrar el cobro en
F_COB/F_LCO va en F-4-B): aquí se importa el extracto, se casa con las
facturas pendientes, Bart confirma y se exporta el Excel con las columnas
rellenas. Una sola cabeza.

Revision ID: 20260908_0098
Revises: 20260907_0097
Create Date: 2026-09-08 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0098"
down_revision: str | None = "20260907_0097"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("iban", sa.String(length=34), nullable=False),
        sa.Column("bic", sa.String(length=11), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"),
        sa.Column("serie", sa.Integer(), nullable=True),
        sa.Column("column_mapping_json", sa.Text(), nullable=True),
        sa.Column("statement_header_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("iban", name="uq_bank_accounts_iban"),
    )
    op.create_table(
        "bank_movements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "account_id", sa.String(length=36),
            sa.ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("fecha_oper", sa.Date(), nullable=False),
        sa.Column("fecha_valor", sa.Date(), nullable=True),
        sa.Column("concepto", sa.Text(), nullable=False),
        sa.Column("importe", sa.Numeric(14, 2), nullable=False),
        sa.Column("saldo", sa.Numeric(14, 2), nullable=True),
        sa.Column("referencia1", sa.String(length=255), nullable=True),
        sa.Column("referencia2", sa.String(length=255), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("payer_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("discard_reason", sa.String(length=255), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bank_movements_account_id", "bank_movements", ["account_id"])
    op.create_index("ix_bank_movements_fecha_oper", "bank_movements", ["fecha_oper"])
    op.create_index("ix_bank_movements_dedupe_key", "bank_movements", ["dedupe_key"])
    op.create_index("ix_bank_movements_status", "bank_movements", ["status"])
    op.create_table(
        "bank_reconciliations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "movement_id", sa.String(length=36),
            sa.ForeignKey("bank_movements.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.Integer(), nullable=False),
        sa.Column("numero", sa.String(length=20), nullable=False),
        sa.Column("cliente_nombre", sa.String(length=255), nullable=True),
        sa.Column("importe", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bank_reconciliations_movement_id", "bank_reconciliations", ["movement_id"])
    op.create_index("ix_bank_reconciliations_status", "bank_reconciliations", ["status"])
    op.create_table(
        "bank_learned_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("pattern", sa.String(length=255), nullable=False),
        sa.Column("client_codcli", sa.String(length=36), nullable=True),
        sa.Column("client_nombre", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bank_learned_rules_kind", "bank_learned_rules", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_bank_learned_rules_kind", table_name="bank_learned_rules")
    op.drop_table("bank_learned_rules")
    op.drop_index("ix_bank_reconciliations_status", table_name="bank_reconciliations")
    op.drop_index("ix_bank_reconciliations_movement_id", table_name="bank_reconciliations")
    op.drop_table("bank_reconciliations")
    for name in ("ix_bank_movements_status", "ix_bank_movements_dedupe_key",
                 "ix_bank_movements_fecha_oper", "ix_bank_movements_account_id"):
        op.drop_index(name, table_name="bank_movements")
    op.drop_table("bank_movements")
    op.drop_table("bank_accounts")
