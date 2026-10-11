"""Captura de ENTRADA universal (11/10/2026).

En #538 la salida se hizo universal; la entrada seguía filtrando por
`user_email_aliases`: lo que llegaba a `ventas@fluxlasers.es`,
`soporte@fluxlasers.es`, `pedidos@bomedia.net` o `info@bomedia.net` se
descartaba en silencio. Ahora el criterio es el DOMINIO del grupo (tabla de
sitios + alias registrados): los alias solo atribuyen, nada se descarta sin
una línea de log, y el desplegable de remitentes sigue siendo lo que cada
usuario eligió. Gmail está simulado: nada sale a la red.
"""
from __future__ import annotations

import base64
import logging
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
from app.main import app
from app.models.crm import (
    Base,
    Contact,
    EmailDirection,
    EmailMessage,
    GmailPubsubWatch,
    User,
    UserEmailAlias,
    UserEmailAliasPref,
    UserRole,
)
from app.models.sitios import SitioWeb
from app.services.email_aliases import active_alias_map
from app.services.web_forms import sitios
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users

ALIAS_REGISTRADO = "norma@bomedia.net"       # de user@example.com (user_email_aliases)
SIN_REGISTRAR_FLUX = "ventas@fluxlasers.es"   # dominio del grupo, dirección sin registrar
SIN_REGISTRAR_BOMEDIA = "pedidos@bomedia.net"
FUERA = "alguien@otro-dominio.org"
KLAUS = "klaus@kunde.de"


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
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.add(UserEmailAlias(user_id=user, alias_email=ALIAS_REGISTRADO, active=True))
        seed.add(UserEmailAliasPref(user_id=user, alias_email=ALIAS_REGISTRADO,
                                    is_allowed=True, is_default=True, user_opted_in=True))
        seed.commit()
    sitios.fijar_para_tests(None)
    yield factory
    sitios.fijar_para_tests(None)
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
    subject: str = "Frage zur Beambox", labels: tuple[str, ...] = ("INBOX",),
    delivered_to: str | None = None,
) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Sat, 11 Oct 2026 09:15:00 +0200"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if delivered_to:
        headers.append({"name": "Delivered-To", "value": delivered_to})
    return {
        "id": mid, "threadId": thread_id, "snippet": "Hallo…", "labelIds": list(labels),
        "payload": {
            "headers": headers, "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hallo, eine Frage.").decode()},
        },
    }


def _persistir(session: Session, *, user_id: str, raw: dict[str, Any]) -> EmailMessage | None:
    return gmail_service._persist_message(
        session, user_id=user_id, raw=raw, gmail_thread_id=raw["threadId"],
        alias_map=active_alias_map(session), emit_activity=False,
    )


def _cuantos(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(EmailMessage)))


# --- el criterio: el dominio, no la tabla -------------------------------------------


def test_un_correo_a_una_direccion_del_grupo_sin_registrar_se_captura_y_se_vincula(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        klaus = Contact(first_name="Klaus", email=KLAUS)
        s.add(klaus)
        s.flush()
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-flux", "t-flux", from_addr=f"Klaus <{KLAUS}>", to=SIN_REGISTRAR_FLUX))
        s.commit()
        assert msg is not None
        assert msg.direction == EmailDirection.INBOUND
        assert msg.contact_id == klaus.id              # por la dirección de la otra parte
        assert msg.delivered_to == SIN_REGISTRAR_FLUX  # la dirección nuestra a la que llegó
        assert msg.created_by_user_id is None
        # Y lo mismo a un dominio de correo del grupo que no es una web.
        otro = _persistir(s, user_id=buzon, raw=_raw(
            "m-bom", "t-bom", from_addr=KLAUS, to="otro@fuera.com", cc=SIN_REGISTRAR_BOMEDIA))
        s.commit()
        assert otro is not None and otro.delivered_to == SIN_REGISTRAR_BOMEDIA
        # Un alias registrado sigue mandando sobre el dominio: `delivered_to`
        # canónico del alias (lo que usa la visibilidad por usuario).
        alias = _persistir(s, user_id=buzon, raw=_raw(
            "m-alias", "t-alias", from_addr=KLAUS, to=f"{SIN_REGISTRAR_FLUX}, {ALIAS_REGISTRADO}"))
        assert alias is not None and alias.delivered_to == ALIAS_REGISTRADO
        assert _cuantos(s) == 3


def test_un_correo_que_no_toca_ningun_dominio_nuestro_se_ignora_con_su_linea_de_log(
    session_factory: sessionmaker, caplog: pytest.LogCaptureFixture,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        with caplog.at_level(logging.INFO, logger="app.integrations.gmail.service"):
            msg = _persistir(s, user_id=buzon, raw=_raw(
                "m-fuera", "t-fuera", from_addr=KLAUS, to=FUERA, cc="nadie@tampoco.com"))
        assert msg is None and _cuantos(s) == 0
    linea = next(r.getMessage() for r in caplog.records
                 if "gmail.captura.descartado" in r.getMessage())
    assert "motivo=fuera_del_grupo" in linea and "msg=m-fuera" in linea
    assert KLAUS in linea and FUERA in linea


def test_el_dominio_del_grupo_tambien_vale_por_delivered_to_y_por_subdominio(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        # Reenvío interno: el To es externo pero Delivered-To es nuestro.
        reenviado = _persistir(s, user_id=buzon, raw=_raw(
            "m-dt", "t-dt", from_addr=KLAUS, to="lista@grupo-externo.com",
            delivered_to="soporte@fluxlasers.eu"))
        assert reenviado is not None and reenviado.delivered_to == "soporte@fluxlasers.eu"
        # Un subdominio de una web del grupo también es nuestro.
        sub = _persistir(s, user_id=buzon, raw=_raw(
            "m-sub", "t-sub", from_addr=KLAUS, to="tienda@shop.mbolasers.com"))
        assert sub is not None
        # Mayúsculas y «www.» no engañan.
        www = _persistir(s, user_id=buzon, raw=_raw(
            "m-www", "t-www", from_addr=KLAUS, to="Sales@WWW.MQEUROPE.COM"))
        assert www is not None and www.delivered_to == "sales@www.mqeurope.com"


def test_los_dominios_del_grupo_salen_de_la_tabla_de_sitios_y_de_los_alias(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        dominios = sitios.dominios_del_grupo(s)
        # Las ocho webs (fluxlasers con sus dos dominios), los de correo y los alias.
        assert {"boprint.net", "artisjet-spain.es", "artisjet-printers.eu", "mboprinters.com",
                "mqeurope.com", "mbolasers.com", "fluxlasers.es", "fluxlasers.eu",
                "pimpam-vending.com", "bomedia.net", "streamtec.es"} <= dominios
        assert "example.com" not in dominios          # el correo de los usuarios no cuenta
        # Una web NUEVA en la tabla se aplica sin desplegar nada.
        s.add(SitioWeb(clave="webnueva", dominio="webnueva.io", host="webnueva.io",
                       marca="Web Nueva", empresa="Streamtec", tipo="web", idioma_defecto="es",
                       idiomas_json='["es"]', prefijo_idioma_defecto=False,
                       slugs_traducidos=False, soporte_propio=False, posicion=20, activo=True))
        # Con filas en la tabla manda la tabla: las de partida hay que tenerlas
        # también (la migración las siembra; aquí solo está la nueva).
        s.commit()
        assert "webnueva.io" in sitios.dominios_del_grupo(s)
        assert "bomedia.net" in sitios.dominios_del_grupo(s)   # los alias siguen contando
        buzon = _uid(s, UserRole.ADMIN)
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-nueva", "t-nueva", from_addr=KLAUS, to="hola@webnueva.io"))
        assert msg is not None and msg.delivered_to == "hola@webnueva.io"


# --- el push de Gmail --------------------------------------------------------------


class _GmailFalso:
    def __init__(self, mensajes: dict[str, dict[str, Any]], stubs: list[dict[str, Any]]) -> None:
        self.mensajes = mensajes
        self.stubs = stubs

    def list_history(self, start_history_id: int) -> dict[str, Any]:
        return {"history": [{"messagesAdded": [{"message": stub} for stub in self.stubs]}],
                "historyId": "99"}

    def get_message(self, mid: str) -> dict[str, Any]:
        return self.mensajes[mid]


def test_el_push_captura_lo_que_llega_a_un_dominio_del_grupo_y_loguea_lo_que_no(
    session_factory: sessionmaker, caplog: pytest.LogCaptureFixture,
) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        s.add(GmailPubsubWatch(
            user_id=buzon, history_id=1, watch_expires_at=datetime.now(UTC) + timedelta(days=6),
            last_renewed_at=datetime.now(UTC), topic_name="projects/x/topics/y",
        ))
        s.commit()
        mensajes = {
            "m-ped": _raw("m-ped", "t-ped", from_addr=KLAUS, to=SIN_REGISTRAR_BOMEDIA),
            "m-fuera": _raw("m-fuera", "t-fuera", from_addr=KLAUS, to=FUERA),
        }
        stubs = [
            {"id": "m-ped", "threadId": "t-ped", "labelIds": ["INBOX"]},
            {"id": "m-fuera", "threadId": "t-fuera", "labelIds": ["INBOX"]},
            {"id": "m-draft", "threadId": "t-draft", "labelIds": ["DRAFT"]},
        ]
        falso = _GmailFalso(mensajes, stubs)
        with patch.object(gmail_service, "_client_for", return_value=falso), \
                caplog.at_level(logging.INFO, logger="app.integrations.gmail.service"):
            importados = gmail_service.process_history(s, user_id=buzon, new_history_id=99)
        s.commit()
        assert importados == 1
        guardado = s.scalar(select(EmailMessage).where(EmailMessage.gmail_message_id == "m-ped"))
        assert guardado is not None and guardado.delivered_to == SIN_REGISTRAR_BOMEDIA
    motivos = [
        r.getMessage() for r in caplog.records if "gmail.captura.descartado" in r.getMessage()
    ]
    assert any("motivo=fuera_del_grupo msg=m-fuera" in m for m in motivos)
    assert any("motivo=etiquetas_no_capturables msg=m-draft" in m and "DRAFT" in m for m in motivos)


# --- los alias atribuyen, no filtran; y el desplegable no se toca -----------------------


def test_el_desplegable_de_remitentes_sigue_siendo_lo_que_cada_usuario_eligio(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Capturar el correo de `ventas@fluxlasers.es` no la convierte en remitente
    de nadie: «enviar como» sigue saliendo solo de `user_email_alias_prefs`
    con la elección del usuario (#540), nunca de lo capturado."""
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        msg = _persistir(s, user_id=buzon, raw=_raw(
            "m-v", "t-v", from_addr=KLAUS, to=SIN_REGISTRAR_FLUX))
        s.commit()
        assert msg is not None
    aliases_gmail = [
        {"send_as_email": ALIAS_REGISTRADO, "display_name": "Norma", "is_primary": False,
         "is_default": False, "verification_status": "accepted"},
        {"send_as_email": SIN_REGISTRAR_FLUX, "display_name": "Ventas", "is_primary": False,
         "is_default": False, "verification_status": "accepted"},
    ]
    with patch.object(gmail_service, "list_aliases", return_value=aliases_gmail):
        r = http.get("/api/emails/my-aliases", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    assert [a["send_as_email"] for a in r.json()] == [ALIAS_REGISTRADO]


def test_la_salida_universal_de_538_sigue_igual(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        buzon = _uid(s, UserRole.ADMIN)
        enviado = _persistir(s, user_id=buzon, raw=_raw(
            "m-sent", "t-sent", from_addr="sales@mqeurope.com", to="cliente@fuera.com",
            labels=("SENT",)))
        assert enviado is not None and enviado.direction == EmailDirection.OUTBOUND
        assert enviado.delivered_to is None


# --- el registro de sitios -------------------------------------------------------------


def test_el_registro_de_sitios_vale_sin_tabla_y_manda_la_tabla_cuando_hay_filas(
    session_factory: sessionmaker,
) -> None:
    # Sin filas (o sin base): la lista de partida, igual que las constantes de siempre.
    assert sitios.web_de_sitio("mqeurope") == "www.mqeurope.com"
    assert sitios.marca_de_sitio("artisjet-eu") == "Artisjet Europe"
    assert sitios.remitente_de_sitio("pimpam") == "info@pimpam-vending.com"
    assert sitios.remitente_de_sitio("webdesconocida") is None
    assert sitios.web_de_sitio("webdesconocida") == "webdesconocida"
    assert set(sitios.WEBS) == {"boprint", "artisjet-es", "artisjet-eu", "mboprinters",
                                "mqeurope", "mbolasers", "fluxlasers", "pimpam"}
    flux = sitios.sitio_de("fluxlasers")
    assert flux is not None and flux.dominios == ("fluxlasers.es", "fluxlasers.eu")
    assert flux.soporte_propio and flux.plugin == "wpml"
    # Las URL por idioma según el plugin: qTranslate con prefijo salvo el
    # idioma por defecto; un idioma no publicado cae al por defecto.
    eu = sitios.sitio_de("artisjet-eu")
    assert eu is not None
    assert eu.url_base("fr") == "https://artisjet-printers.eu/"
    assert eu.url_base("de") == "https://artisjet-printers.eu/de/"
    assert eu.url_base("nl") == "https://artisjet-printers.eu/"
    with session_factory() as s:
        reg = sitios.cargar(s)
        assert reg.de_la_base is False and len(reg.webs) == 8
        assert reg.por_dominio("x@fluxlasers.eu").clave == "fluxlasers"
        assert reg.por_dominio("shop.mbolasers.com").clave == "mbolasers"
        assert reg.por_dominio("gmail.com") is None
        # Con filas manda la tabla (una marca cambiada desde la pantalla).
        s.add(SitioWeb(clave="pimpam", dominio="pimpam-vending.com", host="pimpam-vending.com",
                       marca="PimPam Vending SL", empresa="Streamtec", tipo="web",
                       idioma_defecto="es", idiomas_json='["es","en"]',
                       prefijo_idioma_defecto=False, slugs_traducidos=False,
                       soporte_propio=True, remitente_defecto="hola@pimpam-vending.com",
                       posicion=0, activo=True))
        s.commit()
        vivo = sitios.cargar(s)
        assert vivo.de_la_base is True
        assert vivo.sitio("pimpam").marca == "PimPam Vending SL"
        sitios.fijar_para_tests(vivo)
        try:
            assert sitios.marca_de_sitio("pimpam") == "PimPam Vending SL"
            assert sitios.remitente_de_sitio("pimpam") == "hola@pimpam-vending.com"
            assert sitios.webs_conocidas() == {"pimpam": "pimpam-vending.com"}
        finally:
            sitios.fijar_para_tests(None)
