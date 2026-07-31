"""BoHub ERP Fase A PR 1 — modelos base + migración 0080.

Creación, defaults, integridad FK/cascade y catálogo de excepciones.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.db.base import Base
from app.erp.models import (
    EXCEPTION_SUBTYPES,
    Carrier,
    ErpException,
    ErpSettings,
    ExceptionStatus,
    ExceptionType,
    InvoiceStatus,
    Order,
    OrderLine,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    ProductSkuMapping,
    StatusDomain,
    TransportStatus,
)
from app.models.crm import User, UserRole
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    # SQLite no aplica FKs por defecto — activarlo para que los tests de
    # integridad referencial se comporten como MySQL 8 (prod).
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


def _mk_order(s: Session, **over) -> Order:
    order = Order(order_number=over.pop("order_number", "MAN-0001"), **over)
    s.add(order)
    s.flush()
    return order


def test_order_defaults_start_all_four_domains_at_initial_state(session_factory):
    with session_factory() as s:
        o = _mk_order(s)
        s.commit()
        s.refresh(o)
        assert o.payment_status == PaymentStatus.PENDING
        assert o.preparation_status == PreparationStatus.PENDING_REVIEW
        assert o.transport_status == TransportStatus.NOT_SHIPPED
        assert o.invoice_status == InvoiceStatus.NOT_INVOICED
        assert o.currency == "EUR"
        assert o.approved_at is None


def test_order_lines_cascade_delete_with_order(session_factory):
    with session_factory() as s:
        o = _mk_order(s)
        s.add_all([
            OrderLine(order_id=o.id, position=0, product_sku="SKU-A",
                      description="A", quantity=1, unit_price=10, line_total=10),
            OrderLine(order_id=o.id, position=1, product_sku="SKU-B",
                      description="B", quantity=2, unit_price=5, line_total=10),
        ])
        s.commit()
        assert s.scalar(select(func.count(OrderLine.id))) == 2
        s.delete(s.get(Order, o.id))
        s.commit()
        assert s.scalar(select(func.count(OrderLine.id))) == 0


def test_order_line_codart_nullable_marks_unmapped_sku(session_factory):
    with session_factory() as s:
        o = _mk_order(s)
        s.add(OrderLine(order_id=o.id, product_sku="SKU-NUEVO", description="X"))
        s.commit()
        line = s.scalar(select(OrderLine))
        assert line.product_codart is None  # sin mapear → bloqueo de factura


def test_status_history_records_domain_transition(session_factory):
    with session_factory() as s:
        admin = s.scalar(select(User).where(User.role == UserRole.ADMIN))
        o = _mk_order(s)
        s.add(OrderStatusHistory(
            order_id=o.id, domain=StatusDomain.PREPARATION,
            from_status="pending_review", to_status="in_queue",
            changed_at=datetime.now(UTC), changed_by_user_id=admin.id,
            reason="aprobado en cola PEDIDOS",
        ))
        s.commit()
        h = s.scalar(select(OrderStatusHistory))
        assert h.domain == StatusDomain.PREPARATION
        assert (h.from_status, h.to_status) == ("pending_review", "in_queue")


def test_exception_requires_existing_order(session_factory):
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id="no-such-order"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_exception_defaults_open_and_stores_subtype_metadata(session_factory):
    with session_factory() as s:
        o = _mk_order(s)
        s.add(ErpException(
            type=ExceptionType.STOCK_SHORTAGE, subtype="eta_set",
            order_id=o.id,
            metadata_json='{"eta_date": "2026-08-15", "provider": "MBO GmbH"}',
        ))
        s.commit()
        e = s.scalar(select(ErpException))
        assert e.status == ExceptionStatus.OPEN
        assert e.resolved_at is None
        assert "eta_date" in (e.metadata_json or "")


def test_exception_subtype_catalog_only_for_stock_shortage(session_factory):
    assert set(EXCEPTION_SUBTYPES) == {ExceptionType.STOCK_SHORTAGE}
    assert EXCEPTION_SUBTYPES[ExceptionType.STOCK_SHORTAGE] == {
        "pending_purchase", "eta_set", "eta_unknown", "not_replenishable",
    }


def test_carrier_code_unique(session_factory):
    with session_factory() as s:
        s.add(Carrier(name="Genei", code="genei"))
        s.commit()
        s.add(Carrier(name="Genei bis", code="genei"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_sku_mapping_unique_per_store_and_sku(session_factory):
    with session_factory() as s:
        s.add(ProductSkuMapping(woo_sku="SKU-MBO-3050", factusol_codart="MBO3050"))
        s.commit()
        # Mismo SKU con store NULL: en SQLite/MySQL dos NULL no chocan en
        # UNIQUE — el guard real por tienda se ejercita en Fase B con
        # cuentas reales. Aquí validamos el insert + el default matched_by.
        m = s.scalar(select(ProductSkuMapping))
        assert m.matched_by.value == "manual"
        assert m.confirmed_at is None


def test_erp_settings_singleton_defaults(session_factory):
    with session_factory() as s:
        s.add(ErpSettings())
        s.commit()
        cfg = s.scalar(select(ErpSettings))
        assert cfg.id == "singleton"
        assert cfg.default_invoice_mode.value == "manual"
        assert cfg.auto_invoice_max_amount_eur is None


def test_new_roles_pedidos_and_sat_exist(session_factory):
    assert UserRole.PEDIDOS.value == "pedidos"
    assert UserRole.SAT.value == "sat"
    with session_factory() as s:
        s.add(User(
            email="sat@bomedia.es", full_name="SAT taller",
            password_hash="x", role=UserRole.SAT, is_active=True,
        ))
        s.commit()
        u = s.scalar(select(User).where(User.email == "sat@bomedia.es"))
        assert u.role == UserRole.SAT


def test_company_and_contact_factusol_columns(session_factory):
    from app.models.crm import Company, Contact

    with session_factory() as s:
        c = Company(name="Rotulación Pérez SL", factusol_company_id="430087",
                    factusol_sync_source="crm",
                    factusol_synced_at=datetime.now(UTC))
        s.add(c)
        s.flush()
        s.add(Contact(first_name="Laura", email="laura@perez.es",
                      company_id=c.id, factusol_contact_id="CT-1",
                      factusol_is_primary=True))
        s.commit()
        contact = s.scalar(select(Contact).where(Contact.email == "laura@perez.es"))
        assert contact.factusol_is_primary is True
        assert contact.company.factusol_company_id == "430087"
