"""PR-OAuth-Permisos-Admin Item 12 — estado en user_google_integrations.

Revision ID: 20260626_0073
Revises: 20260626_0072
Create Date: 2026-06-26 19:30:00

Añade columnas de ciclo de vida a `user_google_integrations` para dejar
de BORRAR la fila cuando un refresh falla (invalid_grant) o cuando el
user pulsa "Desconectar Google". Ahora la fila se conserva con un
`status` que el sync/backfill respetan y que la UI usa para el banner
de reconexión.

Columnas nuevas:
  - status                 VARCHAR(32) NOT NULL DEFAULT 'active'
  - last_refresh_error     VARCHAR(255) NULL
  - last_refresh_error_at  DATETIME NULL
  - disconnect_audit_id    VARCHAR(36) NULL

Backfill: todas las filas existentes quedan con status='active' por el
server_default.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0073"
down_revision: str | None = "20260626_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table para que SQLite (tests) acepte el ALTER —
    # MySQL lo trata como ADD COLUMN normal.
    with op.batch_alter_table("user_google_integrations") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(
            sa.Column("last_refresh_error", sa.String(255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "last_refresh_error_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("disconnect_audit_id", sa.String(36), nullable=True)
        )

    # Backfill explícito por si alguna fila quedó con NULL (defensivo —
    # el server_default ya cubre las filas existentes en MySQL).
    op.execute(
        "UPDATE user_google_integrations SET status = 'active' "
        "WHERE status IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("user_google_integrations") as batch:
        batch.drop_column("disconnect_audit_id")
        batch.drop_column("last_refresh_error_at")
        batch.drop_column("last_refresh_error")
        batch.drop_column("status")
