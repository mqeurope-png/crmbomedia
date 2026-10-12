"""Correos compuestos: los bloques, el generador de HTML de correo y su
texto plano, y la ruta de render que usa la vista previa.

Lo que se comprueba es lo que exigen los clientes de correo: tablas y
estilos en línea (ni un `<div>` de maquetación, la única hoja de estilo es la
consulta de medio que apila las columnas por debajo de 480 px), imágenes con
`alt`, ancho y URL absoluta, nada de JavaScript, SVG ni fuentes externas. Y
que los datos de producto salen del catálogo, en el idioma del cliente.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    FUENTE_WOOCOMMERCE,
    TIPO_CONSUMIBLE,
    TIPO_MAQUINA,
    MarcaCatalogo,
    ProductoCatalogo,
)
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import datos as datos_mod
from app.services.correo_compuesto import generador
from tests._test_helpers import auth_headers, seed_test_users

AHORA = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
#: Los precios del compositor sin IVA, los de las tiendas con IVA.
CONFIG = {"iva_por_fuente": {"compositor": "sin", "woocommerce": "con"},
          "precio_meses_orientativo": 6}


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
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


def _maquina(s: Session, origen_id: str, idioma: str, nombre: str, *, precio: str | None,
             publico: bool = True, enlace: str | None = None,
             caracteristicas: list[str] | None = None,
             categoria: str | None = "A3", marca: str = "mbo_dtf") -> None:
    s.add(ProductoCatalogo(
        fuente=FUENTE_COMPOSITOR, tipo=TIPO_MAQUINA, marca=marca, origen_id=origen_id,
        nombre=nombre, descripcion=f"{nombre}: impresora DTF de producción.",
        caracteristicas_json=json.dumps(caracteristicas or ["20 cm", "Cabezal i3200"]),
        categoria=categoria, imagen=f"https://cdn.example/{origen_id}-{idioma}.jpg",
        precio_texto=precio, precio_publico=publico, moneda="EUR" if precio else None,
        enlace=enlace or f"https://mboprinters.com/{idioma}/dtf/{origen_id}/", idioma=idioma,
        sitio="mboprinters", actualizado_origen=AHORA, visto_en=AHORA,
    ))


def _catalogo(s: Session) -> None:
    """Cuatro máquinas DTF (una sin precio público) en es y en, un consumible
    de tienda y la marca con su logo, color y webs."""
    for oid, nombre, precio_es, precio_en in (
            ("o20", "Orion 20", "4.990 €", "4,990 €"), ("l35", "Lyra 35", "7.900 €", "7,900 €"),
            ("v60", "Vega 60 Pro", "desde 21.500 €", "from 21,500 €")):
        _maquina(s, oid, "es", nombre, precio=precio_es)
        _maquina(s, oid, "en", nombre, precio=precio_en,
                 caracteristicas=["20 cm wide", "i3200 head"])
    _maquina(s, "a35", "es", "Astra 35 Pro", precio=None, publico=False)
    s.add(ProductoCatalogo(
        fuente=FUENTE_WOOCOMMERCE, tipo=TIPO_CONSUMIBLE, marca="mbo", origen_id="mbo:77",
        nombre="Tinta DTF blanca 1 L", categoria="Consumibles / Tintas",
        imagen="https://tienda.example/tinta.jpg", precio_texto="49,00 €", moneda="EUR",
        enlace="https://tienda.example/tinta-blanca/", idioma="es", sitio="mboprinters",
        visto_en=AHORA,
    ))
    s.add(MarcaCatalogo(nombre="mbo_dtf", logo="https://cdn.example/mbo-dtf.png",
                        color="#e30613", idioma_defecto="es",
                        urls_json=json.dumps({"es": "https://mboprinters.com/es/",
                                              "en": "https://mboprinters.com/en/"})))
    s.commit()


def _p(origen_id: str, fuente: str = FUENTE_COMPOSITOR) -> dict[str, str]:
    return {"fuente": fuente, "origen_id": origen_id}


def _un_bloque_de_cada() -> list[dict[str, Any]]:
    return [
        {"tipo": "texto",
         "html": "<p>Hola Marny,</p><p>te paso la <strong>gama DTF</strong>:</p>"
                 "<ul><li>Orion 20</li><li>Lyra 35</li></ul>"
                 "<p>Más en <a href='https://mboprinters.com/dtf/'>la web</a>.</p>"},
        {"tipo": "marca", "marca": "mbo_dtf", "texto": "Impresión DTF profesional"},
        {"tipo": "productos_fila", "productos": [_p("o20"), _p("l35"), _p("a35")]},
        {"tipo": "producto", "producto": _p("v60")},
        {"tipo": "video", "url": YOUTUBE, "titulo": "La Orion 20 en marcha"},
        {"tipo": "cta", "botones": [{"texto": "Ver toda la gama",
                                     "url": "https://mboprinters.com/dtf/"}]},
        {"tipo": "destacado", "titulo": "Formación incluida", "estilo": "promo",
         "texto": "Con cada máquina, una sesión de puesta en marcha."},
        {"tipo": "separador", "estilo": "linea"},
    ]


def _render(s: Session, bloques: list[dict[str, Any]], *, idioma: str = "es",
            preview: bool = False, firma: str | None = None) -> generador.Correo:
    estructura = bq.normalizar({"idioma": idioma, "marca": "mbo_dtf", "bloques": bloques})
    datos = datos_mod.resolver(s, estructura, ahora=AHORA, config=CONFIG)
    return generador.generar(estructura, datos, asunto="Gama DTF", preview=preview,
                             firma_html=firma)


def _comprobar_html_de_correo(html: str) -> None:
    bajo = html.lower()
    assert "<div" not in bajo, "un <div> de maquetación"
    for prohibido in ("<script", "<svg", "<link", "@import", "@font-face", "javascript:",
                      "display:flex", "display: flex", "display:grid", "position:absolute"):
        assert prohibido not in bajo, prohibido
    hojas = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert len(hojas) == 1 and hojas[0].strip().startswith("@media only screen and (max-width: ")
    assert hojas[0].count("{") == hojas[0].count("}") and hojas[0].strip().endswith("}")
    for m in re.finditer(r"<(table|td|p|a|img|ul|ol|li)\b([^>]*)>", html):
        assert 'style="' in m.group(2), f"sin estilo en línea: {m.group(0)[:120]}"
    for m in re.finditer(r"<img\b([^>]*)>", html):
        atributos = m.group(1)
        assert re.search(r'\salt="[^"]+"', atributos), m.group(0)
        assert re.search(r'\swidth="\d+"', atributos), m.group(0)
        assert re.search(r'\ssrc="https://', atributos), m.group(0)
    for href in re.findall(r'href="([^"]*)"', html):
        assert href.startswith(("https://", "http://", "mailto:")), href
    assert re.search(r'<table(?=[^>]*class="bh-wrap")(?=[^>]*width="600")[^>]*>', html)
    assert "max-width:600px" in html


def test_cada_bloque_sale_en_tablas_con_estilos_en_linea_y_sin_un_div(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _catalogo(s)
        todos = _un_bloque_de_cada()
        assert {b["tipo"] for b in todos} == set(bq.TIPOS)
        # Un correo de cada tipo de bloque, y uno con todos.
        for bloque in todos:
            correo = _render(s, [bloque])
            _comprobar_html_de_correo(correo.html)
            assert correo.texto.strip() or bloque["tipo"] == "separador", bloque["tipo"]
        completo = _render(s, todos)
        _comprobar_html_de_correo(completo.html)
        assert completo.avisos == []
        assert completo.html.startswith("<!DOCTYPE html>")
        assert "<title>Gama DTF</title>" in completo.html


def test_tres_fichas_en_fila_se_apilan_en_una_columna_por_debajo_de_480_px(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _catalogo(s)
        html = _render(s, [{"tipo": "productos_fila",
                            "productos": [_p("o20"), _p("l35"), _p("v60")]}]).html
    # Las tres fichas son celdas de una misma fila (compactas, como hoy los
    # tríos), con la clase que la consulta de medio convierte en bloque a todo
    # el ancho; el corte, en 600 px, cubre todo lo que baja de 480.
    assert len(re.findall(r'<td class="bh-col bh-ficha" width="\d+" valign="top"', html)) == 3
    assert html.count(">Info →</a>") == 3
    hoja = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL).group(1)
    corte = int(re.search(r"@media only screen and \(max-width: (\d+)px\)", hoja).group(1))
    assert corte >= 480
    regla = re.search(r"\.bh-col \{([^}]*)\}", hoja).group(1)
    assert "display: block !important" in regla and "width: 100% !important" in regla
    assert re.search(r"\.bh-img \{[^}]*width: 100% !important", hoja)
    # Una fila de cuatro se parte: tres en fila y la cuarta como ficha sola.
    partida = bq.normalizar({"bloques": [{"tipo": "productos_fila", "productos": [
        _p("a"), _p("b"), _p("c"), _p("d")]}]})["bloques"]
    assert [b["tipo"] for b in partida] == ["productos_fila", "producto"]
    assert len({b["id"] for b in partida}) == 2


def test_cada_correo_compuesto_trae_su_texto_plano(session_factory: sessionmaker) -> None:
    with session_factory() as s:
        _catalogo(s)
        correo = _render(s, _un_bloque_de_cada(), firma="<p>Manel Puig · Bomedia</p>")
    texto = correo.texto
    assert "<" not in texto and ">" not in texto
    for esperado in ("Hola Marny,", "gama DTF", "- Orion 20", "la web (https://mboprinters.com/dtf/)",
                     "Orion 20", "✓ 20 cm", "4.990 € + IVA",
                     "Info: https://mboprinters.com/es/dtf/o20/", "Precio a medida",
                     "desde 21.500 € + IVA", "Más info: https://mboprinters.com/es/dtf/v60/",
                     f"▶ Ver vídeo: {YOUTUBE}", "Ver toda la gama: https://mboprinters.com/dtf/",
                     "Formación incluida", "Precios sin IVA.", "-- \nManel Puig · Bomedia"):
        assert esperado in texto, esperado
    # La firma va entre las marcas del compositor: lo enviado se compara sin ella.
    firma = f"{generador.FIRMA_INICIO}<p>Manel Puig · Bomedia</p>{generador.FIRMA_FIN}"
    assert firma in correo.html


def test_una_ficha_de_maquina_va_en_el_idioma_del_cliente_con_el_enlace_de_ese_idioma(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _catalogo(s)
        en = _render(s, [{"tipo": "producto", "producto": _p("o20")}], idioma="en")
        es = _render(s, [{"tipo": "producto", "producto": _p("o20")}], idioma="es")
    ficha = en.html
    assert 'src="https://cdn.example/o20-en.jpg"' in ficha and 'alt="Orion 20"' in ficha
    assert re.search(r">Area</td><td[^>]*>A3</td>", ficha)            # el área
    assert "✓ 20 cm wide" in ficha and "✓ i3200 head" in ficha        # dos características
    assert en.precios == ["4,990 €"]                                  # el precio tal cual
    assert re.search(r"4,990 € <span[^>]*>\+ VAT</span>", ficha)       # con su IVA, en inglés
    assert ">More info →</a>" in ficha                                # el botón, en inglés
    assert 'href="https://mboprinters.com/en/dtf/o20/"' in ficha      # el enlace en inglés
    assert "mboprinters.com/es/" not in ficha
    assert ">Más info →</a>" in es.html and 'href="https://mboprinters.com/es/dtf/o20/"' in es.html
    assert re.search(r">Área</td><td[^>]*>A3</td>", es.html) and "4.990 €" in es.html


def test_un_producto_sin_precio_publico_sale_sin_cifra_y_a_medida(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _catalogo(s)
        correo = _render(s, [{"tipo": "producto", "producto": _p("a35")}])
    assert re.search(r">Precio a medida</p>", correo.html)        # en el sitio del precio
    assert correo.precios == []
    assert not re.search(r"\d[\d.,]*\s*€", correo.html)              # ninguna cifra
    assert "Consultar" not in correo.html
    assert "Precios sin IVA" not in correo.html                       # sin precio, sin avisos
    assert "Precio a medida" in correo.texto


def test_la_vista_previa_es_el_correo_mas_los_atributos_para_editar(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _catalogo(s)
        bloques = _un_bloque_de_cada()
        final = _render(s, bloques)
        estructura = bq.normalizar({"idioma": "es", "marca": "mbo_dtf", "bloques": bloques})
        datos = datos_mod.resolver(s, estructura, ahora=AHORA, config=CONFIG)
        vista = generador.generar(estructura, datos, asunto="Gama DTF", preview=True)
        sin_vista = generador.generar(estructura, datos, asunto="Gama DTF")
    ids = [b["id"] for b in estructura["bloques"]]
    assert re.findall(r'data-bh-bloque="([^"]+)"', vista.html) == ids
    campos = set(re.findall(r'data-bh-campo="([^"]+)"', vista.html))
    texto, marca, fila, _, video, cta, destacado, _ = ids
    assert {f"{texto}:html", f"{marca}:texto", f"{fila}:boton", f"{video}:titulo",
            f"{cta}:botones.0.texto", f"{destacado}:titulo", f"{destacado}:texto", ":pie"} <= campos
    assert "data-bh-" not in final.html
    # Quitando los atributos, la vista previa es exactamente el correo que se envía.
    assert re.sub(r' data-bh-[a-z]+="[^"]*"', "", vista.html) == sin_vista.html


def test_normalizar_limpia_lo_que_llegue_y_el_markdown_de_la_ia_se_vuelve_html() -> None:
    avisos: list[str] = []
    e = bq.normalizar({"idioma": "EN", "bloques": [
        {"tipo": "hero", "titulo": "aún no"},
        {"id": "x1", "tipo": "texto", "html": "<div>Hola <b>Marny</b></div><div>dos</div>"
                                             "<script>alert(1)</script>"
                                             "<p><a href='javascript:alert(1)'>pulsa</a></p>"},
        {"id": "x1", "tipo": "video", "url": "https://vimeo.com/123"},
        {"tipo": "cta", "botones": [{"texto": "Mal", "url": "/relativa"},
                                    {"texto": "Bien", "url": "https://mboprinters.com/"}]},
        {"tipo": "destacado", "texto": "<b>Ojo</b> con   esto", "estilo": "raro"},
        {"tipo": "productos_fila", "productos": [_p("solo")]},
    ]}, avisos)
    assert e["idioma"] == "en"
    tipos = [b["tipo"] for b in e["bloques"]]
    assert tipos == ["texto", "cta", "destacado", "producto"]
    assert any("hero" in a for a in avisos) and any("vídeo" in a for a in avisos)
    texto = e["bloques"][0]["html"]
    assert texto == "<p>Hola <strong>Marny</strong></p><p>dos</p>alert(1)<p>pulsa</p>" or (
        "<script" not in texto and "javascript" not in texto and "<strong>Marny</strong>" in texto)
    assert e["bloques"][1]["botones"] == [{"texto": "Bien", "url": "https://mboprinters.com/"}]
    assert e["bloques"][2] == {"id": e["bloques"][2]["id"], "tipo": "destacado",
                               "estilo": "aviso", "texto": "Ojo con esto"}
    assert len({b["id"] for b in e["bloques"]}) == len(e["bloques"])
    # El markdown sencillo de la IA: párrafos, listas, negrita, cursiva y enlaces;
    # lo demás se escapa (la IA no mete HTML).
    html = bq.markdown_a_html(
        "Hola **Marny**,\n\nte paso *dos* opciones:\n\n- [Orion 20](https://mbo.example/o20)\n"
        "- Lyra 35\n\n1. uno\n2. dos\n\n<table>no</table>")
    assert html == ("<p>Hola <strong>Marny</strong>,</p><p>te paso <em>dos</em> opciones:</p>"
                    '<ul><li><a href="https://mbo.example/o20">Orion 20</a></li>'
                    "<li>Lyra 35</li></ul>"
                    "<ol><li>uno</li><li>dos</li></ol><p>&lt;table&gt;no&lt;/table&gt;</p>")
    assert bq.video_youtube("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ"
    assert bq.video_youtube("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert bq.video_youtube("https://vimeo.com/1") is None


def test_la_ruta_de_render_limpia_genera_y_no_envia(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        _catalogo(s)
    cuerpo = {"estructura": {"idioma": "es", "bloques": [
        {"tipo": "hero"}, {"tipo": "texto", "html": "<p>Hola</p><script>x()</script>"},
        {"tipo": "producto", "producto": _p("o20")},
        {"tipo": "producto", "producto": _p("ya-no-esta")}]},
        "asunto": "Gama DTF", "preview": True, "firma_html": "<p>Admin</p>"}
    assert http.post("/api/emails/compuesto/render", json=cuerpo).status_code == 401
    r = http.post("/api/emails/compuesto/render", json=cuerpo, headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert [b["tipo"] for b in d["estructura"]["bloques"]] == ["texto", "producto", "producto"]
    assert "<script" not in d["html"] and "data-bh-bloque" in d["html"]
    assert d["precios"] == ["4.990 €"] and "Orion 20" in d["texto"]
    assert any("hero" in a for a in d["avisos"]) and any("ya-no-esta" in a for a in d["avisos"])
    sin_vista = http.post("/api/emails/compuesto/render", json={**cuerpo, "preview": False},
                          headers=auth_headers(http, "admin")).json()
    assert "data-bh-" not in sin_vista["html"]
    grande = {"estructura": {"bloques": [{"tipo": "texto", "html": "x" * 250_000}]}}
    assert http.post("/api/emails/compuesto/render", json=grande,
                     headers=auth_headers(http, "admin")).status_code == 413


def test_el_catalogo_guarda_el_diseno_del_compositor_y_la_tira_de_marca_lo_usa(
    session_factory: sessionmaker,
) -> None:
    """La insignia y los colores de cada ficha, y la etiqueta, el texto del
    enlace, las medidas y el fondo del logo de cada marca: lo que el
    compositor ya guarda para pintar los correos de hoy (0143)."""
    from app.services.conocimiento import catalogo_compositor  # noqa: PLC0415
    from app.services.web_forms.sitios import (  # noqa: PLC0415
        SITIOS_DE_PARTIDA,
        Registro,
        fijar_para_tests,
    )

    data = {
        "products": [{"id": "o20", "name": "Orion 20", "brand": "mbo_dtf", "price": "4.990 €",
                      "area": "A3", "alt": "10 cm", "badge": "Nuevo", "accent": "#e30613",
                      "badgeBg": "#fee2e2", "badgeColor": "#b91c1c",
                      "link": "https://mboprinters.com/es/dtf/o20/",
                      "i18n": {"en": {"price": "4,990 €", "badge": "New",
                                      "link": "https://mboprinters.com/en/dtf/o20/"}}}],
        "brands": [{"id": "mbo_dtf", "label": "MBO DTF", "logo": "https://cdn.example/mbo.png",
                    "color": "#e30613", "url": {"es": "https://mboprinters.com/es/"},
                    "urlLabel": {"es": "mboprinters.com →", "en": "mboprinters.com/en →"},
                    "logoHeight": "32", "logoMaxWidth": 200, "logoBg": "#0f172a",
                    "divider": "#fecaca"}],
    }
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    try:
        filas = catalogo_compositor.filas_de(data, actualizado_origen=AHORA)
    finally:
        fijar_para_tests(None)
    disenos = {f["idioma"]: json.loads(f["datos_json"]) for f in filas}
    assert disenos["es"]["badge"] == "Nuevo" and disenos["en"]["badge"] == "New"
    assert disenos["es"]["acento"] == "#e30613" and disenos["en"]["badge_fondo"] == "#fee2e2"
    assert disenos["es"]["alt"] == "10 cm"
    with session_factory() as s:
        catalogo_compositor.escribir_marcas(s, data, ahora=AHORA)
        _maquina(s, "o20", "es", "Orion 20", precio="4.990 €")
        s.flush()
        fila = s.query(ProductoCatalogo).filter_by(origen_id="o20").one()
        fila.datos_json = json.dumps(disenos["es"])
        s.commit()
        correo = _render(s, [{"tipo": "marca", "marca": "mbo_dtf"},
                             {"tipo": "producto", "producto": _p("o20")}])
    html = correo.html
    assert 'alt="MBO DTF"' in html and "max-height:32px" in html and "max-width:200px" in html
    assert 'bgcolor="#0f172a"' in html and ">mboprinters.com →</a>" in html
    assert re.search(r"background-color:#fee2e2;[^>]*color:#b91c1c;\">Nuevo</td>", html)
    assert re.search(r">Alt\. máx\.</td><td[^>]*>10 cm</td>", html)
    assert re.search(r"color:#e30613;margin:10px 0 0;text-align:center;\">4\.990 €", html)
