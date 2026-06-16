"""widen email_templates.body_html from TEXT to MEDIUMTEXT (MySQL).

Revision ID: 20260616_0050
Revises: 20260616_0049
Create Date: 2026-06-16 23:10:00

Tras inline-cid-attachments (PR #166) las plantillas importadas de
Gmail llevan los PNG/JPG inline como `data:image/*;base64,...` dentro
del propio HTML. Una plantilla con 1-2 capturas decentes pesa entre
500 KB y 2 MB; las que tienen un catálogo entero (p. ej. "C Flux
Cortadoras y grabadoras láser") rondan los 10 MB.

`TEXT` (65 535 bytes) se queda corto → MySQL devuelve
`Data too long for column 'body_html'` al re-importar. `MEDIUMTEXT`
(16 MB) cubre cualquier plantilla razonable con varias imágenes
base64; `LONGTEXT` sería excesivo.

Solo `body_html` necesita el ensanche. `body_text` se sigue rellenando
desde `extract_text_from_html()` que descarta `<img>`, por lo que
nunca lleva el base64.

En SQLite (tests) no hay distinción entre TEXT y MEDIUMTEXT — el
op.alter_column es no-op semántico. Para MySQL escribimos un ALTER
explícito que MODIFY COLUMN.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260616_0050"
down_revision: str | None = "20260616_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            "email_templates",
            "body_html",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        # Solo posible si ninguna plantilla supera ya 65 KB. En prod
        # post-import muchas filas sí los superan, así que el downgrade
        # truncaría datos: MySQL fallaría con strict_mode. Lo dejamos
        # como TEXT explícito por simetría; el operador que lo necesite
        # tendrá que limpiar primero.
        op.alter_column(
            "email_templates",
            "body_html",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=False,
        )
