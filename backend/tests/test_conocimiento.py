"""Base de conocimiento para la respuesta a leads (Fase 1, 11/10/2026): rutas
de soporte por web e idioma, páginas de producto por interés, el índice de la
KB de FLUX (título y enlace, nunca el cuerpo) y su sincronización, la pantalla
(API) y la configuración. FLUX está simulado: nada sale a la red."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

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
from app.models.conocimiento import (
    SYNC_ERROR,
    SYNC_OK,
    SYNC_PARCIAL,
    FluxArticulo,
    FluxSincronizacion,
)
from app.services.conocimiento import comprobar, enlaces, flux, flux_job, rutas, zendesk
from app.services.conocimiento.config import defectos as config_defectos
from app.services.conocimiento.rutas import (
    ORIGEN_IDIOMA_DEFECTO,
    ORIGEN_REGLA,
    ORIGEN_RUTA,
    RUTAS_DE_PARTIDA,
    SOPORTE_MQ,
    SOPORTE_MQ_DE,
    SOPORTE_MQ_EN,
    SOPORTE_STREAMTEC,
    Rutas,
    url_en_idioma,
)
from app.services.web_forms.sitios import (
    SITIOS_DE_PARTIDA,
    Registro,
    Sitio,
    dominios_del_grupo,
    fijar_para_tests,
    registro,
)
from tests._test_helpers import auth_headers, seed_test_users

BEAMBOX_II = "10647778378639"
ADOR = "7104732498447"


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
def _registro_de_partida() -> Generator[None, None, None]:
    """Los tests ven la lista de sitios de partida (sin base detrás)."""
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    yield
    fijar_para_tests(None)


# --- FLUX simulado -----------------------------------------------------------------


def _articulo(zid: int, titulo: str, *, locale: str, section_id: int = 11,
              updated: str = "2026-09-01T10:00:00Z", **extra: Any) -> dict[str, Any]:
    return {
        "id": zid, "title": titulo, "section_id": section_id,
        "html_url": f"https://support.flux3dp.com/hc/{locale}/articles/{zid}",
        "label_names": ["camera"], "updated_at": updated, "outdated": False,
        "outdated_locales": [], "source_locale": "en-us", "draft": False, **extra,
    }


SECCIONES = {11: int(BEAMBOX_II), 12: int(ADOR)}


def _zendesk_falso(
    por_locale: dict[str, list[dict[str, Any]]], *, caidos: frozenset[str] = frozenset(),
    por_pagina: int = 2, peticiones: list[str] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(str(request.url))
        partes = request.url.path.split("/")      # /api/v2/help_center/{locale}/{recurso}.json
        locale, recurso = partes[4], partes[5].removesuffix(".json")
        if locale in caidos:
            raise httpx.ConnectError("FLUX no responde", request=request)
        if recurso == "sections":
            return httpx.Response(200, json={
                "sections": [{"id": sid, "category_id": cid} for sid, cid in SECCIONES.items()],
                "next_page": None,
            })
        articulos = por_locale.get(locale, [])
        pagina = int(request.url.params.get("page", "1"))
        trozo = articulos[(pagina - 1) * por_pagina: pagina * por_pagina]
        siguiente = (
            f"{flux.API_HC}/{locale}/articles.json?per_page=100&page={pagina + 1}"
            if pagina * por_pagina < len(articulos) else None
        )
        return httpx.Response(200, json={"articles": trozo, "next_page": siguiente})
    return httpx.MockTransport(handler)


def _cliente(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


def _kb_es_en() -> dict[str, list[dict[str, Any]]]:
    return {
        "es-419": [
            _articulo(1001, "Beambox II: calibrar la cámara", locale="es-419"),
            _articulo(1002, "Beambox II: cambiar el tubo láser", locale="es-419"),
            _articulo(1003, "Ador: primer uso", locale="es-419", section_id=12),
            _articulo(1004, "Borrador que no se indexa", locale="es-419", draft=True),
        ],
        "en-us": [
            _articulo(1001, "Beambox II: camera calibration", locale="en-us"),
            _articulo(1003, "Ador: getting started", locale="en-us", section_id=12),
        ],
    }


# --- rutas de soporte ---------------------------------------------------------------


def test_ruta_de_soporte_por_web_e_idioma() -> None:
    tabla = Rutas(RUTAS_DE_PARTIDA)
    aleman = tabla.soporte("mboprinters", "de")
    assert aleman is not None and aleman.url == SOPORTE_MQ_DE and aleman.origen == ORIGEN_RUTA
    castellano = tabla.soporte("mboprinters", "es")
    assert castellano is not None and castellano.url == SOPORTE_STREAMTEC
    # mqeurope no tiene neerlandés: la fila manda al inglés.
    assert tabla.soporte("mqeurope", "nl").url == SOPORTE_MQ_EN
    # Las marcas con soporte propio, en todos sus idiomas.
    assert tabla.soporte("fluxlasers", "de").url == "https://fluxlasers.eu/de/support/"
    assert tabla.soporte("fluxlasers", "es").url == "https://fluxlasers.es/soporte/"
    assert tabla.soporte("pimpam", "pt").url == "https://pimpam-vending.com/pt-pt/contact/"
    assert tabla.soporte("mbolasers", "DE-at").url == "https://mbolasers.com/de/kontakt-mbo-laser/"
    assert len([r for r in RUTAS_DE_PARTIDA if r.tipo == "soporte"]) == 29


def test_idioma_sin_fila_cae_al_por_defecto_de_la_web() -> None:
    tabla = Rutas(RUTAS_DE_PARTIDA)
    italiano = tabla.soporte("mbolasers", "it")
    assert italiano is not None
    assert italiano.url == "https://mbolasers.com/contacto-mbo-lasers/"
    assert italiano.origen == ORIGEN_IDIOMA_DEFECTO and italiano.idioma == "es"
    # artisjet-printers.eu: el por defecto es el francés, sin prefijo.
    assert tabla.soporte("artisjet-eu", "nl").url == SOPORTE_MQ
    # mqeurope en alemán (no publicado): el inglés, su idioma por defecto.
    assert tabla.soporte("mqeurope", "de").url == SOPORTE_MQ_EN


def test_web_sin_filas_aplica_la_regla_de_reparto() -> None:
    nueva = Sitio("webnueva", "webnueva.io", "Nueva", "MQ Europe",
                  idioma_defecto="es", idiomas=("es", "de"))
    fijar_para_tests(Registro(sitios=(*SITIOS_DE_PARTIDA, nueva)))
    tabla = Rutas(RUTAS_DE_PARTIDA)
    es = tabla.soporte("webnueva", "es")
    assert es is not None and es.url == SOPORTE_STREAMTEC and es.origen == ORIGEN_REGLA
    de = tabla.soporte("webnueva", "de")
    assert de is not None and de.url == SOPORTE_MQ_DE and de.origen == ORIGEN_REGLA
    # Un idioma que MQ Europe no tiene: su por defecto (francés).
    assert tabla.soporte("webnueva", "it").url == SOPORTE_MQ
    # Sin web (remitente desconocido) pero con idioma: la regla igual.
    assert tabla.soporte(None, "es").url == SOPORTE_STREAMTEC
    assert tabla.soporte(None, "pt").url == "https://artisjet-printers.eu/pt/technical-support/"
    # Sin web ni idioma no se inventa nada.
    assert tabla.soporte(None, None) is None


def test_pagina_de_producto_en_el_idioma_de_la_web() -> None:
    tabla = Rutas(RUTAS_DE_PARTIDA)
    fr = tabla.producto("dtf", "fr")
    assert fr is not None and fr.url == "https://www.mqeurope.com/fr/dtf-textile-printing/"
    assert fr.idioma == "fr" and fr.etiqueta == "dtf"
    assert tabla.producto("dtf", "en").url == "https://www.mqeurope.com/dtf-textile-printing/"
    # El alemán no está publicado en mqeurope.com: la página en inglés.
    de = tabla.producto("corte_laser", "de")
    assert de.url == "https://www.mqeurope.com/co2-laser-cutting/" and de.idioma == "en"
    assert tabla.producto("vending", "es") is None
    assert len([r for r in RUTAS_DE_PARTIDA if r.tipo == "producto"]) == 7
    # WPML con slugs traducidos (mbolasers): no se adivina la URL en otro idioma.
    mbo = registro().sitio("mbolasers")
    assert url_en_idioma("https://mbolasers.com/contacto-mbo-lasers/", mbo, "de") == (
        "https://mbolasers.com/contacto-mbo-lasers/")
    # qTranslate: el prefijo basta, y una URL que ya lleva prefijo se cambia.
    aj = registro().sitio("artisjet-eu")
    assert url_en_idioma(SOPORTE_MQ_DE, aj, "en") == SOPORTE_MQ_EN
    assert url_en_idioma(SOPORTE_MQ_DE, aj, "fr") == SOPORTE_MQ


# --- FLUX ---------------------------------------------------------------------------


def test_flux_categoria_por_modelo_y_enlace_por_idioma() -> None:
    cats = list(flux.CATEGORIAS_DE_PARTIDA)
    assert len(cats) == 13
    assert flux.modelo_en_texto("Ich habe eine Beambox II und möchte…", cats).modelo == "Beambox II"
    assert flux.modelo_en_texto("Mi Beambox 2 no corta", cats).modelo == "Beambox II"
    assert flux.modelo_en_texto("tengo una beambox", cats).modelo == "Beambox"
    assert flux.modelo_en_texto("HEXA RF 60W", cats).modelo == "HEXA RF"
    assert flux.modelo_en_texto("Escribo desde Ecuador", cats) is None     # no es «Ador»
    assert flux.modelo_en_texto("Beam Air Pro filtro", cats).modelo == "Beam Air Pro"
    assert flux.modelo_en_texto("", cats) is None
    assert flux.enlace_categoria(BEAMBOX_II, "de") == (
        f"https://support.flux3dp.com/hc/de-de/categories/{BEAMBOX_II}")
    assert flux.locale_de("es") == "es-419" and flux.locale_de("pt-PT") == "pt-br"
    assert flux.locale_de("nl") == "nl" and flux.locale_de("it") == "en-us"
    portada = flux.enlace_kb(None, "fr")
    assert portada.url == "https://support.flux3dp.com/hc/fr-fr" and portada.idioma == "fr"


def test_flux_intencion_separa_la_kb_de_nuestro_soporte() -> None:
    cfg = config_defectos()
    assert flux.intencion("¿Cómo calibro la cámara de mi Beambox?", cfg) == flux.INTENCION_KB
    assert flux.intencion("Wie stelle ich die Leistung ein?", cfg) == flux.INTENCION_KB
    assert flux.intencion("La máquina no enciende, necesito un repuesto", cfg) == (
        flux.INTENCION_SOPORTE)
    assert flux.intencion("Meine Beambox ist defekt, Garantie?", cfg) == flux.INTENCION_SOPORTE
    # Señales de las dos: gana el soporte (una máquina parada no espera).
    assert flux.intencion("Cómo configuro el wifi, la máquina está rota", cfg) == (
        flux.INTENCION_SOPORTE)
    assert flux.intencion("Hola, quería información", cfg) is None
    assert flux.intencion(None, cfg) is None


def test_enlaces_para_un_cliente_de_flux(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        kb = enlaces.enlaces_para(
            s, sitio="fluxlasers", idioma="de",
            texto="Wie kalibriere ich die Kamera meiner Beambox II?")
        assert kb["soporte"]["url"] == "https://fluxlasers.eu/de/support/"
        assert kb["flux"]["modelo"] == "Beambox II" and kb["flux"]["intencion"] == "kb"
        assert kb["flux"]["enlace"]["url"] == (
            f"https://support.flux3dp.com/hc/de-de/categories/{BEAMBOX_II}")
        assert kb["flux"]["enlace"]["origen"] == flux.ORIGEN_FLUX_KB
        averia = enlaces.enlaces_para(
            s, sitio="fluxlasers", idioma="de", texto="Meine Beambox II ist defekt.")
        assert averia["flux"]["intencion"] == "soporte"
        assert averia["flux"]["enlace"]["url"] == "https://fluxlasers.eu/de/support/"
        assert averia["flux"]["enlace"]["origen"] == flux.ORIGEN_FLUX_SOPORTE
        # No está claro → el nuestro, y la KB queda a mano por si acaso.
        dudoso = enlaces.enlaces_para(s, sitio="fluxlasers", idioma="es", texto="Hola")
        assert dudoso["flux"]["intencion"] is None
        assert dudoso["flux"]["enlace"]["url"] == "https://fluxlasers.es/soporte/"
        assert dudoso["flux"]["kb"]["url"] == "https://support.flux3dp.com/hc/es-419"
        # Un lead de otra web sin máquina de FLUX: sin bloque FLUX.
        otro = enlaces.enlaces_para(s, sitio="mboprinters", idioma="de", interes="dtf")
        assert otro["flux"] is None and otro["soporte"]["url"] == SOPORTE_MQ_DE
        assert otro["producto"]["url"] == "https://www.mqeurope.com/dtf-textile-printing/"
        # El modelo de sus pedidos manda aunque el texto no lo diga.
        con_ador = enlaces.enlaces_para(s, sitio="mboprinters", idioma="es", modelo="FLUX Ador",
                                        texto="¿Cómo enfoco?")
        assert con_ador["flux"]["modelo"] == "Ador"
        assert con_ador["flux"]["enlace"]["url"] == (
            f"https://support.flux3dp.com/hc/es-419/categories/{ADOR}")


def test_sincronizacion_idempotente_marca_los_idos_y_no_borra(
    session_factory: sessionmaker,
) -> None:
    kb = _kb_es_en()
    peticiones: list[str] = []
    with session_factory() as s:
        primera = zendesk.sincronizar(
            s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb, peticiones=peticiones)))
        s.commit()
        assert primera.estado == SYNC_OK
        assert (primera.articulos, primera.nuevos, primera.idos) == (5, 5, 0)   # sin el borrador
        # Paginó: dos páginas de artículos en es-419 (3 artículos + borrador, de 2 en 2).
        assert sum("es-419/articles" in p for p in peticiones) == 2
        segunda = zendesk.sincronizar(s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb)))
        s.commit()
        assert (segunda.articulos, segunda.nuevos, segunda.actualizados, segunda.idos) == (
            5, 0, 0, 0)
        assert s.scalar(select(FluxArticulo).where(FluxArticulo.id == "1001:es-419")) is not None
        fila = s.get(FluxArticulo, "1002:es-419")
        assert fila.category_id == int(BEAMBOX_II) and fila.titulo.startswith("Beambox II")
        assert json.loads(fila.etiquetas_json) == ["camera"] and fila.source_locale == "en-us"
        # Un artículo desaparece de FLUX: se marca ido, no se borra.
        kb["es-419"] = [a for a in kb["es-419"] if a["id"] != 1002]
        tercera = zendesk.sincronizar(s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb)))
        s.commit()
        assert tercera.idos == 1
        ido = s.get(FluxArticulo, "1002:es-419")
        assert ido is not None and ido.ido_en is not None
        assert [a.titulo for a in flux.articulos_de(s, categoria_id=BEAMBOX_II, idioma="es")] == [
            "Beambox II: calibrar la cámara"]
        # Vuelve (con título nuevo): deja de estar ido.
        kb["es-419"].append(_articulo(1002, "Beambox II: cambiar el tubo", locale="es-419"))
        cuarta = zendesk.sincronizar(s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb)))
        s.commit()
        assert cuarta.actualizados == 1 and s.get(FluxArticulo, "1002:es-419").ido_en is None
        estado = flux.estado_indice(s)
        assert estado["articulos_por_idioma"] == {"es": 3, "en": 2} and estado["idos"] == 0
        assert estado["ultima_sincronizacion"]["estado"] == SYNC_OK
        assert s.scalar(select(FluxSincronizacion.id).where(
            FluxSincronizacion.id == cuarta.id)) is not None


def test_flux_caido_deja_el_indice_anterior_intacto(session_factory: sessionmaker) -> None:
    kb = _kb_es_en()
    with session_factory() as s:
        zendesk.sincronizar(s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb)))
        s.commit()
        antes = {a.id: (a.titulo, a.ido_en) for a in s.scalars(select(FluxArticulo))}
        caida = zendesk.sincronizar(
            s, locales=["es", "en"],
            http=_cliente(_zendesk_falso(kb, caidos=frozenset({"es-419", "en-us"}))))
        s.commit()
        assert caida.estado == SYNC_ERROR and "FLUX no responde" in (caida.detalle or "")
        assert caida.articulos == 0 and caida.idos == 0
        despues = {a.id: (a.titulo, a.ido_en) for a in s.scalars(select(FluxArticulo))}
        assert despues == antes                      # nada marcado ido, nada borrado
        parcial = zendesk.sincronizar(
            s, locales=["es", "en"], http=_cliente(_zendesk_falso(kb, caidos=frozenset({"en-us"}))))
        s.commit()
        assert parcial.estado == SYNC_PARCIAL
        assert json.loads(parcial.locales_json)["en-us"]["error"]
        assert json.loads(parcial.locales_json)["es-419"]["articulos"] == 3


def test_el_job_diario_respeta_el_interruptor_y_no_repite(session_factory: sessionmaker) -> None:
    llamadas: list[datetime] = []

    def _falsa(session: Session, *, origen: str, ahora: datetime, **_: Any) -> FluxSincronizacion:
        llamadas.append(ahora)
        fila = FluxSincronizacion(origen=origen, inicio=ahora, fin=ahora, estado=SYNC_OK)
        session.add(fila)
        session.flush()
        return fila

    ahora = datetime(2026, 10, 12, 4, 30, tzinfo=UTC)
    with session_factory() as s, patch.object(zendesk, "sincronizar", _falsa):
        assert flux_job.run_daily(s, ahora=ahora) is True
        assert flux_job.run_daily(s, ahora=ahora + timedelta(hours=1)) is False   # ya corrió
        assert flux_job.run_daily(s, ahora=ahora + timedelta(hours=13)) is True
        with patch.object(flux_job, "configuracion",
                          return_value={**config_defectos(), "flux_sync_activa": False}):
            assert flux_job.run_daily(s, ahora=ahora + timedelta(days=2)) is False
            assert flux_job.run_daily(s, ahora=ahora + timedelta(days=2), force=True) is True
    assert len(llamadas) == 3


# --- la pantalla (API) ----------------------------------------------------------------


def test_api_una_ruta_nueva_se_aplica_sin_desplegar(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    todo = http.get("/api/erp/conocimiento", headers=headers)
    assert todo.status_code == 200
    datos = todo.json()
    assert len(datos["rutas"]) == 36 and len(datos["sitios"]) == 10
    assert len(datos["flux"]["categorias"]) == 13 and datos["flux"]["locales"]["es"] == "es-419"
    assert datos["flux"]["ultima_sincronizacion"] is None
    # Antes: mqeurope en alemán cae al inglés (idioma por defecto de la web).
    antes = http.post("/api/erp/conocimiento/resolver", headers=headers,
                      json={"sitio": "mqeurope", "idioma": "de"}).json()
    assert antes["soporte"]["url"] == SOPORTE_MQ_EN
    nueva = "https://www.mqeurope.com/de/technischer-support/"
    alta = http.post("/api/erp/conocimiento/rutas", headers=headers, json={
        "tipo": "soporte", "sitio": "mqeurope", "idioma": "de", "url": nueva,
        "nota": "página alemana nueva"})
    assert alta.status_code == 201, alta.text
    fila = next(r for r in alta.json()["rutas"] if r["sitio"] == "mqeurope" and r["idioma"] == "de")
    assert fila["url"] == nueva and fila["activo"] is True
    despues = http.post("/api/erp/conocimiento/resolver", headers=headers,
                        json={"sitio": "mqeurope", "idioma": "de"}).json()
    assert despues["soporte"]["url"] == nueva and despues["soporte"]["origen"] == ORIGEN_RUTA
    # Y la misma lectura que hará el asistente, desde el servicio.
    with session_factory() as s:
        assert rutas.cargar(s).soporte("mqeurope", "de").url == nueva
    # Repetida → 400; desactivada → vuelve al inglés.
    assert http.post("/api/erp/conocimiento/rutas", headers=headers, json={
        "tipo": "soporte", "sitio": "mqeurope", "idioma": "de", "url": nueva}).status_code == 400
    assert http.post("/api/erp/conocimiento/rutas", headers=headers, json={
        "tipo": "soporte", "sitio": "nadie", "idioma": "de", "url": nueva}).status_code == 400
    assert http.post("/api/erp/conocimiento/rutas", headers=headers, json={
        "tipo": "soporte", "sitio": "mqeurope", "idioma": "it", "url": "ftp://x.y"},
    ).status_code == 400
    baja = http.patch(f"/api/erp/conocimiento/rutas/{fila['id']}", headers=headers,
                      json={"activo": False})
    assert baja.status_code == 200
    assert http.post("/api/erp/conocimiento/resolver", headers=headers,
                     json={"sitio": "mqeurope", "idioma": "de"}).json()["soporte"]["url"] == (
        SOPORTE_MQ_EN)
    # Solo quien configura el ERP.
    assert http.get("/api/erp/conocimiento", headers=auth_headers(http, "user")).status_code == 403


def test_api_comprobar_urls_guarda_el_codigo(http: TestClient) -> None:
    headers = auth_headers(http, "admin")
    pedidas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidas.append(f"{request.method} {request.url}")
        if "pt-pt" in str(request.url):
            return httpx.Response(404)
        if request.method == "HEAD" and "mbolasers" in str(request.url):
            return httpx.Response(405)                 # esa web no admite HEAD: se pide GET
        return httpx.Response(200)

    with patch.object(comprobar, "_cliente",
                      lambda: httpx.Client(transport=httpx.MockTransport(handler))):
        r = http.post("/api/erp/conocimiento/rutas/comprobar", headers=headers)
    assert r.status_code == 200
    resultados = r.json()["resultados"]
    assert len(resultados) == 36 and sum(1 for x in resultados if not x["ok"]) == 1
    mal = next(x for x in resultados if not x["ok"])
    assert mal["http"] == 404 and "pt-pt" in mal["url"]
    assert any(p.startswith("GET https://mbolasers.com/") for p in pedidas)
    rutas_api = r.json()["rutas"]
    pt = next(x for x in rutas_api if x["sitio"] == "pimpam" and x["idioma"] == "pt")
    assert pt["comprobado_http"] == 404 and pt["comprobado_en"]
    assert http.get("/api/erp/conocimiento", headers=headers).json()["rutas"][0][
        "comprobado_http"] == 200


def test_api_un_sitio_nuevo_entra_en_la_captura(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    alta = http.post("/api/erp/conocimiento/sitios", headers=headers, json={
        "clave": "webnueva", "dominio": "WebNueva.io", "marca": "Web Nueva", "empresa": "MQ Europe",
        "idioma_defecto": "en", "idiomas": ["de", "fr"], "plugin_traduccion": "qtranslate",
        "remitente_defecto": "Info@WebNueva.io",
    })
    assert alta.status_code == 201, alta.text
    sitio = next(s for s in alta.json()["sitios"] if s["clave"] == "webnueva")
    assert sitio["dominio"] == "webnueva.io" and sitio["idiomas"] == ["en", "de", "fr"]
    assert sitio["remitente_defecto"] == "info@webnueva.io" and alta.json()["sitios_de_la_base"]
    with session_factory() as s:
        assert "webnueva.io" in dominios_del_grupo(s)
        assert "fluxlasers.eu" in dominios_del_grupo(s)          # los de partida siguen
    # Repetida, dominio repetido, tipo raro → 400.
    assert http.post("/api/erp/conocimiento/sitios", headers=headers, json={
        "clave": "webnueva", "dominio": "otra.io", "marca": "x", "empresa": "y"}).status_code == 400
    assert http.post("/api/erp/conocimiento/sitios", headers=headers, json={
        "clave": "otra", "dominio": "webnueva.io", "marca": "x", "empresa": "y"}).status_code == 400
    assert http.post("/api/erp/conocimiento/sitios", headers=headers, json={
        "clave": "otra", "dominio": "otra.io", "marca": "x", "empresa": "y",
        "tipo": "cosa"}).status_code == 400
    baja = http.patch("/api/erp/conocimiento/sitios/webnueva", headers=headers,
                      json={"activo": False, "marca": "Web Nueva (parada)"})
    assert baja.status_code == 200
    with session_factory() as s:
        assert "webnueva.io" not in dominios_del_grupo(s)
    assert http.patch("/api/erp/conocimiento/sitios/nadie", headers=headers,
                      json={"activo": False}).status_code == 404


def test_api_categoria_de_flux_nueva_y_sincronizar_ahora(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    alta = http.post("/api/erp/conocimiento/flux/categorias", headers=headers, json={
        "categoria_id": "999000111", "modelo": "Beam Air Max", "palabras": ["Beam Air Max"]})
    assert alta.status_code == 201, alta.text
    with session_factory() as s:
        cats = flux.cargar_categorias(s)
        assert len(cats) == 14
        assert flux.modelo_en_texto("mi beam air max", cats).modelo == "Beam Air Max"
    cambio = http.patch("/api/erp/conocimiento/flux/categorias/999000111", headers=headers,
                        json={"activo": False})
    assert cambio.status_code == 200
    with session_factory() as s:
        # Desactivada, la palabra larga ya no cuenta: gana la más larga que queda.
        assert flux.modelo_en_texto("mi beam air max", flux.cargar_categorias(s)).modelo == (
            "Beam Air")
    assert http.post("/api/erp/conocimiento/flux/categorias", headers=headers, json={
        "categoria_id": "abc", "modelo": "x"}).status_code == 400
    with patch.object(zendesk, "_cliente", lambda: _cliente(_zendesk_falso(_kb_es_en()))):
        r = http.post("/api/erp/conocimiento/flux/sincronizar", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["sincronizacion"]["estado"] == SYNC_OK
    assert r.json()["sincronizacion"]["origen"] == "manual"
    assert r.json()["articulos_por_idioma"] == {"es": 3, "en": 2}
    # Los demás locales no tenían nada en el simulador: ni error ni artículos.
    assert r.json()["sincronizacion"]["locales"]["de-de"]["articulos"] == 0
    probar = http.post("/api/erp/conocimiento/resolver", headers=headers, json={
        "sitio": "fluxlasers", "idioma": "es",
        "texto": "¿Cómo calibro la cámara de la Beambox II?"})
    assert [a["titulo"] for a in probar.json()["flux"]["articulos"]] == [
        "Beambox II: calibrar la cámara", "Beambox II: cambiar el tubo láser"]


def test_configuracion_en_ajustes_del_erp(session_factory: sessionmaker, http: TestClient) -> None:
    headers = auth_headers(http, "admin")
    antes = http.get("/api/erp/settings", headers=headers).json()["conocimiento"]
    assert antes["flux_sync_activa"] is True and antes["flux_sync_hora"] == "04:30"
    assert "garantía" in antes["flux_palabras_soporte"]
    cambio = http.patch("/api/erp/settings", headers=headers, json={"conocimiento": {
        "flux_sync_activa": False, "flux_sync_hora": "5:15", "flux_palabras_kb": ["Cómo", "como "],
    }})
    assert cambio.status_code == 200, cambio.text
    guardado = cambio.json()["conocimiento"]
    assert guardado["flux_sync_activa"] is False and guardado["flux_sync_hora"] == "05:15"
    assert guardado["flux_palabras_kb"] == ["cómo", "como"]
    assert guardado["flux_palabras_soporte"] == antes["flux_palabras_soporte"]  # no se tocó
    assert http.patch("/api/erp/settings", headers=headers, json={"conocimiento": {
        "flux_sync_hora": "25:00"}}).status_code == 400
    with session_factory() as s:
        from app.services.conocimiento.config import configuracion  # noqa: PLC0415

        assert configuracion(s)["flux_sync_hora"] == "05:15"
        assert configuracion(s)["flux_sync_activa"] is False
