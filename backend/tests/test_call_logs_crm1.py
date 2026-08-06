"""CRM · CRM-1 — filtro de contactos por atributos de llamada + nota al
timeline + acción star score + llamadas en actividad reciente.

Se prueba de punta a punta contra la API real: registrar una llamada, filtrar
`GET /api/contacts` por sus atributos, y comprobar que la nota se propaga, el
star score se aplica y la actividad aparece.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    ActivityEvent,
    Base,
    CallLog,
    Contact,
    ContactTag,
    Note,
    Tag,
)
from tests._test_helpers import auth_headers, seed_test_users


@dataclass
class _Fixture:
    engine: Engine
    factory: sessionmaker


@pytest.fixture()
def db() -> Generator[_Fixture, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield _Fixture(engine=engine, factory=factory)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: _Fixture) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with db.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _contact(factory: sessionmaker, name: str, email: str) -> str:
    with factory() as session:
        c = Contact(first_name=name, email=email, tags="",
                    commercial_status="new")
        session.add(c)
        session.commit()
        return c.id


def _log_call(client: TestClient, contact_id: str, **body) -> dict:
    r = client.post(
        f"/api/contacts/{contact_id}/calls",
        json=body, headers=auth_headers(client),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ids(client: TestClient, **params) -> set[str]:
    r = client.get("/api/contacts", params=params, headers=auth_headers(client))
    assert r.status_code == 200, r.text
    return {c["id"] for c in r.json()["items"]}


# --- Parte A: filtros -------------------------------------------------------


def test_contacts_filter_by_call_result(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="interested")
    _log_call(client, b, result_code="no_answer")

    assert _ids(client, call_result="interested") == {a}
    assert _ids(client, call_result="no_answer") == {b}
    # Multivalor: al menos uno de los dos resultados.
    assert _ids(client, call_result=["interested", "no_answer"]) == {a, b}


def test_contacts_filter_by_call_duration_range(client, db):
    """El spec pedía un rango en segundos, pero `call_logs` solo guarda el
    TRAMO (`duration_bucket`): se filtra por bucket."""
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="contacted", duration_bucket="5_to_30min")
    _log_call(client, b, result_code="contacted", duration_bucket="lt_1min")

    assert _ids(client, call_duration_bucket="5_to_30min") == {a}
    assert _ids(client,
                call_duration_bucket=["1_to_5min", "5_to_30min"]) == {a}


def test_contacts_filter_by_call_date_range(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="contacted",
              called_at="2026-03-15T10:00:00Z")
    _log_call(client, b, result_code="contacted",
              called_at="2026-01-05T10:00:00Z")

    assert _ids(client, call_date_from="2026-03-01T00:00:00Z") == {a}
    assert _ids(client, call_date_to="2026-02-01T00:00:00Z") == {b}
    assert _ids(client, call_date_from="2026-03-01T00:00:00Z",
                call_date_to="2026-03-31T00:00:00Z") == {a}


def test_contacts_filter_by_call_action_posterior(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="interested",
              actions={"adjust_star_score": 4})
    _log_call(client, b, result_code="interested",
              actions={"lead_score_delta": 10})

    assert _ids(client, call_action="adjust_star_score") == {a}
    assert _ids(client, call_action="adjust_lead_score") == {b}
    assert _ids(client,
                call_action=["adjust_star_score", "adjust_lead_score"]) == {a, b}


def test_contacts_filter_combined_call_and_tag(client, db):
    """Los filtros de llamada se combinan con el resto (aquí, un tag)."""
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="interested")
    _log_call(client, b, result_code="interested")
    with db.factory() as session:
        tag = Tag(name="VIP", name_normalized="vip")
        session.add(tag)
        session.flush()
        session.add(ContactTag(contact_id=a, tag_id=tag.id))
        session.commit()
        tag_id = tag.id

    # Ambos tienen la llamada, pero solo Ana el tag.
    assert _ids(client, call_result="interested") == {a, b}
    assert _ids(client, call_result="interested", tag_ids=[tag_id]) == {a}


def test_same_call_must_satisfy_all_flat_filters(client, db):
    """El endpoint plano exige que sea la MISMA llamada la que cumpla todos los
    criterios (a diferencia del builder, donde cada leaf es suelto)."""
    a = _contact(db.factory, "Ana", "ana@x.com")
    # Dos llamadas distintas: una interesada larga, otra no-contesta corta.
    _log_call(client, a, result_code="interested", duration_bucket="gt_30min")
    _log_call(client, a, result_code="no_answer", duration_bucket="lt_1min")

    # «interesada Y corta» no lo cumple ninguna sola llamada.
    assert _ids(client, call_result="interested",
                call_duration_bucket="lt_1min") == set()
    # «interesada Y larga» sí.
    assert _ids(client, call_result="interested",
                call_duration_bucket="gt_30min") == {a}


def test_call_filter_via_rules_engine_search(client, db):
    """El filtro también existe como campo del builder de reglas, que es el
    que pinta la página /contacts."""
    a = _contact(db.factory, "Ana", "ana@x.com")
    b = _contact(db.factory, "Bea", "bea@x.com")
    _log_call(client, a, result_code="interested")
    _log_call(client, b, result_code="voicemail")

    r = client.post(
        "/api/contacts/search",
        json={"rules_json": {"operator": "and", "children": [
            {"type": "rule", "field": "call_result",
             "comparator": "in", "value": ["interested"]},
        ]}},
        headers=auth_headers(client),
    )
    assert r.status_code == 200, r.text
    assert {c["id"] for c in r.json()["items"]} == {a}


# --- Parte C: nota → timeline -----------------------------------------------


def test_call_log_with_note_creates_note_timeline_entry(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    call = _log_call(client, a, result_code="interested",
                     notes="Cliente pidió catálogo")

    with db.factory() as session:
        note = session.scalars(
            select(Note).where(Note.contact_id == a)
        ).one()
        assert note.body == "Cliente pidió catálogo"
        assert note.source == "call_log"
        assert note.call_log_id == call["id"]

    # Y aparece en la pestaña Notas del contacto.
    r = client.get(f"/api/contacts/{a}/notes", headers=auth_headers(client))
    assert r.status_code == 200
    assert any(n["content"] == "Cliente pidió catálogo" for n in r.json())


def test_call_log_without_note_creates_no_note(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    _log_call(client, a, result_code="no_answer")
    with db.factory() as session:
        assert session.scalars(select(Note).where(Note.contact_id == a)).all() == []


def test_note_survives_when_call_is_deleted(client, db):
    """Borrar la llamada conserva la nota (FK ON DELETE SET NULL): menos
    destructivo que perder lo que se escribió."""
    a = _contact(db.factory, "Ana", "ana@x.com")
    call = _log_call(client, a, result_code="interested", notes="importante")
    r = client.delete(f"/api/contacts/{a}/calls/{call['id']}",
                      headers=auth_headers(client))
    assert r.status_code == 200
    with db.factory() as session:
        note = session.scalars(select(Note).where(Note.contact_id == a)).one()
        assert note.body == "importante"
        assert note.call_log_id is None


# --- Parte D: star score ----------------------------------------------------


def test_call_log_action_adjust_star_score_updates_contact_star_rating(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    call = _log_call(client, a, result_code="interested",
                     actions={"adjust_star_score": 4})

    assert "adjust_star_score" in call["actions_executed"]
    with db.factory() as session:
        contact = session.get(Contact, a)
        assert contact.star_rating == 4
        row = session.get(CallLog, call["id"])
        # El valor queda persistido en actions_taken, para el filtro y el resumen.
        assert json.loads(row.actions_taken)["adjust_star_score"] == 4


def test_star_score_out_of_range_is_rejected(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    r = client.post(
        f"/api/contacts/{a}/calls",
        json={"result_code": "interested", "actions": {"adjust_star_score": 9}},
        headers=auth_headers(client),
    )
    assert r.status_code == 422


def test_lead_score_and_star_score_are_independent(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    _log_call(client, a, result_code="interested",
              actions={"lead_score_delta": 15, "adjust_star_score": 5})
    with db.factory() as session:
        contact = session.get(Contact, a)
        assert contact.lead_score == 15
        assert contact.star_rating == 5


# --- Parte E: actividad reciente --------------------------------------------


def test_recent_activity_includes_call_logs(client, db):
    a = _contact(db.factory, "Ana", "ana@x.com")
    _log_call(client, a, result_code="interested",
              notes="pidió catálogo", duration_bucket="1_to_5min")

    with db.factory() as session:
        event = session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.contact_id == a,
                ActivityEvent.event_type == "CALL_LOG",
            )
        ).one()
        assert "Interesado" in (event.subject or "")
        assert event.body == "pidió catálogo"
        assert json.loads(event.metadata_json)["duration_bucket"] == "1_to_5min"

    # Y sale por el endpoint que alimenta la ficha.
    r = client.get(f"/api/contacts/{a}/activity-events",
                   headers=auth_headers(client))
    assert r.status_code == 200
    assert any(e["event_type"] == "CALL_LOG" for e in r.json()["items"])
