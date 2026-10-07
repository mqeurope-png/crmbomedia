"""Formularios web: apariencia por formulario y remates de mapeo.

- `web_forms.appearance_json` (Text, NULL = el aspecto de siempre).
- Campos `tags` con `maps_to_contact_field` (p. ej. el `maquina` de PimPam,
  mapeado a `contact.first_name`) pasan a «sin mapeo»: el tipo `tags` ya
  sabe a dónde va.
- `boprint-contacto`:
  - la casilla «Acepto recibir comunicaciones comerciales» queda mapeada a
    `contact.marketing_consent` (si no tenía mapeo);
  - el `help_text` del campo `privacidad` usa el formato de enlace
    `[texto](url)`: si ya llevaba una URL, se convierte en enlace; si no,
    enlaza a `/politica-de-privacidad/`.

Revision ID: 20261008_0126
Revises: 20261007_0125
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0126"
down_revision: str | None = "20261007_0125"
branch_labels = None
depends_on = None

FORM_BOPRINT = "boprint-contacto"
ENLACE_POLITICA = "[Política de privacidad](/politica-de-privacidad/)"
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def _sin_acentos(texto: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def help_privacidad(actual: str | None) -> str | None:
    """El nuevo `help_text` del campo de privacidad (None = no tocar)."""
    actual = (actual or "").strip()
    if "](" in actual:
        return None  # ya usa el formato de enlace
    m = _URL.search(actual)
    if m:
        url = m.group(0).rstrip(".,;:")
        return actual.replace(url, f"[Política de privacidad]({url})", 1)
    if actual:
        return f"{actual} {ENLACE_POLITICA}"
    return f"Consulta nuestra {ENLACE_POLITICA}."


def upgrade() -> None:
    with op.batch_alter_table("web_forms") as batch:
        batch.add_column(sa.Column("appearance_json", sa.Text(), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE web_form_fields SET maps_to_contact_field = NULL "
        "WHERE field_type = 'tags' AND maps_to_contact_field IS NOT NULL"
    ))

    form_id = conn.execute(sa.text("SELECT id FROM web_forms WHERE slug = :s"),
                           {"s": FORM_BOPRINT}).scalar()
    if form_id is None:
        return
    campos = conn.execute(sa.text(
        "SELECT id, field_key, label, field_type, help_text, maps_to_contact_field "
        "FROM web_form_fields WHERE form_id = :f"
    ), {"f": form_id}).fetchall()
    for cid, key, label, tipo, ayuda, destino in campos:
        texto = _sin_acentos(label or "")
        if (tipo == "checkbox" and not destino and "comunicaciones" in texto
                and "comercial" in texto):
            conn.execute(sa.text(
                "UPDATE web_form_fields SET maps_to_contact_field = 'contact.marketing_consent' "
                "WHERE id = :i"), {"i": cid})
        if key == "privacidad":
            nuevo = help_privacidad(ayuda)
            if nuevo is not None:
                conn.execute(sa.text("UPDATE web_form_fields SET help_text = :h WHERE id = :i"),
                             {"h": nuevo[:512], "i": cid})


def downgrade() -> None:
    # Los mapeos y textos corregidos no se deshacen (eran datos rotos).
    with op.batch_alter_table("web_forms") as batch:
        batch.drop_column("appearance_json")
