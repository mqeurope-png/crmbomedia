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
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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

#: Orígenes y estados de una sincronización de la KB de FLUX.
SYNC_PROGRAMADA = "programada"
SYNC_MANUAL = "manual"
SYNC_OK = "ok"
SYNC_PARCIAL = "parcial"
SYNC_ERROR = "error"


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
