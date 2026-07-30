"""Sprint Web-Forms PR-A — captura de leads desde formularios web.

El flujo público (`process_submission`) se testea directo (es la lógica
del endpoint POST submit) con las verificaciones anti-spam inyectadas
para no depender de red/Redis. Los endpoints admin + config.json van por
HTTP con TestClient.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    ActivityEvent,
    AssignmentRule,
    Contact,
    ContactTag,
    Tag,
    User,
    UserRole,
)
from app.models.web_forms import FormSubmission, WebForm, WebFormField
from app.services.web_forms import process_submission
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


# --- helpers ----------------------------------------------------------------


def _mk_form(session: Session, **overrides) -> WebForm:
    admin_id = session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    form = WebForm(
        slug=overrides.pop("slug", "contacto-mbo-es"),
        name=overrides.pop("name", "Contacto MBO (ES)"),
        brand=overrides.pop("brand", "mbo"),
        language=overrides.pop("language", "es"),
        created_by_user_id=admin_id,
        **overrides,
    )
    session.add(form)
    session.flush()
    fields = [
        WebFormField(form_id=form.id, field_key="name", label="Nombre",
                     field_type="text", position=0,
                     maps_to_contact_field="contact.first_name"),
        WebFormField(form_id=form.id, field_key="email", label="Email",
                     field_type="email", is_required=True, position=1,
                     maps_to_contact_field="contact.email"),
        WebFormField(form_id=form.id, field_key="phone", label="Teléfono",
                     field_type="tel", position=2,
                     maps_to_contact_field="contact.phone"),
        WebFormField(form_id=form.id, field_key="message", label="Mensaje",
                     field_type="textarea", position=3),
    ]
    session.add_all(fields)
    session.commit()
    session.refresh(form)
    return form


def _payload(**over) -> dict:
    base = {
        "name": "Sergio", "email": "sergio@lead.com",
        "phone": "600111222", "message": "Quiero info del 6090",
    }
    base.update(over)
    return base


def _meta(**over) -> dict:
    base = {"ip": "1.2.3.4", "user_agent": "jest", "recaptcha_token": "tok"}
    base.update(over)
    return base


def _high_score(_t, _i):
    return 0.9


def _allow_rate(_ip, _fid):
    return True


def _submit(session, form, payload=None, meta=None, verify=_high_score, rate=_allow_rate):
    return process_submission(
        session, form=form, payload=payload or _payload(), meta=meta or _meta(),
        verify_recaptcha_fn=verify, rate_limit_fn=rate,
    )


# --- contacto ---------------------------------------------------------------


def test_public_submit_creates_new_contact_when_email_new(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        out = _submit(s, form)
        assert out.http_status == 200
        assert out.created_contact is True
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        assert c is not None
        assert c.first_name == "Sergio"
        assert c.phone == "600111222"
        assert c.origin == "web_form"


def test_public_submit_updates_existing_contact_when_email_exists(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        s.add(Contact(first_name="Sergio", email="sergio@lead.com"))
        s.commit()
        out = _submit(s, form)
        assert out.created_contact is False
        # No duplica.
        assert s.scalar(select(func.count(Contact.id)).where(
            Contact.email == "sergio@lead.com")) == 1
        # Evento de historial.
        assert s.scalar(select(func.count(ActivityEvent.id)).where(
            ActivityEvent.event_type == "form.submitted")) == 1


def test_public_submit_never_overwrites_populated_contact_fields(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        s.add(Contact(first_name="Sergio", email="sergio@lead.com", phone="OLD"))
        s.commit()
        _submit(s, form, _payload(phone="999"))
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        assert c.phone == "OLD"  # no se pisa el teléfono ya relleno


def test_public_submit_applies_form_tag_idempotent(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        _submit(s, form)
        _submit(s, form)  # segundo submit del mismo email
        tag = s.scalar(select(Tag).where(Tag.name == "form:contacto-mbo-es"))
        assert tag is not None
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        links = s.scalar(select(func.count()).select_from(ContactTag).where(
            ContactTag.contact_id == c.id, ContactTag.tag_id == tag.id))
        assert links == 1


# --- asignación -------------------------------------------------------------


def test_public_submit_applies_assignment_rules_when_mode_rules(session_factory):
    with session_factory() as s:
        manager_id = s.scalar(select(User.id).where(User.role == UserRole.MANAGER))
        admin_id = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        s.add(AssignmentRule(
            name="catch-all new", is_active=True, priority=1,
            conditions_json=json.dumps({
                "type": "rule", "field": "commercial_status",
                "comparator": "eq", "value": "new",
            }),
            primary_user_id=manager_id,
            created_by_user_id=admin_id,
        ))
        s.commit()
        form = _mk_form(s, assignment_mode="rules")
        _submit(s, form)
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        assert c.owner_user_id == manager_id


def test_public_submit_assigns_fixed_owner_when_mode_fixed(session_factory):
    with session_factory() as s:
        uid = s.scalar(select(User.id).where(User.role == UserRole.USER))
        form = _mk_form(s, assignment_mode="fixed_owner", fixed_owner_user_id=uid)
        _submit(s, form)
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        assert c.owner_user_id == uid


def test_public_submit_leaves_owner_null_when_mode_none(session_factory):
    with session_factory() as s:
        form = _mk_form(s, assignment_mode="none")
        _submit(s, form)
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        assert c.owner_user_id is None


# --- anti-spam --------------------------------------------------------------


def test_public_submit_rejects_when_honeypot_filled(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        out = _submit(s, form, _payload(website="http://spam"))
        assert out.http_status == 400
        assert out.is_spam and out.spam_reason == "honeypot"
        assert s.scalar(select(func.count(Contact.id))) == 0
        sub = s.scalar(select(FormSubmission))
        assert sub.is_spam and sub.spam_reason == "honeypot" and sub.contact_id is None


def test_public_submit_rejects_when_recaptcha_score_below_0_5(session_factory):
    with session_factory() as s:
        form = _mk_form(s, recaptcha_enabled=True)
        out = _submit(s, form, verify=lambda _t, _i: 0.3)
        assert out.http_status == 400
        assert out.spam_reason == "recaptcha_low_score"
        sub = s.scalar(select(FormSubmission))
        assert float(sub.recaptcha_score) == pytest.approx(0.3)
        assert s.scalar(select(func.count(Contact.id))) == 0


def test_public_submit_rejects_when_rate_limit_exceeded(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        out = _submit(s, form, rate=lambda _ip, _fid: False)
        assert out.http_status == 429
        assert out.spam_reason == "rate_limit"
        assert s.scalar(select(func.count(Contact.id))) == 0


def test_public_submit_stores_utm_and_referrer_in_submission(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        _submit(s, form, meta=_meta(
            utm_source="google", utm_medium="cpc", utm_campaign="verano",
            referrer="https://google.com", landing_page="https://mbo.com/es",
        ))
        sub = s.scalar(select(FormSubmission))
        assert sub.utm_source == "google"
        assert sub.utm_medium == "cpc"
        assert sub.referrer == "https://google.com"
        assert sub.landing_page == "https://mbo.com/es"


# --- emails -----------------------------------------------------------------


def test_public_submit_sends_confirmation_email_when_configured(session_factory):
    from app.services.email import get_email_service

    svc = get_email_service()
    svc.sent.clear()
    with session_factory() as s:
        form = _mk_form(s, send_confirmation_email=True)
        _submit(s, form)
    assert any(e.to_email == "sergio@lead.com" for e in svc.sent)


def test_public_submit_notifies_owner_on_new_contact(session_factory):
    from app.services.email import get_email_service

    svc = get_email_service()
    with session_factory() as s:
        uid = s.scalar(select(User.id).where(User.role == UserRole.USER))
        owner_email = s.scalar(select(User.email).where(User.id == uid))
        form = _mk_form(s, assignment_mode="fixed_owner", fixed_owner_user_id=uid,
                        notify_owner_on_new=True)
        svc.sent.clear()
        _submit(s, form)
    assert any(e.to_email == owner_email for e in svc.sent)


# --- admin HTTP -------------------------------------------------------------


def _create_payload() -> dict:
    return {
        "slug": "contacto-mbo-es", "name": "Contacto MBO (ES)",
        "brand": "mbo", "language": "es", "assignment_mode": "none",
        "fields": [
            {"field_key": "email", "label": "Email", "field_type": "email",
             "is_required": True, "position": 0,
             "maps_to_contact_field": "contact.email"},
            {"field_key": "name", "label": "Nombre", "field_type": "text",
             "position": 1, "maps_to_contact_field": "contact.first_name"},
        ],
    }


def test_admin_forms_crud_full_lifecycle(client):
    # Create.
    r = client.post("/api/admin/forms", json=_create_payload(),
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert len(r.json()["fields"]) == 2
    # Get detail.
    d = client.get(f"/api/admin/forms/{fid}", headers=auth_headers(client, "manager"))
    assert d.status_code == 200 and d.json()["slug"] == "contacto-mbo-es"
    # Patch (rename + reemplaza campos).
    upd = _create_payload()
    upd["name"] = "Contacto MBO (ES) v2"
    upd["fields"] = upd["fields"][:1]  # deja solo email
    p = client.patch(f"/api/admin/forms/{fid}", json=upd,
                     headers=auth_headers(client, "manager"))
    assert p.status_code == 200
    assert p.json()["name"] == "Contacto MBO (ES) v2"
    assert len(p.json()["fields"]) == 1
    # List.
    lst = client.get("/api/admin/forms", headers=auth_headers(client, "manager"))
    assert any(f["id"] == fid for f in lst.json())
    # Soft delete.
    dele = client.delete(f"/api/admin/forms/{fid}", headers=auth_headers(client, "manager"))
    assert dele.status_code == 200
    got = client.get(f"/api/admin/forms/{fid}", headers=auth_headers(client, "manager"))
    assert got.json()["is_active"] is False


def test_admin_embed_code_returns_both_snippets(client):
    r = client.post("/api/admin/forms", json=_create_payload(),
                    headers=auth_headers(client, "manager"))
    fid = r.json()["id"]
    e = client.get(f"/api/admin/forms/{fid}/embed-code",
                   headers=auth_headers(client, "manager"))
    assert e.status_code == 200
    body = e.json()
    assert "script_snippet" in body and "iframe_snippet" in body
    assert fid in body["script_snippet"]
    assert "<script" in body["script_snippet"]
    assert "<iframe" in body["iframe_snippet"]


def test_admin_forms_scoped_by_role(client):
    # Comercial (user) no puede.
    denied = client.post("/api/admin/forms", json=_create_payload(),
                         headers=auth_headers(client, "user"))
    assert denied.status_code == 403
    denied_list = client.get("/api/admin/forms", headers=auth_headers(client, "user"))
    assert denied_list.status_code == 403
    # Manager sí.
    ok = client.get("/api/admin/forms", headers=auth_headers(client, "manager"))
    assert ok.status_code == 200


def test_public_config_json_exposes_schema_not_secrets(client, session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        fid = form.id
    r = client.get(f"/public/forms/{fid}/config.json")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "contacto-mbo-es"
    assert {f["key"] for f in body["fields"]} == {"name", "email", "phone", "message"}
    # No expone secretos internos.
    assert "recaptcha_secret" not in json.dumps(body)
    assert "assignment_mode" not in body
    assert "fixed_owner_user_id" not in json.dumps(body)
