"""ERP · FIX Cola SAT — «Descargar albarán» usa el albarán de FACTUSOL.

La cola solo miraba el fichero SUBIDO A MANO (flujo antiguo Fase D), así que
un pedido con su albarán creado por BoHub en FACTUSOL (Fase 2,
`orders.factusol_albaran_number`) caía en «No se pudo descargar
automáticamente. Sube el albarán a mano». Ahora la cola expone el nº del
albarán de FACTUSOL y el taller descarga ESE PDF, el mismo que la ficha
(#396) y el que adjunta el email al SAT (#407); el fichero subido queda de
alternativa.

Lote 2 A3 — pedidos WEB: su albarán lo genera WooCommerce (BoHub nunca lo
crea en FACTUSOL), así que la cola lo ofrece como fuente (`albaran_source =
"woo"`) en vez de «Falta albarán», y solo lo declara no disponible (con
motivo) cuando la descarga de Woo no es posible.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine, ShipmentFile
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_albaran_pdf import _tables as _albaran_tables
from tests.test_factusol_documents import FakeClient


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


def _order(
    s: Session, *, oid: str, number: str, albaran: str | None = None,
    uploaded: bool = False, prep: str = "in_queue",
    source: str = "manual", store_id: str | None = None,
    external_id: str | None = None, file_source: str = "manual_upload",
) -> None:
    o = Order(id=oid, order_number=number, preparation_status=prep,
              payment_status="paid", transport_status="not_shipped",
              factusol_albaran_number=albaran, external_source=source,
              store_id=store_id, external_id=external_id)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=oid, product_sku="99cy", description="Tinta cyan",
                    quantity=2, unit_price=40, line_total=80))
    if uploaded:
        s.add(ShipmentFile(
            order_id=oid, kind="albaran", source=file_source,
            filename="albaran.pdf", mime_type="application/pdf", size_bytes=10,
            storage_path=f"/tmp/{oid}-albaran.pdf",
            uploaded_at=datetime.now(UTC),
        ))
    s.commit()


def _store(s: Session, slug: str, *, configured: bool = True) -> str:
    """Tienda Woo como en producción (con conexión configurada) o a medias
    (`configured=False`: sin URL ni claves → el cliente Woo no arranca)."""
    from app.models.crm import ExternalSystem  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    acc = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug.title(),
        base_url="https://tienda.example" if configured else None,
        consumer_key_encrypted="ck" if configured else None,
        consumer_secret_encrypted="cs" if configured else None,
    )
    s.add(acc)
    s.commit()
    return acc.id


#: Campos del contrato de albarán del item de la cola (Lote 2 A3).
_ALBARAN_KEYS = (
    "has_albaran", "albaran_source", "has_albaran_file", "albaran_file_source",
    "is_web_order", "woo_albaran_available", "woo_albaran_unavailable_reason",
    "factusol_albaran_number",
)


def _albaran(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in _ALBARAN_KEYS}


def _patched(fake: FakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _queue(http: TestClient) -> dict[str, dict[str, Any]]:
    r = http.get("/api/erp/sat/queue", headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    body = r.json()
    return {
        i["order_number"]: i
        for i in [*body["preparing"], *body["ready_for_pickup"]]
    }


def test_cola_sat_descarga_albaran_factusol(http, session_factory) -> None:
    """El caso de producción (MANUAL-000002 con albarán 1-100327): la cola
    trae el nº del albarán de FACTUSOL y el taller se descarga ESE PDF —ya no
    cae al mensaje de subir a mano—, en ambas secciones."""
    with session_factory() as s:
        _order(s, oid="o-fac", number="MANUAL-000002", albaran="1-100327")
        _order(s, oid="o-fac-packed", number="MANUAL-000003",
               albaran="1-100327", prep="packed")

    item = _queue(http)["MANUAL-000002"]
    assert _albaran(item) == {
        "factusol_albaran_number": "1-100327",
        "albaran_source": "factusol",           # manda el albarán de FACTUSOL…
        "has_albaran": True,
        "has_albaran_file": False,              # …aunque no haya fichero subido
        "albaran_file_source": None,
        "is_web_order": False,
        "woo_albaran_available": False,         # no es web: Woo no pinta nada
        "woo_albaran_unavailable_reason": None,
    }
    # El PDF del albarán se descarga del mismo endpoint que la ficha y que el
    # email al SAT.
    fake = FakeClient(_albaran_tables())
    with _patched(fake):
        r = http.get("/api/erp/orders/o-fac/factusol-albaran-pdf",
                     headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    assert "1-100327" in r.headers["content-disposition"]

    # La sección «listos para envío» trae el mismo dato.
    assert _queue(http)["MANUAL-000003"]["factusol_albaran_number"] == "1-100327"


def test_cola_sat_albaran_subido_a_mano_sigue(http, session_factory) -> None:
    """Sin albarán en FACTUSOL pero con fichero subido: la cola lo sigue
    marcando y el taller abre ese fichero (flujo antiguo intacto)."""
    with session_factory() as s:
        _order(s, oid="o-file", number="BOPRIN-1", uploaded=True)

    item = _queue(http)["BOPRIN-1"]
    assert item["has_albaran"] is True
    assert item["albaran_source"] == "file"
    assert item["has_albaran_file"] is True
    assert item["albaran_file_source"] == "manual_upload"
    assert item["factusol_albaran_number"] is None

    r = http.get("/api/erp/orders/o-file/shipping-files?kind=albaran",
                 headers=auth_headers(http, "sat"))
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 1


def test_cola_sat_sin_albaran_mensaje(http, session_factory) -> None:
    """Sin ninguno de los dos: la cola no marca nada (la card ofrece
    descargarlo / crearlo) y el PDF de FACTUSOL responde su 404 propio."""
    with session_factory() as s:
        _order(s, oid="o-nada", number="BOPRIN-2")

    item = _queue(http)["BOPRIN-2"]
    assert _albaran(item) == {
        "factusol_albaran_number": None,
        "albaran_source": None,                 # «Falta albarán» (es manual)
        "has_albaran": False,
        "has_albaran_file": False,
        "albaran_file_source": None,
        "is_web_order": False,
        "woo_albaran_available": False,
        "woo_albaran_unavailable_reason": None,  # no es web: sin motivo
    }

    fake = FakeClient(_albaran_tables())
    with _patched(fake):
        r = http.get("/api/erp/orders/o-nada/factusol-albaran-pdf",
                     headers=auth_headers(http, "sat"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "albaran_not_in_bohub"
    assert fake.calls == []        # ni se consulta FACTUSOL


def test_cola_sat_factusol_manda_sobre_el_fichero(http, session_factory) -> None:
    """Con los dos, manda FACTUSOL: es el documento real del pedido y el
    mismo que se envía por email (coherencia entre las dos vías)."""
    with session_factory() as s:
        _order(s, oid="o-ambos", number="MANUAL-000004", albaran="1-100327",
               uploaded=True)

    item = _queue(http)["MANUAL-000004"]
    assert item["factusol_albaran_number"] == "1-100327"
    assert item["has_albaran"] is True
    assert item["albaran_source"] == "factusol"
    assert item["has_albaran_file"] is True


# --- Lote 2 A3: pedidos web → albarán de WooCommerce -------------------------


def test_cola_sat_pedido_web_usa_el_albaran_de_woocommerce(http, session_factory) -> None:
    """El caso de producción (ARTISJ-9553 en «Listos» decía «Falta albarán»):
    un pedido web sin fichero aún no está «sin albarán» — su albarán lo genera
    WooCommerce y la cola lo ofrece como fuente, en las dos secciones."""
    with session_factory() as s:
        art = _store(s, "artisjet")
        _order(s, oid="o-web", number="ARTISJ-9553", source="woocommerce",
               store_id=art, external_id="9553", prep="packed")
        _order(s, oid="o-web-q", number="ARTISJ-9554", source="woocommerce",
               store_id=art, external_id="9554")

    queue = _queue(http)
    for number in ("ARTISJ-9553", "ARTISJ-9554"):
        assert _albaran(queue[number]) == {
            "factusol_albaran_number": None,     # BoHub nunca lo crea en FACTUSOL
            "albaran_source": "woo",
            "has_albaran": True,                 # hay albarán: el de la tienda
            "has_albaran_file": False,           # aún no descargado
            "albaran_file_source": None,
            "is_web_order": True,
            "woo_albaran_available": True,
            "woo_albaran_unavailable_reason": None,
        }, number
    assert queue["ARTISJ-9553"]["store_slug"] == "artisjet"


def test_cola_sat_pedido_web_ya_descargado_abre_el_fichero(http, session_factory) -> None:
    """Tras `fetch-from-woo` el albarán de la tienda queda como fichero
    vigente: la cola lo abre directamente (sin volver a descargar) y dice de
    dónde salió."""
    with session_factory() as s:
        bop = _store(s, "boprint")
        _order(s, oid="o-web-file", number="BOPRIN-9", source="woocommerce",
               store_id=bop, external_id="9", uploaded=True,
               file_source="woo_pdf_plugin")

    item = _queue(http)["BOPRIN-9"]
    assert item["albaran_source"] == "file"
    assert item["has_albaran"] is True
    assert item["has_albaran_file"] is True
    assert item["albaran_file_source"] == "woo_pdf_plugin"
    assert item["is_web_order"] is True
    assert item["woo_albaran_available"] is True


def test_cola_sat_pedido_web_sin_descarga_posible_dice_por_que(http, session_factory) -> None:
    """Solo entonces un pedido web queda sin albarán: y el item trae el motivo
    (tienda que falta, cuenta sin conexión configurada, id de Woo ilegible)
    para que el taller lo vea en vez de un «Falta albarán» engañoso."""
    with session_factory() as s:
        sin_conexion = _store(s, "fluxlasers", configured=False)
        ok = _store(s, "artisjet")
        _order(s, oid="o-web-sin-tienda", number="WEB-SIN-TIENDA",
               source="woocommerce", external_id="1")
        _order(s, oid="o-web-sin-conexion", number="WEB-SIN-CONEXION",
               source="woocommerce", store_id=sin_conexion, external_id="2")
        _order(s, oid="o-web-sin-id", number="WEB-SIN-ID",
               source="woocommerce", store_id=ok, external_id="wc_order_abc")

    queue = _queue(http)
    for number in ("WEB-SIN-TIENDA", "WEB-SIN-CONEXION", "WEB-SIN-ID"):
        item = queue[number]
        assert item["is_web_order"] is True, number
        assert item["albaran_source"] is None, number
        assert item["has_albaran"] is False, number
        assert item["woo_albaran_available"] is False, number
    assert queue["WEB-SIN-TIENDA"]["woo_albaran_unavailable_reason"] == (
        "El pedido no tiene tienda vinculada en BoHub."
    )
    assert queue["WEB-SIN-CONEXION"]["woo_albaran_unavailable_reason"] == (
        "La tienda «fluxlasers» no tiene configurada la conexión con WooCommerce."
    )
    assert queue["WEB-SIN-ID"]["woo_albaran_unavailable_reason"] == (
        "Falta el id del pedido en WooCommerce."
    )


def test_woo_albaran_state_tienda_borrada() -> None:
    """Pedido web cuya tienda ya no existe en `integration_accounts`."""
    from app.erp.api.sat import woo_albaran_state  # noqa: PLC0415

    o = Order(order_number="WEB-X", external_source="woocommerce",
              store_id="ya-no-existe", external_id="7")
    assert woo_albaran_state(o, None) == (
        False, "La tienda del pedido ya no existe en BoHub.",
    )
    # Manual: no aplica, y sin motivo (el albarán sale de FACTUSOL).
    assert woo_albaran_state(Order(order_number="MAN-1"), None) == (False, None)
