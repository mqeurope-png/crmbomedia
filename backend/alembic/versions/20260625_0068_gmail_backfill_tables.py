"""Sprint-Backfill-Gmail — tablas gmail_backfill_jobs + email_message_attachments.

Revision ID: 20260625_0068
Revises: 20260625_0067
Create Date: 2026-06-25 13:00:00

Bart pidió 2026-06-25 cargar 3 años de Gmail histórico entre alias-
comercial ↔ email-contacto, asociado al `contact_id` correcto y al
`gmail_account_user_id` del comercial. Tabla de control con progreso
+ tabla de adjuntos en disco con FK al mensaje.

`gmail_backfill_jobs.mode` separa 'estimate' (cuenta sin escribir) de
'execute' (importa). El worker procesa ambos contra la misma cola
`gmail:backfill_historic`. La UI lanza primero estimate, muestra el
desglose, y solo entonces lanza execute.

`email_message_attachments` separa los adjuntos del JSON inline en
`email_messages.attachments_json` (que sigue ahí para metadata corto
en send-side). El backfill descarga binarios a disco y guarda 1 fila
por adjunto con storage_path relativo al volumen
`/var/lib/crmbo/attachments`.

`email_messages.imported_via` etiqueta el origen del row para que
queries operativas distingan 'historic_backfill' / 'sent_from_crm' /
'incoming_realtime'.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_0068"
down_revision: str | None = "20260625_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_backfill_jobs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("initiated_by_user_id", sa.String(36), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("total_estimated", sa.Integer(), nullable=True),
        sa.Column(
            "total_processed", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_imported", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_skipped", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_errors", sa.Integer(), nullable=False,
            server_default="0",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"], ["users.id"],
            name="fk_gmail_backfill_jobs_initiator",
        ),
    )
    op.create_index(
        "idx_gmail_backfill_jobs_status",
        "gmail_backfill_jobs",
        ["status"],
    )

    op.create_table(
        "email_message_attachments",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("storage_path", sa.String(500), nullable=True),
        sa.Column("gmail_attachment_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["email_messages.id"],
            ondelete="CASCADE",
            name="fk_email_message_attachments_message",
        ),
    )
    op.create_index(
        "idx_email_message_attachments_message",
        "email_message_attachments",
        ["message_id"],
    )

    op.add_column(
        "email_messages",
        sa.Column("imported_via", sa.String(40), nullable=True),
    )
    op.add_column(
        "email_messages",
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "idx_email_messages_imported_via",
        "email_messages",
        ["imported_via"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_email_messages_imported_via", table_name="email_messages"
    )
    op.drop_column("email_messages", "imported_at")
    op.drop_column("email_messages", "imported_via")
    op.drop_index(
        "idx_email_message_attachments_message",
        table_name="email_message_attachments",
    )
    op.drop_table("email_message_attachments")
    op.drop_index(
        "idx_gmail_backfill_jobs_status", table_name="gmail_backfill_jobs"
    )
    op.drop_table("gmail_backfill_jobs")
