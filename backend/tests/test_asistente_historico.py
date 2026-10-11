"""«Ayúdame a contestar» — qué histórico mira y cuánto (11/10/2026): los cuatro
niveles, el presupuesto de tokens, la empresa por su registro (nunca por el
dominio del correo) y la lista de lo que entró. Claude está simulado."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
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
from app.models.ia import BorradorAsistente
from app.services import llm
from app.services.asistente import config as config_asistente
from app.services.asistente import contexto, historico
from app.services.asistente.redactor import prompt_usuario
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users

ALIAS = "info@bomedia.net"
KLAUS = "klaus@elbarquito.net"
PETRA = "petra@elbarquito.net"
HOY = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


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


def _admin(session: Session) -> User:
    return session.scalar(select(User).where(User.role == UserRole.ADMIN))


def _contacto(session: Session, email: str, *, nombre: str = "Klaus",
              empresa: Company | None = None) -> Contact:
    c = Contact(first_name=nombre, last_name="Barquito", email=email, language="es",
                company_id=empresa.id if empresa else None)
    session.add(c)
    session.flush()
    return c


def _empresa(session: Session, nombre: str, dominio: str | None = None) -> Company:
    e = Company(name=nombre, domain=dominio)
    session.add(e)
    session.flush()
    return e


def _hilo(
    session: Session, contacto: Contact | None, asunto: str, mensajes: list[tuple[str, str]],
    *, hace_dias: int = 0,
) -> EmailThread:
    """`mensajes` = [(lado, texto)], un mensaje por hora a partir de `hace_dias`."""
    admin = _admin(session)
    base = HOY - timedelta(days=hace_dias)
    hilo = EmailThread(
        contact_id=contacto.id if contacto else None, initiated_by_user_id=admin.id,
        gmail_thread_id=f"gt-{asunto[:12]}-{hace_dias}", gmail_account_user_id=admin.id,
        subject=asunto, first_message_at=base, last_message_at=base, message_count=len(mensajes),
    )
    session.add(hilo)
    session.flush()
    for i, (lado, texto) in enumerate(mensajes):
        nuestro = lado == "nosotros"
        session.add(EmailMessage(
            thread_id=hilo.id, gmail_message_id=f"gm-{hilo.id[:8]}-{i}",
            gmail_account_user_id=admin.id,
            direction=EmailDirection.OUTBOUND if nuestro else EmailDirection.INBOUND,
            from_email=ALIAS if nuestro else (contacto.email if contacto else "nadie@x.io"),
            to_emails_json=json.dumps([contacto.email if (nuestro and contacto) else ALIAS]),
            subject=asunto, body_text=texto, sent_at=base + timedelta(hours=i),
            contact_id=contacto.id if contacto else None,
            delivered_to=None if nuestro else "info@mboprinters.com",
        ))
    hilo.last_message_at = base + timedelta(hours=len(mensajes))
    session.flush()
    return hilo


def _pedido(session: Session, contacto: Contact, numero: str, descripcion: str) -> Order:
    o = Order(external_source=OrderSource.MANUAL, order_number=numero, contact_id=contacto.id,
              company_id=contacto.company_id, total_amount=1200.0, currency="EUR",
              payment_status=PaymentStatus.PAID, placed_at=HOY - timedelta(days=200))
    session.add(o)
    session.flush()
    session.add(OrderLine(order_id=o.id, position=0, product_sku="X", description=descripcion,
                          quantity=1, unit_price=1200.0, line_total=1200.0))
    session.flush()
    return o


def _ctx(session: Session, hilo: EmailThread, alcance: str = "contacto") -> contexto.Contexto:
    return contexto.construir(session, thread=hilo, usuario=_admin(session), alcance=alcance)


# --- las piezas ----------------------------------------------------------------------


def test_los_asuntos_con_prefijos_se_reconocen_como_el_mismo() -> None:
    normalizados = {historico.asunto_normalizado(a) for a in (
        "Re: Pedido 4411", "RE: Pedido 4411", "AW: Pedido 4411", "Fwd: [EXT] Pedido 4411",
        "RV: Re: Pedido 4411", "pedido 4411",
    )}
    assert normalizados == {"pedido 4411"}
    assert historico.asunto_normalizado("Rechazo del pedido") == "rechazo del pedido"
    assert historico.referencias("¿Qué pasa con el pedido 4411 y la factura FAC-2026-12?") == {
        "4411", "FAC-2026-12"}
    assert historico.referencias("En 2024 compramos") == set()        # un año no es referencia
    assert "impresora" not in historico.palabras_contenido("la impresora para camisetas")
    assert "camisetas" in historico.palabras_contenido("la impresora para camisetas")
    assert historico.palabras_de_producto(["Artisjet 3000U PRO UV Led", "Tinta UV blanca"]) == {
        "artisjet", "3000u", "blanca"}


# --- los niveles ---------------------------------------------------------------------


def test_un_contacto_con_un_hilo_y_nada_mas_se_comporta_como_antes(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)
        hilo = _hilo(s, klaus, "Impresora UV", [("cliente", "Hola, ¿precio de la UV mediana?")])
        ctx = _ctx(s, hilo)
        assert ctx.niveles == [1, 2, 3] and ctx.historico == [] and ctx.empresa_asuntos == []
        assert ctx.contacto is not None and ctx.asuntos_recientes == []
        assert ctx.recortes == [] and ctx.presupuesto_tokens == 12000
        resumen = ctx.resumen()
        assert resumen["historico"] == [] and resumen["empresa"] == []
        assert resumen["mensajes_historico"] == 0 and resumen["mensajes_hilo"] == 1
        # «Solo este hilo»: ni ficha ni histórico, pero sí los enlaces de la web.
        solo = _ctx(s, hilo, alcance="hilo")
        assert solo.niveles == [1] and solo.contacto is None and solo.pedidos == []
        assert solo.enlaces["soporte"] is not None


def test_la_referencia_de_hoy_trae_el_correo_de_hace_un_ano(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)
        _hilo(s, klaus, "Oferta cabezal", [("cliente", "Me interesa el cabezal nuevo."),
                                            ("nosotros", "Te paso oferta del cabezal.")],
              hace_dias=400)
        _hilo(s, klaus, "Pedido 4411", [("cliente", "Confirmo el pedido 4411 de la tinta."),
                                         ("nosotros", "Pedido 4411 en marcha, sale el lunes.")],
              hace_dias=365)
        _hilo(s, klaus, "Queja embalaje", [("cliente", "La caja llegó rota.")], hace_dias=300)
        hoy = _hilo(s, klaus, "Duda", [("cliente", "Hola, ¿tenéis la factura del pedido 4411? "
                                                    "No la encuentro.")])
        ctx = _ctx(s, hoy)
        assert [h["asunto"] for h in ctx.historico] == ["Pedido 4411"]
        assert ctx.historico[0]["motivo"] == "menciona la referencia 4411"
        assert ctx.historico[0]["lado"] == "nosotros"          # el más reciente del hilo
        assert "Pedido 4411 en marcha" in ctx.historico[0]["texto"]
        prompt = prompt_usuario(ctx)
        assert "CORREOS ANTERIORES DEL CONTACTO QUE VIENEN A CUENTO (1)" in prompt
        assert "Pedido 4411 en marcha, sale el lunes." in prompt
        # Los otros hilos están como título (expediente), nunca con su cuerpo.
        assert "La caja llegó rota" not in prompt and "Me interesa el cabezal" not in prompt
        assert "Oferta cabezal" in prompt and "Queja embalaje" in prompt
        # Y los 3 asuntos del expediente (títulos) sí están.
        assert len(ctx.asuntos_recientes) == 3
        # La lista de lo que se usó coincide con lo que se le pasó.
        resumen = ctx.resumen()
        assert [h["asunto"] for h in resumen["historico"]] == ["Pedido 4411"]
        assert resumen["historico"][0]["con_cuerpo"] is True and resumen["niveles"] == [1, 2, 3]


def test_mismo_asunto_misma_maquina_y_solape_de_palabras(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)
        _pedido(s, klaus, "M-7", "FLUX Beambox II 40W")
        _hilo(s, klaus, "Pedido 4411", [("nosotros", "Pedido 4411 enviado.")], hace_dias=200)
        _hilo(s, klaus, "Lente", [("cliente", "La lente de la Beambox se ha rayado, ¿recambio?")],
              hace_dias=150)
        _hilo(s, klaus, "Curso", [("cliente", "¿Hacéis formación presencial para grabado en "
                                              "madera contrachapada y acrílico con la máquina?")],
              hace_dias=100)
        _hilo(s, klaus, "Factura", [("cliente", "¿Me mandáis la factura de marzo?")], hace_dias=50)
        hoy = _hilo(s, klaus, "AW: Pedido 4411", [(
            "cliente", "Quería preguntar por el grabado en madera contrachapada y acrílico: "
                       "¿qué potencia y velocidad usáis para el grabado en acrílico?")])
        ctx = _ctx(s, hoy)
        motivos = {h["asunto"]: h["motivo"] for h in ctx.historico}
        assert motivos["Pedido 4411"] == "mismo asunto"
        assert motivos["Lente"].startswith("misma máquina o producto (beambox")
        assert motivos["Curso"].startswith("solape de palabras")
        assert "Factura" not in motivos
        # El más reciente primero.
        assert [h["asunto"] for h in ctx.historico] == ["Curso", "Lente", "Pedido 4411"]


def test_ningun_correo_viejo_relacionado_deja_el_nivel_vacio(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)
        _hilo(s, klaus, "Navidad", [("nosotros", "Felices fiestas desde Bomedia.")], hace_dias=300)
        hoy = _hilo(s, klaus, "Dudas", [("cliente", "¿Vendéis vinilo textil?")])
        ctx = _ctx(s, hoy)
        assert ctx.historico == [] and len(ctx.asuntos_recientes) == 1
        assert "CORREOS ANTERIORES" not in prompt_usuario(ctx)


def test_doscientos_correos_no_pasan_del_presupuesto_y_el_hilo_entra_entero(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)
        relleno = ("Hablamos del grabado en acrílico y madera contrachapada, potencia y "
                   "velocidad. ") * 12
        for i in range(100):
            _hilo(s, klaus, f"Grabado acrílico {i}", [("cliente", relleno), ("nosotros", relleno)],
                  hace_dias=10 + i)
        textos = [f"Mensaje {n} del hilo: grabado en acrílico y madera, potencia y velocidad."
                  for n in range(6)]
        hoy = _hilo(s, klaus, "Grabado acrílico hoy", [
            ("cliente" if n % 2 == 0 else "nosotros", t) for n, t in enumerate(textos)])
        assert s.scalar(select(EmailMessage.id).where(
            EmailMessage.contact_id == klaus.id).limit(1)) is not None
        ctx = _ctx(s, hoy)
        assert len(ctx.historico) == 5                         # el tope, no 100
        # Un presupuesto pequeño: se recorta por abajo y el hilo sigue entero.
        from app.services.asistente.config import estimar_tokens  # noqa: PLC0415

        medir = lambda c: estimar_tokens(prompt_usuario(c))  # noqa: E731
        antes = medir(ctx)
        tokens = historico.ajustar_al_presupuesto(ctx, presupuesto=antes - 600, medir=medir)
        assert tokens <= antes - 600 and len(ctx.historico) < 5 and ctx.recortes
        assert all(r.startswith("histórico:") for r in ctx.recortes)
        prompt = prompt_usuario(ctx)
        for t in textos:
            assert prompt.count(t) == 1
        assert f"(Por tamaño se dejaron fuera {len(ctx.recortes)} correos antiguos" in prompt
        # Si ni el hilo cabe, no se recorta nada más: lo que no cabe no cabe.
        tokens = historico.ajustar_al_presupuesto(ctx, presupuesto=10, medir=medir)
        assert ctx.historico == [] and tokens > 10


def test_la_empresa_se_agrupa_por_registro_y_va_etiquetada(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        barquito = _empresa(s, "El Barquito SL", "elbarquito.net")
        klaus = _contacto(s, KLAUS, nombre="Klaus", empresa=barquito)
        petra = _contacto(s, PETRA, nombre="Petra", empresa=barquito)
        _pedido(s, klaus, "M-9", "FLUX Beambox II 40W")          # la máquina de la empresa
        _hilo(s, petra, "Descuento Beambox", [
            ("cliente", "¿Nos hacéis descuento en la Beambox para el taller?"),
            ("nosotros", "Petra, os dejamos la Beambox a 2.900 € hasta fin de mes."),
        ], hace_dias=20)
        _hilo(s, petra, "Vacaciones", [("cliente", "Cerramos en agosto.")], hace_dias=60)
        hoy = _hilo(s, klaus, "Beambox", [("cliente", "¿Cuánto cuesta la Beambox? ¿Y la lente?")])
        # «Todo lo de este contacto»: lo de Petra NO entra.
        ctx = _ctx(s, hoy, alcance="contacto")
        assert ctx.empresa_asuntos == [] and 4 not in ctx.niveles
        assert "Petra" not in prompt_usuario(ctx)
        # «Todo lo de la empresa»: entra etiquetado, y el que viene a cuento, con cuerpo.
        ctx = _ctx(s, hoy, alcance="empresa")
        assert ctx.niveles == [1, 2, 3, 4] and ctx.empresa == "El Barquito SL"
        assert [e["asunto"] for e in ctx.empresa_asuntos] == ["Descuento Beambox", "Vacaciones"]
        assert all(e["quien"] == "Petra Barquito" for e in ctx.empresa_asuntos)
        assert "texto" in ctx.empresa_asuntos[0] and "texto" not in ctx.empresa_asuntos[1]
        prompt = prompt_usuario(ctx)
        assert "lo escribió Petra Barquito" in prompt
        assert "no repitas precios, descuentos ni condiciones dadas a otra persona" in prompt
        assert "2.900 €" in prompt and "Cerramos en agosto" not in prompt
        resumen = ctx.resumen()
        assert resumen["empresa"][0]["con_cuerpo"] is True and resumen["mensajes_historico"] == 1
        assert resumen["empresa_nombre"] == "El Barquito SL"


def test_dos_contactos_de_gmail_no_se_mezclan_jamas(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        ana = _contacto(s, "ana@gmail.com", nombre="Ana")
        luis = _contacto(s, "luis@gmail.com", nombre="Luis")
        _hilo(s, luis, "Oferta Ador", [("nosotros", "Luis, la Ador a 2.500 €.")], hace_dias=5)
        hoy = _hilo(s, ana, "Ador", [("cliente", "¿Precio de la Ador?")])
        for alcance in ("contacto", "empresa"):
            ctx = _ctx(s, hoy, alcance=alcance)
            assert ctx.empresa_asuntos == [] and ctx.historico == []
            assert "Luis" not in prompt_usuario(ctx) and "2.500" not in prompt_usuario(ctx)
        # Ni aunque alguien haya creado una «empresa» llamada gmail.com.
        gmail = _empresa(s, "gmail.com", "gmail.com")
        ana.company_id = gmail.id
        luis.company_id = gmail.id
        s.flush()
        s.expire_all()
        ctx = _ctx(s, hoy, alcance="empresa")
        assert ctx.empresa_asuntos == [] and 4 not in ctx.niveles
        gratuitos = config_asistente.defectos()["dominios_gratuitos"]
        assert historico.empresa_agrupable(s.get(Contact, ana.id), gratuitos) is None


def test_sin_empresa_asignada_el_nivel_cuatro_queda_vacio(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        klaus = _contacto(s, KLAUS)                             # mismo dominio que Petra…
        petra = _contacto(s, PETRA, nombre="Petra", empresa=_empresa(s, "El Barquito SL"))
        _hilo(s, petra, "Oferta", [("nosotros", "Petra, oferta.")], hace_dias=3)
        hoy = _hilo(s, klaus, "Hola", [("cliente", "¿Oferta?")])
        ctx = _ctx(s, hoy, alcance="empresa")                   # …pero sin empresa: nada
        assert ctx.empresa_asuntos == [] and 4 not in ctx.niveles


def test_remitente_desconocido_solo_el_hilo(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        hoy = _hilo(s, None, "Hola", [("cliente", "¿Tenéis stock de tinta blanca?")])
        for alcance in ("hilo", "contacto", "empresa"):
            ctx = _ctx(s, hoy, alcance=alcance)
            assert ctx.niveles == [1] and ctx.contacto is None
            assert ctx.historico == [] and ctx.empresa_asuntos == []


def test_el_registro_guarda_el_alcance_y_los_mensajes(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    class _Falso:
        def __call__(self, **kw: Any) -> str:
            llm.registrar_uso(llm.Uso(modelo="claude-sonnet-4-6", tokens_entrada=900,
                                      tokens_salida=120))
            return json.dumps({"asunto": "Re: Beambox", "cuerpo": "Hola Klaus.", "idioma": "es",
                               "pendiente": [], "enlaces_usados": []})

    with session_factory() as s:
        barquito = _empresa(s, "El Barquito SL", "elbarquito.net")
        klaus = _contacto(s, KLAUS, empresa=barquito)
        petra = _contacto(s, PETRA, nombre="Petra", empresa=barquito)
        _pedido(s, klaus, "M-9", "FLUX Beambox II 40W")
        _hilo(s, petra, "Beambox taller", [("cliente", "¿La Beambox sirve para el taller?")],
              hace_dias=20)
        _hilo(s, klaus, "Beambox", [("cliente", "¿Precio de la Beambox?")], hace_dias=90)
        hoy = _hilo(s, klaus, "RE: Beambox", [("cliente", "¿Y la Beambox con la lente nueva?")])
        s.commit()
        thread_id = hoy.id
    headers = auth_headers(http, "admin")
    with patch.object(llm, "_invoke_claude", _Falso()):
        r = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                      json={"alcance": "empresa"})
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["alcance"] == "empresa" and b["mensajes_hilo"] == 1 and b["mensajes_historico"] == 2
    assert b["contexto"]["niveles"] == [1, 2, 3, 4]
    assert [h["asunto"] for h in b["contexto"]["historico"]] == ["Beambox"]
    assert b["contexto"]["empresa"][0]["quien"] == "Petra Barquito"
    assert b["contexto"]["tokens_estimados"] > 0
    assert b["contexto"]["presupuesto_tokens"] == 12000
    with session_factory() as s:
        fila = s.get(BorradorAsistente, b["id"])
        assert fila.alcance == "empresa" and fila.mensajes_historico == 2
    with patch.object(llm, "_invoke_claude", _Falso()):
        solo = http.post(f"/api/emails/threads/{thread_id}/asistente", headers=headers,
                         json={"alcance": "hilo"}).json()["borrador"]
    assert solo["alcance"] == "hilo" and solo["mensajes_historico"] == 0
    assert solo["contexto"]["niveles"] == [1] and solo["contexto"]["contacto"] is None


def test_la_configuracion_del_asistente_en_ajustes(http: TestClient) -> None:
    headers = auth_headers(http, "admin")
    antes = http.get("/api/erp/settings", headers=headers).json()["asistente"]
    assert antes["presupuesto_tokens"] == 12000 and antes["historico_max"] == 5
    assert "gmail.com" in antes["dominios_gratuitos"]
    r = http.patch("/api/erp/settings", headers=headers, json={"asistente": {
        "presupuesto_tokens": 20000,
        "dominios_gratuitos": ["Gmail.com", "@miweb.io", "mal dominio"],
    }})
    assert r.status_code == 200, r.text
    assert r.json()["asistente"]["presupuesto_tokens"] == 20000
    assert r.json()["asistente"]["dominios_gratuitos"] == ["gmail.com", "miweb.io"]
    assert r.json()["asistente"]["historico_max"] == 5
    assert http.patch("/api/erp/settings", headers=headers, json={"asistente": {
        "presupuesto_tokens": 10}}).status_code == 400


def test_las_referencias_de_firma_no_cuentan() -> None:
    """Teléfonos, fechas y lo que está en todas las firmas (CP, NIF) no son
    referencias: dos correos no vienen a cuento por compartir el pie."""
    assert historico.referencias("Tel. 612345678 · 28001 Madrid · 11/10/2026 · pedido 4411") == {
        "28001", "4411"}
    firma = "\n--\nAcme SL · NIF B12345678 · 28001 Madrid"
    candidatos = [
        historico.Candidato(id=f"c{i}", thread_id=f"t{i}", fecha=HOY - timedelta(days=i),
                            lado="cliente", de=KLAUS, asunto=f"Tema {i}",
                            texto=f"Cosas varias {i}." + firma)
        for i in range(5)
    ]
    hoy = "¿Qué pasa con el pedido 4411?" + firma
    refs = historico.referencias_de_hoy(hoy, candidatos)
    assert refs == {"4411"}                      # el CP y el NIF del pie, fuera
    assert historico.elegir(candidatos, asunto_actual="Hoy", texto_actual=hoy) == []
    con_pedido = candidatos + [historico.Candidato(
        id="c9", thread_id="t9", fecha=HOY - timedelta(days=300), lado="nosotros", de=ALIAS,
        asunto="Envío", texto="El pedido 4411 sale el lunes." + firma)]
    elegidos = historico.elegir(con_pedido, asunto_actual="Hoy", texto_actual=hoy)
    assert [(c.id, m) for c, m in elegidos] == [("c9", "menciona la referencia 4411")]
