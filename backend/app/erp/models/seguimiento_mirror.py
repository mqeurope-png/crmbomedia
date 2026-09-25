"""Seguimiento (app) — estado del ESPEJO bidireccional BoHub ↔ hoja (Fase 2).

BoHub es la fuente de verdad; la hoja de Drive es una proyección que se
sincroniza en los dos sentidos, casada por id (`Order.id` para las filas de
BoHub, el id de `seguimiento_legacy` para el histórico, el de
`seguimiento_manual` para las filas tecleadas a mano).

  - `SeguimientoSnapshot`: la ÚLTIMA foto que el reconcile escribió en la hoja,
    fila a fila por id. Comparando hoja ↔ foto se sabe qué ha tocado una
    persona (y qué fila ha desaparecido); comparando foto ↔ BD, qué ha cambiado
    BoHub.
  - `SeguimientoOverride`: lo que una persona escribió a mano en una columna
    editable de una fila de BoHub (Cliente, Factura, Factura enviada, Nº serie ·
    WhiteRIP, Nota). Manda sobre el valor de BoHub. Es un dato del SEGUIMIENTO:
    nunca se copia a los campos del pedido que alimentan FACTUSOL / cobro /
    workflows.
  - `SeguimientoManual`: las filas tecleadas a mano (Origen = MANUAL) que BoHub
    validó e ingirió, con su id estable. Borrado LÓGICO (`deleted_at`).
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Tipos de fila del espejo (columna `kind` del snapshot).
KIND_ORDER = "order"
KIND_LEGACY = "legacy"
KIND_MANUAL = "manual"


class SeguimientoSnapshot(TimestampMixin, Base):
    """Última foto escrita en la hoja para una fila (por su id estable)."""

    __tablename__ = "seguimiento_sync_snapshot"

    #: id estable de la fila tal como va en la columna «id» de la hoja.
    row_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: order | legacy | manual.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: JSON: los valores de la fila tal como el reconcile los ESCRIBIÓ.
    values_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class SeguimientoOverride(TimestampMixin, Base):
    """Valor escrito a mano en una columna editable de una fila de BoHub."""

    __tablename__ = "seguimiento_overrides"
    __table_args__ = (
        UniqueConstraint("order_id", "column_key", name="uq_seguimiento_override_col"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    #: Clave lógica de la columna (ver `seguimiento_mirror.OVERRIDE_COLUMNS`).
    column_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: El valor tal como se leyó de la hoja (fechas en ISO).
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class SeguimientoManual(TimestampMixin, Base):
    """Fila tecleada a mano en la hoja, ya validada e ingerida por BoHub."""

    __tablename__ = "seguimiento_manual"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: id que lleva la fila en la columna «id» de la hoja: el suyo propio o, si
    #: la fila se cruzó con un pedido de BoHub (#466), el `Order.id`.
    row_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    #: Pedido de BoHub con el que se cruzó (si lo hay).
    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    #: JSON: la fila tal como está en la hoja (la última versión VÁLIDA).
    values_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: Borrado lógico: alguien quitó la fila de la hoja. Se conserva (se puede
    #: recuperar) pero ya no se pinta.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
