"""ERP · Cuadre — descuadres entre BoHub, FACTUSOL, Genei y la hoja de Drive.

El panel es SOLO LECTURA: estas tablas guardan lo que ENCUENTRAN las
comprobaciones (`app.erp.cuadre`), nunca corrigen nada.

  - `CuadreFinding`: un descuadre, identificado por `check_id + entidad_id`.
    Una pasada que ya no lo ve lo da por `resuelto` (no se borra nunca). Si
    alguien lo marca «revisado / no es un descuadre» con un motivo, no vuelve
    a aparecer mientras su `huella` (hash de los valores que lo motivan) no
    cambie; si cambia, vuelve a abrirse como nuevo.
  - `CuadreRun`: cada pasada (nocturna o «Comprobar ahora»), por fuente de
    datos (`mysql` / `factusol`): cuándo, cómo acabó y el resumen por
    comprobación. Alimenta «última pasada» y el «comprobando…» de la pantalla.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Estados de un descuadre.
ESTADO_ABIERTO = "abierto"
ESTADO_REVISADO = "revisado"
ESTADO_RESUELTO = "resuelto"
ESTADOS_FINDING: tuple[str, ...] = (ESTADO_ABIERTO, ESTADO_REVISADO, ESTADO_RESUELTO)

#: Estados de una pasada.
RUN_EN_COLA = "en_cola"
RUN_CORRIENDO = "corriendo"
RUN_OK = "ok"
RUN_CON_ERRORES = "con_errores"
RUN_ERROR = "error"
RUN_PENDIENTES: tuple[str, ...] = (RUN_EN_COLA, RUN_CORRIENDO)


class CuadreFinding(TimestampMixin, Base):
    """Un descuadre encontrado por una comprobación del Cuadre."""

    __tablename__ = "cuadre_findings"
    __table_args__ = (
        UniqueConstraint("check_id", "entidad_id", name="uq_cuadre_finding_entidad"),
        Index("ix_cuadre_findings_estado_check", "estado", "check_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: Id de la comprobación (`app.erp.cuadre.registry`).
    check_id: Mapped[str] = mapped_column(String(40), nullable=False)
    #: pedido | factura | presupuesto | fila_hoja.
    entidad_tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    #: `Order.id`, `serie-código` de la factura/proforma o id de la fila.
    entidad_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 de los valores que motivan el descuadre (no del texto).
    huella: Mapped[str] = mapped_column(String(64), nullable=False)
    #: JSON: etiqueta, detalle, enlace, pista de arreglo y datos.
    detalle_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: alta | media | baja (la de la comprobación).
    severidad: Mapped[str] = mapped_column(String(10), nullable=False)
    #: abierto | revisado | resuelto.
    estado: Mapped[str] = mapped_column(String(12), nullable=False, default=ESTADO_ABIERTO)
    #: Quién lo marcó «revisado / no es un descuadre» (User.id) y por qué.
    visto_por: Mapped[str | None] = mapped_column(String(36))
    motivo: Mapped[str | None] = mapped_column(String(255))
    revisado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: La primera vez que se vio; la última; cuándo se (re)abrió por última
    #: vez (para «solo nuevos») y cuándo se dio por resuelto.
    primera_vez_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultima_vez_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    abierto_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resuelto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CuadreRun(TimestampMixin, Base):
    """Una pasada del Cuadre sobre una fuente de datos."""

    __tablename__ = "cuadre_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: mysql | factusol.
    fuente: Mapped[str] = mapped_column(String(10), nullable=False)
    #: nocturno | manual.
    origen: Mapped[str] = mapped_column(String(10), nullable=False)
    #: en_cola | corriendo | ok | con_errores | error.
    estado: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    lanzado_por: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    #: JSON: por comprobación, hallazgos / nuevos / resueltos / error.
    resumen_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(String(500))
