"""De dónde saca el redactor lo que no tiene en el catálogo (migración 0148).

- `soporte_fuentes`: la LISTA BLANCA de centros de soporte de terceros (FLUX
  de partida; artisJet, MBO… cuando se tengan), editable desde Configuración
  ERP → Base de conocimiento. Internet entero, no: solo se descarga de estos
  dominios.
- `soporte_cache`: lo descargado (búsquedas y artículos), con caducidad: un
  artículo de soporte no cambia de un día para otro y se consulta mucho.
- `web_indice`: el índice de nuestras webs (páginas, entradas y productos sin
  repuestos), una fila por web, tipo, id de WordPress e idioma. Lo que deja de
  aparecer en un recorrido se marca `ido_en` (el patrón de `kb_catalogo`): no
  se borra ni se vacía y vuelve a llenar.
- `web_indice_reglas`: qué entra y qué no (slugs de páginas legales/carrito,
  categorías de fuera, excepciones por id), editable: las webs crecen.
- `web_indice_pasadas`: cada recorrido (programado o «reindexar ahora»), con
  su estado para la pantalla.

Todo es de solo lectura hacia fuera: nada escribe en las webs ni en FACTUSOL.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Texto largo: en MySQL `TEXT` se queda en 64 KB; un artículo o una página
#: pueden pasar.
TextoLargo = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")

#: Tipos de fuente de soporte.
SOPORTE_ZENDESK = "zendesk"     # centro de ayuda de Zendesk: API pública de búsqueda
SOPORTE_WORDPRESS = "wordpress"  # una web WordPress: /wp/v2/search
SOPORTE_HTML = "html"            # solo artículos por enlace (sin búsqueda)
SOPORTE_TIPOS = (SOPORTE_ZENDESK, SOPORTE_WORDPRESS, SOPORTE_HTML)

#: Tipos de entrada del índice.
WEB_PAGINA = "pagina"
WEB_ENTRADA = "entrada"
WEB_PRODUCTO = "producto"
WEB_TIPOS = (WEB_PAGINA, WEB_ENTRADA, WEB_PRODUCTO)

#: Tipos de regla del índice.
REGLA_PAGINA_SLUG = "pagina_excluir_slug"      # expresión regular sobre el slug
REGLA_PAGINA_ID = "pagina_excluir_id"          # id de WordPress (página o entrada)
REGLA_CATEGORIA_FUERA = "categoria_fuera"      # nombre de categoría que EMPIEZA por…
REGLA_PRODUCTO_EXCLUIR = "producto_excluir_id"  # id de producto que no entra
REGLA_PRODUCTO_INCLUIR = "producto_incluir_id"  # id que entra aunque una regla lo saque
REGLA_TIPOS = (REGLA_PAGINA_SLUG, REGLA_PAGINA_ID, REGLA_CATEGORIA_FUERA,
               REGLA_PRODUCTO_EXCLUIR, REGLA_PRODUCTO_INCLUIR)

#: El precio web: lo que dice la propia web (la casa decidió guardarlo «como
#: dice la web»). Nunca es un precio de oferta: ese sale de FACTUSOL.
IVA_SIN = "sin_iva"
IVA_CON = "con_iva"
IVA_DESCONOCIDO = "desconocido"

#: Estados de una pasada.
PASADA_EN_COLA = "en_cola"
PASADA_CORRIENDO = "corriendo"
PASADA_OK = "ok"
PASADA_PARCIAL = "parcial"
PASADA_ERROR = "error"


def _uuid() -> str:
    return str(uuid4())


def _ahora() -> datetime:
    return datetime.now(UTC)


class SoporteFuente(TimestampMixin, Base):
    __tablename__ = "soporte_fuentes"

    #: `flux`, `artisjet`, `mbo`…
    clave: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    #: El host que se permite (sin `https://`): `support.flux3dp.com`.
    dominio: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, default=SOPORTE_ZENDESK)
    #: `https://support.flux3dp.com`.
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Marca de la base de conocimiento (`flux`, `artisjet`, `mbo`), para el
    #: aviso y para preferir su soporte.
    marca: Mapped[str | None] = mapped_column(String(40))
    #: `{"es": "es-419", "en": "en-us"}`: el locale de la fuente por idioma
    #: nuestro (Zendesk). Vacío = el que dé la web.
    locales_json: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notas: Mapped[str | None] = mapped_column(Text)
    #: Lo que dio «Comprobar»: `ok` (sirve contenido), `vacio` (cáscara que se
    #: pinta con JavaScript, o sin resultados), `error`.
    comprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comprobado_estado: Mapped[str | None] = mapped_column(String(12))
    comprobado_detalle: Mapped[str | None] = mapped_column(Text)


class SoporteCache(Base):
    __tablename__ = "soporte_cache"
    __table_args__ = (Index("ix_soporte_cache_expira_en", "expira_en"),)

    #: Huella de `tipo|fuente|idioma|consulta-o-id`.
    clave: Mapped[str] = mapped_column(String(64), primary_key=True)
    fuente: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `busqueda` / `articulo`.
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    datos_json: Mapped[str] = mapped_column(TextoLargo, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=_ahora)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebIndiceEntrada(TimestampMixin, Base):
    __tablename__ = "web_indice"
    __table_args__ = (
        UniqueConstraint("sitio", "tipo", "wp_id", "idioma", name="uq_web_indice_clave"),
        Index("ix_web_indice_sitio_idioma", "sitio", "idioma"),
        Index("ix_web_indice_ido_en", "ido_en"),
        # FULLTEXT en MySQL (en SQLite, un índice normal sin uso): la búsqueda
        # usa MATCH … AGAINST con estas MISMAS columnas.
        Index("ft_web_indice_titulo_texto", "titulo", "texto", mysql_prefix="FULLTEXT"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: La clave de `sitios_web` (`boprint`, `fluxlasers`…).
    sitio: Mapped[str] = mapped_column(String(40), nullable=False)
    #: El host del que se leyó (`fluxlasers.eu`).
    dominio: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(700), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    #: Id de WordPress (los hay de 10 cifras: BIGINT).
    wp_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idioma: Mapped[str] = mapped_column(String(5), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(255))
    titulo: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    #: Texto limpio (sin HTML, sin shortcodes ni CSS sueltos).
    texto: Mapped[str] = mapped_column(TextoLargo, nullable=False, default="")
    extracto: Mapped[str | None] = mapped_column(Text)
    imagen: Mapped[str | None] = mapped_column(String(700))
    #: Lista JSON de URL (la galería del producto, sin la principal).
    galeria_json: Mapped[str | None] = mapped_column(Text)
    #: Lista JSON de nombres de categoría.
    categorias_json: Mapped[str | None] = mapped_column(Text)
    #: Precio de la web, tal cual lo dice la web. NUNCA precio de oferta.
    precio_web: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_web_texto: Mapped[str | None] = mapped_column(String(80))
    #: `sin_iva` / `con_iva` / `desconocido` (lo que dice la web).
    precio_iva: Mapped[str | None] = mapped_column(String(12))
    moneda: Mapped[str | None] = mapped_column(String(3))
    #: Fecha de modificación según WordPress.
    modificado_web: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Huella del contenido, para contar actualizados sin reescribir lo igual.
    huella: Mapped[str | None] = mapped_column(String(64))
    visto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Cuándo dejó de aparecer en la web (no se borra: se marca).
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebIndiceRegla(TimestampMixin, Base):
    __tablename__ = "web_indice_reglas"
    __table_args__ = (Index("ix_web_indice_reglas_tipo", "tipo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    #: La expresión regular (slug), el prefijo (categoría) o el id.
    patron: Mapped[str] = mapped_column(String(255), nullable=False)
    #: La web a la que se aplica (`None` = a todas).
    sitio: Mapped[str | None] = mapped_column(String(40))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    nota: Mapped[str | None] = mapped_column(String(255))
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebIndicePasada(TimestampMixin, Base):
    __tablename__ = "web_indice_pasadas"
    __table_args__ = (Index("ix_web_indice_pasadas_estado", "estado"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: `programada` / `manual`.
    origen: Mapped[str] = mapped_column(String(12), nullable=False)
    estado: Mapped[str] = mapped_column(String(12), nullable=False, default=PASADA_EN_COLA)
    lanzado_por: Mapped[str | None] = mapped_column(String(36))
    inicio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Lista JSON de las webs que faltan por recorrer (el recorrido va web a
    #: web, un trabajo por web, para no bloquear los webhooks de worker-web).
    pendientes_json: Mapped[str | None] = mapped_column(Text)
    #: Por web: `{"boprint": {"estado": "ok", "paginas": 10, …, "error": null}}`.
    resumen_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
