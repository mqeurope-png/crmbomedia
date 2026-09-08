"""ERP-F6-fix3 — la columna Empresa del seguimiento salía siempre «BO».

Las tres tiendas Woo estaban configuradas a serie 1 (Bomedia), así que todo se
etiquetaba BO — incluidos pedidos de artisJet que deberían ser MQ. La empresa
debe salir de la SERIE DE LA FACTURA si existe, o de la serie configurada para
la TIENDA, o quedar vacía; y escribirse con la abreviatura de Bart.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp import seguimiento as core
from app.erp.models import Order, OrderSource
from app.erp.seguimiento import (
    normalize_abbr,
    resolve_empresa_serie,
    series_abbreviations_config,
)
from app.main import app
from app.models.crm import Company
from app.models.integration_settings import (
    ExternalSystem,
    IntegrationAccount,
    IntegrationMode,
)
from tests._test_helpers import auth_headers, seed_test_users

# Series por tienda que decidió Bart.
STORE_SERIES = {"artisjet-europe": "2", "boprint": "5", "fluxlasers": "5"}


# --- Parte A/B: unidades de resolución ---------------------------------------------


def test_empresa_from_invoice_series_when_invoiced() -> None:
    # Facturado en serie 2 → 2, aunque su tienda esté configurada como 5.
    assert resolve_empresa_serie(
        invoice_number="2-526078", store_slug="boprint", store_id="x",
        source="woocommerce", by_source={"boprint": "5"},
    ) == 2


def test_empresa_from_store_series_when_not_invoiced() -> None:
    by_source = dict(STORE_SERIES)
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="artisjet-europe", store_id="a",
        source="woocommerce", by_source=by_source,
    ) == 2
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="boprint", store_id="b",
        source="woocommerce", by_source=by_source,
    ) == 5
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="fluxlasers", store_id="f",
        source="woocommerce", by_source=by_source,
    ) == 5


def test_empresa_empty_when_unknown() -> None:
    # Sin factura y sin serie configurada → None (celda vacía), nunca BO.
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="tienda-desconocida", store_id="z",
        source="woocommerce", by_source={},
    ) is None
    # Y NO se cae al default global (a diferencia de la resolución de emisión).
    assert resolve_empresa_serie(
        invoice_number=None, store_slug=None, store_id=None,
        source="manual", by_source={"default": "5"},
    ) is None


def test_series_per_store_config() -> None:
    by_source = {"boprint": "5", "woocommerce": "1"}
    # Cada tienda con su serie…
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="boprint", store_id="b",
        source="woocommerce", by_source=by_source,
    ) == 5
    # …y respaldo al valor global de WooCommerce para las que no tienen entrada.
    assert resolve_empresa_serie(
        invoice_number=None, store_slug="otra-tienda", store_id="o",
        source="woocommerce", by_source=by_source,
    ) == 1


def test_series_abbreviations_are_configurable() -> None:
    # Precargadas con las confirmadas; la serie 4 (Lambert) NO se inventa.
    default = series_abbreviations_config(None)
    assert default == {1: "BO", 2: "MQ", 5: "ST"}
    assert 4 not in default
    # Configurable: se puede añadir la 4 y cambiar cualquiera.
    custom = series_abbreviations_config({"4": "LAM", "2": "MQE"})
    assert custom[4] == "LAM"
    assert custom[2] == "MQE"
    assert custom[1] == "BO"
    # Vaciar una la elimina.
    assert 1 not in series_abbreviations_config({"1": ""})


def test_abbreviation_comparison_is_normalised() -> None:
    assert normalize_abbr("st") == normalize_abbr("ST") == "ST"
    assert normalize_abbr(" St ") == "ST"
    assert normalize_abbr("bom") == "BOM"
    assert normalize_abbr("MQ") != normalize_abbr("BO")


# --- fixtures + integración --------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str) -> str:
    acc = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        mode=IntegrationMode.LIVE,
    )
    s.add(acc)
    s.flush()
    return acc.id


def _order(s: Session, number: str, *, cliente: str, store_id: str | None = None,
           factura: str | None = None,
           source: OrderSource = OrderSource.WOOCOMMERCE) -> None:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    s.add(Order(
        order_number=number, external_source=source, company_id=comp.id,
        store_id=store_id, factusol_invoice_number=factura,
        placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    ))
    s.flush()


def _configure_stores(http) -> None:
    r = http.patch("/api/erp/settings",
                   json={"factusol_series_by_source": STORE_SERIES},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text


def _rows(session_factory):
    from app.erp.api.orders import customer_names
    with session_factory() as s:
        orders = list(s.scalars(select(Order)))
        return core.build_rows(s, customer_names=customer_names(s, orders))


def _empresa_of(rows: list[dict], number: str) -> str:
    return next(r["empresa_corta"] for r in rows if r["order_number"] == number)


def test_artisjet_orders_are_not_labelled_BO(session_factory, http) -> None:
    # Regresión del fallo real: ARTISJ-9544 salía BO; debe ser MQ.
    with session_factory() as s:
        artis = _store(s, "artisjet-europe")
        _order(s, "ARTISJ-9544", cliente="Cliente artis", store_id=artis)
        s.commit()
    _configure_stores(http)
    rows = _rows(session_factory)
    assert _empresa_of(rows, "ARTISJ-9544") == "MQ"
    assert _empresa_of(rows, "ARTISJ-9544") != "BO"


def test_empresa_resolution_end_to_end(session_factory, http) -> None:
    with session_factory() as s:
        artis = _store(s, "artisjet-europe")
        bop = _store(s, "boprint")
        flux = _store(s, "fluxlasers")
        # artisJet sin factura → MQ (serie 2 de su tienda).
        _order(s, "ARTISJ-9540", cliente="A", store_id=artis)
        # boprint sin factura → ST (serie 5).
        _order(s, "BOP-099894", cliente="B", store_id=bop)
        # flux sin factura → ST.
        _order(s, "FLE-005746", cliente="C", store_id=flux)
        # boprint YA facturado en serie 1 → BO (manda la factura, no la tienda).
        _order(s, "BOP-099873", cliente="D", store_id=bop, factura="1-260719")
        # Manual sin tienda ni factura → vacío.
        _order(s, "MANUAL-000001", cliente="E", source=OrderSource.MANUAL)
        s.commit()
    _configure_stores(http)
    rows = _rows(session_factory)
    assert _empresa_of(rows, "ARTISJ-9540") == "MQ"
    assert _empresa_of(rows, "BOP-099894") == "ST"
    assert _empresa_of(rows, "FLE-005746") == "ST"
    assert _empresa_of(rows, "BOP-099873") == "BO"      # de la factura 1-…
    assert _empresa_of(rows, "MANUAL-000001") == ""     # vacío, no BO

    # Parte C: la vista devuelve lo mismo y el filtro por serie funciona.
    r = http.get("/api/erp/seguimiento?en_curso=false", headers=auth_headers(http, "user"))
    by_num = {i["order_number"]: i for i in r.json()["items"]}
    assert by_num["ARTISJ-9540"]["empresa_corta"] == "MQ"
    r = http.get("/api/erp/seguimiento?en_curso=false&serie=2",
                 headers=auth_headers(http, "user"))
    assert [i["order_number"] for i in r.json()["items"]] == ["ARTISJ-9540"]

    # Las tiendas Woo se exponen en ajustes para configurar su serie.
    r = http.get("/api/erp/settings", headers=auth_headers(http, "user"))
    slugs = {st["slug"] for st in r.json()["woocommerce_stores"]}
    assert {"artisjet-europe", "boprint", "fluxlasers"} <= slugs
    assert r.json()["factusol_series_abbreviations"] == {"1": "BO", "2": "MQ", "5": "ST"}


def test_invoice_emission_series_logic_unchanged(session_factory) -> None:
    # Este PR NO toca resolve_serie: sigue heredando del TIPPCL del pedido.
    from app.integrations.factusol.service import resolve_serie

    with session_factory() as s:
        artis = _store(s, "artisjet-europe")
        o = Order(order_number="ARTISJ-9600", external_source=OrderSource.WOOCOMMERCE,
                  store_id=artis, placed_at=datetime(2026, 9, 1, tzinfo=UTC))
        s.add(o)
        s.flush()
        # La serie del pedido en FACTUSOL (TIPPCL) MANDA sobre la config.
        assert resolve_serie(s, o, pcl_row={"TIPPCL": "5", "CODPCL": 1}) == 5
        # Elección explícita del modal, por encima de todo.
        assert resolve_serie(s, o, requested=2, pcl_row={"TIPPCL": "5"}) == 2
