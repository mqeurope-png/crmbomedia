"""Remates de la respuesta a leads y el análisis de la IA en la ficha del
contacto (10/10/2026).

- A1: una sola entrada en el workflow sembrado y en el guardado del editor,
  y el error de validación nombra los pasos marcados.
- A2: las tareas vencidas van de más reciente a más antigua.
- A3: «Creado en origen» cae a la fecha de alta cuando no hay fecha de
  origen, al ordenar en la lista legacy y en el motor de entidades.
- A4: `lead.consulta` en las variables del workflow (y documentadas las que
  faltaban).
- B: las clasificaciones de un contacto para la ficha (la última arriba,
  consulta entera, contexto, discrepancia de idioma en palabras) y el plazo
  de la etapa en los pipelines del contacto.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.api.workflows import _validate_workflow_structure
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Contact,
    ContactPipelineStage,
    Pipeline,
    PipelineStage,
    Task,
    TaskStatus,
    User,
)
from app.models.leads import LeadClassification
from app.models.workflows import Workflow, WorkflowStatus, WorkflowStep
from app.repositories import crm as crm_repository
from app.repositories.tasks import buckets_for_user
from app.services.entities.registry import get_entity
from app.services.leads import registro
from app.services.leads import workflow as workflow_leads
from app.services.leads.clasificador import (
    ClasificadorPalabrasClave,
    EntradaLead,
    clasificar_lead,
)
from app.services.segments.fields import FIELD_SPECS
from app.workflows.entrada import describir_paso, elegir_entrada
from app.workflows.variables import available_variables, build_context, render
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


def _admin_id(session: Session) -> str:
    return session.scalar(select(User.id).where(User.email == "admin@example.com"))


def _contacto(session: Session, email: str, **over) -> Contact:
    c = Contact(first_name=over.pop("first_name", "Lead"), email=email, **over)
    session.add(c)
    session.flush()
    return c


def _ventas_b2b(session: Session, *, plazos: bool = False) -> Pipeline:
    pipeline = Pipeline(name="Ventas B2B", is_active=True)
    session.add(pipeline)
    session.flush()
    for pos, (nombre, dias) in enumerate([
        ("Nuevo lead", 1), ("Contactado", 3), ("Cualificado", 7), ("Propuesta enviada", 14),
        ("Cerrado ganado", None), ("Cerrado perdido", None), ("Descartado / spam", None),
    ]):
        session.add(PipelineStage(
            pipeline_id=pipeline.id, name=nombre, position=pos,
            target_days=dias if plazos else None,
            is_won=nombre == "Cerrado ganado",
            is_lost=nombre in {"Cerrado perdido", "Descartado / spam"},
        ))
    session.flush()
    return pipeline


# --- A1 · una sola entrada, y es el disparador ------------------------------------


def test_elegir_entrada_respeta_una_marca_y_si_no_elige_el_disparador() -> None:
    pasos = [("t", "trigger", True), ("a", "action_classify_lead", False),
             ("b", "wait_time", False)]
    aristas = {"a", "b"}
    assert elegir_entrada(pasos, aristas) == "t"
    # Una sola marca, aunque no sea el disparador: se respeta (es lo que pidió el editor).
    assert elegir_entrada([("t", "trigger", False), ("a", "wait_time", True)], {"a"}) == "a"
    # Cinco marcadas (lo de producción): el disparador sin conexiones de entrada.
    cinco = [(pid, tipo, True) for pid, tipo, _m in pasos]
    cinco += [("c", "exit_natural", True), ("d", "action_create_task", True)]
    assert elegir_entrada(cinco, {"a", "b", "c", "d"}) == "t"
    # Ninguna marcada y el disparador no va primero: sigue siendo el disparador.
    assert elegir_entrada([("a", "wait_time", False), ("t", "trigger", False)], {"a"}) == "t"
    # Sin disparador: el primero sin conexiones de entrada.
    sin_disparador = [("a", "wait_time", False), ("b", "exit_natural", False)]
    assert elegir_entrada(sin_disparador, {"a"}) == "b"
    # Todos reciben conexiones: el primero.
    assert elegir_entrada(sin_disparador, {"a", "b"}) == "a"
    assert elegir_entrada([], set()) is None


def test_describir_paso_nombra_el_paso_para_el_mensaje() -> None:
    uuid = "3f2a1b2c-0000-4000-8000-000000000000"
    assert describir_paso("wait_time", "Esperar 12 h", uuid) \
        == "Esperar 12 h (wait_time) · 3f2a1b2c"
    assert describir_paso("trigger", None, "9b7e6d5c-1111") == "trigger · 9b7e6d5c"
    assert describir_paso("trigger", "  ", "") == "trigger"


def test_el_workflow_sembrado_tiene_una_sola_entrada_el_disparador(http, session_factory) -> None:
    with session_factory() as s:
        _ventas_b2b(s)
        s.commit()
    r = http.post("/api/erp/leads/workflow", headers=auth_headers(http, "admin"))
    assert r.status_code == 201, r.text
    with session_factory() as s:
        pasos = list(s.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_id == r.json()["id"])))
        entradas = [p for p in pasos if p.is_entry]
        assert len(entradas) == 1 and entradas[0].type == "trigger"
        wf = s.get(Workflow, r.json()["id"])
        assert not [e for e in _validate_workflow_structure(s, wf) if "entrada" in e]


def _definicion(steps: list[dict], edges: list[dict]) -> dict:
    return {"steps": steps, "edges": edges}


def _paso(client_id: str, tipo: str, *, is_entry: bool, config: dict | None = None) -> dict:
    return {"client_id": client_id, "type": tipo, "config": config or {},
            "position_x": 0, "position_y": 0, "is_entry": is_entry}


def test_guardar_desde_el_editor_con_varias_entradas_deja_solo_el_disparador(http) -> None:
    cab = auth_headers(http, "admin")
    wf = http.post("/api/workflows", json={"name": "Entradas", "trigger_type": "contact.created"},
                   headers=cab)
    assert wf.status_code == 201, wf.text
    wf_id = wf.json()["id"]
    # Lo que mandaba el editor viejo tras reordenar nodos: varias marcas.
    r = http.put(f"/api/workflows/{wf_id}", headers=cab, json=_definicion(
        [_paso("s1", "trigger", is_entry=True),
         _paso("s2", "action_add_tag", is_entry=True, config={"tag": "Hot"}),
         _paso("s3", "exit_natural", is_entry=True)],
        [{"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
         {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "default"}],
    ))
    assert r.status_code == 200, r.text
    entradas = [p for p in r.json()["steps"] if p["is_entry"]]
    assert len(entradas) == 1 and entradas[0]["type"] == "trigger"
    # Ninguna marca y el disparador no va primero: la entrada sigue siendo el disparador.
    r = http.put(f"/api/workflows/{wf_id}", headers=cab, json=_definicion(
        [_paso("s2", "action_add_tag", is_entry=False, config={"tag": "Hot"}),
         _paso("s1", "trigger", is_entry=False),
         _paso("s3", "exit_natural", is_entry=False)],
        [{"from_client_id": "s1", "to_client_id": "s2", "branch_label": "default"},
         {"from_client_id": "s2", "to_client_id": "s3", "branch_label": "default"}],
    ))
    assert r.status_code == 200, r.text
    entradas = [p for p in r.json()["steps"] if p["is_entry"]]
    assert len(entradas) == 1 and entradas[0]["type"] == "trigger"


def test_el_error_de_varias_entradas_nombra_los_pasos(session_factory) -> None:
    with session_factory() as s:
        wf = Workflow(name="Roto", trigger_type="lead.received", status=WorkflowStatus.DRAFT,
                      owner_user_id=_admin_id(s))
        s.add(wf)
        s.flush()
        s.add_all([
            WorkflowStep(id="aaaaaaaa-0000-4000-8000-000000000001", workflow_id=wf.id,
                         type="trigger", config_json="{}", position_x=0, position_y=0,
                         is_entry=True, display_name=None),
            WorkflowStep(id="bbbbbbbb-0000-4000-8000-000000000002", workflow_id=wf.id,
                         type="wait_time", config_json=json.dumps({"duration_minutes": 5}),
                         position_x=0, position_y=100, is_entry=True, display_name="Esperar 12 h"),
            WorkflowStep(id="cccccccc-0000-4000-8000-000000000003", workflow_id=wf.id,
                         type="exit_natural", config_json="{}", position_x=0, position_y=200,
                         is_entry=False, display_name=None),
        ])
        s.flush()
        errores = _validate_workflow_structure(s, wf)
    mensaje = next(e for e in errores if e.startswith("Solo puede haber un paso de entrada"))
    assert "hay 2 marcados" in mensaje
    assert "trigger · aaaaaaaa" in mensaje and "Esperar 12 h (wait_time) · bbbbbbbb" in mensaje
    assert "cccccccc" not in mensaje
    assert "guardar lo corrige" in mensaje


# --- A2 · vencidas de más reciente a más antigua -----------------------------------


def test_las_vencidas_van_de_mas_reciente_a_mas_antigua(session_factory) -> None:
    ahora = datetime.now(UTC)
    with session_factory() as s:
        admin = _admin_id(s)
        for titulo, hace in [("hace 400 días", 400), ("hace 2 días", 2), ("hace 30 días", 30)]:
            s.add(Task(title=titulo, due_at=ahora - timedelta(days=hace), status=TaskStatus.PENDING,
                       assigned_user_id=admin, created_by_user_id=admin))
        for titulo, dentro in [("dentro de 9 días", 9), ("dentro de 3 días", 3)]:
            s.add(Task(title=titulo, due_at=ahora + timedelta(days=dentro),
                       status=TaskStatus.PENDING, assigned_user_id=admin, created_by_user_id=admin))
        s.flush()
        cubos = buckets_for_user(s, admin)
        assert [t.title for t in cubos["overdue"]] \
            == ["hace 2 días", "hace 30 días", "hace 400 días"]
        # Lo que viene sigue de más cercano a más lejano.
        assert [t.title for t in cubos["later"]] == ["dentro de 3 días", "dentro de 9 días"]


# --- A3 · «Creado en origen» con la fecha de alta de respaldo ------------------------


def _tres_contactos(s: Session) -> tuple[Contact, Contact, Contact]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    agile = _contacto(s, "agile@ejemplo.es", first_name="Agile",
                      created_at=base + timedelta(days=240),
                      created_at_external=base)                       # origen: 1 ene
    formulario = _contacto(s, "web@ejemplo.es", first_name="Web",
                           created_at=base + timedelta(days=273),
                           created_at_external=None)                  # alta: 1 oct, sin origen
    otro = _contacto(s, "otro@ejemplo.es", first_name="Otro",
                     created_at=base + timedelta(days=200),
                     created_at_external=base + timedelta(days=120))  # origen: 1 may
    return agile, formulario, otro


def test_creado_en_origen_ordena_con_la_fecha_de_alta_si_no_hay_origen(session_factory) -> None:
    with session_factory() as s:
        agile, formulario, otro = _tres_contactos(s)
        desc = crm_repository.list_contacts(s, sort_by="created_at_external", sort_dir="desc")
        assert [c.email for c in desc] == [formulario.email, otro.email, agile.email]
        asc = crm_repository.list_contacts(s, sort_by="created_at_external", sort_dir="asc")
        assert [c.email for c in asc] == [agile.email, otro.email, formulario.email]
    # El motor de entidades (la lista nueva) ordena por la misma expresión,
    # pero filtra y serializa por la columna real (NULL sigue siendo NULL).
    spec = FIELD_SPECS["created_at_external"]
    orden = str(get_entity("contact").sort_column("created_at_external"))
    assert "coalesce" in orden.lower() and "created_at_external" in orden
    # `serialize_row` lee la fila por `column.key`: tiene que seguir siendo la columna.
    assert spec.sortable is True and spec.column.key == "created_at_external"


def test_la_lista_de_contactos_de_la_api_ordena_igual(http, session_factory) -> None:
    with session_factory() as s:
        agile, formulario, otro = _tres_contactos(s)
        s.commit()
        esperado = [formulario.id, otro.id, agile.id]
    r = http.post("/api/entities/contact/search", headers=auth_headers(http, "admin"),
                  json={"sort_by": "created_at_external", "sort_dir": "desc", "limit": 10})
    assert r.status_code == 200, r.text
    assert [fila["id"] for fila in r.json()["items"]] == esperado
    # La fila trae las dos fechas: la celda enseña la de alta cuando falta la de origen.
    web = next(f for f in r.json()["items"] if f["id"] == formulario.id)
    assert web["created_at_external"] is None and web["created_at"]
    # Filtrar «Creado en origen vacío» sigue encontrando a los de formulario
    # (el COALESCE es solo para ordenar).
    r = http.post("/api/entities/contact/search", headers=auth_headers(http, "admin"),
                  json={"rules_json": {"type": "rule", "field": "created_at_external",
                                       "comparator": "is_null"}, "limit": 10})
    assert r.status_code == 200, r.text
    assert [fila["id"] for fila in r.json()["items"]] == [formulario.id]


# --- A4 · lead.consulta -------------------------------------------------------------


def test_lead_consulta_es_el_texto_del_cliente_y_esta_documentada(session_factory) -> None:
    with session_factory() as s:
        ana = _contacto(s, "ana@ejemplo.es", first_name="Ana")
        assert render("[{{ lead.consulta }}]", build_context(session=s, contact=ana)) == "[]"
        entrada = EntradaLead(texto="  Quiero una máquina de vending con pantalla.\nGracias ",
                              fuente="web_form", referencia="envio-1", lead_at=datetime.now(UTC),
                              sitio="pimpam", idioma_formulario="es", email=ana.email)
        registro.registrar(s, ana, entrada, clasificar_lead(entrada, ClasificadorPalabrasClave()))
        s.flush()
        assert render("{{ lead.consulta }}", build_context(session=s, contact=ana)) \
            == "Quiero una máquina de vending con pantalla.\nGracias"
    documentadas = set(available_variables())
    for nombre in ("lead.consulta", "lead.es_spam", "lead.confianza_num", "lead.fuente",
                   "lead.estado"):
        assert nombre in documentadas


def test_la_tarea_del_workflow_sembrado_lleva_la_consulta(session_factory) -> None:
    with session_factory() as s:
        _ventas_b2b(s)
        definicion = workflow_leads.definicion(s, asignar_a=None)
    tarea = next(p for p in definicion["steps"] if p["type"] == "action_create_task")
    assert "{{ lead.consulta }}" in tarea["config"]["description"]


# --- B · las clasificaciones de un contacto para la ficha ------------------------------


def _clasificar(
    s: Session, contacto: Contact, texto: str, *, referencia: str, hace: timedelta,
    sitio: str = "pimpam", idioma_formulario: str = "es",
) -> LeadClassification:
    entrada = EntradaLead(texto=texto, fuente="web_form", referencia=referencia,
                          lead_at=datetime.now(UTC) - hace, sitio=sitio,
                          idioma_formulario=idioma_formulario, productos=["Vending"],
                          pais="FR", email=contacto.email)
    clasificacion = clasificar_lead(entrada, ClasificadorPalabrasClave())
    return registro.registrar(s, contacto, entrada, clasificacion)


def test_las_clasificaciones_de_un_contacto_la_ultima_arriba(http, session_factory) -> None:
    frase = "Bonjour, je cherche un distributeur automatique de boissons pour un bureau. "
    larga = (frase * 8).strip()
    with session_factory() as s:
        torra = _contacto(s, "torracollons@elbarquito.net", first_name="Torra")
        vieja = _clasificar(s, torra, "Quiero una máquina de vending", referencia="envio-1",
                            hace=timedelta(days=20))
        nueva = _clasificar(s, torra, larga, referencia="envio-2", hace=timedelta(hours=2),
                            idioma_formulario="de")
        # Lo que detecta el clasificador de verdad: el formulario en DE y el texto en FR.
        nueva.language, nueva.form_language, nueva.language_mismatch = "fr", "de", True
        s.commit()
        torra_id, vieja_id, nueva_id = torra.id, vieja.id, nueva.id
    # Lo ve quien ve la ficha: un viewer basta.
    r = http.get(f"/api/erp/leads/contactos/{torra_id}", headers=auth_headers(http, "viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["umbral_confianza"] == 0.7
    assert [i["id"] for i in body["items"]] == [nueva_id, vieja_id]
    ultima = body["items"][0]
    assert ultima["texto_completo"] == larga
    assert len(ultima["texto"]) == 400 and len(ultima["texto_completo"]) > 400
    contexto = ultima["contexto"]
    assert contexto["sitio"] == "pimpam" and contexto["productos"] == ["Vending"]
    assert contexto["pais"] == "FR" and contexto["idioma_formulario"] == "de"
    assert ultima["web"] == "pimpam-vending.com"
    assert ultima["idioma_discrepancia_texto"] == "el formulario era DE pero el texto está en FR"
    assert body["items"][1]["idioma_discrepancia_texto"] is None
    assert body["opciones"]["idiomas"] and body["opciones"]["intereses"]
    # Corregir sigue siendo cosa de `erp.config`: el viewer no puede.
    r = http.post(f"/api/erp/leads/clasificaciones/{nueva_id}/corregir",
                  headers=auth_headers(http, "viewer"), json={"es_spam": True})
    assert r.status_code == 403
    assert http.get("/api/erp/leads/contactos/no-existe",
                    headers=auth_headers(http, "viewer")).status_code == 404
    assert http.get(f"/api/erp/leads/contactos/{torra_id}").status_code == 401


def test_la_lista_de_erp_leads_tambien_trae_la_consulta_entera(http, session_factory) -> None:
    with session_factory() as s:
        ana = _contacto(s, "ana@ejemplo.es", first_name="Ana")
        _clasificar(s, ana, "Quiero una máquina de vending", referencia="envio-1",
                    hace=timedelta(days=1))
        s.commit()
    r = http.get("/api/erp/leads/clasificaciones", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["texto_completo"] == "Quiero una máquina de vending"
    assert item["contexto"]["sitio"] == "pimpam" and item["idioma_discrepancia_texto"] is None


# --- B4 · plazo de la etapa en los pipelines del contacto ------------------------------


def test_los_pipelines_del_contacto_llevan_plazo_y_si_se_paso(http, session_factory) -> None:
    cab = auth_headers(http, "admin")
    with session_factory() as s:
        pipeline = _ventas_b2b(s, plazos=True)
        contactado = s.scalar(select(PipelineStage).where(PipelineStage.name == "Contactado"))
        ganado = s.scalar(select(PipelineStage).where(PipelineStage.name == "Cerrado ganado"))
        ana = _contacto(s, "ana@ejemplo.es", first_name="Ana")
        bea = _contacto(s, "bea@ejemplo.es", first_name="Bea")
        s.commit()
        pipeline_id, contactado_id, ganado_id = pipeline.id, contactado.id, ganado.id
        ana_id, bea_id = ana.id, bea.id
    r = http.post(f"/api/contacts/{ana_id}/pipelines", headers=cab,
                  json={"pipeline_id": pipeline_id, "stage_id": contactado_id})
    assert r.status_code == 201, r.text
    r = http.post(f"/api/contacts/{bea_id}/pipelines", headers=cab,
                  json={"pipeline_id": pipeline_id, "stage_id": ganado_id})
    assert r.status_code == 201, r.text
    with session_factory() as s:
        # Ana lleva 5 días en «Contactado» (plazo 3): fuera de plazo.
        fila = s.scalar(select(ContactPipelineStage)
                        .where(ContactPipelineStage.contact_id == ana_id))
        fila.entered_stage_at = datetime.now(UTC) - timedelta(days=5, hours=1)
        s.commit()
    r = http.get(f"/api/contacts/{ana_id}/pipelines", headers=cab)
    assert r.status_code == 200, r.text
    (ana_fila,) = r.json()
    assert ana_fila["stage_name"] == "Contactado" and ana_fila["days_in_stage"] == 5
    assert ana_fila["target_days"] == 3 and ana_fila["is_overdue"] is True
    # Bea en «Cerrado ganado», sin plazo: nunca fuera de plazo.
    (bea_fila,) = http.get(f"/api/contacts/{bea_id}/pipelines", headers=cab).json()
    assert bea_fila["target_days"] is None and bea_fila["is_overdue"] is False
    assert bea_fila["is_won"] is True and bea_fila["days_in_stage"] == 0
