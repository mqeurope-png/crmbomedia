"""BoHub ERP — Configuración global (Fase A, migración 0080).

Singleton (una sola fila, id fijo "singleton") — mismo patrón que otras
configs globales del CRM. Editable solo por ADMIN vía
GET/PATCH /api/erp/settings (PR 6).
"""
from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values

ERP_SETTINGS_SINGLETON_ID = "singleton"


class InvoiceMode(StrEnum):
    MANUAL = "manual"          # siempre revisión humana
    AUTO = "auto"              # factura automática al cobrar
    AUTO_UNDER_MAX = "auto_under_max"  # auto solo bajo el importe máximo


class ErpSettings(TimestampMixin, Base):
    __tablename__ = "erp_settings"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=ERP_SETTINGS_SINGLETON_ID
    )
    default_invoice_mode: Mapped[InvoiceMode] = mapped_column(
        Enum(InvoiceMode, native_enum=False, values_callable=enum_values, length=16),
        nullable=False,
        default=InvoiceMode.MANUAL,
    )
    # Umbral para auto_under_max (EUR). NULL = sin límite.
    auto_invoice_max_amount_eur: Mapped[float | None] = mapped_column(Numeric(12, 2))
    default_carrier_id: Mapped[str | None] = mapped_column(
        ForeignKey("carriers.id", ondelete="SET NULL")
    )
    factusol_default_ejercicio: Mapped[str | None] = mapped_column(String(4))
    # Flag reservado para la Fase C. Tras B-2-fix5 el ERP confía en la
    # fuente y NO valida SKU ni empresas contra FACTUSOL, así que este
    # toggle es hoy un no-op; se conserva por si Fase C lo aprovecha.
    factusol_live: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
