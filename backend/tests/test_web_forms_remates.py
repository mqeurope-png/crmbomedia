"""Formularios web — remates que perdían datos, desactivado, contador y
formato (ancho, alineación, estilo).

- Campo `tags`: las etiquetas elegidas llegan al contacto (caso Isabella).
- El editor no deja mapear un `tags` a un campo de texto.
- `contact.marketing_consent` mapeable desde una casilla.
- Enlaces `[texto](url)` en `label` y `help_text` en las tres vías.
- Desactivado ≠ inexistente.
- Contador de reales y bloqueados; purga de bloqueados sin payload.
- Apariencia: 50 % centrado en las tres vías, 100 % en móvil, por defecto el
  HTML de siempre, valores inválidos rechazados.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.web_forms_admin as admin_api
import app.api.web_forms_embed as embed
import app.api.web_forms_public as public_api
import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import ConsentStatus, Contact, ContactTag, Tag, User, UserRole
from app.models.web_forms import FormSubmission, WebForm, WebFormField
from app.services.web_forms import process_submission
from app.services.web_forms.apariencia import Apariencia
from app.services.web_forms.enlaces import texto_con_enlaces
from app.services.web_forms.submit import purgar_bloqueados
from tests._test_helpers import auth_headers, seed_test_users
from tests._web_forms_golden import (
    GOLDEN_API_BASE,
    GOLDEN_DIR,
    GOLDEN_FORM_ID,
    crear_formulario_golden,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


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
    monkeypatch.setattr(embed, "_api_base", lambda: GOLDEN_API_BASE)
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_id(s: Session) -> str:
    return s.scalar(select(User.id).where(User.role == UserRole.ADMIN))


def _form(s: Session, campos: list[WebFormField], **over) -> WebForm:
    form = WebForm(slug=over.pop("slug", "pimpam-contacto"), name="Contacto", brand="pimpam",
                   language="es", created_by_user_id=_admin_id(s), recaptcha_enabled=False,
                   **over)
    s.add(form)
    s.flush()
    for i, c in enumerate(campos):
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


def _tags(s: Session, *nombres: str) -> list[Tag]:
    out = []
    for n in nombres:
        t = Tag(name=n, name_normalized=n.strip().lower())
        s.add(t)
        out.append(t)
    s.commit()
    return out


# --- 1 · campo tags → etiquetas en el contacto -----------------------------------------------


def test_isabella_tres_maquinas_quedan_en_el_contacto(factory):
    """El caso real de PimPam: `maquina` con tres etiquetas marcadas (y el
    mapeo absurdo a contact.first_name que tenía) → las tres en el contacto,
    en la pestaña de etiquetas y en la columna antigua."""
    with factory() as s:
        trust, freebird, young = _tags(s, "Artisjet Trust 6090", "3000PRO Freebird",
                                       "Artisjet Young")
        opciones = [{"tag_id": t.id, "label": t.name} for t in (trust, freebird, young)]
        form = _form(s, [
            WebFormField(field_key="nombre", label="Nombre", field_type="text",
                         maps_to_contact_field="contact.first_name"),
            WebFormField(field_key="email", label="Email", field_type="email",
                         maps_to_contact_field="contact.email"),
            WebFormField(field_key="maquina", label="Máquina", field_type="tags",
                         options_json=json.dumps(opciones),
                         maps_to_contact_field="contact.first_name"),
        ])
        out = _enviar(s, form, {"nombre": "Isabella", "email": "isabella@glowbtl.mx",
                                "maquina": [trust.id, freebird.id, young.id]})
        assert out.http_status == 200, out.response
        c = s.get(Contact, out.contact_id)
        assert c.first_name == "Isabella"
        nombres = set(s.scalars(select(Tag.name).join(ContactTag, ContactTag.tag_id == Tag.id)
                                .where(ContactTag.contact_id == c.id)))
        assert {"Artisjet Trust 6090", "3000PRO Freebird", "Artisjet Young"} <= nombres
        assert set(c.tags.split(",")) >= {"Artisjet Trust 6090", "3000PRO Freebird",
                                          "Artisjet Young"}


def test_tags_por_texto_o_con_id_caducado_y_nunca_fuera_de_las_opciones(factory):
    with factory() as s:
        textil, uv, ajena = _tags(s, "Textil", "UV LED ocasión", "VIP")
        opciones = [{"tag_id": "id-que-ya-no-existe", "label": "Textil"},
                    {"value": "uv", "label": "UV LED ocasión"}]
        form = _form(s, [
            WebFormField(field_key="email", label="Email", field_type="email"),
            WebFormField(field_key="productos", label="Productos", field_type="tags",
                         options_json=json.dumps(opciones)),
        ], slug="boprint-contacto")
        out = _enviar(s, form, {"email": "a@b.es",
                                "productos[]": ["id-que-ya-no-existe", "uv", ajena.id]})
        tags = set(s.scalars(select(ContactTag.tag_id).where(
            ContactTag.contact_id == out.contact_id)))
        assert textil.id in tags and uv.id in tags
        assert ajena.id not in tags          # una etiqueta que el campo no ofrece, no


def test_el_editor_rechaza_mapear_un_campo_tags(http):
    payload = {"slug": "f-tags", "name": "F", "fields": [
        {"label": "Máquina", "field_type": "tags", "options": [],
         "maps_to_contact_field": "contact.first_name"}]}
    r = http.post("/api/admin/forms", json=payload, headers=auth_headers(http, "manager"))
    assert r.status_code == 400 and "etiquetas" in r.json()["detail"]
    payload["fields"][0]["maps_to_contact_field"] = None
    assert http.post("/api/admin/forms", json=payload,
                     headers=auth_headers(http, "manager")).status_code == 201


# --- 2 · consentimiento comercial ----------------------------------------------------------


def _form_consent(s: Session) -> WebForm:
    return _form(s, [
        WebFormField(field_key="email", label="Email", field_type="email"),
        WebFormField(field_key="comerciales", label="Acepto recibir comunicaciones comerciales",
                     field_type="checkbox", maps_to_contact_field="contact.marketing_consent"),
    ], slug="boprint-contacto")


def test_casilla_marcada_da_el_consentimiento_y_sin_marcar_no(factory):
    with factory() as s:
        form = _form_consent(s)
        si = _enviar(s, form, {"email": "si@b.es", "comerciales": "on"})
        no = _enviar(s, form, {"email": "no@b.es"})
        assert s.get(Contact, si.contact_id).marketing_consent == ConsentStatus.GRANTED
        assert s.get(Contact, no.contact_id).marketing_consent == ConsentStatus.UNKNOWN


def test_un_valor_por_defecto_no_da_el_consentimiento(factory, http):
    """Una casilla de consentimiento con «valor por defecto» lo daría aunque
    la persona no la marcara: no se aplica y el editor no lo deja guardar."""
    with factory() as s:
        form = _form(s, [
            WebFormField(field_key="email", label="Email", field_type="email"),
            WebFormField(field_key="comerciales", label="Acepto comunicaciones comerciales",
                         field_type="checkbox", default_value="on",
                         maps_to_contact_field="contact.marketing_consent"),
        ], slug="boprint-contacto")
        out = _enviar(s, form, {"email": "nadie@b.es"})
        assert s.get(Contact, out.contact_id).marketing_consent == ConsentStatus.UNKNOWN
    r = http.post("/api/admin/forms", headers=auth_headers(http, "manager"), json={
        "slug": "f-cd", "name": "F", "fields": [{
            "label": "Acepto", "field_type": "checkbox", "default_value": "on",
            "maps_to_contact_field": "contact.marketing_consent"}]})
    assert r.status_code == 400 and "valor por defecto" in r.json()["detail"]


def test_consentimiento_mapeable_solo_desde_una_casilla(http):
    mapeables = http.get("/api/admin/contact-fields-mappable",
                         headers=auth_headers(http, "manager")).json()["standard"]
    assert any(m["value"] == "contact.marketing_consent" for m in mapeables)
    r = http.post("/api/admin/forms", headers=auth_headers(http, "manager"), json={
        "slug": "f-c", "name": "F", "fields": [{"label": "Acepto", "field_type": "text",
                                                "maps_to_contact_field":
                                                    "contact.marketing_consent"}]})
    assert r.status_code == 400 and "casilla" in r.json()["detail"]


# --- 3 · enlaces -------------------------------------------------------------------------


@pytest.mark.parametrize(("texto", "esperado"), [
    ("Lee la [Política de privacidad](/politica-de-privacidad/).",
     'Lee la <a href="/politica-de-privacidad/" target="_blank" rel="noopener noreferrer">'
     "Política de privacidad</a>."),
    ("[web](https://boprint.net/x?a=1&b=2)",
     '<a href="https://boprint.net/x?a=1&amp;b=2" target="_blank" '
     'rel="noopener noreferrer">web</a>'),
    ("[x](javascript:alert(1))", "[x](javascript:alert(1))"),
    ("[x](//evil.example/)", "[x](//evil.example/)"),
    ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ('[a"b](/r/)', '<a href="/r/" target="_blank" rel="noopener noreferrer">a&quot;b</a>'),
    ("sin enlaces & cosas", "sin enlaces &amp; cosas"),
])
def test_enlaces_seguros(texto, esperado):
    assert texto_con_enlaces(texto) == esperado


def test_enlaces_en_las_tres_vias(http, factory):
    with factory() as s:
        form = _form(s, [WebFormField(
            field_key="privacidad", field_type="checkbox", is_required=True,
            label="He leído y acepto la [Política de Privacidad](/politica-de-privacidad/)",
            help_text="Más en [la web](https://boprint.net/legal) <script>x</script>")])
        fid = form.id
        fragmento = embed.build_pure_html_fragment(form, api_base=GOLDEN_API_BASE,
                                                   site_key=None)
    enlace = '<a href="/politica-de-privacidad/" target="_blank" rel="noopener noreferrer">'
    iframe = http.get(f"/forms/{fid}").text
    cfg = http.get(f"/public/forms/{fid}/config.json").json()
    for html_ in (iframe, fragmento, cfg["fields"][0]["label_html"]):
        assert enlace in html_
    for html_ in (iframe, fragmento, cfg["fields"][0]["help_html"]):
        assert '<a href="https://boprint.net/legal"' in html_
        assert "<script>x" not in html_ and "&lt;script&gt;x" in html_
    # El widget pinta label_html/help_html (y escapa si no llegan).
    js = http.get(f"/forms/embed/{fid}.js").text
    assert "f.label_html!=null?f.label_html:esc(f.label)" in js


# --- 4 · desactivado ≠ inexistente -------------------------------------------------------------


def test_desactivado_tiene_su_propio_error(http, factory):
    with factory() as s:
        form = _form(s, [WebFormField(field_key="email", label="Email", field_type="email")],
                     is_active=False)
        fid = form.id
    r = http.get(f"/public/forms/{fid}/config.json")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "form_inactive"
    assert "desactivado" in r.json()["detail"]["message"].lower()
    sub = http.post(f"/public/forms/{fid}/submit", json={"email": "a@b.es"})
    assert sub.status_code == 403 and sub.json()["detail"]["code"] == "form_inactive"
    no = http.get("/public/forms/no-existe/config.json")
    assert no.status_code == 404 and no.json()["detail"] == "Form not found"
    code = http.get(f"/api/admin/forms/{fid}/embed-code", headers=auth_headers(http, "manager"))
    assert code.json()["is_active"] is False


# --- 5 · contador y purga -----------------------------------------------------------------


def test_contador_separa_reales_y_bloqueados(http, factory):
    with factory() as s:
        form = _form(s, [WebFormField(field_key="email", label="Email", field_type="email")])
        for i in range(5):
            s.add(FormSubmission(form_id=form.id, raw_payload_json=json.dumps({"email": i})))
        for _ in range(41):
            s.add(FormSubmission(form_id=form.id, raw_payload_json="{}", is_spam=True,
                                 spam_reason="recaptcha_low_score", recaptcha_score=0))
        s.commit()
    fila = next(f for f in http.get("/api/admin/forms",
                                    headers=auth_headers(http, "manager")).json())
    assert (fila["submissions_real"], fila["submissions_blocked"]) == (5, 41)


def test_purga_los_bloqueados_sin_payload_de_mas_de_90_dias(factory):
    ahora = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    viejo = (ahora - timedelta(days=91)).replace(tzinfo=None)
    reciente = (ahora - timedelta(days=10)).replace(tzinfo=None)
    with factory() as s:
        form = _form(s, [WebFormField(field_key="email", label="Email", field_type="email")])
        filas = [
            ("{}", True, viejo),                      # se purga
            ("{}", True, reciente),                   # aún no
            ('{"email": "x@y.z"}', True, viejo),      # trae datos: se queda
            ("{}", False, viejo),                     # envío real: nunca
        ]
        for payload, spam, cuando in filas:
            s.add(FormSubmission(form_id=form.id, raw_payload_json=payload, is_spam=spam,
                                 created_at=cuando))
        s.commit()
        assert purgar_bloqueados(s, ahora=ahora) == 1
        s.commit()
        assert s.scalar(select(text("count(*)")).select_from(FormSubmission)) == 3


# --- 7/8 · apariencia ------------------------------------------------------------------------


def _form_apariencia(s: Session, **ap) -> WebForm:
    return _form(s, [WebFormField(field_key="email", label="Email", field_type="email")],
                 appearance_json=json.dumps(ap))


def test_ancho_50_centrado_en_las_tres_vias_y_100_en_movil(http, factory):
    with factory() as s:
        form = _form_apariencia(s, width_pct=50, align="center", submit_text="Pide información")
        fid = form.id
        fragmento = embed.build_pure_html_fragment(form, api_base=GOLDEN_API_BASE,
                                                   site_key=None)
    iframe = http.get(f"/forms/{fid}").text
    cfg = http.get(f"/public/forms/{fid}/config.json").json()
    for css_, selector in ((iframe, "#bh-form.bh-form"),
                           (cfg["style_css"], f'[data-bohub-form="{fid}"] form.bh-form'),
                           (fragmento, f"#bh-form-{fid}")):
        assert f"{selector}{{width:50%;max-width:none;margin-left:auto;margin-right:auto}}" \
            in css_
        assert f"@media (max-width:600px){{{selector}{{width:100%;max-width:100%}}}}" in css_
    assert ">Pide información</button>" in iframe and ">Pide información</button>" in fragmento
    assert cfg["submit_text"] == "Pide información"


def test_estilo_por_defecto_deja_el_html_de_siempre(http, factory):
    """La «foto» de tests/fixtures/web_forms_golden se sacó con el código de
    antes del PR. Un formulario sin apariencia genera lo mismo (el iframe
    solo añade el script invisible que avisa de su altura)."""
    golden = BACKEND_ROOT / GOLDEN_DIR
    with factory() as s:
        form = crear_formulario_golden(s)
        fragmento = embed.build_pure_html_fragment(form, api_base=GOLDEN_API_BASE,
                                                   site_key=None)
    assert fragmento == (golden / "fragmento.html").read_text()
    iframe = http.get(f"/forms/{GOLDEN_FORM_ID}").text
    assert iframe.replace(embed.iframe_resize_js(GOLDEN_FORM_ID), "") == \
        (golden / "iframe.html").read_text()
    cfg = http.get(f"/public/forms/{GOLDEN_FORM_ID}/config.json").json()
    antes = json.loads((golden / "config.json").read_text())
    nuevas = {"submit_text", "style_css"}
    assert {k: v for k, v in cfg.items() if k not in nuevas | {"fields"}} == \
        {k: v for k, v in antes.items() if k != "fields"}
    assert cfg["style_css"] == "" and cfg["submit_text"] == "Enviar"
    for nuevo, viejo in zip(cfg["fields"], antes["fields"], strict=True):
        assert {k: v for k, v in nuevo.items() if k not in {"label_html", "help_html"}} == viejo


@pytest.mark.parametrize("apariencia", [
    {"primary_color": "red"},
    {"primary_color": "#12345g"},
    {"text_color": "#fff;}body{display:none"},
    {"width_pct": 10},
    {"width_pct": 120},
    {"max_width_px": 50},
    {"align": "justify"},
    {"theme": "rosa"},
    {"font": "Comic Sans"},
    {"inventado": 1},
])
def test_valores_invalidos_rechazados(http, apariencia):
    r = http.post("/api/admin/forms", headers=auth_headers(http, "manager"), json={
        "slug": "f-ap", "name": "F", "appearance": apariencia,
        "fields": [{"label": "Email", "field_type": "email"}]})
    assert r.status_code == 422


def test_apariencia_se_guarda_y_la_vista_previa_la_pinta(http):
    h = auth_headers(http, "manager")
    r = http.post("/api/admin/forms", headers=h, json={
        "slug": "f-ap", "name": "F", "fields": [{"label": "Email", "field_type": "email"}],
        "appearance": {"width_pct": 60, "align": "right", "primary_color": "#E30613",
                       "theme": "dark", "radius_px": 0, "font": "serif"}})
    assert r.status_code == 201, r.text
    ap = r.json()["appearance"]
    assert ap["primary_color"] == "#e30613" and ap["width_pct"] == 60
    # PATCH sin `appearance` no la borra.
    det = r.json()
    det.pop("id")
    det.pop("created_by_user_id")
    det.pop("created_at")
    det.pop("appearance")
    assert http.patch(f"/api/admin/forms/{r.json()['id']}", headers=h,
                      json=det).json()["appearance"]["width_pct"] == 60
    prev = http.post("/api/admin/forms/preview", headers=h, json={
        "name": "F", "fields": [{"label": "Email", "field_type": "email"}],
        "appearance": {"width_pct": 50, "align": "center", "primary_color": "#e30613"}})
    assert prev.status_code == 200
    assert "width:50%" in prev.json()["html"] and "background:#e30613" in prev.json()["html"]


def test_apariencia_vacia_es_la_de_siempre():
    assert Apariencia().es_por_defecto()
    assert Apariencia(primary_color="").es_por_defecto()


# --- iframe que crece con el contenido ---------------------------------------------------------


def test_iframe_lleva_los_enlaces_relativos_a_la_web_que_lo_inserta(http, factory):
    """El iframe se sirve desde BoHub: sin esto, «/politica-de-privacidad/»
    abriría la página de BoHub y no la de la marca."""
    with factory() as s:
        fid = _form(s, [WebFormField(field_key="email", label="Email", field_type="email")]).id
    html_ = http.get(f"/forms/{fid}").text
    assert 'querySelectorAll(\'a[href^="/"]\')' in html_
    assert "document.referrer" in html_ and "ancestorOrigins" in html_


def test_el_widget_escapa_comillas_en_los_atributos():
    assert 'replace(/"/g,"&quot;")' in embed._WIDGET_BOOT_JS


def test_iframe_avisa_de_su_altura_y_el_codigo_la_aplica(http, factory):
    with factory() as s:
        fid = _form(s, [WebFormField(field_key="email", label="Email", field_type="email")]).id
    assert "bohub-form-height" in http.get(f"/forms/{fid}").text
    code = http.get(f"/api/admin/forms/{fid}/embed-code",
                    headers=auth_headers(http, "manager")).json()
    assert f'id="bohub-form-{fid}"' in code["iframe_snippet"]
    assert "bohub-form-height" in code["iframe_snippet"]
    assert json.dumps(GOLDEN_API_BASE) in code["iframe_snippet"] or \
        "e.origin!==" in code["iframe_snippet"]


# --- migración 0126 ---------------------------------------------------------------------------


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
    # alembic/env.py (fileConfig) desactiva los loggers existentes: se restauran.
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


def test_migracion_0126_arregla_mapeos_y_textos(alembic_cfg):
    cfg, db_url = alembic_cfg
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as seed:
        seed_test_users(seed)
        seed.commit()
        autor = _admin_id(seed)
    with engine.begin() as c:
        c.execute(text("ALTER TABLE web_forms DROP COLUMN appearance_json"))
        for fid, slug in (("f1", "boprint-contacto"), ("f2", "pimpam-contacto")):
            c.execute(text(
                "INSERT INTO web_forms (id, slug, name, language, is_active, "
                "submit_success_mode, send_confirmation_email, assignment_mode, "
                "notify_owner_on_new, recaptcha_enabled, created_by_user_id, created_at, "
                "updated_at) VALUES (:i, :s, 'F', 'es', 1, 'modal', 0, 'rules', 1, 1, :u, "
                "'2026-10-07', '2026-10-07')"), {"i": fid, "s": slug, "u": autor})
        campos = [
            ("c1", "f2", "maquina", "Máquina", "tags", None, "contact.first_name"),
            ("c2", "f1", "comerciales", "Acepto recibir comunicaciones comerciales",
             "checkbox", None, None),
            ("c3", "f1", "privacidad", "He leído y acepto la Política de Privacidad",
             "checkbox", "Ver https://boprint.net/politica-privacidad/", None),
            ("c4", "f1", "nombre", "Nombre", "text", None, "contact.first_name"),
        ]
        for cid, fid, key, label, tipo, ayuda, destino in campos:
            c.execute(text(
                "INSERT INTO web_form_fields (id, form_id, field_key, label, field_type, "
                "help_text, is_required, is_hidden, position, maps_to_contact_field) VALUES "
                "(:i, :f, :k, :l, :t, :h, 0, 0, 0, :d)"),
                {"i": cid, "f": fid, "k": key, "l": label, "t": tipo, "h": ayuda, "d": destino})
    command.stamp(cfg, "20261007_0125")
    command.upgrade(cfg, "20261008_0126")
    with engine.connect() as c:
        filas = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT id, maps_to_contact_field, help_text FROM web_form_fields"))}
        assert "appearance_json" in {r[1] for r in c.execute(text(
            "PRAGMA table_info(web_forms)"))}
    assert filas["c1"][0] is None                                  # tags → sin mapeo
    assert filas["c2"][0] == "contact.marketing_consent"
    assert filas["c3"][1] == ("Ver [Política de privacidad]"
                              "(https://boprint.net/politica-privacidad/)")
    assert filas["c4"][0] == "contact.first_name"                  # lo demás, igual
    command.downgrade(cfg, "20261007_0125")


def test_texto_de_privacidad_por_defecto():
    import importlib.util

    ruta = BACKEND_ROOT / "alembic/versions/20261008_0126_formularios_apariencia.py"
    spec = importlib.util.spec_from_file_location("m0126", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.help_privacidad(None) == \
        "Consulta nuestra [Política de privacidad](/politica-de-privacidad/)."
    assert mod.help_privacidad("Ya [enlazado](/x/)") is None


# Que el import del módulo público no se quede sin usar (lo monta app.main).
assert public_api.router is not None and admin_api.router is not None
