"""PR-Fix-Sync-No-Sobreescribe-Cambios-CRM — manually_edited_fields_json.

Revision ID: 20260626_0066
Revises: 20260625_0065
Create Date: 2026-06-26 09:00:00

Bart confirmó (2026-06-21) que cada sync periódico de Agile/Brevo
machacaba campos editados manualmente en el CRM (lead_score, owner,
teléfono, lo que el comercial tocara). El comercial editaba a las
10:00, el sync a las 11:00 revertía.

Solución: capa de protección por contacto. La columna nueva guarda
un JSON array con los nombres de los campos que el operador editó
manualmente desde la UI. El sync de Agile/Brevo lee ese array antes
de sobrescribir cada campo de Capa A — si está marcado, skip.

Default NULL para contactos legacy (= ningún campo protegido todavía).
A partir del deploy, cada PATCH `/api/contacts/{id}` que toque un
campo de Capa A lo añade al array.

NO hay backfill retroactivo de campos perdidos — lo perdido perdido.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0066"
down_revision: str | None = "20260625_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "manually_edited_fields_json",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("contacts", "manually_edited_fields_json")
