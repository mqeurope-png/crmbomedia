"""ERP · rediseño de flujo (Fase 4) — pantalla Proformas.

Las tres colas de la maqueta (aceptada → por convertir, pendiente, rechazada)
más «convertidas» (ya es pedido de BoHub) salen del MISMO criterio que la
bandeja (`workflow.quote_queue`); cada proforma lleva su empresa vinculada,
su régimen de IVA y, si existe, su pedido. «Convertir en pedido» sigue siendo
idempotente y marca el origen proforma. Sin red: FACTUSOL va simulado.
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

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.erp.workflow import QUOTE_QUEUES, quote_queue
from app.integrations.factusol.quotes import convert_quote_to_order, quote_estado
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users


class _FakeFactusol:
    def __init__(self, quotes: list[dict[str, Any]]):
        self.default_ejercicio = "2026"
        self._quotes = list(quotes)
        self.writes: list[Any] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla != "F_PRE":
            return []
        rows = list(self._quotes)
        if filtro.startswith("CODPRE="):
            wanted = filtro.split("=", 1)[1].split(" ")[0]
            rows = [r for r in rows if str(r.get("CODPRE")) == wanted]
        elif filtro.startswith("CLIPRE="):
            wanted = filtro.split("=", 1)[1].split(" ")[0].strip("'")
            rows = [r for r in rows if str(r.get("CLIPRE")) == wanted]
        return rows

    def write_record(self, *args, **kwargs):  # pragma: no cover — no se escribe
        self.writes.append((args, kwargs))
        raise AssertionError("la pantalla Proformas no escribe en FACTUSOL")


def _row(codpre: int, clipre: str, **over: Any) -> dict[str, Any]:
    row = {
        "CODPRE": codpre, "TIPPRE": "1", "REFPRE": f"Ref {codpre}",
        "FECPRE": "2026-09-04T00:00:00", "CLIPRE": clipre, "CNOPRE": f"Cliente {clipre}",
        "NET1PRE": 100.0, "PIVA1PRE": 21.0, "IIVA1PRE": 21.0, "TOTPRE": 121.0,
    }
    row.update(over)
    return row


QUOTES = [
    _row(501, "2760", ESTPRE=1),                                   # aceptada, intracom
    _row(502, "2458", ESTPRE=0),                                   # pendiente, nacional
    _row(503, "2458"),                                             # sin ESTPRE = pendiente
    _row(504, "2760", ESTPRE=2),                                   # rechazada
    _row(505, "2760", ESTPRE=1),                                   # aceptada YA convertida
    _row(506, "9999", ESTPRE=1, PIVA1PRE=0, NET1PRE=300.0,        # sin empresa CRM;
         IIVA1PRE=0, TOTPRE=300.0),                                # cabecera al 0 %
    _row(507, "2458", ESTPRE=7),                                   # estado no reconocido
    _row(508, "2760", ESTPRE=0, TIPPRE="5"),                       # serie 5 (Streamtec)
]


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        s.add_all([
            Company(id="fr", name="La Maison de la Plaque", country="FR",
                    vat="FR16339753527", factusol_company_id="2760"),
            Company(id="es", name="Duplicoder SL", country="ES", tax_id="B12345678",
                    factusol_company_id="2458"),
        ])
        s.add(Order(id="o505", order_number="PRO-000505", company_id="fr",
                    external_source=OrderSource.FACTUSOL_PROFORMA, external_id="505",
                    total_amount=121.0, currency="EUR", payment_status="pending",
                    preparation_status="pending_review"))
        s.commit()
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


def _patched(fake: _FakeFactusol):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


def _list(http, **params):
    return http.get("/api/erp/factusol/quotes", params={"days_back": 0, **params},
                    headers=auth_headers(http, "pedidos"))


# --- colas ---------------------------------------------------------------------------


def test_estado_y_cola_por_estpre() -> None:
    """Mismo criterio en un solo sitio: ESTPRE → estado → cola; convertida manda."""
    assert quote_estado(None) == "pendiente" and quote_estado(0) == "pendiente"
    assert quote_estado(1) == "aceptada" and quote_estado(2) == "rechazada"
    assert quote_estado(7) == "otro"
    assert quote_queue("aceptada", converted=False) == "aceptadas"
    assert quote_queue("pendiente", converted=False) == "pendientes"
    assert quote_queue("rechazada", converted=False) == "rechazadas"
    assert quote_queue("aceptada", converted=True) == "convertidas"
    assert quote_queue("otro", converted=False) is None
    assert QUOTE_QUEUES == ("aceptadas", "pendientes", "rechazadas", "convertidas")


def test_listado_calcula_las_tres_colas_y_convertidas(http) -> None:
    with _patched(_FakeFactusol(QUOTES)):
        r = _list(http)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queue_counts"] == {
        "aceptadas": 2, "pendientes": 3, "rechazadas": 1, "convertidas": 1,
    }
    # Qué valores de ESTPRE hay de verdad (para cerrar el mapeo «rechazada»).
    assert body["estpre_values"] == {"1": 3, "0": 2, "null": 1, "2": 1, "7": 1}
    by = {q["codpre"]: q for q in body["items"]}
    # Serie (empresa emisora) y número visible «serie-código».
    assert by["501"]["serie"] == 1 and by["501"]["serie_label"] == "Bomedia"
    assert by["501"]["numero"] == "1-000501"
    assert by["508"]["serie"] == 5 and by["508"]["serie_label"] == "Streamtec"
    assert by["508"]["numero"] == "5-000508"
    assert by["501"]["estado"] == "aceptada" and by["501"]["queue"] == "aceptadas"
    assert by["501"]["queue_label"] == "Aceptadas · por convertir"
    assert by["502"]["queue"] == "pendientes" and by["503"]["queue"] == "pendientes"
    assert by["503"]["estado_label"] == "pendiente"
    assert by["504"]["estado"] == "rechazada" and by["504"]["queue"] == "rechazadas"
    # Ya convertida: cola «convertidas» con su pedido, aunque ESTPRE siga en 1.
    assert by["505"]["queue"] == "convertidas"
    assert by["505"]["order"] == {"id": "o505", "order_number": "PRO-000505"}
    assert by["501"]["order"] is None
    # Estado no reconocido: sin cola (sale en «todas»), nunca se inventa.
    assert by["507"]["estado"] == "otro" and by["507"]["queue"] is None


def test_listado_lleva_empresa_y_regimen(http) -> None:
    """Importe + régimen según el país/régimen ya calculado (empresa CRM
    vinculada al CLIPRE); sin empresa, lo que diga la cabecera (0 % = exento)."""
    with _patched(_FakeFactusol(QUOTES)):
        by = {q["codpre"]: q for q in _list(http).json()["items"]}
    fr = by["501"]
    assert fr["company"] == {"id": "fr", "name": "La Maison de la Plaque", "country": "FR",
                             "factusol_id": "2760"}
    assert fr["regime"] == "intracomunitario" and fr["country_iso2"] == "FR"
    assert fr["regime_label"] == "Intracomunitario (exento)" and fr["exento"] is True
    assert fr["regime_source"] == "empresa"
    es = by["502"]
    assert es["regime"] == "nacional" and es["exento"] is False
    assert es["regime_label"] == "Nacional (con IVA)"
    # Sin empresa CRM: la cabecera manda (PIVA1PRE = 0 explícito).
    sin = by["506"]
    assert sin["company"] is None and sin["regime"] is None
    assert sin["exento"] is True and sin["regime_source"] == "cabecera"
    assert sin["regime_label"] == "Exento (según la proforma)"
    # Sin empresa y con IVA en la cabecera: nada que afirmar.
    otro = by["507"]
    assert otro["company"]["id"] == "es"           # 2458 está vinculada


def test_filtro_por_cola_y_contadores_completos(http) -> None:
    with _patched(_FakeFactusol(QUOTES)):
        r = _list(http, queue="aceptadas")
        assert {q["codpre"] for q in r.json()["items"]} == {"501", "506"}
        assert r.json()["queue_counts"]["pendientes"] == 3                 # cuenta todas
        # `limit` llega al listado (la pantalla pide más de las 100 por defecto).
        r = _list(http, limit=2)
        assert len(r.json()["items"]) == 2
        assert _list(http, limit=5000).status_code == 422
        r = _list(http, queue="convertidas")
        assert [q["codpre"] for q in r.json()["items"]] == ["505"]
        assert _list(http, queue="lo_que_sea").status_code == 422


# --- convertir en pedido: idempotente, origen proforma ------------------------------


def test_convertir_en_pedido_es_idempotente_y_marca_el_origen(http, session_factory) -> None:
    fake = _FakeFactusol(QUOTES)
    with session_factory() as s:
        first = convert_quote_to_order(fake, s, "501", ejercicio="2026")
        assert first["already_existed"] is False
        order = s.get(Order, first["order_id"])
        assert order.external_source == OrderSource.FACTUSOL_PROFORMA
        assert order.external_id == "501" and order.order_number == "PRO-000501"
        assert order.company_id == "fr"
        second = convert_quote_to_order(fake, s, "501", ejercicio="2026")
        assert second["already_existed"] is True and second["order_id"] == first["order_id"]
        assert s.query(Order).filter(Order.external_id == "501").count() == 1
    assert fake.writes == []                                   # no escribe en FACTUSOL
    # Y la pantalla la pasa a «convertidas» con su pedido.
    with _patched(fake):
        body = _list(http).json()
    by = {q["codpre"]: q for q in body["items"]}
    assert by["501"]["queue"] == "convertidas"
    assert by["501"]["order"]["order_number"] == "PRO-000501"
    assert body["queue_counts"]["aceptadas"] == 1 and body["queue_counts"]["convertidas"] == 2


def test_listado_por_empresa_conserva_las_colas(http) -> None:
    with _patched(_FakeFactusol(QUOTES)):
        r = _list(http, company_id="es")
    assert {q["queue"] for q in r.json()["items"]} == {"pendientes", None}
