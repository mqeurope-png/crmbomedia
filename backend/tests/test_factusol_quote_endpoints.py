"""BoHub ERP Fase C · C-4 — endpoints de proformas FACTUSOL.

La cola RQ y el cliente FACTUSOL se mockean: sin Redis ni red.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users


class _FakeFactusol:
    def __init__(self, *, quotes=None, articles=None):
        self.default_ejercicio = "2026"
        self._quotes = list(quotes or [])
        self._articles = list(articles or [])

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_ART":
            return list(self._articles)
        if tabla != "F_PRE":
            return []
        rows = list(self._quotes)
        if filtro.startswith("CODPRE="):
            wanted = filtro.split("=", 1)[1].split(" ")[0]
            rows = [r for r in rows if str(r.get("CODPRE")) == wanted]
        return rows


def _quote_row(codpre: int, **over: Any) -> dict[str, Any]:
    row = {
        "CODPRE": codpre, "TIPPRE": "1", "REFPRE": "Proforma de prueba",
        "FECPRE": "2026-08-01T00:00:00", "CLIPRE": "55555",
        "CNOPRE": "Acme SL", "NET1PRE": 100.0, "PIVA1PRE": 21.0,
        "IIVA1PRE": 21.0, "TOTPRE": 121.0,
    }
    row.update(over)
    return row


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
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _company(s: Session, *, codcli: str | None = "55555") -> str:
    c = Company(name="Acme SL", tax_id="B12345678", factusol_company_id=codcli)
    s.add(c)
    s.commit()
    return c.id


def _patch_client(fake):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


# --- listado ----------------------------------------------------------------


def test_list_quotes_de_una_empresa_vinculada(client, session_factory):
    with session_factory() as s:
        cid = _company(s)
    with _patch_client(_FakeFactusol(quotes=[_quote_row(10)])):
        r = client.get(f"/api/erp/factusol/quotes?company_id={cid}&days_back=0",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unlinked"] is False
    assert body["items"][0]["codpre"] == "10"


def test_list_quotes_empresa_sin_vinculo_devuelve_unlinked(client, session_factory):
    """No es un error: la empresa existe pero aún no tiene CODCLI, así que no
    hay proformas que enseñar y la UI muestra el aviso de vincular."""
    with session_factory() as s:
        cid = _company(s, codcli=None)
    r = client.get(f"/api/erp/factusol/quotes?company_id={cid}",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json() == {"items": [], "unlinked": True, "ejercicio": None}


def test_list_quotes_404_si_la_empresa_no_existe(client):
    r = client.get("/api/erp/factusol/quotes?company_id=no-existe",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "company_not_found"


# --- detalle ----------------------------------------------------------------


def test_get_quote_devuelve_desglose_y_origen(client):
    with _patch_client(_FakeFactusol(quotes=[_quote_row(10)])):
        r = client.get("/api/erp/factusol/quotes/10",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    assert body["codpre"] == "10"
    assert body["line_source"] == "ref_text"
    assert body["lines"] == []


def test_get_quote_404_si_no_existe(client):
    with _patch_client(_FakeFactusol()):
        r = client.get("/api/erp/factusol/quotes/999",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "quote_not_found"


def test_status_no_colisiona_con_la_ruta_de_codpre(client):
    """`/quotes/status/{job_id}` se declara antes que `/quotes/{codpre}`; si el
    orden se invirtiera, «status» se interpretaría como un CODPRE."""
    r = client.get("/api/erp/factusol/quotes/status/job-123",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


# --- artículos --------------------------------------------------------------


def test_search_articles_endpoint(client):
    fake = _FakeFactusol(articles=[
        {"CODART": "ART-1", "DESART": "Cable HDMI", "PCOART": 8.5, "TIVART": 21},
    ])
    with _patch_client(fake):
        r = client.get("/api/erp/factusol/articles/search?q=hdmi",
                       headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert r.json()["items"][0]["codart"] == "ART-1"


# --- creación ---------------------------------------------------------------


def test_create_quote_encola_y_audita(client, session_factory):
    with session_factory() as s:
        cid = _company(s)
    with patch("app.integrations.factusol.jobs.enqueue_create_quote",
               return_value="job-q1") as enq:
        r = client.post("/api/erp/factusol/quotes",
                        headers=auth_headers(client, "pedidos"),
                        json={"company_id": cid, "lines": [
                            {"description": "Cable", "quantity": 2,
                             "unit_price": 10},
                        ]})
    assert r.status_code == 202, r.text
    assert r.json() == {"job_id": "job-q1", "status": "queued"}
    # El cliente de la proforma sale de la empresa CRM vinculada.
    customer = enq.call_args.args[0]
    assert customer["codcli"] == "55555"
    assert customer["nif"] == "B12345678"
    with session_factory() as s:
        audits = list(s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.factusol_quote_create")))
        assert len(audits) == 1


def test_create_quote_409_si_la_empresa_no_esta_vinculada(client, session_factory):
    with session_factory() as s:
        cid = _company(s, codcli=None)
    r = client.post("/api/erp/factusol/quotes",
                    headers=auth_headers(client, "pedidos"),
                    json={"company_id": cid, "referencia": "Algo"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "company_not_linked"


def test_create_quote_422_si_va_vacia(client, session_factory):
    with session_factory() as s:
        cid = _company(s)
    r = client.post("/api/erp/factusol/quotes",
                    headers=auth_headers(client, "pedidos"),
                    json={"company_id": cid})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_quote"


def test_create_quote_prohibido_para_roles_de_solo_lectura(client, session_factory):
    with session_factory() as s:
        cid = _company(s)
    for role in ("sat", "user", "viewer"):
        r = client.post("/api/erp/factusol/quotes",
                        headers=auth_headers(client, role),
                        json={"company_id": cid, "referencia": "X"})
        assert r.status_code == 403, role


# --- duplicar / convertir ---------------------------------------------------


def test_duplicate_quote_encola(client):
    with patch("app.integrations.factusol.jobs.enqueue_duplicate_quote",
               return_value="job-d1"):
        r = client.post("/api/erp/factusol/quotes/10/duplicate",
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 202
    assert r.json()["job_id"] == "job-d1"


def test_convert_to_order_encola_con_el_actor(client):
    with patch("app.integrations.factusol.jobs.enqueue_convert_quote_to_order",
               return_value="job-c1") as enq:
        r = client.post("/api/erp/factusol/quotes/10/convert-to-order",
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 202
    assert r.json()["job_id"] == "job-c1"
    # El actor viaja al job para firmar el historial del pedido creado.
    assert enq.call_args.args[1] is not None


def test_convert_to_order_prohibido_para_solo_lectura(client):
    r = client.post("/api/erp/factusol/quotes/10/convert-to-order",
                    headers=auth_headers(client, "user"))
    assert r.status_code == 403
