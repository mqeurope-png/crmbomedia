"""Brevo: apuntar en el contacto que la subida fue rechazada, y por qué.

146.876 trabajos de `brevo:push_contact` fallaron con el mismo 400 del 25/06
al 14/09: un error de DATOS que no se arregla repitiendo, y el runner
periódico los reencolaba sin parar. Mientras tanto, los contactos del CRM no
subían a Brevo y las campañas salían contra una lista desactualizada.

Dos columnas en `contacts`:
  - `brevo_rejected_at`: cuándo lo rechazó Brevo. Mientras esté puesta, ni el
    runner periódico lo detecta ni `should_push` lo deja pasar. La marca es
    pegajosa: se levanta con «Volver a subir todo» de la pantalla de Brevo,
    que es cuando alguien ha decidido que el dato ya está corregido.
  - `brevo_rejected_reason`: lo que contesta Brevo, que desde #522 incluye el
    cuerpo de la respuesta. Es lo que hace diagnosticable el 400.

No rellena nada: los rechazos se apuntan a partir del próximo intento.

Revision ID: 20261009_0129
Revises: 20261009_0128
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0129"
down_revision: str | None = "20261009_0128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("brevo_rejected_at", sa.DateTime(timezone=True),
                                   nullable=True))
        batch.add_column(sa.Column("brevo_rejected_reason", sa.String(length=500),
                                   nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_column("brevo_rejected_reason")
        batch.drop_column("brevo_rejected_at")
