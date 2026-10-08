"""Formularios web con 25 formularios en ocho webs y seis idiomas.

- 14: el origen del lead dice de qué web y en qué idioma viene, se puede
  filtrar y segmentar por cualquiera de los dos, y los leads ya entrados se
  reconstruyen (migración 0127).
- 15: un solo código de inserción por web (`/forms/embed/<sitio>.js`, donde
  el sitio es la clave del slug y NO la marca: `artisjet` está en dos webs),
  que resuelve el idioma de la página y tiene respaldo por web.
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
from app.services.web_forms.seleccion import formulario_de_sitio
from app.services.web_forms.sitios import (
    etiqueta_de_origen,
    origen_de,
    origen_legible,
)
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


def _form(s: Session, *, slug: str, idioma: str, marca: str = "", campos=None,
          **over) -> WebForm:
    autor = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    form = WebForm(slug=slug, name=f"Contacto {slug}", brand=marca,
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
        form = _form(s, slug="mboprinters-contacto-de", idioma="de")
        out = _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de"})
        c = s.get(Contact, out.contact_id)
        assert c.origin == "Formulario web · mboprinters.com (alemán)"
        assert c.origin_account_id == "web_form:mboprinters:de"
    # Y se lee igual desde el origen guardado (ficha, avisos).
    assert etiqueta_de_origen("web_form:mboprinters:de") == "mboprinters.com (alemán)"
    assert etiqueta_de_origen("web_form:boprint-contacto") is None   # formato antiguo


def test_una_web_desconocida_no_inventa_dominio(factory):
    with factory() as s:
        form = _form(s, slug="webnueva-contacto-es", idioma="es")
        out = _enviar(s, form, {"nombre": "A", "email": "a@b.es"})
        c = s.get(Contact, out.contact_id)
        assert c.origin == "Formulario web · webnueva (español)"
        assert c.origin_account_id == "web_form:webnueva:es"


@pytest.mark.parametrize(("campo", "valor", "encuentra"), [
    ("lead_web", "mboprinters", True),
    ("lead_web", "mbolasers", False),
    ("language", "de", True),
    ("language", "es", False),
])
def test_se_filtra_por_web_y_por_idioma(factory, campo, valor, encuentra):
    """«Los leads de mboprinters» y «los leads en alemán», sin tirar de la
    etiqueta `form:<slug>`."""
    with factory() as s:
        form = _form(s, slug="mboprinters-contacto-de", idioma="de")
        out = _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de"})
        reglas = {"operator": "AND", "children": [
            {"type": "rule", "field": campo, "comparator": "eq", "value": valor}]}
        ids = set(s.scalars(select(Contact.id).where(seg_engine.build_filter(reglas))))
        assert (out.contact_id in ids) is encuentra
        # El mismo criterio en memoria (reglas de asignación, workflows).
        contacto = s.get(Contact, out.contact_id)
        assert seg_engine.evaluate_contact_against_rules(contacto, reglas) is encuentra


@pytest.mark.parametrize("comparador", ["neq", "not_in"])
def test_quien_no_viene_de_formulario_cuenta_como_otra_web(factory, comparador):
    """«La web NO es mboprinters» incluye a quien no entró por formulario, y
    tiene que decir lo mismo en SQL que en memoria: el camino en memoria es
    el de las reglas de asignación."""
    with factory() as s:
        suelto = Contact(first_name="Ana", email="ana@woo.es",
                         origin="WooCommerce", origin_account_id="woocommerce:boprint")
        sin_cuenta = Contact(first_name="Luis", email="luis@manual.es",
                             origin="Manual")
        s.add_all([suelto, sin_cuenta])
        s.commit()
        valor = "mboprinters" if comparador == "neq" else ["mboprinters"]
        reglas = {"operator": "AND", "children": [
            {"type": "rule", "field": "lead_web",
             "comparator": comparador, "value": valor}]}
        ids = set(s.scalars(select(Contact.id).where(seg_engine.build_filter(reglas))))
        for c in (suelto, sin_cuenta):
            assert c.id in ids
            assert seg_engine.evaluate_contact_against_rules(c, reglas) is True


@pytest.mark.parametrize(("slug", "web"), [
    ("artisjet-es-contacto-de", "artisjet-spain.es"),
    ("artisjet-eu-contacto-nl", "artisjet-printers.eu"),
    ("boprint-contacto", "boprint.net"),
    ("fluxlasers-contacto-es", "fluxlasers.es"),
    ("mboprinters-contacto-de", "mboprinters.com"),
    ("mbolasers-contacto-fr", "mbolasers.com"),
    ("mqeurope-contacto-en", "www.mqeurope.com"),
    ("pimpam-contacto-es", "pimpam-vending.com"),
])
def test_las_ocho_webs(slug, web):
    from app.services.web_forms.sitios import web_de_formulario

    assert web_de_formulario(slug) == web


def test_los_dos_campos_se_ofrecen_con_su_texto():
    web = FIELD_SPECS["lead_web"]
    assert web.enum_labels["mboprinters"] == "mboprinters.com"
    assert web.enum_labels["mqeurope"] == "www.mqeurope.com"
    assert FIELD_SPECS["language"].enum_labels["de"] == "alemán"
    assert "nl" in FIELD_SPECS["language"].enum_values


def test_la_marca_no_decide_la_web_dos_webs_con_la_misma_marca(factory):
    """`artisjet` es la marca de DOS webs: la web sale del slug, nunca de la
    marca, así que sus leads no se mezclan."""
    with factory() as s:
        es = _form(s, slug="artisjet-es-contacto-es", marca="artisjet", idioma="es")
        eu = _form(s, slug="artisjet-eu-contacto-de", marca="artisjet", idioma="de")
        a = _enviar(s, es, {"nombre": "A", "email": "a@es.com"})
        b = _enviar(s, eu, {"nombre": "B", "email": "b@eu.com"})
        ca, cb = s.get(Contact, a.contact_id), s.get(Contact, b.contact_id)
    assert ca.origin == "Formulario web · artisjet-spain.es (español)"
    assert cb.origin == "Formulario web · artisjet-printers.eu (alemán)"
    assert ca.origin_account_id == "web_form:artisjet-es:es"
    assert cb.origin_account_id == "web_form:artisjet-eu:de"


def test_el_idioma_es_un_dato_del_contacto_y_de_su_empresa(factory):
    """El idioma no se queda dentro del texto del origen: va en
    `contacts.language` (se cruza con cualquier web) y en la empresa, que es
    de donde lo lee la cascada de idioma del ERP al escribirle."""
    with factory() as s:
        form = _form(s, slug="mbolasers-contacto-nl", idioma="nl", campos=[
            WebFormField(field_key="email", label="Email", field_type="email",
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="empresa", label="Empresa", field_type="text",
                         maps_to_contact_field="contact.company_id"),
        ])
        out = _enviar(s, form, {"email": "jan@vandijk.nl", "empresa": "Van Dijk BV"})
        c = s.get(Contact, out.contact_id)
        assert c.language == "nl"
        assert c.company is not None and c.company.language == "nl"


def test_un_idioma_ya_puesto_no_se_pisa(factory):
    from app.models.crm import Company

    with factory() as s:
        empresa = Company(name="Van Dijk BV", language="de")
        contacto = Contact(first_name="Jan", email="jan@vandijk.nl", language="de",
                           company_id=None)
        s.add_all([empresa, contacto])
        s.commit()
        contacto.company_id = empresa.id
        s.commit()
        form = _form(s, slug="mbolasers-contacto-nl", idioma="nl")
        _enviar(s, form, {"nombre": "Jan", "email": "jan@vandijk.nl"})
        s.refresh(contacto)
        s.refresh(empresa)
        assert (contacto.language, empresa.language) == ("de", "de")


def test_el_acuse_al_lead_va_en_su_idioma(factory):
    svc = _servicio_email()
    with factory() as s:
        form = _form(s, slug="mboprinters-contacto-de", marca="MBO Printers",
                     idioma="de", send_confirmation_email=True)
        _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de"})
    acuse = next(e for e in svc.sent if e.to_email == "hans@muster.de")
    assert acuse.subject == "Vielen Dank für Ihre Anfrage bei MBO Printers"
    assert acuse.text_body.startswith("Vielen Dank!")
    assert "¡Gracias!" not in acuse.text_body


# --- 15 · un solo código por web -------------------------------------------------------------


def test_el_embed_por_web_sirve_el_idioma_de_la_pagina(http, factory):
    with factory() as s:
        _form(s, slug="mbolasers-contacto-es", idioma="es")
        _form(s, slug="mbolasers-contacto-nl", idioma="nl")
        _form(s, slug="mbolasers-contacto-en", idioma="en",
              is_site_default=True)
    js = http.get("/forms/embed/mbolasers.js")
    assert js.status_code == 200
    # El propio widget resuelve el idioma y pide el config de la web.
    assert '"mbolasers"' in js.text and "/public/forms/by-site/" in js.text
    assert "documentElement.getAttribute(\"lang\")" in js.text

    def cfg(lang):
        return http.get(f"/public/forms/by-site/mbolasers/config.json?lang={lang}").json()

    assert cfg("nl")["slug"] == "mbolasers-contacto-nl"
    assert cfg("nl-BE")["slug"] == "mbolasers-contacto-nl"       # nl-BE → nl
    assert cfg("fr")["slug"] == "mbolasers-contacto-en"          # sin francés → el de respaldo
    # Sin idioma NO es «página en castellano»: también se lleva el respaldo.
    assert cfg("")["slug"] == "mbolasers-contacto-en"
    assert cfg("es")["slug"] == "mbolasers-contacto-es"          # castellano de verdad


def test_sin_respaldo_marcado_cae_al_castellano_y_luego_al_ingles(factory):
    with factory() as s:
        _form(s, slug="fluxlasers-contacto-es", idioma="es")
        _form(s, slug="fluxlasers-contacto-de", idioma="de")
        assert formulario_de_sitio(s, "fluxlasers", "fr").slug == "fluxlasers-contacto-es"
        assert formulario_de_sitio(s, "FLUXLASERS", "de").slug == "fluxlasers-contacto-de"
        assert formulario_de_sitio(s, "no-existe", "es") is None


def test_una_pagina_sin_lang_se_lleva_el_respaldo_no_el_castellano(factory):
    """Una web europea cuyas plantillas no pongan `lang` debe servir el
    formulario que el operador marcó de respaldo, no el castellano por el
    hecho de no saber el idioma."""
    with factory() as s:
        _form(s, slug="artisjet-eu-contacto-es", idioma="es")
        _form(s, slug="artisjet-eu-contacto-de", idioma="de")
        _form(s, slug="artisjet-eu-contacto-en", idioma="en", is_site_default=True)
        elegido = lambda lang: formulario_de_sitio(s, "artisjet-eu", lang).slug  # noqa: E731
        assert elegido("de") == "artisjet-eu-contacto-de"      # el idioma pedido
        assert elegido("es") == "artisjet-eu-contacto-es"      # castellano de verdad
        assert elegido("") == "artisjet-eu-contacto-en"        # sin lang → respaldo
        assert elegido("it") == "artisjet-eu-contacto-en"      # idioma que no tenemos
        assert elegido(None) == "artisjet-eu-contacto-en"


def test_una_web_sin_formularios_activos_lo_dice_en_la_consola(http, factory):
    with factory() as s:
        _form(s, slug="pimpam-contacto-es", idioma="es", is_active=False)
    js = http.get("/forms/embed/pimpam-vending.js")
    assert js.status_code == 200
    assert "console.warn" in js.text and "site_without_forms" in js.text
    r = http.get("/public/forms/by-site/pimpam-vending/config.json?lang=es")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "site_without_forms"


def test_si_no_llega_la_configuracion_el_aviso_va_en_el_idioma_de_la_pagina(http, factory):
    """«No se pudo cargar el formulario» se dice antes de saber qué
    formulario es, así que sus traducciones viajan en el propio JS."""
    with factory() as s:
        _form(s, slug="mboprinters-contacto-de", idioma="de")
    js = http.get("/forms/embed/mboprinters.js").text
    assert "Das Formular konnte nicht geladen werden." in js
    assert "Het formulier kon niet worden geladen." in js
    assert "NO_CARGADO[idiomaPagina()]" in js
    # Y ya no queda el castellano fijo como única salida.
    assert 'esc(d.loading_error||"No se pudo cargar' not in js


def test_el_embed_por_id_sigue_funcionando(http, factory):
    """Es lo que está pegado hoy en las webs: no se toca."""
    with factory() as s:
        fid = _form(s, slug="boprint-contacto", idioma="es").id
    js = http.get(f"/forms/embed/{fid}.js")
    assert js.status_code == 200
    assert f'CLAVE="{fid}"' in js.text.replace(" ", "")
    assert "SITIO=null" in js.text.replace(" ", "")      # no es modo web
    assert http.get(f"/public/forms/{fid}/config.json").json()["slug"] == "boprint-contacto"


# --- 16 · el formulario, en su idioma ---------------------------------------------------------


@pytest.mark.parametrize(("idioma", "boton"), [
    ("es", "Enviar"), ("en", "Send"), ("fr", "Envoyer"),
    ("de", "Senden"), ("nl", "Versturen"), ("pt", "Enviar"),
])
def test_el_boton_va_en_el_idioma_del_formulario(http, factory, idioma, boton):
    with factory() as s:
        form = _form(s, slug=f"mbolasers-contacto-{idioma}", idioma=idioma)
        fid = form.id
        fragmento = embed.build_pure_html_fragment(form, api_base=API_BASE, site_key=None)
    iframe = http.get(f"/forms/{fid}").text
    cfg = http.get(f"/public/forms/{fid}/config.json").json()
    assert cfg["submit_text"] == boton
    assert f">{boton}</button>" in iframe
    assert f">{boton}</button>" in fragmento


def test_los_mensajes_del_formulario_tambien(http, factory):
    with factory() as s:
        form = _form(s, slug="mboprinters-contacto-de", idioma="de")
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
        form = _form(s, slug="mbolasers-contacto-fr", idioma="fr",
                     appearance_json=json.dumps({"submit_text": "Demander un devis"}))
        fid = form.id
    assert http.get(f"/public/forms/{fid}/config.json").json()["submit_text"] == \
        "Demander un devis"
    assert texto_submit("fr", None) == "Envoyer"
    assert textos("xx")["submit"] == "Enviar"       # idioma desconocido → castellano


# --- 17 · el código que se copia -------------------------------------------------------------


def test_el_codigo_a_copiar_lleva_el_div_y_la_variante_por_marca(http, factory):
    with factory() as s:
        fid = _form(s, slug="mbolasers-contacto-es", idioma="es").id
    code = http.get(f"/api/admin/forms/{fid}/embed-code",
                    headers=auth_headers(http, "manager")).json()
    assert f'<div data-bohub-form="{fid}"></div>' in code["script_snippet"]
    assert code["script_snippet"].count("\n") == 1          # las DOS líneas
    assert code["site"] == "mbolasers" and code["site_web"] == "mbolasers.com"
    assert '/forms/embed/mbolasers.js' in code["site_snippet"]
    assert '<div data-bohub-form="mbolasers"></div>' in code["site_snippet"]


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
    return _form(s, slug="mboprinters-contacto-de", idioma="de",
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
    from app.services.web_forms import aviso as aviso_mod

    class Roto:
        def send_notification(self, **kw):
            raise RuntimeError("SMTP caído")

    monkeypatch.setattr(email_mod, "get_email_service", lambda: Roto())
    # Un test anterior puede haber ejecutado `env.py` de Alembic, cuyo
    # `fileConfig()` apaga los loggers que ya existían; entonces `caplog` no
    # vería nada. Lo que se comprueba aquí es que el aviso avisa, no la
    # configuración de logging que dejó otro test.
    monkeypatch.setattr(aviso_mod.logger, "disabled", False)
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


def test_una_web_puede_tener_su_propia_plantilla(factory, tmp_path, monkeypatch):
    """`lead_<sitio>.html` se usa sin tocar código (logotipo, firma)."""
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
    # Los contactos se siembran con el esquema completo y DESPUÉS se quita
    # lo que añade la 0127, para simular el estado anterior.
    contactos = [
        # (id, origin, origin_account_id) — los tres primeros, de formulario
        ("c1", "web_form", "web_form:mboprinters-contacto-de"),
        ("c2", "web_form", "web_form:ya-no-existe-este-slug"),
        ("c3", "web_form", None),
        ("c4", "Manual", None),
        ("c5", "woocommerce", "woocommerce:boprint"),
    ]
    with sessionmaker(bind=engine)() as s:
        for cid, origen, cuenta in contactos:
            s.add(Contact(id=cid, first_name="X", email=f"{cid}@x.es", origin=origen,
                          origin_account_id=cuenta))
        s.commit()
    with engine.begin() as c:
        c.execute(text("ALTER TABLE web_forms DROP COLUMN is_site_default"))
        c.execute(text("DROP INDEX IF EXISTS ix_contacts_language"))
        c.execute(text("ALTER TABLE contacts DROP COLUMN language"))
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
        # c2 y c3 se resuelven por su envío guardado.
        for sid, cid, fid in (("s1", "c2", "f2"), ("s2", "c3", "f1")):
            c.execute(text(
                "INSERT INTO form_submissions (id, form_id, contact_id, raw_payload_json, "
                "is_spam, created_at) VALUES (:i, :f, :c, '{}', 0, '2026-10-07')"),
                {"i": sid, "f": fid, "c": cid})
    command.stamp(cfg, "20261008_0126")
    command.upgrade(cfg, "20261008_0127")
    with engine.connect() as c:
        filas = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT id, origin, origin_account_id FROM contacts"))}
        assert "is_site_default" in {r[1] for r in c.execute(text(
            "PRAGMA table_info(web_forms)"))}
        idiomas = {r[0]: r[1] for r in c.execute(text(
            "SELECT id, language FROM contacts"))}
    slug_de = "mboprinters-contacto-de"
    slug_nl = "mbolasers-contacto-nl"
    assert filas["c1"] == (origen_legible(slug_de, "de"), origen_de(slug_de, "de"))
    assert filas["c2"] == (origen_legible(slug_nl, "nl"),
                           origen_de(slug_nl, "nl"))          # por el envío
    assert filas["c3"] == (origen_legible(slug_de, "de"),
                           origen_de(slug_de, "de"))          # sin cuenta, por el envío
    # El idioma queda como dato propio del contacto.
    assert (idiomas["c1"], idiomas["c2"], idiomas["c3"]) == ("de", "nl", "de")
    assert idiomas["c4"] is None and idiomas["c5"] is None
    assert filas["c4"] == ("Manual", None)                    # lo demás, intacto
    assert filas["c5"] == ("woocommerce", "woocommerce:boprint")

    # Y se puede volver a subir: el origen ya está reconstruido, pero la
    # columna `language` se va con el downgrade y hay que rellenarla otra vez
    # (en MySQL el DDL hace commit, así que una migración cortada a medias
    # deja justo este estado).
    command.downgrade(cfg, "20261008_0126")
    command.upgrade(cfg, "20261008_0127")
    with engine.connect() as c:
        idiomas = {r[0]: r[1] for r in c.execute(text(
            "SELECT id, language FROM contacts"))}
        filas = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT id, origin, origin_account_id FROM contacts"))}
    assert (idiomas["c1"], idiomas["c2"], idiomas["c3"]) == ("de", "nl", "de")
    assert idiomas["c4"] is None and idiomas["c5"] is None
    assert filas["c1"] == (origen_legible(slug_de, "de"), origen_de(slug_de, "de"))
    command.downgrade(cfg, "20261008_0126")


def test_el_aviso_no_rompe_si_el_slug_no_sigue_el_convenio(factory):
    svc = _servicio_email()
    with factory() as s:
        form = _form(s, slug="sin-convenio", idioma="es")
        out = _enviar(s, form, {"nombre": "A", "email": "a@b.es"})
        assert s.get(Contact, out.contact_id).origin_account_id == "web_form:sin-convenio:es"
    assert svc.sent[0].subject == "Nuevo lead desde sin-convenio (español)"


def test_enviar_aviso_devuelve_a_quien_se_avisó(factory):
    _servicio_email()
    with factory() as s:
        form = _form_completo(s)
        contacto = Contact(email="a@b.de", first_name="Ana")
        s.add(contacto)
        s.commit()
        assert enviar_aviso_lead(s, form, contacto, PAYLOAD) == ["info@streamtec.es"]
