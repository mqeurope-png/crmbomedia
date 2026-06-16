"""create email_template_attachments + revert body_html to TEXT.

Revision ID: 20260616_0051
Revises: 20260616_0050
Create Date: 2026-06-16 23:35:00

Plan C: en vez de embeber los attachments inline como base64 dentro
de `email_templates.body_html` (lo que obligó a la migración 0050 a
ensanchar la columna a MEDIUMTEXT), los movemos a una tabla aparte
con BLOB binario y rewriteamos el HTML para que apunte a un endpoint
del CRM que sirve el binario.

Ventajas frente a base64:

- `body_html` vuelve a pesar 1-50 KB típico: cache, joins, listados
  rápidos otra vez.
- El binario se sirve con cache headers `immutable` → el navegador
  no lo re-pide entre aperturas del editor.
- Compatibilidad con el flow de envío: el send path detecta las
  URLs `/api/email-templates/{id}/attachments/by-cid/{cid}` en el
  body, reinyecta el binario como inline MIME part y reemplaza el
  src por `cid:{cid}` para que el cliente de correo destinatario
  renderice la imagen como siempre.

Precondición operativa: si ya existe alguna plantilla cuyo
`body_html` excede 65 535 bytes (porque hubiera corrido un import
post-0050 con base64), el ALTER COLUMN revert fallaría con strict
mode. El operador limpia esas plantillas grandes ANTES de aplicar
esta migración. Tras este PR Bart ya tenía pendiente re-lanzar el
import — no hay datos críticos que conservar.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260616_0051"
down_revision: str | None = "20260616_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_mysql = bind.dialect.name == "mysql"

    op.create_table(
        "email_template_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(length=36),
            sa.ForeignKey("email_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_cid", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column(
            "data",
            mysql.MEDIUMBLOB() if is_mysql else sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_email_template_attachments_template_id",
        "email_template_attachments",
        ["template_id"],
    )
    op.create_index(
        "ix_email_template_attachments_template_cid",
        "email_template_attachments",
        ["template_id", "original_cid"],
    )

    if is_mysql:
        # Vuelve a TEXT. Sin base64 inline el cap de 65 KB sobra. Si
        # alguna fila viva tuviera body_html > 65 535 bytes, el ALTER
        # fallaría con strict_mode (ver docstring); la solución es
        # purgar plantillas grandes antes de re-lanzar el import.
        op.alter_column(
            "email_templates",
            "body_html",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_mysql = bind.dialect.name == "mysql"
    if is_mysql:
        op.alter_column(
            "email_templates",
            "body_html",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=False,
        )
    op.drop_index(
        "ix_email_template_attachments_template_cid",
        table_name="email_template_attachments",
    )
    op.drop_index(
        "ix_email_template_attachments_template_id",
        table_name="email_template_attachments",
    )
    op.drop_table("email_template_attachments")
