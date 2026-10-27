"""Ofertas: presupuestar a un lead sin tocar FACTUSOL (migración 0146).

Un presupuesto de FACTUSOL necesita un cliente con datos fiscales; un lead no
los tiene. La OFERTA es un presupuesto que vive en BoHub: se crea desde una
PLANTILLA (las líneas de un presupuesto real, con sus artículos de FACTUSOL),
se manda a cualquiera con un PDF, y solo baja a FACTUSOL (`F_PRE`) cuando el
cliente dice que sí y una persona pulsa «Convertir en presupuesto».

Tablas:
- Las plantillas (nombre, descripción, activa y sus líneas con `codart` y
  `sku`) son desde la 0147 plantillas de DOCUMENTO: `plantillas_documento`,
  ver `app.erp.models.plantillas`.
- `ofertas` + `ofertas_lineas`: la oferta (contacto, empresa de BoHub si la
  tiene, serie emisora, idioma, con/sin IVA, fecha, validez, estado, totales,
  y el enlace al `F_PRE` creado al convertir) y sus líneas. Una línea sin
  artículo es válida: se resuelve al convertir.
- `ofertas_envios`: el historial de envíos (cuándo se mandó cada versión y a
  quién).
- `ofertas_contador`: el contador propio de la numeración `OF-2026-0001`, uno
  por año.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.erp.models.plantillas import PlantillaDocumento, PlantillaDocumentoLinea
from app.models.crm import Base, TimestampMixin

#: Las plantillas de oferta del #559 son ahora plantillas de DOCUMENTO
#: (`app.erp.models.plantillas`, migración 0147): sirven para una oferta, una
#: proforma o un pedido. Se conservan los nombres para quien los importe.
OfertaPlantilla = PlantillaDocumento
OfertaPlantillaLinea = PlantillaDocumentoLinea

#: Estados de la oferta. `caducada` también se DERIVA al leer (una enviada con
#: la validez pasada); el job diario lo deja escrito y emite el hecho.
ESTADO_BORRADOR = "borrador"
ESTADO_ENVIADA = "enviada"
ESTADO_CADUCADA = "caducada"
ESTADO_CONVERTIDA = "convertida"
ESTADOS_OFERTA: tuple[str, ...] = (
    ESTADO_BORRADOR, ESTADO_ENVIADA, ESTADO_CADUCADA, ESTADO_CONVERTIDA,
)


def _uuid() -> str:
    return str(uuid4())


class Oferta(TimestampMixin, Base):
    __tablename__ = "ofertas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: `OF-2026-0001`: prefijo configurable + año + contador propio.
    numero: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    secuencia: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Si el contacto desaparece, la oferta queda (sin contacto) por su historial.
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True,
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True,
    )
    plantilla_id: Mapped[str | None] = mapped_column(
        ForeignKey("plantillas_documento.id", ondelete="SET NULL"),
    )
    #: Empresa emisora (1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec).
    serie: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idioma: Mapped[str] = mapped_column(String(5), nullable=False, default="es")
    con_iva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    validez_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    valida_hasta: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ESTADO_BORRADOR, index=True,
    )
    #: «Su referencia» del documento y las observaciones del pie.
    referencia: Mapped[str | None] = mapped_column(String(255))
    notas: Mapped[str | None] = mapped_column(Text)
    base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=21)
    iva: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    #: Cada edición sube la versión; el envío apunta cuál se mandó.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: El presupuesto de FACTUSOL creado al convertir (serie + CODPRE + ejercicio).
    factusol_serie: Mapped[int | None] = mapped_column(Integer)
    factusol_codpre: Mapped[int | None] = mapped_column(Integer)
    factusol_ejercicio: Mapped[str | None] = mapped_column(String(4))
    convertida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enviada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    caducada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    lineas: Mapped[list[OfertaLinea]] = relationship(
        back_populates="oferta", cascade="all, delete-orphan",
        order_by="OfertaLinea.posicion",
    )
    envios: Mapped[list[OfertaEnvio]] = relationship(
        back_populates="oferta", cascade="all, delete-orphan",
        order_by="OfertaEnvio.enviado_en",
    )
    contact = relationship("Contact", foreign_keys=[contact_id])
    company = relationship("Company", foreign_keys=[company_id])
    plantilla: Mapped[PlantillaDocumento | None] = relationship()


class OfertaLinea(Base):
    __tablename__ = "ofertas_lineas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    oferta_id: Mapped[str] = mapped_column(
        ForeignKey("ofertas.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    posicion: Mapped[int] = mapped_column(Integer, nullable=False)
    codart: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[str | None] = mapped_column(String(64))
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    descuento_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=21)
    #: Importe de la línea (cantidad × precio − descuento), sin IVA.
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    oferta: Mapped[Oferta] = relationship(back_populates="lineas")


class OfertaEnvio(Base):
    __tablename__ = "ofertas_envios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    oferta_id: Mapped[str] = mapped_column(
        ForeignKey("ofertas.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    enviado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Qué versión de la oferta se mandó, con su fecha y total de entonces.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fecha_oferta: Mapped[date | None] = mapped_column(Date)
    total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    idioma: Mapped[str | None] = mapped_column(String(5))
    para_json: Mapped[str | None] = mapped_column(Text)
    cc_json: Mapped[str | None] = mapped_column(Text)
    asunto: Mapped[str | None] = mapped_column(String(500))
    from_alias: Mapped[str | None] = mapped_column(String(255))
    adjunto: Mapped[str | None] = mapped_column(String(255))
    message_id: Mapped[str | None] = mapped_column(String(255))
    thread_id: Mapped[str | None] = mapped_column(String(255))

    oferta: Mapped[Oferta] = relationship(back_populates="envios")


class OfertaContador(Base):
    """Un contador por año: `ultimo` es la última secuencia dada."""

    __tablename__ = "ofertas_contador"
    __table_args__ = (UniqueConstraint("anio", name="uq_ofertas_contador_anio"),)

    anio: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ultimo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
