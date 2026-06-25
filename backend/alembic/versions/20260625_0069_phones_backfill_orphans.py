"""Bug 11 — backfill legacy `contacts.phone` → `contact_phones`.

Revision ID: 20260625_0069
Revises: 20260625_0068
Create Date: 2026-06-25 15:00:00

Auditoría 2026-06-25 (PR-Bugs-Tanda): el sync de Agile y Brevo escribe
los teléfonos secundarios en `contact_phones` (vía
`reconcile_*_secondary_phones`) y el primario en el campo legacy
`contacts.phone`. Pero la migración 0043 que introdujo `contact_phones`
NO hizo backfill — los contactos con `contacts.phone` pero sin filas
en `contact_phones` (la mayoría de contactos legacy importados antes
del split) aparecen "sin teléfonos" en el sidebar de la ficha, aunque
la cabecera sí los muestre (porque lee el campo legacy).

Bug 11 (Bart 2026-06-25): doble fuente de verdad → confusión + riesgo
de pérdida cuando se editan teléfonos solo en la tabla nueva.

Solución one-shot: para cada contacto con `contacts.phone IS NOT NULL`
y SIN ninguna fila en `contact_phones`, INSERT una fila
`(contact_id, label='principal', number=contacts.phone, is_primary=True,
source='legacy')`. Idempotente: re-ejecutar no duplica porque filtra
por contactos sin filas existentes.

PATCH endpoint y los CRUD de `contact_phones` ya mantienen
`contacts.phone` sincronizado al primario (ver routes.py:2582), así que
escritura nueva sigue funcionando. Esta migración cierra el hueco
histórico.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260625_0069"
down_revision: str | None = "20260625_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MySQL + SQLite compatible: no usamos UUID() ni gen_random_uuid()
    # — generamos el `id` con un fallback portable (hex de
    # contact_id + sufijo fijo) que da una clave única determinista.
    # Esto evita depender de extensiones server-side.
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        # SQLite no tiene UUID(); usamos `lower(hex(randomblob(16)))`
        # con el patrón típico de Alembic en este repo.
        op.execute(
            """
            INSERT INTO contact_phones (
                id, contact_id, label, number, is_primary, source,
                created_at, updated_at
            )
            SELECT
                lower(
                    substr(hex(randomblob(16)), 1, 8) || '-' ||
                    substr(hex(randomblob(16)), 1, 4) || '-' ||
                    substr(hex(randomblob(16)), 1, 4) || '-' ||
                    substr(hex(randomblob(16)), 1, 4) || '-' ||
                    substr(hex(randomblob(16)), 1, 12)
                ),
                c.id, 'principal', c.phone, 1, 'legacy',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM contacts c
            WHERE c.phone IS NOT NULL
              AND TRIM(c.phone) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM contact_phones cp
                  WHERE cp.contact_id = c.id
              )
            """
        )
    else:
        # MySQL: usa UUID() nativo. Mismo idempotency check.
        op.execute(
            """
            INSERT INTO contact_phones (
                id, contact_id, label, number, is_primary, source,
                created_at, updated_at
            )
            SELECT
                UUID(),
                c.id, 'principal', c.phone, 1, 'legacy',
                NOW(6), NOW(6)
            FROM contacts c
            WHERE c.phone IS NOT NULL
              AND TRIM(c.phone) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM contact_phones cp
                  WHERE cp.contact_id = c.id
              )
            """
        )


def downgrade() -> None:
    # Borra solo las filas que esta migración creó (source='legacy').
    # Los teléfonos secundarios añadidos por syncs Agile/Brevo viven
    # con source='agilecrm' / 'brevo' y no se tocan.
    op.execute("DELETE FROM contact_phones WHERE source = 'legacy'")
