"""Respuesta a leads · PR B — el proveedor de IA, la configuración, el modo en
seco, la lista corregible y el workflow de la Fase 1."""
from __future__ import annotations

import json
import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.email_templates.models import EmailTemplate
from app.main import app
from app.models.crm import (
    AuditLog,
    Contact,
    Note,
    Pipeline,
    PipelineStage,
    User,
)
from app.models.leads import LeadClassification
from app.models.web_forms import FormSubmission, WebForm, WebFormField
from app.models.workflows import WorkflowEdge, WorkflowStep
from app.services import llm
from app.services.leads import config as config_leads
from app.services.leads import en_seco, registro
from app.services.leads import workflow as workflow_leads
from app.services.leads.clasificador import (
    ClasificadorPalabrasClave,
    EntradaLead,
    clasificar_lead,
    proveedor_por_defecto,
)
from app.services.leads.proveedor_anthropic import ClasificadorAnthropic
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
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def con_clave(monkeypatch):
    """Como si hubiera `ANTHROPIC_API_KEY`: ninguna llamada real sale (se
    parchea `_invoke_claude`)."""
    from app.core.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


ALEMAN = ("Hallo, wir möchten Transferfolie für unseren UV-Drucker bestellen. "
          "Welche Folie haben Sie? Danke und Grüße")


def _admin_id(session: Session) -> str:
    return session.scalar(select(User.id).where(User.email == "admin@example.com"))


# --- el proveedor de IA ---------------------------------------------------------


def test_el_proveedor_de_ia_parsea_la_respuesta(monkeypatch, con_clave) -> None:
    prompts: list[tuple[str, str]] = []

    def _falso(*, api_key, model, system_prompt, user_prompt):
        prompts.append((system_prompt, user_prompt))
        return json.dumps({"idioma": "fr", "interes": "uv_gran_formato", "es_spam": False,
                           "confianza": 0.83, "motivo": "Pregunta por paneles de 2,5 m"})

    monkeypatch.setattr(llm, "_invoke_claude", _falso)
    assert isinstance(proveedor_por_defecto(), ClasificadorAnthropic)
    entrada = EntradaLead(texto="Bonjour, des panneaux de 2,5 m", fuente="agilecrm",
                          referencia="n1", email="x@y.fr", cuenta_agile="acc", pais="FR")
    out = clasificar_lead(entrada, ClasificadorAnthropic())
    assert (out.idioma, out.interes, out.es_spam) == ("fr", "uv_gran_formato", False)
    assert out.confianza == 0.83 and out.proveedor == "anthropic" and out.modelo
    assert (out.idioma_fuente, out.interes_fuente) == ("ia", "ia")
    # El contexto va en el prompt; la clave, no.
    assert "Dominio del correo: y.fr" in prompts[0][1] and "clave-de-prueba" not in prompts[0][1]


def test_el_proveedor_de_ia_cae_a_palabras_clave_si_falla(monkeypatch, con_clave, caplog) -> None:
    def _roto(**kwargs):
        raise llm.LLMUpstreamError("Provider returned non-text content (blocks: thinking)")

    monkeypatch.setattr(llm, "_invoke_claude", _roto)
    with caplog.at_level(logging.WARNING, logger="app.services.leads.proveedor_anthropic"):
        out = ClasificadorAnthropic().clasificar(EntradaLead(texto=ALEMAN, fuente="agilecrm",
                                                             referencia="n1"))
    assert (out.idioma, out.interes, out.proveedor) == ("de", "consumibles", "palabras_clave")
    # El motivo y el log llevan el MENSAJE del error, no solo la clase.
    assert ("respaldo por palabras clave: IA no disponible: LLMUpstreamError: Provider returned "
            "non-text content (blocks: thinking)") in out.motivo
    assert any("non-text content (blocks: thinking)" in r.getMessage() for r in caplog.records)
    # Valores fuera de catálogo: se normalizan, no revientan.
    monkeypatch.setattr(llm, "_invoke_claude", lambda **k: json.dumps(
        {"idioma": "zh-Hant", "interes": "cohetes", "es_spam": "no", "confianza": 7}))
    out = ClasificadorAnthropic().clasificar(EntradaLead(texto="ok", fuente="agilecrm",
                                                         referencia="n2"))
    assert (out.idioma, out.interes, out.confianza) == (None, "otro", 1.0)
    # «no» / «false» como texto NO es spam (un texto no vacío no es «sí»).
    assert out.es_spam is False
    monkeypatch.setattr(llm, "_invoke_claude", lambda **k: json.dumps(
        {"idioma": "en", "interes": "otro", "es_spam": "false", "confianza": 0.9}))
    assert ClasificadorAnthropic().clasificar(EntradaLead(
        texto="ok", fuente="agilecrm", referencia="n3")).es_spam is False
    monkeypatch.setattr(llm, "_invoke_claude", lambda **k: json.dumps(
        {"idioma": "en", "interes": "otro", "es_spam": "true", "confianza": 0.9}))
    assert ClasificadorAnthropic().clasificar(EntradaLead(
        texto="ok", fuente="agilecrm", referencia="n4")).es_spam is True


def test_sin_clave_el_proveedor_por_defecto_es_palabras_clave() -> None:
    # `tests/conftest.py` vacía ANTHROPIC_API_KEY: la suite nunca llama a Anthropic.
    assert isinstance(proveedor_por_defecto(), ClasificadorPalabrasClave)


def test_la_api_de_leads_exige_la_capacidad_de_configuracion(http) -> None:
    cab = auth_headers(http, "user")
    assert http.get("/api/erp/leads/clasificaciones", headers=cab).status_code == 403
    assert http.post("/api/erp/leads/clasificaciones/x/corregir", headers=cab,
                     json={"es_spam": True}).status_code == 403
    assert http.post("/api/erp/leads/en-seco", headers=cab, json={"dias": 15}).status_code == 403
    assert http.post("/api/erp/leads/workflow", headers=cab).status_code == 403
    assert http.get("/api/erp/leads/workflow", headers=cab).status_code == 403


# --- la configuración --------------------------------------------------------------


def test_validar_la_configuracion_funde_y_rechaza() -> None:
    base = config_leads.validar({"activo": True, "tope_diario": 5}, None)
    assert base["activo"] is True and base["tope_diario"] == 5
    assert base["umbral_confianza"] == config_leads.UMBRAL_DEFECTO
    fundida = config_leads.validar(
        {"mapa": {"vending:es": "tpl-1"}, "remitentes": {"por_web": {"pimpam": "hola@pim.com"}}},
        base, plantillas_validas={"tpl-1"},
    )
    assert fundida["tope_diario"] == 5 and fundida["mapa"] == {"vending:es": "tpl-1"}
    assert fundida["remitentes"]["por_web"] == {"pimpam": "hola@pim.com"}
    for malo in (
        {"activo": "sí"}, {"tope_diario": 0}, {"umbral_confianza": 2},
        {"ventana": {"start": "18:00", "end": "09:00"}},
        {"mapa": {"cohetes:es": "x"}}, {"mapa": {"vending:es": "no-existe"}},
        {"remitentes": {"por_web": {"pimpam": "no es un correo"}}},
        {"remitentes": {"por_cuenta_agile": {"acc": "web-desconocida"}}},
    ):
        with pytest.raises(ValueError):
            config_leads.validar(malo, base, plantillas_validas={"tpl-1"})
    con_cuenta = config_leads.validar(
        {"remitentes": {"por_cuenta_agile": {"acc": "artisjet-es"}}}, base)
    assert con_cuenta["remitentes"]["por_cuenta_agile"] == {"acc": "artisjet-es"}


def test_la_configuracion_va_en_los_ajustes_del_erp(http, session_factory) -> None:
    with session_factory() as s:
        s.add(EmailTemplate(name="Lead · Vending (ES)", subject="x", body_html="<p>x</p>"))
        s.commit()
    r = http.get("/api/erp/settings", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lead_response"]["activo"] is False
    catalogo = body["lead_response_catalogo"]
    assert [i["id"] for i in catalogo["intereses"]][:2] == ["uv_pequeno_mediano", "uv_gran_formato"]
    assert catalogo["mapa_por_nombre"]["vending:es"] == catalogo["plantillas"][0]["id"]
    assert catalogo["mapa_por_nombre"]["vending:de"] is None
    assert any(w["clave"] == "pimpam" and w["remitente_defecto"] == "info@pimpam-vending.com"
               for w in catalogo["webs"])

    r = http.patch("/api/erp/settings", headers=auth_headers(http, "admin"), json={
        "lead_response": {"activo": True, "tope_diario": 10,
                          "mapa": {"vending:de": catalogo["plantillas"][0]["id"]}},
    })
    assert r.status_code == 200, r.text
    guardada = http.get("/api/erp/settings", headers=auth_headers(http, "admin")).json()
    assert guardada["lead_response"]["activo"] is True
    assert guardada["lead_response"]["tope_diario"] == 10
    assert guardada["lead_response"]["mapa"] == {"vending:de": catalogo["plantillas"][0]["id"]}
    with session_factory() as s:
        assert config_leads.configuracion(s)["activo"] is True

    r = http.patch("/api/erp/settings", headers=auth_headers(http, "admin"), json={
        "lead_response": {"mapa": {"vending:es": "no-existe"}},
    })
    assert r.status_code == 400 and "no existe" in r.text


# --- en seco ---------------------------------------------------------------------


def _form(session: Session, slug: str = "pimpam-contacto-es", language: str = "es") -> WebForm:
    form = WebForm(slug=slug, name=slug, brand="pimpam", language=language,
                   created_by_user_id=_admin_id(session), assignment_mode="none")
    session.add(form)
    session.flush()
    session.add(WebFormField(form_id=form.id, field_key="message", label="Consulta",
                             field_type="textarea", position=0))
    session.flush()
    return form


def _envio(session: Session, form: WebForm, contact: Contact, texto: str, *,
           hace: timedelta) -> FormSubmission:
    envio = FormSubmission(form_id=form.id, contact_id=contact.id,
                           raw_payload_json=json.dumps({"message": texto}), is_spam=False,
                           created_at=datetime.now(UTC) - hace)
    session.add(envio)
    session.flush()
    return envio


def _contacto(session: Session, email: str, **over) -> Contact:
    c = Contact(first_name=over.pop("first_name", "Lead"), email=email, **over)
    session.add(c)
    session.flush()
    return c


def test_en_seco_dice_que_haria_y_no_escribe_nada(session_factory) -> None:
    with session_factory() as s:
        s.add(EmailTemplate(name="Lead · Vending (ES)", subject="x", body_html="<p>x</p>"))
        form = _form(s)
        ana = _contacto(s, "ana@ejemplo.es", first_name="Ana")
        _envio(s, form, ana, "Quiero una máquina de vending con pantalla", hace=timedelta(days=2))
        klaus = _contacto(s, "klaus@druck.de", origin_account_id="agilecrm:acc")
        s.add(Note(contact_id=klaus.id, body=f"form note\n\n{ALEMAN}", external_system="agilecrm",
                   external_account_id="acc",
                   external_created_at=datetime.now(UTC) - timedelta(days=3)))
        viejo = _contacto(s, "viejo@ejemplo.es")
        _envio(s, form, viejo, "Quiero una impresora UV", hace=timedelta(days=40))
        spam = _contacto(s, "hello@blastleadgeneration.com")
        _envio(s, form, spam, "We sell B2B leads", hace=timedelta(days=1))
        s.commit()

        informe = en_seco.simular(s, dias=15, proveedor=ClasificadorPalabrasClave())
        assert informe["nada_escrito"] is True
        por_email = {f["email"]: f for f in informe["items"]}
        assert set(por_email) == {"ana@ejemplo.es", "klaus@druck.de",
                                  "hello@blastleadgeneration.com"}
        ana_fila = por_email["ana@ejemplo.es"]
        assert ana_fila["clasificacion"]["interes"] == "vending"
        assert ana_fila["haria"] == {"etapa": "Nuevo lead", "plantilla": "Lead · Vending (ES)",
                                     "remitente": "info@pimpam-vending.com", "tarea": True,
                                     "aviso": None}
        assert ana_fila["web"] == "pimpam-vending.com" and ana_fila["ya_clasificado"] is False
        klaus_fila = por_email["klaus@druck.de"]
        assert klaus_fila["clasificacion"]["interes"] == "consumibles"
        assert klaus_fila["haria"]["plantilla"] is None
        assert "no es un lead comercial" in klaus_fila["haria"]["aviso"]
        assert "sin remitente" in klaus_fila["haria"]["aviso"]    # cuenta de Agile sin web
        assert por_email["hello@blastleadgeneration.com"]["haria"]["etapa"] == "Descartado / spam"
        assert informe["resumen"]["total"] == 3 and informe["resumen"]["spam"] == 1
        assert informe["resumen"]["por_interes"]["vending"] == 1
        # Nada escrito: ni siquiera pendiente de flush (la sesión va sin autoflush).
        assert not s.new and not s.dirty
        assert s.scalar(select(LeadClassification)) is None
        assert s.get(Contact, ana.id).lead_interest is None


# --- la lista corregible y el workflow -----------------------------------------------


def test_lista_y_correccion_a_mano(http, session_factory) -> None:
    with session_factory() as s:
        ana = _contacto(s, "ana@ejemplo.es", first_name="Ana")
        entrada = EntradaLead(texto="Quiero una máquina de vending", fuente="web_form",
                              referencia="envio-1", lead_at=datetime.now(UTC), sitio="pimpam",
                              idioma_formulario="es", email=ana.email)
        fila = registro.registrar(
            s, ana, entrada, clasificar_lead(entrada, ClasificadorPalabrasClave()),
        )
        s.commit()
        fila_id = fila.id
    r = http.get("/api/erp/leads/clasificaciones?dias=15", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["corregidas"] == 0
    item = body["items"][0]
    assert item["interes"] == "vending" and item["web"] == "pimpam-vending.com"
    # Una sola palabra clave: 0,55 de confianza, por debajo del umbral (0,7).
    assert item["correccion"]["corregida"] is False and item["bajo_umbral"] is True
    assert item["confianza"] == 0.55 and body["umbral_confianza"] == 0.7

    r = http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir",
                  headers=auth_headers(http, "admin"),
                  json={"interes": "distribucion", "nota": "Quiere distribuir, no comprar"})
    assert r.status_code == 200, r.text
    corregido = r.json()
    assert corregido["efectivo"]["interes"] == "distribucion"
    assert corregido["interes"] == "vending"                      # la original se conserva
    assert corregido["correccion"]["por"] and corregido["correccion"]["nota"]
    with session_factory() as s:
        assert s.get(Contact, s.get(LeadClassification, fila_id).contact_id).lead_interest \
            == "distribucion"
        assert s.scalar(select(AuditLog).where(
            AuditLog.action == "lead.classification_corrected")) is not None
    r = http.post(f"/api/erp/leads/clasificaciones/{fila_id}/corregir",
                  headers=auth_headers(http, "admin"), json={"interes": "cohetes"})
    assert r.status_code == 400
    r = http.get("/api/erp/leads/clasificaciones", headers=auth_headers(http, "admin"))
    assert r.json()["corregidas"] == 1


def _ventas_b2b(session: Session) -> Pipeline:
    pipeline = Pipeline(name="Ventas B2B", is_active=True)
    session.add(pipeline)
    session.flush()
    for pos, nombre in enumerate(["Nuevo lead", "Contactado", "Cualificado", "Propuesta enviada",
                                  "Cerrado ganado", "Cerrado perdido", "Descartado / spam"]):
        session.add(PipelineStage(pipeline_id=pipeline.id, name=nombre, position=pos,
                                  is_won=nombre == "Cerrado ganado",
                                  is_lost=nombre in {"Cerrado perdido", "Descartado / spam"}))
    session.flush()
    return pipeline


def test_crear_el_workflow_de_la_fase_1(http, session_factory) -> None:
    r = http.get("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.json()["existe"] is False and r.json()["pipeline_ok"] is False
    r = http.post("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.status_code == 400 and "Ventas B2B" in r.text

    with session_factory() as s:
        pipeline = _ventas_b2b(s)
        s.commit()
        pipeline_id = pipeline.id
    r = http.post("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.status_code == 201, r.text
    creado = r.json()
    assert creado["status"] == "draft" and creado["url"].startswith("/admin/workflows/")
    with session_factory() as s:
        pasos = list(s.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == creado["id"])))
        tipos = sorted(p.type for p in pasos)
        assert tipos == sorted([
            "trigger", "action_classify_lead", "action_add_to_pipeline", "exit_lost",
            "exit_natural", "wait_time", "action_prepare_email_draft", "action_add_to_pipeline",
            "action_create_task", "exit_natural",
        ])
        assert "action_send_email" not in tipos
        por_tipo = {}
        for p in pasos:
            por_tipo.setdefault(p.type, []).append(json.loads(p.config_json))
        etapas = {c["stage_id"] for c in por_tipo["action_add_to_pipeline"]}
        nombres = {s.get(PipelineStage, e).name for e in etapas}
        assert nombres == {"Nuevo lead", "Descartado / spam"}
        assert all(c["pipeline_id"] == pipeline_id for c in por_tipo["action_add_to_pipeline"])
        espera = por_tipo["wait_time"][0]
        assert espera["duration_minutes"] == 720 and espera["window"]["start"] == "09:00"
        assert por_tipo["action_create_task"][0]["assign_to_user_id"] == _admin_id(s)
        # El paso no fija su antigüedad (lee la configuración en cada lead); el
        # trigger se siembra con la de la configuración.
        assert por_tipo["action_classify_lead"][0] == {}
        aristas = list(s.scalars(
            select(WorkflowEdge).where(WorkflowEdge.workflow_id == creado["id"])))
        assert {e.branch_label for e in aristas} == {"default", "spam", "ok", "omitido"}
        wf = workflow_leads.existente(s)
        assert wf is not None and wf.definition_hash
        assert json.loads(wf.trigger_config_json) == {"max_age_hours": 72}
        auditoria = s.scalar(select(AuditLog).where(AuditLog.action == "workflow.created")
                             .order_by(AuditLog.created_at.desc()))
        assert auditoria is not None and auditoria.actor_user_id == _admin_id(s)
    r = http.post("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.status_code == 409 and r.json()["detail"]["workflow_id"] == creado["id"]
    r = http.get("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.json() == {"existe": True, "id": creado["id"], "status": "draft",
                        "url": creado["url"], "pipeline_ok": True, "pipeline_aviso": None}
