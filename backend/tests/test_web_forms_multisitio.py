"""Formularios web con 25 formularios en ocho webs y seis idiomas.

- 14: el origen del lead dice de qué web y en qué idioma viene, se puede
  filtrar y segmentar por cualquiera de los dos, y los leads ya entrados se
  reconstruyen (migración 0127).
- 15: un solo código de inserción por web (`/forms/embed/<marca>.js`), que
  resuelve el idioma de la página y tiene respaldo por marca.
- 16: el botón y los textos del formulario, en su idioma.
- 17: el código que se copia incluye el `<div>` contenedor.
- 18: aviso por correo de cada lead, con todo lo necesario para contestar.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.web_forms_embed as embed
import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import ConsentStatus, Contact, Tag, User, UserRole
from app.models.web_forms import WebForm, WebFormField
from app.services.segments import engine as seg_engine
from app.services.segments.fields import FIELD_SPECS
from app.services.web_forms import process_submission
from app.services.web_forms.aviso import construir_lead, destinatarios, enviar_aviso_lead
from app.services.web_forms.marcas import etiqueta_de_origen, origen_de, origen_legible
from app.services.web_forms.seleccion import formulario_de_marca
from app.services.web_forms.textos import texto_submit, textos
from tests._test_helpers import auth_headers, seed_test_users

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://bohub.example"


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(factory, monkeypatch) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    monkeypatch.setattr(embed, "_api_base", lambda: API_BASE)
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _form(s: Session, *, slug: str, marca: str, idioma: str, campos=None,
          **over) -> WebForm:
    autor = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    form = WebForm(slug=slug, name=f"Contacto {marca} {idioma}", brand=marca,
                   language=idioma, created_by_user_id=autor, recaptcha_enabled=False,
                   **over)
    s.add(form)
    s.flush()
    for i, c in enumerate(campos or [
        WebFormField(field_key="nombre", label="Nombre", field_type="text",
                     maps_to_contact_field="contact.first_name"),
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, maps_to_contact_field="contact.email"),
    ]):
        c.form_id = form.id
        c.position = i
        s.add(c)
    s.commit()
    s.refresh(form)
    return form


def _enviar(s: Session, form: WebForm, payload: dict):
    return process_submission(s, form=form, payload=payload, meta={"ip": "1.2.3.4"},
                              verify_recaptcha_fn=lambda _t, _i: 0.9,
                              rate_limit_fn=lambda _ip, _f: True)


# --- 14 · el origen dice de dónde viene -------------------------------------------------------


def test_el_lead_lleva_su_web_y_su_idioma(factory):
    with factory() as s:
        form = _form(s, slug="mboprinters-contacto-de", marca="mboprinters", idioma="de")
        out = _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de"})
        c = s.get(Contact, out.contact_id)
        assert c.origin == "Formulario web · mboprinters.com (alemán)"
        assert c.origin_account_id == "web_form:mboprinters:de"
    # Y se lee igual desde el origen guardado (ficha, avisos).
    assert etiqueta_de_origen("web_form:mboprinters:de") == "mboprinters.com (alemán)"
    assert etiqueta_de_origen("web_form:boprint-contacto") is None   # formato antiguo


def test_una_marca_sin_web_conocida_no_inventa_dominio(factory):
    with factory() as s:
        form = _form(s, slug="marca-nueva-es", marca="marca-nueva", idioma="es")
        out = _enviar(s, form, {"nombre": "A", "email": "a@b.es"})
        assert s.get(Contact, out.contact_id).origin == "Formulario web · marca-nueva (español)"


@pytest.mark.parametrize(("campo", "valor", "encuentra"), [
    ("lead_web", "mboprinters", True),
    ("lead_web", "mbolasers", False),
    ("lead_idioma", "de", True),
    ("lead_idioma", "es", False),
])
def test_se_filtra_por_web_y_por_idioma(factory, campo, valor, encuentra):
    """«Los leads de mboprinters» y «los leads en alemán», sin tirar de la
    etiqueta `form:<slug>`."""
    with factory() as s:
        form = _form(s, slug="mboprinters-contacto-de", marca="mboprinters", idioma="de")
        out = _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de"})
        reglas = {"operator": "AND", "children": [
            {"type": "rule", "field": campo, "comparator": "eq", "value": valor}]}
        ids = set(s.scalars(select(Contact.id).where(seg_engine.build_filter(reglas))))
        assert (out.contact_id in ids) is encuentra
        # El mismo criterio en memoria (reglas de asignación, workflows).
        contacto = s.get(Contact, out.contact_id)
        assert seg_engine.evaluate_contact_against_rules(contacto, reglas) is encuentra


def test_los_dos_campos_se_ofrecen_con_su_texto():
    web = FIELD_SPECS["lead_web"]
    assert web.enum_labels["mboprinters"] == "mboprinters.com"
    assert FIELD_SPECS["lead_idioma"].enum_labels["de"] == "alemán"
    assert "nl" in FIELD_SPECS["lead_idioma"].enum_values


# --- 15 · un solo código por web -------------------------------------------------------------


def test_el_embed_por_marca_sirve_el_idioma_de_la_pagina(http, factory):
    with factory() as s:
        _form(s, slug="mbolasers-es", marca="mbolasers", idioma="es")
        _form(s, slug="mbolasers-nl", marca="mbolasers", idioma="nl")
        _form(s, slug="mbolasers-en", marca="mbolasers", idioma="en",
              is_brand_default=True)
    js = http.get("/forms/embed/mbolasers.js")
    assert js.status_code == 200
    # El propio widget resuelve el idioma y pide el config de la marca.
    assert '"mbolasers"' in js.text and "/public/forms/by-brand/" in js.text
    assert "documentElement.getAttribute(\"lang\")" in js.text

    def cfg(lang):
        return http.get(f"/public/forms/by-brand/mbolasers/config.json?lang={lang}").json()

    assert cfg("nl")["slug"] == "mbolasers-nl"
    assert cfg("nl-BE")["slug"] == "mbolasers-nl"       # nl-BE → nl
    assert cfg("fr")["slug"] == "mbolasers-en"          # sin francés → el de respaldo
    assert cfg("")["slug"] == "mbolasers-es"            # sin idioma → el castellano


def test_sin_respaldo_marcado_cae_al_castellano_y_luego_al_ingles(factory):
    with factory() as s:
        _form(s, slug="fluxlasers-es", marca="fluxlasers", idioma="es")
        _form(s, slug="fluxlasers-de", marca="fluxlasers", idioma="de")
        assert formulario_de_marca(s, "fluxlasers", "fr").slug == "fluxlasers-es"
        assert formulario_de_marca(s, "FLUXLASERS", "de").slug == "fluxlasers-de"
        assert formulario_de_marca(s, "no-existe", "es") is None


def test_una_marca_sin_formularios_activos_lo_dice_en_la_consola(http, factory):
    with factory() as s:
        _form(s, slug="pimpam-es", marca="pimpam-vending", idioma="es", is_active=False)
    js = http.get("/forms/embed/pimpam-vending.js")
    assert js.status_code == 200
    assert "console.warn" in js.text and "brand_without_forms" in js.text
    r = http.get("/public/forms/by-brand/pimpam-vending/config.json?lang=es")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "brand_without_forms"


def test_el_embed_por_id_sigue_funcionando(http, factory):
    """Es lo que está pegado hoy en las webs: no se toca."""
    with factory() as s:
        fid = _form(s, slug="boprint-contacto", marca="boprint", idioma="es").id
    js = http.get(f"/forms/embed/{fid}.js")
    assert js.status_code == 200
    assert f'CLAVE="{fid}"' in js.text.replace(" ", "")
    assert "MARCA=null" in js.text.replace(" ", "")      # no es modo marca
    assert http.get(f"/public/forms/{fid}/config.json").json()["slug"] == "boprint-contacto"


# --- 16 · el formulario, en su idioma ---------------------------------------------------------


@pytest.mark.parametrize(("idioma", "boton"), [
    ("es", "Enviar"), ("en", "Send"), ("fr", "Envoyer"),
    ("de", "Senden"), ("nl", "Versturen"), ("pt", "Enviar"),
])
def test_el_boton_va_en_el_idioma_del_formulario(http, factory, idioma, boton):
    with factory() as s:
        form = _form(s, slug=f"mbolasers-{idioma}", marca="mbolasers", idioma=idioma)
        fid = form.id
        fragmento = embed.build_pure_html_fragment(form, api_base=API_BASE, site_key=None)
    iframe = http.get(f"/forms/{fid}").text
    cfg = http.get(f"/public/forms/{fid}/config.json").json()
    assert cfg["submit_text"] == boton
    assert f">{boton}</button>" in iframe
    assert f">{boton}</button>" in fragmento


def test_los_mensajes_del_formulario_tambien(http, factory):
    with factory() as s:
        form = _form(s, slug="mboprinters-de", marca="mboprinters", idioma="de")
        fid = form.id
        fragmento = embed.build_pure_html_fragment(form, api_base=API_BASE, site_key=None)
    cfg = http.get(f"/public/forms/{fid}/config.json").json()
    assert cfg["texts"]["gracias"].startswith("Vielen Dank")
    assert cfg["texts"]["missing_required"] == "Es fehlen Pflichtfelder."
    iframe = http.get(f"/forms/{fid}").text
    # Los textos viajan al JS del iframe y del fragmento, no van fijos.
    for html_ in (iframe, fragmento):
        assert "Vielen Dank" in html_
        # Ni un texto en castellano en un formulario alemán.
        assert "¡Gracias!" not in html_ and "No se pudo enviar" not in html_
    js = http.get(f"/forms/embed/{fid}.js").text
    assert "texts:cfg.texts" in js and "T(res.j&&res.j.error)" in js


def test_el_texto_propio_del_boton_manda_sobre_el_idioma(http, factory):
    with factory() as s:
        form = _form(s, slug="mbolasers-fr", marca="mbolasers", idioma="fr",
                     appearance_json=json.dumps({"submit_text": "Demander un devis"}))
        fid = form.id
    assert http.get(f"/public/forms/{fid}/config.json").json()["submit_text"] == \
        "Demander un devis"
    assert texto_submit("fr", None) == "Envoyer"
    assert textos("xx")["submit"] == "Enviar"       # idioma desconocido → castellano


# --- 17 · el código que se copia -------------------------------------------------------------


def test_el_codigo_a_copiar_lleva_el_div_y_la_variante_por_marca(http, factory):
    with factory() as s:
        fid = _form(s, slug="mbolasers-es", marca="mbolasers", idioma="es").id
    code = http.get(f"/api/admin/forms/{fid}/embed-code",
                    headers=auth_headers(http, "manager")).json()
    assert f'<div data-bohub-form="{fid}"></div>' in code["script_snippet"]
    assert code["script_snippet"].count("\n") == 1          # las DOS líneas
    assert code["brand"] == "mbolasers"
    assert '/forms/embed/mbolasers.js' in code["brand_snippet"]
    assert '<div data-bohub-form="mbolasers"></div>' in code["brand_snippet"]


# --- 18 · aviso por correo de cada lead -------------------------------------------------------


def _form_completo(s: Session, **over) -> WebForm:
    t1 = Tag(name="Artisjet Young", name_normalized="artisjet young")
    t2 = Tag(name="Gran Formato", name_normalized="gran formato")
    s.add_all([t1, t2])
    s.commit()
    campos = [
        WebFormField(field_key="nombre", label="Nombre", field_type="text",
                     maps_to_contact_field="contact.first_name"),
        WebFormField(field_key="apellidos", label="Apellidos", field_type="text",
                     maps_to_contact_field="contact.last_name"),
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, maps_to_contact_field="contact.email"),
        WebFormField(field_key="telefono", label="Teléfono", field_type="tel",
                     maps_to_contact_field="contact.phone"),
        WebFormField(field_key="productos", label="Productos", field_type="tags",
                     options_json=json.dumps(
                         [{"tag_id": t1.id, "label": t1.name},
                          {"tag_id": t2.id, "label": t2.name}])),
        WebFormField(field_key="pais", label="País", field_type="text"),
        WebFormField(field_key="consulta", label="Consulta", field_type="textarea"),
        WebFormField(field_key="comerciales", label="Acepto comunicaciones comerciales",
                     field_type="checkbox",
                     maps_to_contact_field="contact.marketing_consent"),
    ]
    return _form(s, slug="mboprinters-contacto-de", marca="mboprinters", idioma="de",
                 campos=campos, **over)


PAYLOAD = {
    "nombre": "Hans", "apellidos": "Müller", "email": "hans@muster.de",
    "telefono": "+49 170 000", "pais": "Alemania",
    "consulta": "Necesito presupuesto de dos equipos.\nGracias.",
    "comerciales": "on",
}


def _servicio_email():
    from app.services.email import get_email_service

    svc = get_email_service()
    svc.sent.clear()
    return svc


def test_el_aviso_lleva_la_web_el_idioma_los_productos_y_la_consulta(factory):
    svc = _servicio_email()
    with factory() as s:
        form = _form_completo(s)
        productos = [o["tag_id"] for o in json.loads(form.fields[4].options_json)]
        out = _enviar(s, form, {**PAYLOAD, "productos": productos})
        assert out.http_status == 200, out.response
        contacto = s.get(Contact, out.contact_id)
        assert contacto.marketing_consent == ConsentStatus.GRANTED
    aviso = next(e for e in svc.sent if e.to_email == "info@streamtec.es")
    assert aviso.subject == "Nuevo lead desde mboprinters.com (alemán)"
    for texto in (aviso.text_body, aviso.html_body):
        assert "Hans Müller" in texto and "hans@muster.de" in texto
        assert "+49 170 000" in texto
        assert "Artisjet Young" in texto and "Gran Formato" in texto
        assert "Necesito presupuesto de dos equipos." in texto
        assert "Alemania" in texto                       # ningún campo se pierde
        assert f"/contacts/{out.contact_id}" in texto    # enlace a la ficha
    assert "aceptadas" in aviso.text_body.lower()
    # Ni un uuid de etiqueta a la vista.
    assert all(p not in aviso.text_body for p in productos)


def test_tambien_al_comercial_asignado_y_sin_duplicar(factory):
    svc = _servicio_email()
    with factory() as s:
        comercial = s.scalar(select(User).where(User.role == UserRole.USER))
        form = _form_completo(s, assignment_mode="fixed_owner",
                              fixed_owner_user_id=comercial.id)
        _enviar(s, form, {**PAYLOAD, "email": "otro@muster.de"})
        destinos = sorted(e.to_email for e in svc.sent)
        assert destinos == sorted(["info@streamtec.es", comercial.email])
        # Si el comercial ES la dirección fija, un solo correo.
        contacto = Contact(email="x@y.z", first_name="X", owner_user_id=comercial.id)
        comercial.email = "info@streamtec.es"
        s.flush()
        assert destinatarios(s, contacto) == [("info@streamtec.es", "")]


def test_un_fallo_de_correo_no_tumba_el_lead(factory, monkeypatch, caplog):
    from app.services import email as email_mod

    class Roto:
        def send_notification(self, **kw):
            raise RuntimeError("SMTP caído")

    monkeypatch.setattr(email_mod, "get_email_service", lambda: Roto())
    with factory() as s, caplog.at_level(logging.WARNING):
        form = _form_completo(s)
        out = _enviar(s, form, PAYLOAD)
        assert out.http_status == 200
        assert s.get(Contact, out.contact_id) is not None     # el lead se guarda
    assert "aviso_lead" in caplog.text


def test_sin_notify_owner_no_se_avisa(factory):
    svc = _servicio_email()
    with factory() as s:
        form = _form_completo(s, notify_owner_on_new=False)
        _enviar(s, form, PAYLOAD)
    assert svc.sent == []


def test_una_marca_puede_tener_su_propia_plantilla(factory, tmp_path, monkeypatch):
    """`lead_<marca>.html` se usa sin tocar código (logotipo, firma)."""
    from app.services import email as email_mod

    plantillas = tmp_path / "email"
    plantillas.mkdir()
    for nombre in ("lead_notification.txt", "lead_notification.html"):
        (plantillas / nombre).write_text("base {{ web }}")
    (plantillas / "lead_mboprinters.html").write_text("<p>PROPIA {{ web }}</p>")
    monkeypatch.setattr(email_mod, "_jinja_env", email_mod.Environment(
        loader=email_mod.FileSystemLoader(plantillas),
        autoescape=email_mod.select_autoescape(["html", "xml"]),
    ))
    svc = _servicio_email()
    with factory() as s:
        form = _form_completo(s)
        _enviar(s, form, PAYLOAD)
    aviso = svc.sent[0]
    assert aviso.html_body == "<p>PROPIA mboprinters.com</p>"
    assert aviso.text_body == "base mboprinters.com"       # el texto, el de base


def test_el_lead_del_aviso_se_arma_sin_tocar_la_base(factory):
    with factory() as s:
        form = _form_completo(s)
        contacto = Contact(email="a@b.de", first_name="Ana", last_name="Gil",
                           phone="600", marketing_consent=ConsentStatus.UNKNOWN)
        lead = construir_lead(form, contacto, {"consulta": "Hola", "pais": "España"},
                              etiquetas=["Textil"], nuevo=False)
    assert lead.asunto() == "Nuevo lead desde mboprinters.com (alemán)"
    assert lead.nombre == "Ana Gil" and lead.consulta == "Hola"
    assert lead.campos == [("País", "España")] and lead.etiquetas == ["Textil"]
    assert lead.consentimiento is False and lead.nuevo is False


# --- migración 0127 --------------------------------------------------------------------------


@pytest.fixture()
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old_cwd = os.getcwd()
    os.chdir(BACKEND_ROOT)
    estados = {n: lg.disabled for n, lg in logging.root.manager.loggerDict.items()
               if isinstance(lg, logging.Logger)}
    try:
        yield cfg, db_url
    finally:
        for n, lg in logging.root.manager.loggerDict.items():
            if isinstance(lg, logging.Logger):
                lg.disabled = estados.get(n, False)
        os.chdir(old_cwd)
        get_settings.cache_clear()


def test_la_migracion_reconstruye_los_leads_ya_entrados(alembic_cfg):
    cfg, db_url = alembic_cfg
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as seed:
        seed_test_users(seed)
        seed.commit()
        autor = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    with engine.begin() as c:
        c.execute(text("ALTER TABLE web_forms DROP COLUMN is_brand_default"))
        for fid, slug, marca, idioma in (
            ("f1", "mboprinters-contacto-de", "mboprinters", "de"),
            ("f2", "mbolasers-contacto-nl", "mbolasers", "nl"),
        ):
            c.execute(text(
                "INSERT INTO web_forms (id, slug, name, brand, language, is_active, "
                "submit_success_mode, send_confirmation_email, assignment_mode, "
                "notify_owner_on_new, recaptcha_enabled, created_by_user_id, created_at, "
                "updated_at) VALUES (:i, :s, 'F', :b, :l, 1, 'modal', 0, 'rules', 1, 1, "
                ":u, '2026-10-07', '2026-10-07')"),
                {"i": fid, "s": slug, "b": marca, "l": idioma, "u": autor})
        contactos = [
            # (id, origin, origin_account_id) — los dos primeros, de formulario
            ("c1", "web_form", "web_form:mboprinters-contacto-de"),
            ("c2", "web_form", "web_form:ya-no-existe-este-slug"),
            ("c3", "web_form", None),
            ("c4", "Manual", None),
            ("c5", "woocommerce", "woocommerce:boprint"),
        ]
        # c2 y c3 se resuelven por su envío guardado.
        for sid, cid, fid in (("s1", "c2", "f2"), ("s2", "c3", "f1")):
            c.execute(text(
                "INSERT INTO form_submissions (id, form_id, contact_id, raw_payload_json, "
                "is_spam, created_at) VALUES (:i, :f, :c, '{}', 0, '2026-10-07')"),
                {"i": sid, "f": fid, "c": cid})
    with sessionmaker(bind=engine)() as s:
        for cid, origen, cuenta in contactos:
            s.add(Contact(id=cid, first_name="X", email=f"{cid}@x.es", origin=origen,
                          origin_account_id=cuenta))
        s.commit()
    command.stamp(cfg, "20261008_0126")
    command.upgrade(cfg, "20261008_0127")
    with engine.connect() as c:
        filas = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT id, origin, origin_account_id FROM contacts"))}
        assert "is_brand_default" in {r[1] for r in c.execute(text(
            "PRAGMA table_info(web_forms)"))}
    assert filas["c1"] == (origen_legible("mboprinters", "de"),
                           origen_de("mboprinters", "de"))
    assert filas["c2"] == (origen_legible("mbolasers", "nl"),
                           origen_de("mbolasers", "nl"))      # por el envío
    assert filas["c3"] == (origen_legible("mboprinters", "de"),
                           origen_de("mboprinters", "de"))    # sin cuenta, por el envío
    assert filas["c4"] == ("Manual", None)                    # lo demás, intacto
    assert filas["c5"] == ("woocommerce", "woocommerce:boprint")
    command.downgrade(cfg, "20261008_0126")


def test_el_aviso_no_rompe_si_el_formulario_no_tiene_marca(factory):
    svc = _servicio_email()
    with factory() as s:
        form = _form(s, slug="sin-marca", marca="", idioma="es")
        out = _enviar(s, form, {"nombre": "A", "email": "a@b.es"})
        assert s.get(Contact, out.contact_id).origin_account_id == "web_form:sin-marca:es"
    assert svc.sent[0].subject == "Nuevo lead desde web sin marca (español)"


def test_enviar_aviso_devuelve_a_quien_se_avisó(factory):
    _servicio_email()
    with factory() as s:
        form = _form_completo(s)
        contacto = Contact(email="a@b.de", first_name="Ana")
        s.add(contacto)
        s.commit()
        assert enviar_aviso_lead(s, form, contacto, PAYLOAD) == ["info@streamtec.es"]
