"""Remitentes: la elección del usuario manda sobre la sincronización de alias.

`user_email_alias_prefs.user_opted_in`: NULL = el usuario no se ha pronunciado
(manda la regla por defecto del sync: alias propio visible, ajeno oculto salvo
que sea su predeterminado), 1 = lo quiere como remitente, 0 = lo rechaza. Hasta
ahora el sync solo podía mirar `is_default`, que es uno por definición: las
secundarias que un comercial activaba a mano se apagaban solas en cada pasada
(Bart, 09/10/2026 17:24, junto con varios centenares de filas) y el alias
propio no se podía apagar.

Datos, en este orden:

1. **Predeterminado imposible** (`is_default=1` con `is_allowed=0`: un remitente
   por defecto que no se puede usar; lo creaba el propio sync al sembrar el
   default de Gmail sobre un alias ajeno que acto seguido ocultaba). Deja de
   ser predeterminado. Si el usuario queda sin predeterminado entre sus
   remitentes visibles, pasa a serlo su alias propio o, si no está visible, el
   primero por orden alfabético.
2. **Bart** (`cd2532ab-50c8-4e06-beb8-9a707072e484`, `bart@bomedia.net`): quiere
   `bart@streamtec.es` (predeterminado), `bart@artisjet-printers.eu` y
   `bart@mqeurope.com`; rechaza `bart@bomedia.net`. Solo si existe ese usuario
   con ese correo (en el CI no hay datos y no hace nada). Idempotente.

Revision ID: 20261010_0133
Revises: 20261010_0132
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0133"
down_revision: str | None = "20261010_0132"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

BART_ID = "cd2532ab-50c8-4e06-beb8-9a707072e484"
BART_EMAIL = "bart@bomedia.net"
BART_QUIERE = ("bart@streamtec.es", "bart@artisjet-printers.eu", "bart@mqeurope.com")
BART_PREDETERMINADO = "bart@streamtec.es"
BART_RECHAZA = ("bart@bomedia.net",)


def _normalizar_predeterminados(conn: sa.Connection, ahora: datetime) -> int:
    """El predeterminado es uno y está visible. Devuelve cuántos usuarios
    recibieron un predeterminado nuevo."""
    conn.execute(
        sa.text(
            "UPDATE user_email_alias_prefs SET is_default = 0, updated_at = :t "
            "WHERE is_default = 1 AND is_allowed = 0"
        ),
        {"t": ahora},
    )
    filas = conn.execute(
        sa.text(
            "SELECT p.user_id, p.alias_email, p.is_default, u.email "
            "FROM user_email_alias_prefs p JOIN users u ON u.id = p.user_id "
            "WHERE p.is_allowed = 1 ORDER BY p.user_id, p.alias_email"
        )
    ).fetchall()
    por_usuario: dict[str, dict[str, object]] = {}
    for user_id, alias, es_default, email in filas:
        datos = por_usuario.setdefault(
            str(user_id), {"email": (email or "").strip().lower(), "filas": []}
        )
        datos["filas"].append((str(alias), bool(es_default)))  # type: ignore[union-attr]
    promovidos = 0
    for user_id, datos in por_usuario.items():
        visibles: list[tuple[str, bool]] = datos["filas"]  # type: ignore[assignment]
        if any(es_default for _, es_default in visibles):
            continue
        propio = next(
            (alias for alias, _ in visibles if alias.strip().lower() == datos["email"]),
            None,
        )
        elegido = propio or min((alias for alias, _ in visibles), key=str.lower)
        conn.execute(
            sa.text(
                "UPDATE user_email_alias_prefs SET is_default = 1, updated_at = :t "
                "WHERE user_id = :u AND alias_email = :a"
            ),
            {"t": ahora, "u": user_id, "a": elegido},
        )
        promovidos += 1
    return promovidos


def _fijar_eleccion(
    conn: sa.Connection, ahora: datetime, alias: str, *, quiere: bool, predeterminado: bool,
) -> None:
    existente = conn.execute(
        sa.text(
            "SELECT id FROM user_email_alias_prefs "
            "WHERE user_id = :u AND LOWER(alias_email) = :a"
        ),
        {"u": BART_ID, "a": alias.lower()},
    ).first()
    valores = {
        "t": ahora,
        "allowed": 1 if quiere else 0,
        "opted": 1 if quiere else 0,
        "default": 1 if (quiere and predeterminado) else 0,
    }
    if existente is not None:
        if quiere and not predeterminado:
            # No tocar el predeterminado que tuviera: solo encenderlo y marcarlo.
            conn.execute(
                sa.text(
                    "UPDATE user_email_alias_prefs SET is_allowed = 1, user_opted_in = 1, "
                    "updated_at = :t WHERE id = :id"
                ),
                {"t": ahora, "id": existente[0]},
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE user_email_alias_prefs SET is_allowed = :allowed, "
                    "user_opted_in = :opted, is_default = :default, updated_at = :t "
                    "WHERE id = :id"
                ),
                {**valores, "id": existente[0]},
            )
        return
    conn.execute(
        sa.text(
            "INSERT INTO user_email_alias_prefs "
            "(id, user_id, alias_email, is_allowed, is_default, user_opted_in, "
            "gmail_display_name, display_name_override, created_at, updated_at) "
            "VALUES (:id, :u, :a, :allowed, :default, :opted, NULL, NULL, :t, :t)"
        ),
        {**valores, "id": str(uuid4()), "u": BART_ID, "a": alias},
    )


def _sembrar_bart(conn: sa.Connection, ahora: datetime) -> bool:
    fila = conn.execute(
        sa.text("SELECT email FROM users WHERE id = :id"), {"id": BART_ID}
    ).first()
    if fila is None or (fila[0] or "").strip().lower() != BART_EMAIL:
        return False
    for alias in BART_QUIERE:
        _fijar_eleccion(
            conn, ahora, alias, quiere=True, predeterminado=(alias == BART_PREDETERMINADO),
        )
    for alias in BART_RECHAZA:
        _fijar_eleccion(conn, ahora, alias, quiere=False, predeterminado=False)
    # Un solo predeterminado: el que Bart usa hoy.
    conn.execute(
        sa.text(
            "UPDATE user_email_alias_prefs SET is_default = 0, updated_at = :t "
            "WHERE user_id = :u AND is_default = 1 AND LOWER(alias_email) <> :a"
        ),
        {"t": ahora, "u": BART_ID, "a": BART_PREDETERMINADO},
    )
    return True


def upgrade() -> None:
    with op.batch_alter_table("user_email_alias_prefs") as batch:
        batch.add_column(sa.Column("user_opted_in", sa.Boolean(), nullable=True))

    conn = op.get_bind()
    ahora = datetime.now(UTC)
    promovidos = _normalizar_predeterminados(conn, ahora)
    bart = _sembrar_bart(conn, ahora)
    logger.info(
        "alias: predeterminados corregidos (%d usuario(s) con predeterminado nuevo); "
        "elecciones de Bart %s",
        promovidos, "sembradas" if bart else "no sembradas (usuario no encontrado)",
    )


def downgrade() -> None:
    """Quita la columna. Los predeterminados corregidos y las elecciones
    sembradas se quedan: son estados válidos también sin la columna."""
    with op.batch_alter_table("user_email_alias_prefs") as batch:
        batch.drop_column("user_opted_in")
