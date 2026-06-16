"""unify notes — add source/pinned/created_by_user_id to notes,
migrate the single contact_notes row, drop contact_notes.

Revision ID: 20260616_0049
Revises: 20260616_0048
Create Date: 2026-06-16 17:05:00

QoL hot-fix follow-up. Pre-this: el CRM tenía DOS tablas de notas:

- `notes` (~285 filas, todas Agile timeline desde `/contacts/{id}/
  notes`) con `body`, `external_author_*`, `external_created_at`.
  La que pintaba la TAB "Notas" de la ficha.
- `contact_notes` (1 fila manual de prueba) con `content`, `source`,
  `pinned`, `created_by_user_id`. La que pintaba la SECCIÓN lateral
  "Notas" y la que el filtro `notes_content` consultaba — por eso
  Bart reportó que el filtro no encontraba las notas de Vitali.

Tras este migration: una sola tabla `notes` con las columnas útiles
de `contact_notes` añadidas, la 1 fila manual movida, y
`contact_notes` borrada.

El backfill SQL es directo porque el universo de origen es <5 filas
en prod (1 fila de prueba) y la dedupe por (contact_id, body) es
trivial. La operación es atómica en una sola transacción Alembic.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260616_0049"
down_revision: str | None = "20260616_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add the 3 columns from contact_notes that we actually use.
    op.add_column(
        "notes",
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "notes",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "notes",
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
    )

    # 2. Mark existing rows with a useful source. Anything imported from
    # AgileCRM timeline gets `agile:timeline`; manual / unknown stays at
    # the default `manual`.
    op.execute(
        "UPDATE notes SET source = 'agile:timeline' "
        "WHERE external_system = 'agilecrm'"
    )

    # 3. Move the lone manual row from contact_notes → notes. Using a
    # parametrised INSERT...SELECT keeps the timestamps + content
    # intact. `external_*` columns stay NULL for manual rows.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_contact_notes = "contact_notes" in inspector.get_table_names()
    if has_contact_notes:
        # Detect the columns dynamically — older snapshots may not
        # have the same shape if the table was prototyped manually.
        existing_cols = {
            col["name"] for col in inspector.get_columns("contact_notes")
        }
        wants = {
            "id",
            "contact_id",
            "content",
            "source",
            "pinned",
            "created_by_user_id",
            "created_at",
            "updated_at",
        }
        cols = wants & existing_cols
        col_list = ", ".join(sorted(cols))
        rows = bind.execute(
            sa.text(f"SELECT {col_list} FROM contact_notes")  # noqa: S608
        ).mappings().all()
        now = datetime.now(UTC)
        for row in rows:
            # `body` is required on notes; map from `content`.
            body = (row.get("content") or "").strip()
            if not body:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO notes (
                        id, contact_id, body, author_user_id,
                        source, pinned, created_by_user_id,
                        created_at, updated_at
                    ) VALUES (
                        :id, :contact_id, :body, :author_user_id,
                        :source, :pinned, :created_by_user_id,
                        :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": row["id"],
                    "contact_id": row["contact_id"],
                    "body": body,
                    "author_user_id": row.get("created_by_user_id"),
                    "source": row.get("source") or "manual",
                    "pinned": bool(row.get("pinned")),
                    "created_by_user_id": row.get("created_by_user_id"),
                    "created_at": row.get("created_at") or now,
                    "updated_at": row.get("updated_at") or now,
                },
            )

        # 4. Drop the now-empty contact_notes table.
        op.drop_table("contact_notes")


def downgrade() -> None:
    # Re-create contact_notes (minimal shape — matches sprint-Empresas
    # sub-PR 4/4) and migrate qualifying rows back.
    op.create_table(
        "contact_notes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE"
        ),
    )
    op.execute(
        """
        INSERT INTO contact_notes (
            id, contact_id, content, source, pinned,
            created_by_user_id, created_at, updated_at
        )
        SELECT id, contact_id, body, source, pinned,
               created_by_user_id, created_at, updated_at
        FROM notes
        WHERE source = 'manual' OR source LIKE 'agile:Note%'
        """
    )
    op.drop_column("notes", "created_by_user_id")
    op.drop_column("notes", "pinned")
    op.drop_column("notes", "source")
