"""BoHub ERP Fase C · C-3 — sync de clientes CRM ↔ FACTUSOL.

El cliente FACTUSOL se sustituye por un fake en memoria (sin red). Cubre la
búsqueda (NIF/email/nombre + cross-check CRM), el vínculo y el alta con sus dos
guards heredados del bug de C-2-fix1: dedupe por NIF y bloqueo de clientes
gestionados por la app externa Woo→FACTUSOL.
"""
from __future__ import annotations

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
from app.models.crm import Company, Contact
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
    """F_CLI simulado: devuelve filas según el filtro y registra escrituras."""

    def __init__(self, rows=None, *, max_codcli=None):
        self.default_ejercicio = "2026"
        self._rows = rows or []
        self._max = max_codcli
        self.writes: list[tuple[str, dict]] = []
        self.filters: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla != "F_CLI":
            return []
        self.filters.append(filtro)
        if "ORDER BY CODCLI DESC" in filtro:
            return [{"CODCLI": self._max}] if self._max is not None else []
        return list(self._rows)

    def write_record(self, tabla, data, *, ejercicio=None):
        self.writes.append((tabla, data))
        return {"ok": True}


def _cli(codcli, nombre="LABORATORIOS PORTA S.L.", nif="B64113590", **over):
    """Fila F_CLI con los nombres de columna REALES (C-3-fix1)."""
    base = {"CODCLI": codcli, "NIFCLI": nif, "NOFCLI": nombre, "NOCCLI": nombre,
            "DOMCLI": "c. Fígols, 19-21", "POBCLI": "Barcelona",
            "CPOCLI": "08028", "PROCLI": "Barcelona", "PAICLI": "724",
            "EMACLI": "info@porta.example", "TELCLI": "600000000"}
    base.update(over)
    return base


def _patch_client(fake):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


# --- búsqueda ---------------------------------------------------------------


def test_factusol_customers_search_by_nif_found(client):
    fake = FakeFactusol([_cli(2458)])
    with _patch_client(fake):
        r = client.get("/api/erp/factusol/customers/search?q=B64113590&by=nif",
                       headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["codcli"] == "2458"
    assert items[0]["nombre"] == "LABORATORIOS PORTA S.L."
    assert items[0]["nif"] == "B64113590"
    assert items[0]["factusol_matches_crm_id"] is None
    # Filtro exacto e insensible a mayúsculas.
    assert "UPPER(NIFCLI)=UPPER('B64113590')" in fake.filters[0]


def test_factusol_customers_search_by_nif_not_found(client):
    with _patch_client(FakeFactusol([])):
        r = client.get("/api/erp/factusol/customers/search?q=X0000000Z&by=nif",
                       headers=auth_headers(client, "pedidos"))
    assert r.json()["items"] == []


def test_factusol_customers_search_by_name_limits_50(client):
    rows = [_cli(i, nombre=f"Cliente {i}") for i in range(200)]
    with _patch_client(FakeFactusol(rows)):
        r = client.get("/api/erp/factusol/customers/search?q=Cliente&by=name",
                       headers=auth_headers(client, "pedidos"))
    assert len(r.json()["items"]) == 50


def test_factusol_customers_search_escapes_quotes(client):
    """Un NIF con comilla no debe romper (ni inyectar) el filtro SQL."""
    fake = FakeFactusol([])
    with _patch_client(fake):
        client.get("/api/erp/factusol/customers/search?q=B6' OR '1'='1&by=nif",
                   headers=auth_headers(client, "pedidos"))
    assert "''" in fake.filters[0]  # comilla escapada


def test_factusol_customers_search_cross_checks_crm(client, session_factory):
    with session_factory() as s:
        comp = Company(name="Porta CRM", factusol_company_id="2458")
        s.add(comp)
        s.commit()
        comp_id = comp.id
    fake = FakeFactusol([_cli(2458), _cli(9999, nombre="Otro", nif="B1")])
    with _patch_client(fake):
        r = client.get("/api/erp/factusol/customers/search?q=a&by=name",
                       headers=auth_headers(client, "pedidos"))
    by_code = {i["codcli"]: i for i in r.json()["items"]}
    assert by_code["2458"]["factusol_matches_crm_id"] == comp_id
    assert by_code["2458"]["crm_link"]["type"] == "company"
    assert by_code["9999"]["factusol_matches_crm_id"] is None


# --- vínculo ----------------------------------------------------------------


def test_factusol_customers_link_success(client, session_factory):
    with session_factory() as s:
        comp = Company(name="Sin vincular")
        s.add(comp)
        s.commit()
        comp_id = comp.id
    r = client.post("/api/erp/factusol/customers/link", json={
        "crm_type": "company", "crm_id": comp_id, "factusol_codcli": "2458",
    }, headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.get(Company, comp_id).factusol_company_id == "2458"


def test_factusol_customers_link_contact(client, session_factory):
    with session_factory() as s:
        c = Contact(first_name="Ana", last_name="Pi", email="ana@example.com")
        s.add(c)
        s.commit()
        cid = c.id
    r = client.post("/api/erp/factusol/customers/link", json={
        "crm_type": "contact", "crm_id": cid, "factusol_codcli": "777",
    }, headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert s.get(Contact, cid).factusol_contact_id == "777"


def test_factusol_customers_link_duplicate(client, session_factory):
    with session_factory() as s:
        a = Company(name="Ya vinculada", factusol_company_id="2458")
        b = Company(name="Otra")
        s.add_all([a, b])
        s.commit()
        b_id = b.id
    r = client.post("/api/erp/factusol/customers/link", json={
        "crm_type": "company", "crm_id": b_id, "factusol_codcli": "2458",
    }, headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_linked"


def test_factusol_customers_link_forbidden_for_view_only(client, session_factory):
    with session_factory() as s:
        comp = Company(name="X")
        s.add(comp)
        s.commit()
        comp_id = comp.id
    r = client.post("/api/erp/factusol/customers/link", json={
        "crm_type": "company", "crm_id": comp_id, "factusol_codcli": "1",
    }, headers=auth_headers(client, "sat"))
    assert r.status_code == 403


# --- alta -------------------------------------------------------------------


def _create_payload(comp_id, **over):
    base = {"crm_type": "company", "crm_id": comp_id, "nombre": "Nueva SL",
            "nif": "B12345678", "direccion": "C Falsa 1", "ciudad": "Barcelona",
            "cp": "08001", "provincia": "Barcelona"}
    base.update(over)
    return base


def test_factusol_customers_create_new(client, session_factory):
    with session_factory() as s:
        comp = Company(name="Nueva SL")
        s.add(comp)
        s.commit()
        comp_id = comp.id
    fake = FakeFactusol([], max_codcli=4531)
    with _patch_client(fake):
        r = client.post("/api/erp/factusol/customers/create",
                        json=_create_payload(comp_id),
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True and body["factusol_codcli"] == "4532"
    written = next(rec for t, rec in fake.writes if t == "F_CLI")
    assert written["CODCLI"] == "4532"
    assert written["NOFCLI"] == "Nueva SL" and written["NIFCLI"] == "B12345678"
    assert written["NOCCLI"] == "Nueva SL"   # comercial = fiscal por defecto
    assert written["DOMCLI"] == "C Falsa 1"
    assert written["PAICLI"] == "724"        # ES → ISO numérico
    with session_factory() as s:
        assert s.get(Company, comp_id).factusol_company_id == "4532"


def test_factusol_customers_create_deduplicates(client, session_factory):
    """Si el NIF ya existe en F_CLI no se escribe: se vincula el existente.
    Este es el guard que evita el BDEscribirRegistroError de C-2-fix1."""
    with session_factory() as s:
        comp = Company(name="Ya en FACTUSOL")
        s.add(comp)
        s.commit()
        comp_id = comp.id
    fake = FakeFactusol([_cli(2458, nif="B12345678")], max_codcli=4531)
    with _patch_client(fake):
        r = client.post("/api/erp/factusol/customers/create",
                        json=_create_payload(comp_id),
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is False and body["factusol_codcli"] == "2458"
    assert fake.writes == []          # NO se escribió nada
    with session_factory() as s:
        assert s.get(Company, comp_id).factusol_company_id == "2458"


def test_factusol_customers_create_rejects_woo_managed(client, session_factory):
    """En clientes con pedidos Woo el cliente lo crea la app externa."""
    with session_factory() as s:
        comp = Company(name="Cliente Woo")
        s.add(comp)
        s.flush()
        s.add(Order(order_number="BOPRIN-1", company_id=comp.id,
                    external_source="woocommerce"))
        s.commit()
        comp_id = comp.id
    fake = FakeFactusol([], max_codcli=1)
    with _patch_client(fake):
        r = client.post("/api/erp/factusol/customers/create",
                        json=_create_payload(comp_id),
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "woo_managed_customer"
    assert fake.writes == []


# --- C-3-fix1: nombres de columna REALES de F_CLI ---------------------------


def test_search_by_name_searches_both_nofcli_and_noccli(client):
    """El nombre puede estar solo en el fiscal o solo en el comercial."""
    fake = FakeFactusol([_cli(1)])
    with _patch_client(fake):
        client.get("/api/erp/factusol/customers/search?q=LABORA&by=name",
                   headers=auth_headers(client, "pedidos"))
    filtro = fake.filters[0]
    assert "NOFCLI" in filtro and "NOCCLI" in filtro
    assert "LIKE" in filtro.upper()
    # Regresión del bug de prod: NOMCLI no existe en F_CLI.
    assert "NOMCLI" not in filtro


def test_build_customer_payload_uses_real_columns():
    from app.integrations.factusol.customers import build_customer_payload

    payload = build_customer_payload({
        "nombre": "Nueva SL", "nif": "B1", "direccion": "C Falsa 1",
        "ciudad": "Barcelona", "cp": "08001", "provincia": "Barcelona",
        "pais": "ES",
    }, "77")
    assert payload["NOFCLI"] == "Nueva SL"     # fiscal
    assert payload["NOCCLI"] == "Nueva SL"     # comercial
    assert payload["NIFCLI"] == "B1"
    assert payload["DOMCLI"] == "C Falsa 1"
    assert payload["PAICLI"] == "724"
    # Las columnas inventadas del bug NO deben aparecer.
    for dead in ("NOMCLI", "CIFCLI", "DIRCLI", "NACCLI"):
        assert dead not in payload


def test_country_code_maps_iso_alpha2_to_numeric():
    from app.integrations.factusol.customers import _country_code

    assert _country_code("ES") == "724"
    assert _country_code("FR") == "250"
    assert _country_code("US") == "840"
    assert _country_code("es") == "724"      # case-insensitive
    assert _country_code("724") == "724"     # ya numérico → tal cual
    assert _country_code("XX") == "724"      # desconocido → fallback ES
    assert _country_code("") == "724"


def test_search_exposes_aliases_for_frontend(client):
    """`nombre` (comercial > fiscal) y `nif` los consume la UI directamente."""
    fake = FakeFactusol([_cli(1, nombre="PORTA FISCAL SL")])
    with _patch_client(fake):
        r = client.get("/api/erp/factusol/customers/search?q=B64113590&by=nif",
                       headers=auth_headers(client, "pedidos"))
    item = r.json()["items"][0]
    assert item["nombre"] == "PORTA FISCAL SL"
    assert item["nif"] == "B64113590"
    assert item["nofcli"] == "PORTA FISCAL SL"
    assert item["domcli"] == "c. Fígols, 19-21"


def test_search_alias_falls_back_to_fiscal_when_commercial_empty(client):
    fake = FakeFactusol([_cli(1, NOCCLI="")])
    with _patch_client(fake):
        r = client.get("/api/erp/factusol/customers/search?q=B64113590&by=nif",
                       headers=auth_headers(client, "pedidos"))
    assert r.json()["items"][0]["nombre"] == "LABORATORIOS PORTA S.L."


# --- C-3-fix3: crear empresa CRM + vincular, atómico ------------------------


def _crm_and_link_body(codcli="2758", **over):
    data = {"nombre": "LABORATORIOS PORTA S.L.", "nif": "B64113590",
            "direccion": "c. Fígols, 19-21", "ciudad": "Barcelona",
            "cp": "08028", "provincia": "Barcelona"}
    data.update(over)
    return {"factusol_codcli": codcli, "factusol_customer_data": data}


def test_create_crm_and_link_success(client, session_factory):
    r = client.post("/api/erp/factusol/customers/create-crm-and-link",
                    json=_crm_and_link_body(),
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True and body["factusol_codcli"] == "2758"
    with session_factory() as s:
        comp = s.get(Company, body["company_id"])
        assert comp.name == "LABORATORIOS PORTA S.L."
        assert comp.factusol_company_id == "2758"   # creada Y vinculada
        assert comp.tax_id == "B64113590"
        assert comp.city == "Barcelona"


def test_create_crm_and_link_rejects_if_taken(client, session_factory):
    """El bug de prod: reintentar dejaba empresas huérfanas. Ahora se rechaza
    ANTES de crear nada y el 409 explica a qué empresa está vinculado."""
    with session_factory() as s:
        s.add(Company(name="PORTA YA VINCULADA", factusol_company_id="2758"))
        s.commit()
        before = s.query(Company).count()

    r = client.post("/api/erp/factusol/customers/create-crm-and-link",
                    json=_crm_and_link_body(),
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    detail = r.json()["detail"]["detail"]
    assert "2758" in detail and "PORTA YA VINCULADA" in detail

    with session_factory() as s:
        # NINGUNA empresa nueva: cero huérfanas.
        assert s.query(Company).count() == before
        assert s.query(Company).filter(
            Company.factusol_company_id.is_(None)).count() == 0


def test_create_crm_and_link_rollback_on_db_error(client, session_factory):
    """Si la escritura falla a mitad, no queda ninguna empresa creada."""
    with session_factory() as s:
        before = s.query(Company).count()

    with patch("app.erp.api.factusol._audit_customer_link",
               side_effect=RuntimeError("boom")):
        r = client.post("/api/erp/factusol/customers/create-crm-and-link",
                        json=_crm_and_link_body(codcli="9999"),
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "create_and_link_failed"
    with session_factory() as s:
        assert s.query(Company).count() == before   # rollback efectivo


def test_create_crm_and_link_forbidden_for_view_only(client):
    r = client.post("/api/erp/factusol/customers/create-crm-and-link",
                    json=_crm_and_link_body(), headers=auth_headers(client, "sat"))
    assert r.status_code == 403
