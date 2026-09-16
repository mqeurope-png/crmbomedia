"""BoHub ERP · Lote 4 — cliente FACTUSOL del pedido, también los WEB.

Un pedido web sin empresa CRM sigue teniendo cliente FACTUSOL: se resuelve por
el CLIFAC de su factura (o el CLIALB del albarán) y se deja completar lo que
falte (NIF) reusando el flujo confirmado de escritura en F_CLI
(`ActualizarRegistro`, solo las columnas que cambian). El cliente FACTUSOL se
sustituye por un fake en memoria (sin red), como en `test_factusol_customers`.
"""
from __future__ import annotations

import re
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users


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


class FakeFactusol:
    """F_FAC / F_ALB / F_CLI simulados. `load_table` honra el predicado trivial
    `COL=NNN` (el que usan `get_header` y `customer_row`); registra escrituras."""

    def __init__(self, tables):
        self.default_ejercicio = "2026"
        self.tables = tables
        self.writes: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        rows = list(self.tables.get(tabla, []))
        head = filtro.split(" ORDER BY ")[0].strip()
        m = re.match(r"^(\w+)=(\d+)$", head)
        if m:
            col, val = m.group(1), m.group(2)
            rows = [r for r in rows if str(r.get(col) or "").strip() == val]
            return rows
        # Predicado de igualdad por cadena: `REFPCL='FLU-005784'` (el que usa
        # `find_pcl_by_order` para localizar el pedido de cliente por REFPCL).
        m = re.match(r"^(\w+)='([^']*)'$", head)
        if m:
            col, val = m.group(1), m.group(2)
            rows = [r for r in rows if str(r.get(col) or "").strip() == val]
        return rows

    def write_record(self, tabla, data, *, ejercicio=None):  # pragma: no cover
        self.writes.append((tabla, data))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, data))
        for row in self.tables.get(tabla, []):
            if str(row.get("CODCLI")) == str(data.get("CODCLI")):
                row.update({k: v for k, v in data.items() if k != "CODCLI"})
        return {"ok": True}


def _cli(codcli, *, nombre="Escola La Muntanyeta", nif="", **over):
    base = {"CODCLI": codcli, "NIFCLI": nif, "NOFCLI": nombre, "NOCCLI": nombre,
            "DOMCLI": "C/ Escola 1", "POBCLI": "Barcelona", "CPOCLI": "08001",
            "PROCLI": "Barcelona", "PAICLI": "724", "EMACLI": "", "TELCLI": "",
            "IFICLI": 0, "IVACLI": 0, "TIVCLI": 1}
    base.update(over)
    return base


def _fac(codfac, *, tip=5, clifac):
    return {"TIPFAC": tip, "CODFAC": codfac, "CLIFAC": clifac,
            "CNOFAC": "Escola La Muntanyeta", "FECFAC": "2026-05-10",
            "TOTFAC": 121.0, "NET1FAC": 100.0, "PIVA1FAC": 21.0,
            "ESTFAC": 2, "REFFAC": "FLUXLA-5784"}


def _pcl(codpcl, *, tip=5, clipcl, ref="FLU-005784", nombre="BOMEDIA"):
    """F_PCL simulado tal como lo cargó FesteWeb: su `CLIPCL` es el CODCLI.
    `REFPCL` = prefijo de tienda (aquí el derivado `FLU`) + nº Woo a 6."""
    return {"TIPPCL": tip, "CODPCL": codpcl, "CLIPCL": clipcl, "REFPCL": ref,
            "CNOPCL": nombre, "FECPCL": "2026-05-01", "TOTPCL": 121.0}


def _patch_client(fake):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _web_order(session_factory, **over) -> str:
    with session_factory() as s:
        o = Order(order_number="FLUXLA-5784", external_source="woocommerce", **over)
        s.add(o)
        s.commit()
        return o.id


# --- resolución PREFERENTE por el CLIPCL del pedido de cliente F_PCL ---------


def test_web_order_resolves_customer_from_pcl_clipcl(client, session_factory):
    """Bloque 1: un pedido web sin empresa/factura/albarán se resuelve por el
    `CLIPCL` de su F_PCL (FesteWeb) — fuente PREFERENTE y autoritativa, que
    resuelve la ambigüedad BOMEDIA 11/89 (aquí gana el 11 que eligió FesteWeb)."""
    oid = _web_order(session_factory)
    fake = FakeFactusol({
        "F_PCL": [_pcl(1000, clipcl=11)],
        "F_CLI": [_cli(11, nombre="BOMEDIA", nif="B00000000")],
    })
    with _patch_client(fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-customer",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["codcli"] == "11"
    assert body["source"] == "pedido_cliente"
    assert body["cliente"]["nombre"] == "BOMEDIA"
    assert body["company_id"] is None


def test_web_order_pcl_wins_over_invoice(client, session_factory):
    """El F_PCL es PREFERENTE: aunque el pedido esté facturado, el CODCLI sale
    del CLIPCL del pedido de cliente (lo que FesteWeb eligió), no del CLIFAC."""
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    fake = FakeFactusol({
        "F_PCL": [_pcl(1000, clipcl=11)],
        "F_FAC": [_fac(260090, clifac=2458)],
        "F_CLI": [_cli(11, nombre="BOMEDIA"), _cli(2458)],
    })
    with _patch_client(fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-customer",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["codcli"] == "11" and body["source"] == "pedido_cliente"


# --- resolución por CLIFAC de la factura (fallback sin F_PCL) ----------------


def test_web_order_without_pcl_falls_back_to_invoice_clifac(client, session_factory):
    """El caso real FLUXLA-5784: pedido web sin F_PCL localizable pero facturado
    #260090; su F_FAC.CLIFAC resuelve el CODCLI y devuelve la ficha F_CLI."""
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    fake = FakeFactusol({
        "F_FAC": [_fac(260090, clifac=2458)],
        "F_CLI": [_cli(2458, nif="")],
    })
    with _patch_client(fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-customer",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["codcli"] == "2458"
    assert body["source"] == "factura"
    assert body["cliente"]["nombre"] == "Escola La Muntanyeta"
    assert body["company_id"] is None
    # El NIF vacío se ofrece para completar.
    assert "nif" in body["missing"]


def test_web_order_without_any_document_returns_not_found(client, session_factory):
    """Sin F_PCL, empresa, factura ni albarán no hay CODCLI resoluble:
    found:false. Un pedido web SÍ consulta el F_PCL antes de rendirse."""
    oid = _web_order(session_factory)
    fake = FakeFactusol({})  # ni F_PCL, ni factura, ni albarán, ni cliente
    with _patch_client(fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-customer",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is False
    assert body["codcli"] is None and body["source"] is None
    assert body["cliente"] is None


def test_resolves_by_albaran_clialb_when_no_company_or_invoice(client, session_factory):
    oid = _web_order(session_factory, factusol_albaran_number="5-500003")
    fake = FakeFactusol({
        "F_ALB": [{"TIPALB": 5, "CODALB": 500003, "CLIALB": 2458,
                   "CNOALB": "Escola La Muntanyeta"}],
        "F_CLI": [_cli(2458, nif="G12345678")],
    })
    with _patch_client(fake):
        r = client.get(f"/api/erp/orders/{oid}/factusol-customer",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True and body["source"] == "albaran"
    assert body["codcli"] == "2458"
    assert body["missing"] == []  # nada que completar


# --- completar el NIF que falta (escritura confirmada en F_CLI) --------------


def test_complete_nif_writes_only_changed_column_with_confirmation(client, session_factory):
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    fake = FakeFactusol({
        "F_FAC": [_fac(260090, clifac=2458)],
        "F_CLI": [_cli(2458, nif="")],
    })
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer",
            json={"confirm": True, "nif": "G12345678"},
            headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["codcli"] == "2458"
    # ActualizarRegistro de F_CLI con SOLO la clave + el NIF entrado.
    assert fake.updates == [("F_CLI", {"CODCLI": 2458, "NIFCLI": "G12345678"})]
    assert body["written"] == {"NIFCLI": "G12345678"}


def test_complete_requires_confirmation(client, session_factory):
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    fake = FakeFactusol({
        "F_FAC": [_fac(260090, clifac=2458)],
        "F_CLI": [_cli(2458, nif="")],
    })
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer",
            json={"confirm": False, "nif": "G12345678"},
            headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"
    assert fake.updates == []  # nada escrito


def test_complete_never_invents_only_writes_entered_fields(client, session_factory):
    """Un valor que ya coincide (o vacío) no se escribe: solo lo que cambia."""
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    fake = FakeFactusol({
        "F_FAC": [_fac(260090, clifac=2458)],
        "F_CLI": [_cli(2458, nif="G12345678")],  # el NIF ya está
    })
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer",
            json={"confirm": True, "nif": "G12345678"},
            headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is False
    assert fake.updates == []


def test_complete_forbidden_for_view_only(client, session_factory):
    oid = _web_order(session_factory, factusol_invoice_number="5-260090")
    r = client.post(
        f"/api/erp/orders/{oid}/factusol-customer",
        json={"confirm": True, "nif": "G12345678"},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 403


# --- vincular la empresa del pedido al CODCLI EXACTO del F_PCL ---------------


def _web_order_with_company(session_factory, name="BOMEDIA SL", **over) -> tuple[str, str]:
    """Pedido web + empresa CRM SIN vincular (para probar el enlace exacto)."""
    from app.models.crm import Company

    with session_factory() as s:
        comp = Company(name=name)
        s.add(comp)
        s.flush()
        o = Order(order_number="FLUXLA-5784", external_source="woocommerce",
                  company_id=comp.id, **over)
        s.add(o)
        s.commit()
        return o.id, comp.id


def test_link_company_to_pcl_clipcl_exactly(client, session_factory):
    """Vincula la empresa del pedido al CLIPCL del F_PCL (autoritativo), SIN
    crear nada en FACTUSOL: solo escribe `Company.factusol_company_id`."""
    from app.models.crm import Company

    oid, cid = _web_order_with_company(session_factory)
    fake = FakeFactusol({
        "F_PCL": [_pcl(1000, clipcl=11)],
        "F_CLI": [_cli(11, nombre="BOMEDIA")],
    })
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer/link",
            json={"confirm": True}, headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] is True
    assert body["codcli"] == "11" and body["source"] == "pedido_cliente"
    # El vínculo quedó escrito en el CRM; FACTUSOL no recibió ninguna escritura.
    with session_factory() as s:
        assert s.get(Company, cid).factusol_company_id == "11"
    assert fake.writes == [] and fake.updates == []


def test_link_company_requires_confirmation(client, session_factory):
    oid, cid = _web_order_with_company(session_factory)
    fake = FakeFactusol({"F_PCL": [_pcl(1000, clipcl=11)], "F_CLI": [_cli(11)]})
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer/link",
            json={"confirm": False}, headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"


def test_link_company_without_company_conflicts(client, session_factory):
    """Un pedido web sin empresa CRM no tiene a quién vincular: 409 no_company."""
    oid = _web_order(session_factory)
    fake = FakeFactusol({"F_PCL": [_pcl(1000, clipcl=11)], "F_CLI": [_cli(11)]})
    with _patch_client(fake):
        r = client.post(
            f"/api/erp/orders/{oid}/factusol-customer/link",
            json={"confirm": True}, headers=auth_headers(client, "pedidos"),
        )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_company"


def test_link_company_forbidden_for_view_only(client, session_factory):
    oid, _cid = _web_order_with_company(session_factory)
    r = client.post(
        f"/api/erp/orders/{oid}/factusol-customer/link",
        json={"confirm": True}, headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 403
