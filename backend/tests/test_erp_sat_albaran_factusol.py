"""ERP · FIX Cola SAT — «Descargar albarán» usa el albarán de FACTUSOL.

La cola solo miraba el fichero SUBIDO A MANO (flujo antiguo Fase D), así que
un pedido con su albarán creado por BoHub en FACTUSOL (Fase 2,
`orders.factusol_albaran_number`) caía en «No se pudo descargar
automáticamente. Sube el albarán a mano». Ahora la cola expone el nº del
albarán de FACTUSOL y el taller descarga ESE PDF, el mismo que la ficha
(#396) y el que adjunta el email al SAT (#407); el fichero subido queda de
alternativa.
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
) -> None:
    o = Order(id=oid, order_number=number, preparation_status=prep,
              payment_status="paid", transport_status="not_shipped",
              factusol_albaran_number=albaran)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=oid, product_sku="99cy", description="Tinta cyan",
                    quantity=2, unit_price=40, line_total=80))
    if uploaded:
        s.add(ShipmentFile(
            order_id=oid, kind="albaran", source="manual_upload",
            filename="albaran.pdf", mime_type="application/pdf", size_bytes=10,
            storage_path=f"/tmp/{oid}-albaran.pdf",
            uploaded_at=datetime.now(UTC),
        ))
    s.commit()


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
    assert item["factusol_albaran_number"] == "1-100327"
    assert item["has_albaran"] is False        # no hay fichero subido…
    # …pero el PDF del albarán SÍ se descarga, del mismo endpoint que la ficha
    # y que el email al SAT.
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
    assert item["has_albaran"] is False
    assert item["factusol_albaran_number"] is None

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
