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
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# `orders.store_id` referencia `integration_accounts` (modelo en
# `app.models.integration_settings`). Se importa AQUÍ, junto al FK, para que
# esa tabla esté siempre en el MetaData allí donde esté `Order`: el unit of
# work resuelve el FK al ordenar tablas en CADA flush de `orders`
# (`Mapper._sorted_tables` → `NoReferencedTableError` si falta), y en el
# worker RQ —que no importa `app.main`— nadie más la registraba. Hasta #391
# lo hacía por casualidad `quotes.convert_quote_to_order` (importaba
# `app.erp.api.orders`); al reescribirlo, convertir una proforma en pedido
# (y el albarán / pago de la Fase 2) reventaba en `worker-factusol`.
from app.models import integration_settings as _integration_settings  # noqa: F401
from app.models.crm import Base, TimestampMixin, enum_values


class OrderSource(StrEnum):
    WOOCOMMERCE = "woocommerce"
    FACTUSOL_PROFORMA = "factusol_proforma"
    # Fase 1: pedido creado desde un PEDIDO DE CLIENTE de FACTUSOL (F_PCL).
    # Enum no nativo (texto, length=40): sin migración.
    FACTUSOL_PEDIDO = "factusol_pedido"
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
    # WooCommerce: estado CRUDO del pedido en el origen ("processing",
    # "completed", "cancelled", "refunded", "failed", "pending", "on-hold",
    # "trash"…). Se refresca SIEMPRE desde la fuente (webhook/reconciliación):
    # es la base para sacar del seguimiento los cancelados/fallidos y los
    # reembolsos no cumplidos. Es INDEPENDIENTE de la exclusión manual (F6-fix7)
    # y de las máquinas de estado propias de BoHub. NULL = aún no se conoce
    # (pedidos importados antes de este cambio, o pedidos no-Woo).
    woo_status: Mapped[str | None] = mapped_column(String(20))
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

    # Fase 2: nº del albarán FACTUSOL (`serie-código`, p. ej. `5-500008`) que
    # BoHub creó en F_ALB al convertir la proforma / pedido de cliente en
    # pedido. NULL si no hay albarán. Los pedidos web NUNCA lo llevan (el
    # albarán lo crea WooCommerce). Indexado: al facturar ese albarán desde el
    # explorador se localiza el pedido para vincular la factura y registrar el
    # cobro apuntado (opción B).
    factusol_albaran_number: Mapped[str | None] = mapped_column(
        String(32), index=True,
    )

    # Cobro manual (F-4-B desde la app). El pedido solo guarda el CODFAC de su
    # factura; la SERIE (TIPFAC, clave compuesta) se resuelve una vez y se
    # guarda aquí. El estado de cobro EN FACTUSOL («cobrada» = ESTFAC=2 /
    # saldo 0 en F_LCO; «pendiente» = factura emitida sin cobro completo) se
    # persiste para verlo fila a fila en la bandeja y filtrar, y se refresca
    # en vivo desde la ficha, el modal, el job de cobro y «Actualizar cobros».
    # Es el estado CONTABLE, distinto del «Pagado» del CRM (`payment_status`).
    # NULL = sin factura o aún sin comprobar. Detalle en `packing_json.
    # factusol_cobro` (nº, total, cobrado, saldo, ESTFAC, nº de líneas).
    factusol_invoice_serie: Mapped[int | None] = mapped_column(Integer)
    factusol_cobro_status: Mapped[str | None] = mapped_column(String(16), index=True)
    factusol_cobro_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # ERP-F6-fix7 — EXCLUIR del seguimiento (decisión de Bart). Un pedido
    # excluido no se lista en el seguimiento, no se inserta en la hoja de Drive,
    # no se actualiza y no se cuenta. Es REVERSIBLE (volver a incluir) y NO borra
    # ni modifica el pedido en BoHub ni en FACTUSOL: solo lo saca del
    # seguimiento. Se registra quién y cuándo, con un motivo opcional.
    # OJO: «excluido» es distinto de «escrito en Drive» (esto último vive en
    # `erp_drive_sync_rows.synced_at`): escribirse en la hoja NO excluye.
    seguimiento_excluded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    seguimiento_excluded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    seguimiento_excluded_reason: Mapped[str | None] = mapped_column(Text)

    # «Marcar completado» (decisión de Bart, SOLO BoHub): estado FINAL del
    # pedido — ya facturado y enviado, aunque el envío se tramite fuera de
    # BoHub. Manual y reversible («Desmarcar»); no exige que Transporte esté
    # «enviado» ni que haya factura (solo se avisa). NUNCA se propaga a
    # WooCommerce: el estado de la tienda no cambia.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # E4-fix1: idioma del pedido (ISO 639-1: es/en/de/fr/nl), detectado en la
    # importación Woo (WPML/locale/país) o corregido a mano en la ficha.
    # NULL = desconocido — la cascada de idioma del PDF cae al cliente o a la
    # empresa emisora, nunca se inventa aquí.
    language: Mapped[str | None] = mapped_column(String(5))

    # ERP-F6 — campos del Excel de seguimiento que BoHub no tenía.
    # Nº de serie del equipo: texto LIBRE — en el Excel real también se usa
    # para notas («FINALIZADO, + RMA TRANSPORTE»), así que no se valida.
    serial_number: Mapped[str | None] = mapped_column(Text)
    # Licencia de WhiteRIP (software que Bart vende a veces con el equipo).
    whiterip_license: Mapped[str | None] = mapped_column(String(64))
    # Origen del envío — el «OFI-TER-SAT» del Excel: de dónde sale la
    # mercancía (SAT, OFI, TER, directo, INSITU…). Lista configurable en
    # /erp/settings; aquí no se valida (el Excel tampoco lo hacía).
    shipping_origin: Mapped[str | None] = mapped_column(String(40))

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
