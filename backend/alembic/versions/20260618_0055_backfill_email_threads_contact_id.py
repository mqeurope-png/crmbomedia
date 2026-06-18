"""backfill email_threads.contact_id desde email_messages.

Revision ID: 20260618_0055
Revises: 20260617_0054
Create Date: 2026-06-18 14:00:00

Bug residual del PR-Ficha-Cleanup (#192):
- Pre-bug-fix los emails se enviaban con `contact_id` correcto en
  `email_messages`, PERO si la chain
  `_get_or_create_thread` encontraba un thread Gmail huérfano (sin
  contact_id), reutilizaba esa row sin actualizar el contact_id.
- Resultado: muchos `email_threads.contact_id` quedaron `NULL` aunque
  los `email_messages` SÍ tienen el contact_id. La pestaña Emails de
  la ficha filtra por `email_threads.contact_id`, así que el thread
  no aparece.

Esta migración asocia retroactivamente los threads huérfanos cruzando
los emails de sus mensajes contra `contacts.email`:

- OUTBOUND messages: `to_emails_json` contiene los destinatarios. Si
  alguno matchea el email de un contacto activo, el thread es sobre
  ese contacto.
- INBOUND messages: `from_email` es el remitente. Si matchea un
  contacto, el thread es sobre ese contacto (la respuesta a un envío
  del CRM).

Conflict resolution: si un thread tiene mensajes para múltiples
contactos (poco común: contactos comparten thread_id solo en CCs
solapados), se queda con el PRIMER match — el mensaje más antiguo
suele ser el que estableció el thread, así que su contacto es el
principal.

Python-side (no SQL dialect-specific) para soportar SQLite en tests
sin JSON_CONTAINS. Para una BD de prod con miles de threads esto
tarda <1 s.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_0055"
down_revision: str | None = "20260617_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # Mapa email lowercase → contact_id. Filtramos a is_active=1 para
    # no atribuir threads a contactos archivados (que el operador
    # borró/deshabilitó conscientemente).
    contact_rows = bind.execute(
        sa.text(
            "SELECT id, LOWER(email) AS email FROM contacts "
            "WHERE email IS NOT NULL AND email != '' "
            "AND is_active = 1"
        )
    ).fetchall()
    email_to_contact: dict[str, str] = {row.email: row.id for row in contact_rows}
    if not email_to_contact:
        return

    # Threads huérfanos. Ordenamos por id estable para que el
    # backfill sea determinista entre runs.
    orphan_threads = bind.execute(
        sa.text(
            "SELECT id FROM email_threads WHERE contact_id IS NULL "
            "ORDER BY id"
        )
    ).fetchall()
    if not orphan_threads:
        return

    # Por cada thread huérfano: walk de sus mensajes ordenados por
    # `sent_at` ASC para que el primer match sea el mensaje más
    # antiguo (estable + intuitivo).
    updated = 0
    for thread_row in orphan_threads:
        thread_id = thread_row.id
        messages = bind.execute(
            sa.text(
                "SELECT direction, from_email, to_emails_json "
                "FROM email_messages WHERE thread_id = :tid "
                "ORDER BY sent_at ASC"
            ),
            {"tid": thread_id},
        ).fetchall()

        found_contact_id: str | None = None
        for msg in messages:
            direction = str(msg.direction or "").lower()
            if direction == "outbound":
                # Decode to_emails_json (Text storing JSON array).
                # Tolerante a NULL / malformed.
                if not msg.to_emails_json:
                    continue
                try:
                    to_list = json.loads(msg.to_emails_json)
                except (TypeError, ValueError):
                    continue
                if not isinstance(to_list, list):
                    continue
                for raw in to_list:
                    if not isinstance(raw, str):
                        continue
                    candidate = raw.strip().lower()
                    if candidate in email_to_contact:
                        found_contact_id = email_to_contact[candidate]
                        break
            elif direction == "inbound":
                from_email = (msg.from_email or "").strip().lower()
                if from_email and from_email in email_to_contact:
                    found_contact_id = email_to_contact[from_email]
            if found_contact_id is not None:
                break

        if found_contact_id is None:
            continue

        bind.execute(
            sa.text(
                "UPDATE email_threads SET contact_id = :cid "
                "WHERE id = :tid AND contact_id IS NULL"
            ),
            {"cid": found_contact_id, "tid": thread_id},
        )
        updated += 1

    # No `print`: alembic suprime stdout en prod. El operador puede
    # contar con `SELECT COUNT(*) FROM email_threads WHERE contact_id
    # IS NOT NULL` antes/después si quiere saberlo.
    _ = updated


def downgrade() -> None:
    # No-op: no podemos saber qué threads tenían contact_id NULL
    # ANTES del backfill (no hay snapshot). Re-NULL-ificar todo es
    # peor que dejar el dato. Si Bart quiere revertir, lo hace a mano.
    pass
