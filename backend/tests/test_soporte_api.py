"""La API del soporte de terceros (`/api/erp/soporte`): la consulta es del
taller también (rol `sat`, que sigue sin poder enviar correos) y la lista
blanca es de administración. Las formas son las de `soporteApi.ts`."""
from __future__ import annotations

import json
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.crm import AuditLog
from app.models.fuentes_externas import SoporteFuente
from app.services import soporte
from tests._test_helpers import auth_headers, seed_test_users

FLUX = "support.flux3dp.com"

#: Las claves de cada tipo de `frontend/src/app/lib/soporteApi.ts`.
CLAVES_BUSQUEDA = {"consulta", "resultados", "avisos", "fuentes", "fallidas"}
CLAVES_RESULTADO = {"fuente", "fuente_nombre", "articulo_id", "titulo", "extracto", "url",
                    "idioma"}
CLAVES_ARTICULO = {"fuente", "fuente_nombre", "articulo_id", "titulo", "url", "idioma", "texto",
                   "html", "actualizado"}
CLAVES_FUENTE = {"clave", "nombre", "dominio", "tipo", "base_url", "marca", "locales", "activo",
                 "posicion", "notas", "comprobado_en", "comprobado_estado",
                 "comprobado_detalle"}

CUERPO = ("<p>Antes de calibrar, apaga la máquina y limpia la lente con un bastoncillo. "
          "Comprueba que la cámara no tiene polvo ni restos de material quemado; si los "
          "tiene, límpiala con un paño seco y vuelve a encenderla.</p>"
          "<script>alert(1)</script>")


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with fabrica() as s:
        seed_test_users(s)
        s.add(SoporteFuente(
            clave="flux", nombre="FLUX · centro de ayuda", dominio=FLUX, tipo="zendesk",
            base_url=f"https://{FLUX}", marca="flux",
            locales_json=json.dumps({"es": "es-419", "en": "en-us"}), activo=True,
            posicion=0))
        s.commit()
    yield fabrica
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


@pytest.fixture()
def peticiones() -> Generator[list[httpx.URL], None, None]:
    """FLUX simulado (y un DNS que da una IP pública): búsqueda y artículo."""
    hechas: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hechas.append(request.url)
        if request.url.path == "/api/v2/help_center/articles/search.json":
            return httpx.Response(200, json={"results": [{
                "id": 111, "title": "Calibrar la cámara", "locale": "es-419", "body": CUERPO,
                "snippet": "Limpia la <em>cámara</em>", "result_type": "article",
                "html_url": f"https://{FLUX}/hc/es-419/articles/111-calibrar"}],
                "count": 1})
        if request.url.path == "/api/v2/help_center/es-419/articles/111.json":
            return httpx.Response(200, json={"article": {
                "id": 111, "title": "Calibrar la cámara", "locale": "es-419", "body": CUERPO,
                "html_url": f"https://{FLUX}/hc/es-419/articles/111-calibrar",
                "updated_at": "2026-09-01T10:00:00Z"}})
        return httpx.Response(404)

    soporte.fijar_red_para_tests(
        cliente=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield hechas
    soporte.fijar_red_para_tests()


# ---------------------------------------------------------------------------
# Consulta: el taller puede buscar y leer…
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["sat", "admin", "user", "comercial", "pedidos"])
def test_buscar_y_leer_con_ver_la_cola_sat(http, peticiones, rol):
    h = auth_headers(http, rol)

    r = http.get("/api/erp/soporte/buscar", params={"q": "cámara", "idioma": "es"}, headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert set(cuerpo) == CLAVES_BUSQUEDA
    assert cuerpo["fuentes"] == ["flux"]
    assert len(cuerpo["resultados"]) == 1
    resultado = cuerpo["resultados"][0]
    assert set(resultado) == CLAVES_RESULTADO
    assert resultado["articulo_id"] == "111"
    assert resultado["extracto"] == "Limpia la cámara"

    r = http.get("/api/erp/soporte/articulo",
                 params={"fuente": "flux", "id": "111", "idioma": "es"}, headers=h)
    assert r.status_code == 200, r.text
    art = r.json()
    assert set(art) == CLAVES_ARTICULO
    assert "paño seco" in art["texto"]
    assert "<script" not in art["html"] and "alert" not in art["texto"]


def test_la_cache_se_guarda_entre_peticiones(http, peticiones):
    h = auth_headers(http, "sat")
    for _ in range(2):
        assert http.get("/api/erp/soporte/buscar", params={"q": "cámara"},
                        headers=h).status_code == 200
        assert http.get("/api/erp/soporte/articulo", params={"fuente": "flux", "id": "111"},
                        headers=h).status_code == 200
    assert len(peticiones) == 2


def test_el_visor_no_consulta(http, peticiones):
    h = auth_headers(http, "viewer")
    assert http.get("/api/erp/soporte/buscar", params={"q": "cámara"},
                    headers=h).status_code == 403
    assert peticiones == []


def test_los_errores_salen_con_codigo_y_detalle(http, peticiones):
    h = auth_headers(http, "sat")

    r = http.get("/api/erp/soporte/articulo", params={"fuente": "otra", "id": "1"}, headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "fuente_desconocida"
    assert "otra" in r.json()["detail"]["detail"]

    r = http.get("/api/erp/soporte/buscar", params={"q": " "}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "consulta_vacia"

    r = http.get("/api/erp/soporte/articulo", params={"fuente": "flux", "id": "999"},
                 headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "no_encontrado"


# ---------------------------------------------------------------------------
# … pero no enviar nada ni tocar la lista blanca
# ---------------------------------------------------------------------------


def test_el_taller_sigue_sin_poder_enviar_correos(http, peticiones):
    h = auth_headers(http, "sat")
    assert http.post("/api/emails/send", json={"to": ["a@b.com"], "subject": "x",
                                                "body_text": "x"}, headers=h).status_code == 403
    assert http.post("/api/emails/asistente", json={"destinatario": "a@b.com",
                                                    "instruccion": "hola"},
                     headers=h).status_code == 403


def test_el_taller_no_toca_la_lista_blanca(http, peticiones):
    h = auth_headers(http, "sat")
    assert http.get("/api/erp/soporte/fuentes", headers=h).status_code == 403
    assert http.post("/api/erp/soporte/fuentes", json={"clave": "x1", "nombre": "X",
                                                       "dominio": "x.example.com"},
                     headers=h).status_code == 403
    assert http.patch("/api/erp/soporte/fuentes/flux", json={"activo": False},
                      headers=h).status_code == 403
    assert http.post("/api/erp/soporte/fuentes/flux/comprobar", headers=h).status_code == 403
    assert peticiones == []


# ---------------------------------------------------------------------------
# La lista blanca (administración)
# ---------------------------------------------------------------------------


def test_admin_lista_crea_edita_y_comprueba(http, peticiones, session_factory):
    h = auth_headers(http, "admin")

    r = http.get("/api/erp/soporte/fuentes", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    assert [f["clave"] for f in items] == ["flux"]
    assert set(items[0]) == CLAVES_FUENTE
    assert items[0]["locales"] == {"es": "es-419", "en": "en-us"}

    r = http.post("/api/erp/soporte/fuentes", headers=h, json={
        "clave": "artisjet", "nombre": "artisJet", "dominio": "help.artisjet.com",
        "tipo": "wordpress", "locales": {"es": "es"}, "activo": False})
    assert r.status_code == 201, r.text
    nueva = r.json()
    assert set(nueva) == CLAVES_FUENTE
    assert (nueva["base_url"], nueva["activo"], nueva["posicion"]) == (
        "https://help.artisjet.com", False, 1)

    r = http.post("/api/erp/soporte/fuentes", headers=h, json={
        "clave": "otra", "nombre": "Otra", "dominio": "127.0.0.1"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "datos_invalidos"

    r = http.patch("/api/erp/soporte/fuentes/artisjet", headers=h,
                   json={"activo": True, "notas": "Cuando tengan URL de soporte."})
    assert r.status_code == 200, r.text
    assert r.json()["activo"] is True
    assert r.json()["notas"] == "Cuando tengan URL de soporte."

    r = http.patch("/api/erp/soporte/fuentes/nada", headers=h, json={"activo": True})
    assert r.status_code == 404

    r = http.post("/api/erp/soporte/fuentes/flux/comprobar", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["comprobado_estado"] == "ok"
    assert r.json()["comprobado_en"]

    with session_factory() as s:
        auditados = s.scalars(select(AuditLog).where(
            AuditLog.target_type == "kb_soporte_fuente")).all()
        assert sorted((a.target_id, a.action, json.loads(a.metadata_json or "{}")["accion"])
                      for a in auditados) == [("artisjet", "kb.changed", "alta"),
                                              ("artisjet", "kb.changed", "cambio")]
