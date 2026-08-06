"""CRM · CRM-1 — persistir acciones de la llamada + enlace nota↔llamada + índices.

El registro de llamadas (`call_logs`) ejecutaba sus acciones post-llamada
(cambiar pipeline, ajustar lead score, crear tarea, disparar workflow) pero
**no dejaba constancia de cuáles se ejecutaron** en la propia fila: solo
quedaban en `audit_logs` y en `follow_up_task_id`. Para poder **filtrar
contactos por la acción posterior de sus llamadas** (CRM-1 Parte A) y para
guardar el nuevo *star score* ajustado (Parte D) hace falta un sitio en la
llamada donde leerlo.

- `call_logs.actions_taken` — JSON (texto) con las acciones que corrieron, p.ej.
  `{"adjust_lead_score": 10, "adjust_star_score": 4, "create_callback_task": true}`.
  El filtro por acción hace un LIKE sobre este texto — portable SQLite/MySQL
  porque la clave sale entrecomillada (`"adjust_star_score"`) en los dos.
- `notes.call_log_id` — FK a `call_logs`, `ON DELETE SET NULL`: la nota de una
  llamada se propaga al timeline como `Note` (source='call_log'); si se borra la
  llamada, la nota **se conserva** (queda huérfana del enlace, pero sigue).
- Índice en `call_logs.result_code` — el filtro nuevo cruza por resultado.
  `contact_id`, `called_at` y `user_id` ya están indexados desde 0080.

Revision ID: 20260806_0089
Revises: 20260805_0088
Create Date: 2026-08-06 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0089"
down_revision: str | None = "20260805_0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("call_logs", sa.Column("actions_taken", sa.Text(), nullable=True))
    op.add_column("notes", sa.Column("call_log_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_notes_call_log_id", "notes", "call_logs",
        ["call_log_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_notes_call_log_id", "notes", ["call_log_id"])
    op.create_index("ix_call_logs_result_code", "call_logs", ["result_code"])


def downgrade() -> None:
    op.drop_index("ix_call_logs_result_code", table_name="call_logs")
    op.drop_index("ix_notes_call_log_id", table_name="notes")
    op.drop_constraint("fk_notes_call_log_id", "notes", type_="foreignkey")
    op.drop_column("notes", "call_log_id")
    op.drop_column("call_logs", "actions_taken")
