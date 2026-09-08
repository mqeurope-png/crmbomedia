"""ERP-F6 — estado de sincronización con la hoja de seguimiento de Drive.

Una fila por pedido sincronizado: la clave con la que se localiza su fila en
la hoja (columna «Albarán / Nº Pedido Web») y la ÚLTIMA foto de valores que
BoHub escribió. Esa foto es lo que permite distinguir «la celda sigue como la
dejé → puedo actualizarla» de «alguien la tocó a mano → conflicto, no tocar».
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin


class ErpDriveSyncRow(TimestampMixin, Base):
    __tablename__ = "erp_drive_sync_rows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: Valor de «Albarán / Nº Pedido Web» con el que se localiza la fila.
    row_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: JSON: lista de 17 valores tal como BoHub los dejó en la hoja.
    last_values_json: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
