"""BoHub ERP — Bandeja de excepciones (Fase A, migración 0080).

Tabla NUEVA `exceptions` (decisión cerrada nº9: no se extiende sync_logs).
Catálogo cerrado con Bart: 9 tipos + subtipos de stock. La clase se llama
`ErpException` (no `Exception`) para no sombrear el builtin de Python; la
tabla mantiene el nombre `exceptions` acordado.

El chip de alerta cuando `metadata.eta_date <= NOW()` es presentación (UI):
la excepción NO se resuelve sola.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values


class ExceptionType(StrEnum):
    # Stock (bloquean cola SAT; SAT las reporta al descubrir el problema).
    STOCK_SHORTAGE = "stock_shortage"          # subtipos abajo
    MATERIAL_DEFECTIVE = "material_defective"  # llegó defectuoso, repedir
    # Preparación (durante trabajo SAT).
    SAT_ISSUE = "sat_issue"
    SIZE_EXCEEDS_CARRIER = "size_exceeds_carrier"
    BLOCKED_BY_CUSTOMER_REQUEST = "blocked_by_customer_request"
    # Transporte (post envío).
    CARRIER_INCIDENT = "carrier_incident"
    RETURNED_BY_TRANSPORT = "returned_by_transport"
    # Facturación (post envío, NO bloquea envío).
    FACTUSOL_WRITE_FAILED = "factusol_write_failed"
    INVOICE_EMAIL_FAILED = "invoice_email_failed"


#: Subtipos válidos por tipo (solo stock_shortage los tiene en el catálogo).
EXCEPTION_SUBTYPES: dict[ExceptionType, set[str]] = {
    ExceptionType.STOCK_SHORTAGE: {
        "pending_purchase",   # pendiente de compra
        "eta_set",            # pedido con ETA (metadata: eta_date, provider)
        "eta_unknown",        # pedido sin ETA
        "not_replenishable",  # descatalogado
    },
}


class ExceptionStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ErpException(TimestampMixin, Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        Index("idx_exceptions_status_type", "status", "type"),
        Index("idx_exceptions_order", "order_id"),
        Index("idx_exceptions_assigned", "assigned_to_user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    type: Mapped[ExceptionType] = mapped_column(
        Enum(ExceptionType, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
    )
    subtype: Mapped[str | None] = mapped_column(String(32))
    # Específicos por tipo: {"eta_date": "...", "provider": "..."} en
    # stock_shortage:eta_set; descripción libre en sat_issue; etc.
    metadata_json: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    reported_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_to_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ExceptionStatus] = mapped_column(
        Enum(
            ExceptionStatus, native_enum=False,
            values_callable=enum_values, length=16,
        ),
        nullable=False,
        default=ExceptionStatus.OPEN,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
