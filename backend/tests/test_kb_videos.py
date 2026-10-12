"""Base de conocimiento, Fase 3: la tabla de vídeos de YouTube.

Con los títulos y los metadatos reales de los canales del grupo (12/10/2026):
el idioma sale del título cuando está claro (el del audio dice «en» en los
veinte vídeos de FREEBIRD en cinco idiomas), los temas se detectan solos (los
cuatro vídeos de FREEBIRD, cada uno en cinco idiomas), las máquinas se cruzan
por el título («artis 3000U Pro» es la «artisJet 3000Pro»), la miniatura es la
buena si existe y se guarda cuál, lo corregido a mano no lo pisa la
sincronización y la clave va en la cabecera, nunca en la URL. Nada sale a la
red: YouTube, img.youtube.com y las webs son `MockTransport`.
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

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    FUENTE_VIDEOS,
    TIPO_MAQUINA,
    MarcaCatalogo,
    ProductoCatalogo,
    VideoKb,
)
from app.models.sitios import SitioWeb
from app.services.conocimiento import catalogo, videos
from app.services.correo_compuesto import videos as videos_correo
from tests._test_helpers import auth_headers, seed_test_users

AHORA = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LUEGO = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
CLAVE = "clave-youtube-de-prueba"
CANALES = [{"handle": "@artisjeteurope", "marca": "artisjet"},
           {"handle": "@MQEuropebvba", "marca": ""}]

#: Los cuatro vídeos de FREEBIRD en cinco idiomas, como están en
#: @artisjeteurope: misma fecha, misma duración por vídeo, el audio dice «en».
FREEBIRD: list[tuple[str, str, str, str]] = [
    # (id, idioma de los metadatos, duración, título)
    ("5DMPkemhfm8", "es", "PT2M36S", "Acelera tus proyectos de personalización con FREEBIRD"),
    ("AKy1bYItnAo", "de", "PT2M36S", "Beschleunigen Sie Ihre Personalisierungsprojekte mit "
                                     "FREEBIRD"),
    ("gPQxKCdbAyw", "fr", "PT2M36S", "Accélérez vos projets de personnalisation avec FREEBIRD"),
    ("p971EdQz23E", "nl", "PT2M36S", "Versnel je maatwerkprojecten met FREEBIRD"),
    ("3Vu3x-VxyC0", "pt-PT", "PT2M36S", "Acelere os seus projetos de personalização com "
                                       "FREEBIRD"),
    ("TVzPBSYwBkA", "es", "PT1M47S", "artis 3000U Pro con FREEBIRD – La impresora UV donde la "
                                     "versatilidad se convierte en posibilidades"),
    ("W0gnHonRrvQ", "de", "PT1M47S", "artis 3000U Pro mit FREEBIRD – Der B3+ UV-Drucker, wo "
                                     "Vielseitigkeit auf Möglichkeiten trifft"),
    ("U7g3OKtOkAI", "fr", "PT1M47S", "artis 3000U Pro avec FREEBIRD – L’imprimante UV B3+ où "
                                     "polyvalence rime avec possibilités"),
    ("xigSpGc7cOg", "nl", "PT1M47S", "artis 3000U Pro met FREEBIRD – De B3+ UV-printer waar "
                                     "veelzijdigheid mogelijkheden creëert"),
    ("Pned0OPXdEw", "pt-PT", "PT1M47S", "artis 3000U Pro com FREEBIRD – A impressora UV B3+ onde "
                                       "versatilidade encontra possibilidades"),
    ("keIJei91HnU", "es", "PT1M7S", "FREEBIRD: Menos clics, más resultados"),
    ("m6ifTNpuSlE", "de", "PT1M7S", "FREEBIRD: Weniger Klicks, mehr Ergebnisse"),
    ("1rJjNLHlRRA", "fr", "PT1M7S", "FREEBIRD : Moins de clics, plus de résultats"),
    ("Qv5bDwxHH5U", "nl", "PT1M7S", "FREEBIRD : Minder klikken, meer resultaat"),
    ("ulxjZKNho28", "pt-PT", "PT1M7S", "FREEBIRD: Menos cliques, mais resultados"),
    ("qq7hzHyxXQI", "es", "PT44S", "Cómo 1 + 1 = 10 – La Revolución BEC1GO de artisJet"),
    ("yGtPa0S9UBU", "de", "PT44S", "Wie 1 + 1 = 10 wird – Die BEC1GO Revolution von artisJet"),
    ("jgVtsYGKrA0", "fr", "PT44S", "Comment 1 + 1 = 10 ? La révolution BEC1GO™ d’artisJet"),
    ("L0NPXiSrU-o", "nl", "PT44S", "Hoe 1 + 1 = 10 wordt – De BEC1GO Revolutie van artisJet"),
    ("SjDPr50CdBE", "pt-PT", "PT44S", "Como 1 + 1 = 10 – A Revolução BEC1GO da artisJet"),
]
#: Más vídeos de los canales: uno en inglés del mismo tema de la 3000U (otro
#: día: no se agrupa solo), un «short», y dos de mqeurope (castellano con el
#: audio en «de-DE»; un título de solo nombres propios).
OTROS: list[tuple[str, str, str | None, str, str, str]] = [
    # (id, canal, idioma de los metadatos, audio, duración, título)
    ("zN3a88uSbsA", "artisjet", "en", "en", "PT1M47S",
     "artis 3000U pro With FREEBIRD - Introducing B3+ UV LED Printer, Where Versatility Meets "
     "Possibility"),
    ("h9oLaSQVItk", "artisjet", "en", "en", "PT19S",
     "Varnish glossy diamond print effect with BEC1GO #shorts #uvprinting"),
    ("gcFdoImbmGQ", "mq", "es", "de-DE", "PT3M2S", "Como funciona el horno"),
    ("UwTpV_hCLyI", "mq", "es", "de-DE", "PT1M5S", "ARTIS YOUNG PROUD"),
]
CANAL_ARTISJET = "UCHtDyO5Z9_uiz4UY00MMk5w"
CANAL_MQ = "UCXuEf1o6gHv28VmO5zUnk6w"
SIN_HD = {"UwTpV_hCLyI"}               # sin miniatura buena: maxresdefault da 404


def _item(vid: str, *, canal: str, titulo: str, duracion: str, metadatos: str | None,
          audio: str | None = "en", fecha: str = "2025-10-18T10:00:00Z") -> dict[str, Any]:
    return {"id": vid, "snippet": {
        "title": titulo, "description": "", "publishedAt": fecha,
        "channelId": canal, "channelTitle": "MBO - Artisjet Europe" if canal == CANAL_ARTISJET
        else "mqeurope bv", "defaultLanguage": metadatos, "defaultAudioLanguage": audio,
        "tags": ["shorts"] if "#shorts" in titulo else []},
        "contentDetails": {"duration": duracion}, "status": {"privacyStatus": "public"}}


def _detalles() -> dict[str, dict[str, Any]]:
    salida = {vid: _item(vid, canal=CANAL_ARTISJET, titulo=t, duracion=d, metadatos=m)
              for vid, m, d, t in FREEBIRD}
    for vid, canal, m, audio, d, t in OTROS:
        salida[vid] = _item(vid, canal=CANAL_ARTISJET if canal == "artisjet" else CANAL_MQ,
                            titulo=t, duracion=d, metadatos=m, audio=audio,
                            fecha="2025-04-23T10:00:00Z")
    return salida


def _youtube(*, quitar: set[str] = frozenset(), peticiones: list[httpx.Request] | None = None,
             caida: bool = False) -> httpx.Client:
    detalles = _detalles()
    subidas = {"UU" + CANAL_ARTISJET[2:]: [vid for vid, *_ in FREEBIRD]
               + ["zN3a88uSbsA", "h9oLaSQVItk"],
               "UU" + CANAL_MQ[2:]: ["gcFdoImbmGQ", "UwTpV_hCLyI"]}

    def handler(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(request)
        if request.url.host == "img.youtube.com":
            vid = request.url.path.split("/")[2]
            return httpx.Response(404 if vid in SIN_HD else 200)
        if request.url.host != "www.googleapis.com":
            return httpx.Response(404)
        if caida:
            return httpx.Response(403, json={"error": {
                "code": 403, "message": "The request cannot be completed because you have "
                "exceeded your quota.", "errors": [{"reason": "quotaExceeded"}]}})
        assert request.headers.get("X-Goog-Api-Key") == CLAVE
        ruta = request.url.path.rsplit("/", 1)[-1]
        params = request.url.params
        if ruta == "channels":
            canal = {"@artisjeteurope": CANAL_ARTISJET, "@MQEuropebvba": CANAL_MQ}.get(
                params["forHandle"])
            items = [{"id": canal, "snippet": {"title": params["forHandle"]},
                      "contentDetails": {"relatedPlaylists": {"uploads": "UU" + canal[2:]}}}
                     ] if canal else []
            return httpx.Response(200, json={"items": items})
        if ruta == "playlistItems":
            ids = [v for v in subidas[params["playlistId"]] if v not in quitar]
            return httpx.Response(200, json={"items": [
                {"contentDetails": {"videoId": v}} for v in ids]})
        if ruta == "videos":
            return httpx.Response(200, json={"items": [
                detalles[v] for v in params["id"].split(",") if v in detalles
                and v not in quitar]})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"X-Goog-Api-Key": CLAVE})


def _webs(enlaces: dict[str, list[str]] | None = None) -> httpx.Client:
    """Las páginas de las webs (para los vídeos enlazados) e img.youtube.com."""
    enlaces = enlaces or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "img.youtube.com":
            vid = request.url.path.split("/")[2]
            return httpx.Response(404 if vid in SIN_HD else 200)
        if "/wp/v2/" in request.url.path:
            vids = enlaces.get(request.url.host, []) if request.url.path.endswith("/pages") else []
            contenido = "".join(f'<iframe src="https://www.youtube.com/embed/{v}?rel=0">'
                                "</iframe>" for v in vids)
            return httpx.Response(200, json=[{"content": {"rendered": contenido}}],
                                  headers={"X-WP-TotalPages": "1"})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(SitioWeb(clave="boprint", dominio="boprint.net", host="boprint.net",
                          marca="Boprint", empresa="Streamtec", tipo="web", idioma_defecto="es",
                          idiomas_json='["es"]', posicion=0, activo=True))
        for marca, nombre in (("artisjet", "artisJet 3000Pro"), ("artisjet", "artisJet Young"),
                              ("artisjet", "artisJet Proud"), ("flux", "FLUX Beamo")):
            seed.add(ProductoCatalogo(
                fuente=FUENTE_COMPOSITOR, tipo=TIPO_MAQUINA, marca=marca,
                origen_id=nombre.lower().replace(" ", "-"), nombre=nombre, idioma="es",
                sitio="", visible=True, visto_en=AHORA))
        for marca in ("artisjet", "flux", "mbo"):
            seed.add(MarcaCatalogo(nombre=marca, idioma_defecto="es"))
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


def _sincronizar(s: Session, *, ahora: datetime = AHORA, **kw: Any) -> Any:
    yt = kw.pop("yt", None) or _youtube()
    return videos.sincronizar(s, http=yt, http_webs=kw.pop("webs", None) or _webs(),
                              ahora=ahora, api_key=CLAVE, canales=CANALES, **kw)


def _por_id(s: Session) -> dict[str, VideoKb]:
    return {v.video_id: v for v in s.scalars(select(VideoKb))}


def test_el_idioma_sale_del_titulo_y_si_no_de_los_metadatos() -> None:
    esperado = {"es": "es", "de": "de", "fr": "fr", "nl": "nl", "pt-PT": "pt"}
    for _vid, metadatos, _d, titulo in FREEBIRD:
        assert videos.idioma_de_titulo(titulo) == esperado[metadatos], titulo
    assert videos.idioma_de_titulo(OTROS[0][5]) == "en"
    assert videos.idioma_de_titulo("Como funciona el horno") == "es"
    # Solo nombres propios: no se sabe por el título.
    assert videos.idioma_de_titulo("ARTIS YOUNG PROUD") is None
    assert videos.idioma_efectivo("ARTIS YOUNG PROUD", "es", "de-DE") == ("es", "metadatos")
    assert videos.idioma_efectivo("ARTIS YOUNG PROUD", "zh-Hant", "en-US") == ("en", "audio")
    assert videos.idioma_efectivo("Wie 1 + 1 = 10 wird", "en", "en") == ("de", "titulo")
    assert videos.idioma_efectivo("BEC1GO", None, None) == (None, None)


def test_la_pasada_temas_productos_miniaturas_y_la_clave_en_la_cabecera(
    session_factory: sessionmaker,
) -> None:
    peticiones: list[httpx.Request] = []
    with session_factory() as s:
        pasada = _sincronizar(s, yt=_youtube(peticiones=peticiones),
                              webs=_webs({"boprint.net": ["TVzPBSYwBkA", "dQw4w9WgXcQ"]}))
        s.commit()
        assert pasada.estado == "ok" and pasada.fuente == FUENTE_VIDEOS, pasada.detalle
        filas = _por_id(s)
        # 20 de FREEBIRD + 4 más de los canales; el de la web que no es de los canales
        # no existe en YouTube (no está en «videos»): no entra.
        assert len(filas) == 24
        # La clave, en la cabecera y nunca en la URL.
        api = [r for r in peticiones if r.url.host == "www.googleapis.com"]
        assert api and all("key" not in r.url.params and CLAVE not in str(r.url) for r in api)
        # El idioma de cada vídeo de FREEBIRD sale del título (el audio dice «en»).
        assert {f.idioma for vid, *_ in FREEBIRD for f in [filas[vid]]} == {
            "es", "de", "fr", "nl", "pt"}
        assert filas["keIJei91HnU"].idioma_fuente == "titulo"
        assert filas["UwTpV_hCLyI"].idioma == "es"            # metadatos (el audio dice de-DE)
        # Los cuatro temas de FREEBIRD, cada uno en cinco idiomas, solos.
        temas: dict[str, set[str]] = {}
        for f in filas.values():
            if f.tema:
                temas.setdefault(f.tema, set()).add(str(f.idioma))
        assert len(temas) == 4 and all(len(i) == 5 for i in temas.values())
        assert "freebird-menos-clics-mas-resultados" in temas
        assert filas["zN3a88uSbsA"].tema is None                # otro día: no se agrupa solo
        # «artis 3000U Pro» es la «artisJet 3000Pro», y todo su tema la comparte.
        tres_mil = filas["TVzPBSYwBkA"]
        assert [p["origen_id"] for p in videos.productos(tres_mil)] == ["artisjet-3000pro"]
        assert all(videos.productos(f) == videos.productos(tres_mil)
                   for f in filas.values() if f.tema == tres_mil.tema)
        assert tres_mil.marca == "artisjet"
        assert {p["origen_id"] for p in videos.productos(filas["UwTpV_hCLyI"])} == {
            "artisjet-young", "artisjet-proud"}
        # La miniatura buena si existe, y cuál se usó.
        assert tres_mil.miniatura_calidad == "maxresdefault"
        assert tres_mil.miniatura == "https://img.youtube.com/vi/TVzPBSYwBkA/maxresdefault.jpg"
        assert filas["UwTpV_hCLyI"].miniatura_calidad == "mqdefault"
        assert filas["h9oLaSQVItk"].corto
        assert json.loads(tres_mil.webs_json) == ["boprint"]
        assert tres_mil.origen == "canal" and json.loads(pasada.resumen_json)["miniaturas"] == {
            "buena": 23, "segura": 1, "sin_respuesta": 0}


def test_lo_corregido_a_mano_no_lo_pisa_la_pasada_y_lo_ido_se_marca(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar(s)
        s.commit()
        filas = _por_id(s)
        ingles = filas["zN3a88uSbsA"]
        videos.actualizar(s, ingles.id, {"tema": filas["TVzPBSYwBkA"].tema})
        videos.actualizar(s, filas["UwTpV_hCLyI"].id, {"idioma": "en", "productos": []})
        s.commit()
        # Al día siguiente el short ya no está en el canal.
        pasada = _sincronizar(s, ahora=LUEGO, yt=_youtube(quitar={"h9oLaSQVItk"}))
        s.commit()
        assert pasada.estado == "ok" and pasada.idos == 1
        filas = _por_id(s)
        assert filas["zN3a88uSbsA"].tema == filas["TVzPBSYwBkA"].tema     # se queda
        assert videos.productos(filas["zN3a88uSbsA"]) == videos.productos(
            filas["TVzPBSYwBkA"])                                          # y comparte productos
        assert filas["UwTpV_hCLyI"].idioma == "en" and videos.productos(filas["UwTpV_hCLyI"]) == []
        assert filas["UwTpV_hCLyI"].idioma_fuente == "manual"
        assert filas["h9oLaSQVItk"].ido_en is not None                      # ido, no borrado
        # Volver a lo automático.
        videos.actualizar(s, filas["UwTpV_hCLyI"].id, {"automatico": ["idioma", "productos"]})
        s.commit()
        assert filas["UwTpV_hCLyI"].idioma == "es" and len(videos.productos(
            filas["UwTpV_hCLyI"])) == 2
        with pytest.raises(ValueError):
            videos.actualizar(s, filas["UwTpV_hCLyI"].id, {"idioma": "klingon"})
        with pytest.raises(ValueError):
            videos.actualizar(s, filas["UwTpV_hCLyI"].id, {"productos": ["no-existe"]})


def test_sin_clave_o_con_la_api_caida_la_tabla_queda_intacta(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    get_settings.cache_clear()
    with session_factory() as s:
        sin_clave = videos.sincronizar(s, canales=CANALES, ahora=AHORA)
        assert sin_clave.estado == "error" and "YOUTUBE_API_KEY" in sin_clave.detalle
        _sincronizar(s)
        s.commit()
        antes = len(_por_id(s))
        caida = _sincronizar(s, ahora=LUEGO, yt=_youtube(caida=True))
        s.commit()
        assert caida.estado == "error" and "quotaExceeded" in caida.detalle
        assert CLAVE not in (caida.detalle or "") and CLAVE not in (caida.resumen_json or "")
        assert len(_por_id(s)) == antes and all(f.ido_en is None for f in _por_id(s).values())
    get_settings.cache_clear()


def test_lo_que_se_le_ofrece_a_la_ia_y_lo_que_resuelve_el_correo(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar(s)
        s.commit()
        filas = _por_id(s)
        videos.actualizar(s, filas["zN3a88uSbsA"].id, {"tema": filas["TVzPBSYwBkA"].tema})
        s.commit()
        # Un neerlandés que pregunta por la 3000Pro: la versión neerlandesa del tema.
        nl = videos.para_correo(s, idioma="nl", productos=["artisjet-3000pro"])
        assert [v["id"] for v in nl] == ["xigSpGc7cOg"]
        # Un italiano: la inglesa (el tema la tiene); FREEBIRD por la instrucción.
        it = videos.para_correo(s, idioma="it", palabras=["freebird"])
        assert [v["id"] for v in it] == ["zN3a88uSbsA"]
        # El short no se ofrece nunca; uno oculto, tampoco.
        assert "h9oLaSQVItk" not in {v["id"] for v in videos.para_correo(
            s, idioma="en", palabras=["bec1go", "varnish"])}
        filas["keIJei91HnU"].visible = False
        s.commit()
        assert videos.para_correo(s, idioma="es", palabras=["clics"]) == []
        # El correo en alemán con el vídeo castellano: sale la versión alemana.
        datos = videos_correo.resolver(s, [{"plataforma": "youtube", "id": "TVzPBSYwBkA"}],
                                       idioma="de")
        assert datos["youtube:TVzPBSYwBkA"]["url"] == "https://www.youtube.com/watch?v=W0gnHonRrvQ"
        assert datos["youtube:TVzPBSYwBkA"]["miniatura"].endswith("W0gnHonRrvQ/maxresdefault.jpg")
        assert datos["youtube:TVzPBSYwBkA"]["duracion"] == 107
        # Un vídeo que no está en la tabla: lo que da su URL (la miniatura segura).
        fuera = videos_correo.resolver(s, [{"plataforma": "youtube", "id": "dQw4w9WgXcQ"}],
                                       idioma="es")["youtube:dQw4w9WgXcQ"]
        assert fuera["origen"] == "url" and fuera["miniatura"].endswith("/mqdefault.jpg")


def test_la_pantalla_y_pegar_una_url(session_factory: sessionmaker, http: TestClient,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    get_settings.cache_clear()
    with session_factory() as s:
        _sincronizar(s)
        s.commit()
        tres_mil = _por_id(s)["TVzPBSYwBkA"].id
    cab = auth_headers(http, "admin")
    r = http.get("/api/erp/conocimiento/videos", headers=cab, params={"idioma": "nl"})
    assert r.status_code == 200 and r.json()["total"] == 4
    r = http.get("/api/erp/conocimiento/videos", headers=cab, params={"q": "3000u pro"})
    assert {v["video_id"] for v in r.json()["videos"]} >= {"TVzPBSYwBkA", "zN3a88uSbsA"}
    r = http.patch(f"/api/erp/conocimiento/videos/{tres_mil}", headers=cab,
                   json={"marca": "MBO", "visible": False})
    assert r.status_code == 200, r.text
    assert r.json()["video"]["marca"] == "MBO" and r.json()["video"]["visible"] is False
    assert r.json()["video"]["manual"] == ["marca"]
    r = http.patch(f"/api/erp/conocimiento/videos/{tres_mil}", headers=cab,
                   json={"productos": [{"origen_id": "artisjet-young"}]})
    assert [p["nombre"] for p in r.json()["video"]["productos"]] == ["artisJet Young"]
    assert http.patch(f"/api/erp/conocimiento/videos/{tres_mil}", headers=cab,
                      json={"idioma": "xx"}).status_code == 400
    # Pegar una URL suelta sin clave: entra con su miniatura; Vimeo no.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(videos.imagenes, "_cliente", lambda: _webs())
        r = http.post("/api/erp/conocimiento/videos", headers=cab,
                      json={"url": "https://youtu.be/dQw4w9WgXcQ?t=3"})
    assert r.status_code == 201, r.text
    nuevo = r.json()["video"]
    assert nuevo["origen"] == "manual" and nuevo["miniatura_calidad"] == "maxresdefault"
    assert http.post("/api/erp/conocimiento/videos", headers=cab,
                     json={"url": "https://vimeo.com/1112093555"}).status_code == 400
    # Otra vez la misma: la que hay.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(videos.imagenes, "_cliente", lambda: _webs())
        otra = http.post("/api/erp/conocimiento/videos", headers=cab,
                         json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert otra.json()["video"]["id"] == nuevo["id"]
    estado = http.get("/api/erp/conocimiento", headers=cab).json()["medios"]["videos"]
    assert estado["total"] == 25 and estado["temas"] == 4 and estado["clave_configurada"] is False
    assert estado["ultima_sincronizacion"]["fuente"] == FUENTE_VIDEOS
    # Una pasada nueva no toca lo pegado a mano.
    with session_factory() as s:
        _sincronizar(s, ahora=LUEGO)
        s.commit()
        assert _por_id(s)["dQw4w9WgXcQ"].ido_en is None
        assert _por_id(s)["TVzPBSYwBkA"].marca == "MBO"
    get_settings.cache_clear()


def test_productos_por_el_titulo_dentro_de_la_marca(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        grupos = catalogo._maquinas_por_producto(s)
        def nombres(titulo: str, marca: str | None) -> list[str]:
            return [p["nombre"] for p in videos.productos_en_titulo(titulo, grupos, marca)]
        assert nombres("artis 3000U pro UV LED printer in Bosnia", "artisjet") == [
            "artisJet 3000Pro"]
        assert nombres("FLUX beamo | World's Smallest CO2 Laser Cutter", "flux") == ["FLUX Beamo"]
        # El número de modelo solo cuenta dentro de la marca del vídeo.
        assert nombres("Lego printing with the 3000 series", "flux") == []
        # Un modelo de una sola palabra es su nombre entero, pero solo dentro de su marca.
        assert nombres("Be Proud of the Next Card You Print", "artisjet") == ["artisJet Proud"]
        assert nombres("Be Proud of the Next Card You Print", "flux") == []
        assert sorted(nombres("ARTIS YOUNG PROUD", None)) == ["artisJet Proud", "artisJet Young"]
        assert videos.marca_en_titulo("IMPRESORAS UV LED ARTISJET A3000 PRO", ["artisjet",
                                                                              "flux"]) == (
            "artisjet")
