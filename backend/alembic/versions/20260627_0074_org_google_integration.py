"""PR-OAuth-Google-Unificado — org_google_integration + user_calendar_prefs.

Revision ID: 20260627_0074
Revises: 20260626_0073
Create Date: 2026-06-27 09:00:00

Unifica las 6 conexiones Google per-user en UNA org-wide. Crea:
  - `org_google_integration` (singleton, id='singleton') con los tokens
    compartidos.
  - `user_calendar_prefs` (per-user) para preservar el calendario que
    cada user eligió.

Migración de datos:
  - Toma la fila de `user_google_integrations` con status='active' MÁS
    RECIENTE (por connected_at desc) → sus tokens son los válidos →
    se insertan en `org_google_integration`.
  - Copia `selected_calendar_id/summary` de cada user a
    `user_calendar_prefs`.

`user_google_integrations` se conserva (no se borra) para histórico; ya
no se usa para tokens.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0074"
down_revision: str | None = "20260626_0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_google_integration",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("google_email", sa.String(255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "token_expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_by_user_id", sa.String(36), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="active"
        ),
        sa.Column("last_refresh_error", sa.String(255), nullable=True),
        sa.Column(
            "last_refresh_error_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("disconnect_audit_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "user_calendar_prefs",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("selected_calendar_id", sa.String(255), nullable=True),
        sa.Column("selected_calendar_summary", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    bind = op.get_bind()
    now = datetime.now(UTC)

    # 1. Migrar la integración per-user más reciente y activa → org.
    # `user_google_integrations` usa `user_id` (no `connected_by_user_id`),
    # así que lo aliasamos. Preferimos la fila active más reciente; si no
    # hay ninguna active, caemos a la más reciente de cualquier estado.
    row = bind.execute(
        sa.text(
            "SELECT google_email, access_token_encrypted, "
            "refresh_token_encrypted, token_expires_at, scopes, "
            "connected_at, user_id AS connected_by_user_id, "
            "last_sync_at, status "
            "FROM user_google_integrations "
            "WHERE status = 'active' "
            "ORDER BY connected_at DESC LIMIT 1"
        )
    ).fetchone()
    if row is None:
        row = bind.execute(
            sa.text(
                "SELECT google_email, access_token_encrypted, "
                "refresh_token_encrypted, token_expires_at, scopes, "
                "connected_at, user_id AS connected_by_user_id, "
                "last_sync_at, status "
                "FROM user_google_integrations "
                "ORDER BY connected_at DESC LIMIT 1"
            )
        ).fetchone()

    if row is not None:
        bind.execute(
            sa.text(
                "INSERT INTO org_google_integration "
                "(id, google_email, access_token_encrypted, "
                " refresh_token_encrypted, token_expires_at, scopes, "
                " connected_at, connected_by_user_id, last_sync_at, status, "
                " created_at, updated_at) "
                "VALUES (:id, :email, :at, :rt, :exp, :scopes, :conn, "
                "        :by, :sync, :status, :now, :now)"
            ),
            {
                "id": "singleton",
                "email": row.google_email,
                "at": row.access_token_encrypted,
                "rt": row.refresh_token_encrypted,
                "exp": row.token_expires_at,
                "scopes": row.scopes,
                "conn": row.connected_at,
                "by": getattr(row, "connected_by_user_id", None),
                "sync": row.last_sync_at,
                "status": row.status or "active",
                "now": now,
            },
        )

    # 2. Copiar el calendario seleccionado de cada user → user_calendar_prefs.
    cal_rows = bind.execute(
        sa.text(
            "SELECT user_id, selected_calendar_id, selected_calendar_summary "
            "FROM user_google_integrations "
            "WHERE selected_calendar_id IS NOT NULL"
        )
    ).fetchall()
    for cal in cal_rows:
        bind.execute(
            sa.text(
                "INSERT INTO user_calendar_prefs "
                "(user_id, selected_calendar_id, selected_calendar_summary, "
                " created_at, updated_at) "
                "VALUES (:uid, :cid, :csum, :now, :now)"
            ),
            {
                "uid": cal.user_id,
                "cid": cal.selected_calendar_id,
                "csum": cal.selected_calendar_summary,
                "now": now,
            },
        )

    _ = uuid  # reservado por si se necesitan ids generados.


def downgrade() -> None:
    op.drop_table("user_calendar_prefs")
    op.drop_table("org_google_integration")
