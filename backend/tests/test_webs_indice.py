"""El índice de nuestras webs: lo que entra (y lo que no), un idioma por fila,
las pasadas (una web por trabajo), el patrón visto / ido y el tic mensual.

Nada sale a la red: las webs son WordPress simulados (`test_webs_comun`) por
`httpx.MockTransport`, con el resolutor de nombres falso.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra todos los modelos
from app.db.base import Base
from app.models.fuentes_externas import (
    IVA_DESCONOCIDO,
    IVA_SIN,
    PASADA_EN_COLA,
    PASADA_ERROR,
    PASADA_OK,
    PASADA_PARCIAL,
    REGLA_CATEGORIA_FUERA,
    REGLA_PAGINA_ID,
    REGLA_PAGINA_SLUG,
    REGLA_PRODUCTO_EXCLUIR,
    REGLA_PRODUCTO_INCLUIR,
    WEB_ENTRADA,
    WEB_PAGINA,
    WEB_PRODUCTO,
    WebIndiceEntrada,
    WebIndicePasada,
    WebIndiceRegla,
)
from app.services.conocimiento import config as config_kb
from app.services.externo.html_texto import plano
from app.services.web_forms import sitios as registro_sitios
from app.services.webs import busqueda, job, lector, pasadas
from app.services.webs import reglas as reglas_mod
from app.services.webs.reglas_partida import CATEGORIAS_FUERA
from tests.test_webs_comun import (
    AJ_HOST,
    FLUX_HOST,
    MBO_HOST,
    MBOL_HOST,
    PIMPAM_HOST,
    Internet,
    registro_con,
    web_artisjet_eu,
    web_flux,
    web_mbolasers,
    web_mboprinters,
    web_pimpam,
)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def internet(monkeypatch: pytest.MonkeyPatch) -> Internet:
    """Las webs simuladas, y sin Redis: la pasada se recorre en línea."""
    red = Internet()
    monkeypatch.setattr(pasadas, "nuevo_http", red.fabrica())

    def _sin_redis(_pasada_id: str) -> None:
        raise ConnectionError("sin Redis en los tests")

    monkeypatch.setattr(pasadas, "_encolar", _sin_redis)
    return red


@pytest.fixture()
def usar(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Las webs que hay en la tabla de sitios (activas, tipo web)."""
    def _usar(*claves: str) -> None:
        registro = registro_con(*claves)
        monkeypatch.setattr(registro_sitios, "cargar", lambda _session: registro)
    return _usar


def indexar(factory: sessionmaker) -> dict[str, Any]:
    with factory() as s:
        return pasadas.pasada_como_dict(pasadas.encolar_pasada(s, "manual", None)) or {}


def filas(s: Session, *, vivas: bool = True, **filtros: Any) -> list[WebIndiceEntrada]:
    consulta = select(WebIndiceEntrada).filter_by(**filtros)
    if vivas:
        consulta = consulta.where(WebIndiceEntrada.ido_en.is_(None))
    return list(s.scalars(consulta.order_by(WebIndiceEntrada.wp_id, WebIndiceEntrada.idioma)))


# --- qué entra y qué no ------------------------------------------------------------------------

#: Ninguno de estos puede estar en el índice: repuestos, recambios, pièces de
#: rechange, consumibles, tintas, cartuchos, servicios, reacondicionadas y sin
#: categoría.
PRODUCTOS_FUERA = (
    "Tubo láser Beambox 40W", "Espejo Hexa", "Lente de repuesto", "Panal de abeja consumible",
    "Cartucho de tinta Ador negro", "Beambox Open Box", "Servicio de instalación",
    "Servicio técnico (TSM)", "Capping Artisjet Freebird",
    "Pompe péristaltique pour Artis Young", "Carte mère Trust", "Encre UV LED cyan 1L",
    "Services installation", "Plaque de tête d'impression MBO 3020",
)


def test_ni_un_repuesto_ni_consumible_y_si_los_accesorios_por_modelo(
        factory: sessionmaker, internet: Internet, usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_flux(), web_artisjet_eu())
    pasada = indexar(factory)
    assert pasada["estado"] == PASADA_OK, pasada
    with factory() as s:
        productos = filas(s, tipo=WEB_PRODUCTO)
        titulos = {f.titulo for f in productos}
        for fuera in PRODUCTOS_FUERA:
            assert fuera not in titulos, f"{fuera} no debería estar en el índice"
        # Ni una categoría de fuera en lo indexado (ni madre ni hija).
        prefijos = [plano(p) for p, _ in CATEGORIAS_FUERA]
        for fila in productos:
            for cat in busqueda._lista(fila.categorias_json):  # noqa: SLF001
                assert not any(plano(cat).startswith(p) for p in prefijos), (fila.titulo, cat)
        # Dentro: máquinas y accesorios, también los de cada modelo, y el
        # accesorio colgado solo de la madre «Accessoires et pièces de rechange».
        for dentro in ("Beambox II 55W", "Flux Hexa 60W", "Módulo rotatorio 2.0 Beambox",
                       "Modulo rotatorio para Flux Hexa", "Chuck Rotary Add-on Beamo",
                       "artisJet DIVE", "Ink Bottle System with Shelf"):
            assert dentro in titulos, f"falta {dentro}"
        hexa = next(f for f in productos if f.wp_id == 2350)
        assert "Accesorios Hexa" in busqueda._lista(hexa.categorias_json)  # noqa: SLF001
        assert hexa.imagen and hexa.imagen.endswith("-0.jpg")
        assert busqueda._lista(hexa.galeria_json) == [  # noqa: SLF001
            f"https://{FLUX_HOST}/wp-content/uploads/modulo-rotatorio-para-flux-hexa-1.jpg"]


def test_flux_se_lee_de_fluxlasers_eu_y_no_de_es(factory: sessionmaker,
                                                  internet: Internet, usar) -> None:
    usar("fluxlasers")
    web = web_flux()
    internet.poner(web)
    indexar(factory)
    assert web.peticiones and all(p.startswith(f"https://{FLUX_HOST}/") for p in web.peticiones)
    with factory() as s:
        assert {f.dominio for f in filas(s)} == {FLUX_HOST}
        # fluxlasers no publica más idiomas que el español: todo lo demás era
        # respaldo y no se indexa.
        assert {f.idioma for f in filas(s)} == {"es"}


def test_ninguna_pagina_legal_ni_de_carrito(factory: sessionmaker, internet: Internet,
                                            usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_flux(), web_artisjet_eu())
    indexar(factory)
    with factory() as s:
        slugs = {f.slug for f in filas(s) if f.tipo != WEB_PRODUCTO}
    assert slugs == {"beambox-ii", "hexa", "events"}
    for fuera in ("aviso-legal", "carrito", "politica-privacidad", "mentions-legales",
                  "cookie-policy", "basket", "contacto"):
        assert fuera not in slugs


def test_artisjet_eu_una_fila_por_idioma_sin_mezclar_y_sin_respaldo(
        factory: sessionmaker, internet: Internet, usar) -> None:
    usar("artisjet-eu")
    internet.poner(web_artisjet_eu())
    indexar(factory)
    with factory() as s:
        events = {f.idioma: f for f in filas(s, wp_id=9011)}
        # pt no está traducida: WPGlobus sirve el francés con la URL /pt/.
        assert set(events) == {"fr", "de", "en"}
        assert "Retrouvez" in events["fr"].texto and "Treffen" not in events["fr"].texto
        assert "Treffen" in events["de"].texto and "Retrouvez" not in events["de"].texto
        assert "Meet artisJet" in events["en"].texto and "Messen" not in events["en"].texto
        assert "/en/events/" in events["en"].url and events["en"].titulo == "Events"
        dive = {f.idioma: f for f in filas(s, wp_id=9741)}
        assert set(dive) == {"fr", "de", "en"}       # el pt es el texto francés: fuera
        assert "Drucker" in dive["de"].texto and "imprimante" not in dive["de"].texto.lower()
        # Sin traducir en ningún idioma: solo la fila francesa.
        assert {f.idioma for f in filas(s, wp_id=3850)} == {"fr"}


def test_mboprinters_se_pide_con_rest_route_y_ampersand(factory: sessionmaker,
                                                       internet: Internet, usar) -> None:
    usar("mboprinters")
    web = web_mboprinters()
    internet.poner(web)
    indexar(factory)
    rest = [p for p in web.peticiones if "rest_route" in p]
    assert rest
    for url in rest:
        assert url.count("?") == 1, url                     # un segundo «?» da vacío
        assert url.startswith((f"https://{MBO_HOST}/?rest_route=/",
                               f"https://{MBO_HOST}/en/?rest_route=/",
                               f"https://{MBO_HOST}/de/?rest_route=/",
                               f"https://{MBO_HOST}/fr/?rest_route=/")), url
    assert any(u.startswith(f"https://{MBO_HOST}/?rest_route=/wp/v2/pages&per_page=100&page=1"
                            "&_fields=") for u in rest)
    assert any(u.startswith(f"https://{MBO_HOST}/en/?rest_route=/wp/v2/pages&") for u in rest)
    with factory() as s:
        pagina_es = filas(s, wp_id=3004, idioma="es")[0]
        # WPBakery y el CSS del carrusel fuera; el texto se queda.
        for basura in ("[vc_", "{color", "wpcpro", "font-size"):
            assert basura not in pagina_es.texto
        assert "Impresora MBO 6090 UV Led de sobremesa" in pagina_es.texto
        productos = filas(s, tipo=WEB_PRODUCTO)
        assert {f.idioma for f in productos} == {"es", "en", "de", "fr"}
        assert all(f.precio_web is None and f.precio_iva is None for f in productos)


# --- precios -----------------------------------------------------------------------------------


def test_el_precio_de_flux_es_precio_web_sin_iva_y_la_busqueda_lo_marca(
        factory: sessionmaker, internet: Internet, usar) -> None:
    usar("fluxlasers")
    internet.poner(web_flux())
    indexar(factory)
    with factory() as s:
        beambox = filas(s, wp_id=3949)[0]
        assert beambox.precio_web == Decimal("3049.59")
        assert beambox.precio_iva == IVA_SIN and beambox.moneda == "EUR"
        assert beambox.precio_web_texto == "3,049.59€ (+ IVA)"
        # Rebaja: lo que ve el cliente, sin el tachado ni el texto para lectores.
        hexa = filas(s, wp_id=2350)[0]
        assert hexa.precio_web == Decimal("136.50") and hexa.precio_web_texto == "136.50€ (+ IVA)"
        # A 0 («a consultar»): no se guarda.
        beamo = filas(s, wp_id=4318)[0]
        assert beamo.precio_web is None and beamo.precio_web_texto is None
        assert beamo.precio_iva is None
        hallado = busqueda.buscar(s, "Beambox II", tipo=WEB_PRODUCTO)
    assert hallado and hallado[0].titulo == "Beambox II 55W"
    assert hallado[0].precio_web == pytest.approx(3049.59)
    assert hallado[0].precio_iva == IVA_SIN and hallado[0].precio_web_texto == "3,049.59€ (+ IVA)"


@pytest.mark.parametrize(("texto", "iva"), [
    ("3,049.59€ (+ IVA)", "sin_iva"), ("525,00 € + IVA", "sin_iva"), ("100 €+IVA", "sin_iva"),
    ("890,00 € zzgl. MwSt", "sin_iva"), ("€ 95,00 excl. BTW", "sin_iva"),
    ("120,00 € HT", "sin_iva"), ("99 € excl. VAT", "sin_iva"),
    ("1.200 € IVA incluido", "con_iva"), ("50 € inkl. MwSt.", "con_iva"),
    ("80 € TTC", "con_iva"), ("€95,00", "desconocido"), ("8,00€ IVA", "desconocido"),
])
def test_el_iva_es_lo_que_dice_la_web(texto: str, iva: str) -> None:
    assert lector.iva_de(texto) == iva


def test_precio_a_cero_o_sin_precio_no_se_guarda() -> None:
    assert lector.precio_de({"prices": {"price": "0"}})["precio_web"] is None
    assert lector.precio_de({"prices": {}})["precio_web"] is None
    sin_sufijo = lector.precio_de({"prices": {"price": "9500", "currency_minor_unit": 2,
                                              "currency_code": "EUR"},
                                   "price_html": "&euro;95,00"})
    assert sin_sufijo["precio_web"] == Decimal("95.00")
    assert sin_sufijo["precio_iva"] == IVA_DESCONOCIDO


def test_url_rest_siempre_con_rest_route_y_ampersand() -> None:
    url = lector.url_rest("https://mboprinters.com/en/", "/wp/v2/pages",
                          {"per_page": 100, "page": 2, "_fields": "id,slug", "lang": None})
    assert url == ("https://mboprinters.com/en/?rest_route=/wp/v2/pages&per_page=100&page=2"
                   "&_fields=id,slug")


# --- idiomas WPML y el HTML de mbolasers ----------------------------------------------------------


def test_wpml_descarta_el_respaldo_y_encuentra_pt_pt(factory: sessionmaker,
                                                     internet: Internet, usar) -> None:
    usar("mbolasers", "pimpam")
    mbol, pimpam = web_mbolasers(), web_pimpam()
    internet.poner(mbol, pimpam)
    pasada = indexar(factory)
    assert pasada["resumen"]["pimpam"]["estado"] == PASADA_OK
    with factory() as s:
        de = filas(s, sitio="mbolasers", tipo=WEB_PRODUCTO, idioma="de")
        # El CNC español servido tal cual en «de» (mismo id, enlace sin /de/): fuera.
        assert [f.wp_id for f in de] == [2821]
        assert de[0].precio_iva == IVA_SIN                  # «zzgl. MwSt»
        # fr y nl no están publicados: todo era el español.
        assert not filas(s, sitio="mbolasers", idioma="fr")
        assert not filas(s, sitio="mbolasers", idioma="nl")
        # pimpam publica el portugués como pt-pt: se guarda como «pt».
        pt = filas(s, sitio="pimpam", idioma="pt")
        assert [f.wp_id for f in pt] == [616] and "/pt-pt/" in pt[0].url
        assert not filas(s, sitio="pimpam", tipo=WEB_PRODUCTO)      # sin tienda
    assert any("lang=pt-pt" in p for p in pimpam.peticiones)


def test_mbolasers_sus_paginas_se_leen_del_html_publico(factory: sessionmaker,
                                                         internet: Internet, usar) -> None:
    usar("mbolasers")
    web = web_mbolasers()
    internet.poner(web)
    indexar(factory)
    # La REST da 500 con el contenido: la lista se pide sin él y cada página
    # se lee de su HTML.
    assert any("_fields=id,slug,link,modified_gmt,title,excerpt" in p for p in web.peticiones)
    assert f"https://{MBOL_HOST}/fiber-lasers/" in web.peticiones
    with factory() as s:
        es = filas(s, sitio="mbolasers", tipo=WEB_PAGINA, idioma="es")
        assert [f.wp_id for f in es] == [1265]
        assert "Láser de fibra MBO" in es[0].texto
        for fuera in ("Llámanos", "© MBO", "Inicio Tienda Contacto", "var a"):
            assert fuera not in es[0].texto            # sin cabecera, pie ni scripts
        assert es[0].titulo == "MBO Fiber Lasers"
        assert [f.wp_id for f in filas(s, tipo=WEB_PAGINA, idioma="en")] == [2373]
        # El HTML de la página «de» dice <html lang="es-ES">: respaldo, fuera.
        assert not filas(s, tipo=WEB_PAGINA, idioma="de")
        # Las entradas y los productos sí van por la API.
        assert filas(s, tipo=WEB_PRODUCTO, idioma="es")


# --- visto / ido ---------------------------------------------------------------------------------


def test_reindexar_dos_veces_no_duplica(factory: sessionmaker, internet: Internet, usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_flux(), web_artisjet_eu())
    primera = indexar(factory)
    with factory() as s:
        antes = len(filas(s, vivas=False))
    segunda = indexar(factory)
    with factory() as s:
        assert len(filas(s, vivas=False)) == antes == len(filas(s))
    assert primera["resumen"]["fluxlasers"]["nuevos"] > 0
    for clave in ("fluxlasers", "artisjet-eu"):
        r = segunda["resumen"][clave]
        assert (r["nuevos"], r["actualizados"], r["idos"]) == (0, 0, 0), (clave, r)


def test_un_producto_retirado_deja_de_salir_y_queda_ido_sin_perder_la_fila(
        factory: sessionmaker, internet: Internet, usar) -> None:
    usar("fluxlasers")
    web = web_flux()
    internet.poner(web)
    indexar(factory)
    with factory() as s:
        assert [f.titulo for f in busqueda.buscar(s, "rotatorio Hexa")] == [
            "Modulo rotatorio para Flux Hexa"]
    retirado = next(p for p in web.productos["es"] if p["id"] == 2350)
    web.productos["es"].remove(retirado)
    pasada = indexar(factory)
    assert pasada["resumen"]["fluxlasers"]["idos"] == 1
    with factory() as s:
        fila = filas(s, vivas=False, wp_id=2350)[0]
        # Ni con todas las palabras ni en la segunda vuelta (alguna palabra).
        assert fila.id not in {f.id for f in busqueda.buscar(s, "rotatorio Hexa")}
        assert busqueda.fragmentos_de(s, [fila.id]) == []
        assert fila.ido_en is not None and fila.titulo == "Modulo rotatorio para Flux Hexa"
    # Si vuelve a la web, vuelve al índice (la misma fila).
    web.productos["es"].append(retirado)
    pasada = indexar(factory)
    assert pasada["resumen"]["fluxlasers"]["actualizados"] == 1
    with factory() as s:
        assert [f.ido_en for f in filas(s, vivas=False, wp_id=2350)] == [None]


def test_una_web_o_un_idioma_que_falla_no_marca_nada_ido(factory: sessionmaker,
                                                         internet: Internet, usar) -> None:
    usar("artisjet-eu", "fluxlasers")
    aj, flux = web_artisjet_eu(), web_flux()
    internet.poner(aj, flux)
    indexar(factory)
    with factory() as s:
        de_antes = len(filas(s, sitio="artisjet-eu", idioma="de"))
        flux_antes = len(filas(s, sitio="fluxlasers"))
    assert de_antes > 0
    aj.caidos = {"de"}
    del internet.webs[FLUX_HOST]                    # fluxlasers.eu no responde
    pasada = indexar(factory)
    assert pasada["estado"] == PASADA_PARCIAL
    assert pasada["resumen"]["artisjet-eu"]["estado"] == PASADA_PARCIAL
    assert "de" in pasada["resumen"]["artisjet-eu"]["error"]
    assert pasada["resumen"]["fluxlasers"]["estado"] == PASADA_ERROR
    with factory() as s:
        assert len(filas(s, sitio="artisjet-eu", idioma="de")) == de_antes
        assert len(filas(s, sitio="fluxlasers")) == flux_antes


def test_cero_productos_con_200_no_vacia_el_indice(factory: sessionmaker,
                                                   internet: Internet, usar) -> None:
    usar("fluxlasers")
    web = web_flux()
    internet.poner(web)
    indexar(factory)
    with factory() as s:
        antes = len(filas(s, tipo=WEB_PRODUCTO))
    web.productos["es"] = []
    indexar(factory)
    with factory() as s:
        assert len(filas(s, tipo=WEB_PRODUCTO)) == antes > 0


def test_una_web_sin_tienda_se_queda_sin_productos(factory: sessionmaker,
                                                   internet: Internet, usar) -> None:
    usar("fluxlasers")
    web = web_flux()
    internet.poner(web)
    indexar(factory)
    web.sin_tienda = True                               # quitaron WooCommerce
    pasada = indexar(factory)
    assert pasada["estado"] == PASADA_OK
    with factory() as s:
        assert not filas(s, tipo=WEB_PRODUCTO)
        assert filas(s, tipo=WEB_PAGINA)                # las páginas siguen


def test_una_web_que_da_404_a_todo_no_se_queda_sin_productos(factory: sessionmaker,
                                                             internet: Internet, usar) -> None:
    """Un 404 de la Store API solo es «sin tienda» si en esa misma lectura la
    REST de la web respondió bien. Si da 404 a todo (REST rota, vhost mal
    apuntado), la lectura ha fallado: no se marca nada ido."""
    usar("fluxlasers")
    web = web_flux()
    internet.poner(web)
    indexar(factory)
    with factory() as s:
        antes = {(f.tipo, f.wp_id, f.idioma) for f in filas(s)}
    assert {t for t, _, _ in antes} == {WEB_PAGINA, WEB_ENTRADA, WEB_PRODUCTO}
    web.rota = True
    pasada = indexar(factory)
    assert pasada["resumen"]["fluxlasers"]["estado"] == PASADA_ERROR
    assert pasada["resumen"]["fluxlasers"]["idos"] == 0
    with factory() as s:
        assert {(f.tipo, f.wp_id, f.idioma) for f in filas(s)} == antes
        assert all(f.ido_en is None for f in filas(s, vivas=False))


def test_una_web_desactivada_deja_lo_suyo_ido(factory: sessionmaker,
                                              internet: Internet, usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_flux(), web_artisjet_eu())
    indexar(factory)
    usar("artisjet-eu")                                 # fluxlasers fuera de la lista
    indexar(factory)
    with factory() as s:
        assert not filas(s, sitio="fluxlasers")
        assert filas(s, sitio="fluxlasers", vivas=False)
        assert filas(s, sitio="artisjet-eu")


# --- las reglas ----------------------------------------------------------------------------------


def test_las_reglas_editadas_cambian_lo_que_entra(factory: sessionmaker,
                                                  internet: Internet, usar) -> None:
    usar("fluxlasers")
    internet.poner(web_flux())
    indexar(factory)
    with factory() as s:
        assert filas(s, wp_id=2350) and filas(s, wp_id=3950)
        assert not filas(s, wp_id=5001)
        reglas_mod.asegurar_sembradas(s)
        s.add_all([
            WebIndiceRegla(tipo=REGLA_CATEGORIA_FUERA, patron="Accesorios Hexa", activo=True,
                           posicion=50),
            WebIndiceRegla(tipo=REGLA_PRODUCTO_INCLUIR, patron="5001", sitio="fluxlasers",
                           activo=True, posicion=0),
            WebIndiceRegla(tipo=REGLA_PAGINA_SLUG, patron=r"^beambox", activo=True,
                           posicion=50),
        ])
        s.commit()
    indexar(factory)
    with factory() as s:
        assert not filas(s, wp_id=2350)                 # Accesorios Hexa, ahora fuera
        assert filas(s, wp_id=5001)                     # excepción por id: dentro
        assert not filas(s, wp_id=3950)                 # la página beambox-ii, fuera
        assert filas(s, wp_id=3949)                     # lo demás sigue
        # Una regla desactivada deja de contar.
        hexa = s.scalars(select(WebIndiceRegla).where(
            WebIndiceRegla.patron == "Accesorios Hexa")).one()
        hexa.activo = False
        s.commit()
    indexar(factory)
    with factory() as s:
        assert filas(s, wp_id=2350)


def test_con_la_tabla_vacia_valen_las_de_partida(factory: sessionmaker) -> None:
    with factory() as s:
        reglas = reglas_mod.cargar(s)
        assert not reglas.de_la_base
        flux = reglas.para("fluxlasers")
        assert flux.categoria_fuera("Repuestos Beambox")
        assert flux.categoria_fuera("PIÈCES DE RECHANGE A5000")
        assert flux.categoria_fuera("Pieces de rechange")       # sin tildes también
        assert not flux.categoria_fuera("Accesorios Beambox")
        assert not flux.categoria_fuera("Accessoires et pièces de rechange")
        assert flux.pagina_fuera("politica-de-cookies", 1)
        assert flux.pagina_fuera("zur-kasse", 1) and flux.pagina_fuera("impressum", 1)
        assert not flux.pagina_fuera("beambox-ii", 1)
        assert reglas_mod.asegurar_sembradas(s)
        assert not reglas_mod.asegurar_sembradas(s)             # ya sembradas
        assert reglas_mod.cargar(s).de_la_base


@pytest.mark.parametrize(("slug", "fuera"), [
    ("testimonios", False), ("testimonials", False), ("testimonianze", False),
    ("testemunhos", False), ("temoignages", False),
    ("test", True), ("test-form", True), ("prueba-test", True), ("test-2", True),
    ("testagile", True),
])
def test_las_paginas_de_prueba_fuera_y_los_testimonios_dentro(slug: str, fuera: bool) -> None:
    reglas = reglas_mod.Reglas(reglas=reglas_mod._de_partida()).para("x")  # noqa: SLF001
    assert reglas.pagina_fuera(slug, 1) is fuera


def test_una_regla_por_id_sin_web_no_vale_para_ninguna(
        factory: sessionmaker, caplog: pytest.LogCaptureFixture) -> None:
    """Los ids de WordPress son de cada web: una regla por id sin web (metida a
    mano en la base: la API no la deja guardar) se salta, no vale para todas."""
    with factory() as s:
        reglas_mod.asegurar_sembradas(s)
        s.add_all([
            WebIndiceRegla(tipo=REGLA_PRODUCTO_INCLUIR, patron="5001", sitio=None, activo=True,
                           posicion=0),
            WebIndiceRegla(tipo=REGLA_PRODUCTO_EXCLUIR, patron="3949", sitio="", activo=True,
                           posicion=0),
            WebIndiceRegla(tipo=REGLA_PAGINA_ID, patron="3950", sitio=None, activo=True,
                           posicion=0),
            WebIndiceRegla(tipo=REGLA_PRODUCTO_INCLUIR, patron="77", sitio="boprint",
                           activo=True, posicion=0),
        ])
        s.commit()
        with caplog.at_level("WARNING", logger=reglas_mod.__name__):
            flux, boprint = reglas_mod.cargar(s).para("fluxlasers"), \
                reglas_mod.cargar(s).para("boprint")
    assert (flux.incluir, flux.excluir, flux.paginas_id) == (set(), set(), set())
    assert (boprint.incluir, boprint.excluir, boprint.paginas_id) == ({77}, set(), set())
    assert not flux.pagina_fuera("beambox-ii", 3950)
    assert lector.producto_dentro(
        {"id": 5001, "categories": [{"id": 21, "name": "Repuestos"}]}, flux, set()) == (
        False, "categoria")
    assert "sin web" in caplog.text


def test_categorias_fuera_heredan_a_las_hijas() -> None:
    from tests.test_webs_comun import AJ_CATEGORIAS, FLUX_CATEGORIAS

    reglas = reglas_mod.Reglas(reglas=reglas_mod._de_partida()).para("x")  # noqa: SLF001
    assert lector.categorias_fuera(FLUX_CATEGORIAS, reglas) == {
        21, 24, 231, 22, 15, 230, 227}
    assert lector.categorias_fuera(AJ_CATEGORIAS, reglas) == {51, 52, 130, 53, 5, 96}


def test_los_recambios_que_las_webs_cuelgan_de_accesorios_quedan_fuera() -> None:
    """Repasado sobre el recorrido real: artisjet-spain.es cuelga cabezales y
    dampers de «Accesorios» y artisjet-printers.eu consumibles de «Accessoires
    et pièces de rechange»; las excepciones de partida los sacan, cada una en
    su web, y el molde a medida de «Autres pièces de rechange» entra."""
    partida = reglas_mod.Reglas(reglas=reglas_mod._de_partida())  # noqa: SLF001
    es, eu, boprint = partida.para("artisjet-es"), partida.para("artisjet-eu"), \
        partida.para("boprint")
    accesorios = [{"id": 9, "name": "Accesorios"}]
    for pid in (470, 474, 481, 485, 2026051305):
        assert lector.producto_dentro({"id": pid, "categories": accesorios}, es, set()) == \
            (False, "excluido")
    assert lector.producto_dentro({"id": 477, "categories": accesorios}, es, set())[0]
    mezcla = [{"id": 7, "name": "Accessoires et pièces de rechange"}]
    for pid in (1892, 1989, 2979):
        assert not lector.producto_dentro({"id": pid, "categories": mezcla}, eu, set())[0]
    assert lector.producto_dentro({"id": 3850, "categories": mezcla}, eu, set())[0]
    autres = [{"id": 8, "name": "Autres pièces de rechange"}]
    assert lector.producto_dentro({"id": 477, "categories": autres}, eu, set()) == \
        (True, "incluido")
    assert not lector.producto_dentro({"id": 478, "categories": autres}, eu, set())[0]
    # Los ids son de cada web: el 470 de otra web no se toca.
    assert lector.producto_dentro({"id": 470, "categories": accesorios}, boprint, set())[0]


# --- las pasadas: una web por trabajo -----------------------------------------------------------


def test_la_pasada_va_web_a_web_encadenando_trabajos(
        factory: sessionmaker, internet: Internet, monkeypatch: pytest.MonkeyPatch, usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_flux(), web_artisjet_eu())
    encolados: list[str] = []
    monkeypatch.setattr(pasadas, "_encolar", encolados.append)
    monkeypatch.setattr("app.db.session.get_engine", lambda: factory.kw["bind"])
    with factory() as s:
        pasada = pasadas.encolar_pasada(s, "manual", "u-1")
        pid = pasada.id
        assert pasada.estado == PASADA_EN_COLA and pasada.lanzado_por == "u-1"
        assert pasadas.pasada_como_dict(pasada)["pendientes"] == ["artisjet-eu", "fluxlasers"]
        # Otra petición con una en cola: la misma, sin encolar nada.
        assert pasadas.encolar_pasada(s, "manual", "u-2").id == pid
    assert encolados == [pid]
    pasadas.trabajo_web(pid)                # la primera web, y encola la siguiente
    assert encolados == [pid, pid]
    with factory() as s:
        p = pasadas.pasada_como_dict(s.get(WebIndicePasada, pid))
        assert p["estado"] == "corriendo" and p["pendientes"] == ["fluxlasers"]
        assert set(p["resumen"]) == {"artisjet-eu"} and p["inicio"]
    pasadas.trabajo_web(pid)                # la última cierra
    assert encolados == [pid, pid]
    with factory() as s:
        p = pasadas.pasada_como_dict(s.get(WebIndicePasada, pid))
        assert p["estado"] == PASADA_OK and p["pendientes"] == [] and p["fin"]
        assert set(p["resumen"]) == {"artisjet-eu", "fluxlasers"}


def test_todas_las_webs_caidas_es_error(factory: sessionmaker, internet: Internet, usar) -> None:
    usar("fluxlasers", "artisjet-eu")
    pasada = indexar(factory)                           # ninguna web responde
    assert pasada["estado"] == PASADA_ERROR
    assert "fluxlasers" in pasada["error"] and "artisjet-eu" in pasada["error"]


def test_una_pasada_atascada_caduca(factory: sessionmaker,
                                    monkeypatch: pytest.MonkeyPatch, usar) -> None:
    usar("fluxlasers")
    monkeypatch.setattr(pasadas, "_encolar", lambda _pid: None)
    hace_3h = datetime.now(UTC) - timedelta(hours=3)
    with factory() as s:
        vieja = WebIndicePasada(origen="manual", estado="corriendo", pendientes_json="[]",
                                created_at=hace_3h, updated_at=hace_3h)
        s.add(vieja)
        s.commit()
        nueva = pasadas.encolar_pasada(s, "manual", None)
        assert nueva.id != vieja.id and nueva.estado == PASADA_EN_COLA
        s.refresh(vieja)
        assert vieja.estado == PASADA_ERROR and "2 horas" in vieja.error


def test_sin_redis_en_produccion_la_pasada_queda_en_error(
        factory: sessionmaker, internet: Internet, monkeypatch: pytest.MonkeyPatch, usar) -> None:
    usar("fluxlasers")
    internet.poner(web_flux())
    monkeypatch.setattr(pasadas, "_en_produccion", lambda: True)
    pasada = indexar(factory)
    assert pasada["estado"] == PASADA_ERROR and "Redis" in pasada["error"]
    with factory() as s:
        assert not filas(s)                             # no recorrió en la petición


# --- el tic mensual ----------------------------------------------------------------------------


def _pasada(s: Session, origen: str, estado: str, hace_dias: float) -> None:
    cuando = datetime.now(UTC) - timedelta(days=hace_dias)
    s.add(WebIndicePasada(origen=origen, estado=estado, inicio=cuando, fin=cuando,
                          created_at=cuando, updated_at=cuando))
    s.commit()


def test_el_tic_diario_solo_lanza_la_pasada_una_vez_al_mes(factory: sessionmaker, usar) -> None:
    usar("fluxlasers")
    with factory() as s:
        assert job.toca(s)                                      # nunca hubo
        _pasada(s, "programada", PASADA_OK, 10)
        assert not job.toca(s)                                  # hace 10 días
        _pasada(s, "manual", PASADA_OK, 0)
        assert not job.toca(s)
    with factory() as s:
        s.query(WebIndicePasada).delete()
        _pasada(s, "programada", PASADA_PARCIAL, 31)
        _pasada(s, "programada", PASADA_ERROR, 1)              # una fallida no cuenta
        assert job.toca(s)
        with patch.object(pasadas, "_config", return_value={
                **config_kb.defectos(), "webs_indice_dias": 40}):
            assert not job.toca(s)
        with patch.object(pasadas, "_config", return_value={
                **config_kb.defectos(), "webs_indice_activo": False}):
            assert not job.toca(s)
        _pasada(s, "manual", PASADA_EN_COLA, 0)                # una en marcha
        assert not job.toca(s)


@pytest.mark.parametrize("dias", [1, 30])
def test_el_tic_lanza_la_pasada_el_dia_n_y_no_el_n_mas_uno(factory: sessionmaker,
                                                           dias: int) -> None:
    """El `inicio` de la pasada cae algo después del tic que la lanzó: el tic
    de N días después, a la misma hora, ya la lanza (y una sola vez)."""
    tic = datetime(2026, 10, 1, 4, 20, tzinfo=UTC)
    config = {**config_kb.defectos(), "webs_indice_dias": dias}
    with factory() as s, patch.object(pasadas, "_config", return_value=config):
        s.add(WebIndicePasada(origen="programada", estado=PASADA_OK,
                              inicio=tic + timedelta(seconds=0.5),
                              fin=tic + timedelta(minutes=5), created_at=tic, updated_at=tic))
        s.commit()
        assert job.toca(s, ahora=tic + timedelta(days=dias))
        # Ni un día antes, ni otro tic el mismo día de la pasada.
        assert not job.toca(s, ahora=tic + timedelta(days=dias - 1))
        assert not job.toca(s, ahora=tic + timedelta(hours=6))


def test_run_daily_encola_la_programada(factory: sessionmaker,
                                        monkeypatch: pytest.MonkeyPatch, usar) -> None:
    usar("fluxlasers")
    encolados: list[str] = []
    monkeypatch.setattr(pasadas, "_encolar", encolados.append)
    with factory() as s:
        assert job.run_daily(s)
        p = pasadas.ultima(s, estados=(PASADA_EN_COLA,))
        assert p is not None and p.origen == "programada" and encolados == [p.id]
        assert not job.run_daily(s)                              # ya hay una en cola


def test_el_tic_se_arma_en_la_cola_de_worker_web_una_sola_vez() -> None:
    captado: dict[str, Any] = {}

    class FakeRedis:
        def __init__(self) -> None:
            self.claves: set[str] = set()

        def set(self, key, _v, nx=False, ex=None):  # noqa: ANN001, ANN202
            if nx and key in self.claves:
                return False
            self.claves.add(key)
            captado["ttl"] = ex
            return True

        def delete(self, key):  # noqa: ANN001, ANN202
            self.claves.discard(key)

    class FakeQueue:
        def __init__(self, name, connection=None, default_timeout=None):  # noqa: ANN001
            captado["cola"] = name

        def enqueue_in(self, delay, fn):  # noqa: ANN001, ANN202
            captado["delay"], captado["fn"] = delay, fn
            captado["n"] = captado.get("n", 0) + 1

    conn = FakeRedis()
    ahora = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    with patch("app.workers.queues.redis_connection", return_value=conn), \
            patch("rq.Queue", FakeQueue), \
            patch.object(job, "_hora_configurada", return_value="05:20"):
        job.schedule_daily(ahora)
        job.schedule_daily(ahora)                    # latido vivo → no arma otro
    assert captado["cola"] == "webs:indice" and captado["n"] == 1
    assert captado["fn"] is job._daily_runner  # noqa: SLF001
    assert timedelta(hours=16) < captado["delay"] < timedelta(hours=18)


# --- la configuración --------------------------------------------------------------------------


def test_la_configuracion_del_indice_tiene_sus_valores_de_partida() -> None:
    cfg = config_kb.defectos()
    assert cfg["webs_indice_activo"] is True and cfg["webs_indice_hora"] == "05:20"
    assert cfg["webs_indice_dias"] == 30
    assert cfg["webs_indice_hosts"] == {"fluxlasers": "fluxlasers.eu"}
    norm = config_kb.normalizar({"webs_indice_dias": "7", "webs_indice_hora": "6:05",
                                 "webs_indice_hosts": {"fluxlasers": "https://FluxLasers.EU/"}})
    assert norm["webs_indice_dias"] == 7 and norm["webs_indice_hora"] == "06:05"
    assert norm["webs_indice_hosts"] == {"fluxlasers": "fluxlasers.eu"}
    assert config_kb.normalizar({"webs_indice_dias": 0})["webs_indice_dias"] == 30
    nuevo = config_kb.validar({"webs_indice_activo": False, "webs_indice_dias": 15}, {})
    assert nuevo["webs_indice_activo"] is False and nuevo["webs_indice_dias"] == 15
    for malo in ({"webs_indice_hora": "25:00"}, {"webs_indice_dias": 0},
                 {"webs_indice_activo": "sí"}, {"webs_indice_hosts": {"fluxlasers": "no es"}},
                 {"webs_indice_hosts": ["fluxlasers.eu"]}):
        with pytest.raises(ValueError):
            config_kb.validar(malo, {})


def test_el_host_de_cada_web_sale_de_la_configuracion() -> None:
    from tests.test_webs_comun import sitio

    cfg = config_kb.defectos()
    assert pasadas.host_de(sitio("fluxlasers"), cfg) == "fluxlasers.eu"
    assert pasadas.host_de(sitio("boprint"), cfg) == "boprint.net"
    assert pasadas.host_de(sitio("mqeurope"), cfg) == "www.mqeurope.com"
    assert pasadas.host_de(sitio("fluxlasers"), {"webs_indice_hosts": {}}) == "fluxlasers.es"


def test_las_paginas_html_solo_van_a_los_hosts_de_la_web() -> None:
    from tests.test_webs_comun import sitio

    hosts = lector.hosts_de(sitio("fluxlasers"), "fluxlasers.eu")
    assert {"fluxlasers.eu", "www.fluxlasers.eu", "fluxlasers.es"} <= hosts
    assert "evil.example.com" not in hosts
    assert AJ_HOST in lector.hosts_de(sitio("artisjet-eu"), AJ_HOST)
    assert PIMPAM_HOST in lector.hosts_de(sitio("pimpam"), PIMPAM_HOST)


def test_wpglobus_sin_el_idioma_por_defecto_no_toca_los_demas(
        factory: sessionmaker, internet: Internet, usar) -> None:
    """Si el francés de artisjet-printers.eu no responde, no se puede saber qué
    página en/de/pt es traducción y cuál es el francés de respaldo: esos
    idiomas no se escriben (ni se marcan idos) hasta la próxima."""
    usar("artisjet-eu")
    web = web_artisjet_eu()
    internet.poner(web)
    indexar(factory)
    with factory() as s:
        antes = {(f.wp_id, f.idioma) for f in filas(s)}
    web.caidos = {"fr"}
    pasada = indexar(factory)
    assert pasada["resumen"]["artisjet-eu"]["estado"] == PASADA_ERROR
    with factory() as s:
        assert {(f.wp_id, f.idioma) for f in filas(s)} == antes
