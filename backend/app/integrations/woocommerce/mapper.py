"""Mapper WooCommerce Order → BoHub ERP Order (Fase B PR B-2).

Reglas del spec (decisiones cerradas):
- external_source = 'woocommerce', store_id = integration_accounts.id.
- payment_status: paid si date_paid seteado; pending si status=processing
  sin date_paid (excepción visible en Cola PEDIDOS, no aprobable).
- preparation_status = pending_review (entra en Cola PEDIDOS).
- Auto-crea contact por email si no existe.
- Auto-crea company por (CIF+razón social) si el pedido los trae y no
  existe empresa con ese CIF.
- Cada línea busca product_sku_mapping por (woo_sku, store_id) con
  confirmed_at NOT NULL → si existe, setea codart; si no, la línea queda
  con codart NULL y NO se crea ninguna excepción. El ERP confía en la
  fuente: un pedido de WooCommerce es válido tal cual llega (B-2-fix5).

Idempotente: si ya existe Order con (external_source='woocommerce',
external_id=str(woo_id), store_id) hace UPDATE conservativo (no pisa
estados avanzados; refresca líneas si el pedido Woo cambió).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.models import (
    Order,
    OrderLine,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ProductSkuMapping,
)
from app.models.crm import Company, Contact
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ImportOutcome:
    order_id: str
    created: bool          # True si es alta, False si update
    contact_created: bool
    company_created: bool
    unmapped_skus: list[str]


def import_woo_order(
    session: Session, *, store: IntegrationAccount, woo_order: dict[str, Any],
) -> ImportOutcome:
    """Idempotente + dedup por (external_source, external_id, store_id)."""
    external_id = str(woo_order.get("id") or "")
    if not external_id:
        raise ValueError("payload sin id de pedido Woo")

    email = _pick_email(woo_order)
    contact, contact_created = _resolve_contact(session, email, woo_order)

    company, company_created = _resolve_company(session, woo_order)
    if company is not None and contact.company_id is None:
        contact.company_id = company.id
        session.flush()

    existing = session.scalar(select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id == external_id,
        Order.store_id == store.id,
    ))
    if existing is not None:
        _refresh_existing(session, existing, woo_order, contact, company, store)
        return ImportOutcome(
            order_id=existing.id, created=False,
            contact_created=contact_created, company_created=company_created,
            unmapped_skus=_unmapped_from(session, existing.id),
        )

    order = _create_order(session, woo_order, store, contact, company)
    session.flush()
    _apply_lines(session, order, woo_order, store)
    session.flush()
    # B-2-fix4: pedidos anteriores a la fecha de corte de la tienda se
    # auto-marcan como procesados externamente (no entran a Cola PEDIDOS).
    _auto_mark_external_if_before_cutoff(session, order, store)
    return ImportOutcome(
        order_id=order.id, created=True,
        contact_created=contact_created, company_created=company_created,
        unmapped_skus=_unmapped_from(session, order.id),
    )


# --- contact / company ------------------------------------------------------


def _pick_email(woo: dict[str, Any]) -> str:
    billing = woo.get("billing") or {}
    raw = (billing.get("email") or "").strip().lower()
    if not raw or not _EMAIL_RE.match(raw):
        raise ValueError(f"pedido Woo sin email válido: {raw!r}")
    return raw


def _resolve_contact(
    session: Session, email: str, woo: dict[str, Any],
) -> tuple[Contact, bool]:
    existing = session.scalar(
        select(Contact).where(func.lower(Contact.email) == email)
    )
    if existing is not None:
        return existing, False
    billing = woo.get("billing") or {}
    contact = Contact(
        first_name=(billing.get("first_name") or email.split("@")[0] or "Lead").strip(),
        last_name=(billing.get("last_name") or "").strip() or None,
        email=email,
        phone=(billing.get("phone") or "").strip() or None,
        origin="woocommerce",
        origin_account_id=f"woocommerce:{woo.get('_store_slug', '')}",
        address_city=(billing.get("city") or "").strip() or None,
        address_country=(billing.get("country") or "").strip() or None,
        address_postal_code=(billing.get("postcode") or "").strip() or None,
        address_line=", ".join(
            p for p in [
                (billing.get("address_1") or "").strip(),
                (billing.get("address_2") or "").strip(),
            ] if p
        ) or None,
    )
    session.add(contact)
    session.flush()
    return contact, True


def _resolve_company(
    session: Session, woo: dict[str, Any],
) -> tuple[Company | None, bool]:
    """Auto-crea empresa si el billing trae CIF + razón social. Sin CIF NO
    hay empresa (evitamos duplicar 'Particular' N veces)."""
    billing = woo.get("billing") or {}
    name = (billing.get("company") or "").strip()
    cif = _extract_cif(woo)
    if not (name and cif):
        return None, False
    normalised_cif = cif.upper().replace(" ", "").replace("-", "")
    existing = session.scalar(select(Company).where(
        func.upper(func.replace(func.replace(Company.tax_id, " ", ""), "-", ""))
        == normalised_cif
    ))
    if existing is not None:
        return existing, False
    company = Company(name=name, tax_id=normalised_cif, source="woocommerce")
    session.add(company)
    session.flush()
    return company, True


def _extract_cif(woo: dict[str, Any]) -> str | None:
    """WooCommerce España guarda el CIF en `billing.vat` (Plugin YITH/
    similar) o en `meta_data` con clave `_billing_nif`/`billing_nif`."""
    billing = woo.get("billing") or {}
    if billing.get("vat"):
        return str(billing["vat"]).strip()
    for meta in woo.get("meta_data") or []:
        key = (meta.get("key") or "").lower().lstrip("_")
        if key in {"billing_nif", "billing_cif", "billing_vat_id", "vat_id"}:
            val = str(meta.get("value") or "").strip()
            if val:
                return val
    return None


# --- order + líneas ---------------------------------------------------------


def _payment_status(woo: dict[str, Any]) -> PaymentStatus:
    if woo.get("date_paid"):
        return PaymentStatus.PAID
    return PaymentStatus.PENDING


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _order_number(woo: dict[str, Any]) -> str:
    """Prefijo con el nombre corto de la tienda para evitar colisiones con
    los manuales del CRM (MAN-N) — decisión operativa."""
    slug = woo.get("_store_slug") or "woo"
    number = str(woo.get("number") or woo.get("id"))
    return f"{slug.upper()[:6]}-{number}"


def _create_order(
    session: Session, woo: dict[str, Any], store: IntegrationAccount,
    contact: Contact, company: Company | None,
) -> Order:
    order = Order(
        external_source=OrderSource.WOOCOMMERCE,
        external_id=str(woo.get("id")),
        store_id=store.id,
        order_number=_order_number(woo),
        contact_id=contact.id,
        company_id=company.id if company else None,
        total_amount=float(woo.get("total") or 0),
        currency=(woo.get("currency") or "EUR")[:3],
        payment_status=_payment_status(woo),
        preparation_status=PreparationStatus.PENDING_REVIEW,
        placed_at=_parse_dt(woo.get("date_created")) or datetime.now(UTC),
    )
    session.add(order)
    return order


def _refresh_existing(
    session: Session, order: Order, woo: dict[str, Any],
    contact: Contact, company: Company | None, store: IntegrationAccount,
) -> None:
    """UPDATE conservativo. NO pisa preparation_status ni estados
    avanzados (el ERP puede tener el pedido ya aprobado / embalado; solo
    refresca datos económicos y detección de pago."""
    _ = store  # firma incluye store para simetría con _create_order
    order.total_amount = float(woo.get("total") or order.total_amount)
    order.currency = (woo.get("currency") or order.currency)[:3]
    # Detección de pago: si Woo ya tiene date_paid y el ERP seguía pending
    # → promociona a paid (el estado avanzará en la máquina en su momento).
    new_payment = _payment_status(woo)
    if order.payment_status == PaymentStatus.PENDING and new_payment == PaymentStatus.PAID:
        order.payment_status = PaymentStatus.PAID
    # Si no había contact/company vinculado (rare), engancha ahora.
    if order.contact_id is None:
        order.contact_id = contact.id
    if company is not None and order.company_id is None:
        order.company_id = company.id


def _apply_lines(
    session: Session, order: Order, woo: dict[str, Any], store: IntegrationAccount,
) -> None:
    """Crea las líneas del pedido. Si el SKU tiene un mapping CONFIRMADO se
    rellena el CODART; si no, queda NULL y no se hace nada más — el ERP
    confía en la fuente y no crea excepciones sintéticas (B-2-fix5)."""
    for i, li in enumerate(woo.get("line_items") or []):
        sku = str(li.get("sku") or "").strip()
        if not sku:
            sku = f"woo-pid-{li.get('product_id') or li.get('id')}"
        qty = float(li.get("quantity") or 0)
        line_total = float(li.get("total") or 0)
        unit_price = round(line_total / qty, 4) if qty else 0.0
        codart = _resolve_codart(session, sku, store.id)
        session.add(OrderLine(
            order_id=order.id, position=i, product_sku=sku,
            product_codart=codart,
            description=(li.get("name") or sku)[:255],
            quantity=qty, unit_price=unit_price,
            tax_rate=float(li.get("tax_class") == "reduced-rate" and 10 or 21),
            line_total=line_total,
        ))


def _resolve_codart(
    session: Session, woo_sku: str, store_id: str,
) -> str | None:
    """Solo mappings CONFIRMADOS mapean automáticamente. Los propuestos
    (fuzzy sin confirmar) requieren revisión en `/admin/erp/sku-mapping`
    (PR B-5)."""
    row = session.scalar(select(ProductSkuMapping).where(
        ProductSkuMapping.woo_sku == woo_sku,
        ProductSkuMapping.store_id == store_id,
        ProductSkuMapping.confirmed_at.is_not(None),
    ))
    return row.factusol_codart if row else None


def _store_cutoff(store: IntegrationAccount) -> datetime | None:
    if not store.metadata_json:
        return None
    try:
        meta = json.loads(store.metadata_json)
    except (TypeError, ValueError):
        return None
    return _parse_dt(meta.get("external_cutoff_date")) if isinstance(meta, dict) else None


def _auto_mark_external_if_before_cutoff(
    session: Session, order: Order, store: IntegrationAccount,
) -> None:
    cutoff = _store_cutoff(store)
    if cutoff is None or order.placed_at is None:
        return
    placed = order.placed_at
    if placed.tzinfo is None:
        placed = placed.replace(tzinfo=UTC)
    if placed < cutoff:
        from app.erp.external_processing import (  # noqa: PLC0415
            mark_order_externally_processed,
        )

        mark_order_externally_processed(
            session, order=order, actor=None,
            note="Pedido anterior a la fecha de corte de la tienda "
                 "(migrado del proceso anterior).",
        )
        session.flush()


def _unmapped_from(session: Session, order_id: str) -> list[str]:
    lines = session.scalars(
        select(OrderLine).where(
            OrderLine.order_id == order_id,
            OrderLine.product_codart.is_(None),
        )
    ).all()
    return [ln.product_sku for ln in lines]
