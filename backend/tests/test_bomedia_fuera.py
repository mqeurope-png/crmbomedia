"""Bomedia sale del ecosistema (13/10/2026): nunca más en lo que lee un cliente.

Castellano → Streamtec; el resto de idiomas → MQ Europe (el criterio de las
rutas de soporte). Las direcciones de bomedia.net siguen vivas y no cuentan; la
serie 1 de FACTUSOL sigue siendo «Bomedia S.L.» por decisión de Bart.

Los cinco de la petición: un borrador con «Bomedia» no llega tal cual a la
pantalla y queda registrado; `bart@bomedia.net` como dirección no es
infracción; un hilo lleno de «Bomedia» no hace que el borrador nuevo lo use;
el Cuadre encuentra una plantilla sembrada y no marca los alias; una resiembra
en limpio no planta «Bomedia». Claude está simulado: nada sale a la red.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.email_templates.models import EmailTemplate
from app.erp.cuadre.checks_textos import agrupar, bomedia_en_textos_cliente, textos_cliente
from app.erp.cuadre.contexto import Contexto
from app.erp.factusol_pdf import company_for_serie
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID, ErpSettings
from app.main import app
from app.models.conocimiento import MarcaCatalogo, ProductoCatalogo
from app.models.crm import AuditLog, EmailSignature, User, UserEmailAliasPref, UserRole
from app.models.sitios import SitioWeb
from app.models.web_forms import WebForm
from app.models.workflows import Workflow, WorkflowStep
from app.services import empresas_grupo as eg
from app.services import llm
from app.services.asistente import redactor
from app.services.conocimiento import catalogo_compositor
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from app.workflows.templates import TEMPLATES
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users
from tests.test_asistente import ALIAS, _admin, _ClaudeFalso, _contacto, _hilo, _pedir


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
        admin = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.add(UserEmailAliasPref(user_id=admin, alias_email=ALIAS, is_allowed=True,
                                    is_default=True))
        seed.commit()
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


@pytest.fixture(autouse=True)
def _ia_configurada(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    yield
    fijar_para_tests(None)
    get_settings.cache_clear()


MENSAJE_ES = ("Hola,\n\nos compré tintas hace años y quiero repetir. ¿Me mandáis precio?"
              "\n\nGracias\nKlaus")

#: Lo que haría un modelo que copia el tono y la firma de los hilos viejos.
COPIA_LA_FIRMA_VIEJA = {
    "asunto": "Re: Tintas de Bomedia",
    "cuerpo": ("Hola Klaus,\n\nGracias por volver a confiar en Bomedia. Ya trabajaste con "
               "Bomedia en 2019, así que te preparo la oferta con las mismas tintas.\n\n"
               "Un saludo,\nAdmin\nBomedia, S.L."),
    "idioma": "es", "pendiente": [], "enlaces_usados": [],
}


def _hilo_es(s: Session, *, viejo_de_bomedia: bool = False) -> str:
    contacto = _contacto(s, idioma="es")
    mensajes: list[dict[str, Any]] = []
    if viejo_de_bomedia:
        # El histórico real: nuestros correos de antes, firmados como Bomedia.
        mensajes.append({"de": "manel@bomedia.net", "lado": "nosotros",
                         "texto": ("Hola Klaus,\n\nte paso las tintas de Bomedia.\n\nUn saludo,\n"
                                   "Manel\nBomedia, S.L. · www.bomedia.net")})
    mensajes.append({"de": contacto.email, "texto": MENSAJE_ES})
    hilo = _hilo(s, contacto=contacto, mensajes=mensajes, asunto="Tintas",
                 delivered_to="info@bomedia.net")
    s.commit()
    return hilo.id


# --- 1 · un borrador con «Bomedia» no llega tal cual, y queda registrado --------------------


def test_un_borrador_con_bomedia_no_llega_tal_cual_y_queda_registrado(
    session_factory: sessionmaker, http: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s)
    falso = _ClaudeFalso(COPIA_LA_FIRMA_VIEJA)
    with caplog.at_level(logging.WARNING, logger="app.services.asistente.redactor"), \
            patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    # Nada de «Bomedia» en lo que llega al editor: ni cuerpo, ni HTML, ni asunto.
    for campo in ("texto", "html", "asunto"):
        assert "bomedia" not in b[campo].lower(), (campo, b[campo])
    assert "Gracias por volver a confiar en Streamtec" in b["texto"]
    assert b["texto"].rstrip().endswith("Streamtec")            # «Bomedia, S.L.» entero
    # El aviso dice QUÉ frase se tocó, antes y ahora: «ya trabajaste con…» es un
    # hecho, y quien revisa tiene que poder releerlo.
    aviso = b["bomedia"]
    assert aviso["empresa"] == "Streamtec"
    antes = [c["antes"] for c in aviso["cambios"]]
    assert any("Ya trabajaste con Bomedia en 2019" in a for a in antes)
    hecho = next(c for c in aviso["cambios"] if "2019" in c["antes"])
    assert "Ya trabajaste con Streamtec en 2019" in hecho["despues"]
    assert {c["campo"] for c in aviso["cambios"]} == {"asunto", "cuerpo"}
    # Registrado: en el log, en la auditoría y en el propio turno.
    assert any("Bomedia" in r.getMessage() for r in caplog.records)
    with session_factory() as s:
        entrada = s.scalar(select(AuditLog).where(AuditLog.action == redactor.ACCION_BOMEDIA))
        assert entrada is not None and entrada.target_id == b["id"]
        meta = json.loads(entrada.metadata_json)
        assert meta["empresa"] == "Streamtec" and len(meta["cambios"]) == len(aviso["cambios"])


def test_en_otro_idioma_la_empresa_es_mq_europe(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s)
    respuesta = {"asunto": "Re: Tinten", "idioma": "de", "pendiente": [], "enlaces_usados": [],
                 "cuerpo": "Hallo Klaus,\n\ndanke, dass Sie wieder Bomedia vertrauen.\n\nAdmin"}
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(respuesta)):
        b = _pedir(http, thread_id).json()["borrador"]
    assert "danke, dass Sie wieder MQ Europe vertrauen" in b["texto"]
    assert b["bomedia"]["empresa"] == "MQ Europe"


# --- 2 · una dirección de bomedia.net no es infracción --------------------------------------


def test_una_direccion_bomedia_net_no_es_infraccion(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s)
    respuesta = {"asunto": "Re: Tintas", "idioma": "es", "pendiente": [], "enlaces_usados": [],
                 "cuerpo": ("Hola Klaus,\n\npara el pedido de tintas escríbeme a "
                            "bart@bomedia.net y te lo preparo hoy.\n\nUn saludo,\nAdmin")}
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(respuesta)):
        b = _pedir(http, thread_id).json()["borrador"]
    assert "bart@bomedia.net" in b["texto"]
    assert b["bomedia"] is None
    with session_factory() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == redactor.ACCION_BOMEDIA)) is None


def test_la_deteccion_no_cuenta_direcciones_ni_dominios() -> None:
    assert not eg.hay_bomedia("bart@bomedia.net · bomedia.net · https://bomedia.es/tintas")
    assert not eg.hay_bomedia("norma@bomedia.net, eduard@bomedia.net, bomedia@bomedia.net")
    assert not eg.hay_bomedia("agile-mbomedia · …/bomedia-v4/logo.png")
    assert eg.hay_bomedia("Un abrazo del equipo Bomedia.")
    assert eg.hay_bomedia("2025 TEXTIL BOMEDIA")
    assert eg.sustituir("Firmado: Bomedia, S.L.", eg.STREAMTEC) == ("Firmado: Streamtec.", 1)
    assert eg.sustituir("9.300 € de Bomedia S.L. por", eg.MQ_EUROPE)[0] == (
        "9.300 € de MQ Europe por")


# --- 3 · un hilo lleno de «Bomedia» no hace que el borrador nuevo lo use -------------------


def test_un_hilo_lleno_de_bomedia_no_hace_que_el_borrador_lo_use(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s, viejo_de_bomedia=True)
    # El modelo obedece: lo que se le manda le dice quién es y que no la copie.
    obediente = _ClaudeFalso({"asunto": "Re: Tintas", "idioma": "es", "pendiente": [],
                              "enlaces_usados": [],
                              "cuerpo": "Hola Klaus,\n\nte preparo las tintas.\n\nAdmin"})
    with patch.object(llm, "_invoke_claude", obediente):
        b = _pedir(http, thread_id).json()["borrador"]
    sistema, contexto = obediente.ultimo["system"], obediente.ultimo["user"]
    assert "Bomedia, MQ Europe" not in sistema                  # la identidad, sin Bomedia
    assert "NUNCA escribas «Bomedia»" in sistema
    assert "EMPRESA: Streamtec si el correo va en castellano" in contexto
    # El hilo lo cuenta tal cual (es histórico), pero bomedia.net no es una web:
    # llegó a info@bomedia.net y la cabecera no le da al modelo una «web bomedia».
    assert "Bomedia, S.L." in contexto
    assert "WEB POR LA QUE LLEGÓ: bomedia" not in contexto
    assert redactor._web_de_llegada("bomedia") == "ninguna (llegó por correo directo)"
    assert redactor._web_de_llegada("mboprinters") == "mboprinters"
    assert b["bomedia"] is None and "bomedia" not in b["texto"].lower()
    # Y si aun así copia la firma vieja, la guardia la para: sale como Streamtec.
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(COPIA_LA_FIRMA_VIEJA)):
        b = _pedir(http, thread_id).json()["borrador"]
    assert "bomedia" not in b["texto"].lower() and "Streamtec" in b["texto"]


def test_el_compuesto_tambien_pasa_por_la_guardia(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s)
    compuesto = {
        "modo": "compuesto", "asunto": "Tintas de Bomedia", "idioma": "es", "aviso": "",
        "pendiente": [],
        "bloques": [
            {"tipo": "texto", "texto": "Hola Klaus,\n\nen Bomedia tenemos tus tintas."},
            {"tipo": "texto", "texto": "Escríbeme a bart@bomedia.net.\n\nUn saludo,\nAdmin"},
        ],
    }
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(compuesto)):
        r = _pedir(http, thread_id, instruccion="mándale las tintas", formato="compuesto")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["modo"] == "compuesto"
    assert "bomedia" not in b["asunto"].lower()
    assert "en Streamtec tenemos tus tintas" in b["texto"]
    assert "bart@bomedia.net" in b["texto"]                        # la dirección, intacta
    assert "Bomedia" not in json.dumps(b["estructura"], ensure_ascii=False).replace(
        "bomedia.net", "")
    assert {c["campo"] for c in b["bomedia"]["cambios"]} == {"asunto", "cuerpo"}


def test_una_correccion_no_arrastra_el_aviso_del_turno_anterior(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_es(s)
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(COPIA_LA_FIRMA_VIEJA)):
        primero = _pedir(http, thread_id).json()["borrador"]
    assert primero["bomedia"] is not None
    limpio = {"asunto": "Re: Tintas", "idioma": "es", "pendiente": [], "enlaces_usados": [],
              "cuerpo": "Hola Klaus,\n\nte preparo la oferta hoy.\n\nAdmin"}
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(limpio)):
        r = http.post(f"/api/emails/asistente/{primero['id']}/turno",
                      headers=auth_headers(http, "admin"), json={"instruccion": "más corto"})
    assert r.status_code == 201, r.text
    assert r.json()["borrador"]["bomedia"] is None


def test_la_oferta_de_la_serie_1_no_le_da_bomedia_al_modelo() -> None:
    serie1 = {"emisora": "Bomedia S.L.", "serie": 1}
    assert "Bomedia" not in redactor._emisora_para_ia(serie1)
    assert redactor._emisora_para_ia({"emisora": "Streamtec, S.L.", "serie": 5}) == (
        "Streamtec, S.L.")
    # La empresa del correo: la emisora si es Streamtec o MQ Europe; si no, el idioma.
    assert eg.empresa_para("es", 2) == (eg.MQ_EUROPE, eg.ORIGEN_DOCUMENTO)
    assert eg.empresa_para("de", 5) == (eg.STREAMTEC, eg.ORIGEN_DOCUMENTO)
    assert eg.empresa_para("de", 1) == (eg.MQ_EUROPE, eg.ORIGEN_IDIOMA)
    assert eg.empresa_para("es", "Bomedia S.L.") == (eg.STREAMTEC, eg.ORIGEN_IDIOMA)
    for idioma in ("en", "de", "fr", "nl", "pt"):
        assert eg.empresa_por_idioma(idioma) == eg.MQ_EUROPE


# --- 4 · el Cuadre encuentra lo sembrado y no marca los alias ------------------------------


def _ajustes(s: Session, datos: dict[str, Any]) -> None:
    fila = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID) or ErpSettings(
        id=ERP_SETTINGS_SINGLETON_ID)
    fila.factusol_series_json = json.dumps(datos, ensure_ascii=False)
    s.add(fila)


def test_el_cuadre_encuentra_lo_sembrado_y_no_marca_los_alias(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        admin = _admin(s)
        s.add_all([
            EmailTemplate(name="2025 TEXTIL BOMEDIA", subject="Novedades textil",
                          body_html="<p>Hola,</p><p>Un saludo del equipo Bomedia.</p>"),
            EmailTemplate(name="Contacto", subject="Escríbenos",
                          body_html="<p>Escríbenos a bart@bomedia.net o a norma@bomedia.net</p>"),
            EmailSignature(user_id=admin.id, name="Vieja", html_content="<b>Bomedia, S.L.</b>"),
            EmailSignature(user_id=admin.id, name="Manel",
                           html_content="<b>Streamtec, S.L.</b> · eduard@bomedia.net"),
            MarcaCatalogo(nombre="bomedia", urls_json=json.dumps({"es": "https://bomedia.es"})),
        ])
        wf = Workflow(name="Bienvenida vieja", trigger_type="contact.created")
        s.add(wf)
        s.flush()
        s.add(WorkflowStep(workflow_id=wf.id, type="action_send_email", config_json=json.dumps(
            {"subject": "Bienvenido a Bomedia", "body_html": "<p>Hola</p>",
             "from_alias": "info@bomedia.net"})))
        # El borrador de respuesta a leads: remitente con nombre visible.
        s.add(WorkflowStep(workflow_id=wf.id, type="action_prepare_email_draft",
                           config_json=json.dumps({"from_name": "Equipo Bomedia"})))
        s.add(WebForm(slug="viejo-contacto", name="Viejo", created_by_user_id=admin.id,
                      submit_success_message="Gracias por escribir a Bomedia."))
        s.add(WebForm(slug="otro-contacto", name="Contacto Bomedia", created_by_user_id=admin.id))
        # El «De:» visible de un alias; el alias en sí (bomedia.net) no cuenta.
        s.add(UserEmailAliasPref(user_id=admin.id, alias_email="bart@bomedia.net",
                                 is_allowed=True, display_name_override="Bart · Bomedia"))
        s.add_all([
            SitioWeb(clave="boprint", dominio="boprint.net", host="boprint.net",
                     marca="Boprint by Bomedia", empresa="Streamtec", tipo="web"),
            SitioWeb(clave="bomedia", dominio="bomedia.net", host="bomedia.net",
                     marca="Bomedia", empresa="Bomedia", tipo="correo"),
            ProductoCatalogo(fuente="compositor", origen_id="m1", nombre="Muestras Bomedia",
                             idioma="es", visto_en=datetime.now(UTC)),
        ])
        programado = _hilo(s, contacto=None, asunto="Novedades Bomedia", mensajes=[
            {"de": "bart@bomedia.net", "lado": "nosotros", "texto": "Hola"}]).messages[0]
        programado.scheduled_status = "pending"
        # La serie 1 sigue siendo «Bomedia S.L.» (emisora de erp_settings): no es
        # un texto que se repase aquí. Las demás series sí, y el aviso de envío.
        _ajustes(s, {"companies": {
                         "1": {"nombre": "Bomedia S.L."},
                         "5": {"bancos": [{"nombre": "Bomedia Sabadell", "iban": "ES00"}]}},
                     "names": {"1": "Bomedia", "5": "Streamtec"},
                     "shipment_email_templates": {"es": {"asunto": "Tu pedido de Bomedia"}}})
        s.commit()
        hallados = list(bomedia_en_textos_cliente(Contexto(s, ahora=datetime.now(UTC),
                                                           config={})))
    por_etiqueta = {h.etiqueta: h for h in hallados}
    assert set(por_etiqueta) == {
        "Plantilla «2025 TEXTIL BOMEDIA»",
        "Firma «Vieja»",
        "Marca de la base de conocimiento «bomedia»",
        "Workflow «Bienvenida vieja» · enviar correo",
        "Workflow «Bienvenida vieja» · preparar borrador",
        "Formulario web «Viejo»",
        "Formulario web «Contacto Bomedia»",
        "Remitente «bart@bomedia.net»",
        "Web «boprint.net»",
        "Producto del catálogo «Muestras Bomedia»",
        "Correo programado «Novedades Bomedia»",
        "Configuración ERP · Empresa emisora serie 5",
        "Configuración ERP · Plantillas del aviso de envío",
    }
    # Un banco con «Bomedia» en una serie que no es la 1 saldría en su PDF.
    assert "Bomedia Sabadell" in por_etiqueta["Configuración ERP · Empresa emisora serie 5"].detalle
    # Cada uno dice dónde y con qué frase: es el inventario que se repasa a mano.
    plantilla = por_etiqueta["Plantilla «2025 TEXTIL BOMEDIA»"]
    assert "Un saludo del equipo Bomedia." in plantilla.detalle
    assert plantilla.datos["campos"] == ["cuerpo"]
    assert plantilla.enlace == "/emails/plantillas"
    assert all(h.entidad_tipo == "texto_cliente" and len(h.entidad_id) <= 64 for h in hallados)
    # Ni los alias de bomedia.net, ni la firma buena, ni la serie 1 (emisora y
    # nombre), ni el dominio de correo bomedia.net de sitios_web.
    assert "Plantilla «Contacto»" not in por_etiqueta and "Firma «Manel»" not in por_etiqueta
    assert not any(h.entidad_id in ("emisora:1", "serie:1", "sitio:bomedia") for h in hallados)
    assert "Remitente «info@bomedia.net»" not in por_etiqueta     # sin nombre visible


def test_un_id_largo_no_choca_en_los_64_caracteres() -> None:
    from app.erp.cuadre.checks_textos import entidad_id  # noqa: PLC0415

    a, b = entidad_id("marca", "x" * 80), entidad_id("marca", "x" * 79 + "y")
    assert len(a) <= 64 and len(b) <= 64 and a != b
    assert entidad_id("plantilla", "abc") == "plantilla:abc"


# --- 5 · una resiembra en limpio no planta «Bomedia» ---------------------------------------


def _textos_de(valor: Any) -> list[str]:
    return eg.textos_de([valor])


def test_las_semillas_del_repositorio_no_plantan_bomedia() -> None:
    # La galería de plantillas de workflow (lo que se clona al crear uno).
    assert [t for t in _textos_de(TEMPLATES) if eg.hay_bomedia(t)] == []
    # La pasada del compositor: la marca retirada no se escribe nunca, ni sin
    # producto (como hoy) ni el día que tenga uno.
    data = {
        "brands": [{"id": "bomedia", "urls": {"es": "https://bomedia.es"}},
                   {"id": "flux", "urls": {"es": "https://fluxlasers.es"}}],
        "products": [{"id": "p1", "brand": "bomedia", "name": "Muestra"},
                     {"id": "p2", "brand": "flux", "name": "Beambox"}],
    }
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        escritas, _ = catalogo_compositor.escribir_marcas(s, data, ahora=datetime.now(UTC))
        assert escritas == 1
        assert [m.nombre for m in s.scalars(select(MarcaCatalogo))] == ["flux"]


def test_una_resiembra_en_limpio_deja_el_cuadre_a_cero(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    # Las tres plantillas de workflow, clonadas como lo haría Bart: ninguna
    # deja «Bomedia» en un paso de enviar correo.
    for template_id in TEMPLATES:
        r = http.post(f"/api/workflows/_templates/{template_id}/use",
                      headers=auth_headers(http, "admin"))
        assert r.status_code in (200, 201), r.text
    with session_factory() as s:
        pasos = s.scalars(select(WorkflowStep).where(WorkflowStep.type == "action_send_email"))
        asuntos = [json.loads(p.config_json).get("subject", "") for p in pasos]
        assert "Bienvenido/a a Streamtec, {{ contact.first_name }}" in asuntos
        assert agrupar(textos_cliente(s)) == []


# --- y lo que NO se toca ------------------------------------------------------------------


def test_la_serie_1_sigue_siendo_bomedia_sl(session_factory: sessionmaker) -> None:
    """Decisión de Bart: la emisora 1 se queda como está (facturas, proformas,
    albaranes y la firma del correo de presupuesto de esa serie)."""
    with session_factory() as s:
        assert company_for_serie(s, 1)["nombre"] == "Bomedia S.L."
        assert company_for_serie(s, 5)["nombre"] != "Bomedia S.L."


def test_el_script_de_inventario_saca_el_listado_para_el_pr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from app.db import session as db_session  # noqa: PLC0415
    from scripts import inventario_bomedia  # noqa: PLC0415

    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(EmailTemplate(name="FESPA 2024", subject="Nos vemos en FESPA",
                            body_html="<p>Visita el stand de Bomedia.</p>"))
        s.add(EmailTemplate(name="Limpia", subject="Hola", body_html="<p>bart@bomedia.net</p>"))
        s.commit()
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    assert inventario_bomedia.main(["--markdown"]) == 0
    salida = capsys.readouterr()
    assert "| 1 | Plantilla «FESPA 2024» | cuerpo | «Visita el stand de Bomedia.» |" in salida.out
    assert "Limpia" not in salida.out
    assert "1 texto(s)" in salida.err
