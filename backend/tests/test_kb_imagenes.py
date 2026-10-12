"""Base de conocimiento, Fase 3: el índice de imágenes de las webs.

Lo que se comprueba, con las formas de los datos reales del 12/10/2026 (la API
REST de WordPress de las ocho webs): los cinco filtros del índice, la
paginación por `X-WP-TotalPages` (WordPress devuelve páginas cortas a mitad de
la lista), los tamaños con las mismas proporciones, que una web caída deja su
índice como estaba, que lo que desaparece se marca y no se borra, que las
favoritas y las ocultas son a mano y no las pisa la sincronización, y lo que
se le ofrece a la IA. Nada sale a la red: las webs son un `MockTransport`.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.conocimiento import (
    FUENTE_IMAGENES,
    FUENTE_WOOCOMMERCE,
    TIPO_CONSUMIBLE,
    CatalogoSincronizacion,
    ImagenWeb,
    ProductoCatalogo,
)
from app.models.sitios import SitioWeb
from app.services.conocimiento import imagenes
from app.services.web_forms.sitios import Registro, Sitio, fijar_para_tests
from tests._test_helpers import auth_headers, seed_test_users

AHORA = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LUEGO = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
BOPRINT = Sitio(clave="boprint", dominio="boprint.net", marca="Boprint", empresa="Streamtec")
FLUX = Sitio(clave="fluxlasers", dominio="fluxlasers.es", marca="Fluxlasers",
             empresa="Streamtec", posicion=1)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        # Dos webs en la tabla de sitios: la pasada lee la tabla, no la lista de partida.
        for posicion, sitio in enumerate((BOPRINT, FLUX)):
            seed.add(SitioWeb(clave=sitio.clave, dominio=sitio.dominio, host=sitio.dominio,
                              marca=sitio.marca, empresa=sitio.empresa, tipo="web",
                              idioma_defecto="es", idiomas_json='["es"]', posicion=posicion,
                              activo=True))
        seed.commit()
    fijar_para_tests(Registro(sitios=(BOPRINT, FLUX)))
    yield factory
    fijar_para_tests(None)
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


def _medio(mid: int, titulo: str, *, ancho: int = 1920, alto: int = 1072, post: int | None = None,
           mime: str = "image/jpeg", host: str = "boprint.net") -> dict[str, Any]:
    """Un adjunto con la forma real de `/wp-json/wp/v2/media` (tamaños del tema
    incluidos: los recortes cuadrados no valen para un correo)."""
    base = f"https://{host}/wp-content/uploads/2026/05/{titulo.replace(' ', '-')}"
    ext = "webp" if mime == "image/webp" else "jpg"
    escala = 1536 / ancho
    return {
        "id": mid, "date": "2026-05-06T08:03:45", "title": {"rendered": titulo},
        "mime_type": mime, "media_type": "image", "post": post,
        "link": f"https://{host}/{titulo.replace(' ', '-')}/",
        "source_url": f"{base}.{ext}",
        "media_details": {"width": ancho, "height": alto, "sizes": {
            "thumbnail": {"width": 150, "height": 150, "source_url": f"{base}-150x150.{ext}"},
            "medium_large": {"width": 768, "height": round(768 * alto / ancho),
                             "source_url": f"{base}-768x{round(768 * alto / ancho)}.{ext}"},
            "1536x1536": {"width": 1536, "height": round(alto * escala),
                          "source_url": f"{base}-1536x{round(alto * escala)}.{ext}"},
            "featured": {"width": 642, "height": 300, "source_url": f"{base}-642x300.{ext}"},
            "full": {"width": ancho, "height": alto, "source_url": f"{base}.{ext}"},
        }},
    }


#: La biblioteca de boprint en tres páginas, la del medio CORTA (WordPress
#: quita después de paginar los adjuntos de entradas privadas): una página de
#: menos de 100 no es la última.
BOPRINT_P1 = [
    _medio(1, "custom coasters"),                                   # vale
    _medio(2, "smartphone accessories"),                            # vale
    _medio(3, "boprint logo", ancho=1920, alto=600),                # logo
    _medio(4, "banner estrecho", ancho=800, alto=400),              # estrecha
    _medio(5, "foto vertical", ancho=1200, alto=1600),              # vertical
    _medio(6, "bannerfespa", ancho=1200, alto=400),                 # 3:1, vale
]
BOPRINT_P2 = [
    _medio(7, "SPU-PSTY-007-1.jpg"),                                # código de artículo
    _medio(8, "promotional and corporate gifts", post=555),         # adjunta a un producto
]
BOPRINT_P3 = [
    _medio(9, "luggage tags", post=77),                             # adjunta a una página
    _medio(10, "cropped-favicon", ancho=1920, alto=1080),           # ruido
    _medio(11, "custom coasters webp", mime="image/webp"),          # WebP: Outlook no
    _medio(12, "B100617"),                                          # recambio de FLUX
]
FLUX_MEDIOS = [_medio(20, "Beam-Air", host="fluxlasers.es", ancho=1746, alto=1164),
               _medio(21, "MCO-21005_2.jpg-1.webp", host="fluxlasers.es",
                      mime="image/webp")]


def _webs(*, boprint: list[list[dict[str, Any]]] | None = None, flux_caida: bool = False,
          productos: dict[str, list[int]] | None = None,
          peticiones: list[httpx.Request] | None = None) -> httpx.Client:
    paginas = boprint if boprint is not None else [BOPRINT_P1, BOPRINT_P2, BOPRINT_P3]
    productos = productos if productos is not None else {"boprint.net": [555, 556]}

    def handler(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(request)
        host, ruta = request.url.host, request.url.path
        pagina = int(request.url.params.get("page") or 1)
        if host == "fluxlasers.es" and flux_caida:
            return httpx.Response(503, text="mantenimiento")
        if ruta.endswith("/wp/v2/media"):
            lista = paginas if host == "boprint.net" else [FLUX_MEDIOS]
            if pagina > len(lista):
                return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
            return httpx.Response(200, json=lista[pagina - 1],
                                  headers={"X-WP-TotalPages": str(len(lista))})
        if ruta.endswith("/wp/v2/product"):
            ids = productos.get(host)
            if ids is None:
                return httpx.Response(404, json={"code": "rest_no_route"})
            return httpx.Response(200, json=[{"id": i} for i in ids],
                                  headers={"X-WP-TotalPages": "1"})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _indexadas(s: Session) -> dict[tuple[str, int], ImagenWeb]:
    return {(f.sitio, f.media_id): f for f in s.scalars(select(ImagenWeb))}


def test_los_cinco_filtros_y_los_tamanos_con_sus_proporciones() -> None:
    productos = {555}
    motivos = {m["id"]: imagenes.motivo_descarte(m, productos)
               for m in BOPRINT_P1 + BOPRINT_P2 + BOPRINT_P3 + FLUX_MEDIOS}
    assert motivos == {
        1: None, 2: None, 3: imagenes.DESCARTE_TITULO, 4: imagenes.DESCARTE_ESTRECHA,
        5: imagenes.DESCARTE_PROPORCION, 6: None, 7: imagenes.DESCARTE_CODIGO,
        8: imagenes.DESCARTE_PRODUCTO, 9: None, 10: imagenes.DESCARTE_TITULO,
        11: imagenes.DESCARTE_FORMATO, 12: imagenes.DESCARTE_CODIGO, 20: None,
        21: imagenes.DESCARTE_CODIGO,
    }
    assert imagenes.motivo_descarte({"id": 1, "media_details": {}}, set()) == (
        imagenes.DESCARTE_SIN_MEDIDAS)
    # Títulos de verdad que SÍ valen: un número de modelo no es un código de artículo.
    for titulo in ("10. artis 3000U pro flatbed", "artisJet YOUNG front view", "mbo600",
                   "lab510sII-solIII-etikettiermaschine", "6090", "MBO 2530 A4 BLACK"):
        assert imagenes.motivo_descarte(_medio(1, titulo), set()) is None, titulo
    # Los tamaños: solo los de las mismas proporciones (fuera el cuadrado y el
    # «featured» recortado), de menor a mayor y con la original.
    tamanos = imagenes.tamanos_de(_medio(1, "custom coasters"))
    assert [t["ancho"] for t in tamanos] == [768, 1536, 1920]
    assert tamanos[-1]["url"].endswith("custom-coasters.jpg")
    # El correo pide el que cubre el doble de su ancho (pantallas retina).
    assert imagenes.url_para(tamanos, 1168)["ancho"] == 1536
    assert imagenes.url_para(tamanos, 4000)["ancho"] == 1920


def test_la_pasada_indexa_lo_que_vale_y_una_web_caida_deja_su_indice(
    session_factory: sessionmaker,
) -> None:
    peticiones: list[httpx.Request] = []
    with session_factory() as s:
        s.add(ProductoCatalogo(  # un producto de la tienda que la web ya no lista
            fuente=FUENTE_WOOCOMMERCE, tipo=TIPO_CONSUMIBLE, marca="Boprint",
            origen_id="boprint:77", nombre="Tinta", idioma="es", sitio="boprint",
            visto_en=AHORA))
        s.commit()
        pasada = imagenes.sincronizar(s, http=_webs(peticiones=peticiones), ahora=AHORA)
        s.commit()
        assert pasada.estado == "ok" and pasada.fuente == FUENTE_IMAGENES
        filas = _indexadas(s)
        # La 9 está adjunta al id 77: en el catálogo es un producto de la tienda.
        assert set(filas) == {("boprint", 1), ("boprint", 2), ("boprint", 6), ("fluxlasers", 20)}
        resumen = json.loads(pasada.resumen_json)
        assert resumen["boprint"]["medios"] == 12 and resumen["boprint"]["productos"] == 3
        assert resumen["boprint"]["descartes"] == {
            "titulo": 2, "estrecha": 1, "proporcion": 1, "codigo": 2, "producto": 2, "formato": 1}
        # Las tres páginas, aunque la segunda viniera corta.
        paginas = sorted(int(r.url.params["page"]) for r in peticiones
                         if r.url.host == "boprint.net" and r.url.path.endswith("/media"))
        assert paginas == [1, 2, 3]
        coasters = filas[("boprint", 1)]
        assert coasters.titulo == "custom coasters" and coasters.ancho == 1920
        assert [t["ancho"] for t in imagenes.tamanos(coasters)] == [768, 1536, 1920]
        s.query(ImagenWeb).filter(ImagenWeb.media_id == 20).update({"favorita": True})
        s.commit()
        # Al día siguiente fluxlasers no responde y boprint ha quitado una imagen.
        sin_la_2 = [[m for m in BOPRINT_P1 if m["id"] != 2], BOPRINT_P2, BOPRINT_P3]
        pasada2 = imagenes.sincronizar(s, http=_webs(boprint=sin_la_2, flux_caida=True),
                                       ahora=LUEGO)
        s.commit()
        assert pasada2.estado == "parcial" and "fluxlasers" in (pasada2.detalle or "")
        filas = _indexadas(s)
        assert filas[("boprint", 2)].ido_en is not None                # ida, no borrada
        assert filas[("boprint", 1)].ido_en is None
        flux = filas[("fluxlasers", 20)]
        assert flux.ido_en is None and flux.favorita                  # intacta: la web cayó
        assert s.scalar(select(CatalogoSincronizacion.estado).where(
            CatalogoSincronizacion.id == pasada.id)) == "ok"


def test_lo_que_se_le_ofrece_a_la_ia(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        imagenes.sincronizar(s, http=_webs(), ahora=AHORA)
        s.commit()
        filas = _indexadas(s)
        # La misma foto en otra web cuenta una vez; una oculta no se ofrece nunca.
        s.add(ImagenWeb(sitio="fluxlasers", media_id=99, titulo="Custom Coasters",
                        url="https://fluxlasers.es/c.jpg", ancho=1920, alto=1072,
                        visto_en=AHORA))
        filas[("boprint", 6)].oculta = True
        s.commit()
        assert imagenes.palabras_de("pon el hero de DTF", "artisJet 3000U") == [
            "dtf", "artisjet", "3000u"]
        ofrecidas = imagenes.para_correo(s, palabras=["coasters"], sitio="boprint")
        assert [(o["sitio"], o["titulo"]) for o in ofrecidas] == [("boprint", "custom coasters")]
        assert imagenes.para_correo(s, palabras=["bannerfespa"]) == []
        # Sin palabras que casen, solo las favoritas.
        assert imagenes.para_correo(s, palabras=["nada"]) == []
        filas[("boprint", 2)].favorita = True
        s.commit()
        assert [o["titulo"] for o in imagenes.para_correo(s, palabras=["nada"])] == [
            "smartphone accessories"]
        # Con palabras que casan, las favoritas van delante a igualdad.
        assert [o["titulo"] for o in imagenes.para_correo(s, palabras=["custom", "smartphone"])
                ][:1] == ["smartphone accessories"]
        datos = imagenes.por_ids(s, [filas[("boprint", 1)].id, "no-existe-0000"])
        assert list(datos) == [filas[("boprint", 1)].id]
        assert datos[filas[("boprint", 1)].id]["tamanos"][0]["ancho"] == 768


def test_la_pantalla_lista_marca_favoritas_y_oculta(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        imagenes.sincronizar(s, http=_webs(), ahora=AHORA)
        s.commit()
        coasters = _indexadas(s)[("boprint", 1)].id
    cab = auth_headers(http, "admin")
    r = http.get("/api/erp/conocimiento/imagenes", headers=cab, params={"q": "coast"})
    assert r.status_code == 200, r.text
    assert [i["titulo"] for i in r.json()["imagenes"]] == ["custom coasters"]
    assert r.json()["imagenes"][0]["miniatura"].endswith("-768x429.jpg")
    r = http.patch(f"/api/erp/conocimiento/imagenes/{coasters}", headers=cab,
                   json={"favorita": True})
    assert r.status_code == 200 and r.json()["imagen"]["favorita"] is True
    assert r.json()["estado"]["favoritas"] == 1
    # Ocultar una favorita la deja de ser.
    r = http.patch(f"/api/erp/conocimiento/imagenes/{coasters}", headers=cab,
                   json={"oculta": True})
    assert r.json()["imagen"] == {**r.json()["imagen"], "oculta": True, "favorita": False}
    assert http.patch("/api/erp/conocimiento/imagenes/nada", headers=cab,
                      json={"oculta": True}).status_code == 404
    assert http.patch(f"/api/erp/conocimiento/imagenes/{coasters}", headers=cab,
                      json={}).status_code == 400
    # Quien no tiene la configuración del ERP no la ve.
    assert http.get("/api/erp/conocimiento/imagenes",
                    headers=auth_headers(http, "viewer")).status_code == 403
    todo = http.get("/api/erp/conocimiento", headers=cab).json()
    assert todo["medios"]["imagenes"]["total"] == 5        # 1, 2, 6, 9 y la de fluxlasers
    assert [c["handle"] for c in todo["medios"]["canales"]] == [
        "@MQEuropebvba", "@artisjeteurope", "@fluxinc", "@artisJet"]
    with session_factory() as s:
        fila = s.get(ImagenWeb, coasters)
        assert fila.oculta and not fila.favorita
        # Una pasada nueva no toca lo marcado a mano.
        imagenes.sincronizar(s, http=_webs(), ahora=LUEGO)
        s.commit()
        s.refresh(fila)
        assert fila.oculta and fila.visto_en.replace(tzinfo=UTC) == LUEGO
