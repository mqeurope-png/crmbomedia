"""El histórico de ventas de FACTUSOL, dentro de BoHub (migración 0153).

Para contestar «¿quién compró una FLUX 1390?» sin depender de que FACTUSOL
tenga abiertos los ejercicios viejos: los ejercicios antiguos solo los ve la
API mientras alguien los tenga reabiertos en el escritorio, así que se
vuelcan una vez y se guardan.

- `historico_ejercicios`: el estado de la carga de cada ejercicio (cuándo, qué
  pasó y los recuentos). Un ejercicio que la API no tiene queda como
  `no_disponible`, sin borrar lo que ya se hubiera volcado.
- `historico_documentos`: cabeceras de factura (`F_FAC`), albarán (`F_ALB`)
  y, desde la 0155, proforma (`F_PRE`, que no cuenta como venta): fecha,
  cliente, serie, total y estado. La serie se guarda tal cual y NO se interpreta (la 5
  de 2017 no es la 5 de hoy). La fecha puede ser imposible (hay una de
  1900): se guarda la original y, aparte, la fecha solo si es razonable.
- `historico_lineas`: las líneas (`F_LFA` / `F_LAL`): artículo, descripción,
  cantidad, precio y total. Las líneas sin artículo son NOTAS (portes,
  condiciones, garantías, referencias de pedido): se guardan con su texto
  pero `es_producto` = False y no cuentan como venta de producto.
- `historico_articulos`: el catálogo de CADA año (`F_ART` de su ejercicio):
  una venta de 2017 de un artículo descatalogado sale con su nombre de 2017.
- `historico_familias`: las familias (`F_FAM`), su `clase` (máquina,
  consumible, recambio, accesorio, otro) y su `gama` (UV LED, Láser…), que
  decide Bart desde la pantalla, no el código (0154).
- `historico_gamas`: la lista de gamas, ampliable desde la pantalla (0154).
- `historico_reglas_familia`: las reglas que PROPONEN clase y gama a partir
  del código o del nombre de la familia; datos, no código (0154).

Solo se lee FACTUSOL. Estas tablas llevan nombres y códigos de clientes: no
van a registros, ni a mensajes de error.
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
#: Proformas (presupuestos, `F_PRE`), desde la 0155: se vuelcan para poder
#: abrir su ficha de cualquier año, pero NO son ventas.
TIPO_PRESUPUESTO = "proforma"
#: Los tipos que cuentan como venta (compradores, familias, parque…).
TIPOS_VENTA = (TIPO_FACTURA, TIPO_ALBARAN)

#: `historico_familias.clase` (la pone Bart). Recambio y accesorio, desde la
#: 0154: un recambio avisa de servicio y un accesorio no es una máquina.
CLASES_FAMILIA = ("maquina", "consumible", "recambio", "accesorio", "otro")
#: «Máquina» se decide por ARTÍCULO (31/10/2026): una familia de clase máquina
#: marca qué hay que mirar, no mete a todos sus artículos en el parque. Un
#: artículo es máquina solo con `historico_articulos_clase.clase = maquina`.
CLASE_MAQUINA = "maquina"
#: En `historico_articulos_clase`: revisado y NO es máquina (sin otra clase;
#: «Aceptar todas» no lo vuelve a proponer).
CLASE_NO_MAQUINA = "no_maquina"

#: `historico_reglas_familia.campo`: contra qué se compara el patrón.
REGLA_CODIGO = "codigo"     # el código de la familia, exacto
REGLA_NOMBRE = "nombre"     # el nombre de la familia contiene el patrón (sin mayúsculas)
CAMPOS_REGLA = (REGLA_CODIGO, REGLA_NOMBRE)


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
    #: Cargado ya con el origen de cada línea (0157). Hasta que se recarga, el
    #: parque no puede emparejar albarán y factura por el enlace.
    con_enlaces: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
    #: Total del documento con impuestos (TOTFAC / TOTALB / TOTPRE) y su
    #: estado tal cual (ESTFAC: cobro; ESTALB: facturado; ESTPRE: aceptado).
    #: Desde la 0155; vacíos hasta que se vuelve a cargar el ejercicio.
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    estado: Mapped[str | None] = mapped_column(String(8))


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
    #: El documento del que sale la línea, como lo apunta FACTUSOL en la propia
    #: línea (`DOC*`/`DTP*`/`DCO*`): `P` presupuesto, `C` pedido de cliente,
    #: `A` albarán (en una factura, el albarán que se facturó). Desde la 0157;
    #: vacío hasta que se vuelve a cargar el ejercicio, o si se tecleó a mano.
    origen_tipo: Mapped[str | None] = mapped_column(String(1))
    origen_serie: Mapped[str | None] = mapped_column(String(8))
    origen_codigo: Mapped[int | None] = mapped_column(Integer)
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
    #: `historico_gamas.codigo`: en una máquina, qué es; en lo demás, para qué
    #: máquina sirve.
    gama: Mapped[str | None] = mapped_column(String(32))
    clase_at: Mapped[datetime | None] = mapped_column(DateTime)
    clase_por: Mapped[str | None] = mapped_column(String(255))
    #: «Mantener la mía»: la propuesta (clase|gama) que Bart ya vio y descartó;
    #: no se vuelve a marcar para revisar mientras la propuesta sea esa.
    propuesta_descartada: Mapped[str | None] = mapped_column(String(64))


class HistoricoArticuloClase(Base):
    """La clase de un artículo concreto, por encima de la de su familia
    (0157): una familia que mezcla máquinas y accesorios. Vale para todos los
    ejercicios y todos los clientes. La pone Bart (desde el parque o desde
    Familias) y se quita para volver a la de la familia."""

    __tablename__ = "historico_articulos_clase"

    codart: Mapped[str] = mapped_column(String(64), primary_key=True)
    clase: Mapped[str] = mapped_column(String(16), nullable=False)
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)


class HistoricoReglaArticulo(Base):
    """Una pista en la descripción de un artículo: que SÍ es una máquina
    («BEAMBOX», «IMPRESORA»…) o que NO lo es («FILTER», «LENS»…). Propone; no
    decide. Datos editables (0158, positivas desde la 0159)."""

    __tablename__ = "historico_reglas_articulo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patron: Mapped[str] = mapped_column(String(64), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    #: `maquina` (el nombre dice que lo es) o `no_maquina` (delata que no). La
    #: propuesta exige una positiva (0159).
    tipo: Mapped[str] = mapped_column(String(16), nullable=False, default="no_maquina")
    #: Casa solo como palabra entera (0161); si no, al principio de palabra.
    palabra_entera: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class HistoricoAjuste(Base):
    """Un ajuste del histórico, como dato (0160): p. ej. el umbral de precio
    a partir del cual un artículo sin pista se propone como máquina dudosa."""

    __tablename__ = "historico_ajustes"

    clave: Mapped[str] = mapped_column(String(64), primary_key=True)
    valor: Mapped[str] = mapped_column(String(255), nullable=False)


class HistoricoEquivalencia(Base):
    """Dos códigos de artículo que son el MISMO modelo (0163): el interno de
    FACTUSOL y el de la web (60995 y 34, la Beambox 40W). `codart` apunta a
    su `canonico`; un grupo es el canónico más todos los que apuntan a él.
    Se edita desde la pantalla. Sirve para emparejar un albarán y su factura
    con códigos distintos, contar una máquina en el parque y encontrar las
    ventas de los dos en «Quién compró qué»."""

    __tablename__ = "historico_equivalencias"

    codart: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonico: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)


class HistoricoAliasArticulo(Base):
    """El código con que se tecleó una línea, que es un artículo del catálogo
    con otro código (0164): `053332` es `53332`, `8` es `00008`. Lo decide
    Bart caso a caso; no reescribe ni la línea ni el catálogo."""

    __tablename__ = "historico_alias_articulo"

    codigo: Mapped[str] = mapped_column(String(64), primary_key=True)
    codart: Mapped[str] = mapped_column(String(64), nullable=False)
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)


class HistoricoSerie(Base):
    """Si una serie de FACTUSOL cuenta como venta (0158). Sin fila, cuenta.
    Las que no cuentan quedan fuera del parque y de «Quién compró qué»."""

    __tablename__ = "historico_series"

    serie: Mapped[str] = mapped_column(String(8), primary_key=True)
    cuenta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)


class HistoricoGama(Base):
    """Una gama (UV LED, Láser…). La lista se amplía desde la pantalla."""

    __tablename__ = "historico_gamas"

    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(64), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class HistoricoReglaFamilia(Base):
    """Una regla que propone clase y gama para las familias que casan con
    ella. Gana la de menor `orden`. Clase o gama vacías = la regla no propone
    eso."""

    __tablename__ = "historico_reglas_familia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campo: Mapped[str] = mapped_column(String(16), nullable=False)
    patron: Mapped[str] = mapped_column(String(64), nullable=False)
    clase: Mapped[str | None] = mapped_column(String(16))
    gama: Mapped[str | None] = mapped_column(String(32))
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


# --- Parque instalado (0156) -----------------------------------------------------------


class ParqueMaquinaManual(Base):
    """Una máquina que no sale de las ventas volcadas y se añade a mano: las
    vendidas antes de 2014, las que entraron como línea de nota o las que el
    cliente compró en otro sitio."""

    __tablename__ = "parque_maquinas_manuales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid4()))
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    codart: Mapped[str | None] = mapped_column(String(64))
    descripcion: Mapped[str] = mapped_column(String(255), nullable=False)
    gama: Mapped[str | None] = mapped_column(String(32))
    fecha: Mapped[date | None] = mapped_column(Date)
    nota: Mapped[str | None] = mapped_column(String(255))
    creado_por: Mapped[str | None] = mapped_column(String(255))
    creado_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: Dada de baja (ya no está en servicio): deja de contar en el parque activo.
    baja_at: Mapped[datetime | None] = mapped_column(DateTime)
    baja_motivo: Mapped[str | None] = mapped_column(String(255))
    baja_por: Mapped[str | None] = mapped_column(String(255))


class ParqueBaja(Base):
    """Una máquina de las ventas volcadas dada de baja. La clave es estable a
    las recargas (las líneas cambian de id en cada carga):
    `ejercicio|tipo|serie|codigo|posicion|codart`.

    Limitación conocida: si alguien edita el documento en FACTUSOL y cambia el
    orden de las líneas, la posición cambia y la baja se pierde o cae en otra
    línea. En facturas antiguas no pasa."""

    __tablename__ = "parque_bajas"

    clave: Mapped[str] = mapped_column(String(160), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(String(36), index=True)
    motivo: Mapped[str | None] = mapped_column(String(255))
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)
