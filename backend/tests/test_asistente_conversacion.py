"""El asistente de respuesta como conversación (11/10/2026, remates de #544):
decirle qué quieres decir, el cuerpo es un correo o está vacío, las
correcciones no vuelven a montar ni a pagar el contexto (caché de prompts), un
correo nuevo sin hilo con el pasado del cliente, el modelo por tarea desde
Configuración ERP y el destinatario por defecto es el cliente. Claude está
simulado: nada sale a la red."""
from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import EmailDirection, EmailMessage, User, UserEmailAliasPref, UserRole
from app.models.ia import (
    ESTADO_DESCARTADO,
    ESTADO_ENVIADO_EDITADO,
    ESTADO_ENVIADO_TAL_CUAL,
    ESTADO_GENERADO,
    ESTADO_REGENERADO,
    BorradorAsistente,
    LlamadaIA,
)
from app.services import llm
from app.services.asistente import contexto as contexto_mod
from app.services.asistente import redactor, texto
from app.services.ia import modelos
from app.services.ia.coste import coste_usd, precio_cache
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users
from tests.test_asistente import (
    ALIAS,
    KLAUS,
    MENSAJE_KLAUS,
    RESPUESTA_IA,
    _admin,
    _ClaudeFalso,
    _contacto,
    _hilo,
    _pedido,
    _pedir,
)


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

TALENTOS = "administracion@talentosdiversos.org"
MANEL = "manel@bomedia.net"

INFORME = (
    "Hola Manel, he revisado el hilo con Marny (Talentosdiversos). Su último mensaje, el de "
    "la lista de equipos y consumibles, ya lo respondiste el 26 con el presupuesto adjunto, y "
    "no hay nada suyo posterior. Por eso no tengo una pregunta del cliente a la que contestar "
    "ahora mismo. Si ha escrito algo más que no me ha llegado, o prefieres que te prepare un "
    "seguimiento, dímelo y lo redacto."
)
CORREO_CORTO = {
    "asunto": "Re: Metallplatten und T-Shirts",
    "cuerpo": ("Hallo Klaus,\n\nkurz: UV-LED für Metall, DTF für T-Shirts.\n\n"
               "Viele Grüße\nAdmin"),
    "idioma": "de", "aviso": "", "pendiente": [], "enlaces_usados": [],
}
SEGUIMIENTO = {
    "asunto": "Seguimiento del presupuesto P-2026-044",
    "cuerpo": ("Hola Klaus,\n\nte escribo por el presupuesto P-2026-044 de hace tres semanas: "
               "¿lo recibiste bien? Si quieres, te reservo la máquina.\n\nUn saludo\nAdmin"),
    "idioma": "es", "aviso": "", "pendiente": [], "enlaces_usados": [],
}


def _mensajes_largos(n: int) -> list[dict[str, Any]]:
    """Un hilo con cuerpo de verdad, para que el contexto pese lo que pesa."""
    salida = []
    for i in range(n):
        if i % 2 == 0:
            salida.append({"de": KLAUS, "texto": (
                f"Guten Tag ({i}), ich habe noch Fragen zur UV-LED-Druckmaschine für "
                "Metallplatten und zum DTF-Verfahren für T-Shirts: Druckgeschwindigkeit, "
                "Tintenverbrauch pro Quadratmeter, Wartungsintervalle und die Lieferzeit "
                "nach Deutschland. Außerdem interessiert mich die Schulung vor Ort.")})
        else:
            salida.append({"de": ALIAS, "lado": "nosotros", "para": KLAUS, "texto": (
                f"Hallo Klaus ({i}), danke für die Nachricht. Die Druckgeschwindigkeit hängt "
                "vom Modus ab; den Tintenverbrauch und die Wartungsintervalle schicke ich "
                "Ihnen im Datenblatt. Die Lieferzeit nach Deutschland klären wir mit dem "
                "Lager und melden uns.")})
    return salida


def _turno(http: TestClient, borrador_id: str, instruccion: str, rol: str = "admin") -> Any:
    return http.post(f"/api/emails/asistente/{borrador_id}/turno",
                     headers=auth_headers(http, rol), json={"instruccion": instruccion})


def _nuevo(http: TestClient, rol: str = "admin", **payload: Any) -> Any:
    return http.post("/api/emails/asistente", headers=auth_headers(http, rol), json=payload)


# --- 1. el cuerpo es un correo o está vacío ------------------------------------------------


def test_sin_mensaje_nuevo_del_cliente_el_cuerpo_queda_vacio_y_el_motivo_va_a_la_franja(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """El caso del 11/10/2026: el último mensaje del hilo es nuestro. Sin
    instrucción no hay nada que contestar: no se llama al modelo (no cuesta
    nada), el cuerpo queda vacío y el aviso lo explica. Y el contexto queda
    guardado: la instrucción que venga después lo reutiliza."""
    falso = _ClaudeFalso(SEGUIMIENTO)
    with session_factory() as s:
        klaus = _contacto(s)
        hilo = _hilo(s, contacto=klaus, mensajes=[
            {"de": KLAUS, "texto": "Os paso la lista de equipos y consumibles que necesitamos."},
            {"de": ALIAS, "lado": "nosotros", "para": KLAUS,
             "texto": "Gracias, te adjunto el presupuesto P-2026-044."},
        ], asunto="Equipos y consumibles")
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["texto"] == "" and b["html"] == ""
    assert "no hay nada nuevo del cliente" in b["aviso"]
    assert "Dime qué quieres decirle" in b["aviso"]
    assert b["turno"] == 0 and b["conversacion_id"] == b["id"] and b["instruccion"] is None
    assert falso.llamadas == []                                  # ni una llamada
    with session_factory() as s:
        fila = s.get(BorradorAsistente, b["id"])
        assert fila.contexto_texto and "HILO (2 mensajes" in fila.contexto_texto
        assert fila.coste_usd == 0 and s.scalar(select(LlamadaIA)) is None
    # La persona dice qué quiere: es el primer borrador de verdad, con el
    # modelo del primer borrador y sobre el contexto ya montado.
    with patch.object(llm, "_invoke_claude", falso), \
            patch.object(contexto_mod, "construir", wraps=contexto_mod.construir) as construir:
        r = _turno(http, b["id"], "pregúntale si recibió el presupuesto y si quiere que le "
                                  "reserve el plato PT5")
    assert r.status_code == 201, r.text
    t = r.json()["borrador"]
    assert construir.call_count == 0                             # no se rearma el contexto
    assert t["texto"].startswith("Hola Klaus") and t["turno"] == 1
    assert t["conversacion_id"] == b["id"] and t["padre_id"] == b["id"]
    assert t["instruccion"].startswith("pregúntale")
    llamada = falso.ultimo
    assert llamada["model"] == get_settings().anthropic_model    # el del primer borrador
    assert "INSTRUCCIÓN DE QUIEN ENVÍA" in llamada["user"] and "plato PT5" in llamada["user"]
    assert llamada["bloques"][0]["text"] == fila.contexto_texto  # el mismo bloque, byte a byte
    with session_factory() as s:
        assert s.get(BorradorAsistente, b["id"]).estado == ESTADO_REGENERADO
        assert s.scalar(select(LlamadaIA)).proposito == "asistente_borrador"


def test_un_informe_para_el_usuario_nunca_llega_al_cuerpo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Si el modelo se despista y escribe un informe para la persona de BoHub
    en el cuerpo, el cuerpo se vacía y el texto pasa al aviso."""
    informe = {**RESPUESTA_IA, "cuerpo": INFORME, "aviso": ""}
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[{"de": TALENTOS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(informe)):
        r = _pedir(http, thread_id)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["texto"] == "" and b["html"] == ""
    assert "La IA no ha redactado un correo" in b["aviso"] and "he revisado el" in b["aviso"]
    assert INFORME in b["aviso"]
    # Y el prompt se lo pide: la regla primera va en las instrucciones.
    assert "REGLA PRIMERA" in redactor.PROMPT_SISTEMA and '"aviso"' in redactor.PROMPT_SISTEMA


def test_la_guardia_reconoce_informes_y_saludos_a_quien_pulsa() -> None:
    assert texto.parece_informe_al_usuario(INFORME, nombre_usuario="Manel") is not None
    assert texto.parece_informe_al_usuario(
        "Hola Manel, aquí tienes el borrador.", nombre_usuario="Manel Puig",
        nombre_destinatario="Marny López") is not None
    # El destinatario se llama como quien pulsa: no es un informe.
    assert texto.parece_informe_al_usuario(
        "Hola Manel, gracias por tu mensaje.", nombre_usuario="Manel",
        nombre_destinatario="Manel Soler") is None
    assert texto.parece_informe_al_usuario(
        "Hola Marny,\n\nte escribo por el presupuesto del 26.", nombre_usuario="Manel",
        nombre_destinatario="Marny") is None
    assert texto.parece_informe_al_usuario(
        "I have reviewed the thread and there is nothing new to reply to. Let me know if you "
        "want a follow-up.", nombre_usuario="Bart") is not None
    assert texto.parece_informe_al_usuario("", nombre_usuario="Bart") is None


# --- 2. la instrucción manda; sin instrucción, como #544 ----------------------------------


def test_con_instruccion_el_prompt_la_lleva_y_manda_sobre_el_hilo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        klaus = _contacto(s)
        hilo = _hilo(s, contacto=klaus, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="dile que el plato PT5 ya está disponible y "
                                                "pregúntale si recibió el presupuesto")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["instruccion"].startswith("dile que el plato PT5")
    bloques = falso.ultimo["bloques"]
    assert len(bloques) == 2
    assert bloques[0]["cache_control"] == {"type": "ephemeral"}       # el contexto
    assert "cache_control" not in bloques[1]                            # la instrucción
    peticion = bloques[1]["text"]
    assert "INSTRUCCIÓN DE QUIEN ENVÍA (manda sobre lo que deduzcas del hilo)" in peticion
    assert "plato PT5" in bloques[1]["text"] and "plato PT5" not in bloques[0]["text"]
    assert "HILO (1 mensajes" in bloques[0]["text"]
    # La persona cambió el «Para»: el borrador va a esa dirección.
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="hola", destinatario="otra@elbarquito.net")
    assert r.json()["borrador"]["destinatario"] == "otra@elbarquito.net"


def test_sin_instruccion_con_mensaje_nuevo_del_cliente_se_comporta_como_antes(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        hilo = _hilo(s, contacto=_contacto(s), mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id)
    assert r.status_code == 201 and r.json()["borrador"]["texto"].startswith("Hallo Klaus")
    peticion = falso.ultimo["bloques"][1]["text"]
    assert "Redacta ahora el borrador de la respuesta al último mensaje del cliente" in peticion
    assert "INSTRUCCIÓN" not in peticion
    assert r.json()["borrador"]["aviso"] is None and r.json()["borrador"]["turno"] == 0


# --- 3. la conversación: correcciones sin rearmar, caché, volver atrás ----------------------


def test_una_correccion_no_vuelve_a_montar_el_contexto_y_manda_mucho_menos(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        klaus = _contacto(s, empresa="El Barquito SL", interes="uv")
        _pedido(s, klaus, numero="M-1001", descripcion="Impresora UV LED mediana 60x90")
        hilo = _hilo(s, contacto=klaus, mensajes=_mensajes_largos(7))   # el último, del cliente
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        primero = _pedir(http, thread_id).json()["borrador"]
    primera = falso.ultimo
    falso.respuesta = CORREO_CORTO
    with patch.object(llm, "_invoke_claude", falso), \
            patch.object(contexto_mod, "construir", wraps=contexto_mod.construir) as construir:
        r = _turno(http, primero["id"], "más corto")
    assert r.status_code == 201, r.text
    segundo = r.json()["borrador"]
    correccion = falso.ultimo
    assert construir.call_count == 0
    # El bloque de contexto es el mismo, byte a byte, y marcado como cacheable:
    # la API lo lee de caché. Lo que va a precio entero es la petición, mucho
    # menor que el primer borrador entero.
    assert correccion["bloques"][0]["text"] == primera["bloques"][0]["text"]
    assert correccion["bloques"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in correccion["bloques"][1]
    contexto_chars = len(correccion["bloques"][0]["text"])
    peticion_chars = len(correccion["bloques"][1]["text"])
    primero_chars = len(primera["bloques"][0]["text"]) + len(primera["bloques"][1]["text"])
    assert peticion_chars < 0.3 * primero_chars
    assert contexto_chars / (contexto_chars + peticion_chars) > 0.75   # casi todo, de caché
    assert "BORRADOR ACTUAL" in correccion["user"] and "CORRECCIÓN DE QUIEN ENVÍA: «más corto»"
    assert primero["texto"] in correccion["bloques"][1]["text"]
    assert correccion["model"] == "claude-haiku-5-5"                   # el de las correcciones
    assert primera["model"] == get_settings().anthropic_model           # el del primer borrador
    assert segundo["turno"] == 1 and segundo["padre_id"] == primero["id"]
    assert segundo["conversacion_id"] == primero["id"]
    assert segundo["texto"].startswith("Hallo Klaus,\n\nkurz")
    assert segundo["contexto"]["pedidos"] == 1                          # lo que ha usado, igual
    with session_factory() as s:
        assert s.get(BorradorAsistente, primero["id"]).estado == ESTADO_REGENERADO
        llamadas = list(s.scalars(select(LlamadaIA).order_by(LlamadaIA.created_at)))
        assert [ll.proposito for ll in llamadas] == ["asistente_borrador", "asistente_correccion"]


def test_tres_correcciones_mantienen_la_conversacion_y_se_puede_volver_atras(
    session_factory: sessionmaker, http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    falso = _ClaudeFalso()
    headers = auth_headers(http, "admin")
    with session_factory() as s:
        hilo = _hilo(s, contacto=_contacto(s), mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        t0 = _pedir(http, thread_id).json()["borrador"]
        turnos = [t0]
        for n, instruccion in enumerate(("más corto", "en inglés", "no menciones el precio"), 1):
            falso.respuesta = {**CORREO_CORTO, "cuerpo": f"Hallo Klaus, Fassung {n}."}
            r = _turno(http, turnos[-1]["id"], instruccion)
            assert r.status_code == 201, r.text
            turnos.append(r.json()["borrador"])
    assert [t["turno"] for t in turnos] == [0, 1, 2, 3]
    assert {t["conversacion_id"] for t in turnos} == {t0["id"]}
    assert [t["padre_id"] for t in turnos[1:]] == [t["id"] for t in turnos[:-1]]
    assert [t["instruccion"] for t in turnos[1:]] == ["más corto", "en inglés",
                                                      "no menciones el precio"]
    with session_factory() as s:
        estados = {t["id"]: s.get(BorradorAsistente, t["id"]).estado for t in turnos}
    assert [estados[t["id"]] for t in turnos] == [ESTADO_REGENERADO] * 3 + [ESTADO_GENERADO]
    # La persona vuelve al segundo borrador y lo envía tal cual: ese queda
    # enviado y el último, que no se usó, como regenerado.
    enviados: list[dict[str, Any]] = []

    class _GmailFalso:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **kwargs: Any) -> dict[str, Any]:
            enviados.append(kwargs)
            return {"id": f"gmail-{len(enviados)}", "threadId": "gmail-thr-1"}

    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", _GmailFalso)
    elegido = turnos[2]
    r = http.post("/api/emails/send", headers=headers, json={
        "from_alias": ALIAS, "to": [KLAUS], "subject": elegido["asunto"],
        "body_html": elegido["html"], "asistente_borrador_id": elegido["id"],
    })
    assert r.status_code == 201, r.text
    with session_factory() as s:
        assert s.get(BorradorAsistente, elegido["id"]).estado == ESTADO_ENVIADO_TAL_CUAL
        assert s.get(BorradorAsistente, turnos[3]["id"]).estado == ESTADO_REGENERADO
    # Un turno ya resuelto no admite más correcciones.
    with patch.object(llm, "_invoke_claude", falso):
        assert _turno(http, elegido["id"], "otra vez").status_code == 409
    assert len(enviados) == 1


def test_cambiar_el_alcance_rearma_el_contexto_y_es_un_borrador_nuevo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        klaus = _contacto(s, empresa="El Barquito SL")
        hilo = _hilo(s, contacto=klaus, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso), \
            patch.object(contexto_mod, "construir", wraps=contexto_mod.construir) as construir:
        primero = _pedir(http, thread_id, alcance="contacto").json()["borrador"]
        _turno(http, primero["id"], "más corto")
        assert construir.call_count == 1
        otro = _pedir(http, thread_id, alcance="empresa",
                      regenerar_de=primero["id"]).json()["borrador"]
        assert construir.call_count == 2
    assert otro["conversacion_id"] == otro["id"] != primero["id"]
    assert otro["alcance"] == "empresa"
    with session_factory() as s:
        # La conversación anterior entera queda como regenerada.
        estados = [t.estado for t in s.scalars(select(BorradorAsistente).where(
            BorradorAsistente.conversacion_id == primero["id"]))]
        assert estados and set(estados) == {ESTADO_REGENERADO}


def test_descartar_descarta_la_conversacion_entera(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        t0 = _pedir(http, thread_id).json()["borrador"]
        t1 = _turno(http, t0["id"], "más corto").json()["borrador"]
    r = http.post(f"/api/emails/asistente/{t1['id']}/descartar",
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200 and r.json()["borrador"]["estado"] == ESTADO_DESCARTADO
    with session_factory() as s:
        assert s.get(BorradorAsistente, t0["id"]).estado == ESTADO_REGENERADO
        assert s.get(BorradorAsistente, t1["id"]).estado == ESTADO_DESCARTADO
    # Otro usuario no puede tocar la conversación de un borrador sin hilo visible.
    assert _turno(http, t1["id"], "x", rol="user").status_code in (403, 409)


# --- 4. el registro: borrador frente a corrección, y el borrador terminado --------------------


def test_el_registro_distingue_borrador_de_correccion_y_suma_el_borrador_terminado(
    session_factory: sessionmaker, http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    falso = _ClaudeFalso(tokens=(3000, 300), cache=(0, 2500))
    headers = auth_headers(http, "admin")
    with session_factory() as s:
        hilo = _hilo(s, contacto=_contacto(s), mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        t0 = _pedir(http, thread_id).json()["borrador"]
        falso.tokens, falso.cache = (400, 120), (2500, 0)        # la corrección lee de caché
        t1 = _turno(http, t0["id"], "más corto").json()["borrador"]
    assert t0["tokens_cache_escritura"] == 2500 and t1["tokens_cache_lectura"] == 2500
    modelo_t0 = get_settings().anthropic_model
    assert t0["coste_usd"] == pytest.approx(coste_usd(modelo_t0, 3000, 300, 0, 2500))
    assert t1["coste_usd"] == pytest.approx(coste_usd("claude-haiku-5-5", 400, 120, 2500, 0))

    class _GmailFalso:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def send_message(self, **_kw: Any) -> dict[str, Any]:
            return {"id": "gmail-1", "threadId": "gmail-thr-1"}

    monkeypatch.setattr("app.integrations.gmail.service.GmailClient", _GmailFalso)
    r = http.post("/api/emails/send", headers=headers, json={
        "from_alias": ALIAS, "to": [KLAUS], "subject": t1["asunto"],
        "body_html": "<p>Hallo Klaus, ganz anders.</p>", "asistente_borrador_id": t1["id"],
    })
    assert r.status_code == 201, r.text
    uso = http.get("/api/erp/ia/uso?dias=7", headers=headers).json()
    por_proposito = {p["clave"]: p for p in uso["por_proposito"]}
    assert set(por_proposito) == {"asistente_borrador", "asistente_correccion"}
    assert por_proposito["asistente_correccion"]["tokens_cache_lectura"] == 2500
    por_modelo = {m["clave"]: m for m in uso["por_modelo"]}
    assert set(por_modelo) == {modelo_t0, "claude-haiku-5-5"}
    conv = uso["conversaciones"]
    assert conv["total"] == 1 and conv["terminadas"] == 1 and conv["correcciones"] == 1
    assert conv["coste_terminadas_usd"] == pytest.approx(
        round(t0["coste_usd"] + t1["coste_usd"], 4), abs=1e-4)
    assert conv["coste_medio_terminada_usd"] == conv["coste_terminadas_usd"]
    assert conv["turnos_medios_terminada"] == 2.0
    assert uso["borradores"]["enviados_editados"] == 1 and uso["borradores"]["regenerados"] == 1
    with session_factory() as s:
        assert s.get(BorradorAsistente, t1["id"]).estado == ESTADO_ENVIADO_EDITADO


# --- 5. un correo nuevo, sin hilo, con el pasado del cliente ---------------------------------


def test_correo_nuevo_a_un_contacto_con_historico_usa_su_expediente_y_acierta_la_maquina(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso(SEGUIMIENTO)
    with session_factory() as s:
        klaus = _contacto(s, empresa="El Barquito SL", interes="dtf")
        _pedido(s, klaus, numero="M-1001", descripcion="FLUX Ador 20W Diodenlaser + Rotary")
        _hilo(s, contacto=klaus, mensajes=[
            {"de": ALIAS, "lado": "nosotros", "para": KLAUS,
             "texto": "Te adjunto el presupuesto P-2026-044 de la Ador con el rotary."},
        ], asunto="Presupuesto P-2026-044")
        s.commit()
        contact_id = klaus.id
    with patch.object(llm, "_invoke_claude", falso):
        r = _nuevo(http, destinatario=KLAUS, alcance="contacto",
                   instruccion="Han pasado tres semanas desde el presupuesto P-2026-044, "
                               "escríbele un seguimiento y pregúntale si quiere que le reserve "
                               "la máquina")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["thread_id"] is None and b["sin_hilo"] is True and b["contact_id"] == contact_id
    prompt = falso.ultimo["user"]
    assert "SIN HILO: es un correo nuevo" in prompt and "HILO (" not in prompt
    assert "modelo FLUX: Ador" in prompt and "Pedido M-1001" in prompt
    assert "ASUNTO: es un correo nuevo; propón tú el asunto" in prompt
    assert "INSTRUCCIÓN DE QUIEN ENVÍA" in prompt and "tres semanas" in prompt
    # El Nivel 3 se elige por la instrucción: la referencia del presupuesto
    # trae el correo viejo, que sin hilo no tendría con qué compararse.
    motivos = [h["motivo"] for h in b["contexto"]["historico"]]
    assert motivos == ["menciona la referencia P-2026-044"]
    assert "presupuesto P-2026-044 de la Ador" in prompt
    assert b["contexto"]["niveles"] == [2, 3] and b["mensajes_hilo"] == 0
    # El asunto lo propone la IA (y es editable en el compositor).
    assert b["asunto"] == "Seguimiento del presupuesto P-2026-044"
    assert b["texto"].startswith("Hola Klaus")
    enlaces = [e["url"] for e in b["enlaces"]]
    assert "https://www.mqeurope.com/dtf-textile-printing/" in enlaces


def test_correo_nuevo_sin_instruccion_no_llama_al_modelo_y_lo_dice(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso()
    with session_factory() as s:
        _contacto(s)
        s.commit()
    with patch.object(llm, "_invoke_claude", falso):
        assert _nuevo(http, destinatario=KLAUS, instruccion="   ").status_code == 400
        assert _nuevo(http, destinatario=KLAUS, instruccion="").status_code == 422
        assert _nuevo(http, destinatario=KLAUS, instruccion="hola",
                      alcance="hilo").status_code == 400
    assert falso.llamadas == []
    r = _nuevo(http, destinatario=KLAUS, instruccion="   ")
    assert "Dime qué quieres decirle" in r.json()["detail"]


def test_correo_nuevo_a_un_contacto_aleman_sale_en_aleman(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        klaus = _contacto(s, idioma="de")
        ctx = contexto_mod.construir(s, thread=None, usuario=_admin(s), destinatario=KLAUS,
                                     instruccion="pregúntale por el pedido")
        assert (ctx.idioma, ctx.idioma_fuente) == ("de", "ficha")
        assert ctx.contacto["id"] == klaus.id and ctx.niveles == [2, 3]
        assert "IDIOMA DE LA RESPUESTA: de (alemán)" in redactor.prompt_usuario(ctx)
        # Sin idioma en la ficha: el de lo último que escribió esa persona.
        otro = _contacto(s, email="otro@elbarquito.net", idioma=None)
        _hilo(s, contacto=otro, mensajes=[{"de": otro.email, "texto": MENSAJE_KLAUS}],
              asunto="Frage")
        ctx2 = contexto_mod.construir(s, thread=None, usuario=_admin(s), destinatario=otro.email,
                                      instruccion="seguimiento")
        assert (ctx2.idioma, ctx2.idioma_fuente) == ("de", "historico")
        # Y sin nada: castellano.
        ctx3 = contexto_mod.construir(s, thread=None, usuario=_admin(s),
                                      destinatario="nadie@x.io", instruccion="seguimiento")
        assert (ctx3.idioma, ctx3.idioma_fuente) == ("es", "defecto")


def test_correo_nuevo_a_una_direccion_que_no_es_contacto_redacta_igual(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeFalso(SEGUIMIENTO)
    with patch.object(llm, "_invoke_claude", falso):
        r = _nuevo(http, destinatario="nadie@x.io", instruccion="dale las gracias por la visita")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["contact_id"] is None and b["contexto"]["contacto"] is None
    assert b["contexto"]["niveles"] == [] and b["destinatario"] == "nadie@x.io"
    assert "remitente desconocido" in falso.ultimo["user"]
    assert b["texto"].startswith("Hola Klaus")
    with session_factory() as s:
        assert s.scalar(select(EmailMessage).where(
            EmailMessage.direction == EmailDirection.OUTBOUND)) is None


# --- 6. el modelo por tarea y el pensamiento -------------------------------------------------


def test_cambiar_el_modelo_en_configuracion_cambia_el_que_se_llama(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    antes = http.get("/api/erp/settings", headers=headers).json()["asistente"]
    assert antes["modelo_borrador"] is None and antes["modelo_correccion"] == "claude-haiku-5-5"
    assert "claude-sonnet-5-5" in antes["modelos"] and antes["pensamiento_extendido"] is False
    r = http.patch("/api/erp/settings", headers=headers, json={"asistente": {
        "modelo_borrador": "claude-opus-5-5", "modelo_correccion": "claude-haiku-4-5",
        "modelos": ["claude-opus-5-5", "Claude-Haiku-4-5", "claude-opus-5-5"],
    }})
    assert r.status_code == 200, r.text
    assert r.json()["asistente"]["modelo_borrador"] == "claude-opus-5-5"
    assert r.json()["asistente"]["modelos"] == ["claude-opus-5-5", "claude-haiku-4-5"]
    assert http.patch("/api/erp/settings", headers=headers, json={"asistente": {
        "modelo_borrador": "no vale; espacios"}}).status_code == 400
    falso = _ClaudeFalso()
    with session_factory() as s:
        hilo = _hilo(s, contacto=_contacto(s), mensajes=[{"de": KLAUS, "texto": MENSAJE_KLAUS}])
        s.commit()
        thread_id = hilo.id
    with patch.object(llm, "_invoke_claude", falso):
        t0 = _pedir(http, thread_id).json()["borrador"]
        _turno(http, t0["id"], "más corto")
    assert [ll["model"] for ll in falso.llamadas] == ["claude-opus-5-5", "claude-haiku-4-5"]
    # Pensamiento apagado: Opus 5.5 no se puede apagar (esfuerzo bajo); Haiku 4.5 no piensa.
    assert falso.llamadas[0]["parametros"] == {"output_config": {"effort": "low"}}
    assert falso.llamadas[1]["parametros"] is None
    assert t0["modelo"] == "claude-opus-5-5"
    # Con el pensamiento extendido encendido no se añade nada: manda el modelo.
    http.patch("/api/erp/settings", headers=headers, json={"asistente": {
        "pensamiento_extendido": True}})
    with patch.object(llm, "_invoke_claude", falso):
        _pedir(http, thread_id)
    assert falso.ultimo["parametros"] is None


def test_los_parametros_de_pensamiento_por_modelo() -> None:
    apagado = {"thinking": {"type": "disabled"}}
    assert modelos.parametros_de_llamada("claude-haiku-5-5", pensamiento_extendido=False) == apagado
    assert modelos.parametros_de_llamada("claude-opus-5", pensamiento_extendido=False) == apagado
    assert modelos.parametros_de_llamada("claude-sonnet-4-6-20260301",
                                         pensamiento_extendido=False) == apagado
    assert modelos.parametros_de_llamada("claude-sonnet-5-5", pensamiento_extendido=False) == {
        "thinking": {"type": "between_tools"}}
    bajo = {"output_config": {"effort": "low"}}
    assert modelos.parametros_de_llamada("claude-opus-5-5", pensamiento_extendido=False) == bajo
    assert modelos.parametros_de_llamada("claude-fable-5-1", pensamiento_extendido=False) == bajo
    assert modelos.parametros_de_llamada("claude-haiku-4-5", pensamiento_extendido=False) == {}
    assert modelos.parametros_de_llamada("otro-modelo", pensamiento_extendido=False) == {}
    assert modelos.parametros_de_llamada("claude-opus-5-5", pensamiento_extendido=True) == {}
    assert modelos.es_nombre_valido("claude-haiku-5-5") and not modelos.es_nombre_valido("x y")
    assert modelos.normalizar_lista(["Claude-Opus-5-5", "", "mal nombre"]) == ["claude-opus-5-5"]


def test_con_el_pensamiento_apagado_el_texto_se_extrae_igual_y_la_cache_se_anota() -> None:
    """La zona del fallo de #537: la respuesta sin bloque de razonamiento se
    lee igual, los parámetros viajan en `extra_body` (el SDK fijado no los
    conoce), los bloques con `cache_control` llegan tal cual y los tokens de
    caché quedan en el uso."""
    recibido: dict[str, Any] = {}
    mensaje = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"asunto": "Re", "cuerpo": "Hola"}')],
        model="claude-haiku-5-5",
        usage=SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=2400,
                              cache_creation_input_tokens=0),
    )

    class _Cliente:
        def __init__(self, api_key: str) -> None:
            def crear(**kw: Any) -> Any:
                recibido.update(kw)
                return mensaje
            self.messages = SimpleNamespace(create=crear)

    bloques = redactor.mensaje_usuario("CONTEXTO " * 50, "PETICIÓN")
    with patch("anthropic.Anthropic", _Cliente):
        salida = llm._invoke_claude(
            api_key="k", model="claude-haiku-5-5", system_prompt="s", user_prompt=bloques,
            max_tokens=100, parametros={"thinking": {"type": "disabled"}})
    assert salida == '{"asunto": "Re", "cuerpo": "Hola"}'
    assert recibido["extra_body"] == {"thinking": {"type": "disabled"}}
    assert recibido["system"] == "s" and recibido["max_tokens"] == 100
    assert recibido["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert recibido["messages"][0]["content"][1] == {"type": "text", "text": "PETICIÓN"}
    uso = llm.tomar_ultimo_uso()
    assert uso is not None and (uso.tokens_entrada, uso.tokens_salida) == (120, 40)
    assert (uso.tokens_cache_lectura, uso.tokens_cache_escritura) == (2400, 0)
    # Sin parámetros no viaja `extra_body` (lo demás, igual).
    recibido.clear()
    with patch("anthropic.Anthropic", _Cliente):
        llm._invoke_claude(api_key="k", model="claude-haiku-5-5", system_prompt="s",
                           user_prompt="u")
    assert "extra_body" not in recibido and recibido["messages"][0]["content"] == "u"


def test_el_coste_cuenta_la_cache_a_su_precio() -> None:
    assert precio_cache("claude-sonnet-5-5") == (pytest.approx(0.2), pytest.approx(2.5))
    assert precio_cache("claude-opus-5-5") == (pytest.approx(0.20), pytest.approx(5.0))
    assert precio_cache("claude-haiku-5-5") == (pytest.approx(0.01), pytest.approx(0.125))
    # 400 de entrada + 2.500 leídos de caché + 120 de salida en Haiku 5.5.
    assert coste_usd("claude-haiku-5-5", 400, 120, 2500, 0) == pytest.approx(
        (400 * 0.10 + 2500 * 0.01 + 120 * 0.50) / 1_000_000)
    # Lo mismo sin caché habría costado la entrada entera: la caché sale a cuenta.
    assert coste_usd("claude-haiku-5-5", 2900, 120) > coste_usd("claude-haiku-5-5", 400, 120, 2500)
    assert coste_usd("claude-opus-5-5", 0, 0, 1000, 0) == pytest.approx(0.0002)
    assert coste_usd("claude-sonnet-4-6-20260301", 1500, 120) == pytest.approx(0.0063)


# --- 7. el destinatario por defecto es el cliente, y el aviso de compañero ---------------------


def test_el_destinatario_por_defecto_es_el_cliente_no_el_companero_que_escribio_el_ultimo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    with session_factory() as s:
        hilo = _hilo(s, contacto=None, mensajes=[
            {"de": TALENTOS, "texto": "Os paso la lista de equipos y consumibles."},
            {"de": ALIAS, "lado": "nosotros", "para": TALENTOS, "texto": "Adjunto el presupuesto."},
            # Manel contesta desde Gmail: entra capturado como entrada, con su dirección del grupo.
            {"de": MANEL, "texto": "Admin, ¿le damos un 5% si cierra este mes?", "para": ALIAS},
        ], asunto="Equipos y consumibles")
        interno = _hilo(s, contacto=None, mensajes=[
            {"de": MANEL, "texto": "¿Tenemos stock del plato PT5?", "para": ALIAS},
        ], asunto="Stock PT5")
        s.commit()
        thread_id, interno_id = hilo.id, interno.id
    detalle = http.get(f"/api/emails/threads/{thread_id}", headers=headers).json()
    assert detalle["reply_to_suggestion"] == TALENTOS and detalle["reply_to_interno"] is False
    # Sin nadie de fuera (un hilo interno), el último que no es el operador, y se avisa.
    interno_detalle = http.get(f"/api/emails/threads/{interno_id}", headers=headers).json()
    assert interno_detalle["reply_to_suggestion"] == MANEL
    assert interno_detalle["reply_to_interno"] is True
    # El asistente sobre el hilo de cliente: el último mensaje es de casa (Manel),
    # así que sin instrucción no hay nada que contestar; con ella, el correo va al cliente.
    falso = _ClaudeFalso(SEGUIMIENTO)
    with patch.object(llm, "_invoke_claude", falso):
        vacio = _pedir(http, thread_id).json()["borrador"]
        assert vacio["texto"] == "" and falso.llamadas == []
        con = _pedir(http, thread_id,
                     instruccion="pregúntale si recibió el presupuesto").json()["borrador"]
    assert con["destinatario"] == TALENTOS and con["destinatario_interno"] is False
    # Y si la persona escribe a Manel, la franja lo dice y el prompt también.
    with patch.object(llm, "_invoke_claude", falso):
        a_manel = _pedir(http, thread_id, instruccion="dile que sí al 5%",
                         destinatario=MANEL).json()["borrador"]
    assert a_manel["destinatario_interno"] is True
    assert "escribes a un COMPAÑERO" in falso.ultimo["user"]


def test_un_hilo_sin_mensajes_del_cliente_tampoco_rompe(session_factory: sessionmaker) -> None:
    """Solo salida (un correo que enviamos y nadie contestó): sin instrucción,
    nada que contestar; con ella, se redacta."""
    with session_factory() as s:
        klaus = _contacto(s)
        hilo = _hilo(s, contacto=klaus, mensajes=[
            {"de": ALIAS, "lado": "nosotros", "para": KLAUS, "texto": "Te paso el catálogo."},
        ], asunto="Catálogo")
        ctx = contexto_mod.construir(s, thread=hilo, usuario=_admin(s))
        assert ctx.ultimo_del_cliente is None and ctx.destinatario == KLAUS
        assert "No hay ningún mensaje del cliente" in redactor.prompt_usuario(ctx)
        assert ctx.resumen()["sin_hilo"] is False and ctx.resumen()["destinatario"] == KLAUS
