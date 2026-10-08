"""Formularios web: repara los `assignment_mode` que el motor no entiende.

Los 25 formularios se cargaron con `assignment_mode = 'fixed'`. El valor que
`app/services/web_forms/submit.py::_apply_assignment` reconoce es
`'fixed_owner'`, así que durante días ningún lead de ninguna web se asignó a
nadie: `contacts.owner_user_id` a NULL, cero filas en `contact_assignments`,
el aviso al propietario sin destinatario y los leads fuera de la cartera de
todo el mundo. Y sin un solo aviso en ninguna parte.

Esta migración arregla los datos; el valor ya no se puede volver a colar (lo
rechazan la API y el modelo) y el motor ahora se queja si se lo encuentra.

Solo se cambia lo que se puede deducir sin inventar (`ALIAS_ASSIGNMENT_MODES`,
hoy `fixed` → `fixed_owner`). Un valor desconocido que no esté en esa tabla se
deja como está y queda en el log: cambiarlo a voleo asignaría leads al
comercial equivocado, que es peor que no asignarlos.

En producción ya se corrigió a mano, así que ahí no debería encontrar nada.

Revision ID: 20261009_0128
Revises: 20261008_0127
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0128"
down_revision: str | None = "20261008_0127"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    from app.models.web_forms import ALIAS_ASSIGNMENT_MODES, ASSIGNMENT_MODES

    conn = op.get_bind()
    filas = conn.execute(sa.text(
        "SELECT id, slug, assignment_mode, fixed_owner_user_id FROM web_forms"
    )).all()
    # Las claves en minúsculas: una carga a mano pudo meter «FIXED».
    alias = {k.lower(): v for k, v in ALIAS_ASSIGNMENT_MODES.items()}
    arreglados = 0
    for form_id, slug, modo, propietario in filas:
        if modo in ASSIGNMENT_MODES:
            continue
        bueno = alias.get((modo or "").strip().lower())
        if bueno is None:
            logger.warning(
                "web_forms %s (%s): assignment_mode %r no es válido y no se "
                "puede deducir el bueno; se deja como está. Válidos: %s",
                slug, form_id, modo, ", ".join(sorted(ASSIGNMENT_MODES)),
            )
            continue
        conn.execute(
            sa.text("UPDATE web_forms SET assignment_mode = :m WHERE id = :i"),
            {"m": bueno, "i": form_id},
        )
        arreglados += 1
        logger.warning(
            "web_forms %s (%s): assignment_mode %r → %r (sus leads no se "
            "estaban asignando a nadie)", slug, form_id, modo, bueno,
        )
        if bueno == "fixed_owner" and not propietario:
            # Arreglar el modo no basta: sin propietario sigue sin asignar.
            # La API exige los dos juntos; estas filas entraron por detrás.
            logger.warning(
                "web_forms %s (%s): ya es «fixed_owner» pero NO tiene "
                "propietario fijo, así que sus leads siguen sin comercial. "
                "Hay que ponerle uno en CRM · Formularios.", slug, form_id,
            )
    if arreglados:
        logger.warning(
            "web_forms: %d formulario(s) con assignment_mode reparado. Los "
            "leads que entraron mientras tanto siguen sin comercial: los "
            "lista el Cuadre en «Lead web sin comercial asignado».", arreglados,
        )


def downgrade() -> None:
    # No se deshace: volver a poner un valor que el motor no entiende
    # reabriría el agujero.
    pass
