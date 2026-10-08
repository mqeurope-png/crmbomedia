"""Formularios web: origen por formulario (marca + idioma) y respaldo por marca.

Con 25 formularios en ocho webs y seis idiomas, «web_form» no dice de dónde
viene un lead. A partir de ahora:

- `contacts.origin` → «Formulario web · mboprinters.com (alemán)» (legible
  en la ficha);
- `contacts.origin_account_id` → `web_form:<marca>:<idioma>` (filtrable y
  segmentable por web y por idioma).

Esta migración reconstruye los leads ya entrados (tenían
`web_form:<slug>`), buscando el formulario por su slug y, si ya no existe
con ese slug, por el envío guardado (`form_submissions`). Un contacto que no
venga de un formulario no se toca.

Añade también `web_forms.is_brand_default`: el formulario de respaldo de una
marca cuando la página está en un idioma que no tiene el suyo.

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


def _origenes(conn: sa.engine.Connection) -> tuple[dict[str, tuple[str, str]],
                                                   dict[str, tuple[str, str]]]:
    """Dos índices de `(marca, idioma)`: por `origin_account_id` antiguo
    (`web_form:<slug>`) y por id de formulario."""
    por_clave: dict[str, tuple[str, str]] = {}
    por_id: dict[str, tuple[str, str]] = {}
    filas = conn.execute(sa.text("SELECT id, slug, brand, language FROM web_forms")).all()
    for fid, slug, brand, language in filas:
        datos = (brand or "", language or "es")
        por_clave[f"{PREFIJO}:{slug}"] = datos
        por_id[fid] = datos
    return por_clave, por_id


def upgrade() -> None:
    from app.services.web_forms.marcas import origen_de, origen_legible

    with op.batch_alter_table("web_forms") as batch:
        batch.add_column(sa.Column("is_brand_default", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))

    conn = op.get_bind()
    por_clave, por_id = _origenes(conn)
    if not por_clave:
        return

    def actualizar(contact_id: str, marca: str, idioma: str) -> None:
        conn.execute(sa.text(
            "UPDATE contacts SET origin = :o, origin_account_id = :a WHERE id = :i"
        ), {"o": origen_legible(marca, idioma), "a": origen_de(marca, idioma),
            "i": contact_id})

    # 1) Los que llevan el origen antiguo `web_form:<slug>`.
    pendientes: list[str] = []
    antiguos = conn.execute(sa.text(
        "SELECT id, origin_account_id FROM contacts "
        "WHERE origin_account_id LIKE :p"
    ), {"p": f"{PREFIJO}:%"}).all()
    for contact_id, clave in antiguos:
        datos = por_clave.get(clave or "")
        if datos is None:
            # Ya migrado (`web_form:marca:idioma`) o el slug ya no existe.
            if (clave or "").count(":") == 1:
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
    with op.batch_alter_table("web_forms") as batch:
        batch.drop_column("is_brand_default")
