"""El administrador gestiona los remitentes del «De» de cualquier usuario (28/10/2026).

Bart añadió cinco direcciones a Manel en Usuarios → «Alias de email
conectado» (`user_email_aliases`) pensando que eran remitentes. No lo son:
esas son direcciones de ENTRADA (el correo que llega a ellas se le atribuye).
El desplegable «De» sale de `user_email_alias_prefs`, y desde CRM-PERFIL el
comercial no puede tocarlo en Mi cuenta: lo gestiona el administrador, pero
la ficha de Usuarios no tenía dónde. Se arregló a mano con un UPDATE.

Ahora la ficha de Usuarios tiene el bloque «Remitentes» (`/api/users/{id}/
senders`), con la misma lógica que Mi cuenta: lo marcado es una elección
(`user_opted_in`) que el sync de Gmail respeta, un solo predeterminado, el
nombre visible se cambia sin tocar Gmail y todo queda en Auditoría.

Gmail está simulado: nada sale a la red. Bart es el `manager`, Norma la
`user` y el admin conectó la cuenta Google de la organización.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.session import get_session
from app.erp.cuadre.checks_textos import textos_cliente
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.service import GmailNotConnectedError
from app.main import app
from app.models.crm import (
    AuditLog,
    Base,
    User,
    UserEmailAlias,
    UserEmailAliasPref,
    UserRole,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)
from tests.test_alias_eleccion_usuario import (
    ARTISJET,
    BART,
    MQEUROPE,
    NORMA,
    STREAMTEC,
    _GmailFalso,
    _pref,
    _prefs,
    _sync,
    _uid,
)

VENTAS = "ventas@streamtec.es"
PEDIDOS = "pedidos@streamtec.es"
MANEL = "manel@bomedia.net"
#: Lo que Manel tiene desde el UPDATE a mano del 28/10.
MANEL_SEIS = (MANEL, "info@artisjet-spain.es", "info@boprint.net", "info@mbolasers.com",
              PEDIDOS, VENTAS)
NORMA_FLUX = "norma@fluxlasers.es"      # dirección de entrada; no es «enviar como»


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


class _Gmail(_GmailFalso):
    """La cuenta de la organización, que además envía (y apunta lo enviado)."""

    def __init__(self, aliases: list[dict[str, Any]]) -> None:
        super().__init__(aliases)
        self.enviados: list[dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.enviados.append(kwargs)
        return {"id": f"gmail-msg-{len(self.enviados)}", "threadId": "gmail-thr-1"}


def _cuenta(*extra: str) -> _Gmail:
    emails = (BART, NORMA, STREAMTEC, ARTISJET, MQEUROPE, *MANEL_SEIS, *extra)
    return _Gmail([
        {"send_as_email": e, "display_name": e.split("@")[0].title(),
         "is_primary": False, "is_default": False, "verification_status": "accepted"}
        for e in dict.fromkeys(emails)
    ])


def _como(cliente: TestClient, rol: str) -> dict[str, str]:
    return auth_headers(cliente, rol)


def _remitentes(http: TestClient, user_id: str, gmail: _Gmail,
                rol: str = "admin") -> Any:
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        return http.get(f"/api/users/{user_id}/senders", headers=_como(http, rol))


def _guardar(http: TestClient, user_id: str, prefs: list[dict[str, Any]], gmail: _Gmail,
             rol: str = "admin") -> Any:
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        return http.put(f"/api/users/{user_id}/senders", json={"preferences": prefs},
                        headers=_como(http, rol))


def _como_lo_manda_la_pantalla(lista: list[dict[str, Any]], *, marcar: dict[str, bool],
                               predeterminado: str | None) -> list[dict[str, Any]]:
    """El selector manda TODAS las filas, con lo que hay más lo que se cambió."""
    out = []
    for a in lista:
        allowed = marcar.get(a["send_as_email"], a["user_pref_allowed"])
        out.append({"alias_email": a["send_as_email"], "is_allowed": allowed,
                    "is_default": allowed and a["send_as_email"] == predeterminado})
    return out


def _desplegable(http: TestClient, rol: str, gmail: _Gmail) -> list[dict[str, Any]]:
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        r = http.get("/api/emails/my-aliases", headers=_como(http, rol))
    assert r.status_code == 200, r.text
    return r.json()


def _estado(session: Session, user_id: str) -> dict[str, tuple[Any, ...]]:
    session.expire_all()
    return {alias: (r.is_allowed, r.is_default, r.user_opted_in, r.display_name_override)
            for alias, r in _prefs(session, user_id).items()}


# --- 1 · el administrador marca, al usuario le sale ------------------------------


def test_el_admin_marca_un_remitente_de_norma_y_le_sale_en_su_desplegable(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)                      # como en producción: filas apagadas
    assert [a["send_as_email"] for a in _desplegable(http, "user", gmail)] == [NORMA]

    lista = _remitentes(http, norma, gmail).json()
    ventas = next(a for a in lista if a["send_as_email"] == VENTAS)
    assert (ventas["user_pref_allowed"], ventas["en_gmail"]) == (False, True)

    r = _guardar(http, norma, _como_lo_manda_la_pantalla(
        lista, marcar={VENTAS: True}, predeterminado=VENTAS), gmail)
    assert r.status_code == 200, r.text
    devuelto = {a["send_as_email"]: a for a in r.json()}
    assert devuelto[VENTAS]["user_pref_allowed"] and devuelto[VENTAS]["user_pref_default"]

    desplegable = _desplegable(http, "user", gmail)
    assert [(a["send_as_email"], a["is_default"]) for a in desplegable] == [
        (VENTAS, True), (NORMA, False)]


def test_un_solo_predeterminado_por_usuario(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)                      # NORMA, visible y predeterminado
    # Solo llega VENTAS como predeterminado: NORMA deja de serlo aunque no venga.
    r = _guardar(http, norma, [{"alias_email": VENTAS, "is_allowed": True,
                                "is_default": True}], gmail)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        estado = _estado(s, norma)
        assert [a for a, e in estado.items() if e[1]] == [VENTAS]
        assert estado[NORMA][:2] == (True, False)
    # Marcados sin ninguno predeterminado: el primero lo es, y solo él.
    r = _guardar(http, norma, [
        {"alias_email": PEDIDOS, "is_allowed": True, "is_default": False},
        {"alias_email": VENTAS, "is_allowed": True, "is_default": False}], gmail)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert [a for a, e in _estado(s, norma).items() if e[1]] == [PEDIDOS]
    # Dos predeterminados a la vez no se aceptan.
    r = _guardar(http, norma, [
        {"alias_email": PEDIDOS, "is_allowed": True, "is_default": True},
        {"alias_email": VENTAS, "is_allowed": True, "is_default": True}], gmail)
    assert r.status_code == 422


# --- 2 · lo que marca el administrador sobrevive al sync ---------------------------


def test_lo_que_marca_el_admin_sobrevive_al_sync_de_gmail(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)
    lista = _remitentes(http, norma, gmail).json()
    # Le enciende dos de Streamtec (ajenos: el sync los apagaría) y le apaga
    # su propia dirección (el sync la encendería).
    r = _guardar(http, norma, _como_lo_manda_la_pantalla(
        lista, marcar={VENTAS: True, PEDIDOS: True, NORMA: False}, predeterminado=VENTAS),
        gmail)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        antes = _estado(s, norma)
        assert antes[VENTAS][:3] == (True, True, True)       # elegido, como si lo eligiera ella
        assert antes[NORMA][:3] == (False, False, False)     # rechazo recordado
        _sync(s, norma, gmail)
        _sync(s, norma, gmail)
        assert _estado(s, norma) == antes
    assert [a["send_as_email"] for a in _desplegable(http, "user", gmail)] == [VENTAS, PEDIDOS]


# --- auditoría y permisos -----------------------------------------------------------


def test_cambiar_los_remitentes_de_otro_queda_en_auditoria(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)
    lista = _remitentes(http, norma, gmail).json()
    prefs = _como_lo_manda_la_pantalla(lista, marcar={VENTAS: True}, predeterminado=VENTAS)
    prefs[[p["alias_email"] for p in prefs].index(VENTAS)]["display_name_override"] = (
        "Norma Streamtec")
    assert _guardar(http, norma, prefs, gmail).status_code == 200
    with session_factory() as s:
        filas = list(s.scalars(select(AuditLog).where(AuditLog.action == "user.senders_updated")))
        assert len(filas) == 1
        fila = filas[0]
        assert (fila.actor_email, fila.target_type, fila.target_id) == (
            "admin@example.com", "user", norma)
        meta = json.loads(fila.metadata_json)
        assert meta["target_email"] == NORMA
        cambio = next(c for c in meta["cambios"] if c["alias"] == VENTAS)
        assert cambio["antes"]["marcado"] is False
        assert cambio["despues"] == {"marcado": True, "predeterminado": True,
                                     "nombre": "Norma Streamtec", "eleccion": True}
        # NORMA deja de ser el predeterminado: también cuenta.
        assert any(c["alias"] == NORMA for c in meta["cambios"])
        assert f"+{VENTAS}" in fila.message and "Norma Streamtec" in fila.message
    # Guardar lo mismo otra vez no deja otra línea.
    lista = _remitentes(http, norma, gmail).json()
    assert _guardar(http, norma, _como_lo_manda_la_pantalla(
        lista, marcar={}, predeterminado=VENTAS), gmail).status_code == 200
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "user.senders_updated")) == 1


def test_un_no_admin_no_puede_tocar_los_remitentes_de_otro(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        bart = _uid(s, UserRole.MANAGER)
        _sync(s, norma, gmail)
        antes = _estado(s, norma)
    prefs = [{"alias_email": VENTAS, "is_allowed": True, "is_default": True}]
    for rol, objetivo in (("manager", norma), ("user", bart), ("user", norma),
                          ("viewer", norma)):
        assert _remitentes(http, objetivo, gmail, rol=rol).status_code == 403
        assert _guardar(http, objetivo, prefs, gmail, rol=rol).status_code == 403
    with session_factory() as s:
        assert _estado(s, norma) == antes
        assert not s.scalar(select(AuditLog.id).where(
            AuditLog.action == "user.senders_updated"))


def test_un_usuario_que_no_existe_da_404(http: TestClient) -> None:
    assert _remitentes(http, "no-existe", _cuenta()).status_code == 404


# --- el nombre visible --------------------------------------------------------------


def test_el_nombre_que_pone_el_admin_es_el_que_sale_en_el_correo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)
    lista = _remitentes(http, norma, gmail).json()
    prefs = _como_lo_manda_la_pantalla(lista, marcar={VENTAS: True}, predeterminado=VENTAS)
    prefs[[p["alias_email"] for p in prefs].index(VENTAS)]["display_name_override"] = (
        "Norma · Streamtec")
    devuelto = {a["send_as_email"]: a for a in _guardar(http, norma, prefs, gmail).json()}
    assert devuelto[VENTAS]["gmail_display_name"] == "Ventas"          # Gmail, intacto
    assert devuelto[VENTAS]["resolved_display_name"] == "Norma · Streamtec"
    desplegable = _desplegable(http, "user", gmail)
    assert desplegable[0]["resolved_display_name"] == "Norma · Streamtec"

    # El compositor no manda `from_name`: el envío lo resuelve de la preferencia.
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        r = http.post("/api/emails/send", headers=_como(http, "user"), json={
            "from_alias": VENTAS, "to": ["cliente@example.com"], "subject": "Hola",
            "body_text": "Prueba"})
    assert r.status_code == 201, r.text
    assert gmail.enviados[-1]["from_alias"] == VENTAS
    assert gmail.enviados[-1]["from_name"] == "Norma · Streamtec"


def test_marca_los_nombres_visibles_que_nombran_bomedia(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, gmail)
    nombres = {VENTAS: "Norma · Bomedia", PEDIDOS: "Pedidos Streamtec",
               NORMA: "Norma (norma@bomedia.net)"}
    prefs = [{"alias_email": a, "is_allowed": True, "is_default": a == VENTAS,
              "display_name_override": n} for a, n in nombres.items()]
    devuelto = {a["send_as_email"]: a for a in _guardar(http, norma, prefs, gmail).json()}
    assert devuelto[VENTAS]["menciona_bomedia"] is True
    assert devuelto[PEDIDOS]["menciona_bomedia"] is False
    # Una dirección @bomedia.net no es una mención (la misma regla del Cuadre).
    assert devuelto[NORMA]["menciona_bomedia"] is False
    # Sin nombre propio manda el de Gmail, y es ese el que se juzga.
    gmail_bomedia = _cuenta()
    gmail_bomedia._aliases = [dict(a, display_name="Bomedia") if a["send_as_email"] == MANEL
                              else a for a in gmail_bomedia._aliases]
    lista = {a["send_as_email"]: a for a in _remitentes(http, norma, gmail_bomedia).json()}
    assert lista[MANEL]["menciona_bomedia"] is True


def test_el_cuadre_manda_a_la_ficha_del_usuario_para_cambiar_el_nombre(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        s.add(UserEmailAliasPref(user_id=norma, alias_email=VENTAS, is_allowed=True,
                                 display_name_override="Norma · Bomedia"))
        s.commit()
        textos = [t for t in textos_cliente(s) if t.tipo == "remitente"]
    assert [t.enlace for t in textos] == [f"/admin/users#remitentes-{norma}"]


# --- 4 · lo que Gmail no deja usar se ve -------------------------------------------


def test_las_direcciones_de_entrada_y_las_que_gmail_no_tiene_salen_marcadas(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    gmail = _cuenta()
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        s.add(UserEmailAlias(user_id=norma, alias_email=NORMA_FLUX, active=True))
        s.add(UserEmailAlias(user_id=norma, alias_email=VENTAS, active=True))
        _pref(s, norma, "viejo@bomedia.net", allowed=False, opted=True)
        s.commit()
        _sync(s, norma, gmail)
    lista = {a["send_as_email"]: a for a in _remitentes(http, norma, gmail).json()}
    assert (lista[NORMA_FLUX]["en_gmail"], lista[NORMA_FLUX]["de_entrada"]) == (False, True)
    assert (lista[VENTAS]["en_gmail"], lista[VENTAS]["de_entrada"]) == (True, True)
    assert lista["viejo@bomedia.net"]["en_gmail"] is False
    assert lista[NORMA]["de_entrada"] is False
    # Mi cuenta sigue enseñando solo lo de Gmail.
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        propia = http.get("/api/emails/aliases", headers=_como(http, "user")).json()
    assert NORMA_FLUX not in {a["send_as_email"] for a in propia}

    # Marcarla se puede (la pantalla avisa antes), pero no sale en el «De» mientras
    # Gmail no la tenga verificada; el sync la apaga y recuerda la elección…
    r = _guardar(http, norma, [{"alias_email": NORMA_FLUX, "is_allowed": True,
                                "is_default": False},
                               {"alias_email": NORMA, "is_allowed": True,
                                "is_default": True}], gmail)
    assert r.status_code == 200, r.text
    assert NORMA_FLUX not in {a["send_as_email"] for a in _desplegable(http, "user", gmail)}
    with session_factory() as s:
        _sync(s, norma, gmail)
        assert _estado(s, norma)[NORMA_FLUX][:3] == (False, False, True)
        # …y cuando se verifica en Gmail, aparece sola.
        _sync(s, norma, _cuenta(NORMA_FLUX))
    assert NORMA_FLUX in {a["send_as_email"]
                          for a in _desplegable(http, "user", _cuenta(NORMA_FLUX))}


def test_sin_gmail_el_admin_ve_lo_guardado_y_mi_cuenta_sigue_igual(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        norma = _uid(s, UserRole.USER)
        _sync(s, norma, _cuenta())
    caido = patch.object(gmail_service, "_client_for",
                         side_effect=GmailNotConnectedError("sin conexión"))
    with caido:
        r = http.get(f"/api/users/{norma}/senders", headers=_como(http, "admin"))
        propia = http.get("/api/emails/aliases", headers=_como(http, "user"))
    assert r.status_code == 200, r.text
    lista = {a["send_as_email"]: a for a in r.json()}
    assert lista[NORMA]["user_pref_allowed"] is True
    assert {a["en_gmail"] for a in lista.values()} == {None}
    assert propia.status_code == 200 and propia.json() == []


# --- lo de hoy no cambia -------------------------------------------------------------


def test_lo_que_tienen_hoy_manel_y_bart_sigue_igual_tras_desplegar(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Sin migración: el despliegue no toca datos. Y ni la nueva lectura del
    administrador ni las pasadas del sync cambian lo que Manel y Bart tienen."""
    gmail = _cuenta()
    with session_factory() as s:
        bart = _uid(s, UserRole.MANAGER)
        manel = User(email=MANEL, full_name="Manel Pons", password_hash="x",
                     role=UserRole.USER, is_active=True)
        s.add(manel)
        s.flush()
        manel_id = manel.id
        for alias in MANEL_SEIS:                    # el UPDATE del 28/10
            fila = _pref(s, manel_id, alias, allowed=True, default=alias == VENTAS, opted=True)
            if alias == MANEL:
                fila.display_name_override = "Manel Pons"
        for alias in (STREAMTEC, ARTISJET, MQEUROPE):
            _pref(s, bart, alias, allowed=True, default=alias == STREAMTEC, opted=True)
        s.commit()
        _sync(s, manel_id, gmail)
        _sync(s, bart, gmail)
        manel_antes = _estado(s, manel_id)
        bart_antes = _estado(s, bart)
    assert sorted(a for a, e in manel_antes.items() if e[0]) == sorted(MANEL_SEIS)
    assert [a for a, e in manel_antes.items() if e[1]] == [VENTAS]
    assert all(manel_antes[a][2] is True for a in MANEL_SEIS)
    assert manel_antes[MANEL][3] == "Manel Pons"
    assert sorted(a for a, e in bart_antes.items() if e[0]) == sorted(
        [BART, STREAMTEC, ARTISJET, MQEUROPE])
    assert [a for a, e in bart_antes.items() if e[1]] == [STREAMTEC]

    # Lo que hace el despliegue en la práctica: el admin abre las fichas, el
    # sync pasa, Bart abre Mi cuenta.
    assert _remitentes(http, manel_id, gmail).status_code == 200
    assert _remitentes(http, bart, gmail).status_code == 200
    with patch.object(gmail_service, "_client_for", return_value=gmail):
        assert http.get("/api/emails/aliases", headers=_como(http, "manager")).status_code == 200
    with session_factory() as s:
        _sync(s, manel_id, gmail)
        _sync(s, bart, gmail)
        assert _estado(s, manel_id) == manel_antes
        assert _estado(s, bart) == bart_antes
    de_bart = _desplegable(http, "manager", gmail)
    assert de_bart[0]["send_as_email"] == STREAMTEC and de_bart[0]["is_default"]
