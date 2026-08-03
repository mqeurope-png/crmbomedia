"""BoHub ERP — Inbox de webhooks (Fase B, migración 0081).

Tabla nueva `integration_events` (decisión cerrada nº8): TODO webhook
entrante (WooCommerce order.created/updated, Genei shipment.updated…) se
persiste aquí ANTES de procesarse — el endpoint responde 200 inmediato y
un worker RQ consume la row. Ventajas:

- **Idempotencia**: el UNIQUE (system, account_id, external_event_id)
  hace que WooCommerce reenviando el mismo evento no cree pedidos dobles.
- **Reintentos con backoff exponencial**: 1min → 5min → 15min → 1h → 6h;
  tras 5 fallos, status=failed + excepción visible en la bandeja del ERP.
- **Trazabilidad**: el payload crudo queda para reconstruir el flujo.

`external_event_id` = el id que da el sistema remoto (X-WC-Webhook-Delivery
en Woo, `shipment_reference` en Genei…). El worker que lo procesa vive en
worker-sync (patrón del CRM).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values


class IntegrationEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    # B-3: topic no soportado (p.ej. product.updated, ping) — se registra
    # para trazabilidad pero no dispara import. VARCHAR(16), sin migración.
    IGNORED = "ignored"


#: Backoff exponencial en segundos por intento (índice = retry_count antes
#: de incrementar). 5 intentos totales → tras el 5º, se marca failed.
RETRY_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)
MAX_RETRIES = len(RETRY_BACKOFF_SECONDS)


class IntegrationEvent(TimestampMixin, Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint(
            "system", "account_id", "external_event_id",
            name="uq_integration_events_dedup",
        ),
        Index("idx_integration_events_status_retry", "status", "next_retry_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: `woocommerce` | `genei` | `factusol` | `dsv` (string libre — no
    #: reusa el enum ExternalSystem porque puede llegar cualquier fuente).
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    #: slug de la cuenta en integration_accounts (p.ej. `boprint`,
    #: `bomedia-envios`). En Genei que es cuenta única, valor fijo.
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[IntegrationEventStatus] = mapped_column(
        Enum(
            IntegrationEventStatus, native_enum=False,
            values_callable=enum_values, length=16,
        ),
        nullable=False,
        default=IntegrationEventStatus.RECEIVED,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
