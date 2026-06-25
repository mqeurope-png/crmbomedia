"""PR-Fix-Leads-Prioritarios-3a-Vez — end-to-end test del flujo:

  Dashboard widget pre-fetcha IDs → encodea rules `id IN [...]` →
  /contacts page parsea → POST /api/contacts/search con rules_json →
  resultado = los mismos contactos del widget.

El primer ciclo (PR #237) no cubrió este flow porque no había tests.
El segundo (PR #238) usaba `field: "id"` sin verificar que la
segments engine lo aceptara — el field `id` no estaba registrado y
la query 400-eaba silenciosamente. Este test prueba:

  1. `field: "id"` está registrado en la segments engine.
  2. `comparator: "in"` está permitido para `id`.
  3. El POST a /api/contacts/search con el árbol de rules devuelve
     EXACTAMENTE los contactos cuyo id está en la lista.
"""
from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import Base, Contact, UserRole
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sf() as seed:
        seed_test_users(seed)
        seed.commit()
    yield sf
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_id_field_is_registered_in_segments_engine():
    """Defensa contra la regresión que causó el bug 3 veces: si alguien
    quita `id` del registry, este test falla antes de que se pierda el
    fix del PR-Fix-Leads-Prioritarios-3a-Vez."""
    from app.services.segments.fields import FIELD_SPECS

    spec = FIELD_SPECS.get("id")
    assert spec is not None, (
        "Field `id` debe estar registrado para que el widget Leads "
        "prioritarios pueda construir el filtro URL `id IN [...]`."
    )
    assert "in" in spec.comparators
    assert "not_in" in spec.comparators


def test_contacts_search_with_id_in_filter_returns_matching_contacts(
    client, factory
):
    """End-to-end: simula el filtro que el widget construye en la URL.
    POST /api/contacts/search con árbol `id IN [ids]` debe devolver
    EXACTAMENTE esos contactos."""
    # Seed 3 contactos
    ids: list[str] = []
    with factory() as session:
        from sqlalchemy import select

        from app.models.crm import User

        admin = session.scalar(
            select(User).where(User.role == UserRole.ADMIN)
        )
        for label in ("a", "b", "c"):
            cid = str(uuid4())
            session.add(
                Contact(
                    id=cid,
                    first_name=label.upper(),
                    email=f"{label}@x.com",
                    owner_user_id=admin.id,
                    is_active=True,
                )
            )
            ids.append(cid)
        # Un 4º que NO debe aparecer
        ignored_id = str(uuid4())
        session.add(
            Contact(
                id=ignored_id,
                first_name="Z",
                email="z@x.com",
                owner_user_id=admin.id,
                is_active=True,
            )
        )
        session.commit()

    # Filtrar por los 3 primeros
    target_ids = ids[:3]
    rules_json = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "id",
                "comparator": "in",
                "value": target_ids,
            }
        ],
    }

    response = client.post(
        "/api/contacts/search",
        headers=auth_headers(client, "admin"),
        json={"rules_json": rules_json},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == set(target_ids)


def test_priority_leads_endpoint_accepts_limit_up_to_200(client, factory):
    """PR-Fix-Leads-Prioritarios-4a-Vez. El bug del PR #239 fue que el
    backend tenía `le=50` y el frontend pedía 500 → 422 → swallow →
    rule URL roto con `value:[""]`. Cap subido a 200 en este PR.

    Defensa: limit=200 debe devolver 200 OK; limit=201 debe 422. Si
    alguien sube el cap más arriba en el futuro, este test recordará
    sincronizar con el frontend."""
    response = client.get(
        "/api/dashboard/priority-leads?limit=200",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 200, response.text

    response = client.get(
        "/api/dashboard/priority-leads?limit=201",
        headers=auth_headers(client, "admin"),
    )
    assert response.status_code == 422, response.text


def test_contacts_search_with_unknown_field_returns_400(client, factory):
    """Defensa: una rule con field inexistente debe 400 (no error
    silencioso). El segundo PR no se enteró del fail porque no había
    test E2E que cubriera el path."""
    rules_json = {
        "operator": "AND",
        "children": [
            {
                "type": "rule",
                "field": "nonexistent_field_xyz",
                "comparator": "in",
                "value": ["abc"],
            }
        ],
    }

    response = client.post(
        "/api/contacts/search",
        headers=auth_headers(client, "admin"),
        json={"rules_json": rules_json},
    )
    assert response.status_code == 400, response.text
