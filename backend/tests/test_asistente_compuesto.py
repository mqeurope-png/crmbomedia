"""El asistente y los correos compuestos: la IA elige la estructura (bloques,
productos por su referencia del catálogo, textos en markdown) y BoHub genera
el HTML. La respuesta normal en texto sigue siendo la de siempre.

Los casos salen del hilo real de administracion@talentosdiversos.org: pide
las máquinas de DTF para su taller textil.
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

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.conocimiento import FUENTE_COMPOSITOR, TIPO_MAQUINA, MarcaCatalogo, ProductoCatalogo
from app.models.crm import User, UserEmailAliasPref, UserRole
from app.models.ia import BorradorAsistente
from app.services import llm
from app.services.correo_compuesto import ia
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users
from tests.test_asistente import ALIAS, _ClaudeFalso, _contacto, _hilo, _pedir

TALENTOS = "administracion@talentosdiversos.org"
AHORA = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
MENSAJE = ("Hola, estamos montando un taller textil y necesitamos una DTF para camisetas. "
           f"¿Qué máquinas de DTF tenéis y a qué precio? Vimos este vídeo: {VIDEO}")


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
def http(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
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


def _catalogo(s: Session) -> None:
    """Las máquinas DTF de MBO (una sin precio público) y la marca."""
    for oid, nombre, precio in (("orion-20", "Orion 20", "4.990 €"),
                                ("lyra-35", "Lyra 35", "7.900 €"),
                                ("astra-35", "Astra 35 Pro", None)):
        s.add(ProductoCatalogo(
            fuente=FUENTE_COMPOSITOR, tipo=TIPO_MAQUINA, marca="mbo_dtf", origen_id=oid,
            nombre=nombre, descripcion=f"{nombre}: impresora DTF para camisetas.",
            caracteristicas_json=json.dumps(["Ancho 60 cm", "Cabezales i3200"]), categoria="60 cm",
            imagen=f"https://cdn.example/{oid}.jpg", precio_texto=precio,
            precio_publico=precio is not None, moneda="EUR" if precio else None,
            enlace=f"https://mboprinters.com/es/dtf/{oid}/", idioma="es", sitio="mboprinters",
            actualizado_origen=AHORA, visto_en=AHORA))
    s.add(MarcaCatalogo(nombre="mbo_dtf", logo="https://cdn.example/mbo.png", color="#e30613",
                        idioma_defecto="es",
                        urls_json=json.dumps({"es": "https://mboprinters.com/es/"})))
    s.commit()


def _hilo_talentos(s: Session) -> str:
    _catalogo(s)
    hilo = _hilo(s, contacto=_contacto(s, TALENTOS, interes="dtf"),
                 mensajes=[{"de": TALENTOS, "texto": MENSAJE}])
    s.commit()
    return hilo.id


def _turno(http: TestClient, borrador_id: str, instruccion: str, **extra: Any) -> Any:
    return http.post(f"/api/emails/asistente/{borrador_id}/turno",
                     headers=auth_headers(http, "admin"),
                     json={"instruccion": instruccion, **extra})


def _refs_de_peticion(texto: str) -> dict[str, str]:
    """`{nombre: ref}` de las líneas del CATÁLOGO del contexto («- [P1] Orion 20 · …»)."""
    salida = {}
    for linea in texto.splitlines():
        if linea.startswith("- [P") or linea.startswith("- [C"):
            ref, resto = linea[3:].split("] ", 1)
            salida[resto.split(" · ")[0]] = ref
    return salida


COMPUESTO_DTF = {
    "modo": "compuesto", "asunto": "Re: Equipos DTF", "idioma": "es", "aviso": "", "pendiente": [],
    "bloques": [
        {"tipo": "texto", "texto": "Hola Marny,\n\npara el taller textil te encajan estas "
                                   "máquinas de **DTF**:"},
        {"tipo": "marca", "marca": "mbo_dtf"},
        {"tipo": "productos_fila", "productos": ["P1", "P2", "P3"]},
        {"tipo": "video", "video": "V1", "titulo": "La Orion 20 en marcha"},
        {"tipo": "cta", "botones": [{"texto": "Ver toda la gama", "enlace": "M:mbo_dtf"}]},
        {"tipo": "texto", "texto": "¿Cuántas camisetas al día hacéis? Con eso te preparo la "
                                   "oferta.\n\nUn saludo,\nAdmin"},
    ],
}


def test_la_gama_dtf_sale_compuesta_y_el_aviso_de_un_pedido_sale_en_texto(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    assert ia.modo_pedido("mándale la gama DTF") == ia.MODO_COMPUESTO
    assert ia.modo_pedido("prepárale un correo con las máquinas") == ia.MODO_COMPUESTO
    assert ia.modo_pedido("Dile que el pedido sale el martes") == ia.MODO_NORMAL
    assert ia.modo_pedido("contéstale que el pedido sale el martes") == ia.MODO_NORMAL
    assert ia.modo_pedido("más corto") is None and ia.modo_pedido("") is None
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    falso = _ClaudeFalso(COMPUESTO_DTF)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="mándale la gama DTF")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    contexto, peticion = (bloque["text"] for bloque in falso.ultimo["bloques"])
    assert "FORMATO DE ESTE TURNO: CORREO COMPUESTO" in peticion
    assert f"[V1] {VIDEO}" in peticion and "mbo_dtf (web https://mboprinters.com/es/)" in peticion
    refs = _refs_de_peticion(contexto)
    assert set(refs) == {"Orion 20", "Lyra 35", "Astra 35 Pro"}
    assert "CORREO COMPUESTO" in falso.ultimo["system"]
    assert b["modo"] == "compuesto"
    estructura = b["estructura"]
    assert [x["tipo"] for x in estructura["bloques"]] == [
        "texto", "marca", "productos_fila", "video", "cta", "texto"]
    fila = estructura["bloques"][2]
    por_ref = {v: k for k, v in refs.items()}
    assert [p["origen_id"] for p in fila["productos"]] == [
        {"Orion 20": "orion-20", "Lyra 35": "lyra-35", "Astra 35 Pro": "astra-35"}[por_ref[ref]]
        for ref in ("P1", "P2", "P3")]
    assert estructura["bloques"][3]["url"] == VIDEO
    assert estructura["bloques"][4]["botones"] == [
        {"texto": "Ver toda la gama", "url": "https://mboprinters.com/es/"}]
    assert estructura["bloques"][0]["html"].startswith("<p>Hola Marny,</p><p>para el taller")
    # El correo lo genera BoHub: los precios del catálogo, el «a medida», el texto plano.
    assert "4.990 €" in b["html"] and "7.900 €" in b["html"] and "Precio a medida" in b["html"]
    assert "<div" not in b["html"].lower() and "data-bh-" not in b["html"]
    assert "Hola Marny," in b["texto"] and "4.990 €" in b["texto"] and "<" not in b["texto"]
    with session_factory() as s:
        fila_bd = s.get(BorradorAsistente, b["id"])
        assert fila_bd.modo == "compuesto" and json.loads(fila_bd.estructura_json) == estructura
        assert json.loads(fila_bd.contexto_json)["refs"]["videos"] == {"V1": VIDEO}
    # «Dile que el pedido sale el martes»: un correo normal, como siempre.
    falso_normal = _ClaudeFalso({"asunto": "Re: Pedido", "cuerpo": "<p>Hola Marny,</p><p>Tu "
                                 "pedido sale el martes.</p>", "idioma": "es", "aviso": "",
                                 "pendiente": [], "enlaces_usados": []})
    with session_factory() as s:
        otro = _hilo(s, contacto=_contacto(s, "otra@talentosdiversos.org"),
                     mensajes=[{"de": "otra@talentosdiversos.org",
                                "texto": "¿Cuándo sale mi pedido?"}], asunto="Pedido")
        s.commit()
        otro_id = otro.id
    with patch.object(llm, "_invoke_claude", falso_normal):
        r2 = _pedir(http, otro_id, instruccion="Dile que el pedido sale el martes")
    b2 = r2.json()["borrador"]
    assert b2["modo"] == "normal" and b2["estructura"] is None
    assert b2["html"] == "<p>Hola Marny,</p><p>Tu pedido sale el martes.</p>"
    assert "FORMATO DE ESTE TURNO" not in falso_normal.ultimo["bloques"][1]["text"]


def test_quita_el_video_y_lo_demas_queda_igual_tambien_lo_retocado_en_la_vista_previa(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    falso = _ClaudeFalso(COMPUESTO_DTF)
    with patch.object(llm, "_invoke_claude", falso):
        primero = _pedir(http, thread_id, instruccion="mándale la gama DTF").json()["borrador"]
    estructura = primero["estructura"]
    video = next(b for b in estructura["bloques"] if b["tipo"] == "video")
    # La persona retoca la entrada en la vista previa antes de pedir la corrección.
    retocada = json.loads(json.dumps(estructura))
    retocada["bloques"][0]["html"] = "<p>Hola Marny,</p><p>te mando lo que hablamos.</p>"
    falso.respuesta = {"modo": "compuesto", "asunto": "Re: Equipos DTF", "aviso": "",
                       "pendiente": [], "operaciones": [{"op": "quitar", "id": video["id"]}]}
    with patch.object(llm, "_invoke_claude", falso):
        r = _turno(http, primero["id"], "quita el vídeo", estructura_actual=retocada)
    assert r.status_code == 201, r.text
    nuevo = r.json()["borrador"]
    peticion = falso.ultimo["bloques"][1]["text"]
    assert "ESTRUCTURA ACTUAL" in peticion and video["id"] in peticion
    assert "te mando lo que hablamos" in peticion                   # lo retocado
    esperado = [b for b in retocada["bloques"] if b["id"] != video["id"]]
    assert nuevo["estructura"]["bloques"] == esperado                # lo demás, igual
    assert nuevo["modo"] == "compuesto" and nuevo["turno"] == 1
    assert "te mando lo que hablamos" in nuevo["texto"] and VIDEO not in nuevo["texto"]
    # «Deja solo dos máquinas» y «añade un botón a la tienda»: más operaciones.
    fila = next(b for b in esperado if b["tipo"] == "productos_fila")
    falso.respuesta = {"modo": "compuesto", "asunto": "Re: Equipos DTF", "aviso": "",
                       "pendiente": [], "operaciones": [
                           {"op": "editar", "id": fila["id"], "campo": "productos",
                            "valor": ["P1", "P2"]},
                           {"op": "añadir", "despues_de": fila["id"], "bloque": {
                               "tipo": "cta", "botones": [{"texto": "Ir a la tienda",
                                                           "enlace": "M:mbo_dtf"}]}}]}
    with patch.object(llm, "_invoke_claude", falso):
        tercero = _turno(http, nuevo["id"], "deja solo dos máquinas y añade un botón a la tienda",
                         estructura_actual=nuevo["estructura"]).json()["borrador"]
    bloques = tercero["estructura"]["bloques"]
    i = next(k for k, b in enumerate(bloques) if b["id"] == fila["id"])
    assert len(bloques[i]["productos"]) == 2
    assert bloques[i + 1]["tipo"] == "cta" and bloques[i + 1]["botones"][0]["texto"] == (
        "Ir a la tienda")
    assert [b for b in bloques if b["id"] not in (fila["id"], bloques[i + 1]["id"])] == [
        b for b in nuevo["estructura"]["bloques"] if b["id"] != fila["id"]]


def test_la_ia_nunca_devuelve_html_si_lo_intenta_se_descarta_y_se_registra(
    session_factory: sessionmaker, http: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    # Con el interruptor en «Con fichas», un «cuerpo» en HTML no vale: se descarta.
    falso = _ClaudeFalso({"asunto": "Re: Equipos DTF", "idioma": "es", "aviso": "",
                          "cuerpo": "<table><tr><td><div>Orion 20</div></td></tr></table>",
                          "pendiente": [], "enlaces_usados": []})
    with caplog.at_level(logging.WARNING), patch.object(llm, "_invoke_claude", falso):
        b = _pedir(http, thread_id, instruccion="las máquinas de DTF",
                   formato="compuesto").json()["borrador"]
    assert b["modo"] == "compuesto" and b["estructura"]["bloques"] == [] and b["html"] == ""
    assert "se ha descartado" in b["aviso"]
    assert any("descartado" in r.getMessage() for r in caplog.records)
    with session_factory() as s:
        assert json.loads(s.get(BorradorAsistente, b["id"]).contexto_json)["html_descartado"] == 1
    # HTML dentro de un texto de bloque: se quita (el texto se queda) y se registra.
    caplog.clear()
    falso.respuesta = {**COMPUESTO_DTF, "bloques": [
        {"tipo": "texto", "texto": "<p>Hola <b>Marny</b>,</p><table><tr><td>gracias</td></tr>"
                                   "</table>"},
        {"tipo": "productos_fila", "productos": ["P1", "P2"]}]}
    with caplog.at_level(logging.WARNING), patch.object(llm, "_invoke_claude", falso):
        b2 = _pedir(http, thread_id, instruccion="mándale la gama DTF").json()["borrador"]
    texto = b2["estructura"]["bloques"][0]["html"]
    assert "<table" not in texto and "<b>" not in texto and "Marny" in texto and "gracias" in texto
    assert "metió HTML" in b2["aviso"]
    assert any("devolvió HTML" in r.getMessage() for r in caplog.records)


def test_ni_productos_ni_enlaces_ni_precios_inventados(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    falso = _ClaudeFalso({**COMPUESTO_DTF, "bloques": [
        {"tipo": "texto", "texto": "Hola Marny, la Orion 20 por solo 3.990 €, y la Lyra 35 a "
                                   "7.900 €. Mira [esta oferta](https://inventado.example/)."},
        {"tipo": "producto", "producto": "P99"},
        {"tipo": "productos_fila", "productos": ["P1", "P2", "P3"]},
        {"tipo": "video", "video": "https://www.youtube.com/watch?v=abcdefghijk"},
        {"tipo": "cta", "botones": [{"texto": "Comprar", "enlace": "https://inventado.example/"},
                                    {"texto": "Ver la primera", "enlace": "P1"}]},
        {"tipo": "marca", "marca": "inventada"},
    ]})
    with patch.object(llm, "_invoke_claude", falso):
        b = _pedir(http, thread_id, instruccion="mándale la gama DTF").json()["borrador"]
    bloques = b["estructura"]["bloques"]
    assert [x["tipo"] for x in bloques] == ["texto", "productos_fila", "cta"]
    assert "inventado.example" not in json.dumps(bloques)
    assert bloques[0]["html"].count("<a ") == 0 and "esta oferta" in bloques[0]["html"]
    # El botón a «P1» lleva el enlace de ESE producto del catálogo, no uno inventado.
    por_ref = {v: k for k, v in _refs_de_peticion(falso.ultimo["bloques"][0]["text"]).items()}
    slug = {"Orion 20": "orion-20", "Lyra 35": "lyra-35", "Astra 35 Pro": "astra-35"}[
        por_ref["P1"]]
    assert bloques[2]["botones"] == [{"texto": "Ver la primera",
                                      "url": f"https://mboprinters.com/es/dtf/{slug}/"}]
    aviso = b["aviso"]
    assert "P99" in aviso and "vídeo" in aviso and "«inventada»" in aviso
    assert "«3.990 €»" in aviso and "7.900 €»" not in aviso              # solo el que no es
