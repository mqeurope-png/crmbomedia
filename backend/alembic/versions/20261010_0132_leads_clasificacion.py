"""Respuesta a leads · Fase 1 — `lead_classifications` y los campos del contacto.

- `lead_classifications`: una fila por lead clasificado (lo que entró, lo que
  salió, de dónde salió cada dato, lo que se preparó y la corrección a mano).
  `(source, source_ref)` única: un lead se clasifica una vez.
- `contacts.lead_interest / lead_is_spam / lead_confidence /
  lead_classified_at`: copia de lo esencial para que las condiciones de los
  workflows y los filtros bifurquen sin JOIN.

Tabla nueva y columnas NULL, sin datos que migrar. Sin DEFAULT en TEXT (MySQL
lo rechaza): el ORM siempre da valor.

Revision ID: 20261010_0132
Revises: 20261010_0131
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261010_0132"
down_revision = "20261010_0131"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=5), nullable=True),
        sa.Column("language_source", sa.String(length=16), nullable=True),
        sa.Column("language_mismatch", sa.Boolean(), nullable=False),
        sa.Column("form_language", sa.String(length=5), nullable=True),
        sa.Column("interest", sa.String(length=40), nullable=True),
        sa.Column("interest_source", sa.String(length=16), nullable=True),
        sa.Column("is_spam", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("status_detail", sa.String(length=200), nullable=True),
        sa.Column("template_id", sa.String(length=36), nullable=True),
        sa.Column("template_name", sa.String(length=200), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("draft_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=True),
        sa.Column("corrected_language", sa.String(length=5), nullable=True),
        sa.Column("corrected_interest", sa.String(length=40), nullable=True),
        sa.Column("corrected_is_spam", sa.Boolean(), nullable=True),
        sa.Column("corrected_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("source", "source_ref", name="uq_lead_classifications_source_ref"),
    )
    op.create_index(
        "ix_lead_classifications_contact", "lead_classifications",
        ["contact_id", "created_at"],
    )
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("lead_interest", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("lead_is_spam", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("lead_confidence", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("lead_classified_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_column("lead_classified_at")
        batch.drop_column("lead_confidence")
        batch.drop_column("lead_is_spam")
        batch.drop_column("lead_interest")
    op.drop_index("ix_lead_classifications_contact", table_name="lead_classifications")
    op.drop_table("lead_classifications")
