"""PR-Consolidado — Star Rating tests.

Verifica:

1. PATCH /api/contacts/{id} acepta `star_rating` 0-5, rechaza otros
   valores con 422.
2. Cambios en star_rating emiten audit `contact.star_rating_changed`
   con metadata `{old, new}`.
3. Sync mapper de AgileCRM lee `star_value` del payload y lo escribe
   a `Contact.star_rating`. NULL o 0 → NULL en BD; 1-5 → ese valor;
   fuera de rango → NULL (defensa contra payloads custom).
4. `star_rating` y `lead_score` son independientes (cambiar uno no
   toca el otro, lectura separada).
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.integrations.agilecrm.mapper import map_agilecrm_contact_to_internal
from app.main import app
from app.models.crm import AuditLog, Base, Contact
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_contact(factory: sessionmaker, **kwargs) -> str:
    with factory() as session:
        contact = Contact(first_name="A", email="a@a.com", **kwargs)
        session.add(contact)
        session.commit()
        return contact.id


# ---------------------------------------------------------------------
# 1. Endpoint validation
# ---------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 2, 3, 4, 5, None])
def test_star_rating_accepts_0_to_5_and_null(
    client: TestClient, session_factory: sessionmaker, value: int | None
):
    """0-5 y None son válidos. Persisten al `star_rating` exactamente
    como vienen (None y 0 se almacenan tal cual; el frontend hace la
    equivalencia visual)."""
    contact_id = _seed_contact(session_factory)
    headers = auth_headers(client, "user")
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": value},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        contact = session.get(Contact, contact_id)
        assert contact.star_rating == value


@pytest.mark.parametrize("value", [-1, 6, 7, 100, "high", 1.5])
def test_star_rating_rejects_others_returns_422(
    client: TestClient, session_factory: sessionmaker, value
):
    """Cualquier valor fuera del 0-5 (negativos, >5, strings,
    floats) → 422."""
    contact_id = _seed_contact(session_factory)
    headers = auth_headers(client, "user")
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": value},
        headers=headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------
# 2. Audit log
# ---------------------------------------------------------------------


def test_patch_contact_updates_star_rating_audit_logged(
    client: TestClient, session_factory: sessionmaker
):
    """Cambiar star_rating emite `contact.star_rating_changed` con
    metadata `{old, new}` correctamente."""
    contact_id = _seed_contact(session_factory, star_rating=2)
    headers = auth_headers(client, "user")
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 5},
        headers=headers,
    )
    assert response.status_code == 200

    with session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "contact.star_rating_changed",
                    AuditLog.target_id == contact_id,
                )
            )
        )
        assert len(audits) == 1
        meta = json.loads(audits[0].metadata_json or "{}")
        assert meta["old"] == 2
        assert meta["new"] == 5


def test_patch_contact_same_star_rating_does_not_emit_audit(
    client: TestClient, session_factory: sessionmaker
):
    """Si el cliente manda el mismo valor que ya estaba, NO se emite
    un audit (evita ensuciar el log con no-ops)."""
    contact_id = _seed_contact(session_factory, star_rating=3)
    headers = auth_headers(client, "user")
    response = client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 3, "first_name": "Cambio"},
        headers=headers,
    )
    assert response.status_code == 200

    with session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "contact.star_rating_changed",
                    AuditLog.target_id == contact_id,
                )
            )
        )
        assert audits == []


# ---------------------------------------------------------------------
# 3. AgileCRM sync mapper
# ---------------------------------------------------------------------


def _payload_with_star_value(star_value):
    """Minimal AgileCRM payload con `star_value` configurable."""
    p = {
        "id": "ag-1",
        "tags": [],
        "properties": [
            {"name": "first_name", "value": "Ag"},
            {"name": "email", "value": "ag@example.com"},
        ],
    }
    if star_value is not None:
        p["star_value"] = star_value
    return p


@pytest.mark.parametrize("agile_value,expected", [
    (1, 1),
    (2, 2),
    (5, 5),
    (0, None),       # 0 → NULL (sin valorar)
    (None, None),    # ausente → NULL
    (6, None),       # > 5 → NULL (defensa)
    (-1, None),      # negativo → NULL
    ("bad", None),   # no numérico → NULL
])
def test_agile_sync_maps_star_value_to_star_rating(agile_value, expected):
    """El mapper traduce `star_value` → `star_rating` con el rango
    correcto. Valores fuera de 1-5 o no numéricos → NULL."""
    payload = _payload_with_star_value(agile_value)
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["star_rating"] == expected


def test_agile_sync_null_star_value_results_in_null_star_rating():
    """Payload sin `star_value` → `star_rating=None`. Sanity."""
    payload = {
        "id": "ag-2",
        "tags": [],
        "properties": [
            {"name": "first_name", "value": "Plain"},
            {"name": "email", "value": "plain@example.com"},
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record.get("star_rating") is None


# ---------------------------------------------------------------------
# 4. star_rating ⟂ lead_score (independencia)
# ---------------------------------------------------------------------


def test_star_rating_and_lead_score_are_independent(
    client: TestClient, session_factory: sessionmaker
):
    """Cambiar uno NO afecta al otro. La PR-Consolidado spec lo
    declara explícitamente."""
    contact_id = _seed_contact(session_factory, lead_score=42, star_rating=3)
    headers = auth_headers(client, "user")

    # Cambia solo star_rating.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"star_rating": 5},
        headers=headers,
    )
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        assert c.star_rating == 5
        assert c.lead_score == 42, "lead_score no debe haberse tocado"

    # Cambia solo lead_score.
    client.patch(
        f"/api/contacts/{contact_id}",
        json={"lead_score": 99},
        headers=headers,
    )
    with session_factory() as session:
        c = session.get(Contact, contact_id)
        assert c.lead_score == 99
        assert c.star_rating == 5, "star_rating no debe haberse tocado"


def test_agile_sync_lead_score_and_star_value_populate_separate_fields():
    """Payload con AMBOS campos. lead_score → Contact.lead_score,
    star_value → Contact.star_rating. NUNCA cross-contamination."""
    payload = {
        "id": "ag-3",
        "tags": [],
        "lead_score": 77,
        "star_value": 4,
        "properties": [
            {"name": "first_name", "value": "Both"},
            {"name": "email", "value": "both@example.com"},
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["lead_score"] == 77
    assert record["star_rating"] == 4
