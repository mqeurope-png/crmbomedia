"""El histórico de ventas de FACTUSOL, dentro de BoHub (migración 0153).

Para contestar «¿quién compró una FLUX 1390?» sin depender de que FACTUSOL
tenga abiertos los ejercicios viejos: los ejercicios antiguos solo los ve la
API mientras alguien los tenga reabiertos en el escritorio, así que se
vuelcan una vez y se guardan.

- `historico_ejercicios`: el estado de la carga de cada ejercicio (cuándo, qué
  pasó y los recuentos). Un ejercicio que la API no tiene queda como
  `no_disponible`, sin borrar lo que ya se hubiera volcado.
- `historico_documentos`: cabeceras de factura (`F_FAC`) y albarán (`F_ALB`):
  fecha, cliente y serie. La serie se guarda tal cual y NO se interpreta (la 5
  de 2017 no es la 5 de hoy). La fecha puede ser imposible (hay una de
  1900): se guarda la original y, aparte, la fecha solo si es razonable.
- `historico_lineas`: las líneas (`F_LFA` / `F_LAL`): artículo, descripción,
  cantidad, precio y total. Las líneas sin artículo son NOTAS (portes,
  condiciones, garantías, referencias de pedido): se guardan con su texto
  pero `es_producto` = False y no cuentan como venta de producto.
- `historico_articulos`: el catálogo de CADA año (`F_ART` de su ejercicio):
  una venta de 2017 de un artículo descatalogado sale con su nombre de 2017.
- `historico_familias`: las familias (`F_FAM`) y su `clase` (máquina,
  consumible, otro), que decide Bart desde la pantalla, no el código.

Solo se lee FACTUSOL. Estas tablas llevan nombres y códigos de clientes: no
van a registros, ni a mensajes de error.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base

#: `historico_ejercicios.estado`
ESTADO_CARGADO = "cargado"
ESTADO_NO_DISPONIBLE = "no_disponible"
ESTADO_ERROR = "error"
ESTADO_CARGANDO = "cargando"

#: `historico_documentos.tipo`
TIPO_FACTURA = "factura"
TIPO_ALBARAN = "albaran"

#: `historico_familias.clase` (la pone Bart).
CLASES_FAMILIA = ("maquina", "consumible", "otro")


class HistoricoEjercicio(Base):
    """El estado de la carga de un ejercicio. Los recuentos y el detalle no
    llevan datos de clientes."""

    __tablename__ = "historico_ejercicios"

    ejercicio: Mapped[str] = mapped_column(String(4), primary_key=True)
    estado: Mapped[str] = mapped_column(String(16), nullable=False)
    #: La última carga que salió bien (los datos son de esa).
    cargado_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: El último intento, saliera bien o no.
    intento_at: Mapped[datetime | None] = mapped_column(DateTime)
    detalle: Mapped[str | None] = mapped_column(String(255))
    facturas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    albaranes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lineas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lineas_producto: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articulos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segundos: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))


class HistoricoDocumento(Base):
    __tablename__ = "historico_documentos"
    __table_args__ = (
        UniqueConstraint("ejercicio", "tipo", "serie", "codigo", name="uq_historico_documento"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ejercicio: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Tal cual viene (TIPFAC / TIPALB). No se interpreta: cambia de empresa
    #: según el año.
    serie: Mapped[str] = mapped_column(String(8), nullable=False)
    codigo: Mapped[int] = mapped_column(Integer, nullable=False)
    #: La fecha si es razonable (2000-2100); la original, siempre.
    fecha: Mapped[date | None] = mapped_column(Date, index=True)
    fecha_original: Mapped[str | None] = mapped_column(String(32))
    #: Código de cliente en FACTUSOL (CLIFAC / CLIALB) y su nombre en el documento.
    codcli: Mapped[str | None] = mapped_column(String(32), index=True)
    cliente_nombre: Mapped[str | None] = mapped_column(String(255))
    referencia: Mapped[str | None] = mapped_column(String(120))


class HistoricoLinea(Base):
    __tablename__ = "historico_lineas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("historico_documentos.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    ejercicio: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    posicion: Mapped[int | None] = mapped_column(Integer)
    #: CODART interno (ARTLFA / ARTLAL); vacío = línea de nota.
    codart: Mapped[str | None] = mapped_column(String(64), index=True)
    descripcion: Mapped[str | None] = mapped_column(Text)
    cantidad: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    precio: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    #: Con artículo: venta de producto. Sin él: una nota (portes, condiciones…).
    es_producto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class HistoricoArticulo(Base):
    """El catálogo de cada ejercicio (`F_ART` de ese año)."""

    __tablename__ = "historico_articulos"

    ejercicio: Mapped[str] = mapped_column(String(4), primary_key=True)
    codart: Mapped[str] = mapped_column(String(64), primary_key=True)
    descripcion: Mapped[str | None] = mapped_column(String(255))
    familia: Mapped[str | None] = mapped_column(String(32), index=True)
    equart: Mapped[str | None] = mapped_column(String(64))


class HistoricoFamilia(Base):
    """Las familias de FACTUSOL y su clase, que decide Bart (se conserva al
    volver a cargar)."""

    __tablename__ = "historico_familias"

    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)
    descripcion: Mapped[str | None] = mapped_column(String(255))
    clase: Mapped[str | None] = mapped_column(String(16))
    clase_at: Mapped[datetime | None] = mapped_column(DateTime)
    clase_por: Mapped[str | None] = mapped_column(String(255))
