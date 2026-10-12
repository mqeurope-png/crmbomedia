"""Reponer los campos personalizados que la sincronización borraba
(`app.services.reponer_campos`, `scripts.reponer_campos_personalizados`).

Lo que escribieron los formularios y los workflows se rehace desde su
rastro y se reponen SOLO las claves que faltan: nunca se pisa un valor, ni
una clave quitada a mano, ni un contacto protegido entero.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos para create_all
from app.core.audit import Action
from app.models.crm import (
    AuditLog,
    Base,
    Contact,
    ExternalReference,
    ExternalSystem,
    User,
)
from app.models.web_forms import FormSubmission, WebForm, WebFormField
from app.models.workflows import (
    Workflow,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowRunState,
    WorkflowStatus,
)
from app.services import contact_sync_protection as protection
from app.services.reponer_campos import FORMULARIO, WORKFLOW, aplicar, planificar
from tests._test_helpers import seed_test_users

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


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


def _contacto(s: Session, nombre: str, campos: object = None) -> Contact:
    c = Contact(
        first_name=nombre, email=f"{nombre.lower()}@example.com", tags="",
        custom_fields=(
            campos if isinstance(campos, str) or campos is None else json.dumps(campos)
        ),
    )
    s.add(c)
    s.flush()
    return c


def _formulario(s: Session) -> WebForm:
    admin = s.scalar(select(User))
    form = WebForm(slug="contacto-es", name="Contacto", created_by_user_id=admin.id)
    s.add(form)
    s.flush()
    s.add_all([
        WebFormField(form_id=form.id, field_key="email", label="Email",
                     field_type="email", maps_to_contact_field="contact.email"),
        WebFormField(form_id=form.id, field_key="producto", label="Producto",
                     field_type="text", position=1,
                     maps_to_contact_field="contact.custom.product_interest"),
        # Una clave que también trae un sync (Brevo y Agile): no se repone.
        WebFormField(form_id=form.id, field_key="horario", label="Horario",
                     field_type="text", position=2,
                     maps_to_contact_field="contact.custom.Horario"),
    ])
    s.flush()
    return form


def _respuesta(
    s: Session, form: WebForm, contacto: Contact, producto: str, cuando: datetime,
    *, spam: bool = False,
) -> None:
    s.add(FormSubmission(
        form_id=form.id, contact_id=contacto.id, is_spam=spam, created_at=cuando,
        raw_payload_json=json.dumps(
            {"email": contacto.email, "producto": producto, "horario": "L-V"}
        ),
    ))


def _paso(
    s: Session, contacto: Contact, resultado: dict, cuando: datetime,
    *, estado: str = "ok",
) -> None:
    wf = s.scalar(select(Workflow))
    if wf is None:
        admin = s.scalar(select(User))
        wf = Workflow(name="Marcar interés", trigger_type="contact.created",
                      status=WorkflowStatus.ACTIVE, created_by_user_id=admin.id,
                      trigger_config_json="{}")
        s.add(wf)
        s.flush()
    rid = str(uuid4())
    s.add(WorkflowRun(
        id=rid, workflow_id=wf.id, contact_id=contacto.id,
        state=WorkflowRunState.COMPLETED, trigger_payload_json="{}",
        started_at=cuando, completed_at=cuando, active_dedup_key=f"archived:{rid}",
    ))
    s.flush()
    s.add(WorkflowRunHistory(
        run_id=rid, workflow_id=wf.id, contact_id=contacto.id,
        step_type="action_set_custom_field", status=estado,
        result_json=json.dumps(resultado), executed_at=cuando,
    ))


def _campos(s: Session, contacto: Contact) -> dict:
    s.expire_all()
    raw = s.get(Contact, contacto.id).custom_fields
    return json.loads(raw) if raw else {}


def test_repone_lo_que_escribieron_formularios_y_workflows_y_solo_lo_que_falta(
    session: Session,
):
    s = session
    form = _formulario(s)
    # Al contacto le borró la sincronización lo del formulario y el workflow.
    ana = _contacto(s, "Ana", {"Horario": "9-14"})
    _respuesta(s, form, ana, "DTF", T0)
    _paso(s, ana, {"field": "INTERES", "value": "UV"}, T0 + timedelta(days=1))
    _paso(s, ana, {"field": "INTERES", "value": "Láser"}, T0 + timedelta(days=2))
    # Un campo nativo va a su columna, no al JSON (también en los pasos de
    # antes de la marca `native`); un paso fallido no cuenta.
    _paso(s, ana, {"field": "job_title", "old": None, "new": "CEO", "native": True},
          T0 + timedelta(days=3))
    _paso(s, ana, {"field": "job_title", "value": "CEO"}, T0 + timedelta(days=3))
    _paso(s, ana, {"field": "OTRO", "value": "x"}, T0 + timedelta(days=4), estado="failed")
    # La clave sigue ahí (con otro valor): no se pisa.
    berta = _contacto(s, "Berta", {"product_interest": "Otra"})
    _respuesta(s, form, berta, "DTF", T0)
    # El formulario solo rellena claves vacías: vale la primera respuesta.
    carla = _contacto(s, "Carla")
    _respuesta(s, form, carla, "DTF", T0)
    _respuesta(s, form, carla, "UV", T0 + timedelta(days=1))
    # Spam: no escribió nada.
    _respuesta(s, form, _contacto(s, "Spam"), "DTF", T0, spam=True)
    # Con la marca antigua (el modal entero): lo que tiene se queda, lo que
    # falta se repone (aquel modal no podía quitar claves).
    dani = _contacto(s, "Dani", {"Horario": "9-14"})
    protection.mark_manually_edited(dani, ["custom_fields"])
    _respuesta(s, form, dani, "DTF", T0)
    # La clave la quitó alguien a mano: no vuelve.
    eva = _contacto(s, "Eva", {"Horario": "9-14"})
    protection.mark_manually_edited(eva, [protection.custom_field_mark("product_interest")])
    _respuesta(s, form, eva, "DTF", T0)
    # Lo guardado no es un objeto JSON: no se toca.
    fede = _contacto(s, "Fede", "texto suelto")
    _respuesta(s, form, fede, "DTF", T0)
    # Ana está en Brevo y en Agile.
    s.add_all([
        ExternalReference(system=ExternalSystem.BREVO, account_id="main",
                          external_id="9", contact_id=ana.id),
        ExternalReference(system=ExternalSystem.AGILECRM, account_id="default",
                          external_id="1", contact_id=ana.id),
    ])
    s.commit()

    plan = planificar(s)
    por_contacto = {r.contact_id: r for r in plan.reposiciones}
    assert set(por_contacto) == {ana.id, carla.id, dani.id}
    assert por_contacto[ana.id].claves == {"product_interest": "DTF", "INTERES": "Láser"}
    assert por_contacto[ana.id].origen == {"product_interest": FORMULARIO, "INTERES": WORKFLOW}
    assert por_contacto[carla.id].claves == {"product_interest": "DTF"}
    assert por_contacto[dani.id].claves == {"product_interest": "DTF"}
    assert plan.no_es_objeto == 1
    # «Horario» de los formularios no se repone: lo trae un sync (les falta a
    # berta y a carla; las demás lo tienen).
    assert plan.de_sync == 2
    assert plan.en_brevo_y_agile == 1

    # Entre el informe y el --apply, una sincronización trae INTERES. La
    # sesión del informe aún tiene la copia vieja de Ana: `aplicar` tiene que
    # releerla, o escribiría el JSON viejo y borraría lo del sync.
    assert s.get(Contact, ana.id).custom_fields == json.dumps({"Horario": "9-14"})
    s.execute(
        update(Contact).where(Contact.id == ana.id)
        .values(custom_fields=json.dumps({"Horario": "9-14", "INTERES": "Impresoras"}))
        .execution_options(synchronize_session=False)
    )

    assert aplicar(s, plan, actor_email="script:test", lote=1) == 3
    assert _campos(s, dani) == {"Horario": "9-14", "product_interest": "DTF"}
    assert _campos(s, ana) == {
        "Horario": "9-14", "INTERES": "Impresoras", "product_interest": "DTF",
    }
    assert _campos(s, carla) == {"product_interest": "DTF"}
    assert _campos(s, berta) == {"product_interest": "Otra"}
    assert _campos(s, eva) == {"Horario": "9-14"}
    assert s.get(Contact, fede.id).custom_fields == "texto suelto"
    rastro = s.scalar(select(AuditLog).where(
        AuditLog.action == Action.CONTACT_CUSTOM_FIELDS_RESTORED,
        AuditLog.target_id == ana.id,
    ))
    assert json.loads(rastro.metadata_json) == {
        "claves": ["product_interest"], "origen": {"product_interest": FORMULARIO},
    }
    assert rastro.actor_email == "script:test"

    # Idempotente: otra pasada no encuentra nada.
    assert planificar(s).reposiciones == []


def test_script_probar_no_escribe_y_apply_repone(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    import sys  # noqa: PLC0415

    from scripts import reponer_campos_personalizados as script  # noqa: PLC0415

    s = session
    form = _formulario(s)
    ana = _contacto(s, "Ana", {"Horario": "9-14"})
    _respuesta(s, form, ana, "DTF", T0)
    s.commit()
    monkeypatch.setattr(script, "get_engine", lambda: s.get_bind())

    monkeypatch.setattr(sys, "argv", ["reponer", "--probar"])
    assert script.main() == 0
    salida = capsys.readouterr().out
    assert "product_interest: 1" in salida
    assert "Nada escrito" in salida
    assert _campos(s, ana) == {"Horario": "9-14"}

    monkeypatch.setattr(sys, "argv", ["reponer", "--apply"])
    assert script.main() == 0
    assert "repuestos en 1 contactos" in capsys.readouterr().out
    assert _campos(s, ana) == {"Horario": "9-14", "product_interest": "DTF"}
