"""Formularios web: la web y el idioma del lead como datos propios.

Con 25 formularios en ocho webs y seis idiomas, «web_form» no dice de dónde
viene un lead. A partir de ahora:

- `contacts.origin` → «Formulario web · mboprinters.com (alemán)» (legible
  en la ficha);
- `contacts.origin_account_id` → `web_form:<sitio>:<idioma>` (filtrable por
  web);
- `contacts.language` → el idioma del formulario, columna nueva: se filtra y
  se segmenta por él cruzando webs, y manda al escribirle.

La web NO sale de `web_forms.brand`: una marca puede estar en dos webs
(`artisjet`). Sale de la clave del slug (`artisjet-es-contacto-de` →
`artisjet-es`), ver `app/services/web_forms/sitios.py`.

Esta migración reconstruye los leads ya entrados (tenían `web_form:<slug>`),
buscando el formulario por su slug y, si ya no existe con ese slug, por el
envío guardado (`form_submissions`). Un contacto que no venga de un
formulario no se toca. El idioma solo se rellena si está vacío.

Añade también `web_forms.is_site_default`: el formulario de respaldo de una
web cuando la página está en un idioma que no tiene el suyo.

Revision ID: 20261008_0127
Revises: 20261008_0126
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0127"
down_revision: str | None = "20261008_0126"
branch_labels = None
depends_on = None

PREFIJO = "web_form"


def _formularios(conn: sa.engine.Connection) -> tuple[dict[str, tuple[str, str]],
                                                      dict[str, tuple[str, str]]]:
    """Dos índices de `(slug, idioma)`: por `origin_account_id` antiguo
    (`web_form:<slug>`) y por id de formulario."""
    por_clave: dict[str, tuple[str, str]] = {}
    por_id: dict[str, tuple[str, str]] = {}
    filas = conn.execute(sa.text("SELECT id, slug, language FROM web_forms")).all()
    for fid, slug, language in filas:
        datos = (slug or "", language or "es")
        por_clave[f"{PREFIJO}:{slug}"] = datos
        por_id[fid] = datos
    return por_clave, por_id


def upgrade() -> None:
    from app.services.web_forms.sitios import (
        origen_de,
        origen_legible,
        partes_de_origen,
    )
    from app.services.web_forms.textos import normalizar_idioma

    with op.batch_alter_table("web_forms") as batch:
        batch.add_column(sa.Column("is_site_default", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(sa.Column("language", sa.String(length=5), nullable=True))
        batch.create_index("ix_contacts_language", ["language"])

    conn = op.get_bind()

    def solo_idioma(contact_id: str, idioma: str) -> None:
        """El origen ya estaba reconstruido (`web_form:<sitio>:<idioma>`),
        pero la columna `language` es nueva y está vacía. Pasa si se vuelve
        atrás y se vuelve a subir, o si la migración se corta a medias en
        MySQL, donde el DDL hace commit implícito."""
        conn.execute(sa.text(
            "UPDATE contacts SET language = COALESCE(language, :l) WHERE id = :i"
        ), {"l": normalizar_idioma(idioma), "i": contact_id})

    por_clave, por_id = _formularios(conn)

    def actualizar(contact_id: str, slug: str, idioma: str) -> None:
        conn.execute(sa.text(
            "UPDATE contacts SET origin = :o, origin_account_id = :a, "
            "language = COALESCE(language, :l) WHERE id = :i"
        ), {"o": origen_legible(slug, idioma), "a": origen_de(slug, idioma),
            "l": normalizar_idioma(idioma), "i": contact_id})

    # 1) Los que llevan el origen antiguo `web_form:<slug>`.
    pendientes: list[str] = []
    antiguos = conn.execute(sa.text(
        "SELECT id, origin_account_id FROM contacts WHERE origin_account_id LIKE :p"
    ), {"p": f"{PREFIJO}:%"}).all()
    for contact_id, clave in antiguos:
        datos = por_clave.get(clave or "")
        if datos is None:
            partes = partes_de_origen(clave)
            if partes is not None:
                # Ya migrado: el origen está bien, solo falta el idioma.
                solo_idioma(contact_id, partes[1])
            elif (clave or "").count(":") == 1:
                # Formato antiguo cuyo slug ya no existe: por el envío.
                pendientes.append(contact_id)
            continue
        actualizar(contact_id, *datos)

    # 2) Los que no se pueden resolver por slug (formulario renombrado o
    #    borrado) y los que quedaron sin `origin_account_id`: por su envío.
    sin_cuenta = conn.execute(sa.text(
        "SELECT id FROM contacts WHERE origin_account_id IS NULL AND origin = :o"
    ), {"o": PREFIJO}).scalars().all()
    por_resolver = set(pendientes) | set(sin_cuenta)
    if not por_resolver:
        return
    envios = conn.execute(sa.text(
        "SELECT contact_id, form_id FROM form_submissions "
        "WHERE contact_id IS NOT NULL ORDER BY created_at DESC"
    )).all()
    vistos: set[str] = set()
    for contact_id, form_id in envios:
        if contact_id not in por_resolver or contact_id in vistos:
            continue
        datos = por_id.get(form_id)
        if datos is None:
            continue
        vistos.add(contact_id)
        actualizar(contact_id, *datos)


def downgrade() -> None:
    # El origen reconstruido no se deshace (era el dato pobre de antes).
    with op.batch_alter_table("contacts") as batch:
        batch.drop_index("ix_contacts_language")
        batch.drop_column("language")
    with op.batch_alter_table("web_forms") as batch:
        batch.drop_column("is_site_default")
