"""FIX «PDF del pedido (FACTUSOL)» en pedidos creados desde proforma.

`GET /api/erp/orders/{id}/factusol-pedido-pdf` imprimía SIEMPRE un pedido de
cliente F_PCL localizado por `REFPCL`; un pedido creado desde una proforma no
tiene F_PCL (su origen es el presupuesto F_PRE, y la Fase 2 le creó un
albarán), así que fallaba con «No se pudo generar el PDF del pedido FACTUSOL».
Ahora imprime el documento de ORIGEN real según el pedido (Fase 1 guarda
tipo + serie + nº en `packing_json.factusol_source`): presupuesto → F_PRE,
pedido de cliente → F_PCL por serie+nº, pedido web → F_PCL por REFPCL. Sin
documento de origen → 404 controlado sin consultar FACTUSOL, y la ficha
recibe `factusol_document = null` para deshabilitar el botón.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.erp.orders_from_factusol import factusol_source_block
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

PRESUPUESTO = {
    "TIPPRE": "1", "CODPRE": 4352, "CLIPRE": 2458, "CNOPRE": "DUPLICODER, S.L.",
    "FECPRE": "2026-09-10T00:00:00", "ESTPRE": 1, "REFPRE": "Obra X",
    "TOTPRE": 186.34, "NET1PRE": 154.0, "PIVA1PRE": 21.0, "IIVA1PRE": 32.34,
    "BAS1PRE": 154.0, "FOPPRE": "002", "CPAPRE": "724",
}
LINEAS_LPS = [
    {"TIPLPS": "1", "CODLPS": 4352, "POSLPS": 1, "ARTLPS": "99cy",
     "DESLPS": "Tinta cyan", "CANLPS": 2, "PRELPS": 40.0, "TOTLPS": 80.0, "IVALPS": 21},
    {"TIPLPS": "1", "CODLPS": 4352, "POSLPS": 2, "ARTLPS": "",
     "DESLPS": "Portes", "CANLPS": 1, "PRELPS": 74.0, "TOTLPS": 74.0, "IVALPS": 21},
]
PEDIDO_CLIENTE = {
    "TIPPCL": "5", "CODPCL": 123, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "ENCARGO TALLER 7",
    "TOTPCL": 60.5, "NET1PCL": 50.0, "PIVA1PCL": 21.0, "FOPPCL": "011",
}
PEDIDO_WEB = {
    "TIPPCL": "5", "CODPCL": 5, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "BOP-099917",
    "TOTPCL": 99.0, "NET1PCL": 81.82, "PIVA1PCL": 21.0, "FOPPCL": "002",
}
LINEAS_LPC = [
    {"TIPLPC": "5", "CODLPC": 123, "POSLPC": 1, "ARTLPC": "CDR80WPT",
     "DESLPC": "CD TQ 700 MB", "CANLPC": 100, "PRELPC": 0.5, "TOTLPC": 50.0},
    {"TIPLPC": "5", "CODLPC": 5, "POSLPC": 1, "ARTLPC": "99cy",
     "DESLPC": "Tinta", "CANLPC": 1, "PRELPC": 81.82, "TOTLPC": 81.82},
]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_PRE": [dict(PRESUPUESTO)], "F_LPS": [dict(r) for r in LINEAS_LPS],
        "F_PCL": [dict(PEDIDO_CLIENTE), dict(PEDIDO_WEB)],
        "F_LPC": [dict(r) for r in LINEAS_LPC],
        "F_FPA": [{"CODFPA": "002", "DESFPA": "Transferencia"}],
    }


def _packing(doc_type: str, serie: int, codigo: int) -> str:
    return json.dumps({"factusol_source": factusol_source_block(
        doc_type=doc_type, serie=serie, codigo=codigo, referencia=None,
        forma_pago=None, forma_pago_nombre=None,
    )})


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
        seed.add_all([
            # PRO-004352: creado desde el presupuesto 1-004352 (Fase 1); la
            # Fase 2 le creó el albarán 1-100327. NO tiene F_PCL.
            Order(id="o-pro", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="4352", order_number="PRO-004352", total_amount=186.34,
                  factusol_albaran_number="1-100327",
                  packing_json=_packing("presupuestos", 1, 4352)),
            Order(id="o-pcl", external_source=OrderSource.FACTUSOL_PEDIDO,
                  external_id="5-000123", order_number="PCL-5-000123", total_amount=60.5,
                  packing_json=_packing("pedidos", 5, 123)),
            Order(id="o-web", external_source=OrderSource.WOOCOMMERCE,
                  external_id="99917", order_number="BOPRIN-99917", total_amount=99.0),
            Order(id="o-manual", external_source=OrderSource.MANUAL,
                  order_number="MANUAL-000001", total_amount=40.0),
            Order(id="o-pro-borrado", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="9999", order_number="PRO-009999", total_amount=1.0,
                  packing_json=_packing("presupuestos", 1, 9999)),
        ])
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


def _patched(fake: FakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _pdf(http, order_id: str, **params):
    return http.get(f"/api/erp/orders/{order_id}/factusol-pedido-pdf",
                    params=params, headers=auth_headers(http, "user"))


def test_pdf_pedido_desde_proforma_usa_presupuesto(http) -> None:
    """Origen proforma → el PDF es el del presupuesto F_PRE de origen (serie +
    nº guardados en el pedido), sin buscar ningún F_PCL por REFPCL. La ficha
    recibe el documento de origen para etiquetar el botón."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = http.get("/api/erp/orders/o-pro", headers=auth_headers(http, "user")).json()
        r = _pdf(http, "o-pro", lang="es")
    assert detail["factusol_document"] == {
        "doc_type": "presupuestos", "serie": 1, "codigo": 4352, "numero": "1-004352",
        "label": "presupuesto", "by_ref": False,
    }
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    disposition = r.headers["content-disposition"]
    assert "Presupuesto" in disposition and "1-004352" in disposition
    assert ("F_PRE", "CODPRE=4352") in fake.calls
    assert ("F_LPS", "CODLPS=4352") in fake.calls
    assert not any(t == "F_PCL" for t, _ in fake.calls)      # ni un REFPCL
    # El presupuesto ya no está en FACTUSOL → 404 propio, no un 502 ni un 500.
    with _patched(FakeClient(_tables())):
        gone = _pdf(http, "o-pro-borrado")
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "document_not_in_factusol"
    assert "1-009999" in gone.json()["detail"]["detail"]


def test_pdf_pedido_desde_pedido_cliente_usa_fpcl(http) -> None:
    """Origen pedido de cliente → sigue con el F_PCL, ahora por serie + nº
    guardados (clave compuesta); un pedido web sigue localizando su F_PCL por
    REFPCL como siempre."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = http.get("/api/erp/orders/o-pcl", headers=auth_headers(http, "user")).json()
        r = _pdf(http, "o-pcl")
    assert detail["factusol_document"]["doc_type"] == "pedidos"
    assert detail["factusol_document"]["numero"] == "5-000123"
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    assert "5-000123" in r.headers["content-disposition"]
    assert ("F_PCL", "CODPCL=123") in fake.calls
    assert ("F_LPC", "CODLPC=123") in fake.calls
    assert not any("REFPCL" in f for _, f in fake.calls)

    fake_web = FakeClient(_tables())
    with _patched(fake_web):
        detail_web = http.get("/api/erp/orders/o-web", headers=auth_headers(http, "user")).json()
        r_web = _pdf(http, "o-web")
    assert detail_web["factusol_document"]["by_ref"] is True
    assert detail_web["factusol_document"]["doc_type"] == "pedidos"
    assert r_web.status_code == 200, r_web.text
    assert ("F_PCL", "REFPCL='BOP-099917'") in fake_web.calls
    assert "5-000005" in r_web.headers["content-disposition"]


def test_pdf_pedido_sin_origen_factusol_no_error(http) -> None:
    """Alta manual sin origen FACTUSOL: la ficha recibe `factusol_document =
    null` (botón deshabilitado, sin banner rojo) y el endpoint responde un
    404 controlado SIN consultar FACTUSOL."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = http.get("/api/erp/orders/o-manual", headers=auth_headers(http, "user")).json()
        r = _pdf(http, "o-manual")
    assert detail["factusol_document"] is None
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "pedido_not_in_factusol"
    assert "no hay PDF" in r.json()["detail"]["detail"]
    assert fake.calls == []
    # Pedido web que la app externa aún no ha replicado: 404 con el código de
    # siempre («aún no existe»), tampoco un error genérico.
    with _patched(FakeClient({"F_PCL": [], "F_LPC": []})):
        r_web = _pdf(http, "o-web")
    assert r_web.status_code == 404
    assert r_web.json()["detail"]["code"] == "pedido_not_in_factusol"
    assert "aún no existe" in r_web.json()["detail"]["detail"]
