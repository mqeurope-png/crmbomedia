"""Correos compuestos con las imágenes de las webs y la tabla de vídeos.

- El hero (imagen encima, al lado o de fondo, con título, subtítulo, puntos y
  botones) y la galería de muestras (de 2 a 6 imágenes en columnas que se
  apilan en el móvil) salen en tablas con estilos en línea, con el tamaño de
  la imagen que toca a su ancho y nunca la original de varios megas.
- La IA solo puede poner imágenes de la lista IMÁGENES (`I#`), que sale del
  índice por lo que pide la instrucción, las máquinas del correo y el
  interés; una inventada no entra. «Pon el hero de DTF» en una corrección
  añade el hero y deja lo demás igual.
- Los vídeos de la tabla van con las máquinas del correo, en el idioma del
  cliente.
"""

from __future__ import annotations

import json
import re
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
from app.models.conocimiento import ImagenWeb, VideoKb
from app.models.crm import User, UserEmailAliasPref, UserRole
from app.models.ia import BorradorAsistente
from app.services import llm
from app.services.asistente import redactor
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import generador, ia
from app.services.html_sanitizer import sanitize_email_html
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import seed_org_google_integration, seed_test_users
from tests.test_asistente import ALIAS, _ClaudeFalso, _pedir
from tests.test_asistente_compuesto import COMPUESTO_DTF, _hilo_talentos, _turno
from tests.test_correo_compuesto import _comprobar_html_de_correo

AHORA = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
IMG = {
    "taller": "1a1a1a1a-0000-4000-8000-0000000000a1",
    "camisetas": "1b1b1b1b-0000-4000-8000-0000000000b1",
    "sudaderas": "1c1c1c1c-0000-4000-8000-0000000000c1",
    "bodas": "1d1d1d1d-0000-4000-8000-0000000000d1",
    "gorras": "1e1e1e1e-0000-4000-8000-0000000000e1",
}
TITULOS = {"taller": "dtf printer at work", "camisetas": "custom t-shirts dtf",
           "sudaderas": "dtf hoodies", "bodas": "wedding gifts", "gorras": "dtf caps"}


def _tamanos(nombre: str) -> list[dict[str, Any]]:
    base = f"https://boprint.net/wp-content/uploads/2026/05/{nombre}"
    return [{"url": f"{base}-768x429.jpg", "ancho": 768, "alto": 429},
            {"url": f"{base}-1536x858.jpg", "ancho": 1536, "alto": 858},
            {"url": f"{base}.jpg", "ancho": 1920, "alto": 1072}]


def _imagenes(s: Session) -> None:
    for i, (clave, iid) in enumerate(IMG.items()):
        s.add(ImagenWeb(id=iid, sitio="boprint", media_id=100 + i, titulo=TITULOS[clave],
                        url=f"https://boprint.net/wp-content/uploads/2026/05/{clave}.jpg",
                        tamanos_json=json.dumps(_tamanos(clave)), ancho=1920, alto=1072,
                        mime="image/jpeg", visto_en=AHORA))


def _datos() -> generador.Datos:
    return generador.Datos(imagenes={
        iid: {"id": iid, "titulo": TITULOS[clave], "ancho": 1920, "alto": 1072,
              "url": f"https://boprint.net/wp-content/uploads/2026/05/{clave}.jpg",
              "tamanos": _tamanos(clave)} for clave, iid in IMG.items()})


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
        _imagenes(seed)
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


def _generar(bloques: list[dict[str, Any]], *, preview: bool = False) -> generador.Correo:
    return generador.generar({"idioma": "es", "bloques": bloques}, _datos(), asunto="DTF",
                             preview=preview)


HERO = {"tipo": "hero", "imagen": {"id": IMG["taller"]}, "titulo": "La gama DTF",
        "subtitulo": "De la pantalla a la prenda en un día",
        "puntos": ["Sin pretratamiento", "Colores vivos en algodón y poliéster"],
        "botones": [{"texto": "Ver la gama", "url": "https://mboprinters.com/es/dtf/"}]}


def test_el_hero_en_sus_tres_disposiciones() -> None:
    arriba = _generar([HERO])
    _comprobar_html_de_correo(arriba.html)
    # A todo el ancho (584 px en el correo): el tamaño que cubre el doble, no la original.
    assert re.search(r'<img src="[^"]*taller-1536x858\.jpg" alt="La gama DTF" width="584"',
                     arriba.html)
    assert "taller.jpg" not in arriba.html
    assert "La gama DTF" in arriba.html and "Colores vivos en algodón" in arriba.html
    assert arriba.texto.splitlines() == [
        "La gama DTF", "De la pantalla a la prenda en un día", "✓ Sin pretratamiento",
        "✓ Colores vivos en algodón y poliéster", "Ver la gama: https://mboprinters.com/es/dtf/"]
    lateral = _generar([{**HERO, "disposicion": "lateral"}])
    _comprobar_html_de_correo(lateral.html)
    # Imagen y texto en dos columnas que se apilan en el móvil; la imagen, a su mitad.
    assert lateral.html.count('class="bh-col') >= 2
    assert re.search(r'taller-768x429\.jpg" alt="La gama DTF" width="282"', lateral.html)
    fondo = _generar([{**HERO, "disposicion": "fondo"}])
    _comprobar_html_de_correo(fondo.html)
    # De fondo: el atributo y el estilo (Gmail, Apple Mail) y el color oscuro que
    # enseña Outlook; el texto, en blanco.
    assert re.search(r'<td background="https://[^"]*taller-1536x858\.jpg" bgcolor="#0f172a"',
                     fondo.html)
    assert "background-image:url('https://boprint.net/wp-content/uploads/2026/05/" \
           "taller-1536x858.jpg')" in fondo.html
    assert "color:#ffffff" in fondo.html and "<img" not in fondo.html
    # Una imagen que ya no está en el índice: el hero no sale, y se dice.
    sin = generador.generar({"idioma": "es", "bloques": [{**HERO, "imagen": {
        "id": "ffffffff-0000-4000-8000-000000000000"}}]}, _datos())
    assert "<img" not in sin.html and any("ya no está en el índice" in a for a in sin.avisos)


def test_las_muestras_en_columnas_con_su_pie() -> None:
    cinco = [{"id": IMG[c]} for c in ("taller", "camisetas", "sudaderas", "bodas", "gorras")]
    correo = _generar([{"tipo": "muestras", "titulo": "Trabajos en DTF", "imagenes": cinco,
                        "pies": ["Taller", "Camisetas", "", "Bodas", "Gorras"]}])
    _comprobar_html_de_correo(correo.html)
    filas = re.findall(r"<tr><td class=\"bh-col bh-ficha\".*?</tr>", correo.html)
    # 5 → una fila de tres y otra de dos (con su hueco).
    assert [f.count('class="bh-col bh-ficha"') for f in filas] == [3, 2]
    assert re.search(r'<img src="[^"]*camisetas-768x429\.jpg" alt="Camisetas" width="182"',
                     correo.html)
    # Sin pie, el texto alternativo es el título de la imagen.
    assert 'alt="dtf hoodies"' in correo.html
    assert correo.texto.splitlines() == ["Trabajos en DTF", "Taller", "Camisetas", "Bodas",
                                         "Gorras"]
    # Menos de dos imágenes no es una galería; más de seis se recortan.
    avisos: list[str] = []
    e = bq.normalizar({"bloques": [{"tipo": "muestras", "imagenes": cinco[:1]},
                                   {"tipo": "muestras", "imagenes": cinco + [
                                       {"id": "aaaaaaaa-0000-4000-8000-000000000001"},
                                       {"id": "aaaaaaaa-0000-4000-8000-000000000002"}]}]},
                      avisos)
    assert [len(b["imagenes"]) for b in e["bloques"]] == [6]
    assert any("al menos 2" in a for a in avisos) and any("como mucho 6" in a for a in avisos)
    # La vista previa: cada pie y el título se editan pulsando encima.
    vista = _generar([{"tipo": "muestras", "id": "m1", "titulo": "Trabajos",
                       "imagenes": cinco[:2], "pies": ["Uno", "Dos"]}], preview=True)
    assert {"m1:titulo", "m1:pies.0", "m1:pies.1"} <= set(
        re.findall(r'data-bh-campo="([^"]+)"', vista.html))


def test_el_saneado_conserva_el_fondo_del_hero_y_nada_mas() -> None:
    html = _generar([{**HERO, "disposicion": "fondo"}]).html
    limpio = sanitize_email_html(html)
    assert 'background="https://boprint.net/wp-content/uploads/2026/05/taller-1536x858.jpg"' in (
        limpio)
    assert "background-image:url(" in limpio and "background-size:cover" in limpio
    malo = sanitize_email_html(html.replace(
        'background="https://', 'background="javascript:alert(1)//https://'))
    assert "javascript:" not in malo


def test_la_ia_pone_hero_y_muestras_solo_de_las_imagenes_de_la_lista(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    respuesta = {**COMPUESTO_DTF, "bloques": [
        COMPUESTO_DTF["bloques"][0],
        {"tipo": "hero", "imagen": "I1", "titulo": "La gama DTF para tu taller",
         "puntos": ["Sin pretratamiento"], "disposicion": "arriba",
         "botones": [{"texto": "Ver la gama", "enlace": "M:mbo_dtf"}]},
        {"tipo": "productos_fila", "productos": ["P1", "P2"]},
        {"tipo": "muestras", "imagenes": ["I2", "I3", "I9"], "pies": ["Camisetas", "Sudaderas",
                                                                      "Inventada"]},
        COMPUESTO_DTF["bloques"][-1],
    ]}
    falso = _ClaudeFalso(respuesta)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="mándale la gama DTF con fotos de trabajos")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    _, peticion = (bloque["text"] for bloque in falso.ultimo["bloques"])
    linea = next(ln for ln in peticion.splitlines() if ln.startswith("IMÁGENES"))
    refs = dict(re.findall(r"\[(I\d+)\] ([^(]+?) \(boprint", linea))
    # Las del DTF, y no la de las bodas (no casa con nada de lo que se pide).
    assert set(refs.values()) == {"dtf printer at work", "custom t-shirts dtf", "dtf hoodies",
                                  "dtf caps"}
    assert "hero" in falso.ultimo["system"] and "muestras" in falso.ultimo["system"]
    por_titulo = {v: k for k, v in refs.items()}
    estructura = b["estructura"]
    hero = estructura["bloques"][1]
    assert hero["tipo"] == "hero" and hero["imagen"] == {
        "id": IMG[next(c for c, t in TITULOS.items() if t == refs["I1"])]}
    assert hero["botones"] == [{"texto": "Ver la gama", "url": "https://mboprinters.com/es/"}]
    muestras = estructura["bloques"][3]
    # La inventada (I9) no entra; las otras dos sí, con su pie.
    assert len(muestras["imagenes"]) == 2 and muestras["pies"] == ["Camisetas", "Sudaderas"]
    assert "no están entre las IMÁGENES" in (b["aviso"] or "")
    assert "-1536x858.jpg" in b["html"] and "-768x429.jpg" in b["html"]
    assert "<div" not in b["html"].lower()
    with session_factory() as s:
        fila = s.get(BorradorAsistente, b["id"])
        guardadas = json.loads(fila.contexto_json)["refs"]["imagenes"]
        assert guardadas["I1"]["id"] == hero["imagen"]["id"] and len(guardadas) == 4
    assert por_titulo  # las referencias se guardan para las correcciones


def test_pon_el_hero_de_dtf_en_una_correccion_y_lo_demas_queda_igual(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(COMPUESTO_DTF)):
        primero = _pedir(http, thread_id, instruccion="mándale la gama DTF").json()["borrador"]
    estructura = primero["estructura"]
    correccion = {"modo": "compuesto", "asunto": "Re: Equipos DTF", "aviso": "", "pendiente": [],
                  "operaciones": [{"op": "añadir", "despues_de": estructura["bloques"][0]["id"],
                                   "bloque": {"tipo": "hero", "imagen": "I1",
                                              "titulo": "La gama DTF"}}]}
    falso = _ClaudeFalso(correccion)
    with patch.object(llm, "_invoke_claude", falso):
        r = _turno(http, primero["id"], "pon el hero de DTF", estructura_actual=estructura)
    assert r.status_code == 201, r.text
    peticion = falso.ultimo["bloques"][1]["text"]
    assert "IMÁGENES de las webs" in peticion and "[I1]" in peticion
    nueva = r.json()["borrador"]["estructura"]
    assert [x["tipo"] for x in nueva["bloques"]] == [
        "texto", "hero", "marca", "productos_fila", "video", "cta", "texto"]
    # Lo que no se tocó, byte a byte.
    sin_hero = [x for x in nueva["bloques"] if x["tipo"] != "hero"]
    assert sin_hero == estructura["bloques"]
    assert nueva["bloques"][1]["imagen"]["id"] in IMG.values()


def test_editar_el_hero_y_las_muestras_no_pierde_las_imagenes_que_ya_tenian() -> None:
    refs = ia.Referencias.desde(maquinas=[], consumibles=[], enlaces=[], videos=[], marcas={},
                                imagenes=[{"id": IMG["camisetas"], "titulo": "custom t-shirts"}])
    base = bq.normalizar({"idioma": "es", "bloques": [
        {"id": "h1", "tipo": "hero", "imagen": {"id": IMG["taller"]}, "titulo": "DTF",
         "puntos": ["Uno"]},
        {"id": "m1", "tipo": "muestras", "imagenes": [{"id": IMG["sudaderas"]},
                                                      {"id": IMG["gorras"]}]}]})
    # Para la IA: la imagen que no está en la lista va como «?id».
    vista = json.loads(ia.estructura_para_ia(base, refs))["bloques"]
    assert vista[0]["imagen"] == f"?{IMG['taller']}" and vista[0]["puntos"] == ["Uno"]
    res = ia.aplicar_operaciones(base, [
        {"op": "editar", "id": "h1", "campo": "puntos", "valor": ["Rápida", "**Sin** <b>HTML</b>"]},
        {"op": "editar", "id": "h1", "campo": "disposicion", "valor": "lateral"},
        {"op": "editar", "id": "m1", "campo": "imagenes",
         "valor": [f"?{IMG['sudaderas']}", "I1", "I7"]},
        {"op": "editar", "id": "m1", "campo": "pies", "valor": ["Sudaderas", "Camisetas"]},
    ], refs)
    hero, muestras = res.estructura["bloques"]
    assert hero["puntos"] == ["Rápida", "**Sin** HTML"] and res.html_descartado == 1
    assert hero["disposicion"] == "lateral" and hero["imagen"] == {"id": IMG["taller"]}
    assert muestras["imagenes"] == [{"id": IMG["sudaderas"]}, {"id": IMG["camisetas"]}]
    assert muestras["pies"] == ["Sudaderas", "Camisetas"]
    # Una imagen que no tenía ni está en la lista no se cuela al editar el hero.
    otra = ia.aplicar_operaciones(base, [{"op": "editar", "id": "h1", "campo": "imagen",
                                          "valor": IMG["bodas"]}], refs)
    assert otra.estructura["bloques"][0]["imagen"] == {"id": IMG["taller"]}
    assert any("no se ha podido" in a for a in otra.avisos)
    # La guardia y la regla de los precios miran también los textos nuevos.
    assert "Rápida" in ia.texto_libre(res.estructura)


def test_los_videos_de_la_tabla_van_con_las_maquinas_en_el_idioma_del_cliente(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        for vid, idioma, titulo in (("aaaaaaaaaa1", "es", "La Orion 20 en marcha"),
                                    ("aaaaaaaaaa2", "de", "Die Orion 20 im Einsatz"),
                                    ("aaaaaaaaaa3", "en", "Orion 20 in action")):
            s.add(VideoKb(plataforma="youtube", video_id=vid, titulo=titulo, idioma=idioma,
                          url=f"https://www.youtube.com/watch?v={vid}", tema="orion-20",
                          productos_json=json.dumps([{"fuente": "compositor",
                                                      "origen_id": "orion-20",
                                                      "nombre": "Orion 20"}]),
                          miniatura_calidad="maxresdefault", duracion=95, visto_en=AHORA))
        s.commit()
        refs = ia.Referencias.desde(
            maquinas=[{"fuente": "compositor", "origen_id": "orion-20", "nombre": "Orion 20"}],
            consumibles=[], enlaces=[], videos=[], marcas={})
        redactor.anadir_medios(s, refs, instruccion="mándale la gama", idioma="de")
        assert [v["id"] for v in refs.videos.values()] == ["aaaaaaaaaa2"]
        cabecera = redactor.formato_del_turno(ia.MODO_COMPUESTO, refs, "de")
        assert ("[V1] https://www.youtube.com/watch?v=aaaaaaaaaa2 (Die Orion 20 im Einsatz · de)"
                in cabecera)
        # En italiano, la inglesa.
        refs_it = ia.Referencias.desde(maquinas=[{"fuente": "compositor",
                                                  "origen_id": "orion-20"}],
                                       consumibles=[], enlaces=[], videos=[], marcas={})
        redactor.anadir_medios(s, refs_it, instruccion=None, idioma="it")
        assert [v["id"] for v in refs_it.videos.values()] == ["aaaaaaaaaa3"]
