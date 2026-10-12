"""Las condiciones de los workflows sobre campos de otras tablas («En
segmento», «En pipeline», «Etapa», «En workflow», notas…): el editor las
ofrece (es el mismo filtro que el de contactos) y antes daban 400 al activar
(«unknown field: in_segment») y salían siempre «No». Ahora se evalúan con el
motor de segmentos contra la base de datos (`conditions._cumple_en_bd`)."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos para create_all
from app.models.crm import (
    Base,
    Contact,
    ContactPipelineStage,
    Note,
    Pipeline,
    PipelineStage,
    Segment,
    User,
)
from app.models.workflows import Workflow, WorkflowRun, WorkflowRunState, WorkflowStatus
from app.workflows.conditions import EvalContext, evaluate, validate_tree
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        yield s
    Base.metadata.drop_all(engine)


def _contacto(s: Session, nombre: str, **campos: object) -> Contact:
    c = Contact(first_name=nombre, email=f"{nombre.lower()}@example.com", tags="", **campos)
    s.add(c)
    s.flush()
    return c


def _regla(field: str, comparator: str, value: object) -> dict:
    return {"type": "rule", "field": field, "comparator": comparator, "value": value}


def _cumple(s: Session, contacto: Contact, arbol: dict) -> bool:
    return evaluate(arbol, EvalContext(session=s, contact=contacto))


def test_en_segmento(session: Session) -> None:
    s = session
    clientes = Segment(name="Clientes", owner_user_id=s.scalar(select(User.id)),
                       rules_json=json.dumps({"operator": "and", "children": [
                           _regla("commercial_status", "eq", "won")]}))
    s.add(clientes)
    ana = _contacto(s, "Ana", commercial_status="won")
    bea = _contacto(s, "Bea", commercial_status="new")
    s.flush()
    dentro = _regla("in_segment", "in", [clientes.id])
    fuera = _regla("in_segment", "not_in", [clientes.id])
    assert _cumple(s, ana, dentro) and not _cumple(s, bea, dentro)
    assert _cumple(s, bea, fuera) and not _cumple(s, ana, fuera)
    # Un segmento que ya no existe no contiene a nadie.
    assert not _cumple(s, ana, _regla("in_segment", "in", [str(uuid4())]))


def test_en_pipeline_y_etapa(session: Session) -> None:
    s = session
    ventas = Pipeline(name="Ventas")
    s.add(ventas)
    s.flush()
    oferta = PipelineStage(pipeline_id=ventas.id, name="Oferta", position=1)
    ganado = PipelineStage(pipeline_id=ventas.id, name="Ganado", position=2)
    s.add_all([oferta, ganado])
    ana = _contacto(s, "Ana")
    bea = _contacto(s, "Bea")
    s.flush()
    ahora = datetime.now(UTC)
    s.add(ContactPipelineStage(contact_id=ana.id, pipeline_id=ventas.id, stage_id=oferta.id,
                               entered_stage_at=ahora, added_to_pipeline_at=ahora))
    s.flush()
    assert _cumple(s, ana, _regla("pipeline_id", "in", [ventas.id]))
    assert not _cumple(s, bea, _regla("pipeline_id", "in", [ventas.id]))
    assert _cumple(s, ana, _regla("pipeline_stage_id", "in", [oferta.id]))
    assert not _cumple(s, ana, _regla("pipeline_stage_id", "in", [ganado.id]))


def test_en_workflow_y_mezclado_con_campos_de_siempre(session: Session) -> None:
    s = session
    admin = s.scalar(select(User))
    wf = Workflow(name="Bienvenida", trigger_type="contact.created",
                  status=WorkflowStatus.ACTIVE, created_by_user_id=admin.id,
                  trigger_config_json="{}")
    s.add(wf)
    ana = _contacto(s, "Ana", lead_score=80)
    bea = _contacto(s, "Bea", lead_score=80)
    s.flush()
    rid = str(uuid4())
    s.add(WorkflowRun(id=rid, workflow_id=wf.id, contact_id=ana.id,
                      state=WorkflowRunState.COMPLETED, trigger_payload_json="{}",
                      started_at=datetime.now(UTC), active_dedup_key=f"archived:{rid}"))
    s.flush()
    arbol = {"operator": "and", "children": [
        _regla("lead_score", "gte", 50),
        _regla("in_workflow", "in", [wf.id]),
    ]}
    assert _cumple(s, ana, arbol) and not _cumple(s, bea, arbol)


def test_lo_cambiado_en_la_misma_transaccion_cuenta(session: Session) -> None:
    """Un paso anterior del workflow añadió una nota sin hacer commit: la
    condición «tiene notas» la ve."""
    s = session
    ana = _contacto(s, "Ana")
    s.commit()
    tiene_notas = _regla("has_notes", "eq", "any")
    assert not _cumple(s, ana, tiene_notas)
    s.add(Note(contact_id=ana.id, body="Llamó para pedir precio.", source="manual"))
    assert _cumple(s, ana, tiene_notas)


def test_al_activar_ya_no_da_unknown_field(session: Session) -> None:
    arbol = {"operator": "and", "children": [
        _regla("in_segment", "in", [str(uuid4())]),
        _regla("pipeline_stage_id", "not_in", [str(uuid4())]),
        _regla("in_workflow", "in", [str(uuid4())]),
        _regla("lead_score", "gt", 10),
    ]}
    assert validate_tree(arbol) == []
    # Lo que el motor no acepta se dice al activar, con el campo.
    errores = validate_tree(_regla("in_segment", "contains", "x"))
    assert errores and "in_segment" in errores[0]
    # Un campo que nadie conoce sigue siendo un error.
    assert validate_tree(_regla("campo_inventado", "eq", 1)) == [
        "unknown field: campo_inventado"]


def test_una_regla_que_el_motor_rechaza_no_se_cumple(session: Session) -> None:
    s = session
    ana = _contacto(s, "Ana")
    assert not _cumple(s, ana, _regla("in_segment", "contains", "x"))


# --- lo que salió en la revisión -------------------------------------------------


def test_campos_de_la_ficha_con_un_operador_que_en_memoria_no_existe(session: Session) -> None:
    """«Creado hace más de N días» o un «entre» de fechas: el editor los ofrece
    sobre campos de la ficha y en memoria no existían (salían siempre «No»).
    Ahora van al motor."""
    from datetime import timedelta  # noqa: PLC0415

    s = session
    viejo = _contacto(s, "Viejo", created_at=datetime.now(UTC) - timedelta(days=40))
    nuevo = _contacto(s, "Nuevo", created_at=datetime.now(UTC) - timedelta(days=2))
    hace_mas_de_30 = _regla("created_at", "older_than_n_days", 30)
    assert _cumple(s, viejo, hace_mas_de_30) and not _cumple(s, nuevo, hace_mas_de_30)
    entre = _regla("created_at", "between", [
        (datetime.now(UTC) - timedelta(days=50)).date().isoformat(),
        (datetime.now(UTC) - timedelta(days=30)).date().isoformat(),
    ])
    assert _cumple(s, viejo, entre) and not _cumple(s, nuevo, entre)
    # Los operadores de siempre siguen en memoria (sin sesión también).
    assert evaluate(_regla("first_name", "contains", "vie"),
                    EvalContext(session=None, contact=viejo))


def test_vocabulario_antiguo_sin_sesion_y_errores_de_la_bd(session: Session) -> None:
    from unittest.mock import patch  # noqa: PLC0415

    from sqlalchemy.exc import OperationalError  # noqa: PLC0415

    s = session
    sin_estrellas = _contacto(s, "Ana")
    con_tres = _contacto(s, "Bea", star_rating=3)
    # Una hoja guardada con el vocabulario antiguo (`op`) se traduce al motor.
    vacio = {"field": "star_rating", "op": "empty", "value": None}
    distinto = {"field": "star_rating", "op": "ne", "value": 3}
    assert _cumple(s, sin_estrellas, vacio) and not _cumple(s, con_tres, vacio)
    assert not _cumple(s, con_tres, distinto)
    assert validate_tree(vacio) == []
    # Sin sesión no revienta: no se cumple.
    assert not evaluate(_regla("has_notes", "eq", "any"),
                        EvalContext(session=None, contact=sin_estrellas))
    # Un error de la base de datos tampoco: no se cumple (y se avisa).
    with patch.object(s, "scalar", side_effect=OperationalError("SELECT", {}, Exception("lock"))):
        assert not _cumple(s, sin_estrellas, vacio)


def test_al_activar_se_validan_las_reglas_del_segmento_nombrado(session: Session) -> None:
    s = session
    roto = Segment(name="Roto", owner_user_id=s.scalar(select(User.id)),
                   rules_json=json.dumps({"operator": "and", "children": [
                       _regla("campo_que_ya_no_existe", "eq", 1)]}))
    s.add(roto)
    s.flush()
    regla = _regla("in_segment", "in", [roto.id])
    # Sin sesión no se puede mirar dentro; con ella, sí.
    assert validate_tree(regla) == []
    errores = validate_tree(regla, session=s)
    assert errores and "in_segment" in errores[0] and "campo_que_ya_no_existe" in errores[0]


def test_en_workflow_no_cuenta_la_ejecucion_en_curso(session: Session) -> None:
    s = session
    admin = s.scalar(select(User))
    wf = Workflow(name="Bienvenida", trigger_type="contact.created",
                  status=WorkflowStatus.ACTIVE, created_by_user_id=admin.id,
                  trigger_config_json="{}")
    s.add(wf)
    ana = _contacto(s, "Ana")
    s.flush()

    def _ejecucion() -> str:
        rid = str(uuid4())
        s.add(WorkflowRun(id=rid, workflow_id=wf.id, contact_id=ana.id,
                          state=WorkflowRunState.RUNNING, trigger_payload_json="{}",
                          started_at=datetime.now(UTC), active_dedup_key=f"archived:{rid}"))
        s.flush()
        return rid

    en_curso = _ejecucion()
    ya_estuvo = _regla("in_workflow", "in", [wf.id])
    ctx = EvalContext(session=s, contact=ana, run_id=en_curso)
    # Primera vez: la única ejecución es la de ahora → «¿ya estuvo?» = no.
    assert not evaluate(ya_estuvo, ctx)
    assert evaluate(_regla("in_workflow", "not_in", [wf.id]), ctx)
    # Con una ejecución anterior, sí.
    _ejecucion()
    assert evaluate(ya_estuvo, ctx)
    # Fuera de una ejecución (el filtro del disparador), cuenta todas.
    assert evaluate(ya_estuvo, EvalContext(session=s, contact=ana))


def test_una_sola_consulta_para_el_estimador_y_el_cron(session: Session) -> None:
    from sqlalchemy import func  # noqa: PLC0415

    from app.workflows.conditions import clausula_sql  # noqa: PLC0415

    s = session
    ventas = Pipeline(name="Ventas")
    s.add(ventas)
    s.flush()
    oferta = PipelineStage(pipeline_id=ventas.id, name="Oferta", position=1)
    s.add(oferta)
    dentro = [_contacto(s, f"Dentro{i}", lead_score=60) for i in range(3)]
    _contacto(s, "Fuera", lead_score=60)
    _contacto(s, "Bajo", lead_score=10)
    s.flush()
    ahora = datetime.now(UTC)
    for c in dentro:
        s.add(ContactPipelineStage(contact_id=c.id, pipeline_id=ventas.id, stage_id=oferta.id,
                                   entered_stage_at=ahora, added_to_pipeline_at=ahora))
    s.flush()
    arbol = {"operator": "and", "children": [
        _regla("lead_score", "gte", 50), _regla("pipeline_id", "in", [ventas.id])]}
    clausula = clausula_sql(arbol, s)
    assert clausula is not None
    assert s.scalar(select(func.count(Contact.id)).where(clausula)) == 3
    # Un árbol solo con campos de la ficha, o con el formato antiguo, sigue
    # evaluándose contacto a contacto (como hasta ahora).
    assert clausula_sql(_regla("lead_score", "gte", 50), s) is None
    assert clausula_sql({"op": "AND", "children": [
        {"field": "pipeline_id", "op": "in", "value": [ventas.id]}]}, s) is None
