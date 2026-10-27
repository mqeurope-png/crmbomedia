"""Fuentes externas del redactor: soporte de terceros e índice de las webs.

Solo CREA tablas nuevas (no toca ni migra datos que ya existan):

1. `soporte_fuentes`: la lista blanca de centros de soporte de terceros,
   editable desde el panel. Se siembra con el de FLUX (Zendesk,
   `support.flux3dp.com`, con API pública de búsqueda).
2. `soporte_cache`: lo descargado del soporte (búsquedas y artículos), con
   caducidad.
3. `web_indice`: el índice de nuestras webs (páginas, entradas y productos),
   con `visto_en` / `ido_en` como `kb_catalogo`. En MySQL lleva un índice
   FULLTEXT sobre título y texto.
4. `web_indice_reglas`: qué entra y qué no en el índice. Se siembra con las
   páginas legales/carrito/prueba y las categorías de repuestos, consumibles,
   tintas, cartuchos, servicios y reacondicionadas.
5. `web_indice_pasadas`: cada recorrido del índice y su estado.

El índice nace vacío: lo llena el primer recorrido (programado una vez al mes
en worker-web, o «Reindexar ahora» en Configuración ERP).

Bajar borra las cinco tablas.

Revision ID: 20261027_0148
Revises: 20261027_0147
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20261027_0148"
down_revision: str | None = "20261027_0147"
branch_labels = None
depends_on = None

_TEXTO_LARGO = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")

#: Copia de `app.services.webs.reglas_partida` (una migración no importa la
#: aplicación: si el código cambia, esta foto no).
_PAGINAS_FUERA = (
    (r"cooki", "cookies"),
    (r"privac|datenschutz|proteccion-datos|confidentialit", "privacidad"),
    (r"aviso-legal|legal-notice|legal-warning|mentions-legales|impressum|juridisch"
     r"|rechtliche|avis-juridique", "aviso legal"),
    (r"condicion|condition|terms|voorwaarden|^agb$|contratacion|contratacao"
     r"|clausulas-informativas", "condiciones y términos"),
    (r"devoluci|^returns?$|retours?|widerruf|refund", "devoluciones"),
    (r"^(carrito|cart|basket|panier|einkaufswagen|winkelkarretje|winkelwagen)$", "carrito"),
    (r"^(checkout|finalizar-compra|pago|pasarela-de-pago|zur-kasse|paiement|afronden"
     r"|compra-finalizada|orders)$", "pago"),
    (r"^(mi-cuenta|my-account|mein-account|mon-compte|mijn-account|account)$", "mi cuenta"),
    (r"^(enviado|sent|gesendet|envoye|verstuurd)$", "aviso de formulario enviado"),
    (r"^test|-test$|test-form|testagile|sample-page|pagina-ejemplo", "páginas de prueba"),
    (r"^dpd-|^app-posts|^app-crea|cowork", "páginas internas"),
    (r"accesibilidad|accessibility", "declaración de accesibilidad"),
)
_CATEGORIAS_FUERA = (
    ("Repuestos", ""), ("Recambios", ""), ("Pièces de rechange", ""), ("Consumibles", ""),
    ("Tintas", "incluye «Tintas Ador», «Tintas UV Led…»"),
    ("Encres", "tintas en francés (artisjet-printers.eu)"), ("Cartuchos", ""),
    ("Packs de servicio", ""), ("Services", ""), ("Servicios", ""), ("Reacondicionadas", ""),
)
_LOCALES_FLUX = {"es": "es-419", "en": "en-us", "de": "de-de", "fr": "fr-fr", "nl": "nl",
                 "pt": "pt-br"}


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    ahora = datetime.now(UTC)

    fuentes = op.create_table(
        "soporte_fuentes",
        sa.Column("clave", sa.String(length=40), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("dominio", sa.String(length=255), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("marca", sa.String(length=40), nullable=True),
        sa.Column("locales_json", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("posicion", sa.Integer(), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("comprobado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comprobado_estado", sa.String(length=12), nullable=True),
        sa.Column("comprobado_detalle", sa.Text(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("clave"),
        sa.UniqueConstraint("dominio", name="uq_soporte_fuentes_dominio"),
    )
    op.bulk_insert(fuentes, [{
        "clave": "flux", "nombre": "FLUX · centro de ayuda", "dominio": "support.flux3dp.com",
        "tipo": "zendesk", "base_url": "https://support.flux3dp.com", "marca": "flux",
        "locales_json": json.dumps(_LOCALES_FLUX), "activo": True, "posicion": 0,
        "notas": "Zendesk Help Center: búsqueda por la API pública "
                 "(/api/v2/help_center/articles/search.json), sin credenciales.",
        "created_at": ahora, "updated_at": ahora,
    }])

    op.create_table(
        "soporte_cache",
        sa.Column("clave", sa.String(length=64), nullable=False),
        sa.Column("fuente", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("datos_json", _TEXTO_LARGO, nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("clave"),
    )
    op.create_index("ix_soporte_cache_expira_en", "soporte_cache", ["expira_en"])

    op.create_table(
        "web_indice",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sitio", sa.String(length=40), nullable=False),
        sa.Column("dominio", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=700), nullable=False),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("wp_id", sa.BigInteger(), nullable=False),
        sa.Column("idioma", sa.String(length=5), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=True),
        sa.Column("titulo", sa.String(length=500), nullable=False),
        sa.Column("texto", _TEXTO_LARGO, nullable=False),
        sa.Column("extracto", sa.Text(), nullable=True),
        sa.Column("imagen", sa.String(length=700), nullable=True),
        sa.Column("galeria_json", sa.Text(), nullable=True),
        sa.Column("categorias_json", sa.Text(), nullable=True),
        sa.Column("precio_web", sa.Numeric(12, 2), nullable=True),
        sa.Column("precio_web_texto", sa.String(length=80), nullable=True),
        sa.Column("precio_iva", sa.String(length=12), nullable=True),
        sa.Column("moneda", sa.String(length=3), nullable=True),
        sa.Column("modificado_web", sa.DateTime(timezone=True), nullable=True),
        sa.Column("huella", sa.String(length=64), nullable=True),
        sa.Column("visto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ido_en", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sitio", "tipo", "wp_id", "idioma", name="uq_web_indice_clave"),
    )
    op.create_index("ix_web_indice_sitio_idioma", "web_indice", ["sitio", "idioma"])
    op.create_index("ix_web_indice_ido_en", "web_indice", ["ido_en"])
    if bind.dialect.name == "mysql":
        # Mismas columnas que el MATCH de la búsqueda (`webs/buscar.py`).
        op.create_index("ft_web_indice_titulo_texto", "web_indice", ["titulo", "texto"],
                        mysql_prefix="FULLTEXT")

    reglas = op.create_table(
        "web_indice_reglas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("patron", sa.String(length=255), nullable=False),
        sa.Column("sitio", sa.String(length=40), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        sa.Column("posicion", sa.Integer(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_indice_reglas_tipo", "web_indice_reglas", ["tipo"])
    filas = []
    for pos, (patron, nota) in enumerate(_PAGINAS_FUERA):
        filas.append({"tipo": "pagina_excluir_slug", "patron": patron, "sitio": None,
                      "activo": True, "nota": nota, "posicion": pos,
                      "created_at": ahora, "updated_at": ahora})
    for pos, (prefijo, nota) in enumerate(_CATEGORIAS_FUERA):
        filas.append({"tipo": "categoria_fuera", "patron": prefijo, "sitio": None,
                      "activo": True, "nota": nota or None, "posicion": pos,
                      "created_at": ahora, "updated_at": ahora})
    op.bulk_insert(reglas, filas)

    op.create_table(
        "web_indice_pasadas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("origen", sa.String(length=12), nullable=False),
        sa.Column("estado", sa.String(length=12), nullable=False),
        sa.Column("lanzado_por", sa.String(length=36), nullable=True),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pendientes_json", sa.Text(), nullable=True),
        sa.Column("resumen_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_indice_pasadas_estado", "web_indice_pasadas", ["estado"])


def downgrade() -> None:
    # Las tablas se borran enteras (se llevan sus índices: quitarlos antes
    # falla en MySQL si sostienen una clave).
    op.drop_table("web_indice_pasadas")
    op.drop_table("web_indice_reglas")
    op.drop_table("web_indice")
    op.drop_table("soporte_cache")
    op.drop_table("soporte_fuentes")
