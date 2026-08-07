"""CRM · CRM-GMAIL — captura universal + spam + alias por usuario.

Tres cambios de esquema para el sprint Gmail (captura universal + real-time
+ sync de spam + filtro por alias por comercial):

1. Tabla nueva `user_email_aliases` — registro de PROPIEDAD del correo
   entrante por alias. Cada `alias_email` pertenece a exactamente un usuario
   (UNIQUE global). Distinta de `user_email_alias_prefs` (preferencias
   Send-As outbound, no únicas globalmente): esta define de quién es la
   bandeja del correo que LLEGA a un alias, y alimenta tanto la captura
   universal del sync como el filtro de visibilidad por comercial.

2. `email_messages` gana `is_spam` / `delivered_to` / `gmail_labels`:
   - `is_spam` (bool, server_default false) — refleja la label SPAM de Gmail;
     no oculta el email, lo marca. Se sincroniza por webhook.
   - `delivered_to` (varchar 320, indexado) — alias del CRM al que llegó el
     mail; base del filtro por comercial.
   - `gmail_labels` (JSON en Text) — labelIds de Gmail para debug.

3. Data-migration (idempotente, se salta en base vacía):
   - Semilla `user_email_aliases` desde `users.email` de los usuarios
     activos (users.email es UNIQUE → cumple el unique global del alias).
     Cada usuario arranca «dueño» de su propia dirección; admin puede
     añadir/quitar/togglear después desde la UI. Log de cuántos se sembraron.
   - Backfill de `delivered_to` en emails existentes: casa To/Cc/Bcc contra
     los alias sembrados; los que no casan quedan NULL (visibles solo a admin
     hasta reprocesarse). Log del recuento.

Revision ID: 20260807_0090
Revises: 20260806_0089
Create Date: 2026-08-07 10:00:00
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0090"
down_revision: str | None = "20260806_0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.gmail_universal_capture")


def upgrade() -> None:
    # --- Parte A: tabla de alias entrante por usuario -------------------
    op.create_table(
        "user_email_aliases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("alias_email", sa.String(length=320), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "alias_email", name="uq_user_email_aliases_alias_email"
        ),
    )
    op.create_index(
        "ix_user_email_aliases_user_id", "user_email_aliases", ["user_id"]
    )
    op.create_index(
        "ix_user_email_aliases_active", "user_email_aliases", ["active"]
    )

    # --- Parte B: columnas nuevas en email_messages ---------------------
    op.add_column(
        "email_messages",
        sa.Column(
            "is_spam", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "email_messages",
        sa.Column("delivered_to", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "email_messages",
        sa.Column("gmail_labels", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_email_messages_is_spam", "email_messages", ["is_spam"]
    )
    op.create_index(
        "ix_email_messages_delivered_to", "email_messages", ["delivered_to"]
    )

    # --- Parte A/B data-migration --------------------------------------
    bind = op.get_bind()
    seeded = _seed_aliases(bind)
    backfilled = _backfill_delivered_to(bind)
    logger.info(
        "gmail_universal_capture: seeded %s aliases, backfilled delivered_to "
        "on %s emails",
        seeded,
        backfilled,
    )


def _seed_aliases(bind: sa.engine.Connection) -> int:
    """One alias per active user, from users.email. users.email is UNIQUE so
    the global-unique constraint on alias_email holds. Idempotent: skips any
    email already present in user_email_aliases."""
    users = bind.execute(
        sa.text(
            "SELECT id, email FROM users "
            "WHERE is_active = :active AND email IS NOT NULL AND email <> ''"
        ),
        {"active": True},
    ).fetchall()
    if not users:
        return 0
    existing = {
        row[0].lower()
        for row in bind.execute(
            sa.text("SELECT alias_email FROM user_email_aliases")
        ).fetchall()
    }
    now = datetime.now(UTC)
    inserted = 0
    insert = sa.text(
        "INSERT INTO user_email_aliases "
        "(id, user_id, alias_email, active, created_at, updated_at) "
        "VALUES (:id, :user_id, :alias_email, :active, :created_at, :updated_at)"
    )
    for user_id, email in users:
        if not email or email.lower() in existing:
            continue
        bind.execute(
            insert,
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "alias_email": email,
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        existing.add(email.lower())
        inserted += 1
    return inserted


def _backfill_delivered_to(bind: sa.engine.Connection) -> int:
    """Stamp delivered_to on existing emails by matching To/Cc/Bcc against the
    seeded alias set. First alias found (case-insensitive) wins. Non-matching
    emails keep delivered_to = NULL."""
    aliases = {
        row[0].lower(): row[0]
        for row in bind.execute(
            sa.text("SELECT alias_email FROM user_email_aliases")
        ).fetchall()
    }
    if not aliases:
        return 0
    rows = bind.execute(
        sa.text(
            "SELECT id, to_emails_json, cc_emails_json, bcc_emails_json "
            "FROM email_messages WHERE delivered_to IS NULL"
        )
    ).fetchall()
    update = sa.text(
        "UPDATE email_messages SET delivered_to = :alias WHERE id = :id"
    )
    updated = 0
    for row in rows:
        match = _first_alias_match(
            aliases, row[1], row[2], row[3]
        )
        if match is None:
            continue
        bind.execute(update, {"alias": match, "id": row[0]})
        updated += 1
    return updated


def _first_alias_match(
    aliases: dict[str, str],
    *json_columns: str | None,
) -> str | None:
    for raw in json_columns:
        if not raw:
            continue
        try:
            addrs = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(addrs, list):
            continue
        for addr in addrs:
            if isinstance(addr, str) and addr.lower() in aliases:
                return aliases[addr.lower()]
    return None


def downgrade() -> None:
    op.drop_index("ix_email_messages_delivered_to", table_name="email_messages")
    op.drop_index("ix_email_messages_is_spam", table_name="email_messages")
    op.drop_column("email_messages", "gmail_labels")
    op.drop_column("email_messages", "delivered_to")
    op.drop_column("email_messages", "is_spam")
    op.drop_index("ix_user_email_aliases_active", table_name="user_email_aliases")
    op.drop_index("ix_user_email_aliases_user_id", table_name="user_email_aliases")
    op.drop_table("user_email_aliases")
