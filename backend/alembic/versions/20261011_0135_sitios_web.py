"""Las webs y los dominios de correo del grupo, en datos.

`sitios_web`: la lista que hasta ahora estaba escrita en el código
(`app.services.web_forms.sitios`), con lo que hace falta para la captura
universal de correo (qué dominios son nuestros), los acuses y la respuesta a
leads (marca y remitente por web) y la base de conocimiento (idiomas
publicados, plugin de traducción, soporte propio). Se siembra con la lista de
partida comprobada el 11/10/2026: las ocho webs y los dos dominios de correo
(`bomedia.net`, `streamtec.es`).

Revision ID: 20261011_0135
Revises: 20261010_0134
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0135"
down_revision: str | None = "20261010_0134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sitios_web",
        sa.Column("clave", sa.String(length=40), nullable=False),
        sa.Column("dominio", sa.String(length=120), nullable=False),
        sa.Column("host", sa.String(length=120), nullable=False),
        sa.Column("dominios_extra_json", sa.Text(), nullable=True),
        sa.Column("marca", sa.String(length=80), nullable=False),
        sa.Column("empresa", sa.String(length=80), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("idioma_defecto", sa.String(length=5), nullable=False),
        sa.Column("idiomas_json", sa.Text(), nullable=True),
        sa.Column("plugin_traduccion", sa.String(length=20), nullable=True),
        sa.Column("prefijo_idioma_defecto", sa.Boolean(), nullable=False),
        sa.Column("slugs_traducidos", sa.Boolean(), nullable=False),
        sa.Column("soporte_propio", sa.Boolean(), nullable=False),
        sa.Column("remitente_defecto", sa.String(length=255), nullable=True),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("clave"),
        sa.UniqueConstraint("dominio", name="uq_sitios_web_dominio"),
    )

    from app.services.web_forms.sitios import SITIOS_DE_PARTIDA  # noqa: PLC0415

    conn = op.get_bind()
    ahora = datetime.now(UTC)
    for sitio in SITIOS_DE_PARTIDA:
        conn.execute(
            sa.text(
                "INSERT INTO sitios_web (clave, dominio, host, dominios_extra_json, marca, "
                "empresa, tipo, idioma_defecto, idiomas_json, plugin_traduccion, "
                "prefijo_idioma_defecto, slugs_traducidos, soporte_propio, remitente_defecto, "
                "posicion, activo, created_at, updated_at) VALUES (:clave, :dominio, :host, "
                ":extra, :marca, :empresa, :tipo, :idioma, :idiomas, :plugin, :prefijo, :slugs, "
                ":soporte, :remitente, :posicion, 1, :t, :t)"
            ),
            {
                "clave": sitio.clave, "dominio": sitio.dominio, "host": sitio.web,
                "extra": json.dumps(list(sitio.dominios_extra)), "marca": sitio.marca,
                "empresa": sitio.empresa, "tipo": sitio.tipo, "idioma": sitio.idioma_defecto,
                "idiomas": json.dumps(list(sitio.idiomas)), "plugin": sitio.plugin,
                "prefijo": 1 if sitio.prefijo_idioma_defecto else 0,
                "slugs": 1 if sitio.slugs_traducidos else 0,
                "soporte": 1 if sitio.soporte_propio else 0,
                "remitente": sitio.remitente_defecto, "posicion": sitio.posicion, "t": ahora,
            },
        )


def downgrade() -> None:
    op.drop_table("sitios_web")
