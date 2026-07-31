"""BoHub ERP — Mapping SKU WooCommerce ↔ CODART FACTUSOL (Fase A, 0080).

La facturación SOLO usa mappings con `confirmed_at NOT NULL` (guard del
PR 2 vía `order_lines.product_codart`). El seed masivo llega en Fase B con
el script de conciliación del Sprint 0; en Fase A los mappings se crean a
mano si hace falta probar el flujo.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin, enum_values


class SkuMatchedBy(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class ProductSkuMapping(TimestampMixin, Base):
    __tablename__ = "product_sku_mapping"
    __table_args__ = (
        UniqueConstraint("store_id", "woo_sku", name="uq_sku_mapping_store_sku"),
        Index("idx_sku_mapping_codart", "factusol_codart"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    woo_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    # Tienda (integration_accounts) — NULL para mappings globales/manuales
    # mientras las cuentas Woo no existan (Fase B las crea).
    store_id: Mapped[str | None] = mapped_column(
        ForeignKey("integration_accounts.id", ondelete="CASCADE")
    )
    factusol_codart: Mapped[str] = mapped_column(String(13), nullable=False)
    matched_by: Mapped[SkuMatchedBy] = mapped_column(
        Enum(SkuMatchedBy, native_enum=False, values_callable=enum_values, length=16),
        nullable=False,
        default=SkuMatchedBy.MANUAL,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
