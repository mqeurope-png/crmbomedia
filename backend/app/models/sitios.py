"""Las webs y los dominios de correo del grupo, en datos (migración 0135).

Hasta el 11/10/2026 la lista de webs estaba escrita en el código
(`app.services.web_forms.sitios.WEBS`). Ahora vive aquí y se gestiona desde
Configuración ERP: la usan la captura universal de correo (qué dominios son
nuestros), los acuses y la respuesta a leads (remitente y marca de cada web)
y la base de conocimiento (idiomas publicados, plugin de traducción, rutas de
soporte). La lista de partida sigue en `sitios.SITIOS_DE_PARTIDA`: siembra la
migración y vale cuando la tabla está vacía (una base recién creada, los
tests), así que todo funciona igual con o sin filas.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Una web pública del grupo (formularios, acuses, soporte).
TIPO_WEB = "web"
#: Un dominio de correo del grupo sin web propia (bomedia.net, streamtec.es):
#: solo cuenta para la captura.
TIPO_CORREO = "correo"
TIPOS: tuple[str, ...] = (TIPO_WEB, TIPO_CORREO)

PLUGIN_QTRANSLATE = "qtranslate"
PLUGIN_WPML = "wpml"
PLUGINS: tuple[str, ...] = (PLUGIN_QTRANSLATE, PLUGIN_WPML)


class SitioWeb(TimestampMixin, Base):
    __tablename__ = "sitios_web"

    #: La clave del slug de los formularios (`artisjet-es`, `fluxlasers`): es
    #: lo que llevan los orígenes de los contactos y las rutas de soporte.
    clave: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: El dominio sin `www.` (para casar el correo).
    dominio: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    #: El host con el que se sirve la web (`www.mqeurope.com`): para construir URLs.
    host: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Otros dominios del MISMO sitio (`fluxlasers.eu` es el mismo WordPress
    #: que `fluxlasers.es`), JSON de lista.
    dominios_extra_json: Mapped[str | None] = mapped_column(Text)
    marca: Mapped[str] = mapped_column(String(80), nullable=False)
    empresa: Mapped[str] = mapped_column(String(80), nullable=False)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False, default=TIPO_WEB)
    idioma_defecto: Mapped[str] = mapped_column(String(5), nullable=False, default="es")
    #: Idiomas publicados, JSON de lista (`["es", "en", "de"]`).
    idiomas_json: Mapped[str | None] = mapped_column(Text)
    #: Plugin de traducción: `qtranslate` (misma ruta, solo cambia el prefijo),
    #: `wpml` (los slugs pueden estar traducidos) o ninguno.
    plugin_traduccion: Mapped[str | None] = mapped_column(String(20))
    #: Si el idioma por defecto lleva prefijo en la URL (lo normal es que no).
    prefijo_idioma_defecto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: WPML con slugs traducidos de verdad (mbolasers): no basta con el prefijo.
    slugs_traducidos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: La marca atiende su propio soporte en todos los idiomas (FLUX, MBO
    #: Lasers, PimPam): la regla de reparto por idioma no le aplica.
    soporte_propio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Remitente por defecto del acuse y del borrador de la respuesta a leads.
    remitente_defecto: Mapped[str | None] = mapped_column(String(255))
    posicion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
