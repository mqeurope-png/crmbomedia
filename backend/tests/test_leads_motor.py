"""Respuesta a leads · Fase 1 — el motor de workflows con las piezas nuevas.

Trigger `lead.received` desde el formulario web y desde las notas «form note»
de Agile; pasos clasificar, preparar borrador, añadir a pipeline, mover de
etapa (con historial) y la ventana horaria. **Ningún correo al cliente.**
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra routers, modelos y handlers de pasos
from app.email_templates.models import EmailTemplate
from app.models.crm import (
    Base,
    Contact,
    ContactPipelineStage,
    ContactStageHistory,
    EmailDraft,
    Note,
    Pipeline,
    PipelineStage,
    Tag,
    Task,
    User,
)
from app.models.leads import LeadClassification
from app.models.web_forms import WebForm, WebFormField
from app.models.workflows import (
    Workflow,
    WorkflowEdge,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.leads.eventos import (
    despachar_leads_de_notas,
    es_nota_de_formulario,
    payload_de_nota,
    texto_de_nota,
)
from app.services.web_forms.submit import process_submission
from app.workflows import conditions
from app.workflows.dispatcher import process_event_inline
from app.workflows.engine import advance_run
from app.workflows.trigger_definitions import config_matches
from app.workflows.ventana import VentanaHoraria, ajustar_a_ventana, ventana_desde_config
from tests._test_helpers import seed_test_users

ADMIN_EMAIL = "admin@example.com"


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


@pytest.fixture(autouse=True)
def _despacho_en_linea(monkeypatch):
    """Sin Redis: el despacho corre en línea, de forma determinista."""
    import app.workflows.dispatcher as dispatcher

    monkeypatch.setattr(
        dispatcher, "dispatch_event",
        lambda session, event_type, contact_id, payload=None: process_event_inline(
            session, event_type, contact_id, payload or {}),
    )


@pytest.fixture(autouse=True)
def _nadie_envia_correo(monkeypatch):
    """Fase 1: ni un correo al cliente. Si algo intenta mandar por Gmail, el
    test falla."""
    import app.integrations.gmail.service as gmail_service

    def _prohibido(*args, **kwargs):
        raise AssertionError("En la Fase 1 no se envía ningún correo al cliente")

    monkeypatch.setattr(gmail_service, "send_email", _prohibido)


# --- helpers ----------------------------------------------------------------


def _admin_id(session: Session) -> str:
    return session.scalar(select(User.id).where(User.email == ADMIN_EMAIL))


def _pipeline(session: Session) -> dict[str, PipelineStage]:
    """«Ventas B2B» con sus etapas y plazos."""
    pipeline = Pipeline(name="Ventas B2B", is_active=True)
    session.add(pipeline)
    session.flush()
    etapas = {}
    for pos, (nombre, dias, ganada, perdida) in enumerate([
        ("Nuevo lead", 1, False, False), ("Contactado", 3, False, False),
        ("Cualificado", 7, False, False), ("Propuesta enviada", 14, False, False),
        ("Cerrado ganado", None, True, False), ("Cerrado perdido", None, False, True),
        ("Descartado / spam", None, False, True),
    ]):
        etapa = PipelineStage(
            pipeline_id=pipeline.id, name=nombre, position=pos, target_days=dias,
            is_won=ganada, is_lost=perdida,
        )
        session.add(etapa)
        etapas[nombre] = etapa
    session.flush()
    etapas["_pipeline"] = pipeline
    return etapas


def _plantilla(session: Session, nombre: str, asunto: str, cuerpo: str) -> EmailTemplate:
    tpl = EmailTemplate(name=nombre, subject=asunto, body_html=cuerpo, is_global=True)
    session.add(tpl)
    session.flush()
    return tpl


def _workflow(session: Session, etapas: dict[str, PipelineStage], *, con_espera: bool = True,
              max_age_hours: int = 72) -> Workflow:
    """El flujo de la Fase 1: clasificar → spam a «Descartado / spam»; si no,
    esperar → borrador → Nuevo lead → tarea. Sin enviar nada."""
    admin = _admin_id(session)
    pipeline = etapas["_pipeline"]
    wf = Workflow(
        name="Respuesta a leads (Fase 1)", trigger_type="lead.received",
        trigger_config_json=json.dumps({"max_age_hours": max_age_hours}),
        status=WorkflowStatus.ACTIVE, allow_reentry=False,
    )
    session.add(wf)
    session.flush()

    def paso(tipo: str, cfg: dict | None = None, *, entrada: bool = False) -> WorkflowStep:
        s = WorkflowStep(workflow_id=wf.id, type=tipo, config_json=json.dumps(cfg or {}),
                         is_entry=entrada)
        session.add(s)
        session.flush()
        return s

    def arista(a: WorkflowStep, b: WorkflowStep, rama: str = "default") -> None:
        session.add(WorkflowEdge(workflow_id=wf.id, from_step_id=a.id, to_step_id=b.id,
                                 branch_label=rama))

    trigger = paso("trigger", entrada=True)
    clasificar = paso("action_classify_lead", {"max_age_hours": max_age_hours})
    descartar = paso("action_add_to_pipeline", {
        "pipeline_id": pipeline.id, "stage_id": etapas["Descartado / spam"].id,
    })
    salida_spam = paso("exit_lost")
    omitido = paso("exit_natural")
    cadena: list[WorkflowStep] = []
    if con_espera:
        cadena.append(paso("wait_time", {
            "duration_minutes": 720,
            "window": {"enabled": False},
        }))
    cadena.append(paso("action_prepare_email_draft", {"template_mode": "por_interes"}))
    cadena.append(paso("action_add_to_pipeline", {
        "pipeline_id": pipeline.id, "stage_id": etapas["Nuevo lead"].id,
    }))
    cadena.append(paso("action_create_task", {
        "title": "Revisar lead: {{ lead.interes_texto }} ({{ lead.confianza }})",
        "description": "Borrador: {{ lead.borrador_url }} · {{ lead.motivo }}",
        "priority": "high", "assign_to_user_id": admin,
        "duration_amount": 1, "duration_unit": "days",
    }))
    cadena.append(paso("exit_natural"))

    arista(trigger, clasificar)
    arista(clasificar, descartar, "spam")
    arista(descartar, salida_spam)
    arista(clasificar, omitido, "omitido")
    arista(clasificar, cadena[0], "ok")
    for a, b in zip(cadena, cadena[1:], strict=False):
        arista(a, b)
    session.commit()
    return wf


def _mk_form(session: Session, *, slug: str = "pimpam-contacto-es", language: str = "es",
             tag_vending: Tag | None = None) -> WebForm:
    form = WebForm(
        slug=slug, name=f"Contacto {slug}", brand="pimpam", language=language,
        created_by_user_id=_admin_id(session), assignment_mode="none",
        notify_owner_on_new=False, send_confirmation_email=False,
    )
    session.add(form)
    session.flush()
    campos = [
        WebFormField(form_id=form.id, field_key="name", label="Nombre", field_type="text",
                     position=0, maps_to_contact_field="contact.first_name"),
        WebFormField(form_id=form.id, field_key="email", label="Email", field_type="email",
                     is_required=True, position=1, maps_to_contact_field="contact.email"),
        WebFormField(form_id=form.id, field_key="message", label="Consulta",
                     field_type="textarea", position=2),
    ]
    if tag_vending is not None:
        campos.append(WebFormField(
            form_id=form.id, field_key="maquina", label="Máquina", field_type="tags",
            position=3, options_json=json.dumps([
                {"tag_id": tag_vending.id, "label": tag_vending.name},
            ]),
        ))
    session.add_all(campos)
    session.commit()
    session.refresh(form)
    return form


def _submit(session: Session, form: WebForm, payload: dict) -> str:
    out = process_submission(
        session, form=form, payload=payload, meta={"ip": "1.2.3.4", "user_agent": "t"},
        verify_recaptcha_fn=lambda _t, _i: 0.9, rate_limit_fn=lambda _ip, _f: True,
    )
    assert out.is_spam is False and out.contact_id
    return out.contact_id


def _despertar(session: Session, run: WorkflowRun) -> None:
    """La espera ya venció: el scheduler lo reanudaría."""
    assert run.state == WorkflowRunState.WAITING
    run.wake_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    advance_run(session, run.id)


def _run(session: Session, contact_id: str) -> WorkflowRun:
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.contact_id == contact_id)
                         .order_by(WorkflowRun.started_at.desc()).limit(1))
    assert run is not None, "no se creó ningún run"
    return run


def _etapa_actual(session: Session, contact_id: str) -> PipelineStage | None:
    fila = session.scalar(select(ContactPipelineStage)
                          .where(ContactPipelineStage.contact_id == contact_id))
    return session.get(PipelineStage, fila.stage_id) if fila is not None else None


def _nota_agile(session: Session, contact: Contact, texto: str, *, hace: timedelta,
                cuenta: str = "agile-artisjet") -> Note:
    nota = Note(
        contact_id=contact.id, body=f"form note\n\n{texto}", external_system="agilecrm",
        external_account_id=cuenta, external_id=f"n-{contact.id[:8]}",
        external_created_at=datetime.now(UTC) - hace, source="agile:timeline",
    )
    session.add(nota)
    session.commit()
    return nota


ALEMAN_CONSUMIBLES = ("Hallo, wir möchten Transferfolie für unseren UV-Drucker bestellen. "
                      "Welche Folie haben Sie? Danke und Grüße")
FRANCES_BOTELLAS = ("Bonjour, nous souhaitons imprimer sur des bouteilles en verre. "
                    "Pouvez-vous nous envoyer un devis pour une imprimante UV ? Merci")
ALEMAN_EN_FORMULARIO_FRANCES = (
    "Guten Tag, wir haben Interesse an einer UV-Druckmaschine für Glasflaschen. "
    "Bitte senden Sie uns ein Angebot. Mit freundlichen Grüßen"
)


# --- el caso de aceptación: Isabella (Glow BTL) por pimpam-contacto-es -------


def test_formulario_pimpam_con_productos_marcados(session_factory, monkeypatch) -> None:
    """Vending sin llamar a la IA, idioma es, borrador con «Lead · Vending (ES)»
    desde info@pimpam-vending.com, contacto en Ventas B2B → Nuevo lead, tarea
    creada y NINGÚN correo enviado."""
    import app.services.leads.clasificador as clasificador

    class _IA:
        nombre = "ia_falsa"

        def clasificar(self, entrada):  # pragma: no cover
            raise AssertionError("con productos marcados la IA no clasifica")

    monkeypatch.setattr(clasificador, "proveedor_por_defecto", lambda: _IA())
    with session_factory() as s:
        etapas = _pipeline(s)
        _plantilla(s, "Lead · Vending (ES)", "Máquinas de vending personalizadas",
                   "<p>Hola {{ contact.first_name }}, aquí tienes la gama de vending.</p>")
        _workflow(s, etapas)
        tag = Tag(name="Máquina de vending", name_normalized="maquina de vending", color="#000")
        s.add(tag)
        s.commit()
        form = _mk_form(s, tag_vending=tag)
        contact_id = _submit(s, form, {
            "name": "Isabella", "email": "isabella@glowbtl.mx",
            "message": "Buscamos una máquina de vending con personalización automática y "
                       "pantalla táctil para eventos.",
            "maquina": [tag.id], "utm_source": "chatgpt.com",
        })
        run = _run(s, contact_id)
        _despertar(s, run)
        s.commit()

        fila = s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contact_id))
        assert fila is not None
        assert (fila.interest, fila.interest_source) == ("vending", "etiquetas")
        assert (fila.language, fila.language_source) == ("es", "formulario")
        assert fila.is_spam is False and fila.confidence >= 0.9
        assert fila.source == "web_form" and fila.status == "preparado"
        contacto = s.get(Contact, contact_id)
        assert (contacto.lead_interest, contacto.lead_is_spam) == ("vending", False)

        borrador = s.get(EmailDraft, fila.draft_id)
        assert borrador is not None
        assert borrador.from_alias == "info@pimpam-vending.com"
        assert borrador.from_name == "Pim Pam Vending"
        assert json.loads(borrador.to_emails_json) == ["isabella@glowbtl.mx"]
        assert borrador.subject == "Máquinas de vending personalizadas"
        assert "Hola Isabella" in (borrador.body_html or "")
        assert fila.template_name == "Lead · Vending (ES)"
        assert fila.sender_email == "info@pimpam-vending.com"

        assert _etapa_actual(s, contact_id).name == "Nuevo lead"
        historial = list(s.scalars(select(ContactStageHistory)))
        assert len(historial) == 1 and historial[0].from_stage_id is None
        assert historial[0].to_stage_id == etapas["Nuevo lead"].id

        tarea = s.scalar(select(Task).where(Task.contact_id == contact_id))
        assert tarea is not None
        assert tarea.title == "Revisar lead: Vending (95%)"
        assert f"draft={borrador.id}" in (tarea.description or "")
        assert fila.task_id == tarea.id

        s.refresh(run)
        assert run.state == WorkflowRunState.COMPLETED
        tipos = [h.step_type for h in s.scalars(
            select(WorkflowRunHistory).where(WorkflowRunHistory.run_id == run.id)
            .order_by(WorkflowRunHistory.executed_at))]
        assert "action_send_email" not in tipos
        assert tipos[-1] == "exit_natural"


# --- notas de AgileCRM -------------------------------------------------------


def _contacto(session: Session, email: str, **over) -> Contact:
    c = Contact(first_name=over.pop("first_name", "Lead"), email=email,
                origin_account_id="agilecrm:agile-artisjet", **over)
    session.add(c)
    session.commit()
    return c


def test_nota_agile_en_aleman_sobre_pelicula_de_transferencia(session_factory) -> None:
    """Idioma de, interés consumibles, sin plantilla de venta (borrador vacío
    con el aviso), tarea creada."""
    with session_factory() as s:
        etapas = _pipeline(s)
        _workflow(s, etapas, con_espera=False)
        contacto = _contacto(s, "max@druckerei.de")
        nota = _nota_agile(s, contacto, ALEMAN_CONSUMIBLES, hace=timedelta(hours=2))
        assert despachar_leads_de_notas(s, contact_id=contacto.id, notas=[nota]) == 1

        fila = s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contacto.id))
        assert (fila.source, fila.source_ref) == ("agilecrm", nota.id)
        assert (fila.language, fila.interest) == ("de", "consumibles")
        assert fila.status == "sin_plantilla"
        assert "no es un lead comercial" in (fila.status_detail or "")
        borrador = s.get(EmailDraft, fila.draft_id)
        assert borrador is not None and not borrador.subject and not borrador.body_html
        assert s.scalar(select(Task).where(Task.contact_id == contacto.id)) is not None
        assert _etapa_actual(s, contacto.id).name == "Nuevo lead"


def test_nota_en_frances_sobre_botellas_de_vidrio(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        _plantilla(s, "Lead · UV pequeño-mediano (FR)", "Impression UV sur objets",
                   "<p>Bonjour {{ contact.first_name }}</p>")
        _workflow(s, etapas, con_espera=False)
        contacto = _contacto(s, "marie@verrerie.fr", first_name="Marie")
        nota = _nota_agile(s, contacto, FRANCES_BOTELLAS, hace=timedelta(hours=5))
        despachar_leads_de_notas(s, contact_id=contacto.id, notas=[nota])

        fila = s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contacto.id))
        assert (fila.language, fila.interest) == ("fr", "uv_pequeno_mediano")
        assert fila.template_name == "Lead · UV pequeño-mediano (FR)"
        assert fila.status == "preparado"
        borrador = s.get(EmailDraft, fila.draft_id)
        assert borrador.subject == "Impression UV sur objets"
        assert "Bonjour Marie" in borrador.body_html


def test_formulario_frances_pero_texto_en_aleman(session_factory) -> None:
    """Gana el alemán y queda anotada la discrepancia."""
    with session_factory() as s:
        etapas = _pipeline(s)
        _workflow(s, etapas, con_espera=False)
        form = _mk_form(s, slug="artisjet-eu-contacto-fr", language="fr")
        contact_id = _submit(s, form, {
            "name": "Klaus", "email": "klaus@glas.de", "message": ALEMAN_EN_FORMULARIO_FRANCES,
        })
        fila = s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contact_id))
        assert (fila.language, fila.language_source) == ("de", "texto")
        assert fila.language_mismatch is True and fila.form_language == "fr"
        assert "fr" in fila.reason and "de" in fila.reason
        assert fila.contexto()["sitio"] == "artisjet-eu"


# --- spam, antigüedad, una vez por lead ---------------------------------------


def test_spam_va_a_descartado_sin_borrador_ni_tarea(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        _workflow(s, etapas)
        form = _mk_form(s)
        contact_id = _submit(s, form, {
            "name": "Blast", "email": "hello@blastleadgeneration.com",
            "message": "We generate qualified B2B leads for your business. Boost your sales "
                       "with our lead generation service.",
        })
        fila = s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contact_id))
        assert fila.is_spam is True and fila.status == "spam"
        assert s.get(Contact, contact_id).lead_is_spam is True
        assert _etapa_actual(s, contact_id).name == "Descartado / spam"
        assert s.scalar(select(EmailDraft).where(EmailDraft.contact_id == contact_id)) is None
        assert s.scalar(select(Task).where(Task.contact_id == contact_id)) is None
        run = _run(s, contact_id)
        assert run.state == WorkflowRunState.COMPLETED and run.exit_kind.value == "lost"


def test_lead_de_hace_20_dias_no_se_procesa(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        _workflow(s, etapas)
        contacto = _contacto(s, "viejo@ejemplo.de")
        nota = _nota_agile(s, contacto, ALEMAN_CONSUMIBLES, hace=timedelta(days=20))
        despachar_leads_de_notas(s, contact_id=contacto.id, notas=[nota])
        assert s.scalar(select(WorkflowRun).where(WorkflowRun.contact_id == contacto.id)) is None
        assert s.scalar(select(LeadClassification)) is None
    # El matcher, por su cuenta: la fecha real manda, y 0 = sin límite.
    viejo = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    assert config_matches("lead.received", {"max_age_hours": 72}, {"lead_at": viejo}) is False
    assert config_matches("lead.received", {"max_age_hours": 0}, {"lead_at": viejo}) is True
    assert config_matches("lead.received", {"source": "agilecrm"}, {"source": "web_form"}) is False
    assert config_matches("lead.received", {"site": "pimpam"}, {"site": "pimpam"}) is True


def test_segundo_paso_sobre_el_mismo_lead_no_duplica(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        _plantilla(s, "Lead · UV pequeño-mediano (FR)", "Impression UV", "<p>Bonjour</p>")
        _workflow(s, etapas, con_espera=False)
        contacto = _contacto(s, "marie@verrerie.fr")
        nota = _nota_agile(s, contacto, FRANCES_BOTELLAS, hace=timedelta(hours=1))
        despachar_leads_de_notas(s, contact_id=contacto.id, notas=[nota])
        # El mismo evento, otra vez (un refresco repetido, un reintento).
        process_event_inline(s, "lead.received", contacto.id, payload_de_nota(nota))
        s.commit()

        assert s.scalar(select(LeadClassification).where(
            LeadClassification.contact_id == contacto.id).with_only_columns(
            LeadClassification.id).limit(5)) is not None
        assert len(list(s.scalars(select(LeadClassification)))) == 1
        assert len(list(s.scalars(select(EmailDraft)))) == 1
        assert len(list(s.scalars(select(Task)))) == 1
        assert len(list(s.scalars(select(ContactStageHistory)))) == 1
        runs = list(s.scalars(select(WorkflowRun).order_by(WorkflowRun.started_at)))
        assert len(runs) == 2
        segundo = [h for h in s.scalars(select(WorkflowRunHistory).where(
            WorkflowRunHistory.run_id == runs[1].id))]
        clasificar = next(h for h in segundo if h.step_type == "action_classify_lead")
        assert clasificar.status == "skipped"
        assert {h.step_type for h in segundo} == {"trigger", "action_classify_lead", "exit_natural"}


# --- pipelines: añadir y mover con historial ----------------------------------


def _run_de_un_paso(session: Session, contact: Contact, tipo: str, cfg: dict) -> WorkflowRun:
    wf = Workflow(name=f"solo {tipo}", trigger_type="contact.manual",
                  status=WorkflowStatus.ACTIVE)
    session.add(wf)
    session.flush()
    trigger = WorkflowStep(workflow_id=wf.id, type="trigger", config_json="{}", is_entry=True)
    paso = WorkflowStep(workflow_id=wf.id, type=tipo, config_json=json.dumps(cfg))
    session.add_all([trigger, paso])
    session.flush()
    session.add(WorkflowEdge(workflow_id=wf.id, from_step_id=trigger.id, to_step_id=paso.id))
    session.flush()
    from app.workflows.engine import start_run

    run = start_run(session, wf, contact, trigger_payload={"event_type": "contact.manual"},
                    skip_dedup=True)
    advance_run(session, run.id)
    session.commit()
    return run


def _historial_del_paso(session: Session, run: WorkflowRun, tipo: str) -> WorkflowRunHistory:
    return session.scalar(select(WorkflowRunHistory).where(
        WorkflowRunHistory.run_id == run.id, WorkflowRunHistory.step_type == tipo))


def test_mover_de_etapa_escribe_el_historial_con_origen_y_destino(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        pipeline = etapas["_pipeline"]
        contacto = _contacto(s, "lead@ejemplo.es")
        # Sin estar en el pipeline: se salta, no inventa una fila.
        run = _run_de_un_paso(s, contacto, "action_move_opportunity_stage", {
            "pipeline_id": pipeline.id, "stage_id": etapas["Contactado"].id,
        })
        assert _historial_del_paso(s, run, "action_move_opportunity_stage").status == "skipped"
        assert s.scalar(select(ContactPipelineStage)) is None

        _run_de_un_paso(s, contacto, "action_add_to_pipeline", {
            "pipeline_id": pipeline.id, "stage_id": etapas["Nuevo lead"].id,
        })
        run = _run_de_un_paso(s, contacto, "action_move_opportunity_stage", {
            "pipeline_id": pipeline.id, "stage_id": etapas["Contactado"].id,
        })
        assert _historial_del_paso(s, run, "action_move_opportunity_stage").status == "ok"
        assert _etapa_actual(s, contacto.id).name == "Contactado"
        historial = list(s.scalars(select(ContactStageHistory)
                                   .order_by(ContactStageHistory.moved_at)))
        assert [(h.from_stage_id, h.to_stage_id) for h in historial] == [
            (None, etapas["Nuevo lead"].id),
            (etapas["Nuevo lead"].id, etapas["Contactado"].id),
        ]
        assert historial[1].moved_at is not None
        # La etapa de otro pipeline no vale.
        otro = Pipeline(name="Otro", is_active=True)
        s.add(otro)
        s.flush()
        ajena = PipelineStage(pipeline_id=otro.id, name="X", position=0)
        s.add(ajena)
        s.flush()
        run = _run_de_un_paso(s, contacto, "action_move_opportunity_stage", {
            "pipeline_id": pipeline.id, "stage_id": ajena.id,
        })
        fila = _historial_del_paso(s, run, "action_move_opportunity_stage")
        assert fila.status == "skipped" and "pipeline" in (fila.error_summary or "")


def test_anadir_a_pipeline_no_mueve_si_ya_esta(session_factory) -> None:
    with session_factory() as s:
        etapas = _pipeline(s)
        pipeline = etapas["_pipeline"]
        contacto = _contacto(s, "lead@ejemplo.es")
        _run_de_un_paso(s, contacto, "action_add_to_pipeline", {
            "pipeline_id": pipeline.id, "stage_id": etapas["Contactado"].id,
        })
        run = _run_de_un_paso(s, contacto, "action_add_to_pipeline", {
            "pipeline_id": pipeline.id, "stage_id": etapas["Nuevo lead"].id,
        })
        assert _historial_del_paso(s, run, "action_add_to_pipeline").status == "skipped"
        assert _etapa_actual(s, contacto.id).name == "Contactado"
        assert len(list(s.scalars(select(ContactStageHistory)))) == 1


# --- condiciones, ventana horaria, notas de Agile ------------------------------


def test_las_condiciones_leen_la_clasificacion_del_contacto(session_factory) -> None:
    with session_factory() as s:
        contacto = _contacto(s, "lead@ejemplo.es", lead_interest="vending", lead_is_spam=False,
                             language="es")
        ctx = conditions.EvalContext(session=s, contact=contacto, trigger_payload={})
        assert conditions.evaluate(
            {"field": "contact.lead_is_spam", "op": "eq", "value": False}, ctx) is True
        assert conditions.evaluate(
            {"field": "contact.lead_interest", "op": "eq", "value": "vending"}, ctx) is True
        assert conditions.evaluate(
            {"field": "contact.language", "op": "eq", "value": "de"}, ctx) is False


def test_ventana_horaria_mueve_el_despertar_al_siguiente_hueco() -> None:
    madrid = ZoneInfo("Europe/Madrid")
    ventana = VentanaHoraria(inicio=datetime.strptime("09:00", "%H:%M").time(),
                             fin=datetime.strptime("18:00", "%H:%M").time(), laborables=True)

    def local(y, m, d, hh, mm=0):
        return datetime(y, m, d, hh, mm, tzinfo=madrid)

    # Martes a las 10:00: dentro, no se toca.
    assert ajustar_a_ventana(local(2026, 10, 13, 10), ventana) == local(2026, 10, 13, 10)
    # Martes a las 03:00 (las 12 h de un lead de las 15:00): a las 09:00 del mismo día.
    assert ajustar_a_ventana(local(2026, 10, 13, 3), ventana) == local(2026, 10, 13, 9)
    # Viernes a las 20:00: al lunes a las 09:00.
    assert ajustar_a_ventana(local(2026, 10, 16, 20), ventana) == local(2026, 10, 19, 9)
    # Sábado al mediodía: al lunes.
    assert ajustar_a_ventana(local(2026, 10, 17, 12), ventana) == local(2026, 10, 19, 9)
    # Sin laborables, el sábado vale.
    fin_de_semana = VentanaHoraria(ventana.inicio, ventana.fin, laborables=False)
    assert ajustar_a_ventana(local(2026, 10, 17, 12), fin_de_semana) == local(2026, 10, 17, 12)
    # Config: apagada o inválida → sin ventana.
    assert ventana_desde_config({"window": {"enabled": False}}) is None
    assert ventana_desde_config({"window": {"start": "18:00", "end": "09:00"}}) is None
    assert ventana_desde_config({"window": {"start": "9:00", "end": "18:00"}}) == ventana
    assert ventana_desde_config({}) is None


def test_el_paso_de_espera_aplica_la_ventana(session_factory) -> None:
    from app.models.workflows import WorkflowStep as _Step
    from app.workflows.steps import _step_wait_time

    with session_factory() as s:
        contacto = _contacto(s, "lead@ejemplo.es")
        paso = _Step(workflow_id="wf", type="wait_time", config_json=json.dumps({
            "duration_minutes": 1,
            "window": {"enabled": True, "start": "00:00", "end": "23:59", "weekdays_only": False},
        }))
        out = _step_wait_time(s, None, paso, contacto)
        # Ventana de todo el día: nunca se mueve y el resultado no la anota.
        assert out.wake_at is not None and "window" not in (out.result or {})
        paso = _Step(workflow_id="wf", type="wait_time", config_json=json.dumps({
            "duration_minutes": 1,
            "window": {"enabled": True, "start": "00:00", "end": "00:01", "weekdays_only": False},
        }))
        out = _step_wait_time(s, None, paso, contacto)
        assert out.wake_at is not None and out.result["sleep_until"] == out.wake_at.isoformat()


def test_las_notas_form_note_de_agile_se_reconocen_y_se_recogen(session_factory) -> None:
    from app.integrations.agilecrm.jobs import _sync_contact_notes

    assert es_nota_de_formulario("form note - precios y disponibilidad")
    assert es_nota_de_formulario("Form Note:\nHola")
    assert not es_nota_de_formulario("Llamada: habló de X")
    assert texto_de_nota("form note - precios y disponibilidad") == "precios y disponibilidad"
    with session_factory() as s:
        contacto = _contacto(s, "lead@ejemplo.es")
        nuevas: list[Note] = []
        escritas = _sync_contact_notes(
            s, contact_id=contacto.id, account_id="agile-artisjet", payloads=[
                {"id": 1, "subject": "form note", "description": "precios", "created_time": 1},
                {"id": 2, "subject": "Llamada", "description": "habló", "created_time": 2},
            ], nuevas=nuevas,
        )
        assert escritas == 2 and len(nuevas) == 2
        # Un segundo refresco con las mismas notas no las recoge como nuevas.
        otra_vez: list[Note] = []
        _sync_contact_notes(
            s, contact_id=contacto.id, account_id="agile-artisjet", payloads=[
                {"id": 1, "subject": "form note", "description": "precios", "created_time": 1},
            ], nuevas=otra_vez,
        )
        assert otra_vez == []
        payload = payload_de_nota(nuevas[0])
        assert payload["source"] == "agilecrm" and payload["text"] == "precios"
        assert payload["agile_account_id"] == "agile-artisjet"


def test_ningun_correo_al_cliente_en_toda_la_fase_1(session_factory) -> None:
    """La fixture `_nadie_envia_correo` convierte cualquier envío en fallo; el
    flujo completo del formulario pasa sin tocarla."""
    with session_factory() as s:
        etapas = _pipeline(s)
        _workflow(s, etapas, con_espera=False)
        form = _mk_form(s)
        contact_id = _submit(s, form, {
            "name": "Ana", "email": "ana@ejemplo.es",
            "message": "Quiero información sobre impresoras UV para botellas",
        })
        run = _run(s, contact_id)
        assert run.state == WorkflowRunState.COMPLETED
        assert s.scalar(select(EmailDraft).where(EmailDraft.contact_id == contact_id)) is not None
