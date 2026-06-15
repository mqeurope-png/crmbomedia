"""add entity_type to contact_views

Revision ID: 20260615_0046
Revises: 20260615_0045
Create Date: 2026-06-15 14:00:00

Sprint Filtros & Listas — PR-B. Generaliza la tabla `contact_views`
(hoy solo contactos) a un store multi-entidad sin renombrarla — el
rename con FKs en prod es arriesgado y todos los call-sites legacy
seguirían funcionando solo si los rebautizamos. La columna
`entity_type` con default `'contact'` deja:

- Las vistas existentes en `entity_type='contact'` (back-compat
  total para el endpoint `/api/contact-views` y para el repositorio).
- Las vistas de empresas / emails / Brevo escriben otros valores
  (`company`, `email_thread`, `brevo_template`, `brevo_campaign`)
  desde el nuevo endpoint `/api/entity-views/{entity}`.

La unicidad de "default por usuario" pasa a ser
`(owner_user_id, entity_type)` y se sigue enforcando en app-layer
(MySQL no soporta partial-unique de forma portable; igual que ya
hace el repositorio hoy).

Índice `(owner_user_id, entity_type)` para que listar por entidad
+ owner sea barato sin tablescan.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0046"
down_revision: str | None = "20260615_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contact_views",
        sa.Column(
            "entity_type",
            sa.String(length=40),
            nullable=False,
            server_default="contact",
        ),
    )
    op.create_index(
        "ix_contact_views_owner_entity",
        "contact_views",
        ["owner_user_id", "entity_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_views_owner_entity", table_name="contact_views")
    op.drop_column("contact_views", "entity_type")
