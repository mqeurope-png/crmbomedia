"""La búsqueda de empresa por NIF-IVA es SIMÉTRICA respecto al prefijo de país.

El síntoma en producción: en el alta de pedido manual, buscar `FR91523447399`
encontraba «EURL Y'A PAS PHOTO» pero `91523447399` (sin el `FR`) no. Aquí se
fija el contrato a nivel de ENDPOINT — no solo del helper de normalización —
en los dos sentidos y con separadores, sin reescribir nunca el valor guardado.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users

NIF_FR = "FR91523447399"
NIF_BARE = "91523447399"
NOMBRE = "EURL Y'A PAS PHOTO"


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


def _names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [c["name"] for c in response.json()["items"]]


def _buscar(http, q: str) -> list[str]:
    return _names(http.get("/api/companies", params={"q": q},
                           headers=auth_headers(http, "admin")))


def test_guardado_con_prefijo_se_encuentra_sin_el(http, session_factory):
    """El caso del VPS: FACTUSOL guarda `FR…` y el operador teclea el número."""
    with session_factory() as s:
        s.add(Company(name=NOMBRE, tax_id=NIF_FR))
        s.commit()

    for q in (NIF_FR, NIF_BARE, "fr 91.523.447-399", " 91523447399 "):
        assert _buscar(http, q) == [NOMBRE], q


def test_guardado_sin_prefijo_se_encuentra_con_el(http, session_factory):
    """Y al revés, que es donde un `LIKE` sobre el valor crudo se pierde."""
    with session_factory() as s:
        s.add(Company(name="Bare SL", tax_id=NIF_BARE))
        s.commit()

    for q in (NIF_BARE, NIF_FR, "FR 91523447399"):
        assert _buscar(http, q) == ["Bare SL"], q


@pytest.mark.parametrize(("prefijado", "desnudo"), [
    ("FR91523447399", "91523447399"),
    ("BE0812240188", "0812240188"),
    ("DE455128445", "455128445"),
    ("NL123456789B01", "123456789B01"),
    ("ESB64113590", "B64113590"),
])
def test_simetria_por_pais(http, session_factory, prefijado, desnudo):
    with session_factory() as s:
        s.add(Company(name="Acme", tax_id=prefijado))
        s.commit()

    assert _buscar(http, prefijado) == ["Acme"], prefijado
    assert _buscar(http, desnudo) == ["Acme"], desnudo


def test_no_reescribe_el_valor_guardado(http, session_factory):
    """Buscar NO toca el dato: el `FR` guardado se conserva tal cual."""
    with session_factory() as s:
        s.add(Company(name=NOMBRE, tax_id=NIF_FR))
        s.commit()

    _buscar(http, NIF_BARE)

    with session_factory() as s:
        assert s.query(Company).one().tax_id == NIF_FR


def test_nif_nacional_y_nombre_siguen_igual(http, session_factory):
    """Regresión: el NIF español y la búsqueda por nombre no cambian."""
    with session_factory() as s:
        s.add(Company(name="Bomedia SL", tax_id="B64113590"))
        s.add(Company(name="Otra Empresa", tax_id="B12345674"))
        s.commit()

    assert _buscar(http, "B64113590") == ["Bomedia SL"]
    assert _buscar(http, "b-64.113.590") == ["Bomedia SL"]
    assert _buscar(http, "Bomedia") == ["Bomedia SL"]
    assert _buscar(http, "Empresa") == ["Otra Empresa"]


def test_un_numero_de_otra_empresa_no_se_cuela(http, session_factory):
    """El ensanchado del prefiltro no debe traer empresas que no casan."""
    with session_factory() as s:
        s.add(Company(name="Acme", tax_id=NIF_FR))
        s.add(Company(name="Otra", tax_id="FR99999999999"))
        s.commit()

    assert _buscar(http, NIF_BARE) == ["Acme"]
