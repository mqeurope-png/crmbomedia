"""Captura de SALIDA universal (10/10/2026).

Del 20/07 al 10/10/2026 todo correo enviado desde una dirección sin registrar
en `user_email_aliases` se descartó en silencio: la captura de salida
filtraba por alias mientras la de entrada guardaba hasta los boletines. El
08/10 Bart contestó a Marc Moll (marc.moll@moll.team) desde
bart@artisjet-printers.eu con una oferta completa y la ficha decía «Aún no
has enviado emails a este contacto».

Ahora lo que está en SENT se guarda siempre: el contacto se casa por el
destinatario, los alias solo atribuyen, y el relleno es relanzable sin
duplicar. Gmail está simulado: nada sale a la red.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.session import get_session
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.backfill_universal import (
    run_backfill_universal,
    run_universal_job,
)
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    GmailPubsubWatch,
    User,
    UserEmailAlias,
    UserEmailAliasPref,
    UserRole,
)
from app.services.email_aliases import (
    active_alias_map,
    personal_mailbox_filter,
    thread_is_visible,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)

ALIAS_REGISTRADO = "norma@bomedia.net"          # de user@example.com (user_email_aliases)
ENVIAR_COMO_DE_BART = "bart@artisjet-printers.eu"   # «enviar como» del manager, sin registrar
SIN_REGISTRAR = "sales@mqeurope.com"             # ni alias ni «enviar como»
MARC = "marc.moll@moll.team"
ASUNTO_MARC = "Informationen & Preise zu unseren UV-LED-Druckern"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        admin = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        user = seed.scalar(select(User.id).where(User.role == UserRole.USER))
        manager = seed.scalar(select(User.id).where(User.role == UserRole.MANAGER))
        # La cuenta Gmail de la organización la conectó el admin: es «el buzón».
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.add(UserEmailAlias(user_id=user, alias_email=ALIAS_REGISTRADO, active=True))
        seed.add(UserEmailAliasPref(user_id=manager, alias_email=ENVIAR_COMO_DE_BART,
                                    is_allowed=True, is_default=True))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _uid(session: Session, role: UserRole) -> str:
    uid = session.scalar(select(User.id).where(User.role == role))
    assert uid
    return uid


def _raw(
    mid: str, thread_id: str, *, from_addr: str, to: str, cc: str | None = None,
    subject: str = ASUNTO_MARC, labels: tuple[str, ...] = ("SENT",),
    date: str = "Thu, 08 Oct 2026 11:32:00 +0200",
) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    return {
        "id": mid, "threadId": thread_id, "snippet": "Gerne senden wir Ihnen…",
        "labelIds": list(labels),
        "payload": {
            "headers": headers, "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Angebot").decode()},
        },
    }


def _persistir(session: Session, *, user_id: str, raw: dict[str, Any]) -> EmailMessage | None:
    return gmail_service._persist_message(
        session, user_id=user_id, raw=raw, gmail_thread_id=raw["threadId"],
        alias_map=active_alias_map(session), emit_activity=False,
    )


def _cuantos(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(EmailMessage)))


class _GmailFalso:
    """Un buzón con los mensajes que se le den, por label."""

    def __init__(self, por_label: dict[str, list[dict[str, Any]]]) -> None:
        self.por_label = por_label
        self.mensajes = {m["id"]: m for msgs in por_label.values() for m in msgs}

    def list_messages(self, *, query: str, page_size: int = 100,
                      page_token: str | None = None,
                      label_ids: list[str] | None = None) -> dict[str, Any]:
        label = (label_ids or ["INBOX"])[0]
        return {"messages": [{"id": m["id"], "threadId": m["threadId"]}
                             for m in self.por_label.get(label, [])]}

    def get_message(self, mid: str) -> dict[str, Any]:
        return self.mensajes[mid]


# --- la captura ---------------------------------------------------------------


def test_enviado_desde_direccion_no_registrada_se_guarda_atribuido_al_buzon(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m1", "t1", from_addr=SIN_REGISTRAR, to="alguien@fuera.com"))
        s.commit()
        assert msg is not None
        assert msg.direction == EmailDirection.OUTBOUND
        assert msg.from_email == SIN_REGISTRAR
        assert msg.created_by_user_id == buzon              # el usuario de cuyo buzón salió
        assert msg.delivered_to is None and msg.contact_id is None
        hilo = s.get(EmailThread, msg.thread_id)
        assert hilo is not None and hilo.initiated_by_user_id == buzon
        assert hilo.has_unread_replies is False


def test_el_correo_a_marc_moll_cuelga_de_su_ficha(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Caso de aceptación: la oferta del 08/10 desde bart@artisjet-printers.eu
    aparece en la ficha de marc.moll@moll.team, atribuida a quien tiene esa
    dirección entre sus «enviar como»."""
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        bart = _uid(s, UserRole.MANAGER)
        marc = Contact(first_name="Marc", last_name="Moll", email=MARC)
        s.add(marc)
        s.flush()
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-marc", "t-marc", from_addr=ENVIAR_COMO_DE_BART, to=f"Marc Moll <{MARC}>"))
        s.commit()
        assert msg is not None
        assert msg.contact_id == marc.id                    # por el destinatario
        assert msg.created_by_user_id == bart                # «enviar como» de Bart
        assert msg.subject == ASUNTO_MARC and msg.from_email == ENVIAR_COMO_DE_BART
        hilo = s.get(EmailThread, msg.thread_id)
        assert hilo is not None and hilo.contact_id == marc.id
        assert hilo.initiated_by_user_id == bart
        marc_id = marc.id

    # Lo que pinta la pestaña Emails de la ficha.
    r = http.get(f"/api/emails/threads?contact_id={marc_id}", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    asuntos = [t["subject"] for t in r.json()["items"]]
    assert ASUNTO_MARC in asuntos


def test_el_contacto_tambien_se_casa_por_cc_y_sin_contacto_se_guarda_igual(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        eva = Contact(first_name="Eva", email="eva@cliente.com")
        s.add(eva)
        s.flush()
        con_cc = _persistir(s, user_id=buzon, raw=_raw(
            "m-cc", "t-cc", from_addr=SIN_REGISTRAR, to="otro@fuera.com", cc="eva@cliente.com"))
        desconocido = _persistir(s, user_id=buzon, raw=_raw(
            "m-x", "t-x", from_addr=SIN_REGISTRAR, to="nadie@desconocido.com"))
        s.commit()
        assert con_cc is not None and con_cc.contact_id == eva.id
        assert desconocido is not None and desconocido.contact_id is None
        assert desconocido.direction == EmailDirection.OUTBOUND
        assert _cuantos(s) == 2


def _watch(session: Session, user_id: str) -> None:
    session.add(GmailPubsubWatch(
        user_id=user_id, history_id=1, watch_expires_at=datetime.now(UTC) + timedelta(days=6),
        last_renewed_at=datetime.now(UTC), topic_name="projects/x/topics/y",
    ))


def test_un_enviado_ya_guardado_bajo_otro_usuario_no_se_duplica(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hay un solo buzón: un envío desde el compositor (o un acuse) se guarda
    bajo el usuario que lo envió con el id que devuelve Gmail, y la copia en
    SENT que ve el push (cuenta del admin) trae ese MISMO id. Ni el push ni
    el relleno lo guardan otra vez."""
    ahora = datetime(2026, 10, 8, 9, 32, tzinfo=UTC)
    with session_factory() as s:
        admin = _uid(s, UserRole.ADMIN)
        manager = _uid(s, UserRole.MANAGER)
        hilo = EmailThread(
            contact_id=None, initiated_by_user_id=manager, gmail_thread_id="t-comp",
            gmail_account_user_id=manager, subject=ASUNTO_MARC, participants_json="[]",
            first_message_at=ahora, last_message_at=ahora, message_count=1,
        )
        s.add(hilo)
        s.flush()
        s.add(EmailMessage(
            thread_id=hilo.id, gmail_message_id="gmsg-1", gmail_account_user_id=manager,
            direction=EmailDirection.OUTBOUND, from_email=ENVIAR_COMO_DE_BART,
            to_emails_json=json.dumps([MARC]), subject=ASUNTO_MARC, sent_at=ahora,
            created_by_user_id=manager, imported_via="sent_from_crm",
        ))
        _watch(s, admin)
        s.commit()

    class _Falso:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {"history": [{"messagesAdded": [{"message": {
                "id": "gmsg-1", "threadId": "t-comp", "labelIds": ["SENT"]}}]}]}

        def get_message(self, _mid: str) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("ya está guardado: no debía pedirse a Gmail")

    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", _Falso)
    with session_factory() as s:
        assert gmail_service.process_history(
            s, user_id=_uid(s, UserRole.ADMIN), new_history_id=200) == 0
        s.commit()
        assert _cuantos(s) == 1

    # El relleno desde la cuenta del admin lo cuenta como dedupe, no lo escribe.
    with session_factory() as s:
        falso = _GmailFalso({"SENT": [_raw(
            "gmsg-1", "t-comp", from_addr=ENVIAR_COMO_DE_BART, to=MARC)]})
        with patch.object(gmail_service, "_client_for", return_value=falso):
            informe = run_backfill_universal(
                s, user_id=_uid(s, UserRole.ADMIN), since=datetime(2026, 10, 1).date(),
                until=datetime(2026, 10, 10).date(), labels=("SENT",),
            )
        s.commit()
        assert informe.skipped_dedupe == 1 and informe.outbound == 0
        assert _cuantos(s) == 1


def test_correo_interno_a_un_alias_de_la_casa_lo_ve_su_dueno(
    session_factory: sessionmaker,
) -> None:
    """Bart (sin registrar) escribe a norma@bomedia.net (alias registrado):
    Gmail lo etiqueta SENT + INBOX. Es un enviado, pero Norma tiene que verlo
    en su bandeja, como correo nuevo."""
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-int", "t-int", from_addr=ENVIAR_COMO_DE_BART, to=ALIAS_REGISTRADO,
            labels=("SENT", "INBOX")))
        s.commit()
        assert msg is not None and msg.direction == EmailDirection.OUTBOUND
        assert msg.delivered_to == ALIAS_REGISTRADO
        assert msg.created_by_user_id == _uid(s, UserRole.MANAGER)
        hilo = s.get(EmailThread, msg.thread_id)
        assert hilo is not None and hilo.has_unread_replies is True
        norma = s.get(User, _uid(s, UserRole.USER))
        assert thread_is_visible(s, norma, hilo) is True


def test_el_comercial_ve_su_respuesta_capturada_en_un_hilo_que_abrio_otro(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        entrada = _persistir(s, user_id=buzon, raw=_raw(
            "m-in", "t-hilo", from_addr=MARC, to=ALIAS_REGISTRADO, labels=("INBOX",),
            subject="Anfrage UV-Drucker"))
        assert entrada is not None and entrada.direction == EmailDirection.INBOUND
        hilo = s.get(EmailThread, entrada.thread_id)
        assert hilo is not None and hilo.initiated_by_user_id == buzon
        # Bart contesta desde su «enviar como», en el mismo hilo (SENT).
        respuesta = _persistir(s, user_id=buzon, raw=_raw(
            "m-out", "t-hilo", from_addr=ENVIAR_COMO_DE_BART, to=MARC))
        s.commit()
        assert respuesta is not None
        assert respuesta.created_by_user_id == _uid(s, UserRole.MANAGER)
        bart = s.get(User, _uid(s, UserRole.MANAGER))
        assert thread_is_visible(s, bart, hilo) is True
        en_su_bandeja = set(s.scalars(
            select(EmailThread.id).where(personal_mailbox_filter(s, bart))))
        assert hilo.id in en_su_bandeja


def test_una_direccion_enviar_como_compartida_se_queda_en_la_cuenta_de_la_organizacion(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        for rol in (UserRole.USER, UserRole.MANAGER):
            s.add(UserEmailAliasPref(user_id=_uid(s, rol), alias_email="info@mboprinters.com",
                                     is_allowed=True))
        s.commit()
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-marca", "t-marca", from_addr="info@mboprinters.com", to=MARC))
        s.commit()
        assert msg is not None and msg.created_by_user_id == buzon


def test_el_relleno_solo_de_sent_no_necesita_alias_registrados(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        for fila in s.scalars(select(UserEmailAlias)):
            s.delete(fila)
        s.commit()
        buzon = _uid(s, UserRole.ADMIN)
        falso = _GmailFalso({"SENT": [_raw("s-1", "t-1", from_addr=SIN_REGISTRAR, to=MARC)]})
        with patch.object(gmail_service, "_client_for", return_value=falso):
            informe = run_backfill_universal(
                s, user_id=buzon, since=datetime(2026, 10, 1).date(),
                until=datetime(2026, 10, 10).date(), labels=("SENT",))
            s.commit()
            assert informe.outbound == 1
            # La entrada sí los necesita: su gate no sabría qué es nuestro.
            with pytest.raises(ValueError, match="alias"):
                run_backfill_universal(
                    s, user_id=buzon, since=datetime(2026, 10, 1).date(),
                    until=datetime(2026, 10, 10).date())


def test_el_relleno_lanzado_dos_veces_sobre_el_mismo_tramo_no_duplica(
    session_factory: sessionmaker,
) -> None:
    falso = _GmailFalso({"SENT": [
        _raw("s-1", "t-1", from_addr=ENVIAR_COMO_DE_BART, to=MARC),
        _raw("s-2", "t-2", from_addr=SIN_REGISTRAR, to="alguien@fuera.com",
             subject="Presupuesto", date="Fri, 09 Oct 2026 09:00:00 +0200"),
    ]})
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        s.add(Contact(first_name="Marc", email=MARC))
        s.commit()
        with patch.object(gmail_service, "_client_for", return_value=falso):
            primero = run_backfill_universal(
                s, user_id=buzon, since=datetime(2026, 10, 1).date(),
                until=datetime(2026, 10, 10).date(), labels=("SENT",))
            s.commit()
            segundo = run_backfill_universal(
                s, user_id=buzon, since=datetime(2026, 10, 1).date(),
                until=datetime(2026, 10, 10).date(), labels=("SENT",))
            s.commit()
        assert (primero.outbound, primero.imported_linked, primero.imported_orphan) == (2, 1, 1)
        assert primero.enviados_por_remitente == {ENVIAR_COMO_DE_BART: 1, SIN_REGISTRAR: 1}
        assert primero.enviados_por_usuario == {"manager@example.com": 1, "admin@example.com": 1}
        assert (segundo.outbound, segundo.skipped_dedupe) == (0, 2)
        assert _cuantos(s) == 2
        assert {m.imported_via for m in s.scalars(select(EmailMessage))} == {
            "historic_backfill_universal"
        }


def test_un_alias_registrado_sigue_igual_que_hoy(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        norma = _uid(s, UserRole.USER)
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-norma", "t-norma", from_addr=ALIAS_REGISTRADO, to="cliente@fuera.com"))
        s.commit()
        assert msg is not None and msg.direction == EmailDirection.OUTBOUND
        assert msg.created_by_user_id == norma                # el dueño del alias
        assert s.get(EmailThread, msg.thread_id).initiated_by_user_id == norma
        # Y la entrada sigue con su gate: un INBOX a nadie nuestro se descarta.
        assert _persistir(s, user_id=buzon, raw=_raw(
            "m-in", "t-in", from_addr="desconocido@fuera.com", to="nadie@otro.com",
            labels=("INBOX",))) is None


def test_el_push_captura_sent_sin_alias_incluso_sin_ningun_alias_registrado(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        _watch(s, buzon)
        # Sin alias registrados en absoluto: antes el push saltaba todo.
        for fila in s.scalars(select(UserEmailAlias)):
            s.delete(fila)
        s.commit()

    class _Falso:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int) -> dict[str, Any]:
            return {"history": [{"messagesAdded": [{"message": {
                "id": "rt-1", "threadId": "trt", "labelIds": ["SENT"]}}]}]}

        def get_message(self, _mid: str) -> dict[str, Any]:
            return _raw("rt-1", "trt", from_addr=SIN_REGISTRAR, to=MARC)

    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", _Falso)
    with session_factory() as s:
        importados = gmail_service.process_history(
            s, user_id=_uid(s, UserRole.ADMIN), new_history_id=200)
        s.commit()
        assert importados == 1
        msg = s.scalar(select(EmailMessage))
        assert msg is not None and msg.direction == EmailDirection.OUTBOUND
        assert msg.imported_via == "incoming_realtime"
        assert msg.created_by_user_id == _uid(s, UserRole.ADMIN)


# --- el relleno como job de worker-gmail ----------------------------------------


def _job(session: Session, *, dry_run: bool) -> GmailBackfillJob:
    job = GmailBackfillJob(
        mode=GmailBackfillMode.UNIVERSAL.value, status=GmailBackfillStatus.QUEUED.value,
        config_json=json.dumps({"since": "2026-07-15", "until": "2026-10-10",
                                "labels": ["SENT"], "dry_run": dry_run}),
    )
    session.add(job)
    session.commit()
    return job


def test_job_universal_en_seco_dice_que_recuperaria_y_de_verdad_lo_guarda(
    session_factory: sessionmaker,
) -> None:
    falso = _GmailFalso({"SENT": [
        _raw("s-1", "t-1", from_addr=ENVIAR_COMO_DE_BART, to=MARC),
        _raw("s-2", "t-2", from_addr=SIN_REGISTRAR, to="alguien@fuera.com",
             subject="Presupuesto", date="Fri, 09 Oct 2026 09:00:00 +0200"),
    ]})
    with session_factory() as s, patch.object(gmail_service, "_client_for", return_value=falso):
        seco = _job(s, dry_run=True)
        run_universal_job(s, seco)
        s.refresh(seco)
        assert seco.status == "completed", seco.error_summary
        resultado = json.loads(seco.result_json)
        assert resultado["dry_run"] is True and resultado["outbound"] == 2
        assert resultado["enviados_por_remitente"] == {ENVIAR_COMO_DE_BART: 1, SIN_REGISTRAR: 1}
        assert resultado["enviados_por_usuario"] == {"manager@example.com": 1,
                                                     "admin@example.com": 1}
        assert resultado["since"] == "2026-07-15" and resultado["labels"] == ["SENT"]
        assert _cuantos(s) == 0                              # en seco no escribe

        real = _job(s, dry_run=False)
        run_universal_job(s, real)
        s.refresh(real)
        assert real.status == "completed" and real.total_imported == 2
        assert _cuantos(s) == 2

        otra_vez = _job(s, dry_run=False)
        run_universal_job(s, otra_vez)
        s.refresh(otra_vez)
        assert (otra_vez.total_imported, otra_vez.total_skipped) == (0, 2)
        assert _cuantos(s) == 2


def test_endpoint_universal_crea_el_job_solo_para_admin(http: TestClient) -> None:
    hoy = datetime.now(UTC).date().isoformat()
    cuerpo = {"since": "2026-07-15", "until": hoy, "labels": ["SENT"], "dry_run": True}
    assert http.post("/api/admin/gmail/backfill/universal", json=cuerpo,
                     headers=auth_headers(http, "user")).status_code == 403
    r = http.post("/api/admin/gmail/backfill/universal", json=cuerpo,
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["mode"] == "universal" and job["status"] == "queued"
    assert job["config"] == {"since": "2026-07-15", "until": hoy,
                             "labels": ["SENT"], "dry_run": True, "dry_run_limit": 5000}
    # Aparece en la lista de jobs con los demás.
    listado = http.get("/api/admin/gmail/backfill", headers=auth_headers(http, "admin")).json()
    assert [j["id"] for j in listado] == [job["id"]]
    # Fechas al revés o futuras: 400.
    r = http.post("/api/admin/gmail/backfill/universal",
                  json={"since": "2026-10-10", "until": "2026-07-15"},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 400
    r = http.post("/api/admin/gmail/backfill/universal",
                  json={"since": "2026-07-15", "until": "2999-01-01"},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 400
