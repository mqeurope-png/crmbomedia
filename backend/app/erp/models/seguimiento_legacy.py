"""Seguimiento (app) — histórico LEGACY de la hoja, importado tal cual.

Hito «id de pedido estable», Fase 1. La hoja de Drive lleva miles de filas de
histórico (~7792) que Bart editó a mano durante años; muchas NO tienen un pedido
de BoHub detrás (son anteriores al ERP), y otras sí (hay solapamiento
vivo↔histórico). Para dejar de casar filas por Nº —frágil: `9562.0`, con/sin
prefijo, cruces falsos tipo #387 `BOPRIN-99927`— cada fila necesita un **id
estable**.

Esta tabla importa ese histórico **verbatim** (una fila por línea, sin limpiar
fechas rotas ni números raros ni filas solo-cliente), cada una con un **id
sintético propio** (su PK). Está AISLADA de `orders`: no es un pedido real, no
cuenta en contadores/filtros, no toca FACTUSOL ni las máquinas de estado. El
backfill supervisado decide, fila a fila, si corresponde a un pedido real (→ se
le asigna el `Order.id` y se deduplica) o si se queda con su id sintético; las
coincidencias dudosas las revisa una persona antes de escribir nada.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Estado del casado de una fila legacy contra los pedidos reales de BoHub.
#:  - pending:   importada, aún sin analizar por el backfill.
#:  - synthetic: no hay pedido real detrás → se queda con su id sintético (PK).
#:  - proposed:  el backfill propone un `Order.id` claro (a confirmar/aplicar).
#:  - dubious:   coincidencia AMBIGUA (Nº/cliente con riesgo de falso positivo,
#:               tipo #387) → la revisa una persona antes de aplicar.
#:  - confirmed: casado con un `Order.id` ya aceptado/aplicado.
LEGACY_MATCH_STATUSES = ("pending", "synthetic", "proposed", "dubious", "confirmed")


class SeguimientoLegacy(TimestampMixin, Base):
    """Una fila del histórico de la hoja, importada tal cual, con id estable."""

    __tablename__ = "seguimiento_legacy"

    #: id SINTÉTICO estable de la fila legacy (su clave en la hoja si no casa con
    #: un pedido real). No es un `Order.id`: vive en su propio espacio.
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: Orden original en el bloque de histórico (para reescribir sin barajar).
    row_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Celdas de MATCHING, en bruto (como venían en la hoja, sin normalizar):
    #: «Nº pedido», «Cliente» y «Fecha». Sirven para PROPONER el casado; el
    #: backfill decide, nunca se limpian aquí.
    numero_raw: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    cliente_raw: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    fecha_raw: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: La fila ENTERA tal cual (JSON de todas las celdas), para poder reescribirla
    #: byte a byte y no perder nada de lo que Bart tecleó.
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: Casado con un pedido real de BoHub (su `Order.id`), si el backfill lo
    #: asignó. Referencia BLANDA (sin FK): la tabla no se acopla a `orders` para
    #: no contaminar sus contadores ni arrastrar cascadas.
    matched_order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    #: Estado del casado (ver LEGACY_MATCH_STATUSES).
    match_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    #: Por qué es dudosa, o qué candidato(s) propone el backfill (para la
    #: revisión de la persona). Texto libre.
    match_note: Mapped[str | None] = mapped_column(Text)

    @property
    def stable_id(self) -> str:
        """El id estable de la fila: el del pedido real si se confirmó, o el
        sintético (su PK) si no hay pedido detrás."""
        if self.match_status == "confirmed" and self.matched_order_id:
            return self.matched_order_id
        return self.id
