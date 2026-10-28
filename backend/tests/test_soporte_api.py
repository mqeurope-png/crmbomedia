"""La API del soporte de terceros (`/api/erp/soporte`): solo la lista blanca,
que es de administración (`erp.config`). Buscar y leer ya no tienen ruta: los
hace el asistente al redactar (`asistente.consulta_fuentes`, sus tests en
`test_asistente_fuentes.py`). La forma de cada fuente es la de
`soporteApi.ts`."""
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

#: Las claves de `SoporteFuente` en `frontend/src/app/lib/soporteApi.ts`.
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
    """FLUX simulado (y un DNS que da una IP pública): la búsqueda de prueba
    de «Comprobar». Cualquier otra petición, 404."""
    hechas: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hechas.append(request.url)
        if request.url.path == "/api/v2/help_center/articles/search.json":
            return httpx.Response(200, json={"results": [{
                "id": 111, "title": "Calibrar la cámara", "locale": "es-419", "body": CUERPO,
                "snippet": "Limpia la <em>cámara</em>", "result_type": "article",
                "html_url": f"https://{FLUX}/hc/es-419/articles/111-calibrar"}],
                "count": 1})
        return httpx.Response(404)

    soporte.fijar_red_para_tests(
        cliente=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    yield hechas
    soporte.fijar_red_para_tests()


# ---------------------------------------------------------------------------
# Buscar y leer ya no son rutas: el asistente consulta él solo al redactar
# ---------------------------------------------------------------------------


def test_buscar_y_leer_ya_no_tienen_ruta(http, peticiones):
    """Quitados los botones «Buscar en soporte» de la franja, nadie llama a
    `/buscar` ni a `/articulo`: ni el admin las encuentra, y no se sale a la
    red."""
    h = auth_headers(http, "admin")
    r = http.get("/api/erp/soporte/buscar", params={"q": "cámara"}, headers=h)
    assert r.status_code in (404, 405), r.text
    r = http.get("/api/erp/soporte/articulo", params={"fuente": "flux", "id": "111"},
                 headers=h)
    assert r.status_code in (404, 405), r.text
    assert peticiones == []


# ---------------------------------------------------------------------------
# El taller: ni enviar nada ni tocar la lista blanca
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
