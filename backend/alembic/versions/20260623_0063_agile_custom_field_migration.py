"""PR-Import-Agile-Completo — backfill custom fields Agile.

Revision ID: 20260623_0063
Revises: 20260622_0062
Create Date: 2026-06-23 09:00:00

Aplica retroactivamente las decisiones del PR para contactos ya
importados de Agile en producción:

1. `Productos`, `Producto`, `etiquetas`, `interests` que viven hoy
   como entradas en `contacts.custom_fields` (JSON) → split por
   separador y convertir cada token en una fila `contact_tags` con
   `source='agile_csv_backfill'`. Eliminar la entrada del JSON.

2. Normalizar la clave `CONTACTO Persona` (cualquier variante de
   casing / underscore) a la canónica `CONTACTO Persona` dentro
   del JSON, para que la pestaña Tags / endpoints muestren un solo
   campo.

3. `Horario` ya unifica entre cuentas por el matching
   `.upper() in CUSTOM_FIELDS_WHITELIST`. Pero si el mismo
   contacto tiene Horario en 2 cuentas distintas con valores
   distintos, hoy queda el último — aprovechamos para concatenar
   con " · " los Horarios distintos vistos.

Idempotente: re-ejecutar no duplica tags ni vuelve a tocar JSONs
ya migrados (cada paso comprueba el estado actual).
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
from sqlalchemy.orm import Session

revision: str = "20260623_0063"
down_revision: str | None = "20260622_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAGLIKE_KEYS_LOWER = {"productos", "producto", "etiquetas", "interests", "interest"}
_CONTACTO_PERSONA_KEYS_LOWER = {"contacto persona", "contacto_persona"}
_HORARIO_KEYS_LOWER = {"horario"}

_SPLIT_REGEX = re.compile(r"[,;|\n\r]+")


def _split_taglike(value):
    if value is None:
        return []
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value
        candidates = _SPLIT_REGEX.split(text)
    else:
        candidates = [value]
    out = []
    seen = set()
    for raw in candidates:
        if not isinstance(raw, (str, int, float)):
            continue
        token = str(raw).strip().strip("\"'")
        if not token or token.lower() in {"null", "none", "n/a", "-"}:
            continue
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        out.append(token)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        _backfill(session)
        session.commit()
    finally:
        session.close()


def _backfill(session: Session) -> None:
    contacts = list(
        session.execute(
            # text-style raw select to avoid coupling to ORM model
            # versions during migration replay.
            __import__("sqlalchemy").text(
                "SELECT id, custom_fields FROM contacts "
                "WHERE custom_fields IS NOT NULL AND custom_fields != ''"
            )
        )
    )
    now = datetime.now(UTC)

    # Pre-fetch tags table for case-insensitive lookups, paged in
    # memory — contactos.custom_fields suele tener <30 tokens
    # distintos en prod.
    tags_by_norm: dict[str, str] = {}
    for row in session.execute(
        __import__("sqlalchemy").text(
            "SELECT id, name_normalized FROM tags"
        )
    ):
        tags_by_norm[row.name_normalized] = row.id

    for cid, raw in contacts:
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        new_fields: dict = {}
        taglike_tokens: list[str] = []
        horario_values: list[str] = []
        contacto_persona_value = None
        for key, value in parsed.items():
            key_lower = key.strip().lower()
            if key_lower in _TAGLIKE_KEYS_LOWER:
                taglike_tokens.extend(_split_taglike(value))
                continue
            if key_lower in _CONTACTO_PERSONA_KEYS_LOWER:
                if isinstance(value, str) and value.strip():
                    contacto_persona_value = value.strip()
                continue
            if key_lower in _HORARIO_KEYS_LOWER:
                if isinstance(value, str) and value.strip():
                    horario_values.append(value.strip())
                continue
            new_fields[key] = value

        # Horario unificado: si hay >1 valor distinto, concatena
        # con " · ". Sin duplicar.
        if horario_values:
            dedup = []
            seen_h = set()
            for h in horario_values:
                k = h.lower()
                if k in seen_h:
                    continue
                seen_h.add(k)
                dedup.append(h)
            new_fields["Horario"] = " · ".join(dedup)

        if contacto_persona_value:
            new_fields["CONTACTO Persona"] = contacto_persona_value

        # Persistir el JSON limpio si cambió.
        new_json = json.dumps(new_fields, default=str) if new_fields else None
        if new_json != raw:
            session.execute(
                __import__("sqlalchemy").text(
                    "UPDATE contacts SET custom_fields = :v WHERE id = :id"
                ),
                {"v": new_json, "id": cid},
            )

        # Crear tags + contact_tags por cada token tag-like.
        for token in taglike_tokens:
            normalized = token.lower()
            tag_id = tags_by_norm.get(normalized)
            if tag_id is None:
                tag_id = str(uuid4())
                session.execute(
                    __import__("sqlalchemy").text(
                        "INSERT INTO tags (id, name, name_normalized, color, "
                        "description, created_at, updated_at) "
                        "VALUES (:id, :n, :nn, NULL, NULL, :now, :now)"
                    ),
                    {
                        "id": tag_id,
                        "n": token,
                        "nn": normalized,
                        "now": now,
                    },
                )
                tags_by_norm[normalized] = tag_id
            # Idempotente: si ya hay row en contact_tags, no duplicar.
            already = session.execute(
                __import__("sqlalchemy").text(
                    "SELECT 1 FROM contact_tags "
                    "WHERE contact_id = :c AND tag_id = :t LIMIT 1"
                ),
                {"c": cid, "t": tag_id},
            ).first()
            if already is not None:
                continue
            session.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO contact_tags "
                    "(contact_id, tag_id, source, assigned_at, "
                    "created_at, updated_at) "
                    "VALUES (:c, :t, :src, :now, :now, :now)"
                ),
                {
                    "c": cid,
                    "t": tag_id,
                    "src": "agile_csv_backfill",
                    "now": now,
                },
            )


def downgrade() -> None:
    """Downgrade no-op intencional. La migración consolida data
    transformada (tags creados, JSONs reescritos) que no es
    reconstruible sin auditar el estado anterior, y la lógica del
    PR queda en el código aunque la migración no se ejecute. Si hay
    que revertir, se borran manualmente los `contact_tags` con
    `source='agile_csv_backfill'` y se reincluye la entrada en el
    JSON desde un dump previo."""
