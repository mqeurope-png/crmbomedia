"""Formularios web: crear las seis plantillas del acuse y activarlo en los 25.

Los 25 formularios de las ocho webs llevan desde el 08/10 recibiendo leads sin
que quien los rellena reciba nada: `send_confirmation_email` estaba a `0` y
`confirmation_email_template_id` a `NULL` en los 25, y no existía ninguna
plantilla de acuse (las 30 que hay son comerciales, importadas de Gmail).

Esta migración, por formulario y en este orden:

1. Crea (si no están) la carpeta «Acuses de recibo (formularios web)» y las
   seis plantillas, una por idioma. Seis cubren las ocho webs porque la marca
   es una variable. Los textos viven en
   `app/services/web_forms/plantillas_acuse.py`.
2. Pone el remitente de cada formulario según su web
   (`sitios.REMITENTES`) si no tiene ninguno.
3. Le asigna la plantilla de SU idioma, si no tiene ya otra.
4. Y entonces, solo entonces, enciende `send_confirmation_email`.

Idempotente: se puede volver a aplicar. **No pisa lo que alguien haya puesto a
mano**: un formulario que ya tenga remitente o plantilla se queda con los
suyos, y las plantillas existentes no se reescriben (se habrán editado desde
Plantillas). Solo enciende el interruptor de los formularios que acaban con
remitente y plantilla: encenderlo sin plantilla mandaría el texto básico, y
sin remitente no mandaría nada.

En una base de datos vacía (el CI de MySQL) no hay formularios ni usuarios y
no hace nada.

Revision ID: 20261010_0131
Revises: 20261010_0130
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0131"
down_revision: str | None = "20261010_0130"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _carpeta(conn: sa.Connection, ahora: datetime) -> str | None:
    """La carpeta de los acuses, creándola si hace falta. `None` si no se
    puede (la tabla no existe en una instalación muy vieja)."""
    from app.services.web_forms.plantillas_acuse import CARPETA  # noqa: PLC0415

    existente = conn.execute(
        sa.text("SELECT id FROM email_template_folders WHERE name = :n LIMIT 1"),
        {"n": CARPETA},
    ).scalar()
    if existente:
        return str(existente)
    folder_id = str(uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO email_template_folders "
            "(id, name, parent_folder_id, owner_user_id, is_global, visibility,"
            " sort_order, created_at, updated_at) "
            "VALUES (:id, :n, NULL, NULL, 1, 'team', 0, :t, :t)"
        ),
        {"id": folder_id, "n": CARPETA, "t": ahora},
    )
    return folder_id


def _plantillas(conn: sa.Connection, ahora: datetime) -> dict[str, str]:
    """`idioma → id de plantilla`, creando las que falten. No reescribe las
    que ya están: se habrán editado desde Plantillas."""
    from app.services.web_forms.plantillas_acuse import PLANTILLAS  # noqa: PLC0415

    folder_id = _carpeta(conn, ahora)
    por_idioma: dict[str, str] = {}
    for idioma, datos in PLANTILLAS.items():
        existente = conn.execute(
            sa.text("SELECT id FROM email_templates WHERE name = :n LIMIT 1"),
            {"n": datos["nombre"]},
        ).scalar()
        if existente:
            por_idioma[idioma] = str(existente)
            continue
        template_id = str(uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO email_templates "
                "(id, name, subject, body_html, body_text, folder_id,"
                " owner_user_id, is_global, usage_count, created_at, updated_at) "
                "VALUES (:id, :n, :s, :h, :x, :f, NULL, 1, 0, :t, :t)"
            ),
            {
                "id": template_id, "n": datos["nombre"], "s": datos["asunto"],
                "h": datos["html"], "x": datos["texto"], "f": folder_id,
                "t": ahora,
            },
        )
        por_idioma[idioma] = template_id
        logger.info("acuse: creada la plantilla de %s", idioma)
    return por_idioma


def upgrade() -> None:
    from app.services.web_forms.sitios import remitente_de_formulario  # noqa: PLC0415
    from app.services.web_forms.textos import normalizar_idioma  # noqa: PLC0415

    conn = op.get_bind()
    formularios = conn.execute(
        sa.text(
            "SELECT id, slug, language, confirmation_from_email,"
            " confirmation_email_template_id, send_confirmation_email"
            " FROM web_forms"
        )
    ).fetchall()
    if not formularios:
        return

    ahora = datetime.now(UTC)
    por_idioma = _plantillas(conn, ahora)

    activados = 0
    sin_remitente = 0
    for fid, slug, idioma, remitente, plantilla, encendido in formularios:
        nuevo_remitente = (remitente or "").strip() or remitente_de_formulario(slug)
        nueva_plantilla = plantilla or por_idioma.get(normalizar_idioma(idioma))
        if not nuevo_remitente:
            # Una web que no está en la lista de remitentes: inventarse un
            # `info@` de un dominio que no firma acabaría en spam. Se queda
            # apagado y se dice cuál.
            sin_remitente += 1
            logger.warning(
                "acuse: el formulario %s no tiene remitente y su web no está "
                "en la lista; su acuse queda apagado", slug,
            )
            continue
        conn.execute(
            sa.text(
                "UPDATE web_forms SET confirmation_from_email = :de,"
                " confirmation_email_template_id = :tpl,"
                " send_confirmation_email = 1 WHERE id = :id"
            ),
            {"de": nuevo_remitente, "tpl": nueva_plantilla, "id": fid},
        )
        if not encendido:
            activados += 1
    logger.info(
        "acuse: %d formulario(s) con el acuse activado, %d sin remitente",
        activados, sin_remitente,
    )


def downgrade() -> None:
    """Apaga el acuse y suelta la plantilla, pero NO borra las plantillas ni la
    carpeta: alguien puede haberlas editado o usado para otra cosa."""
    from app.services.web_forms.plantillas_acuse import PLANTILLAS  # noqa: PLC0415

    conn = op.get_bind()
    nombres = [datos["nombre"] for datos in PLANTILLAS.values()]
    ids = [
        str(fila[0])
        for fila in conn.execute(
            sa.text("SELECT id FROM email_templates WHERE name IN :n").bindparams(
                sa.bindparam("n", value=nombres, expanding=True)
            )
        ).fetchall()
    ]
    if ids:
        conn.execute(
            sa.text(
                "UPDATE web_forms SET send_confirmation_email = 0,"
                " confirmation_email_template_id = NULL"
                " WHERE confirmation_email_template_id IN :ids"
            ).bindparams(sa.bindparam("ids", value=ids, expanding=True))
        )
