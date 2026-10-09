"""ERP · Cuadre — «Lead sin contactar» (respuesta a leads · Fase 1)."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.cuadre import checks_mysql as cm
from app.erp.cuadre.config import normalizar_config
from app.erp.cuadre.contexto import Contexto
from app.models.crm import (
    ActivityEvent,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    Note,
    User,
)
from app.models.leads import LeadClassification
from app.models.web_forms import FormSubmission, WebForm
from tests._test_helpers import seed_test_users

AHORA = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


@pytest.fixture()
def s() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        seed_test_users(session)
        yield session
    Base.metadata.drop_all(engine)


def _ctx(s: Session) -> Contexto:
    return Contexto(s, ahora=AHORA, config=normalizar_config({}))


def _lead_web(s: Session, email: str, *, hace: timedelta, **over) -> Contact:
    c = Contact(first_name=email.split("@")[0], email=email,
                origin_account_id="web_form:pimpam:es", origin="Formulario web · pimpam",
                **over)
    s.add(c)
    s.flush()
    c.created_at = AHORA - hace
    s.flush()
    return c


def _correo(s: Session, contact: Contact, *, cuando: datetime, acuse: bool = False) -> EmailMessage:
    user_id = s.scalar(select(User.id).where(User.email == "admin@example.com"))
    hilo = EmailThread(contact_id=contact.id, initiated_by_user_id=user_id,
                       gmail_thread_id=f"t-{contact.id[:6]}", gmail_account_user_id=user_id,
                       subject="x", first_message_at=cuando, last_message_at=cuando)
    s.add(hilo)
    s.flush()
    m = EmailMessage(thread_id=hilo.id, gmail_message_id=f"m-{contact.id[:6]}",
                     gmail_account_user_id=user_id, direction=EmailDirection.OUTBOUND,
                     from_email="info@pimpam-vending.com",
                     to_emails_json=json.dumps([contact.email]),
                     subject="x", sent_at=cuando, contact_id=contact.id)
    s.add(m)
    s.flush()
    m.created_at = cuando
    if acuse:
        s.add(ActivityEvent(
            contact_id=contact.id, system="crm", account_id="emails",
            external_id=f"email:{m.id}:email.sent_from_crm", event_type="email.sent_from_crm",
            subject="Acuse", metadata_json=json.dumps({"origen": "acuse_formulario_web"}),
            occurred_at=cuando, synced_at=cuando,
        ))
    s.flush()
    return m


def _ids(hallazgos) -> set[str]:
    return {h.entidad_id for h in hallazgos}


def test_lead_sin_ningun_correo_tras_48_horas(s: Session) -> None:
    sin_nada = _lead_web(s, "isabella@glowbtl.mx", hace=timedelta(days=3))
    con_acuse = _lead_web(s, "acuse@ejemplo.es", hace=timedelta(days=3))
    _correo(s, con_acuse, cuando=AHORA - timedelta(days=3), acuse=True)
    contestado = _lead_web(s, "ok@ejemplo.es", hace=timedelta(days=3))
    _correo(s, contestado, cuando=AHORA - timedelta(days=2))
    reciente = _lead_web(s, "hoy@ejemplo.es", hace=timedelta(hours=20))
    spam = _lead_web(s, "spam@leads.com", hace=timedelta(days=3), lead_is_spam=True)
    _lead_web(s, "viejo@ejemplo.es", hace=timedelta(days=60))         # fuera de la ventana
    baja = _lead_web(s, "baja@ejemplo.es", hace=timedelta(days=3), is_active=False)
    s.commit()

    hallazgos = list(cm.lead_sin_contactar(_ctx(s)))
    assert _ids(hallazgos) == {sin_nada.id, con_acuse.id}
    assert all(h.severidad is None for h in hallazgos)           # la de la comprobación: media
    isabella = next(h for h in hallazgos if h.entidad_id == sin_nada.id)
    assert "pimpam-vending.com" in isabella.detalle and "acuse" in isabella.detalle
    assert isabella.enlace == f"/contacts/{sin_nada.id}"
    assert isabella.huella_datos["lead_at"]
    assert {reciente.id, spam.id, contestado.id, baja.id} & _ids(hallazgos) == set()


def test_lead_de_agile_por_su_fecha_real(s: Session) -> None:
    klaus = Contact(first_name="Klaus", email="klaus@druck.de", origin_account_id="agilecrm:acc",
                    lead_interest="consumibles", lead_is_spam=False)
    s.add(klaus)
    s.flush()
    fila = LeadClassification(contact_id=klaus.id, source="agilecrm", source_ref="nota-1",
                              lead_at=AHORA - timedelta(days=4), language="de",
                              interest="consumibles", is_spam=False, confidence=0.7,
                              status="sin_plantilla")
    s.add(fila)
    s.flush()
    fila.created_at = AHORA - timedelta(days=1)
    s.commit()
    hallazgos = list(cm.lead_sin_contactar(_ctx(s)))
    assert _ids(hallazgos) == {klaus.id}
    assert "AgileCRM" in hallazgos[0].detalle and hallazgos[0].datos["interes"] == "consumibles"
    # Un correo de verdad después del lead lo quita.
    _correo(s, klaus, cuando=AHORA - timedelta(days=2))
    s.commit()
    assert list(cm.lead_sin_contactar(_ctx(s))) == []


def test_envios_y_notas_sin_clasificar_tambien_cuentan(s: Session) -> None:
    """La red de seguridad no depende del workflow (apagado, tope, IA): un
    envío de formulario de un contacto antiguo y una nota «form note» de Agile
    sin clasificar se listan por su fecha real. Una nota que no es de
    formulario, no."""
    admin = s.scalar(select(User.id).where(User.email == "admin@example.com"))
    form = WebForm(slug="pimpam-contacto-es", name="Pimpam ES", brand="pimpam", language="es",
                   created_by_user_id=admin, assignment_mode="none")
    s.add(form)
    s.flush()
    antiguo = _lead_web(s, "antiguo@ejemplo.es", hace=timedelta(days=200))   # fuera de ventana
    s.add(FormSubmission(form_id=form.id, contact_id=antiguo.id,
                         raw_payload_json=json.dumps({"message": "Quiero una máquina"}),
                         is_spam=False, created_at=AHORA - timedelta(days=3)))
    con_nota = Contact(first_name="Nota", email="nota@druck.de", origin_account_id="agilecrm:acc")
    sin_formulario = Contact(first_name="Otra", email="otra@druck.de",
                             origin_account_id="agilecrm:acc")
    s.add_all([con_nota, sin_formulario])
    s.flush()
    s.add(Note(contact_id=con_nota.id, body="form note\n\nWir suchen einen UV-Drucker.",
               external_system="agilecrm", external_account_id="acc",
               external_created_at=AHORA - timedelta(days=4), source="agile:timeline"))
    s.add(Note(contact_id=sin_formulario.id, body="Llamada: pide catálogo",
               external_system="agilecrm", external_account_id="acc",
               external_created_at=AHORA - timedelta(days=4), source="agile:timeline"))
    s.commit()

    hallazgos = {h.entidad_id: h for h in cm.lead_sin_contactar(_ctx(s))}
    assert set(hallazgos) == {antiguo.id, con_nota.id}
    assert "hace 3 día(s)" in hallazgos[antiguo.id].detalle
    assert "pimpam-vending.com" in hallazgos[antiguo.id].detalle
    assert "AgileCRM" in hallazgos[con_nota.id].detalle
