"""Plantillas de documento (migración 0147).

Una plantilla es un CONJUNTO DE LÍNEAS —artículos de FACTUSOL, cantidades,
precios, descuentos y textos—, no un documento. Que acabe siendo una oferta,
una proforma o un pedido se decide al usarla: la misma plantilla sirve para
los tres destinos y se guarda desde una proforma, un albarán o un pedido.

Nacieron en el #559 como «plantillas de oferta» (`ofertas_plantillas`); la
0147 renombra esas tablas (la plantilla que había, con sus líneas y el enlace
desde `ofertas.plantilla_id`, sigue tal cual) y añade:

- `idioma`: la misma oferta en castellano y en alemán son dos plantillas.
- carpetas jerárquicas (`plantillas_documento_carpetas`), con el patrón de
  las carpetas de plantillas de correo: padre, dueño, orden, profundidad
  máxima 3; su ACCESO (`plantillas_documento_carpetas_acceso`, la tabla de
  «comparticiones» de las carpetas de correo) y la carpeta por defecto de
  cada usuario (`plantillas_documento_preferencias`).
- etiquetas libres y múltiples (`plantillas_documento_etiquetas`).

Visibilidad (al revés que las de correo, por diseño): las plantillas son de
la empresa y se ven todas. Si a un usuario se le da acceso a alguna carpeta,
desde ese momento SOLO ve esas carpetas (y sus subcarpetas). Sin ninguna
fila de acceso → lo ve todo. El administrador lo ve siempre todo.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.crm import Base, TimestampMixin

#: De dónde se guardó la plantilla.
ORIGEN_PRESUPUESTO = "presupuesto"
ORIGEN_ALBARAN = "albaran"
ORIGEN_PEDIDO = "pedido"
ORIGEN_MANUAL = "manual"
ORIGENES: tuple[str, ...] = (ORIGEN_PRESUPUESTO, ORIGEN_ALBARAN, ORIGEN_PEDIDO, ORIGEN_MANUAL)


def _uuid() -> str:
    return str(uuid4())


class PlantillaCarpeta(TimestampMixin, Base):
    """Carpeta de plantillas: jerárquica como las de las plantillas de correo
    (`email_template_folders`). Borrarla no borra nada: sus plantillas y
    subcarpetas suben a la raíz."""

    __tablename__ = "plantillas_documento_carpetas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("plantillas_documento_carpetas.id", ondelete="SET NULL"), index=True,
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PlantillaCarpetaAcceso(Base):
    """Quién tiene acceso a una carpeta (la «compartición» de las carpetas de
    correo). Un usuario con alguna fila aquí solo ve esas carpetas; sin
    ninguna, lo ve todo."""

    __tablename__ = "plantillas_documento_carpetas_acceso"
    __table_args__ = (
        UniqueConstraint("carpeta_id", "user_id", name="uq_plantillas_carpeta_acceso"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    carpeta_id: Mapped[str] = mapped_column(
        ForeignKey("plantillas_documento_carpetas.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlantillaCarpetaPreferencia(TimestampMixin, Base):
    """La carpeta por defecto de cada usuario (como `user_template_folder_
    prefs` de las de correo): donde se abre la pantalla y donde se guarda una
    plantilla nueva si no se elige otra."""

    __tablename__ = "plantillas_documento_preferencias"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_plantillas_documento_preferencias_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    carpeta_id: Mapped[str] = mapped_column(
        ForeignKey("plantillas_documento_carpetas.id", ondelete="CASCADE"), nullable=False,
    )


class PlantillaDocumento(TimestampMixin, Base):
    __tablename__ = "plantillas_documento"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: es · en · de · fr · nl. Una plantilla por idioma, sin traducciones.
    idioma: Mapped[str] = mapped_column(String(5), nullable=False, default="es")
    carpeta_id: Mapped[str | None] = mapped_column(
        ForeignKey("plantillas_documento_carpetas.id", ondelete="SET NULL"), index=True,
    )
    #: De dónde se guardó: presupuesto / albaran (serie + código de FACTUSOL),
    #: pedido (el de BoHub) o manual. `origen_numero` es el que se enseña.
    origen_tipo: Mapped[str | None] = mapped_column(String(16))
    origen_serie: Mapped[int | None] = mapped_column(Integer)
    origen_codigo: Mapped[int | None] = mapped_column(Integer)
    origen_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
    )
    origen_numero: Mapped[str | None] = mapped_column(String(40))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    lineas: Mapped[list[PlantillaDocumentoLinea]] = relationship(
        back_populates="plantilla", cascade="all, delete-orphan",
        order_by="PlantillaDocumentoLinea.posicion",
    )
    etiquetas: Mapped[list[PlantillaDocumentoEtiqueta]] = relationship(
        back_populates="plantilla", cascade="all, delete-orphan",
        order_by="PlantillaDocumentoEtiqueta.etiqueta_norm",
    )
    carpeta: Mapped[PlantillaCarpeta | None] = relationship()


class PlantillaDocumentoLinea(Base):
    __tablename__ = "plantillas_documento_lineas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plantilla_id: Mapped[str] = mapped_column(
        ForeignKey("plantillas_documento.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    posicion: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `codart` = el código interno de F_ART; `sku` = el comercial (EQUART),
    #: para enseñarlo. Sin artículo (línea de texto) van vacíos.
    codart: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[str | None] = mapped_column(String(64))
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    descuento_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=21)

    plantilla: Mapped[PlantillaDocumento] = relationship(back_populates="lineas")


class PlantillaDocumentoEtiqueta(Base):
    """Etiqueta libre de una plantilla («DTF», «escuelas», «FESPA»…). Se
    guarda tal cual se escribió y normalizada (minúsculas, sin acentos) para
    filtrar y no repetirla."""

    __tablename__ = "plantillas_documento_etiquetas"
    __table_args__ = (
        UniqueConstraint("plantilla_id", "etiqueta_norm", name="uq_plantillas_etiqueta"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plantilla_id: Mapped[str] = mapped_column(
        ForeignKey("plantillas_documento.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    etiqueta: Mapped[str] = mapped_column(String(60), nullable=False)
    #: En binario en MySQL: ya va normalizada, y con la collation por defecto
    #: la UNIQUE daría por iguales «ß» y «ss» (Python no).
    etiqueta_norm: Mapped[str] = mapped_column(
        String(60).with_variant(mysql.VARCHAR(60, collation="utf8mb4_bin"), "mysql"),
        nullable=False, index=True,
    )

    plantilla: Mapped[PlantillaDocumento] = relationship(back_populates="etiquetas")
