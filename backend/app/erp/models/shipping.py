"""BoHub ERP — Expedición manual (Fase D, migración 0085).

Dos tablas nuevas para operar el flujo de envío sin depender aún de la API de
Genei/DSV:

- `shipment_packages`: bultos de un pedido (multi-bulto obligatorio). Un pedido
  solo pasa a `packed` cuando todos sus bultos tienen peso + 3 dimensiones.
- `shipment_files`: albaranes y etiquetas guardados (descargados de Woo o
  subidos a mano). Al subir uno nuevo del mismo `kind`, el anterior se marca
  `replaced_at` pero se conserva; solo el último no-reemplazado se muestra.

Los bytes viven en el servicio de storage abstracto (`app/storage`), no en la
BD: aquí solo se guarda la ruta relativa (`storage_path`).
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin

#: Tipos de documento de expedición.
KIND_ALBARAN = "albaran"
KIND_ETIQUETA = "etiqueta"
SHIPMENT_FILE_KINDS = frozenset({KIND_ALBARAN, KIND_ETIQUETA})

#: Origen del fichero.
SOURCE_WOO_PDF_PLUGIN = "woo_pdf_plugin"   # descarga automática del plugin PDF de Woo
SOURCE_MANUAL_UPLOAD = "manual_upload"     # subido a mano por el operativo
SOURCE_FACTUSOL_PDF = "factusol_pdf"       # futuro: PDF generado desde FACTUSOL
SOURCE_CRM_GENERATED_PDF = "crm_generated_pdf"  # albarán generado por el CRM (reportlab)


class ShipmentPackage(TimestampMixin, Base):
    """Un bulto del pedido. `position` es 1..N por orden de creación."""

    __tablename__ = "shipment_packages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    weight_kg: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    height_cm: Mapped[int] = mapped_column(Integer, nullable=False)
    width_cm: Mapped[int] = mapped_column(Integer, nullable=False)
    depth_cm: Mapped[int] = mapped_column(Integer, nullable=False)


class ShipmentFile(Base):
    """Albarán o etiqueta guardado. Historial completo: un pedido puede tener
    varios ficheros del mismo `kind`; solo el último con `replaced_at IS NULL`
    es el vigente."""

    __tablename__ = "shipment_files"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # kind: albaran | etiqueta
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # source: woo_pdf_plugin | manual_upload | factusol_pdf
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Seteado cuando otro fichero del mismo kind lo sustituye (se conserva).
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
