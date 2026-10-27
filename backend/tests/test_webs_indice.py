"""El índice de nuestras webs: lo que entra (y lo que no), un idioma por fila,
las pasadas (una web por trabajo), el patrón visto / ido y el tic mensual.

Nada sale a la red: las webs son WordPress simulados (`test_webs_comun`) por
`httpx.MockTransport`, con el resolutor de nombres falso.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import replace
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
from app.services.webs.lector import DecisionIdioma
from app.services.webs.reglas import ReglasSitio, ReglaSuelta
from app.services.webs.reglas_partida import CATEGORIAS_FUERA
from tests.test_webs_comun import (
    AJ_CATEGORIAS,
    AJ_HOST,
    FLUX_CATEGORIAS,
    FLUX_DENTRO_ES,
    FLUX_FUERA_ES,
    FLUX_FUERA_PRODUCTOS_ES,
    FLUX_HOST,
    FLUX_IDIOMAS,
    FLUX_MEZCLA_ES,
    FLUX_NOMBRES,
    FLUX_SIN_TRADUCIR,
    MBO_HOST,
    PIMPAM_HOST,
    Internet,
    WebFalsa,
    categoria,
    flux_cat,
    flux_prod,
    producto,
    registro_con,
    sitio,
    web_artisjet_eu,
    web_flux,
    web_flux_wpml,
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
    "Courroie d'entraînement Freebird",
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
                       "artisJet DIVE", "Ink Bottle System with Shelf",
                       "WhiteRIP LOGICIEL D'IMPRESSION"):
            assert dentro in titulos, f"falta {dentro}"
        hexa = next(f for f in productos if f.wp_id == 2350)
        assert "Accesorios Hexa" in busqueda._lista(hexa.categorias_json)  # noqa: SLF001
        assert hexa.imagen and hexa.imagen.endswith("-0.jpg")
        assert busqueda._lista(hexa.galeria_json) == [  # noqa: SLF001
            f"https://{FLUX_HOST}/wp-content/uploads/modulo-rotatorio-para-flux-hexa-1.jpg"]


def test_flux_se_lee_de_fluxlasers_eu_y_no_de_es(factory: sessionmaker,
                                                  internet: Internet, usar) -> None:
    usar("fluxlasers")
    web = web_flux_wpml()
    internet.poner(web)
    indexar(factory)
    assert web.peticiones and all(p.startswith(f"https://{FLUX_HOST}/") for p in web.peticiones)
    with factory() as s:
        assert {f.dominio for f in filas(s)} == {FLUX_HOST}
        # fluxlasers.eu publica sus cinco idiomas (por .es, WPML solo sirve
        # el español).
        assert {f.idioma for f in filas(s)} == set(FLUX_IDIOMAS)


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


# --- idiomas WPML y mbolasers por la REST -------------------------------------------------------


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


def test_mbolasers_se_lee_por_la_rest_y_un_5xx_es_un_error_sin_marcar_nada_ido(
        factory: sessionmaker, internet: Internet, usar) -> None:
    """Desde el 28/10 la REST de mbolasers da las páginas con su contenido (el
    500 era el límite de memoria de WordPress). Ya no hay camino por el HTML
    público: si vuelve el 500, ese grupo queda en error y se queda lo
    anterior."""
    usar("mbolasers")
    web = web_mbolasers()
    internet.poner(web)
    pasada = indexar(factory)
    assert pasada["resumen"]["mbolasers"]["estado"] == PASADA_OK
    # Todo por la REST, ni una petición al HTML público; las páginas, con su
    # contenido.
    assert web.peticiones and all("?rest_route=/" in p for p in web.peticiones)
    assert any("rest_route=/wp/v2/pages&" in p and "content" in p for p in web.peticiones)
    with factory() as s:
        es = filas(s, sitio="mbolasers", tipo=WEB_PAGINA, idioma="es")
        assert [f.wp_id for f in es] == [1265]
        assert "Láser de fibra MBO" in es[0].texto and es[0].titulo == "MBO Fiber Lasers"
        assert [f.wp_id for f in filas(s, tipo=WEB_PAGINA, idioma="en")] == [2373]
        assert [f.wp_id for f in filas(s, tipo=WEB_PAGINA, idioma="de")] == [2866]
        assert [f.wp_id for f in filas(s, tipo=WEB_PRODUCTO, idioma="en")] == [2839]
        antes = {(f.tipo, f.wp_id, f.idioma) for f in filas(s)}
    web.contenido_500 = True                    # vuelve el error de memoria
    web.peticiones.clear()
    pasada = indexar(factory)
    resumen = pasada["resumen"]["mbolasers"]
    assert resumen["estado"] == PASADA_PARCIAL and resumen["idos"] == 0
    assert "pagina es: HTTP 500" in resumen["error"]
    assert any("pagina es: la web da error de servidor" in a for a in resumen["avisos"])
    assert all("?rest_route=/" in p for p in web.peticiones)
    with factory() as s:
        assert {(f.tipo, f.wp_id, f.idioma) for f in filas(s)} == antes


# --- visto / ido ---------------------------------------------------------------------------------


@pytest.mark.parametrize("web_de_flux", [web_flux, web_flux_wpml])
def test_reindexar_dos_veces_no_duplica(factory: sessionmaker, internet: Internet, usar,
                                        web_de_flux: Callable[[], WebFalsa]) -> None:
    usar("fluxlasers", "artisjet-eu")
    internet.poner(web_de_flux(), web_artisjet_eu())
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
        {"id": 5001, "categories": [{"id": 21, "name": "Repuestos"}]},
        DecisionIdioma.de_reglas(flux, {21})) == (False, "categoria")
    assert "sin web" in caplog.text


def test_categorias_fuera_heredan_a_las_hijas() -> None:
    reglas = reglas_mod.Reglas(reglas=reglas_mod._de_partida()).para("x")  # noqa: SLF001
    # El árbol real de FLUX: las siete hijas de «Repuestos», y «Reacondicionadas»
    # aunque cuelgue de «Cortadoras Láser», que es de dentro.
    assert lector.categorias_fuera(FLUX_CATEGORIAS, reglas) == FLUX_FUERA_ES
    # «Logiciels» (19) es hija de «Autres pièces de rechange» (18).
    assert lector.categorias_fuera(AJ_CATEGORIAS, reglas) == {51, 52, 130, 53, 5, 96, 18, 19}


def test_la_decision_es_por_ids_y_los_nombres_no_cuentan() -> None:
    """Fuera del idioma por defecto (sin `nombres`) `producto_dentro` no mira
    los nombres: un «Repuestos» con un id que no es de fuera entra (en otro
    idioma un nombre puede coincidir por azar) y un «Spare Parts» con el id de
    fuera no."""
    reglas = reglas_mod.Reglas(reglas=reglas_mod._de_partida()).para("fluxlasers")
    decision = DecisionIdioma.de_reglas(reglas, {1021})
    assert lector.producto_dentro(
        {"id": 1, "categories": [{"id": 1021, "name": "Spare Parts"}]}, decision) == (
        False, "categoria")
    assert lector.producto_dentro(
        {"id": 2, "categories": [{"id": 7, "name": "Repuestos"}]}, decision) == (True, "")
    assert lector.producto_dentro({"id": 3, "categories": []}, decision) == (
        False, "sin_categoria")
    # WPML fuera del idioma por defecto: sin ninguna categoría conocida, fuera.
    wpml = DecisionIdioma(fuera={1021}, conocidas={1020, 1021})
    assert lector.producto_dentro(
        {"id": 4, "categories": [{"id": 1990, "name": "Black Friday"}]}, wpml) == (
        False, "categoria_sin_original")
    assert lector.producto_dentro(
        {"id": 5, "categories": [{"id": 1990, "name": "Black Friday"},
                                 {"id": 1020, "name": "Accessories"}]}, wpml) == (True, "")


def test_los_recambios_que_las_webs_cuelgan_de_accesorios_quedan_fuera() -> None:
    """Repasado sobre el recorrido real: artisjet-spain.es cuelga cabezales y
    dampers de «Accesorios» y artisjet-printers.eu consumibles de «Accessoires
    et pièces de rechange»; las excepciones de partida los sacan, cada una en
    su web, y el molde a medida y el WhiteRIP de «Autres pièces de rechange»
    entran."""
    partida = reglas_mod.Reglas(reglas=reglas_mod._de_partida())  # noqa: SLF001
    es, eu, boprint = partida.para("artisjet-es"), partida.para("artisjet-eu"), \
        partida.para("boprint")
    de_es, de_boprint = DecisionIdioma.de_reglas(es), DecisionIdioma.de_reglas(boprint)
    de_eu = DecisionIdioma.de_reglas(eu, lector.categorias_fuera(AJ_CATEGORIAS, eu))
    accesorios = [{"id": 9, "name": "Accesorios"}]
    for pid in (470, 474, 481, 485, 2026051305):
        assert lector.producto_dentro({"id": pid, "categories": accesorios}, de_es) == \
            (False, "excluido")
    assert lector.producto_dentro({"id": 477, "categories": accesorios}, de_es)[0]
    mezcla = [{"id": 46, "name": "Accessoires et pièces de rechange"}]
    for pid in (1892, 1989, 2979):
        assert not lector.producto_dentro({"id": pid, "categories": mezcla}, de_eu)[0]
    assert lector.producto_dentro({"id": 3850, "categories": mezcla}, de_eu)[0]
    autres = [{"id": 18, "name": "Autres pièces de rechange"}]
    logiciels = [{"id": 19, "name": "Logiciels"}]
    assert lector.producto_dentro({"id": 477, "categories": autres}, de_eu) == \
        (True, "incluido")
    assert lector.producto_dentro({"id": 493, "categories": logiciels}, de_eu) == \
        (True, "incluido")
    assert not lector.producto_dentro({"id": 478, "categories": autres}, de_eu)[0]
    assert not lector.producto_dentro({"id": 494, "categories": logiciels}, de_eu)[0]
    # Los ids son de cada web: el 470 de otra web no se toca.
    assert lector.producto_dentro({"id": 470, "categories": accesorios}, de_boprint)[0]


# --- WPML: la decisión del idioma por defecto, llevada a los demás por id ------------------------
#
# fluxlasers.eu (27/10/2026): con las categorías decididas idioma a idioma por
# sus nombres traducidos, entraron 1.219 productos en vez de ~166 («Spare
# Parts», «Ersatzteile»… no empiezan por «Repuestos»).

#: Los ids de dentro de FLUX en español.
_FLUX_DENTRO = frozenset(pid for pid, _, _ in FLUX_DENTRO_ES)


def _reglas_flux(*extra: ReglaSuelta) -> ReglasSitio:
    """Las de partida (ninguna es de FLUX) y las que diga el test."""
    return reglas_mod.Reglas(
        reglas=[*reglas_mod._de_partida(), *extra]).para("fluxlasers")  # noqa: SLF001


def _leer_flux(web: WebFalsa, reglas: ReglasSitio,
               idiomas: tuple[str, ...] | None = None) -> lector.LecturaWeb:
    flux = sitio("fluxlasers")
    if idiomas:
        flux = replace(flux, idiomas=idiomas)
    return lector.leer_web(flux, reglas, host=FLUX_HOST, http=Internet().poner(web).http())


def _grupo(lectura: lector.LecturaWeb, tipo: str, idioma: str) -> lector.Grupo:
    return next(g for g in lectura.grupos if g.tipo == tipo and g.idioma == idioma)


def _dentro(web: WebFalsa, lectura: lector.LecturaWeb) -> dict[str, set[int]]:
    """Los productos que entran, por idioma, con el id de su original en
    español (el de la web en cada idioma es otro)."""
    salida: dict[str, set[int]] = {}
    for g in lectura.grupos:
        if g.tipo == WEB_PRODUCTO:
            assert g.error is None, (g.idioma, g.error)
            salida[g.idioma] = {web.originales_prod.get(f["wp_id"], f["wp_id"]) for f in g.filas}
    return salida


def test_una_regla_de_categoria_escrita_una_vez_en_espanol_vale_en_todos_los_idiomas() -> None:
    """La regla se escribe en el idioma por defecto y vale en todos, también en
    uno que se añade después (aquí «it»), sin escribir ninguna traducción."""
    idiomas = (*FLUX_IDIOMAS, "it")
    web = web_flux_wpml(idiomas)
    reglas = _reglas_flux(ReglaSuelta(REGLA_CATEGORIA_FUERA, "Accesorios Hexa"))
    dentro = _dentro(web, _leer_flux(web, reglas, idiomas))
    assert set(dentro) == set(idiomas)
    # Lo que está en «Accesorios Hexa», también lo que está además en otras de
    # dentro (el BEAM AIR PRO en Accesorios, Hexa y Beambox).
    hexa = {pid for pid, _, cats in FLUX_DENTRO_ES if 223 in cats}
    assert hexa == {4314, 3641, 3598, 3384, 2355, 2350}
    for idioma in idiomas:
        assert not dentro[idioma] & hexa, idioma
        assert dentro[idioma] == _FLUX_DENTRO - hexa - {FLUX_SIN_TRADUCIR.get(idioma)}, idioma
    # Ninguna traducción pasa la regla: solo decide el nombre en español.
    for idioma in ("en", "de", "fr", "nl", "it"):
        assert not reglas.categoria_fuera(FLUX_NOMBRES[idioma][223]), idioma


@pytest.mark.parametrize("filtra_el_servidor", [True, False])
def test_flux_wpml_entero_sin_reglas_de_flux_da_166_productos(
        factory: sessionmaker, internet: Internet, usar, filtra_el_servidor: bool) -> None:
    """El recorrido de fluxlasers.eu con las reglas de partida (ninguna de
    FLUX): 34 en español (los reales) y 33 en cada uno de los otros cuatro
    idiomas (falta una traducción en cada uno). Igual si el servidor no
    filtrara: el lector lo revisa otra vez, por ids."""
    usar("fluxlasers")
    web = web_flux_wpml()
    web.ignora_not_in = not filtra_el_servidor
    internet.poner(web)
    pasada = indexar(factory)
    assert pasada["estado"] == PASADA_OK, pasada
    with factory() as s:
        productos = filas(s, sitio="fluxlasers", tipo=WEB_PRODUCTO)
        assert Counter(f.idioma for f in productos) == {
            "es": 34, "en": 33, "de": 33, "fr": 33, "nl": 33}
        assert len(productos) == 166
        for idioma in FLUX_IDIOMAS:
            originales = {web.originales_prod.get(f.wp_id, f.wp_id)
                          for f in productos if f.idioma == idioma}
            assert originales == _FLUX_DENTRO - {FLUX_SIN_TRADUCIR.get(idioma)}, idioma
        es = {f.wp_id: f for f in productos if f.idioma == "es"}
        assert es[1901].titulo == "Flux Hexa – Cortadora láser CO2 (60W)"
        assert busqueda._lista(es[3641].categorias_json) == [  # noqa: SLF001
            "Accesorios", "Accesorios Hexa", "Accesorios Beambox"]
        en = {f.wp_id: f for f in productos if f.idioma == "en"}
        assert busqueda._lista(en[flux_prod("en", 3641)].categorias_json) == [  # noqa: SLF001
            "Accessories", "Hexa Add-ons", "Beambox Add-ons"]
        assert "/en/" in en[flux_prod("en", 3641)].url
        # Ni uno de una categoría de fuera, en ningún idioma.
        nombres_fuera = {FLUX_NOMBRES[i][c] for i in FLUX_IDIOMAS for c in FLUX_FUERA_ES}
        for fila in productos:
            assert not set(busqueda._lista(fila.categorias_json)) & nombres_fuera, (  # noqa: SLF001
                fila.idioma, fila.titulo)
    # Con la lógica de antes (cada idioma decidido con los nombres de su árbol)
    # los repuestos entraban: ninguna regla de partida casa con sus nombres
    # traducidos. Solo el camino por id los saca.
    partida = _reglas_flux()
    hijas_de_repuestos = (21, 246, 24, 257, 23, 216, 231)
    for idioma in ("en", "de", "nl"):
        for c in hijas_de_repuestos:
            assert not partida.categoria_fuera(FLUX_NOMBRES[idioma][c]), FLUX_NOMBRES[idioma][c]
        assert not lector.categorias_fuera(web.categorias[idioma], partida) & {
            flux_cat(idioma, c) for c in hijas_de_repuestos}
    # Sin excepciones por id de FLUX no se pide ninguna traducción de productos.
    assert not any("rest_route=/wc/store/v1/products&" in p and "include=" in p
                   for p in web.peticiones)


@pytest.mark.parametrize("filtra_el_servidor", [True, False])
def test_dentro_y_fuera_a_la_vez_queda_fuera_en_todos_los_idiomas(
        filtra_el_servidor: bool) -> None:
    """Las Open Box («Cortadoras Láser» y «Reacondicionadas») y los cartuchos
    («Flux Ador» y «Tintas Ador»): cualquier categoría de fuera manda, en
    todos los idiomas."""
    web = web_flux_wpml()
    web.ignora_not_in = not filtra_el_servidor
    lectura = _leer_flux(web, _reglas_flux())
    dentro = _dentro(web, lectura)
    mezcla = {pid for pid, _, _ in FLUX_MEZCLA_ES}
    for idioma in FLUX_IDIOMAS:
        assert not dentro[idioma] & mezcla, idioma
        if not filtra_el_servidor:
            # Llegan todos al lector y él los saca por id.
            assert _grupo(lectura, WEB_PRODUCTO, idioma).descartes["categoria"] == (
                len(FLUX_MEZCLA_ES) + len(FLUX_FUERA_PRODUCTOS_ES)), idioma


@pytest.mark.parametrize("filtra_el_servidor", [True, False])
def test_lo_de_una_hija_de_una_categoria_de_fuera_queda_fuera_aunque_el_nombre_no_empiece_igual(
        filtra_el_servidor: bool) -> None:
    """«Beambox Spare Parts» no empieza por «Spare Parts» ni «Ador Onderdelen»
    por «Reserveonderdelen», y lo que solo está en ellas queda fuera; también
    una hija que solo existe en inglés («Beambox II Laser Tubes», sin
    original en español), por colgar de una de fuera."""
    web = web_flux_wpml()
    web.ignora_not_in = not filtra_el_servidor
    # La placa del CAM de la Beambox (2866) solo está en «Repuestos Beambox»;
    # el extractor de tinta, en neerlandés, se tradujo solo con la hija.
    extractor = next(p for p in web.productos["nl"] if p["id"] == flux_prod("nl", 5485))
    extractor["categories"] = [{"id": flux_cat("nl", 246), "name": "Ador Onderdelen",
                                "slug": "ador-onderdelen"}]
    web.categorias["en"].append(categoria(1990, "Beambox II Laser Tubes", flux_cat("en", 21)))
    web.productos["en"].append(producto(19003, "Laser tube 55W", [
        (1990, "Beambox II Laser Tubes")], host=FLUX_HOST, prefijo="en/"))
    lectura = _leer_flux(web, _reglas_flux())
    leidos = {g.idioma: {f["wp_id"] for f in g.filas}
              for g in lectura.grupos if g.tipo == WEB_PRODUCTO}
    assert flux_prod("en", 2866) not in leidos["en"]
    assert flux_prod("nl", 5485) not in leidos["nl"]
    assert 19003 not in leidos["en"]
    assert not any("sin original" in a for a in lectura.avisos)   # cuelga de una de fuera
    assert len(leidos["en"]) == len(leidos["nl"]) == 33


def test_wpml_que_no_ajusta_los_ids_deja_el_idioma_en_error_y_no_marca_nada_ido(
        factory: sessionmaker, internet: Internet, usar) -> None:
    """Cerrado ante la duda: si ninguna categoría de un idioma resulta ser
    traducción de una del idioma por defecto, no se sabe qué es un repuesto.
    Los productos de ese idioma quedan en error y lo de antes se queda."""
    usar("fluxlasers")
    web = web_flux_wpml()
    internet.poner(web)
    indexar(factory)
    with factory() as s:
        antes = {(f.wp_id, f.idioma) for f in filas(s, tipo=WEB_PRODUCTO)}
    assert len(antes) == 166
    web.ajusta_ids = False
    pasada = indexar(factory)
    resumen = pasada["resumen"]["fluxlasers"]
    assert resumen["estado"] == PASADA_PARCIAL and resumen["idos"] == 0
    assert resumen["error"].count("WPML no ha traducido los ids") == 4
    with factory() as s:
        assert {(f.wp_id, f.idioma) for f in filas(s, tipo=WEB_PRODUCTO)} == antes
    lectura = _leer_flux(web, _reglas_flux())
    assert _grupo(lectura, WEB_PRODUCTO, "es").error is None
    for idioma in ("en", "de", "fr", "nl"):
        grupo = _grupo(lectura, WEB_PRODUCTO, idioma)
        assert grupo.error and f"ninguna categoría de «{idioma}»" in grupo.error
        assert not grupo.filas


def test_wpml_que_no_ajusta_los_ids_con_una_categoria_sin_traducir_tambien_queda_en_error(
        ) -> None:
    """Una categoría sin traducir que WPML sirve con su id del español
    («Láser de fibra», 245, en el árbol inglés) no prueba que WPML enlace los
    idiomas: el enlace se mide solo con los ids PROPIOS del idioma. Antes
    pasaba: 53 productos `categoria_sin_original` y un repuesto de «Spare
    Parts» que además estaba en la 245 entraba."""
    web = web_flux_wpml()
    web.ajusta_ids = False
    web.categorias["en"] = [c for c in web.categorias["en"] if c["id"] != flux_cat("en", 245)]
    web.categorias["en"].append(categoria(245, "Láser de fibra"))
    web.productos["en"].append(producto(19007, "Exhaust fan Beambox", [
        (flux_cat("en", 21), "Spare Parts"), (245, "Láser de fibra")], host=FLUX_HOST,
        prefijo="en/"))
    lectura = _leer_flux(web, _reglas_flux())
    en = _grupo(lectura, WEB_PRODUCTO, "en")
    assert en.error and "ninguna categoría de «en»" in en.error
    assert not en.filas and not en.descartes
    # Un idioma cuyas categorías son TODAS ids del idioma por defecto (WPML las
    # sirve sin traducir) sí pasa, aunque WPML no ajuste nada: decide el id.
    web = web_flux_wpml()
    web.ajusta_ids = False
    web.categorias["en"] = [dict(c) for c in web.categorias["es"]]
    for p in web.productos["en"]:
        p["categories"] = [{**c, "id": c["id"] % 1000} for c in p["categories"]]
    en = _grupo(_leer_flux(web, _reglas_flux()), WEB_PRODUCTO, "en")
    assert en.error is None and len(en.filas) == 33
    assert {web.originales_prod.get(f["wp_id"], f["wp_id"]) for f in en.filas} == (
        _FLUX_DENTRO - {FLUX_SIN_TRADUCIR["en"]})


def test_una_categoria_de_fuera_vacia_en_el_idioma_por_defecto_saca_en_los_demas() -> None:
    """La Store API esconde las categorías vacías (`hide_empty`, de partida
    true), en el árbol y en `include=`: «Servicios» (15) no tiene productos en
    español pero «Services» sí en inglés. Sin pedirlas todas, la 15 no estaba
    ni entre las de fuera ni traducida, y un servicio que además estaba en
    «Laser Cutters» entraba."""
    web = web_flux_wpml()
    next(c for c in web.categorias["es"] if c["id"] == 15)["count"] = 0
    web.productos["en"].append(producto(19008, "Beambox II installation", [
        (flux_cat("en", 15), "Services"), (flux_cat("en", 19), "Laser Cutters")],
        host=FLUX_HOST, prefijo="en/"))
    lectura = _leer_flux(web, _reglas_flux())
    en = _grupo(lectura, WEB_PRODUCTO, "en")
    assert en.error is None
    assert 19008 not in {f["wp_id"] for f in en.filas}
    assert len(en.filas) == 33
    assert not any("sin original" in a for a in lectura.avisos)
    categorias = [p for p in web.peticiones if "rest_route=/wc/store/v1/products/categories" in p]
    assert categorias and all("hide_empty=false" in p for p in categorias)


def test_en_el_idioma_por_defecto_el_nombre_de_una_categoria_del_producto_tambien_saca() -> None:
    """Red de seguridad donde los nombres SÍ están en el idioma de las reglas:
    en el idioma por defecto (y en una web de un solo idioma) un producto queda
    fuera si el nombre de una de SUS categorías empieza por una regla, aunque
    esa categoría falte del árbol (`count` desfasado, árbol cortado en
    `MAX_PAGINAS`). En los demás idiomas deciden solo los ids."""
    reglas = _reglas_flux()
    dtf = {"id": 9990, "categories": [{"id": 999, "name": "Consumibles DTF"}]}
    assert lector.producto_dentro(dtf, DecisionIdioma.de_reglas(reglas, {21}, nombres=True)) == (
        False, "categoria")
    assert lector.producto_dentro(dtf, DecisionIdioma.de_reglas(reglas, {21})) == (True, "")
    web = web_flux_wpml()
    web.productos["es"].append(producto(9990, "Film DTF A3", [(999, "Consumibles DTF")],
                                        host=FLUX_HOST))
    # En inglés, otra categoría que tampoco está en el árbol, con un nombre
    # traducido que empieza por una regla de partida («Services»): no decide.
    web.productos["en"] += [
        producto(19990, "DTF setup", [(1999, "Services DTF")], host=FLUX_HOST, prefijo="en/"),
        producto(19991, "Beambox II 55W DTF bundle", [
            (flux_cat("en", 19), "Laser Cutters"), (1999, "Services DTF")], host=FLUX_HOST,
            prefijo="en/"),
    ]
    lectura = _leer_flux(web, reglas)
    es, en = _grupo(lectura, WEB_PRODUCTO, "es"), _grupo(lectura, WEB_PRODUCTO, "en")
    assert 9990 not in {f["wp_id"] for f in es.filas} and len(es.filas) == 34
    ids_en = {f["wp_id"] for f in en.filas}
    assert 19990 not in ids_en and en.descartes["categoria_sin_original"] == 1   # por id
    assert 19991 in ids_en                                   # el nombre traducido no saca
    # Una web de un solo idioma: como el idioma por defecto.
    unico = web_flux()
    unico.productos["es"].append(producto(9990, "Film DTF A3", [(999, "Consumibles DTF")],
                                          host=FLUX_HOST))
    grupo = _grupo(_leer_flux(unico, reglas, ("es",)), WEB_PRODUCTO, "es")
    assert grupo.filas and 9990 not in {f["wp_id"] for f in grupo.filas}


def test_una_categoria_que_solo_existe_en_un_idioma_no_deja_entrar_y_se_avisa() -> None:
    web = web_flux_wpml()
    web.categorias["en"].append(categoria(1991, "Black Friday"))     # sin original en es
    web.productos["en"] += [
        producto(19001, "Black Friday Bundle", [(1991, "Black Friday")], host=FLUX_HOST,
                 prefijo="en/"),
        producto(19002, "Beambox II 55W Black Friday", [
            (1991, "Black Friday"), (flux_cat("en", 19), "Laser Cutters")], host=FLUX_HOST,
            prefijo="en/"),
    ]
    lectura = _leer_flux(web, _reglas_flux())
    en = _grupo(lectura, WEB_PRODUCTO, "en")
    ids = {f["wp_id"] for f in en.filas}
    assert 19001 not in ids                     # solo en la de sin original: fuera
    assert 19002 in ids                         # también en una conocida: dentro
    assert en.descartes["categoria_sin_original"] == 1
    aviso = next(a for a in lectura.avisos if "sin original" in a)
    assert aviso.startswith("producto en: 1 categoría(s) sin original en es")
    assert "«Black Friday» (1991)" in aviso
    # Los demás idiomas, como siempre.
    assert len(_grupo(lectura, WEB_PRODUCTO, "de").filas) == 33


def test_las_excepciones_por_id_se_traducen_en_wpml() -> None:
    """Se escriben con el id del idioma por defecto; WPML da el de cada
    idioma (`include=` ajustado)."""
    web = web_flux_wpml()
    reglas = _reglas_flux(ReglaSuelta(REGLA_PRODUCTO_EXCLUIR, "3949", "fluxlasers"),
                          ReglaSuelta(REGLA_PRODUCTO_INCLUIR, "5580", "fluxlasers"))
    dentro = _dentro(web, _leer_flux(web, reglas))
    for idioma in FLUX_IDIOMAS:
        assert 3949 not in dentro[idioma], idioma     # la Beambox II 55W, excluida
        assert 5580 in dentro[idioma], idioma         # la bomba de agua, un repuesto, incluida
        assert len(dentro[idioma]) == len(_FLUX_DENTRO - {FLUX_SIN_TRADUCIR.get(idioma)})
    assert any("lang=de" in p and "rest_route=/wc/store/v1/products&" in p
               and "include=3949" in p for p in web.peticiones)


def test_el_whiterip_de_artisjet_eu_entra_y_lo_demas_de_autres_pieces_no(
        factory: sessionmaker, internet: Internet, usar) -> None:
    """El 493 está en «Logiciels», hija de «Autres pièces de rechange»: entra
    por la excepción de partida (en cada idioma traducido); una correa de
    «Autres pièces de rechange», no."""
    usar("artisjet-eu")
    internet.poner(web_artisjet_eu())
    indexar(factory)
    with factory() as s:
        whiterip = {f.idioma: f for f in filas(s, wp_id=493)}
        assert set(whiterip) == {"fr", "de", "en"}      # pt es el francés: respaldo
        assert busqueda._lista(whiterip["de"].categorias_json) == ["Software"]  # noqa: SLF001
        assert not filas(s, wp_id=7008)


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
    cfg = config_kb.defectos()
    assert pasadas.host_de(sitio("fluxlasers"), cfg) == "fluxlasers.eu"
    assert pasadas.host_de(sitio("boprint"), cfg) == "boprint.net"
    assert pasadas.host_de(sitio("mqeurope"), cfg) == "www.mqeurope.com"
    assert pasadas.host_de(sitio("fluxlasers"), {"webs_indice_hosts": {}}) == "fluxlasers.es"


def test_las_peticiones_solo_van_a_los_hosts_de_la_web() -> None:
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
