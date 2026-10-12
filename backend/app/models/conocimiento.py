"""Base de conocimiento para la respuesta a leads, Fase 1 (migración 0136).

Material que el asistente de respuesta puede ENLAZAR (nunca inventa): son
tablas, no constantes, para que una ruta nueva o un cambio de URL se aplique
sin desplegar.

- `kb_rutas`: una URL por web × idioma (tipo `soporte`) y una página de
  producto por interés (tipo `producto`, en mqeurope.com). Lo que no está
  aquí no se enlaza. Las FAQ de fluxlasers.es no están ni estarán: están
  desactualizadas.
- `kb_flux_categorias`: las categorías de la base de conocimiento de FLUX
  (support.flux3dp.com), una por modelo, con las palabras con las que se
  reconoce el modelo en un texto o en la descripción de un pedido.
- `kb_flux_articulos`: el ÍNDICE de la KB de FLUX, por idioma: título y enlace
  (nunca el cuerpo), categoría, etiquetas, fecha, si está desfasado. Lo que
  desaparece de FLUX se marca `ido_en`, no se borra.
- `kb_flux_sincronizaciones`: el histórico de pasadas de sincronización.

Fase 2 (migración 0139), el catálogo y los precios verificados:

- `kb_catalogo`: una fila por producto × idioma. Las máquinas salen del JSON
  del compositor de correos de Bomedia (Supabase, fila `main` de
  `composer_data`; BoHub solo LEE); los consumibles y repuestos, de las tiendas
  WooCommerce de las webs (API pública de la tienda, sin credenciales). El
  precio se guarda TAL CUAL está escrito (`precio_texto`) y, aparte, un número
  derivado para ordenar; nunca se convierte de moneda. Lo que desaparece del
  origen se marca `ido_en`, no se borra.
- `kb_catalogo_marcas`: las marcas del compositor (logo, color, URL por
  idioma) y su idioma por defecto, el que se usa cuando un producto no tiene
  precio en el idioma pedido.
- `kb_catalogo_sincronizaciones`: el histórico de pasadas, por fuente.
- `kb_interes_productos`: interés del clasificador → máquinas (marca y patrón
  del nombre), la tabla que decide qué máquinas se le enseñan a un lead.

Fase 3 (migración 0145), las imágenes y los vídeos de los correos compuestos:

- `kb_imagenes`: el índice de la biblioteca de medios de las webs (WordPress,
  `/wp-json/wp/v2/media`), solo lo que vale para un hero o una galería de
  muestras: anchas, apaisadas, sin logos ni fotos de producto. Sin texto
  alternativo en las webs: se busca por el título. Favoritas y ocultas, a
  mano desde la pantalla; lo que desaparece de la web se marca `ido_en`.
- `kb_videos`: los vídeos de YouTube de los canales del grupo, los enlazados
  en las webs y los pegados a mano, con su idioma, su tema (el mismo vídeo en
  varios idiomas), marca, productos del catálogo, miniatura (y cuál se usó) y
  duración. Lo editado a mano no lo pisa la sincronización.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Ruta de soporte: web × idioma → URL.
TIPO_SOPORTE = "soporte"
#: Página de producto: interés → URL (en la web del grupo que la tiene).
TIPO_PRODUCTO = "producto"
TIPOS: tuple[str, ...] = (TIPO_SOPORTE, TIPO_PRODUCTO)

#: Orígenes y estados de una sincronización (KB de FLUX y catálogo).
SYNC_PROGRAMADA = "programada"
SYNC_MANUAL = "manual"
SYNC_OK = "ok"
SYNC_PARCIAL = "parcial"
SYNC_ERROR = "error"

#: Fuentes del catálogo: el compositor de correos (máquinas) y las tiendas
#: WooCommerce de las webs (consumibles y repuestos).
FUENTE_COMPOSITOR = "compositor"
FUENTE_WOOCOMMERCE = "woocommerce"
FUENTES: tuple[str, ...] = (FUENTE_COMPOSITOR, FUENTE_WOOCOMMERCE)

#: Tipos de producto del catálogo.
TIPO_MAQUINA = "maquina"
TIPO_CONSUMIBLE = "consumible"
TIPO_REPUESTO = "repuesto"
TIPOS_PRODUCTO: tuple[str, ...] = (TIPO_MAQUINA, TIPO_CONSUMIBLE, TIPO_REPUESTO)


def _uuid() -> str:
    return str(uuid4())


class RutaConocimiento(TimestampMixin, Base):
    __tablename__ = "kb_rutas"
    __table_args__ = (
        UniqueConstraint("tipo", "sitio", "idioma", "interes", name="uq_kb_rutas_clave"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False, default=TIPO_SOPORTE)
    #: La clave de la web (`sitios_web.clave`).
    sitio: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Idioma de la ruta de soporte; "" cuando no aplica (páginas de producto).
    idioma: Mapped[str] = mapped_column(String(5), nullable=False, default="")
    #: Interés de la página de producto; "" cuando no aplica (rutas de soporte).
    interes: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    nota: Mapped[str | None] = mapped_column(String(255))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Última comprobación de la URL (botón de la pantalla): cuándo, código
    #: HTTP final y el error si no hubo respuesta.
    comprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comprobado_http: Mapped[int | None] = mapped_column(Integer)
    comprobado_error: Mapped[str | None] = mapped_column(String(255))


class FluxCategoria(TimestampMixin, Base):
    __tablename__ = "kb_flux_categorias"

    #: El id de la categoría en Zendesk, como texto (son enteros de 11-14 cifras).
    categoria_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Palabras (en minúsculas) con las que se reconoce el modelo; JSON de lista.
    palabras_json: Mapped[str | None] = mapped_column(Text)
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FluxArticulo(TimestampMixin, Base):
    __tablename__ = "kb_flux_articulos"
    __table_args__ = (
        Index("ix_kb_flux_articulos_locale_categoria", "locale", "category_id"),
        Index("ix_kb_flux_articulos_idioma", "idioma"),
    )

    #: `{zendesk_id}:{locale}` — un artículo tiene una fila por idioma.
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    zendesk_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Nuestro código de idioma (`es`, `en`, …).
    idioma: Mapped[str] = mapped_column(String(5), nullable=False)
    titulo: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    section_id: Mapped[int | None] = mapped_column(BigInteger)
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    etiquetas_json: Mapped[str | None] = mapped_column(Text)
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outdated_locales_json: Mapped[str | None] = mapped_column(Text)
    source_locale: Mapped[str | None] = mapped_column(String(10))
    #: Última pasada que lo vio; `ido_en` = cuándo dejó de estar en FLUX.
    visto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FluxSincronizacion(TimestampMixin, Base):
    __tablename__ = "kb_flux_sincronizaciones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    origen: Mapped[str] = mapped_column(String(20), nullable=False, default=SYNC_PROGRAMADA)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default=SYNC_OK)
    articulos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nuevos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actualizados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Por locale: artículos vistos, nuevos, idos y el error si lo hubo (JSON).
    locales_json: Mapped[str | None] = mapped_column(Text)
    detalle: Mapped[str | None] = mapped_column(Text)


# --- Fase 2: el catálogo ---------------------------------------------------------------


class ProductoCatalogo(TimestampMixin, Base):
    """Un producto en UN idioma. La clave de origen es `(fuente, origen_id,
    idioma)`: en el compositor `origen_id` es el `id` del producto; en una
    tienda, `{sitio}:{id de WooCommerce}`."""

    __tablename__ = "kb_catalogo"
    __table_args__ = (
        UniqueConstraint("fuente", "origen_id", "idioma", name="uq_kb_catalogo_clave"),
        Index("ix_kb_catalogo_tipo_marca", "tipo", "marca"),
        Index("ix_kb_catalogo_idioma", "idioma"),
        Index("ix_kb_catalogo_sitio", "sitio"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fuente: Mapped[str] = mapped_column(String(12), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False, default=TIPO_MAQUINA)
    marca: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    origen_id: Mapped[str] = mapped_column(String(80), nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    #: Características (lista JSON de textos): en el compositor, `feat1` y `feat2`.
    caracteristicas_json: Mapped[str | None] = mapped_column(Text)
    #: Categorías de la tienda («Consumibles / Tintas»), o el área de impresión.
    categoria: Mapped[str | None] = mapped_column(String(255))
    imagen: Mapped[str | None] = mapped_column(String(500))
    #: El precio TAL CUAL está escrito en el origen («9.300 €», «9 300 €»,
    #: «desde 21.500 €»). Se enseña así; `precio_num` es un número derivado
    #: solo para ordenar (vacío cuando no hay importe, nunca cero).
    precio_texto: Mapped[str | None] = mapped_column(String(80))
    precio_num: Mapped[float | None] = mapped_column(Float)
    moneda: Mapped[str | None] = mapped_column(String(3))
    #: False cuando el origen dice «Consultar» (o «on request»): el producto no
    #: tiene precio público y el correo dice que se prepara a medida, nunca
    #: escribe «Consultar». True si hay precio o si no se sabe (0142).
    precio_publico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Una entrada del origen que no es un producto (un premio del compositor,
    #: sin precio ni área de impresión): se guarda para que la pantalla la
    #: enseñe, pero no entra en el catálogo del asistente (0142).
    descartado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    motivo_descarte: Mapped[str | None] = mapped_column(String(120))
    enlace: Mapped[str | None] = mapped_column(String(500))
    #: Nuestro código de idioma (`es`, `en`, …) de ESTA fila.
    idioma: Mapped[str] = mapped_column(String(5), nullable=False)
    #: La web (`sitios_web.clave`) de la tienda; en el compositor, la del enlace
    #: si se reconoce, y si no "".
    sitio: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: Cuándo cambió el origen por última vez: en el compositor, el `updated_at`
    #: de la fila; en una tienda, la pasada que confirmó el precio.
    actualizado_origen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Lo demás que trae el origen y no tiene columna (JSON): badge, área, sku…
    datos_json: Mapped[str | None] = mapped_column(Text)
    visto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarcaCatalogo(TimestampMixin, Base):
    """Una marca del compositor (`data.brands[]`) y su idioma por defecto."""

    __tablename__ = "kb_catalogo_marcas"

    #: El nombre tal cual lo escribe el compositor («artisJet», «MBO»).
    nombre: Mapped[str] = mapped_column(String(80), primary_key=True)
    logo: Mapped[str | None] = mapped_column(String(500))
    color: Mapped[str | None] = mapped_column(String(20))
    #: URL de la marca por idioma (JSON de dict idioma → URL).
    urls_json: Mapped[str | None] = mapped_column(Text)
    #: El idioma cuyo precio se usa cuando el pedido no lo tiene (editable).
    idioma_defecto: Mapped[str] = mapped_column(String(5), nullable=False, default="es")
    #: Los datos de diseño del compositor para la tira de marca de un correo
    #: (JSON): etiqueta, texto del enlace por idioma, alto y ancho máximo del
    #: logo, fondo del logo y color de la divisoria (0143).
    datos_json: Mapped[str | None] = mapped_column(Text)
    visto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogoSincronizacion(TimestampMixin, Base):
    __tablename__ = "kb_catalogo_sincronizaciones"
    __table_args__ = (Index("ix_kb_catalogo_sinc_fuente_inicio", "fuente", "inicio"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fuente: Mapped[str] = mapped_column(String(12), nullable=False)
    origen: Mapped[str] = mapped_column(String(20), nullable=False, default=SYNC_PROGRAMADA)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default=SYNC_OK)
    productos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nuevos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actualizados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: El `updated_at` de la fila del compositor en esa pasada.
    origen_actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Por tienda e idioma (o por idioma en el compositor): recuentos y error (JSON).
    resumen_json: Mapped[str | None] = mapped_column(Text)
    detalle: Mapped[str | None] = mapped_column(Text)


class InteresProducto(TimestampMixin, Base):
    """Interés del clasificador → una máquina del catálogo, por marca y patrón
    del nombre («artisJet» + «Young»). Un patrón vacío son todas las máquinas
    de la marca. Se resuelve contra `kb_catalogo` al montar el contexto, así
    que una fila nueva o un producto nuevo del compositor se aplican sin
    desplegar."""

    __tablename__ = "kb_interes_productos"
    __table_args__ = (
        UniqueConstraint("interes", "marca", "patron", name="uq_kb_interes_productos_clave"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    interes: Mapped[str] = mapped_column(String(40), nullable=False)
    marca: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    patron: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    nota: Mapped[str | None] = mapped_column(String(255))
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --- Fase 3: imágenes y vídeos de los correos compuestos ------------------------------

#: Las pasadas de medios van al mismo histórico que las del catálogo
#: (`kb_catalogo_sincronizaciones`), con su propia fuente.
FUENTE_IMAGENES = "imagenes"
FUENTE_VIDEOS = "videos"
FUENTES_MEDIOS: tuple[str, ...] = (FUENTE_IMAGENES, FUENTE_VIDEOS)

#: De dónde salió un vídeo de la tabla.
VIDEO_ORIGEN_CANAL = "canal"
VIDEO_ORIGEN_WEB = "web"
VIDEO_ORIGEN_MANUAL = "manual"
VIDEO_ORIGENES: tuple[str, ...] = (VIDEO_ORIGEN_CANAL, VIDEO_ORIGEN_WEB, VIDEO_ORIGEN_MANUAL)


class ImagenWeb(TimestampMixin, Base):
    """Una imagen de la biblioteca de medios de una web que vale para un hero o
    una galería de muestras. La clave de origen es `(sitio, media_id)`."""

    __tablename__ = "kb_imagenes"
    __table_args__ = (
        UniqueConstraint("sitio", "media_id", name="uq_kb_imagenes_clave"),
        Index("ix_kb_imagenes_sitio", "sitio"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: La web (`sitios_web.clave`).
    sitio: Mapped[str] = mapped_column(String(40), nullable=False)
    #: El id del adjunto en WordPress.
    media_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: El título del adjunto: lo único por lo que se puede buscar (las webs no
    #: tienen texto alternativo) y el `alt` de la imagen en el correo.
    titulo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: La imagen original y sus tamaños con las MISMAS proporciones (lista JSON
    #: de `{"url", "ancho", "alto"}`, de menor a mayor): el correo pide el que
    #: le toca a su ancho, nunca un recorte cuadrado del tema.
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    tamanos_json: Mapped[str | None] = mapped_column(Text)
    ancho: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alto: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime: Mapped[str | None] = mapped_column(String(40))
    #: La página del adjunto en la web.
    pagina: Mapped[str | None] = mapped_column(String(500))
    #: La entrada o página a la que está adjunta (un producto no entra).
    padre_id: Mapped[int | None] = mapped_column(BigInteger)
    publicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: A mano, desde la pantalla: las favoritas van primero en lo que se le
    #: ofrece a la IA; las ocultas no se le ofrecen nunca.
    favorita: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oculta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoKb(TimestampMixin, Base):
    """Un vídeo que un correo compuesto puede llevar. La clave es
    `(plataforma, video_id)`; el bloque «vídeo» del correo guarda solo eso y
    sus datos salen de aquí al generar el correo."""

    __tablename__ = "kb_videos"
    __table_args__ = (
        UniqueConstraint("plataforma", "video_id", name="uq_kb_videos_clave"),
        Index("ix_kb_videos_tema", "tema"),
        Index("ix_kb_videos_idioma", "idioma"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plataforma: Mapped[str] = mapped_column(String(10), nullable=False, default="youtube")
    video_id: Mapped[str] = mapped_column(String(20), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    titulo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    descripcion: Mapped[str | None] = mapped_column(Text)
    #: Nuestro código de idioma (`es`, `en`…) y de dónde salió: `manual`,
    #: `titulo` (las palabras del título), `metadatos` (el idioma del título y
    #: la descripción en YouTube) o `audio` (el del audio en YouTube).
    idioma: Mapped[str | None] = mapped_column(String(5))
    idioma_fuente: Mapped[str | None] = mapped_column(String(12))
    #: Agrupa el mismo vídeo en varios idiomas: así se elige la versión del
    #: idioma del cliente.
    tema: Mapped[str | None] = mapped_column(String(80))
    #: La marca del catálogo (su clave del compositor: `artisjet`, `flux`…).
    marca: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    #: Los productos del catálogo que nombra (lista JSON de `{"fuente",
    #: "origen_id", "nombre"}`).
    productos_json: Mapped[str | None] = mapped_column(Text)
    #: Lo que se cambió a mano (lista JSON: `idioma`, `tema`, `marca`,
    #: `productos`): la sincronización no lo pisa.
    manual_json: Mapped[str | None] = mapped_column(Text)
    #: La miniatura y cuál se usó (`maxresdefault`, 1280×720, o `mqdefault`,
    #: 320×180; las dos en 16:9): con `mqdefault` se vuelve a probar la buena
    #: en cada pasada, por si el vídeo se resube en HD.
    miniatura: Mapped[str | None] = mapped_column(String(500))
    miniatura_calidad: Mapped[str | None] = mapped_column(String(20))
    miniatura_comprobada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Segundos.
    duracion: Mapped[int | None] = mapped_column(Integer)
    #: Un «short» de YouTube (vertical): no se le ofrece a la IA.
    corto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canal: Mapped[str | None] = mapped_column(String(120))
    canal_id: Mapped[str | None] = mapped_column(String(40))
    origen: Mapped[str] = mapped_column(String(10), nullable=False, default=VIDEO_ORIGEN_CANAL)
    #: Las webs (`sitios_web.clave`) que lo enlazan (lista JSON).
    webs_json: Mapped[str | None] = mapped_column(Text)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    publicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Lo demás de YouTube (JSON): el idioma del audio y de los metadatos tal
    #: cual, las etiquetas.
    datos_json: Mapped[str | None] = mapped_column(Text)
    visto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
