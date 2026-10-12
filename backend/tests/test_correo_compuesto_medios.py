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
from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    TIPO_MAQUINA,
    ImagenWeb,
    ProductoCatalogo,
    VideoKb,
)
from app.models.crm import User, UserEmailAliasPref, UserRole
from app.models.ia import BorradorAsistente
from app.services import llm
from app.services.asistente import redactor
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import generador, ia
from app.services.html_sanitizer import sanitize_email_html
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users
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


def test_los_pies_siguen_a_su_imagen_y_el_hero_reemplazado_conserva_la_suya() -> None:
    refs = ia.Referencias.desde(maquinas=[], consumibles=[], enlaces=[], videos=[], marcas={},
                                imagenes=[{"id": IMG["camisetas"], "titulo": "custom t-shirts"}])
    base = bq.normalizar({"idioma": "es", "bloques": [
        {"id": "h1", "tipo": "hero", "imagen": {"id": IMG["taller"]}, "titulo": "DTF"},
        {"id": "m1", "tipo": "muestras", "pies": ["Sudaderas", "Gorras", "Bodas"],
         "imagenes": [{"id": IMG["sudaderas"]}, {"id": IMG["gorras"]}, {"id": IMG["bodas"]}]}]})
    # Un pie vacío se queda vacío: los demás no se corren a la imagen de al lado.
    pies = ["", "Gorras bordadas", "Regalos de boda"]
    res = ia.aplicar_operaciones(base, [{"op": "editar", "id": "m1", "campo": "pies",
                                         "valor": pies}], refs)
    assert res.estructura["bloques"][1]["pies"] == ["", "Gorras bordadas", "Regalos de boda"]
    # Cambiar las imágenes: la que se queda conserva su pie y la nueva entra sin él.
    res = ia.aplicar_operaciones(base, [{"op": "editar", "id": "m1", "campo": "imagenes", "valor": [
        f"?{IMG['bodas']}", "I1", f"?{IMG['sudaderas']}"]}], refs)
    assert res.estructura["bloques"][1]["pies"] == ["Bodas", "", "Sudaderas"]
    # Reemplazar el hero con la imagen que ya tenía (la IA la ve como «?id» porque no
    # está entre las de este turno): vale, y cambia lo que se pidió.
    res = ia.aplicar_operaciones(base, [{"op": "reemplazar", "id": "h1", "bloque": {
        "tipo": "hero", "imagen": f"?{IMG['taller']}", "disposicion": "fondo",
        "titulo": "DTF"}}], refs)
    assert res.estructura["bloques"][0]["imagen"] == {"id": IMG["taller"]}
    assert res.estructura["bloques"][0]["disposicion"] == "fondo" and not res.avisos
    # Una que no está ni en el correo ni en la lista, no.
    ajena = "9f9f9f9f-0000-4000-8000-000000000099"
    res = ia.aplicar_operaciones(base, [{"op": "reemplazar", "id": "h1", "bloque": {
        "tipo": "hero", "imagen": f"?{ajena}", "titulo": "DTF"}}], refs)
    assert res.estructura["bloques"][0]["imagen"] == {"id": IMG["taller"]}
    assert any("no está entre las IMÁGENES" in a for a in res.avisos)


def test_las_imagenes_de_una_maquina_salen_de_su_nombre_sin_las_palabras_de_la_marca(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        for origen_id, nombre in (("mbo-dtf-a3", "MBO DTF A3"),
                                  ("mbo-dtf-prensa", "MBO DTF Hoodie Press")):
            s.add(ProductoCatalogo(fuente=FUENTE_COMPOSITOR, tipo=TIPO_MAQUINA, marca="MBO DTF",
                                   origen_id=origen_id, nombre=nombre, idioma="es", sitio="",
                                   visible=True, visto_en=AHORA))
        s.commit()
        marcas = {"mbo_dtf": {"nombre": "MBO DTF"}}

        def ofrecidas(origen_id: str) -> set[str]:
            refs = ia.Referencias.desde(
                maquinas=[{"fuente": "compositor", "origen_id": origen_id}], consumibles=[],
                enlaces=[], videos=[], marcas=marcas)
            redactor.anadir_medios(s, refs, instruccion=None, idioma="es")
            return {i["titulo"] for i in refs.imagenes.values()}

        # «MBO DTF A3»: las palabras de la marca (también las de su clave «mbo_dtf») no
        # cuentan, así que no se le ofrecen todas las fotos de DTF.
        assert ofrecidas("mbo-dtf-a3") == set()
        # «Hoodie Press»: la de las sudaderas.
        assert ofrecidas("mbo-dtf-prensa") == {"dtf hoodies"}


def test_en_una_respuesta_el_saludo_va_delante_del_hero_y_un_mismo_boton_sale_una_vez(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """12/10/2026, el hilo de talentosdiversos: el correo abría con un hero a toda
    página y el «Hola Marny» venía después (maquetación de correo frío), y «Ver la
    gama DTF» salía en el hero y otra vez al final."""
    with session_factory() as s:
        thread_id = _hilo_talentos(s)
    hero = {"tipo": "hero", "imagen": "I1", "titulo": "La gama DTF",
            "botones": [{"texto": "Ver la gama DTF", "enlace": "M:mbo_dtf"}]}
    respuesta = {**COMPUESTO_DTF, "bloques": [hero, *COMPUESTO_DTF["bloques"]]}
    falso = _ClaudeFalso(respuesta)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="mándale la gama DTF con fotos de trabajos")
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    bloques = b["estructura"]["bloques"]
    # El saludo primero; el «cta» del final llevaba el mismo enlace que el hero: fuera.
    assert [x["tipo"] for x in bloques] == [
        "texto", "hero", "marca", "productos_fila", "video", "texto"]
    assert "Hola Marny" in bloques[0]["html"]
    assert b["html"].count("Ver la gama DTF") == 1 and "Ver toda la gama" not in b["html"]
    assert b["html"].index("Hola Marny") < b["html"].index("La gama DTF")
    assert "se quita «Ver toda la gama»" in b["aviso"]
    sistema = falso.ultimo["system"]
    assert "el hero va DESPUÉS" in sistema and "un mismo enlace va en un solo botón" in sistema
    # Si la persona deja el hero arriba en la vista previa, una corrección lo respeta.
    a_mano = {**b["estructura"], "bloques": [bloques[1], bloques[0], *bloques[2:]]}
    correccion = {"modo": "compuesto", "asunto": "Re: Equipos DTF", "aviso": "", "pendiente": [],
                  "operaciones": [{"op": "editar", "id": bloques[-1]["id"], "campo": "texto",
                                   "valor": "Un saludo,\nAdmin"}]}
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(correccion)):
        r2 = _turno(http, b["id"], "acorta la despedida", estructura_actual=a_mano)
    assert r2.status_code == 201, r2.text
    assert [x["tipo"] for x in r2.json()["borrador"]["estructura"]["bloques"]][:2] == [
        "hero", "texto"]


def test_en_un_correo_nuevo_sin_conversacion_el_hero_puede_abrir(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Un correo frío: sin hilo y sin correos anteriores con esa dirección."""
    respuesta = {"modo": "compuesto", "asunto": "La gama DTF", "idioma": "es", "aviso": "",
                 "pendiente": [], "bloques": [
                     {"tipo": "hero", "imagen": "I1", "titulo": "La gama DTF"},
                     {"tipo": "texto", "texto": "Hola,\n\nte presento la gama DTF."}]}
    with patch.object(llm, "_invoke_claude", _ClaudeFalso(respuesta)):
        r = http.post("/api/emails/asistente", headers=auth_headers(http, "admin"), json={
            "destinatario": "nadie@nuevo.example", "formato": "compuesto",
            "instruccion": "preséntale la gama DTF con una foto de cabecera"})
    assert r.status_code == 201, r.text
    assert [x["tipo"] for x in r.json()["borrador"]["estructura"]["bloques"]] == [
        "hero", "texto"]
