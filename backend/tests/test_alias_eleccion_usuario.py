"""Los alias que el usuario elige a mano ya no se pierden en cada sync (10/10/2026).

La cuenta de Google es una sola para toda la empresa y Gmail devuelve los 50 y
pico «enviar como» a cada usuario. El sync imponía en cada pasada «propio
visible, ajeno oculto salvo su predeterminado»: las secundarias que Bart
elegía a mano (`bart@artisjet-printers.eu`, `bart@mqeurope.com`) se apagaban
solas y su alias propio (`bart@bomedia.net`) no se podía apagar. Ahora la
elección del usuario (`user_opted_in`) manda sobre esa regla.

Tres cosas distintas que aquí se comprueban por separado: que el usuario esté
activo, que una dirección sea nuestra (se captura) y que el usuario pueda
enviar como ella. Y la definición de «míos» de la Bandeja: lo que escribió,
los hilos donde participó, lo que entra o sale por sus remitentes marcados y
todo lo de los contactos de los que es propietario.

Gmail está simulado: nada sale a la red. Aquí Bart es el `manager`
(manager@example.com), Norma la `user` (user@example.com) y el admin conectó
la cuenta Google de la organización.
"""
from __future__ import annotations

import base64
import importlib.util
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.session import get_session
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.aliases import (
    normalizar_predeterminado,
    sync_all_active_users,
    sync_send_as_aliases,
)
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
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

BART = "manager@example.com"            # su `users.email`: el alias propio
NORMA = "user@example.com"
STREAMTEC = "bart@streamtec.es"          # el predeterminado actual de Bart
ARTISJET = "bart@artisjet-printers.eu"   # secundarias que Bart quiere
MQEUROPE = "bart@mqeurope.com"
NORMA_FLUX = "norma@fluxlasers.es"       # dirección de la empresa (entrante)
INFO_MBO = "info@mboprinters.com"        # dirección de una web, remitente de Bart
INFO_BOMEDIA = "info@bomedia.net"        # el «enviar como» por defecto de la cuenta


# --- infraestructura -----------------------------------------------------------


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
        seed_org_google_integration(seed, connected_by_user_id=admin)
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


def _prefs(session: Session, user_id: str) -> dict[str, UserEmailAliasPref]:
    return {
        r.alias_email: r
        for r in session.scalars(
            select(UserEmailAliasPref).where(UserEmailAliasPref.user_id == user_id)
        )
    }


def _pref(session: Session, user_id: str, alias: str, *, allowed: bool,
          default: bool = False, opted: bool | None = None) -> UserEmailAliasPref:
    row = UserEmailAliasPref(user_id=user_id, alias_email=alias, is_allowed=allowed,
                             is_default=default, user_opted_in=opted)
    session.add(row)
    return row


class _GmailFalso:
    """La cuenta de la organización: los mismos «enviar como» para todos."""

    def __init__(self, aliases: list[dict[str, Any]]) -> None:
        self._aliases = aliases

    def list_send_as_aliases(self) -> list[dict[str, Any]]:
        return self._aliases


def _gmail(*emails: str, default: str | None = None) -> _GmailFalso:
    return _GmailFalso([
        {"send_as_email": e, "display_name": e.split("@")[0].title(),
         "is_primary": e == default, "is_default": e == default,
         "verification_status": "accepted"}
        for e in emails
    ])


TODOS = (INFO_BOMEDIA, BART, NORMA, STREAMTEC, ARTISJET, MQEUROPE, INFO_MBO, NORMA_FLUX)


def _sync(session: Session, user_id: str, gmail: _GmailFalso | None = None) -> None:
    with patch.object(gmail_service, "_client_for", return_value=gmail or _gmail(*TODOS)):
        sync_send_as_aliases(session, user_id=user_id)
    session.commit()


def _sin_predeterminado_imposible(session: Session) -> None:
    assert not session.scalar(
        select(UserEmailAliasPref.id).where(
            UserEmailAliasPref.is_default.is_(True), UserEmailAliasPref.is_allowed.is_(False)
        ).limit(1)
    )


# --- la sincronización respeta la elección ----------------------------------------


def test_un_alias_ajeno_elegido_por_el_usuario_sobrevive_al_sync(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        _pref(s, bart, STREAMTEC, allowed=True, default=True)             # su predeterminado
        _pref(s, bart, ARTISJET, allowed=True, opted=True)                # elegido a mano
        _pref(s, bart, MQEUROPE, allowed=True)                            # encendido sin elección
        s.commit()
        _sync(s, bart)
        filas = _prefs(s, bart)
        assert filas[ARTISJET].is_allowed is True                         # antes se apagaba solo
        assert filas[STREAMTEC].is_allowed is True and filas[STREAMTEC].is_default is True
        assert filas[MQEUROPE].is_allowed is False      # sin pronunciarse: regla de siempre
        assert filas[BART].is_allowed is True                             # propio, sin pronunciarse
        _sin_predeterminado_imposible(s)


def test_rechazar_el_alias_propio_hace_que_no_vuelva_tras_el_sync(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        _pref(s, bart, BART, allowed=False, opted=False)   # no quiere escribir desde ahí
        _pref(s, bart, STREAMTEC, allowed=True, default=True, opted=True)
        s.commit()
        _sync(s, bart)
        _sync(s, bart)                      # dos pasadas, por si acaso
        filas = _prefs(s, bart)
        assert filas[BART].is_allowed is False and filas[BART].is_default is False
        assert filas[STREAMTEC].is_default is True


def test_sin_pronunciarse_sigue_la_regla_de_siempre(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma)                     # primera pasada: sin filas previas
        filas = _prefs(s, norma)
        assert filas[NORMA].is_allowed is True                            # propio → visible
        assert filas[NORMA].is_default is True                            # y predeterminado
        for ajeno in (BART, STREAMTEC, ARTISJET, MQEUROPE, INFO_MBO, INFO_BOMEDIA):
            assert filas[ajeno].is_allowed is False                       # ajenos → ocultos
            assert filas[ajeno].user_opted_in is None                     # nadie se ha pronunciado


def test_norma_no_ve_ningun_alias_de_bart_que_no_haya_elegido(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        for alias in (STREAMTEC, ARTISJET, MQEUROPE):
            _pref(s, bart, alias, allowed=True, default=(alias == STREAMTEC), opted=True)
        s.commit()
        _sync(s, bart)
        _sync(s, norma)
    with patch.object(gmail_service, "_client_for", return_value=_gmail(*TODOS)):
        suyos = http.get("/api/emails/my-aliases", headers=auth_headers(http, "user")).json()
        de_bart = http.get("/api/emails/my-aliases", headers=auth_headers(http, "manager")).json()
    assert [a["send_as_email"] for a in suyos] == [NORMA]
    assert sorted(a["send_as_email"] for a in de_bart) == sorted(
        [BART, STREAMTEC, ARTISJET, MQEUROPE])


def test_un_alias_que_desaparece_de_gmail_queda_apagado_y_su_fila_se_conserva(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        _pref(s, bart, "viejo@bomedia.net", allowed=True, default=True, opted=True)
        _pref(s, bart, STREAMTEC, allowed=True, opted=True)
        s.commit()
        _sync(s, bart, _gmail(BART, STREAMTEC))     # «viejo» ya no está en Gmail
        filas = _prefs(s, bart)
        viejo = filas["viejo@bomedia.net"]
        assert (viejo.is_allowed, viejo.is_default, viejo.user_opted_in) == (False, False, True)
        assert filas[STREAMTEC].is_default is True   # el default pasa al que él eligió
        _sin_predeterminado_imposible(s)


def test_no_queda_ningun_predeterminado_que_no_se_pueda_usar(
    session_factory: sessionmaker,
) -> None:
    """El estado de producción: varios usuarios con info@bomedia.net
    `is_default=1` e `is_allowed=0`. El sync lo deshace y el default pasa al
    alias propio; sembrar el default de Gmail sobre un ajeno oculto ya no pasa."""
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _pref(s, norma, INFO_BOMEDIA, allowed=False, default=True)  # el predeterminado imposible
        _pref(s, norma, NORMA, allowed=True)
        s.commit()
        _sync(s, norma, _gmail(*TODOS, default=INFO_BOMEDIA))
        filas = _prefs(s, norma)
        assert (filas[INFO_BOMEDIA].is_allowed, filas[INFO_BOMEDIA].is_default) == (False, False)
        assert (filas[NORMA].is_allowed, filas[NORMA].is_default) == (True, True)
        _sin_predeterminado_imposible(s)

    # La normalización, sola: un default apagado deja de serlo y, con varios,
    # se queda el propio.
    a = UserEmailAliasPref(user_id="u", alias_email="x@b.net", is_allowed=False, is_default=True)
    b = UserEmailAliasPref(user_id="u", alias_email="yo@b.net", is_allowed=True, is_default=True)
    c = UserEmailAliasPref(user_id="u", alias_email="z@b.net", is_allowed=True, is_default=True)
    normalizar_predeterminado([a, b, c], user_email="yo@b.net")
    assert (a.is_default, b.is_default, c.is_default) == (False, True, False)
    # Sin predeterminado: lo que el usuario eligió gana al de Gmail, y el de
    # Gmail (si está visible) gana al alias propio.
    elegido = UserEmailAliasPref(user_id="u", alias_email="z@b.net", is_allowed=True,
                                 is_default=False, user_opted_in=True)
    propio = UserEmailAliasPref(user_id="u", alias_email="yo@b.net", is_allowed=True,
                                is_default=False)
    gmail = UserEmailAliasPref(user_id="u", alias_email="info@b.net", is_allowed=True,
                               is_default=False)
    normalizar_predeterminado([elegido, propio, gmail], user_email="yo@b.net",
                              predeterminado_de_gmail="info@b.net")
    assert (elegido.is_default, propio.is_default, gmail.is_default) == (True, False, False)
    elegido.user_opted_in, elegido.is_default = None, False
    normalizar_predeterminado([elegido, propio, gmail], user_email="yo@b.net",
                              predeterminado_de_gmail="info@b.net")
    assert (elegido.is_default, propio.is_default, gmail.is_default) == (False, False, True)


def test_el_default_de_gmail_no_pisa_lo_que_bart_eligio(session_factory: sessionmaker) -> None:
    """Bart tiene sus tres remitentes elegidos y ningún predeterminado (el
    viejo desapareció); Gmail dice que el default de la cuenta es su alias
    propio. El predeterminado pasa a uno de los elegidos, no al propio."""
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        for alias in (STREAMTEC, ARTISJET, MQEUROPE):
            _pref(s, bart, alias, allowed=True, opted=True)
        s.commit()
        _sync(s, bart, _gmail(*TODOS, default=BART))
        filas = _prefs(s, bart)
        assert filas[BART].is_allowed is True and filas[BART].is_default is False
        assert [a for a in (STREAMTEC, ARTISJET, MQEUROPE) if filas[a].is_default] == [ARTISJET]
        _sin_predeterminado_imposible(s)


# --- la pantalla escribe la elección -----------------------------------------------


def test_la_api_registra_la_eleccion_y_el_sync_la_respeta(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        _sync(s, bart)                      # el estado que deja el sync hoy
        filas = _prefs(s, bart)
        assert filas[BART].is_allowed and filas[ARTISJET].is_allowed is False

    # Bart activa artisjet y mqeurope, pone streamtec de predeterminado y
    # desactiva su alias propio. El resto lo manda tal cual está.
    def _item(alias: str, allowed: bool, default: bool = False) -> dict[str, Any]:
        return {"alias_email": alias, "is_allowed": allowed, "is_default": default}

    cuerpo = {"preferences": [
        _item(BART, False), _item(STREAMTEC, True, True), _item(ARTISJET, True),
        _item(MQEUROPE, True), _item(INFO_BOMEDIA, False), _item(NORMA, False),
    ]}
    with patch.object(gmail_service, "_client_for", return_value=_gmail(*TODOS)):
        r = http.put("/api/emails/aliases/preferences", json=cuerpo,
                     headers=auth_headers(http, "manager"))
    assert r.status_code == 200, r.text
    por_email = {a["send_as_email"]: a for a in r.json()}
    assert por_email[BART]["user_pref_allowed"] is False
    assert por_email[BART]["user_pref_opted_in"] is False                 # rechazo registrado
    assert por_email[ARTISJET]["user_pref_opted_in"] is True              # elección registrada
    assert por_email[NORMA]["user_pref_opted_in"] is None     # estaba apagado: sin pronunciarse

    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        filas = _prefs(s, bart)
        assert (filas[BART].is_allowed, filas[BART].user_opted_in) == (False, False)
        assert (filas[STREAMTEC].is_allowed, filas[STREAMTEC].is_default,
                filas[STREAMTEC].user_opted_in) == (True, True, True)
        assert filas[MQEUROPE].user_opted_in is True
        assert filas[INFO_BOMEDIA].user_opted_in is None                  # ya estaba apagado
        # Una pasada del sync no cambia nada de lo elegido.
        _sync(s, bart)
        filas = _prefs(s, bart)
        assert {a: filas[a].is_allowed for a in (BART, STREAMTEC, ARTISJET, MQEUROPE)} == {
            BART: False, STREAMTEC: True, ARTISJET: True, MQEUROPE: True}
        assert filas[STREAMTEC].is_default is True
        _sin_predeterminado_imposible(s)


def test_desmarcar_el_alias_propio_antes_del_primer_sync_tambien_se_recuerda(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with patch.object(gmail_service, "_client_for", return_value=_gmail(*TODOS)):
        r = http.put("/api/emails/aliases/preferences",
                     json={"preferences": [
                         {"alias_email": BART, "is_allowed": False, "is_default": False},
                         {"alias_email": NORMA, "is_allowed": False, "is_default": False},
                     ]},
                     headers=auth_headers(http, "manager"))
    assert r.status_code == 200, r.text
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        filas = _prefs(s, bart)
        assert list(filas) == [BART]                      # el ajeno sin fila no crea nada
        assert (filas[BART].is_allowed, filas[BART].user_opted_in) == (False, False)
        _sync(s, bart)
        assert _prefs(s, bart)[BART].is_allowed is False  # el sync no lo vuelve a encender


def test_responder_y_el_acuse_solo_cuentan_los_remitentes_encendidos(
    session_factory: sessionmaker,
) -> None:
    """Con las filas apagadas permanentes, ni «Responder» ni el remitente del
    acuse pueden tratar como propia una dirección por tener fila."""
    from app.api.emails import _user_own_emails
    from app.services.web_forms.acuse import usuario_remitente

    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        _sync(s, bart)                  # una fila por alias; solo el propio encendido
        s.add(UserEmailAlias(user_id=bart, alias_email=STREAMTEC, active=True))
        s.commit()
        propias = _user_own_emails(s, s.get(User, bart))
        assert BART in propias and STREAMTEC in propias   # su correo, su remitente y su alias
        assert NORMA not in propias and INFO_BOMEDIA not in propias
        # Sin admin activo, el acuse busca quién tiene el alias ENCENDIDO (Bart
        # tiene la fila de info@mboprinters.com apagada por el sync).
        for admin in s.scalars(select(User).where(User.role == UserRole.ADMIN)):
            admin.is_active = False
        _pref(s, norma, INFO_MBO, allowed=True, opted=True)
        s.commit()
        assert usuario_remitente(s, INFO_MBO).id == norma


def test_tres_remitentes_en_el_desplegable_y_siguen_los_tres_tras_el_sync(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        for alias in (STREAMTEC, ARTISJET, MQEUROPE):
            _pref(s, bart, alias, allowed=True, default=(alias == STREAMTEC), opted=True)
        _pref(s, bart, BART, allowed=False, opted=False)
        s.commit()

    def _desplegable() -> list[str]:
        with patch.object(gmail_service, "_client_for", return_value=_gmail(*TODOS)):
            r = http.get("/api/emails/my-aliases", headers=auth_headers(http, "manager"))
        assert r.status_code == 200, r.text
        return [a["send_as_email"] for a in r.json()]

    assert _desplegable() == [STREAMTEC, ARTISJET, MQEUROPE]            # el predeterminado primero
    with session_factory() as s:
        _sync(s, _uid(s, UserRole.MANAGER))
    assert _desplegable() == [STREAMTEC, ARTISJET, MQEUROPE]
    # Y la pasada de todos los usuarios activos tampoco los toca.
    with session_factory() as s, patch.object(
        gmail_service, "_client_for", return_value=_gmail(*TODOS)
    ):
        sync_all_active_users(s)
    assert _desplegable() == [STREAMTEC, ARTISJET, MQEUROPE]


# --- dar de baja a una persona no da de baja sus direcciones ----------------------------


def _raw(mid: str, tid: str, *, from_addr: str, to: str, labels: tuple[str, ...] = ("INBOX",),
         subject: str = "Anfrage Laser") -> dict[str, Any]:
    return {
        "id": mid, "threadId": tid, "labelIds": list(labels), "snippet": "…",
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr}, {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Sat, 10 Oct 2026 10:00:00 +0200"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hallo").decode()},
        },
    }


def _persistir(session: Session, *, raw: dict[str, Any]) -> EmailMessage | None:
    return gmail_service._persist_message(
        session, user_id=_uid(session, UserRole.ADMIN), raw=raw, gmail_thread_id=raw["threadId"],
        alias_map=active_alias_map(session), emit_activity=False,
    )


def test_desactivar_a_norma_no_deja_de_capturar_el_correo_a_sus_direcciones(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        s.add(UserEmailAlias(user_id=norma, alias_email=NORMA_FLUX, active=True))
        cliente = Contact(first_name="Klaus", email="klaus@kunde.de")
        s.add(cliente)
        s.commit()
        cliente_id = cliente.id
    r = http.patch(f"/api/users/{norma}/deactivate", headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["is_active"] is False
    with session_factory() as s:
        assert NORMA_FLUX.lower() in active_alias_map(s)   # la dirección sigue siendo nuestra
        msg = _persistir(s, raw=_raw("in-1", "t-in-1", from_addr="klaus@kunde.de", to=NORMA_FLUX))
        s.commit()
        assert msg is not None and msg.direction == EmailDirection.INBOUND
        assert msg.delivered_to == NORMA_FLUX and msg.contact_id == cliente_id


def test_el_historial_de_un_usuario_dado_de_baja_sigue_visible(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Norma escribió a un cliente de Bart antes de la baja: su correo sigue en
    la ficha (para el admin y para Bart, el propietario) y en el timeline."""
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        bart = _uid(s, UserRole.MANAGER)
        s.add(UserEmailAlias(user_id=norma, alias_email=NORMA, active=True))
        cliente = Contact(first_name="Klaus", email="klaus@kunde.de", owner_user_id=bart)
        s.add(cliente)
        s.commit()
        cliente_id = cliente.id
        msg = _persistir(s, raw=_raw("out-1", "t-out-1", from_addr=NORMA, to="klaus@kunde.de",
                                     labels=("SENT",), subject="Angebot Laser"))
        s.commit()
        assert msg is not None and msg.created_by_user_id == norma
    assert http.patch(f"/api/users/{norma}/deactivate",
                      headers=auth_headers(http, "admin")).status_code == 200
    for rol in ("admin", "manager"):
        r = http.get(f"/api/emails/threads?contact_id={cliente_id}",
                     headers=auth_headers(http, rol))
        assert r.status_code == 200, r.text
        assert [t["subject"] for t in r.json()["items"]] == ["Angebot Laser"], rol
        tl = http.get(f"/api/contacts/{cliente_id}/timeline?types=email",
                      headers=auth_headers(http, rol))
        assert tl.status_code == 200, tl.text
        cuerpo = tl.json()
        eventos = cuerpo["items"] if isinstance(cuerpo, dict) else cuerpo
        assert any(e.get("type") == "email" for e in eventos), rol


# --- «míos» ------------------------------------------------------------------------


def _hilo(session: Session, *, iniciado_por: str, contacto: Contact | None,
          mensajes: list[dict[str, Any]], gid: str) -> EmailThread:
    ahora = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    admin = _uid(session, UserRole.ADMIN)
    hilo = EmailThread(
        contact_id=contacto.id if contacto else None, initiated_by_user_id=iniciado_por,
        gmail_thread_id=gid, gmail_account_user_id=admin, subject=f"Hilo {gid}",
        participants_json="[]", first_message_at=ahora, last_message_at=ahora,
        message_count=len(mensajes),
    )
    session.add(hilo)
    session.flush()
    for i, m in enumerate(mensajes):
        session.add(EmailMessage(
            thread_id=hilo.id, gmail_message_id=f"{gid}-{i}", gmail_account_user_id=admin,
            direction=m.get("direction", EmailDirection.INBOUND), from_email=m["from"],
            to_emails_json=f'["{m["to"]}"]', delivered_to=m.get("delivered_to"),
            created_by_user_id=m.get("created_by"), contact_id=contacto.id if contacto else None,
            subject=f"Hilo {gid}", sent_at=ahora, imported_via="incoming_realtime",
        ))
    session.flush()
    return hilo


def _mios(session: Session, user_id: str) -> set[str]:
    usuario = session.get(User, user_id)
    assert usuario is not None
    return set(session.scalars(
        select(EmailThread.id).where(personal_mailbox_filter(session, usuario))))


def test_mios_incluye_lo_que_entra_por_una_direccion_marcada_como_mi_remitente(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        admin = _uid(s, UserRole.ADMIN)
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        # La web es de la empresa (entrante registrada en la cuenta de la
        # organización); Bart la tiene marcada como remitente suyo.
        s.add(UserEmailAlias(user_id=admin, alias_email=INFO_MBO, active=True))
        _pref(s, bart, INFO_MBO, allowed=True, opted=True)
        _pref(s, bart, BART, allowed=True)
        _pref(s, norma, NORMA, allowed=True)
        hilo = _hilo(s, iniciado_por=admin, contacto=None, gid="web-mbo", mensajes=[
            {"from": "lead@kunde.de", "to": INFO_MBO, "delivered_to": INFO_MBO},
        ])
        s.commit()
        assert hilo.id in _mios(s, bart)
        assert hilo.id not in _mios(s, norma)
        assert thread_is_visible(s, s.get(User, bart), hilo) is True
        assert thread_is_visible(s, s.get(User, norma), hilo) is False
    r = http.get("/api/emails/threads", headers=auth_headers(http, "manager"))
    assert [t["subject"] for t in r.json()["items"]] == ["Hilo web-mbo"]
    r = http.get("/api/emails/threads", headers=auth_headers(http, "user"))
    assert r.json()["items"] == []


def test_marcar_la_direccion_de_otro_no_da_visibilidad_sobre_su_correo(
    session_factory: sessionmaker,
) -> None:
    """El endpoint de preferencias es de cada usuario y no está restringido:
    Norma puede marcar el correo de Bart o su alias registrado como remitente
    suyo, pero eso no le abre el correo de Bart en «míos»."""
    with session_factory() as s:
        admin = _uid(s, UserRole.ADMIN)
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        s.add(UserEmailAlias(user_id=bart, alias_email=STREAMTEC, active=True))
        _pref(s, norma, BART, allowed=True, opted=True)        # el correo de usuario de Bart
        _pref(s, norma, STREAMTEC, allowed=True, opted=True)   # un alias registrado a Bart
        _pref(s, norma, NORMA, allowed=True)
        a_bart = _hilo(s, iniciado_por=admin, contacto=None, gid="a-bart", mensajes=[
            {"from": "lead@kunde.de", "to": STREAMTEC, "delivered_to": STREAMTEC},
        ])
        de_bart = _hilo(s, iniciado_por=admin, contacto=None, gid="de-bart", mensajes=[
            {"from": BART, "to": "lead@kunde.de", "direction": EmailDirection.OUTBOUND,
             "created_by": bart},
        ])
        s.commit()
        assert a_bart.id not in _mios(s, norma) and de_bart.id not in _mios(s, norma)
        assert a_bart.id in _mios(s, bart) and de_bart.id in _mios(s, bart)
        assert thread_is_visible(s, s.get(User, norma), a_bart) is False


def test_mios_incluye_todo_el_correo_de_mis_contactos_aunque_lo_escribiera_otro(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        admin = _uid(s, UserRole.ADMIN)
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        lead = Contact(first_name="Marc", email="marc@kunde.de", owner_user_id=bart)
        s.add(lead)
        s.flush()
        _pref(s, norma, NORMA, allowed=True)
        # Norma contesta al lead de Bart desde su propia dirección.
        hilo = _hilo(s, iniciado_por=admin, contacto=lead, gid="lead-marc", mensajes=[
            {"from": "marc@kunde.de", "to": INFO_BOMEDIA, "delivered_to": None},
            {"from": NORMA, "to": "marc@kunde.de", "direction": EmailDirection.OUTBOUND,
             "created_by": norma},
        ])
        s.commit()
        assert hilo.id in _mios(s, bart)        # propietario del contacto
        assert hilo.id in _mios(s, norma)       # lo escribió ella
        assert thread_is_visible(s, s.get(User, bart), hilo) is True
    r = http.get("/api/emails/threads", headers=auth_headers(http, "manager"))
    assert [t["subject"] for t in r.json()["items"]] == ["Hilo lead-marc"]
    # También en la ficha del contacto, que filtra por visibilidad.
    with session_factory() as s:
        lead_id = s.scalar(select(Contact.id).where(Contact.email == "marc@kunde.de"))
    r = http.get(f"/api/emails/threads?contact_id={lead_id}", headers=auth_headers(http, "manager"))
    assert [t["subject"] for t in r.json()["items"]] == ["Hilo lead-marc"]


def test_lo_que_no_cumple_ninguna_regla_no_es_de_nadie_pero_esta_en_todos(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        admin = _uid(s, UserRole.ADMIN)
        bart = _uid(s, UserRole.MANAGER)
        norma = _uid(s, UserRole.USER)
        viewer = _uid(s, UserRole.VIEWER)
        _pref(s, bart, BART, allowed=True)
        _pref(s, norma, NORMA, allowed=True)
        sin_dueno = Contact(first_name="Nadie", email="nadie@kunde.de")  # sin propietario
        s.add(sin_dueno)
        s.flush()
        # Lo abrió un usuario que ya no cuenta (viewer), a una dirección que
        # nadie tiene registrada ni marcada.
        hilo = _hilo(s, iniciado_por=viewer, contacto=sin_dueno, gid="nadie", mensajes=[
            {"from": "nadie@kunde.de", "to": "ventas@bomedia.net",
             "delivered_to": "ventas@bomedia.net"},
        ])
        s.commit()
        for uid in (admin, bart, norma):
            assert hilo.id not in _mios(s, uid)
    for rol in ("admin", "manager", "user"):
        r = http.get("/api/emails/threads", headers=auth_headers(http, rol))
        assert r.json()["items"] == [], rol
    # «Todos» sigue siendo todo.
    r = http.get("/api/emails/threads?scope=team", headers=auth_headers(http, "admin"))
    assert [t["subject"] for t in r.json()["items"]] == ["Hilo nadie"]


# --- la migración 0133: los datos ----------------------------------------------------


def _migracion_0133() -> Any:
    ruta = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "20261010_0133_alias_eleccion_usuario.py")
    spec = importlib.util.spec_from_file_location("migracion_0133", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_corrige_el_predeterminado_imposible_y_siembra_a_bart(
    session_factory: sessionmaker,
) -> None:
    """La lógica de datos de la migración 0133 contra el esquema del ORM (la
    cadena entera de Alembic no se aplica sobre SQLite). Idempotente, y sin el
    usuario de Bart (el CI) no siembra nada."""
    mig = _migracion_0133()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        s.add(User(id=mig.BART_ID, email=mig.BART_EMAIL, full_name="Bart",
                   password_hash="no-es-un-hash", role=UserRole.MANAGER, is_active=True))
        # Norma: el predeterminado imposible de producción.
        _pref(s, norma, INFO_BOMEDIA, allowed=False, default=True)
        _pref(s, norma, NORMA, allowed=True)
        # Bart, tal y como lo dejó la pasada del 09/10 a las 17:24.
        _pref(s, mig.BART_ID, STREAMTEC, allowed=True, default=True)
        _pref(s, mig.BART_ID, ARTISJET, allowed=False)
        _pref(s, mig.BART_ID, mig.BART_EMAIL, allowed=True)
        s.commit()
    engine = session_factory.kw["bind"]
    ahora = datetime.now(UTC)
    for _ in range(2):                                      # dos veces: idempotente
        with engine.begin() as conn:
            mig._normalizar_predeterminados(conn, ahora)
            assert mig._sembrar_bart(conn, ahora) is True
    with session_factory() as s:
        de_norma = _prefs(s, _uid(s, UserRole.USER))
        assert (de_norma[INFO_BOMEDIA].is_default, de_norma[NORMA].is_default) == (False, True)
        de_bart = _prefs(s, mig.BART_ID)
        assert len(de_bart) == 4                            # mqeurope se creó; nada duplicado
        for alias in (STREAMTEC, ARTISJET, MQEUROPE):
            assert (de_bart[alias].is_allowed, de_bart[alias].user_opted_in) == (True, True), alias
        assert de_bart[STREAMTEC].is_default is True
        assert (de_bart[mig.BART_EMAIL].is_allowed, de_bart[mig.BART_EMAIL].is_default,
                de_bart[mig.BART_EMAIL].user_opted_in) == (False, False, False)
        _sin_predeterminado_imposible(s)
        s.delete(s.get(User, mig.BART_ID))
        s.commit()
    with engine.begin() as conn:
        assert mig._sembrar_bart(conn, ahora) is False
