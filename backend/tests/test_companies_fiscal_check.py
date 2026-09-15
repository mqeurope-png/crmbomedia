"""Fase 2 (rediseño de flujo) — buscador de empresa unificado + «Crear
empresa»: comprobación fiscal previa (régimen por país + NIF-IVA, duplicados
en el CRM y en FACTUSOL, gancho VIES), búsqueda también por NIF-IVA y guard
anti-duplicados por NIF en el alta.
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
from app.main import app
from app.models.crm import Company
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
        seed.add_all([
            Company(id="maison", name="SAS La Maison de la Plaque", country="FR",
                    tax_id="FR16339753527", vat="FR16339753527",
                    factusol_company_id="2760", domain="maisonplaque.fr"),
            Company(id="cadeau", name="La Maison du Cadeau", country="ES",
                    tax_id="B-98.765.432", city="Madrid"),
            Company(id="dupli", name="Duplicoder SL", country="ES",
                    tax_id="B12345678", factusol_company_id="2458"),
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


class _FakeFactusol:
    """F_CLI simulado para el guard anti-duplicados: un cliente con NIF."""

    def __init__(self, rows: list[dict] | None = None, *, broken: bool = False):
        self.default_ejercicio = "2026"
        self._rows = rows or []
        self.broken = broken
        self.filters: list[str] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if self.broken:
            raise RuntimeError("DELSOL caído")
        self.filters.append(filtro)
        nif = filtro.split("'")[1].upper() if "NIFCLI" in filtro else None
        return [r for r in self._rows if nif and str(r.get("NIFCLI", "")).upper() == nif]


def _patched(fake: _FakeFactusol):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _check(http, **params):
    return http.get("/api/companies/fiscal-check", params=params,
                    headers=auth_headers(http, "user"))


# --- régimen detectado --------------------------------------------------------


def test_fiscal_check_regimen_por_pais_y_vat(http) -> None:
    """La misma regla que fija el régimen del cliente F_CLI, antes de guardar."""
    with _patched(_FakeFactusol()):
        es = _check(http, country="España", tax_id="B11111111").json()
        assert es["country_iso2"] == "ES" and es["regime"] == "nacional"
        fr = _check(http, country="FR", vat="fr 16.339.753.527").json()
        assert fr["regime"] == "intracomunitario" and fr["in_eu"] is True
        assert fr["vat_normalized"] == "FR16339753527"
        assert "intracomunitario" in fr["regime_reason"]
        fr_sin_vat = _check(http, country="Francia").json()
        assert fr_sin_vat["regime"] == "nacional"          # consumidor final UE
        no = _check(http, country="NO", tax_id="987654321").json()
        assert no["regime"] == "exportacion" and no["in_eu"] is False
        # VIES aplica (UE con NIF-IVA) pero está desactivado en los tests →
        # «pendiente» sin veredicto; el detalle vivo está en `test_vies.py`.
        assert fr["vies"]["applies"] is True and fr["vies"]["vat"] == "FR16339753527"
        assert fr["vies"]["status"] == "pendiente" and fr["vies"]["valid"] is None
        assert es["vies"]["applies"] is False and no["vies"]["applies"] is False
        # «Volver a comprobar» (`force=true`) con VIES desactivado: mismo
        # resultado, sin error (lo que hace `force` se prueba en test_vies).
        forced = _check(http, country="FR", vat="FR16339753527", force="true")
        assert forced.status_code == 200
        assert forced.json()["vies"]["status"] == "pendiente"
        assert forced.json()["vies"]["checked_at"] is None


# --- duplicados -----------------------------------------------------------------


def test_fiscal_check_duplicados_crm_normalizando_nif(http) -> None:
    """`b98765432` casa con «B-98.765.432»; el NIF-IVA con la empresa vinculada."""
    with _patched(_FakeFactusol()):
        r = _check(http, tax_id="b98765432").json()
        assert [c["id"] for c in r["duplicates"]["crm"]] == ["cadeau"]
        # La tarjeta candidata («Usar esta», Lote 2): nombre, NIF, población y
        # vínculo FACTUSOL.
        assert r["duplicates"]["crm"][0] == {
            "id": "cadeau", "name": "La Maison du Cadeau", "tax_id": "B-98.765.432",
            "vat": None, "country": "ES", "city": "Madrid", "factusol_company_id": None,
        }
        r = _check(http, vat="FR16339753527").json()
        assert [c["id"] for c in r["duplicates"]["crm"]] == ["maison"]
        assert r["duplicates"]["crm"][0]["factusol_company_id"] == "2760"
        # Editando esa misma empresa no es un duplicado.
        r = _check(http, vat="FR16339753527", exclude_id="maison").json()
        assert r["duplicates"]["crm"] == []
        # Sin identificador no hay nada que comprobar (ni en FACTUSOL).
        r = _check(http, country="ES").json()
        assert r["duplicates"] == {
            "crm": [], "factusol": None, "factusol_checked": False, "factusol_error": None,
        }


def test_fiscal_check_duplicado_en_factusol(http) -> None:
    fake = _FakeFactusol([{"CODCLI": 3392, "NIFCLI": "BE0812240188",
                           "NOFCLI": "LIGUE BRAILLE", "NOCCLI": ""}])
    with _patched(fake):
        r = _check(http, country="BE", vat="BE0812240188").json()
    assert r["duplicates"]["factusol_checked"] is True
    assert r["duplicates"]["factusol"] == {
        "codcli": "3392", "nombre": "LIGUE BRAILLE", "nif": "BE0812240188",
    }
    assert r["duplicates"]["crm"] == []                    # existe solo en FACTUSOL


def test_fiscal_check_factusol_caido_no_bloquea(http) -> None:
    """DELSOL no responde → se informa (`factusol_checked=False`) y el alta
    sigue siendo posible; nunca un 5xx."""
    with _patched(_FakeFactusol(broken=True)):
        r = _check(http, tax_id="B99999999")
    assert r.status_code == 200
    dup = r.json()["duplicates"]
    assert dup["factusol_checked"] is False and dup["factusol"] is None
    assert "DELSOL" in dup["factusol_error"]


# --- buscador unificado -----------------------------------------------------------


def test_buscador_filtra_por_nombre_cif_dominio_y_vat(http) -> None:
    h = auth_headers(http, "user")
    ids = lambda q: [c["id"] for c in http.get(  # noqa: E731
        "/api/companies", params={"q": q, "limit": 10}, headers=h).json()["items"]]
    assert ids("la mai") == ["cadeau", "maison"]           # nombre (orden alfabético)
    assert ids("maisonplaque") == ["maison"]              # dominio
    assert ids("B12345678") == ["dupli"]                  # CIF
    assert ids("FR16339753527") == ["maison"]             # NIF-IVA (nuevo)
    # Cada resultado dice si está en FACTUSOL (nº) o es solo CRM.
    items = {c["id"]: c for c in http.get(
        "/api/companies", params={"q": "la mai"}, headers=h).json()["items"]}
    assert items["maison"]["factusol_company_id"] == "2760"
    assert items["cadeau"]["factusol_company_id"] is None


# --- alta: no duplicar por NIF ------------------------------------------------------


def test_crear_empresa_no_duplica_por_nif(http) -> None:
    h = auth_headers(http, "user")
    r = http.post("/api/companies", json={"name": "Duplicoder (otra vez)",
                                          "tax_id": "b-12345678"}, headers=h)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate_tax_id"
    assert detail["existing_company_id"] == "dupli"
    assert "FACTUSOL nº 2458" in detail["detail"]
    # Por NIF-IVA también.
    r = http.post("/api/companies", json={"name": "Maison bis", "vat": "FR16339753527"},
                  headers=h)
    assert r.status_code == 409 and r.json()["detail"]["existing_company_id"] == "maison"
    # Con NIF nuevo (o sin NIF, como el alta rápida de siempre) se crea.
    r = http.post("/api/companies", json={"name": "Nueva SL", "tax_id": "B55555555",
                                          "country": "ES"}, headers=h)
    assert r.status_code == 201 and r.json()["tax_id"] == "B55555555"
    r = http.post("/api/companies", json={"name": "Sin NIF SL"}, headers=h)
    assert r.status_code == 201
