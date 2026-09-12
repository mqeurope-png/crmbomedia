"""«PDF del albarán (FACTUSOL)» en la ficha del pedido (continúa la Fase 2).

`GET /api/erp/orders/{id}/factusol-albaran-pdf` compone el PDF del albarán
que BoHub creó en FACTUSOL (`orders.factusol_albaran_number`, `serie-código`)
con el MISMO motor E4 que «PDF del pedido (FACTUSOL)» y los PDF de factura:
la API de DELSOL no imprime, BoHub lee F_ALB + F_LAL por clave compuesta y
maqueta el A4. Solo lectura. Sin albarán → 404 controlado.
"""
from __future__ import annotations

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
from app.integrations.factusol.client import FactusolError
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

#: Albarán REAL de la Fase 2: pedido PRO-004352 → albarán FACTUSOL 1-100327.
ALB_HEADER = {
    "TIPALB": "1", "CODALB": 100327, "FECALB": "2026-09-12T00:00:00", "ESTALB": 0,
    "CLIALB": 2458, "CNOALB": "DUPLICODER, S.L.", "CDOALB": "C/ Mayor 1",
    "CPOALB": "Barcelona", "CCPALB": "08001", "CPRALB": "Barcelona",
    "CNIALB": "B12345678", "CPAALB": "724", "REFALB": "Obra X", "FOPALB": "002",
    "NET1ALB": 154.0, "PIVA1ALB": 21.0, "IIVA1ALB": 32.34, "BAS1ALB": 154.0,
    "TOTALB": 186.34, "PEDALB": "",
}
ALB_HOMONIMO_SERIE_5 = {**ALB_HEADER, "TIPALB": "5", "CNOALB": "OTRA SL", "TOTALB": 1.0}
LAL_LINES = [
    {"TIPLAL": "1", "CODLAL": 100327, "POSLAL": 1, "ARTLAL": "99cy",
     "DESLAL": "Tinta cyan", "CANLAL": 2, "PRELAL": 40.0, "TOTLAL": 80.0,
     "IVALAL": 21, "DOCLAL": "P", "DTPLAL": "1", "DCOLAL": 4352},
    {"TIPLAL": "1", "CODLAL": 100327, "POSLAL": 2, "ARTLAL": "",
     "DESLAL": "Portes", "CANLAL": 1, "PRELAL": 74.0, "TOTLAL": 74.0,
     "IVALAL": 21, "DOCLAL": "P", "DTPLAL": "1", "DCOLAL": 4352},
    # Línea del albarán HOMÓNIMO de la serie 5: no debe entrar en el PDF.
    {"TIPLAL": "5", "CODLAL": 100327, "POSLAL": 1, "ARTLAL": "XX",
     "DESLAL": "Ajena", "CANLAL": 9, "PRELAL": 1.0, "TOTLAL": 9.0},
]
FPA = [{"CODFPA": "002", "DESFPA": "Transferencia"}]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_ALB": [dict(ALB_HOMONIMO_SERIE_5), dict(ALB_HEADER)],
        "F_LAL": [dict(r) for r in LAL_LINES],
        "F_FPA": FPA,
    }


class BoomClient(FakeClient):
    """FACTUSOL caído: cualquier lectura falla."""

    def load_table(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        raise FactusolError("timeout hablando con DELSOL", status=504)


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
            Order(id="o-alb", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="4352", order_number="PRO-004352",
                  total_amount=186.34, factusol_albaran_number="1-100327"),
            Order(id="o-sin", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="4353", order_number="PRO-004353", total_amount=10),
            Order(id="o-roto", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="4354", order_number="PRO-004354", total_amount=10,
                  factusol_albaran_number="1-999999"),
            Order(id="o-web", external_source=OrderSource.WOOCOMMERCE,
                  external_id="99917", order_number="BOPRIN-99917", total_amount=10),
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


def test_descargar_pdf_albaran_factusol(http) -> None:
    """Pedido con `factusol_albaran_number` → PDF A4 del albarán, leído de
    FACTUSOL por su clave COMPUESTA (serie 1 + nº 100327: el homónimo de la
    serie 5 y su línea quedan fuera), con el nombre de fichero del albarán.
    Solo lectura: el cliente falso no tiene `write_record`."""
    fake = FakeClient(_tables())
    with _patched(fake):
        r = http.get("/api/erp/orders/o-alb/factusol-albaran-pdf?lang=es",
                     headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    disposition = r.headers["content-disposition"]
    assert "1-100327" in disposition and "DUPLICODER" in disposition
    assert "albar" in disposition.lower()
    # Serie + número correctos contra FACTUSOL (cabecera y líneas por número,
    # serie casada en Python — el mismo criterio que el resto de los PDF).
    assert ("F_ALB", "CODALB=100327") in fake.calls
    assert ("F_LAL", "CODLAL=100327") in fake.calls
    # Variante «valorado» e idioma inglés: mismo endpoint, mismo motor.
    with _patched(FakeClient(_tables())):
        r2 = http.get(
            "/api/erp/orders/o-alb/factusol-albaran-pdf?lang=en&variant=valorado",
            headers=auth_headers(http, "user"),
        )
    assert r2.status_code == 200 and r2.content[:5] == b"%PDF-"
    assert r2.content != r.content


def test_pdf_albaran_solo_si_hay_albaran(http) -> None:
    """Sin `factusol_albaran_number` no se ofrece nada: 404 controlado con
    código propio (también para un pedido web). Con nº pero sin fila en
    FACTUSOL → 404 propio; FACTUSOL caído → 502; sin sesión → 401."""
    fake = FakeClient(_tables())
    with _patched(fake):
        for order_id in ("o-sin", "o-web"):
            r = http.get(f"/api/erp/orders/{order_id}/factusol-albaran-pdf",
                         headers=auth_headers(http, "user"))
            assert r.status_code == 404, r.text
            assert r.json()["detail"]["code"] == "albaran_not_in_bohub"
        # Ni siquiera se consulta FACTUSOL para esos.
        assert fake.calls == []
        r = http.get("/api/erp/orders/o-roto/factusol-albaran-pdf",
                     headers=auth_headers(http, "user"))
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "albaran_not_in_factusol"
        assert "1-999999" in r.json()["detail"]["detail"]
    with _patched(BoomClient({})):
        r = http.get("/api/erp/orders/o-alb/factusol-albaran-pdf",
                     headers=auth_headers(http, "user"))
        assert r.status_code == 502
        assert r.json()["detail"]["code"] == "factusol_pdf_failed"
    assert http.get("/api/erp/orders/o-alb/factusol-albaran-pdf").status_code == 401
    assert http.get("/api/erp/orders/o-alb/factusol-albaran-pdf?variant=anticipo",
                    headers=auth_headers(http, "user")).status_code == 422
