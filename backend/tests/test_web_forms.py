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
    AuditLog,
    Company,
    Contact,
    ContactTag,
    CustomFieldDefinition,
    Note,
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


def test_public_endpoints_send_open_cors_header(client, session_factory):
    with session_factory() as s:
        fid = _mk_form(s).id
    r = client.get(f"/public/forms/{fid}/config.json")
    assert r.headers.get("access-control-allow-origin") == "*"


# --- embed: iframe HTML + widget JS (PR-B) ----------------------------------


def test_iframe_html_renders_form(client, session_factory):
    with session_factory() as s:
        fid = _mk_form(s).id
    r = client.get(f"/forms/{fid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "Contacto MBO (ES)" in body
    assert 'name="email"' in body
    assert 'name="website"' in body  # honeypot presente
    # El submit se construye en JS: apiBase + "/public/forms/" + id + "/submit".
    assert "/public/forms/" in body
    assert fid in body


def test_widget_js_served_small_and_selfcontained(client, session_factory):
    with session_factory() as s:
        fid = _mk_form(s).id
    r = client.get(f"/forms/embed/{fid}.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    body = r.text
    assert "config.json" in body  # fetch del schema
    assert fid in body
    assert "data-bohub-form" in body
    # Sin dependencias externas (no jQuery/React) y compacto.
    assert "jquery" not in body.lower()
    assert len(body.encode()) < 15_000  # <15KB en crudo (gzip aún menor)


def test_embed_endpoints_404_when_form_inactive(client, session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        form.is_active = False
        s.commit()
        fid = form.id
    assert client.get(f"/forms/{fid}").status_code == 404
    assert client.get(f"/forms/embed/{fid}.js").status_code == 404


# --- Hotfix editor: mapping + validación + default_value --------------------


def test_endpoint_contact_fields_mappable_returns_standard_and_custom(
    client, session_factory
):
    with session_factory() as s:
        admin_id = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        s.add(CustomFieldDefinition(
            key="product_interest", label="Producto de interés",
            field_type="text", source="manual", created_by_user_id=admin_id,
        ))
        s.commit()
    r = client.get("/api/admin/contact-fields-mappable",
                   headers=auth_headers(client, "manager"))
    assert r.status_code == 200, r.text
    body = r.json()
    std_values = {f["value"] for f in body["standard"]}
    assert "contact.email" in std_values and "contact.first_name" in std_values
    cust_values = {f["value"] for f in body["custom"]}
    assert "contact.custom.product_interest" in cust_values
    # comercial no accede.
    assert client.get("/api/admin/contact-fields-mappable",
                      headers=auth_headers(client, "user")).status_code == 403


def _mk_form_custom(session, fields: list[WebFormField], **over) -> WebForm:
    admin_id = session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    form = WebForm(slug=over.pop("slug", "f-custom"), name="F", brand="mbo",
                   language="es", created_by_user_id=admin_id, **over)
    session.add(form)
    session.flush()
    for f in fields:
        f.form_id = form.id
        session.add(f)
    session.commit()
    session.refresh(form)
    return form


def test_submit_engine_respects_maps_to_contact_field_when_set(session_factory):
    """El motor usa maps_to_contact_field, no adivina por field_key: un
    campo con clave 'nombre_completo' mapeado a contact.first_name debe
    poblar first_name."""
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="nombre_completo", label="Nombre",
                         field_type="text", position=0,
                         maps_to_contact_field="contact.first_name"),
            WebFormField(field_key="correo", label="Email", field_type="email",
                         is_required=True, position=1,
                         maps_to_contact_field="contact.email"),
        ])
        _submit(s, form, {"nombre_completo": "Sergio", "correo": "s@lead.com"})
        c = s.scalar(select(Contact).where(Contact.email == "s@lead.com"))
        assert c is not None and c.first_name == "Sergio"


def test_form_validation_rejects_select_without_options(client):
    payload = {
        "slug": "f-sel", "name": "F", "assignment_mode": "none",
        "fields": [
            {"field_key": "email", "label": "Email", "field_type": "email",
             "is_required": True, "position": 0},
            {"field_key": "prod", "label": "Producto", "field_type": "select",
             "options": [], "position": 1},
        ],
    }
    r = client.post("/api/admin/forms", json=payload,
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 400
    assert "desplegable" in r.text.lower() or "opción" in r.text.lower()


def test_submit_hidden_field_uses_default_value_when_empty(session_factory):
    """Un campo hidden con default_value que el submit no trae debe usar
    el default (típico UTM oculto)."""
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         is_required=True, position=0,
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="src", label="Origen", field_type="hidden",
                         is_hidden=True, default_value="web-directa", position=1,
                         maps_to_contact_field="contact.job_title"),
        ])
        # El payload NO trae 'src'.
        out = _submit(s, form, {"email": "s@lead.com"})
        assert out.http_status == 200
        c = s.scalar(select(Contact).where(Contact.email == "s@lead.com"))
        assert c.job_title == "web-directa"  # default aplicado


def test_iframe_renders_hidden_field_with_default_value(client, session_factory):
    """El campo hidden debe emitirse como input hidden con su default para
    que llegue en el submit (antes se descartaba)."""
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         is_required=True, position=0,
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="utm_source", label="UTM", field_type="hidden",
                         is_hidden=True, default_value="web-directa", position=1),
        ], slug="f-hidden")
        fid = form.id
    body = client.get(f"/forms/{fid}").text
    assert 'type="hidden" name="utm_source" value="web-directa"' in body


# --- v2: field_key autofill + tipo tags + idiomas ---------------------------


def test_form_field_key_autogenerated_from_label_when_empty(client):
    payload = {
        "slug": "f-auto", "name": "F", "assignment_mode": "none",
        "fields": [
            {"label": "Nombre completo", "field_type": "text"},
            {"label": "Email", "field_type": "email", "is_required": True,
             "maps_to_contact_field": "contact.email"},
        ],
    }
    r = client.post("/api/admin/forms", json=payload,
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 201, r.text
    keys = [f["field_key"] for f in r.json()["fields"]]
    assert "nombre_completo" in keys


def test_form_field_key_collision_gets_numeric_suffix(client):
    payload = {
        "slug": "f-col", "name": "F", "assignment_mode": "none",
        "fields": [
            {"label": "Producto", "field_type": "text"},
            {"label": "Producto", "field_type": "text"},
        ],
    }
    r = client.post("/api/admin/forms", json=payload,
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 201, r.text
    keys = sorted(f["field_key"] for f in r.json()["fields"])
    assert keys == ["producto", "producto_2"]


def test_form_tags_field_saves_options_json_with_tag_ids(client, session_factory):
    with session_factory() as s:
        t1 = Tag(name="MBO 3050", name_normalized="mbo 3050")
        s.add(t1)
        s.commit()
        tid = t1.id
    payload = {
        "slug": "f-tags", "name": "F", "assignment_mode": "none",
        "fields": [
            {"label": "Email", "field_type": "email",
             "maps_to_contact_field": "contact.email"},
            {"label": "Modelos", "field_type": "tags",
             "options": [{"tag_id": tid, "label": "MBO 3050"}]},
        ],
    }
    r = client.post("/api/admin/forms", json=payload,
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 201, r.text
    tags_field = next(f for f in r.json()["fields"] if f["field_type"] == "tags")
    assert tags_field["options"] == [{"tag_id": tid, "label": "MBO 3050"}]


def test_submit_applies_tags_to_contact_when_tags_field_selected(session_factory):
    with session_factory() as s:
        t1 = Tag(name="MBO 3050", name_normalized="mbo 3050")
        t2 = Tag(name="MBO 6090", name_normalized="mbo 6090")
        s.add_all([t1, t2])
        s.flush()
        tid1, tid2 = t1.id, t2.id
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         is_required=True, position=0,
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="modelos", label="Modelos", field_type="tags",
                         position=1, options_json=json.dumps([
                             {"tag_id": tid1, "label": "MBO 3050"},
                             {"tag_id": tid2, "label": "MBO 6090"},
                         ])),
        ], slug="f-tags-apply")
        _submit(s, form, _payload(modelos=[tid1, tid2]))
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        applied = {ct.tag_id for ct in s.scalars(
            select(ContactTag).where(ContactTag.contact_id == c.id))}
        assert {tid1, tid2} <= applied
        audits = [
            json.loads(a.metadata_json or "{}")
            for a in s.scalars(select(AuditLog).where(
                AuditLog.action == "contact_tag.added"))
        ]
        assert sum(1 for m in audits if m.get("via") == "form") == 2

    # Re-submit mismo email + mismas tags → no duplica ni re-audita.
    with session_factory() as s:
        form = s.scalar(select(WebForm).where(WebForm.slug == "f-tags-apply"))
        _submit(s, form, _payload(modelos=[tid1, tid2]))
        c = s.scalar(select(Contact).where(Contact.email == "sergio@lead.com"))
        n = s.scalar(select(func.count()).select_from(ContactTag).where(
            ContactTag.contact_id == c.id, ContactTag.tag_id.in_([tid1, tid2])))
        assert n == 2
        n_audit = sum(
            1 for a in s.scalars(select(AuditLog).where(
                AuditLog.action == "contact_tag.added"))
            if json.loads(a.metadata_json or "{}").get("via") == "form"
        )
        assert n_audit == 2  # no se re-audita


def test_endpoint_tags_selectable_returns_search_results(client, session_factory):
    with session_factory() as s:
        s.add_all([
            Tag(name="MBO 3050", name_normalized="mbo 3050"),
            Tag(name="Artis 5000", name_normalized="artis 5000"),
        ])
        s.commit()
    r = client.get("/api/admin/tags-selectable?search=mbo",
                   headers=auth_headers(client, "manager"))
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "MBO 3050" in names
    assert "Artis 5000" not in names


def test_web_form_accepts_language_pt_and_nl(client):
    for lang in ("pt", "nl"):
        r = client.post("/api/admin/forms", json={
            "slug": f"f-{lang}", "name": "F", "language": lang,
            "assignment_mode": "none",
            "fields": [{"label": "Email", "field_type": "email",
                        "maps_to_contact_field": "contact.email"}],
        }, headers=auth_headers(client, "manager"))
        assert r.status_code == 201, r.text
        assert r.json()["language"] == lang


def test_iframe_renders_tags_field_as_checkbox_group(client, session_factory):
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         position=0, maps_to_contact_field="contact.email"),
            WebFormField(field_key="modelos", label="Modelos", field_type="tags",
                         position=1, options_json=json.dumps([
                             {"tag_id": "t1", "label": "MBO 3050"},
                             {"tag_id": "t2", "label": "MBO 6090"},
                         ])),
        ], slug="f-iframe-tags")
        fid = form.id
    body = client.get(f"/forms/{fid}").text
    assert 'name="modelos[]"' in body
    assert "MBO 3050" in body and "MBO 6090" in body


# --- v3: notas + mapeador ampliado + HTML embed + contact_action ------------


def _mk_notes_form(session, **over):
    return _mk_form_custom(session, [
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, position=0,
                     maps_to_contact_field="contact.email"),
        WebFormField(field_key="mensaje", label="Mensaje", field_type="textarea",
                     position=1, maps_to_contact_field="contact.notes"),
    ], slug=over.pop("slug", "f-notes"), **over)


def test_notes_field_appends_with_timestamp_and_form_name(session_factory):
    import re as _re
    with session_factory() as s:
        form = _mk_notes_form(s)
        _submit(s, form, {"email": "n@lead.com", "mensaje": "Quiero info del 6090"})
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        notes = s.scalars(select(Note).where(Note.contact_id == c.id)).all()
        assert len(notes) == 1
        body = notes[0].body
        assert "Quiero info del 6090" in body
        assert f"Formulario: {form.name}" in body
        assert _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", body)
        assert notes[0].source == "web_form"


def test_notes_field_does_not_overwrite_existing_notes(session_factory):
    with session_factory() as s:
        form = _mk_notes_form(s)
        s.add(Contact(first_name="N", email="n@lead.com"))
        s.flush()
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        s.add(Note(contact_id=c.id, body="Nota previa importante", source="manual"))
        s.commit()
        _submit(s, form, {"email": "n@lead.com", "mensaje": "Nueva desde el form"})
        bodies = [n.body for n in s.scalars(select(Note).where(Note.contact_id == c.id))]
        assert len(bodies) == 2
        assert any("Nota previa importante" in b for b in bodies)  # sigue ahí
        assert any("Nueva desde el form" in b for b in bodies)     # + la nueva


def test_notes_field_first_entry_without_previous_separator(session_factory):
    with session_factory() as s:
        form = _mk_notes_form(s)
        _submit(s, form, {"email": "n@lead.com", "mensaje": "Primera entrada"})
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        notes = s.scalars(select(Note).where(Note.contact_id == c.id)).all()
        assert len(notes) == 1
        # La primera entrada empieza por su propia cabecera, sin arrastrar
        # contenido anterior (una sola cabecera de separador).
        assert notes[0].body.startswith("--- ")
        assert notes[0].body.count("--- ") == 1


def test_lead_score_field_ignores_non_numeric_input(session_factory):
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         is_required=True, position=0,
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="score", label="Score", field_type="text",
                         position=1, maps_to_contact_field="contact.lead_score"),
        ], slug="f-score")
        _submit(s, form, {"email": "bad@lead.com", "score": "no-numero"})
        _submit(s, form, {"email": "good@lead.com", "score": "77"})
        bad = s.scalar(select(Contact).where(Contact.email == "bad@lead.com"))
        good = s.scalar(select(Contact).where(Contact.email == "good@lead.com"))
        assert bad.lead_score is None   # no numérico → ignorado
        assert good.lead_score == 77    # numérico válido → aplicado


def test_stars_and_commercial_status_fields_applied(session_factory):
    with session_factory() as s:
        form = _mk_form_custom(s, [
            WebFormField(field_key="email", label="Email", field_type="email",
                         is_required=True, position=0,
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="rating", label="Interés", field_type="select",
                         position=1, maps_to_contact_field="contact.stars",
                         options_json=json.dumps([{"value": "5", "label": "Mucho"}])),
            WebFormField(field_key="estado", label="Estado", field_type="select",
                         position=2, maps_to_contact_field="contact.commercial_status",
                         options_json=json.dumps([{"value": "qualified", "label": "Cualificado"}])),
        ], slug="f-stars")
        _submit(s, form, {"email": "n@lead.com", "rating": "5", "estado": "qualified"})
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        assert c.star_rating == 5
        assert c.commercial_status == "qualified"


def _mk_company_form(session, **over):
    return _mk_form_custom(session, [
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, position=0,
                     maps_to_contact_field="contact.email"),
        WebFormField(field_key="empresa", label="Empresa", field_type="text",
                     position=1, maps_to_contact_field="contact.company_id"),
    ], slug=over.pop("slug", "f-company"), **over)


def test_company_lookup_by_name_case_insensitive(session_factory):
    with session_factory() as s:
        existing = Company(name="Bomedia")
        s.add(existing)
        s.flush()
        cid = existing.id
        form = _mk_company_form(s)
        _submit(s, form, {"email": "n@lead.com", "empresa": "bOmEdIa"})
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        assert c.company_id == cid  # reusa la existente
        assert s.scalar(select(func.count(Company.id)).where(
            func.lower(Company.name) == "bomedia")) == 1  # no duplica


def test_company_created_when_not_exists_with_form_metadata(session_factory):
    with session_factory() as s:
        form = _mk_company_form(s)
        _submit(s, form, {"email": "n@lead.com", "empresa": "Nueva SL"})
        comp = s.scalar(select(Company).where(Company.name == "Nueva SL"))
        assert comp is not None
        assert comp.source == "web_form"  # provenance del formulario
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        assert c.company_id == comp.id


def test_company_not_overwritten_if_contact_already_has_one(session_factory):
    with session_factory() as s:
        orig = Company(name="Original SA")
        s.add(orig)
        s.flush()
        oid = orig.id
        s.add(Contact(first_name="N", email="n@lead.com", company_id=oid))
        s.commit()
        form = _mk_company_form(s)
        out = _submit(s, form, {"email": "n@lead.com", "empresa": "Otra Distinta"})
        c = s.scalar(select(Contact).where(Contact.email == "n@lead.com"))
        assert c.company_id == oid  # respeta la empresa existente
        # No crea la empresa descartada.
        assert s.scalar(select(Company).where(Company.name == "Otra Distinta")) is None
        # Deja constancia del intento en el raw_payload de la submission.
        sub = s.get(FormSubmission, out.submission_id)
        assert "_company_change_skipped" in sub.raw_payload_json


def test_html_embed_endpoint_returns_valid_html_with_recaptcha_snippet(
    client, session_factory, monkeypatch
):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "recaptcha_site_key", "test-site-key")
    with session_factory() as s:
        fid = _mk_form(s, recaptcha_enabled=True).id
    r = client.get(f"/public/forms/{fid}/html")
    assert r.status_code == 200
    body = r.text
    # Fragmento puro: sin <html>/<head>/<body>.
    assert "<html" not in body.lower() and "<body" not in body.lower()
    # <form> con action al submit + method POST.
    assert 'method="POST"' in body and "/public/forms/" in body
    # reCAPTCHA v3: script api.js + ejecución inline + token.
    assert "recaptcha/api.js" in body
    assert "grecaptcha" in body
    assert "recaptcha_token" in body
    # Clases semánticas + sugerencia de estilos comentada.
    assert "bh-form" in body and "bh-field" in body and "bh-button" in body
    assert "Sugerencia de estilos" in body


def test_html_embed_includes_honeypot_and_all_fields(client, session_factory):
    with session_factory() as s:
        fid = _mk_form(s).id  # name, email, phone, message
    r = client.get(f"/public/forms/{fid}/html")
    assert r.status_code == 200
    body = r.text
    assert 'name="website"' in body  # honeypot siempre
    for key in ("name", "email", "phone", "message"):
        assert f'name="{key}"' in body


def test_submission_contact_action_created_for_new_contact(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        out = _submit(s, form)
        sub = s.get(FormSubmission, out.submission_id)
        assert sub.contact_action == "created"


def test_submission_contact_action_updated_for_existing_contact(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        s.add(Contact(first_name="Sergio", email="sergio@lead.com"))
        s.commit()
        out = _submit(s, form)
        sub = s.get(FormSubmission, out.submission_id)
        assert sub.contact_action == "updated"


def test_submission_contact_action_spam_for_blocked_submission(session_factory):
    with session_factory() as s:
        form = _mk_form(s)
        out = _submit(s, form, _payload(website="http://spam"))
        sub = s.get(FormSubmission, out.submission_id)
        assert sub.contact_action == "spam"


def test_mappable_endpoint_includes_v3_targets(client):
    r = client.get("/api/admin/contact-fields-mappable",
                   headers=auth_headers(client, "manager"))
    assert r.status_code == 200
    values = {f["value"] for f in r.json()["standard"]}
    assert {
        "contact.notes", "contact.lead_score", "contact.stars",
        "contact.commercial_status", "contact.company_id",
    } <= values


def test_admin_embed_code_includes_pure_html_snippet(client):
    r = client.post("/api/admin/forms", json=_create_payload(),
                    headers=auth_headers(client, "manager"))
    fid = r.json()["id"]
    e = client.get(f"/api/admin/forms/{fid}/embed-code",
                   headers=auth_headers(client, "manager"))
    assert e.status_code == 200
    body = e.json()
    assert "html_snippet" in body
    assert "bh-form" in body["html_snippet"]
    assert 'method="POST"' in body["html_snippet"]


# --- v4: fix estrellas + tipo campo `stars` visual -------------------------


def _mk_stars_form(session, *, field_type="stars", **over):
    return _mk_form_custom(session, [
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, position=0,
                     maps_to_contact_field="contact.email"),
        WebFormField(field_key="valoracion", label="Valoración",
                     field_type=field_type, position=1,
                     maps_to_contact_field="contact.stars"),
    ], slug=over.pop("slug", "f-stars-v4"), **over)


def test_stars_mapping_saves_integer_to_star_rating(session_factory):
    """Bug 1: un submit con stars=5 sobre contacto nuevo guarda 5 (int) en
    la columna real star_rating."""
    with session_factory() as s:
        form = _mk_stars_form(s)
        _submit(s, form, {"email": "new@lead.com", "valoracion": "5"})
        c = s.scalar(select(Contact).where(Contact.email == "new@lead.com"))
        assert c.star_rating == 5
        assert isinstance(c.star_rating, int)


def test_stars_mapping_ignores_invalid_input(session_factory):
    with session_factory() as s:
        form = _mk_stars_form(s)
        _submit(s, form, {"email": "a@lead.com", "valoracion": "no-numero"})
        _submit(s, form, {"email": "b@lead.com", "valoracion": "9"})   # fuera de rango
        _submit(s, form, {"email": "c@lead.com", "valoracion": ""})    # vacío
        for email in ("a@lead.com", "b@lead.com", "c@lead.com"):
            c = s.scalar(select(Contact).where(Contact.email == email))
            assert c is not None and c.star_rating is None


def test_stars_mapping_does_not_overwrite_existing_star_rating(session_factory):
    with session_factory() as s:
        form = _mk_stars_form(s)
        s.add(Contact(first_name="X", email="x@lead.com", star_rating=3))
        s.commit()
        _submit(s, form, {"email": "x@lead.com", "valoracion": "5"})
        c = s.scalar(select(Contact).where(Contact.email == "x@lead.com"))
        assert c.star_rating == 3  # respeta la valoración ya asignada


def test_field_type_stars_accepted_by_form_creation(client):
    payload = {
        "slug": "f-stars-create", "name": "F", "assignment_mode": "none",
        "fields": [
            {"label": "Email", "field_type": "email",
             "maps_to_contact_field": "contact.email"},
            {"label": "Valoración", "field_type": "stars",
             "maps_to_contact_field": "contact.stars"},
        ],
    }
    r = client.post("/api/admin/forms", json=payload,
                    headers=auth_headers(client, "manager"))
    assert r.status_code == 201, r.text
    types = {f["field_type"] for f in r.json()["fields"]}
    assert "stars" in types


def test_iframe_and_html_render_stars_field_as_radio_group(client, session_factory):
    with session_factory() as s:
        form = _mk_stars_form(s, slug="f-stars-render")
        fid = form.id
    for path in (f"/forms/{fid}", f"/public/forms/{fid}/html"):
        body = client.get(path).text
        assert 'class="bh-stars"' in body
        assert 'name="valoracion" value="5"' in body
        assert 'name="valoracion" value="1"' in body
        assert "★" in body
