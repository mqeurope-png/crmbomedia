"""IVA real + envío/comisiones en los pedidos web (fix del IVA fabricado).

Antes, el mapper metía un IVA fijo (21 %/10 %) y no importaba el envío ni las
comisiones, así que el desglose económico salía inventado (caso ARTISJ-9552).
Aquí se comprueba que las líneas llevan el IVA REAL de Woo, que el envío y los
fees entran como líneas propias, y que el desglose cuadra leyendo la fuente.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt
from app.db.base import Base
from app.erp.models import Order, OrderSource
from app.integrations.woocommerce.mapper import (
    LINE_KIND_FEE,
    LINE_KIND_SHIPPING,
    import_woo_order,
    remap_web_order_lines,
)
from app.integrations.woocommerce.recalc import (
    economics_mismatch,
    order_economics,
    woo_economics,
)
from app.models.crm import ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


def _store(s: Session, slug: str = "artisjet") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=f"Woo {slug}", enabled=True,
        mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.commit()
    return a


def _woo(**over) -> dict:
    base = {
        "id": 9552, "number": "9552", "status": "processing",
        "total": "113.36", "currency": "EUR",
        "date_created": "2026-09-10T08:00:00Z", "date_paid": "2026-09-10T08:01:00Z",
        "billing": {"email": "cliente@ejemplo.com", "first_name": "A", "country": "ES"},
        "prices_include_tax": False, "meta_data": [],
        "line_items": [], "shipping_lines": [], "fee_lines": [],
    }
    base.update(over)
    return base


def _artisj_9552() -> dict:
    """El caso real de reproducción: producto 90, envío 19, comisión 4,36,
    IVA 0, total 113,36."""
    return _woo(
        line_items=[{
            "id": 1, "product_id": 10, "sku": "4785",
            "name": "Encoder Strip Sensor", "quantity": 1,
            "total": "90.00", "total_tax": "0.00", "tax_class": "",
        }],
        shipping_lines=[{
            "id": 2, "method_title": "Flexible Shipping",
            "total": "19.00", "total_tax": "0.00",
        }],
        fee_lines=[{
            "id": 3, "name": "PayPal cost 4%", "total": "4.36",
            "total_tax": "0.00", "tax_status": "none",
        }],
    )


def _lines_by_kind(order: Order) -> dict[str | None, list]:
    out: dict[str | None, list] = {}
    for ln in sorted(order.lines, key=lambda x: x.position):
        out.setdefault(ln.line_kind, []).append(ln)
    return out


# --- caso ARTISJ-9552: sin IVA inventado, con envío y comisión ---------------


def test_artisj_9552_desglose_real_sin_iva_inventado(session_factory):
    with session_factory() as s:
        store = _store(s)
        out = import_woo_order(s, store=store, woo_order=_artisj_9552())
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        # El total es el de Woo (lo que pagó el cliente), nunca recalculado.
        assert float(order.total_amount) == pytest.approx(113.36)
        by = _lines_by_kind(order)
        # Mercancía: 90 al 0 % (NO 21 % fabricado).
        [prod] = by[None]
        assert float(prod.line_total) == pytest.approx(90.0)
        assert float(prod.tax_rate) == pytest.approx(0.0)
        # Envío 19 (is_shipping + line_kind shipping).
        [envio] = by[LINE_KIND_SHIPPING]
        assert float(envio.line_total) == pytest.approx(19.0)
        assert envio.is_shipping is True
        assert float(envio.tax_rate) == pytest.approx(0.0)
        assert "Envío" in envio.description
        # Comisión 4,36 (line_kind fee, NO is_shipping).
        [fee] = by[LINE_KIND_FEE]
        assert float(fee.line_total) == pytest.approx(4.36)
        assert fee.is_shipping is False
        assert fee.description == "PayPal cost 4%"
        # El desglose cuadra LEYENDO, no derivando.
        eco = order_economics(order)
        assert eco == {"base": 90.0, "envio": 19.0, "cargos": 4.36,
                       "iva": 0.0, "total": 113.36}
        # Y coincide con lo que dice el propio Woo.
        assert economics_mismatch(eco, woo_economics(_artisj_9552())) == []


# --- IVA real por tipo -------------------------------------------------------


def test_iva_real_nacional_21(session_factory):
    with session_factory() as s:
        store = _store(s)
        woo = _woo(total="108.90", line_items=[{
            "id": 1, "sku": "P21", "name": "Art", "quantity": 1,
            "total": "90.00", "total_tax": "18.90", "tax_class": "",
        }])
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        [prod] = order.lines
        assert float(prod.tax_rate) == pytest.approx(21.0)
        assert float(prod.line_total) == pytest.approx(90.0)


def test_iva_real_reducido_10(session_factory):
    with session_factory() as s:
        store = _store(s)
        woo = _woo(total="110.00", line_items=[{
            "id": 1, "sku": "P10", "name": "Libro", "quantity": 1,
            "total": "100.00", "total_tax": "10.00", "tax_class": "reduced-rate",
        }])
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        [prod] = order.lines
        assert float(prod.tax_rate) == pytest.approx(10.0)


def test_sin_impuesto_no_inventa_21(session_factory):
    """total_tax=0 (o ausente) → IVA 0, jamás un 21 % fabricado."""
    with session_factory() as s:
        store = _store(s)
        woo = _woo(total="50.00", line_items=[{
            "id": 1, "sku": "X", "name": "Sin IVA", "quantity": 1,
            "total": "50.00", "total_tax": "0.00", "tax_class": "",
        }])
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        assert float(order.lines[0].tax_rate) == pytest.approx(0.0)


def test_prices_include_tax_no_duplica_iva(session_factory):
    """Con `prices_include_tax`, el `total`/`total_tax` de la REST API siguen
    siendo sin IVA / el IVA aparte: no se suma nada encima."""
    with session_factory() as s:
        store = _store(s)
        woo = _woo(total="121.00", prices_include_tax=True, line_items=[{
            "id": 1, "sku": "P", "name": "Art", "quantity": 1,
            "total": "100.00", "total_tax": "21.00", "tax_class": "",
        }])
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        assert float(order.lines[0].tax_rate) == pytest.approx(21.0)
        assert float(order.lines[0].line_total) == pytest.approx(100.0)


# --- envío / comisiones con su impuesto --------------------------------------


def test_envio_y_fee_llevan_su_iva(session_factory):
    with session_factory() as s:
        store = _store(s)
        woo = _woo(
            total="157.30",
            line_items=[{"id": 1, "sku": "P", "name": "Art", "quantity": 1,
                         "total": "100.00", "total_tax": "21.00", "tax_class": ""}],
            shipping_lines=[{"id": 2, "method_title": "SEUR",
                             "total": "10.00", "total_tax": "2.10"}],
            fee_lines=[{"id": 3, "name": "Gestión", "total": "20.00",
                        "total_tax": "4.20"}],
        )
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        by = _lines_by_kind(order)
        assert float(by[LINE_KIND_SHIPPING][0].tax_rate) == pytest.approx(21.0)
        assert float(by[LINE_KIND_FEE][0].tax_rate) == pytest.approx(21.0)


def test_shipping_total_fallback_sin_shipping_lines(session_factory):
    """Sin `shipping_lines[]` pero con `shipping_total`, se sintetiza el envío."""
    with session_factory() as s:
        store = _store(s)
        woo = _woo(
            total="109.00", shipping_total="9.00", shipping_tax="0.00",
            line_items=[{"id": 1, "sku": "P", "name": "Art", "quantity": 1,
                         "total": "100.00", "total_tax": "0.00", "tax_class": ""}],
        )
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        by = _lines_by_kind(order)
        assert len(by.get(LINE_KIND_SHIPPING, [])) == 1
        assert float(by[LINE_KIND_SHIPPING][0].line_total) == pytest.approx(9.0)


def test_envio_gratis_no_crea_linea(session_factory):
    with session_factory() as s:
        store = _store(s)
        woo = _woo(
            total="100.00",
            line_items=[{"id": 1, "sku": "P", "name": "Art", "quantity": 1,
                         "total": "100.00", "total_tax": "0.00", "tax_class": ""}],
            shipping_lines=[{"id": 2, "method_title": "Gratis",
                             "total": "0.00", "total_tax": "0.00"}],
        )
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        assert all(ln.line_kind != LINE_KIND_SHIPPING for ln in order.lines)


def test_envio_y_fee_no_cuentan_como_sin_mapear(session_factory):
    """El envío y las comisiones van sin CODART a propósito: no son «sin
    mapear» (solo la mercancía sin CODART lo es)."""
    with session_factory() as s:
        store = _store(s)
        out = import_woo_order(s, store=store, woo_order=_artisj_9552())
        s.commit()
        # El producto 4785 no tiene mapping → único «sin mapear».
        assert out.unmapped_skus == ["4785"]


# --- recálculo de un pedido importado con la lógica vieja --------------------


def test_remap_web_order_lines_corrige_desglose(session_factory):
    with session_factory() as s:
        store = _store(s)
        # Simula la importación VIEJA: solo el producto, con IVA fabricado 21 %.
        viejo = _woo(total="113.36", line_items=[{
            "id": 1, "sku": "4785", "name": "Encoder Strip Sensor",
            "quantity": 1, "total": "90.00", "total_tax": "18.90", "tax_class": "",
        }])
        out = import_woo_order(s, store=store, woo_order=viejo)
        s.commit()
        order = s.get(Order, out.order_id, options=[selectinload(Order.lines)])
        # De partida: IVA fabricado y sin envío/comisión → no cuadra con Woo.
        assert economics_mismatch(order_economics(order),
                                  woo_economics(_artisj_9552())) != []
        # Recalcular desde el payload real lo deja cuadrado.
        remap_web_order_lines(s, order, _artisj_9552(), store)
        s.commit()
        s.refresh(order)
        eco = order_economics(order)
        assert eco == {"base": 90.0, "envio": 19.0, "cargos": 4.36,
                       "iva": 0.0, "total": 113.36}
        assert economics_mismatch(eco, woo_economics(_artisj_9552())) == []


def test_manual_order_lines_sin_line_kind(session_factory):
    """Un pedido MANUAL (no Woo) no lleva line_kind: el mapper no lo toca."""
    with session_factory() as s:
        store = _store(s)
        out = import_woo_order(s, store=store, woo_order=_artisj_9552())
        s.commit()
        order = s.get(Order, out.order_id)
        # (control) el pedido web sí es woocommerce
        assert order.external_source == OrderSource.WOOCOMMERCE
