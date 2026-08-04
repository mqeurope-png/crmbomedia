"""BoHub ERP — Pedidos (Fase A, migración 0080).

Un pedido lleva 4 máquinas de estado INDEPENDIENTES (pago / preparación /
transporte / facturación) — columnas Enum propias + historial completo en
`order_status_history` para KPIs de tiempos (decisión cerrada nº5). Las
transiciones válidas viven en `app.erp.state_machine.definitions` (PR 2);
los modelos no imponen flujo, solo estados legales.

Sin tabla `shipments` en Fase A: `tracking_number`/`carrier_id`/packing
viven en el pedido (una expedición por pedido). Si Fase B+ trae multi-bulto
real, se extrae a tabla propia.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.crm import Base, TimestampMixin, enum_values


class OrderSource(StrEnum):
    WOOCOMMERCE = "woocommerce"
    FACTUSOL_PROFORMA = "factusol_proforma"
    MANUAL = "manual"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PARTIAL_PAID = "partial_paid"
    CREDIT_APPROVED = "credit_approved"  # B2B con crédito aprobado
    FAILED = "failed"
    REFUNDED = "refunded"


class PreparationStatus(StrEnum):
    PENDING_REVIEW = "pending_review"  # Cola PEDIDOS (aprobación)
    IN_QUEUE = "in_queue"              # aprobado → Cola SAT
    PREPARING = "preparing"
    PACKED = "packed"
    BLOCKED = "blocked"
    # B-2-fix4: gestionado fuera del sistema (Excel/proceso anterior).
    ALREADY_COMPLETED_EXTERNALLY = "already_completed_externally"


class TransportStatus(StrEnum):
    NOT_SHIPPED = "not_shipped"
    LABEL_CREATED = "label_created"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    INCIDENT = "incident"
    RETURNED = "returned"
    ALREADY_SHIPPED_EXTERNALLY = "already_shipped_externally"


class InvoiceStatus(StrEnum):
    NOT_INVOICED = "not_invoiced"
    PENDING = "pending"
    GENERATED = "generated"
    ERROR = "error"
    CREDIT_NOTE = "credit_note"
    ALREADY_INVOICED_EXTERNALLY = "already_invoiced_externally"
    # Fase C: factura emitida en FACTUSOL desde el ERP (via emit_invoice).
    INVOICED_BY_ERP = "invoiced_by_erp"


class StatusDomain(StrEnum):
    PAYMENT = "payment"
    PREPARATION = "preparation"
    TRANSPORT = "transport"
    INVOICE = "invoice"


def _enum(enum_cls: type[StrEnum], **kw):
    # length=40 cubre los valores «already_*_externally» (B-2-fix4, hasta 28
    # chars). Las columnas se amplían a VARCHAR(40) en la migración 0082.
    return Enum(enum_cls, native_enum=False, values_callable=enum_values, length=40, **kw)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_prep_status", "preparation_status"),
        Index("idx_orders_source_external", "external_source", "external_id"),
        Index("idx_orders_placed", "placed_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    external_source: Mapped[OrderSource] = mapped_column(
        _enum(OrderSource), nullable=False, default=OrderSource.MANUAL
    )
    external_id: Mapped[str | None] = mapped_column(String(64))
    # FK a integration_accounts cuando external_source='woocommerce'
    # (multi-tienda, Fase B). NULL en manual/proforma.
    store_id: Mapped[str | None] = mapped_column(
        ForeignKey("integration_accounts.id", ondelete="SET NULL")
    )
    order_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    total_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    payment_status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING
    )
    preparation_status: Mapped[PreparationStatus] = mapped_column(
        _enum(PreparationStatus), nullable=False,
        default=PreparationStatus.PENDING_REVIEW,
    )
    transport_status: Mapped[TransportStatus] = mapped_column(
        _enum(TransportStatus), nullable=False, default=TransportStatus.NOT_SHIPPED
    )
    invoice_status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus), nullable=False, default=InvoiceStatus.NOT_INVOICED
    )

    carrier_id: Mapped[str | None] = mapped_column(
        ForeignKey("carriers.id", ondelete="SET NULL")
    )
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    # Peso/dimensiones/bultos que introduce SAT al embalar (PR 5):
    # {"weight_kg": 12.5, "dimensions_cm": "60x40x30", "packages": 1}
    packing_json: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    # Aprobación en Cola PEDIDOS (PR 3).
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Fecha real del pedido en el sistema de origen (Woo) o de alta manual.
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # B-2-fix4: «Marcar como procesado externamente». Cuando está seteado,
    # el pedido se gestionó fuera del ERP (Excel/proceso anterior o previo a
    # la fecha de corte de la tienda) — sale de las colas activas y se
    # muestra con badge gris «Externalizado».
    externally_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    externally_processed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    externally_processed_note: Mapped[str | None] = mapped_column(Text)

    # Fase C: número de la factura emitida en FACTUSOL (CODFAC) — vincula el
    # pedido con su factura contable. NULL hasta emitir.
    factusol_invoice_number: Mapped[str | None] = mapped_column(String(32))

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan",
        order_by="OrderLine.position",
    )
    status_history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", cascade="all, delete-orphan",
        order_by="OrderStatusHistory.changed_at",
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    product_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    # CODART FACTUSOL resuelto vía product_sku_mapping. NULL = sin mapear
    # (bloquea la factura, guard del PR 2; bloqueo visible en Cola PEDIDOS).
    product_codart: Mapped[str | None] = mapped_column(String(13))
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=21)
    line_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderStatusHistory(Base):
    """Cada transición de cualquiera de los 4 dominios — base de los KPIs
    de tiempos (cuánto tarda un pedido en cada estado)."""

    __tablename__ = "order_status_history"
    __table_args__ = (
        Index("idx_osh_order_domain", "order_id", "domain", "changed_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[StatusDomain] = mapped_column(_enum(StatusDomain), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    changed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="status_history")
