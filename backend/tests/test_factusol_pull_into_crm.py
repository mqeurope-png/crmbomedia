"""ERP · Alta de pedido + ficha de empresa: buscador de proformas, precarga de
la empresa desde FACTUSOL y «Traer datos de FACTUSOL» (FACTUSOL → CRM).

Cliente FACTUSOL simulado (F_CLI, F_PRE, F_LPS). «Traer datos» sobrescribe el
mapping de la diff + país en la empresa CRM, deja auditoría y NUNCA escribe en
FACTUSOL.
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
from app.integrations.factusol.customers import (
    PULL_FIELDS,
    apply_pull,
    get_customer,
    pull_changes,
    search_customers,
)
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users


class FakeFactusol:
    """F_CLI / F_PRE / F_LPS en memoria. Aplica los filtros de igualdad del
    lector y registra TODAS las escrituras (deben quedar vacías)."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self.tables = tables
        self.default_ejercicio = "2026"
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[tuple[str, str]] = []

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((tabla, filtro))
        rows = list(self.tables.get(tabla, []))
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        if predicate.startswith("UPPER("):
            # UPPER(NIFCLI)=UPPER('x') → igualdad insensible a mayúsculas.
            column = predicate[len("UPPER("):predicate.index(")")]
            wanted = predicate.split("UPPER('", 2)[-1].rstrip("')").casefold()
            return [r for r in rows if str(r.get(column) or "").casefold() == wanted]
        column, _, raw = predicate.partition("=")
        column, wanted = column.strip(), raw.strip().strip("'")
        if rows and column not in rows[0]:
            return []
        return [r for r in rows if str(r.get(column)) == wanted]

    def write_record(self, tabla, data, *, ejercicio=None):  # pragma: no cover
        self.writes.append((tabla, dict(data)))
        return {"respuesta": "OK"}

    def update_record(self, tabla, data, *, ejercicio=None):  # pragma: no cover
        self.updates.append((tabla, dict(data)))
        return {"respuesta": "OK"}

    def delete_records(self, tabla, filtro, *, ejercicio=None):  # pragma: no cover
        self.deletes.append((tabla, filtro))
        return {"respuesta": "OK"}


def _cli(codcli: int, **over: Any) -> dict[str, Any]:
    base = {
        "CODCLI": codcli, "NIFCLI": "B12345678", "NOFCLI": "ACME SL",
        "NOCCLI": "Acme", "DOMCLI": "C/ Mayor 1", "POBCLI": "Madrid",
        "CPOCLI": "28001", "PROCLI": "Madrid", "PAICLI": "724",
        "EMACLI": "info@acme.example", "TELCLI": "600000000",
    }
    base.update(over)
    return base


def _pre(codpre: int, *, clipre="55555", cno="Roca Joiers", ref="Cabezal + SAT") -> dict:
    return {
        "CODPRE": codpre, "TIPPRE": "1", "CLIPRE": clipre, "CNOPRE": cno,
        "FECPRE": "2026-09-01T00:00:00", "ESTPRE": 0, "REFPRE": ref,
        "NET1PRE": 355.0, "PIVA1PRE": 21.0, "IIVA1PRE": 74.55, "TOTPRE": 429.55,
        "FOPPRE": "002",
    }


def _lps(codpre: int, pos: int, *, art="", desc="Línea", cant=1.0, precio=10.0) -> dict:
    return {
        "TIPLPS": "1", "CODLPS": codpre, "POSLPS": pos, "ARTLPS": art,
        "DESLPS": desc, "CANLPS": cant, "DT1LPS": 0.0, "PRELPS": precio,
        "TOTLPS": round(cant * precio, 2), "IVALPS": 0.0,
    }


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_CLI": [_cli(55555), _cli(77777, NIFCLI="B77777777", NOFCLI="OTRA SL")],
        "F_PRE": [_pre(574), _pre(575, clipre="77777", cno="Otra SL", ref="Tinta")],
        "F_LPS": [
            _lps(574, 1, art="MBO", desc="Cabezal MBO 250", precio=250),
            _lps(574, 2, art="SAT", desc="Hora SAT", cant=1, precio=105),
        ],
        "F_FPA": [{"CODFPA": "002", "DESFPA": "Transferencia"}],
    }


#: Empresa CRM con TODOS los campos distintos de FACTUSOL (para el pull).
OLD_COMPANY = {
    "id": "acme", "name": "Acme Viejo SL", "tax_id": "B00000000",
    "address_line": "Calle Vieja 9", "city": "Lleida", "postal_code": "25001",
    "state": "Lleida", "country": "FR", "factusol_company_id": "55555",
}


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
        seed.add(Company(**OLD_COMPANY))
        seed.add(Company(id="sinlink", name="Sin Vínculo SL"))
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


def _patched(fake: FakeFactusol):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


# --- A) buscador de proformas en el alta ----------------------------------------


def test_alta_pedido_buscador_proformas(session_factory, http) -> None:
    """El alta usa el mismo buscador de la ficha: `/quotes/search` (cualquier
    cliente, por nº / referencia / cliente) y luego carga la elegida con la
    previsualización de Fase 1."""
    fake = FakeFactusol(_tables())
    with _patched(fake):
        r = http.get("/api/erp/factusol/quotes/search", params={"q": "roca"},
                     headers=auth_headers(http, "pedidos"))
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [it["codpre"] for it in items] == ["574"]
        assert items[0]["cliente_nombre"] == "Roca Joiers"
        assert items[0]["referencia"] == "Cabezal + SAT"
        # Por número y por referencia también casa.
        assert [it["codpre"] for it in http.get(
            "/api/erp/factusol/quotes/search", params={"q": "575"},
            headers=auth_headers(http, "pedidos")).json()["items"]] == ["575"]
        assert [it["codpre"] for it in http.get(
            "/api/erp/factusol/quotes/search", params={"q": "tinta"},
            headers=auth_headers(http, "pedidos")).json()["items"]] == ["575"]
        # Y las de la empresa (el listado de la ficha) siguen saliendo por vínculo.
        mine = http.get("/api/erp/factusol/quotes", params={"company_id": "acme"},
                        headers=auth_headers(http, "pedidos")).json()
        assert [it["codpre"] for it in mine["items"]] == ["574"]
        # Elegida → se carga en el pedido (Fase 1) con sus líneas.
        p = http.get("/api/erp/orders/from-factusol/preview",
                     params={"doc_type": "presupuestos", "serie": 1, "codigo": 574},
                     headers=auth_headers(http, "pedidos"))
        assert p.status_code == 200, p.text
        assert [ln["description"] for ln in p.json()["lines"]] == ["Cabezal MBO 250", "Hora SAT"]
        assert p.json()["company_id"] == "acme"
    assert fake.writes == []


# --- B) precarga de la empresa desde FACTUSOL en el alta -------------------------


def test_alta_pedido_precarga_empresa_desde_factusol(session_factory, http) -> None:
    """Empresa vinculada → el alta lee su F_CLI por CODCLI y precarga NIF y
    dirección (país ya en ISO2)."""
    fake = FakeFactusol(_tables())
    with _patched(fake):
        r = http.get("/api/erp/factusol/customers/search",
                     params={"q": "55555", "by": "codcli"},
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    (cust,) = r.json()["items"]
    assert cust["codcli"] == "55555" and cust["nif"] == "B12345678"
    assert cust["domcli"] == "C/ Mayor 1" and cust["pobcli"] == "Madrid"
    assert cust["cpocli"] == "28001" and cust["procli"] == "Madrid"
    assert cust["paicli"] == "724" and cust["pais_iso2"] == "ES"
    assert cust["crm_link"] == {"type": "company", "id": "acme", "name": "Acme Viejo SL"}
    assert ("F_CLI", "CODCLI=55555") in fake.calls
    assert fake.writes == []
    # Un código no numérico no existe: [] sin tocar FACTUSOL.
    assert search_customers(fake, "abc", by="codcli", ejercicio="2026") == []
    assert get_customer(fake, "99999", ejercicio="2026") is None


# --- C) «Traer datos de FACTUSOL» --------------------------------------------------


def test_traer_datos_factusol_sobrescribe_empresa(session_factory, http) -> None:
    fake = FakeFactusol(_tables())
    with _patched(fake):
        # Previsualización: los 7 campos del mapping cambian (nada escrito).
        pre = http.get("/api/erp/factusol/customers/pull-preview",
                       params={"company_id": "acme"},
                       headers=auth_headers(http, "pedidos"))
        assert pre.status_code == 200, pre.text
        body = pre.json()
        assert body["codcli"] == "55555"
        assert {c["field"]: (c["crm"], c["factusol"]) for c in body["changes"]} == {
            "nombre": ("Acme Viejo SL", "ACME SL"),
            "nif": ("B00000000", "B12345678"),
            "direccion": ("Calle Vieja 9", "C/ Mayor 1"),
            "ciudad": ("Lleida", "Madrid"),
            "cp": ("25001", "28001"),
            "provincia": ("Lleida", "Madrid"),
            "pais": ("FR", "ES"),
        }
        assert body["changes"][0]["label"] == "Nombre"
        with session_factory() as s:
            assert s.get(Company, "acme").name == "Acme Viejo SL"  # la preview no toca

        # Traer datos: sobrescribe TODO el mapping (no solo lo vacío).
        r = http.post("/api/erp/factusol/customers/pull-into-crm",
                      json={"company_id": "acme"},
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 7 and r.json()["codcli"] == "55555"
        with session_factory() as s:
            c = s.get(Company, "acme")
            assert (c.name, c.tax_id, c.address_line, c.city, c.postal_code,
                    c.state, c.country) == (
                "ACME SL", "B12345678", "C/ Mayor 1", "Madrid", "28001", "Madrid", "ES",
            )
            assert c.factusol_company_id == "55555"      # el vínculo no cambia
            assert c.factusol_sync_source == "factusol_pull"
            assert c.factusol_synced_at is not None
            # Historial / auditoría.
            (log,) = list(s.scalars(select(AuditLog).where(
                AuditLog.action == "erp.factusol_customer_pull",
            )))
            assert log.target_type == "company" and log.target_id == "acme"
            assert log.actor_user_id is not None
            assert '"factusol_codcli": "55555"' in log.metadata_json
            assert "datos tra" in log.metadata_json and "cliente n" in log.metadata_json
            assert '"field": "nif"' in log.metadata_json

        # Idempotente: ya no hay nada que traer.
        again = http.post("/api/erp/factusol/customers/pull-into-crm",
                          json={"company_id": "acme"},
                          headers=auth_headers(http, "pedidos"))
        assert again.status_code == 200 and again.json()["applied"] == 0
        assert http.get("/api/erp/factusol/customers/pull-preview",
                        params={"company_id": "acme"},
                        headers=auth_headers(http, "pedidos")).json()["changes"] == []


def test_traer_datos_no_toca_factusol(session_factory, http) -> None:
    fake = FakeFactusol(_tables())
    with _patched(fake):
        assert http.get("/api/erp/factusol/customers/pull-preview",
                        params={"company_id": "acme"},
                        headers=auth_headers(http, "pedidos")).status_code == 200
        assert http.post("/api/erp/factusol/customers/pull-into-crm",
                         json={"company_id": "acme"},
                         headers=auth_headers(http, "pedidos")).status_code == 200
    # Solo LECTURAS de F_CLI; ninguna escritura, actualización ni borrado.
    assert fake.writes == [] and fake.updates == [] and fake.deletes == []
    assert {t for t, _ in fake.calls} == {"F_CLI"}
    assert all(f.startswith("CODCLI=") for _, f in fake.calls)


def test_traer_datos_errores_y_permisos(session_factory, http) -> None:
    fake = FakeFactusol(_tables())
    with _patched(fake):
        # Sin vínculo → 409 con código.
        r = http.post("/api/erp/factusol/customers/pull-into-crm",
                      json={"company_id": "sinlink"},
                      headers=auth_headers(http, "pedidos"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "company_unlinked"
        # Empresa inexistente → 404.
        assert http.post("/api/erp/factusol/customers/pull-into-crm",
                         json={"company_id": "nope"},
                         headers=auth_headers(http, "pedidos")).status_code == 404
        # Solo lectura no puede (ni previsualizar).
        assert http.post("/api/erp/factusol/customers/pull-into-crm",
                         json={"company_id": "acme"},
                         headers=auth_headers(http, "user")).status_code in (401, 403)
        assert http.get("/api/erp/factusol/customers/pull-preview",
                        params={"company_id": "acme"},
                        headers=auth_headers(http, "user")).status_code in (401, 403)
    # El CODCLI vinculado ya no existe en F_CLI → 404 con código.
    with _patched(FakeFactusol({"F_CLI": []})):
        r = http.get("/api/erp/factusol/customers/pull-preview",
                     params={"company_id": "acme"},
                     headers=auth_headers(http, "pedidos"))
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "factusol_customer_not_found"
    with session_factory() as s:
        assert s.get(Company, "acme").name == "Acme Viejo SL"  # nada cambió
    assert fake.writes == []


def test_pull_puro_nombre_vacio_no_pisa_y_mapping_completo() -> None:
    assert [f for f, _, _ in PULL_FIELDS] == [
        "nombre", "nif", "direccion", "ciudad", "cp", "provincia", "pais",
    ]
    company = Company(**OLD_COMPANY)
    customer = {"nofcli": "", "noccli": "", "nifcli": "B12345678", "domcli": "",
                "pobcli": "Madrid", "cpocli": "28001", "procli": "Madrid",
                "pais_iso2": "ES"}
    changes = apply_pull(company, customer)
    # Sin nombre en FACTUSOL, el nombre del CRM se conserva (NOT NULL).
    assert company.name == "Acme Viejo SL"
    assert "nombre" not in {c["field"] for c in changes}
    # Un campo vacío en FACTUSOL SÍ pisa el del CRM (FACTUSOL manda).
    assert company.address_line is None
    assert {c["field"] for c in changes} == {
        "nif", "direccion", "ciudad", "cp", "provincia", "pais",
    }
    assert pull_changes(company, customer) == []
