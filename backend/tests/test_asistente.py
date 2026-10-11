"""«Ayúdame a contestar» (11/10/2026): el asistente redacta el BORRADOR de la
respuesta a un hilo con el hilo entero, la ficha, los pedidos y la base de
conocimiento; una persona lo revisa y lo envía desde el compositor. Claude
está simulado (`llm._invoke_claude`): nada sale a la red. Y hay un test que
comprueba que ningún camino del asistente envía correo."""
from __future__ import annotations

import json
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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
from app.erp.models.orders import Order, OrderLine, OrderSource, PaymentStatus
from app.integrations.gmail import service as gmail_service
from app.main import app
from app.models.crm import (
    Company,
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    User,
    UserEmailAliasPref,
    UserRole,
)
from app.models.ia import (
    ESTADO_DESCARTADO,
    ESTADO_ENVIADO_EDITADO,
    ESTADO_ENVIADO_TAL_CUAL,
    ESTADO_REGENERADO,
    BorradorAsistente,
    LlamadaIA,
)
from app.services import llm
from app.services.asistente import contexto, texto
from app.services.ia.coste import coste_usd
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users

ADOR = "7104732498447"
BEAMBOX_II = "10647778378639"
ALIAS = "info@bomedia.net"
KLAUS = "torracollons@elbarquito.net"

RESPUESTA_IA = {
    "asunto": "Re: Metallplatten und T-Shirts",
    "cuerpo": (
        "Hallo Klaus,\n\nvielen Dank für Ihre Nachricht. Für Metallplatten und T-Shirts "
        "kommen zwei Technologien in Frage: UV-LED-Druck und DTF.\n\nMehr dazu: "
        "https://www.mqeurope.com/dtf-textile-printing/\n\nViele Grüße\nAdmin"
    ),
    "idioma": "de",
    "pendiente": ["precio de la impresora UV mediana"],
    "enlaces_usados": ["https://www.mqeurope.com/dtf-textile-printing/"],
}


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
    """La clave existe (si no, el asistente dice que la IA no está
    configurada), pero la llamada está simulada: nunca sale a la red."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    yield
    fijar_para_tests(None)
    get_settings.cache_clear()


class _ClaudeFalso:
    """Sustituye a `llm._invoke_claude`: guarda los prompts y devuelve la
    respuesta dada, dejando el uso (tokens) como lo dejaría la API."""

    def __init__(self, respuesta: dict[str, Any] | str = RESPUESTA_IA, *,
                 tokens: tuple[int, int] = (1200, 250), fallo: Exception | None = None) -> None:
        self.respuesta = respuesta
        self.tokens = tokens
        self.fallo = fallo
        self.llamadas: list[dict[str, Any]] = []

    def __call__(self, *, api_key: str, model: str, system_prompt: str, user_prompt: str,
                 max_tokens: int | None = None) -> str:
        assert api_key == "clave-de-prueba"
        self.llamadas.append({"model": model, "system": system_prompt, "user": user_prompt,
                              "max_tokens": max_tokens})
        if self.fallo is not None:
            llm.registrar_uso(None)
            raise self.fallo
        llm.registrar_uso(llm.Uso(modelo=model, tokens_entrada=self.tokens[0],
                                  tokens_salida=self.tokens[1], duracion_ms=900))
        return self.respuesta if isinstance(self.respuesta, str) else json.dumps(self.respuesta)

    @property
    def ultimo(self) -> dict[str, Any]:
        return self.llamadas[-1]


def _admin(session: Session) -> User:
    return session.scalar(select(User).where(User.role == UserRole.ADMIN))


def _pedir(http: TestClient, thread_id: str, rol: str = "admin", **payload: Any) -> Any:
    return http.post(f"/api/emails/threads/{thread_id}/asistente",
                     headers=auth_headers(http, rol), json=payload)


def _hilo(
    session: Session, *, contacto: Contact | None, mensajes: list[dict[str, Any]],
    asunto: str = "Metallplatten und T-Shirts", delivered_to: str = "info@mboprinters.com",
) -> EmailThread:
    """Un hilo con sus mensajes; `mensajes` = [{de, texto, lado?, fecha?}]."""
    admin = _admin(session)
    base = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    hilo = EmailThread(
        contact_id=contacto.id if contacto else None, initiated_by_user_id=admin.id,
        gmail_thread_id=f"gt-{asunto[:6]}-{len(mensajes)}", gmail_account_user_id=admin.id,
        subject=asunto, first_message_at=base, last_message_at=base, message_count=len(mensajes),
    )
    session.add(hilo)
    session.flush()
    for i, m in enumerate(mensajes):
        nuestro = m.get("lado") == "nosotros"
        fecha = m.get("fecha") or base + timedelta(hours=i)
        session.add(EmailMessage(
            thread_id=hilo.id, gmail_message_id=f"gm-{hilo.id[:6]}-{i}",
            gmail_account_user_id=admin.id,
            direction=EmailDirection.OUTBOUND if nuestro else EmailDirection.INBOUND,
            from_email=m["de"], to_emails_json=json.dumps([m.get("para") or delivered_to]),
            subject=asunto, body_text=m["texto"], body_html=m.get("html"), sent_at=fecha,
            contact_id=contacto.id if contacto else None,
            delivered_to=None if nuestro else delivered_to,
            created_by_user_id=admin.id if nuestro else None,
        ))
    hilo.last_message_at = base + timedelta(hours=len(mensajes))
    session.flush()
    return hilo


def _contacto(session: Session, email: str = KLAUS, *, idioma: str = "es",
              empresa: str | None = None, interes: str | None = None) -> Contact:
    c = Contact(first_name="Klaus", last_name="Barquito", email=email, language=idioma,
                origin="web_form:mboprinters-contacto-de", lead_interest=interes)
    if empresa:
        emp = Company(name=empresa)
        session.add(emp)
        session.flush()
        c.company_id = emp.id
    session.add(c)
    session.flush()
    return c


def _pedido(session: Session, contacto: Contact, *, numero: str, descripcion: str,
            pago: PaymentStatus = PaymentStatus.PAID, hace_dias: int = 30) -> Order:
    o = Order(
        external_source=OrderSource.MANUAL, order_number=numero, contact_id=contacto.id,
        company_id=contacto.company_id, total_amount=3990.0, currency="EUR",
        payment_status=pago, placed_at=datetime.now(UTC) - timedelta(days=hace_dias),
    )
    session.add(o)
    session.flush()
    session.add(OrderLine(order_id=o.id, position=0, product_sku="FLX-ADOR",
                          description=descripcion, quantity=1, unit_price=3990.0,
                          line_total=3990.0))
    session.flush()
    return o


MENSAJE_KLAUS = (
    "Guten Tag,\n\nich möchte auf Metallplatten sowie auf T-Shirts drucken. Welche Maschine "
    "empfehlen Sie und was kostet sie?\n\nMit freundlichen Grüßen\nKlaus"
)


# --- el borrador -------------------------------------------------------------------


def test_remitente_desconocido_tambien_tiene_borrador(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        hilo = _hilo(s, contacto=None,
                     mensajes=[{"de": "nadie@desconocido.io", "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["texto"].startswith("Hallo Klaus") and b["idioma"] == "de"
    assert b["destinatario"] == "nadie@desconocido.io" and b["asunto"].startswith("Re:")
    assert b["html"].startswith("<p>Hallo Klaus,</p>")
    # Sin ficha no hay interés ni página de producto ofrecida: una URL que el
    # modelo haya puesto por su cuenta se queda como texto, no como enlace.
    assert "https://www.mqeurope.com/dtf-textile-printing/" in b["html"] and "<a " not in b["html"]
    assert b["contexto"]["contacto"] is None and b["mensajes_hilo"] == 1
    assert b["pendiente"] == ["precio de la impresora UV mediana"]
    prompt = falso.ultimo["user"]
    assert "remitente desconocido" in prompt and "no supongas nada" in prompt
    # Sin ficha no hay interés: ni página de producto; sí la ruta de soporte por idioma.
    assert "página de producto" not in prompt
    assert "https://artisjet-printers.eu/de/technical-support/" in prompt
    assert falso.ultimo["max_tokens"] == 1500
    with session_factory() as s:
        assert s.scalar(select(EmailMessage).where(EmailMessage.thread_id == thread_id,
                                                   EmailMessage.direction == "outbound")) is None


def test_contacto_con_pedido_ador_lleva_la_maquina_y_su_categoria_de_flux(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        klaus = _contacto(s, empresa="El Barquito SL", interes="dtf")
        _pedido(s, klaus, numero="M-1001", descripcion="FLUX Ador 20W Diodenlaser + Rotary")
        _pedido(s, klaus, numero="M-1002", descripcion="Tinta UV blanca 1 l", hace_dias=5,
                pago=PaymentStatus.PENDING)
        hilo = _hilo(s, contacto=klaus, mensajes=[
            {"de": KLAUS, "texto": "Wie kalibriere ich die Kamera? Und wo bleibt meine Rechnung?"},
        ], asunto="Kamera", delivered_to="ventas@fluxlasers.es")
        s.commit()
        thread_id, contact_id = hilo.id, klaus.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, alcance="contacto")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    prompt = falso.ultimo["user"]
    assert "Ador" in prompt and "modelo FLUX: Ador" in prompt
    assert "Pedido M-1001" in prompt and "Pedido M-1002" in prompt
    assert "Pendiente de cobro: pedido M-1002" in prompt
    assert "El Barquito SL" in prompt
    enlaces = [e["url"] for e in b["enlaces"]]
    assert f"https://support.flux3dp.com/hc/de-de/categories/{ADOR}" in enlaces   # KB en alemán
    assert "https://fluxlasers.eu/de/support/" in enlaces                          # soporte FLUX
    # La página de producto de su interés (dtf), ofrecida y por tanto enlazada en el HTML.
    assert "https://www.mqeurope.com/dtf-textile-printing/" in enlaces
    assert "página de producto (dtf)" in prompt
    assert '<a href="https://www.mqeurope.com/dtf-textile-printing/">' in b["html"]
    assert b["contexto"]["modelo_flux"] == "Ador" and b["contexto"]["pedidos"] == 2
    assert b["contexto"]["facturas_pendientes"] == 1 and b["contact_id"] == contact_id
    assert b["contexto"]["contacto"]["empresa"] == "El Barquito SL"


def test_hilo_en_aleman_sale_en_aleman_aunque_la_ficha_diga_castellano(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        klaus = _contacto(s, idioma="es")
        hilo = _hilo(s, contacto=klaus, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s))
        assert ctx.idioma == "de" and ctx.idioma_fuente == "mensaje"
        assert ctx.contacto["idioma"] == "es"
        assert ctx.enlaces["soporte"]["url"] == "https://artisjet-printers.eu/de/technical-support/"
        assert ctx.sitio == "mboprinters" and ctx.destinatario == KLAUS
        assert ctx.asunto_respuesta == "Re: Metallplatten und T-Shirts"
        from app.services.asistente.redactor import prompt_usuario  # noqa: PLC0415

        prompt = prompt_usuario(ctx)
        assert "IDIOMA DE LA RESPUESTA: de (alemán)" in prompt
        assert "Idioma de la ficha: es" in prompt
        # Mensaje sin palabras que delaten idioma: manda la ficha.
        corto = _hilo(s, contacto=klaus, mensajes=[{"de": KLAUS, "texto": "OK"}], asunto="OK")
        ctx2 = contexto.construir(s, thread=corto, usuario=_admin(s))
        assert ctx2.idioma == "es" and ctx2.idioma_fuente == "ficha"


def test_un_hilo_de_cinco_mensajes_entra_entero_y_sin_repetir(
    session_factory: sessionmaker,
) -> None:
    textos = [
        ("cliente", "Hola, quiero información sobre la impresora UV mediana."),
        ("nosotros", "Hola Klaus, te paso el catálogo. ¿Qué formato necesitas imprimir?"),
        ("cliente", "Placas de metal de 30x30 y también camisetas."),
        ("nosotros", "Perfecto, para camisetas lo suyo es DTF. ¿Volumen mensual?"),
        ("cliente", "Unas 200 camisetas al mes. ¿Precio de la UV mediana y plazo?"),
    ]
    with session_factory() as s:
        klaus = _contacto(s)
        hilo = _hilo(s, contacto=klaus, mensajes=[
            {"de": KLAUS if lado == "cliente" else ALIAS, "texto": t, "lado": lado,
             "para": KLAUS if lado == "nosotros" else ALIAS}
            for lado, t in textos
        ], asunto="UV mediana")
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s))
        assert [m.lado for m in ctx.mensajes] == ["cliente", "nosotros", "cliente", "nosotros",
                                                   "cliente"]
        assert ctx.ultimo_del_cliente.texto.startswith("Unas 200 camisetas")
        from app.services.asistente.redactor import prompt_usuario  # noqa: PLC0415

        prompt = prompt_usuario(ctx)
        assert "HILO (5 mensajes" in prompt
        for _, t in textos:
            assert prompt.count(t) == 1
        # Lo que el cliente citó del mensaje anterior no se duplica.
        citado = _hilo(s, contacto=klaus, mensajes=[
            {"de": ALIAS, "texto": "Te paso el catálogo.", "lado": "nosotros", "para": KLAUS},
            {"de": KLAUS, "texto": "Gracias, ¿y el precio?\n\nEl 10/10/2026, Admin escribió:\n"
                                    "> Te paso el catálogo."},
        ], asunto="Catálogo")
        ctx2 = contexto.construir(s, thread=citado, usuario=_admin(s))
        assert ctx2.mensajes[1].texto == "Gracias, ¿y el precio?"


def test_el_bloque_de_razonamiento_primero_no_rompe_y_el_uso_se_recoge() -> None:
    """La generación Claude 5 razona antes de contestar: el texto va después
    del bloque `thinking` (#537). Y los tokens de la API quedan en el
    contexto para anotarlos."""
    pensamiento = SimpleNamespace(type="thinking", thinking="Veamos…")
    texto_bloque = SimpleNamespace(type="text", text='{"asunto": "Re", "cuerpo": "Hola"}')
    mensaje = SimpleNamespace(
        content=[pensamiento, texto_bloque], model="claude-sonnet-4-6-20260301",
        usage=SimpleNamespace(input_tokens=1500, output_tokens=120),
    )

    class _Cliente:
        def __init__(self, api_key: str) -> None:
            self.messages = SimpleNamespace(create=lambda **kw: mensaje)

    with patch("anthropic.Anthropic", _Cliente):
        salida = llm._invoke_claude(api_key="k", model="claude-sonnet-4-6", system_prompt="s",
                                    user_prompt="u", max_tokens=100)
    assert salida == '{"asunto": "Re", "cuerpo": "Hola"}'
    uso = llm.tomar_ultimo_uso()
    assert uso is not None and (uso.tokens_entrada, uso.tokens_salida) == (1500, 120)
    assert uso.modelo == "claude-sonnet-4-6-20260301" and uso.duracion_ms is not None
    assert llm.tomar_ultimo_uso() is None                 # recogerlo lo vacía
    assert coste_usd(uso.modelo, 1500, 120) == pytest.approx(0.0063, rel=1e-6)


def test_el_registro_guarda_tokens_coste_y_resultado(
    session_factory: sessionmaker, http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    falso = _ClaudeFalso(tokens=(2000, 300))
    headers = auth_headers(http, "admin")
    with session_factory() as s:
        klaus = _contacto(s)
        hilo = _hilo(s, contacto=klaus, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        primero = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                            json={}).json()["borrador"]
        # «Regenerar»: el anterior queda como regenerado.
        segundo = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                            json={"regenerar_de": primero["id"]}).json()["borrador"]
        tercero = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                            json={}).json()["borrador"]
    assert primero["tokens_entrada"] == 2000 and primero["tokens_salida"] == 300
    assert primero["coste_usd"] == pytest.approx(coste_usd("claude-sonnet-4-6", 2000, 300))
    assert primero["modelo"] == get_settings().anthropic_model
    with session_factory() as s:
        llamadas = list(s.scalars(select(LlamadaIA).order_by(LlamadaIA.created_at)))
        assert len(llamadas) == 3
        assert all(ll.proposito == "asistente_borrador" and ll.ok for ll in llamadas)
        assert llamadas[0].tokens_entrada == 2000 and llamadas[0].referencia_id == primero["id"]
        assert llamadas[0].duracion_ms == 900
        assert s.get(BorradorAsistente, primero["id"]).estado == ESTADO_REGENERADO
    # Descartar.
    r = http.post(f"/api/emails/threads/{thread_id}/asistente/{segundo['id']}/descartar",
                  headers=headers)
    assert r.status_code == 200 and r.json()["borrador"]["estado"] == ESTADO_DESCARTADO
    # Enviar desde el compositor con el texto del borrador tal cual (más la
    # firma que añade el compositor) → «enviado tal cual».
    enviados: list[dict[str, Any]] = []

    class _GmailFalso:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            enviados.append(kwargs)
            return {"id": f"gmail-{len(enviados)}", "threadId": "gmail-thr-1"}

    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", _GmailFalso)
    firma = "<!--crmbo:signature--><p>—</p><p>Admin · BoHub</p><!--/crmbo:signature-->"
    cuerpo_html = tercero["html"] + firma
    r = http.post("/api/emails/send", headers=headers, json={
        "from_alias": ALIAS, "to": [KLAUS], "subject": tercero["asunto"], "body_html": cuerpo_html,
        "asistente_borrador_id": tercero["id"],
    })
    assert r.status_code == 201, r.text
    with session_factory() as s:
        b = s.get(BorradorAsistente, tercero["id"])
        assert b.estado == ESTADO_ENVIADO_TAL_CUAL and b.email_message_id == r.json()["id"]
    # Otro borrador, editado antes de enviar → «enviado editado».
    with patch.object(llm, "_invoke_claude", falso):
        cuarto = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                           json={}).json()["borrador"]
    r = http.post("/api/emails/send", headers=headers, json={
        "from_alias": ALIAS, "to": [KLAUS], "subject": cuarto["asunto"],
        "body_html": "<p>Hallo Klaus, hier ist eine ganz andere Antwort.</p>",
        "asistente_borrador_id": cuarto["id"],
    })
    assert r.status_code == 201, r.text
    with session_factory() as s:
        assert s.get(BorradorAsistente, cuarto["id"]).estado == ESTADO_ENVIADO_EDITADO
    # Un id de borrador que no existe no rompe el envío.
    r = http.post("/api/emails/send", headers=headers, json={
        "from_alias": ALIAS, "to": [KLAUS], "subject": "x", "body_text": "hola",
        "asistente_borrador_id": "no-existe",
    })
    assert r.status_code == 201
    assert len(enviados) == 3
    # La pantalla de la IA ve los números.
    uso = http.get("/api/erp/ia/uso?dias=7", headers=headers).json()
    assert uso["llamadas"] == 4 and uso["tokens_entrada"] == 8000
    assert uso["por_proposito"][0]["clave"] == "asistente_borrador"
    assert uso["borradores"] == {"generados": 0, "regenerados": 1, "descartados": 1,
                                 "enviados_tal_cual": 1, "enviados_editados": 1, "total": 4}
    assert http.get("/api/erp/ia/uso", headers=auth_headers(http, "user")).status_code == 403


def test_la_ia_caida_queda_anotada_y_no_rompe_nada(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    caido = _ClaudeFalso(fallo=llm.LLMUpstreamError("Provider error"))
    with patch.object(llm, "_invoke_claude", caido):
        r = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers, json={})
    assert r.status_code == 502 and "no ha podido redactar" in r.json()["detail"]
    with session_factory() as s:
        fallida = s.scalar(select(LlamadaIA))
        assert fallida is not None and fallida.ok is False and "Provider error" in fallida.error
        assert s.scalar(select(BorradorAsistente)) is None
    # Sin clave: 503 y nada anotado.
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
        get_settings.cache_clear()
        r = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers, json={})
    get_settings.cache_clear()
    assert r.status_code == 503
    # El modelo contesta sin JSON: el texto entero es el cuerpo.
    with patch.object(llm, "_invoke_claude", _ClaudeFalso("Hallo Klaus, danke.")):
        r = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers, json={})
    assert r.status_code == 201 and r.json()["borrador"]["texto"] == "Hallo Klaus, danke."
    assert r.json()["borrador"]["asunto"] == "Re: Metallplatten und T-Shirts"


def test_lo_puede_pedir_quien_ve_el_hilo_y_nadie_mas(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", _ClaudeFalso()) as falso:
        r = _pedir(http, thread_id, rol="user")
        assert r.status_code == 403 and not falso.llamadas
        assert http.post(f"/api/emails/threads/{thread_id}/asistente",
                         headers=auth_headers(http, "admin"),
                         json={"alcance": "galaxia"}).status_code == 400
        assert http.post("/api/emails/threads/no-existe/asistente",
                         headers=auth_headers(http, "admin"), json={}).status_code == 404


def test_ningun_camino_del_asistente_envia_correo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Lo pide una persona, lo revisa una persona, lo envía una persona: el
    paquete del asistente no toca Gmail ni el envío, y generar un borrador no
    crea ningún mensaje ni llama a enviar."""
    raiz = Path(__file__).resolve().parents[1] / "app"
    ficheros = list((raiz / "services" / "asistente").glob("*.py")) + [
        raiz / "api" / "asistente.py", raiz / "services" / "ia" / "uso.py",
        raiz / "services" / "ia" / "coste.py",
    ]
    prohibido = re.compile(r"send_email|send_message|GmailClient|gmail_service|smtplib|\.send\(")
    for f in ficheros:
        codigo = f.read_text(encoding="utf-8")
        # Las docstrings pueden nombrar el envío para decir que no lo hacen;
        # el código, no.
        sin_doc = re.sub(r'"""[\s\S]*?"""', "", codigo)
        sin_doc = re.sub(r"#[^\n]*", "", sin_doc)
        assert not prohibido.search(sin_doc), f"{f.name} toca el envío de correo"
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with (
        patch.object(llm, "_invoke_claude", _ClaudeFalso()),
        patch.object(gmail_service, "send_email") as enviar,
        patch.object(gmail_service, "_client_for") as cliente,
    ):
        r = _pedir(http, thread_id)
        assert r.status_code == 201
        enviar.assert_not_called()
        cliente.assert_not_called()
    with session_factory() as s:
        assert s.scalar(select(EmailMessage).where(EmailMessage.direction == "outbound")) is None
        assert s.scalar(select(BorradorAsistente)).estado == "generado"


# --- texto ---------------------------------------------------------------------------


def test_texto_a_html_y_comparacion_de_lo_enviado() -> None:
    html = texto.a_html("Hola Klaus,\n\nMira esto: https://fluxlasers.es/soporte/ y ya.\nSaludos",
                        ["https://fluxlasers.es/soporte/"])
    assert html == (
        "<p>Hola Klaus,</p><p>Mira esto: "
        '<a href="https://fluxlasers.es/soporte/">https://fluxlasers.es/soporte/</a> y ya.'
        "<br>Saludos</p>"
    )
    assert "<a" not in texto.a_html("ver https://otra.web/", [])          # fuera de la lista, texto
    assert texto.normalizado("Hola,  Klaus — ¿qué tal?") == texto.normalizado(
        "hola klaus qué tal")
    con_firma = "<p>x</p><!--crmbo:signature--><p>f</p><!--/crmbo:signature-->"
    assert texto.sin_firma(con_firma) == "<p>x</p>"
    assert texto.sin_cita("Gracias\n\nOn Fri, Oct 10, 2026 Admin wrote:\n> catálogo") == "Gracias"
    assert texto.sin_cita("Danke!\n\nAm 10.10.2026 schrieb Admin:\n> Katalog") == "Danke!"
    assert texto.recortar("abcdef", 4) == "abc…"
