"""Tarea B — «+ Nuevo pedido manual» exige EMPRESA vinculada a FACTUSOL.

Para facturar / crear el albarán hace falta el cliente de FACTUSOL (F_CLI)
de la empresa: un contacto suelto no basta (422) y una empresa sin
`factusol_company_id` no vale (409 `company_not_linked` con el atajo «créala
primero»). El alta desde ficha de empresa (`?company_id=`) y la Fase 1 (con
`factusol_source`) siguen igual.
"""
from __future__ import annotations

from collections.abc import Generator

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

LINES = [{"description": "Reparación láser", "quantity": 1, "unit_price": 90}]


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
            Company(id="linked", name="Duplicoder SL", factusol_company_id="2458"),
            Company(id="unlinked", name="Sin FACTUSOL SL"),
        ])
        seed.flush()
        seed.add(Contact(id="ct-1", first_name="Eva", last_name="Carreira",
                         email="eva@example.com", company_id=None))
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


def _post(http, **body):
    return http.post("/api/erp/orders", json={"lines": LINES, **body},
                     headers=auth_headers(http, "pedidos"))


def test_pedido_manual_sin_empresa_no_se_crea(http, session_factory) -> None:
    """Solo un contacto (o nada) → 422: el pedido manual necesita empresa."""
    r = _post(http, contact_id="ct-1")
    assert r.status_code == 422, r.text
    assert "necesita una empresa" in r.text
    assert _post(http).status_code == 422
    with session_factory() as s:
        assert s.query(Order).count() == 0


def test_pedido_manual_empresa_no_en_factusol_pide_crear(http, session_factory) -> None:
    """Empresa sin vínculo a F_CLI → 409 `company_not_linked` con el atajo
    («créala primero en FACTUSOL»); no se crea nada. Empresa inexistente → 404."""
    r = _post(http, company_id="unlinked")
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "company_not_linked"
    assert "aún no existe en FACTUSOL" in body["detail"] and "créala primero" in body["detail"]
    assert body["company_id"] == "unlinked" and body["company_name"] == "Sin FACTUSOL SL"
    # Con contacto además de la empresa tampoco: la empresa manda.
    assert _post(http, company_id="unlinked", contact_id="ct-1").status_code == 409
    assert _post(http, company_id="no-existe").status_code == 404
    with session_factory() as s:
        assert s.query(Order).count() == 0


def test_pedido_manual_empresa_vinculada_ok(http, session_factory) -> None:
    """Empresa vinculada a FACTUSOL → 201 (con o sin contacto), como desde la
    ficha de empresa. La Fase 1 (`factusol_source`) mantiene su regla."""
    r = _post(http, company_id="linked")
    assert r.status_code == 201, r.text
    assert r.json()["company_id"] == "linked" and r.json()["external_source"] == "manual"
    r = _post(http, company_id="linked", contact_id="ct-1", order_number="MAN-CT")
    assert r.status_code == 201, r.text
    assert r.json()["contact_id"] == "ct-1"

    # Fase 1 (alta desde un documento FACTUSOL): el cliente viene del
    # documento; con contacto solo sigue valiendo (regla anterior), y la
    # comprobación de vínculo no se aplica (la empresa la resolvió el CODCLI).
    with session_factory() as s:
        s.get(Contact, "ct-1").company_id = None
        s.commit()
    r = _post(http, contact_id="ct-1", order_number="PRO-TEST", factusol_source={
        "doc_type": "presupuestos", "serie": 1, "codigo": 700,
    })
    assert r.status_code == 201, r.text
    assert r.json()["external_source"] == "factusol_proforma"
