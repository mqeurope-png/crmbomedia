"""Fichas de producto montadas desde el índice de las webs (28/10/2026).

Un producto que el catálogo del compositor no tiene y está publicado en una de
nuestras webs (la «MBO 6090 UV Led – Desktop», solo en mboprinters.com) puede
ir en un correo compuesto con el mismo bloque de ficha, montada desde su
página del índice (`webs.fichas`): título, descripción, imagen principal
enlazada, enlace y el precio de la web con su IVA tal como está guardado, o
«precio a consultar». Lo que se comprueba:

- La ficha del índice sale con su imagen, su enlace y el precio de la web; si
  el producto está también en el catálogo, va la del catálogo.
- La variante que pide quien envía y que solo está en la web («la versión
  desktop») va aunque el catálogo tenga una parecida, cada ficha con su
  enlace.
- Sin precio en la web, «precio a consultar»; un precio que dice quien envía
  en sus instrucciones se usa (y el aviso lo dice), uno que no dice, no.
- La ficha va en el idioma del correo si la web lo tiene; si no, en el que
  haya, y se dice.
- «Lo que ha usado» (`contexto["fichas"]`) y el aviso distinguen las fichas
  del catálogo de las del índice, con su enlace.
- El enlace de una ficha del catálogo que no es una página de producto del
  índice (`boprint.net/contacto/`) se avisa en el borrador y en el Cuadre;
  nunca se corrige.

Nada sale a la red: Claude está simulado (el plan de la consulta y la
redacción) y las páginas de las webs son filas de `web_indice`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.erp.cuadre import engine as cuadre_engine
from app.erp.cuadre.checks_catalogo import (
    ENLACE_CATALOGO_NO_PRODUCTO,
    ENTIDAD_PRODUCTO_CATALOGO,
    enlace_catalogo_no_producto,
)
from app.erp.cuadre.config import normalizar_config
from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import FUENTE_MYSQL, registro
from app.erp.models import CuadreFinding
from app.main import app
from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    FUENTE_WOOCOMMERCE,
    TIPO_CONSUMIBLE,
    TIPO_MAQUINA,
    ProductoCatalogo,
)
from app.models.crm import User, UserEmailAliasPref, UserRole
from app.models.fuentes_externas import WebIndiceEntrada
from app.services import llm, soporte
from app.services.asistente import consulta_fuentes, material_fuera, procedencia
from app.services.conocimiento.catalogo import avisos_en
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import datos as datos_mod
from app.services.correo_compuesto import generador, ia
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from app.services.webs import fichas
from tests._test_helpers import seed_org_google_integration, seed_test_users
from tests.test_asistente import ALIAS, _contacto, _hilo, _pedir
from tests.test_asistente_compuesto import _refs_de_peticion
from tests.test_asistente_fuentes import (
    AMBAS,
    NINGUNA,
    _ClaudeConPlan,
    _material,
    _partes,
    _turno,
)
from tests.test_soporte import Red
from tests.test_webs_api import fila as fila_web

AHORA = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
LAURA = "laura@rotulosmar.es"
#: La MBO 6090 del catálogo y su página en mboprinters.com (la del índice, con
#: la barra final: es la misma página).
URL_6090 = "https://mboprinters.com/product/mbo-6090-uv-led"
URL_6090_WEB = "https://mboprinters.com/product/mbo-6090-uv-led/"
IMG_6090_WEB = "https://mboprinters.com/wp-content/uploads/2026/10/mbo-6090-uv-led.jpg"
#: La versión desktop: solo en la web (en castellano y en inglés).
URL_DESKTOP = "https://mboprinters.com/product/mbo-6090-uv-led-desktop/"
URL_DESKTOP_EN = "https://mboprinters.com/en/product/mbo-6090-uv-led-desktop/"
IMG_DESKTOP = "https://mboprinters.com/wp-content/uploads/2026/10/mbo-6090-desktop.jpg"
GALERIA_DESKTOP = ["https://mboprinters.com/wp-content/uploads/2026/10/mbo-6090-desktop-lado.jpg",
                   "https://mboprinters.com/wp-content/uploads/2026/10/mbo-6090-desktop-cama.jpg"]
TITULO_DESKTOP = "MBO 6090 UV Led – Desktop"
TITULO_DESKTOP_EN = "MBO 6090 UV LED Desktop"
PRECIO_DESKTOP = "7.990,00 € (+ IVA)"
#: La artisJet 6090 Trust: del catálogo, en una web sin productos en el índice.
URL_TRUST = "https://artisjet-printers.eu/en/product/artisjet-6090-trust/"
IMG_TRUST = "https://cdn.example/aj-6090-trust.jpg"
#: La ficha del catálogo que apuntaba a la página de contacto (28/10/2026).
URL_CONTACTO = "https://boprint.net/contacto/"
#: Un consumible de la tienda de boprint.net (los consumibles no entran en el índice).
URL_TINTA = "https://boprint.net/producto/tinta-uv-blanca-1l/"
#: Lo que devuelve el plan de la consulta: el producto que se busca en las webs y
#: los que pide quien envía.
PLAN_DESKTOP = {"tecnica": False, "marca": "", "soporte_en": "", "soporte": "",
                "webs": "MBO 6090 Desktop", "productos": []}
PLAN_PEDIDA = {**PLAN_DESKTOP, "webs": "", "productos": ["MBO 6090 desktop"]}
PLAN_SIN_WEBS = {**PLAN_DESKTOP, "webs": ""}
#: Los precios del compositor sin IVA (como en producción).
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
        admin = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.add(UserEmailAliasPref(user_id=admin, alias_email=ALIAS, is_allowed=True,
                                    is_default=True))
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


@pytest.fixture()
def s(session_factory: sessionmaker) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _ia_configurada(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    get_settings.cache_clear()
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    yield
    fijar_para_tests(None)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _sin_red() -> Generator[None, None, None]:
    """Ni el soporte sale a la red: una petición que llegue, falla."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"petición inesperada a {request.url}")
    Red(handler)
    yield
    soporte.fijar_red_para_tests()


# --- los datos ---------------------------------------------------------------------------------


def _web(s: Session, **datos: Any) -> WebIndiceEntrada:
    """Una fila del índice de mboprinters.com (WPGlobus: el mismo id de
    WordPress en todos los idiomas)."""
    base: dict[str, Any] = {"sitio": "mboprinters", "dominio": "mboprinters.com",
                            "visto_en": AHORA}
    base.update(datos)
    return fila_web(s, **base)


def _desktop(s: Session, *, precio: bool = True, ingles: bool = True) -> None:
    """La MBO 6090 UV Led – Desktop: solo en la web, en castellano y en inglés,
    con su imagen principal, dos de galería y el precio de la web (o sin él)."""
    con_precio: dict[str, Any] = {
        "precio_web": Decimal("7990.00"), "precio_web_texto": PRECIO_DESKTOP,
        "precio_iva": "sin_iva", "moneda": "EUR"} if precio else {}
    _web(s, wp_id=4202, idioma="es", url=URL_DESKTOP, slug="mbo-6090-uv-led-desktop",
         titulo=TITULO_DESKTOP,
         extracto="Versión de sobremesa de la MBO 6090 para imprimir en UV sobre objetos de "
                  "hasta 15 cm de alto.",
         texto="Versión de sobremesa de la MBO 6090. Impresión UV directa sobre objetos.",
         imagen=IMG_DESKTOP, galeria_json=json.dumps(GALERIA_DESKTOP),
         categorias_json='["Impresoras UV"]', **con_precio)
    if ingles:
        en_precio = ({**con_precio, "precio_web_texto": "€7,990.00 (+ VAT)"} if precio else {})
        _web(s, wp_id=4202, idioma="en", url=URL_DESKTOP_EN, slug="mbo-6090-uv-led-desktop",
             titulo=TITULO_DESKTOP_EN,
             extracto="Desktop version of the MBO 6090 for direct UV printing on objects.",
             texto="Desktop version of the MBO 6090.", imagen=IMG_DESKTOP,
             galeria_json=json.dumps(GALERIA_DESKTOP), **en_precio)


def _indice(s: Session, **desktop: Any) -> None:
    """mboprinters.com en el índice: la MBO 6090 UV Led (la del catálogo) y la
    Desktop (solo en la web)."""
    for idioma, url, titulo in (("es", URL_6090_WEB, "MBO 6090 UV Led"),
                                ("en", "https://mboprinters.com/en/product/mbo-6090-uv-led/",
                                 "MBO 6090 UV LED")):
        _web(s, wp_id=4101, idioma=idioma, url=url, slug="mbo-6090-uv-led", titulo=titulo,
             texto="Impresora UV de 60 × 90 cm.", imagen=IMG_6090_WEB,
             precio_web=Decimal("10661.16"), precio_web_texto="10.661,16 € (+ IVA)",
             precio_iva="sin_iva", moneda="EUR")
    _desktop(s, **desktop)
    s.commit()


def _boprint(s: Session) -> None:
    """boprint.net en el índice: un producto y la página de contacto."""
    fila_web(s, sitio="boprint", dominio="boprint.net", wp_id=364, titulo="Impresora UV Led 5000U",
             url="https://boprint.net/producto/impresora-uv-led-5000u/",
             slug="impresora-uv-led-5000u", texto="Impresión UV sobre objetos A3.")
    fila_web(s, sitio="boprint", dominio="boprint.net", wp_id=12, tipo="pagina",
             titulo="Contacto", url=URL_CONTACTO, slug="contacto", texto="Escríbenos.")
    s.commit()


def _maquina(s: Session, origen_id: str, nombre: str, *, enlace: str, sitio: str,
             precio: str | None = "12.900 €", marca: str = "mbo", idioma: str = "es",
             **extra: Any) -> None:
    s.add(ProductoCatalogo(
        fuente=FUENTE_COMPOSITOR, tipo=TIPO_MAQUINA, marca=marca, origen_id=origen_id,
        nombre=nombre, descripcion=f"{nombre}: impresora UV de producción.",
        caracteristicas_json=json.dumps(["Área 60 × 90 cm", "Tinta blanca y barniz"]),
        categoria="60 × 90 cm", imagen=f"https://cdn.example/{origen_id}.jpg",
        precio_texto=precio, precio_publico=precio is not None, moneda="EUR" if precio else None,
        enlace=enlace, idioma=idioma, sitio=sitio, actualizado_origen=AHORA, visto_en=AHORA,
        **extra))


def _catalogo(s: Session) -> None:
    """La MBO 6090 (con el enlace de su página en mboprinters.com) y la
    artisJet 6090 Trust (de una web que no tiene productos en el índice)."""
    _maquina(s, "mbo-6090", "MBO 6090", enlace=URL_6090, sitio="mboprinters")
    _maquina(s, "aj-6090-trust", "artisJet 6090 Trust", enlace=URL_TRUST, sitio="artisjet-eu",
             precio="9.990 €", marca="artisjet")
    s.commit()


def _freebird(s: Session) -> None:
    """La artisJet 3000Pro Freebird del catálogo, con el enlace a la página de
    contacto de boprint.net (el caso del 28/10/2026)."""
    _maquina(s, "aj-3000pro", "artisJet 3000Pro Freebird", enlace=URL_CONTACTO, sitio="boprint",
             precio="6.490 €", marca="artisjet")
    s.commit()


def _hilo_laura(s: Session, mensaje: str, *, idioma: str = "es") -> str:
    hilo = _hilo(s, contacto=_contacto(s, LAURA, idioma=idioma),
                 mensajes=[{"de": LAURA, "texto": mensaje}], asunto="Impresora UV")
    s.commit()
    return hilo.id


def _compuesto(*productos: Any, idioma: str = "es", **extra: Any) -> dict[str, Any]:
    """Lo que devuelve la IA: un saludo, una fila de fichas y la despedida."""
    return {"modo": "compuesto", "asunto": "Re: Impresora UV", "idioma": idioma, "aviso": "",
            "pendiente": [], "bloques": [
                {"tipo": "texto", "texto": "Hola Klaus,\n\nte paso lo que te comenté:"},
                {"tipo": "productos_fila", "productos": list(productos)},
                {"tipo": "texto", "texto": "Un saludo,\nAdmin"}], **extra}


def _piezas_web(peticion: str) -> dict[str, str]:
    """`{título: ref}` de las piezas de «Nuestras webs» de la petición."""
    return {titulo: ref for ref, titulo in re.findall(r"^\[(W\d+)\] (.+)$", peticion, re.M)}


class _ClaudeQueLee(_ClaudeConPlan):
    """El plan y la redacción simulados, pero la redacción se arma con lo que
    lleva esa llamada (el orden de las referencias no se sabe antes): las del
    CATÁLOGO del contexto («- [P1] MBO 6090 · …»), las que añade la petición
    de una corrección y las piezas de «Nuestras webs» de la petición («[W1]
    MBO 6090 UV Led – Desktop»), por su nombre."""

    def __init__(self, plan: dict[str, Any], construir: Any) -> None:
        super().__init__(plan, {})
        self.construir = construir

    def __call__(self, **kw: Any) -> str:
        if kw["system_prompt"] != consulta_fuentes.PROMPT_PLAN:
            contexto, peticion = (b["text"] for b in kw["user_prompt"])
            self.respuesta = self.construir({**_refs_de_peticion(contexto),
                                             **_refs_de_peticion(peticion),
                                             **_piezas_web(peticion)})
        return super().__call__(**kw)


def _fila_productos(b: dict[str, Any]) -> list[dict[str, Any]]:
    """Los productos del correo, bloque a bloque."""
    salida: list[dict[str, Any]] = []
    for bloque in b["estructura"]["bloques"]:
        if bloque["tipo"] == bq.TIPO_PRODUCTO:
            salida.append(bloque["producto"])
        elif bloque["tipo"] == bq.TIPO_PRODUCTOS_FILA:
            salida.extend(bloque["productos"])
    return salida


def _img_enlazada(html: str, enlace: str, imagen: str) -> bool:
    """La imagen de la ficha, enlazada a su página."""
    return bool(re.search(rf'<a href="{re.escape(enlace)}"[^>]*><img src="{re.escape(imagen)}"',
                          html))


# --- (1) un producto que solo está en el índice ------------------------------------------------


def test_un_producto_que_solo_esta_en_la_web_sale_con_su_ficha_imagen_enlace_y_precio_de_la_web(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """El catálogo no tiene la Desktop; el índice, sí. La pieza de «Nuestras
    webs» vale como ficha (`W1`) y el correo la lleva como una del catálogo:
    su imagen principal enlazada, su descripción, su enlace y el precio de la
    web tal como está guardado (con su IVA, sin añadirle otro), con la frase
    del IVA en el pie."""
    falso = _ClaudeQueLee(PLAN_DESKTOP, lambda refs: _compuesto(
        refs[TITULO_DESKTOP], material_usado=[refs[TITULO_DESKTOP]]))
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Hola, ¿tenéis la MBO 6090 Desktop? ¿Qué precio tiene?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con la ficha", formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    sistema, _, peticion = _partes(falso)
    material = _material(peticion)
    assert f"[W1] {TITULO_DESKTOP}" in material and "Ficha: W1" in material
    assert (f"Precio en la web: {PRECIO_DESKTOP} (sin IVA, según la web) — NO es precio de "
            "oferta") in material
    assert f"Imagen: {IMG_DESKTOP}" in material
    assert f"Galería: {' · '.join(GALERIA_DESKTOP)}" in material
    assert "Pedida por quien envía" not in material
    assert material_fuera.REGLA_FICHAS_WEB in sistema
    b = r.json()["borrador"]
    assert b["modo"] == "compuesto"
    assert _fila_productos(b) == [{"fuente": "web", "origen_id": "mboprinters:4202"}]
    html = b["html"]
    assert _img_enlazada(html, URL_DESKTOP, IMG_DESKTOP)
    assert f'alt="{TITULO_DESKTOP}"' in html and "Versión de sobremesa de la MBO 6090" in html
    assert f'<a href="{URL_DESKTOP}" target="_blank"' in html
    # El precio de la web, tal cual: ya dice su IVA, no se le añade otro.
    assert f">{PRECIO_DESKTOP}</p>" in html and html.count("+ IVA") == 1
    assert avisos_en("es")["sin"] in html
    assert avisos_en("es")["sin_declarar"] not in html
    assert PRECIO_DESKTOP in b["texto"] and f"Más info: {URL_DESKTOP}" in b["texto"]
    # «Lo que ha usado» y el aviso: es una ficha del índice, con su enlace y su precio.
    assert b["contexto"]["fichas"] == [{
        "procedencia": "indice", "nombre": TITULO_DESKTOP, "enlace": URL_DESKTOP,
        "dominio": "mboprinters.com", "sitio": "mboprinters", "origen_id": "mboprinters:4202",
        "precio_texto": PRECIO_DESKTOP, "precio_origen": "web", "precio_iva": "sin_iva",
        "idioma": "es", "sin_idioma_pedido": False, "imagen": IMG_DESKTOP,
        "galeria": [IMG_DESKTOP, *GALERIA_DESKTOP]}]
    assert (f"Fichas del correo: «{TITULO_DESKTOP}», del índice de mboprinters.com "
            f"({URL_DESKTOP}; precio de la web: {PRECIO_DESKTOP}). Las del índice no están en "
            "el catálogo: revisa su precio y su descripción antes de enviar.") in b["aviso"]
    assert "ya no está" not in b["aviso"]
    [web] = b["contexto"]["material"]["webs"]
    assert (web["ref"], web["ficha"], web["wp_id"], web["pedida"], web["usado"]) == (
        "W1", True, 4202, False, True)


# --- (2) un producto que está en los dos sitios ------------------------------------------------


def test_un_producto_del_catalogo_y_de_la_web_va_con_la_ficha_del_catalogo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """La MBO 6090 está en el catálogo y su página en el índice: manda el
    catálogo (precio de venta, características). La página de la web no se
    ofrece como ficha (ni la que sale en la búsqueda ni la que pide quien
    envía por su enlace, con o sin la barra final), y la ficha es la del
    catálogo, con su precio."""
    falso = _ClaudeQueLee({**PLAN_DESKTOP, "webs": "MBO 6090 UV Led"},
                          lambda refs: _compuesto(refs["MBO 6090"]))
    with session_factory() as s:
        _indice(s)
        _catalogo(s)
        thread_id = _hilo_laura(s, "Hola, me interesa la MBO 6090. ¿Qué precio tiene?")
    instruccion = f"mándale la ficha de la MBO 6090 ({URL_6090})"
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion=instruccion, formato="compuesto", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    material = _material(_partes(falso)[2])
    piezas = _piezas_web(material)
    assert "MBO 6090 UV Led" in piezas
    pieza = material.split(f"[{piezas['MBO 6090 UV Led']}] MBO 6090 UV Led\n", 1)[1]
    pieza = pieza.split("\n[W", 1)[0]
    assert "Ficha:" not in pieza and "En el CATÁLOGO: sí" in pieza
    assert "Pedida por quien envía" not in pieza
    b = r.json()["borrador"]
    m = b["contexto"]["material"]
    seis = next(w for w in m["webs"] if w["titulo"] == "MBO 6090 UV Led")
    assert "ficha" not in seis
    # La pedida por su enlace es la del catálogo: se encontró, pero no va como pieza.
    assert m["fichas_pedidas"] == [{"url": URL_6090, "encontrada": True, "catalogo": True,
                                    "titulo": "MBO 6090"}]
    assert not any(w.get("pedida") for w in m["webs"])
    assert _fila_productos(b) == [{"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-6090"}]
    assert "12.900 €" in b["html"] and "10.661,16" not in b["html"]
    assert _img_enlazada(b["html"], URL_6090, "https://cdn.example/mbo-6090.jpg")
    assert b["contexto"]["fichas"] == [{
        "procedencia": "catalogo", "nombre": "MBO 6090", "enlace": URL_6090, "marca": "mbo",
        "origen_id": "mbo-6090", "precio_texto": "12.900 €", "precio_origen": "catalogo"}]
    # Sin fichas del índice, el aviso no cambia.
    assert "Fichas del correo" not in (b["aviso"] or "")
    assert "no lleva a una página" not in (b["aviso"] or "")


def test_si_la_ia_pone_la_ficha_de_la_web_de_un_producto_del_catalogo_va_la_del_catalogo() -> None:
    """La red de debajo: una ficha `W#` cuya página es la de un producto del
    catálogo (con o sin `www.`, el prefijo de idioma o la barra final) es la
    misma: va la del catálogo, con su precio de venta, y el aviso lo dice. Una
    variante que solo está en la web (otra página) va como ficha de la web."""
    refs = ia.Referencias.desde(
        maquinas=[{"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-6090", "enlace": URL_6090}],
        consumibles=[], enlaces=[], videos=[], marcas={})
    refs.anadir_producto_web("W2", {"fuente": "web", "origen_id": "mboprinters:4101"},
                             "https://www.mboprinters.com/en/product/mbo-6090-uv-led/")
    refs.anadir_producto_web("W3", {"fuente": "web", "origen_id": "mboprinters:4202"},
                             URL_DESKTOP)
    assert refs.equivalentes == {"W2": "P1"}
    res = ia.estructura_desde_ia([{"tipo": "productos_fila", "productos": ["W2", "W3"]}], refs,
                                 idioma="es")
    [fila] = res.estructura["bloques"]
    assert fila["productos"] == [{"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-6090"},
                                 {"fuente": "web", "origen_id": "mboprinters:4202"}]
    assert res.avisos == ["La ficha de la web «W2» es el mismo producto que «P1» del catálogo: "
                          "va la del catálogo, con su precio de venta."]
    # Un precio para la ficha del catálogo no se usa: el del catálogo no se cambia.
    res = ia.estructura_desde_ia([{"tipo": "producto",
                                   "producto": {"ref": "P1", "precio": "9.000 €"}}], refs,
                                 idioma="es")
    assert res.estructura["bloques"][0]["producto"] == {"fuente": FUENTE_COMPOSITOR,
                                                       "origen_id": "mbo-6090"}
    assert "el precio de una ficha del catálogo sale del catálogo y no se cambia" in res.avisos[0]
    # Una W que ya no está en las referencias (la numeración de otro turno): no sale.
    res = ia.estructura_desde_ia([{"tipo": "producto", "producto": "W9"}], refs, idioma="es")
    assert res.estructura["bloques"] == []
    assert "La IA nombró un producto que no está en el catálogo («W9»)" in res.avisos[0]


# --- (3) la variante que pide quien envía -----------------------------------------------------


def test_la_variante_que_pide_quien_envia_y_solo_esta_en_la_web_va_junto_a_las_del_catalogo(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """«Incluye la MBO 6090 versión desktop»: el catálogo tiene la MBO 6090 (y
    la Trust), no la Desktop. La pieza de la Desktop (pedida por su enlace y
    por su nombre: una sola) va la primera y lo dice; la ficha de la web va en
    la fila al lado de la Trust del catálogo, cada una con su enlace."""
    instruccion = (f"incluye la MBO 6090 versión desktop junto a la artisJet 6090 Trust: "
                   f"{URL_DESKTOP.rstrip('/')}")
    falso = _ClaudeQueLee({**PLAN_PEDIDA, "webs": "MBO 6090"}, lambda refs: _compuesto(
        refs[TITULO_DESKTOP], refs["artisJet 6090 Trust"]))
    with session_factory() as s:
        _indice(s)
        _catalogo(s)
        thread_id = _hilo_laura(s, "Hola, me interesa la MBO 6090. ¿Qué precio tiene?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion=instruccion, formato="compuesto", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    [plan] = falso.planes
    assert "incluye la MBO 6090 versión desktop" in plan["user"]
    material = _material(_partes(falso)[2])
    assert material.count(f"] {TITULO_DESKTOP}\n") == 1
    pieza = material.split(f"[W1] {TITULO_DESKTOP}\n", 1)[1].split("\n[W", 1)[0]
    assert "Ficha: W1" in pieza and "Pedida por quien envía: sí" in pieza
    b = r.json()["borrador"]
    m = b["contexto"]["material"]
    assert m["webs"][0]["ref"] == "W1" and m["webs"][0]["pedida"] is True
    assert m["consulta"]["productos"] == ["MBO 6090 desktop"]
    assert {"url": URL_DESKTOP.rstrip("/"), "encontrada": True,
            "titulo": TITULO_DESKTOP} in m["fichas_pedidas"]
    assert _fila_productos(b) == [{"fuente": "web", "origen_id": "mboprinters:4202"},
                                  {"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"}]
    [fila] = [x for x in b["estructura"]["bloques"] if x["tipo"] == bq.TIPO_PRODUCTOS_FILA]
    assert len(fila["productos"]) == 2
    html = b["html"]
    assert _img_enlazada(html, URL_DESKTOP, IMG_DESKTOP)
    assert _img_enlazada(html, URL_TRUST, IMG_TRUST)
    assert PRECIO_DESKTOP in html and "9.990 €" in html
    assert f'href="{URL_6090}"' not in html and "12.900 €" not in html
    assert "mismo producto" not in (b["aviso"] or "")
    # (7) «Lo que ha usado» dice de dónde sale cada una, con su enlace.
    fichas_correo = b["contexto"]["fichas"]
    assert [(f["procedencia"], f["nombre"], f["enlace"], f["precio_origen"])
            for f in fichas_correo] == [
        ("indice", TITULO_DESKTOP, URL_DESKTOP, "web"),
        ("catalogo", "artisJet 6090 Trust", URL_TRUST, "catalogo")]
    assert fichas_correo[0]["dominio"] == "mboprinters.com"
    assert fichas_correo[1]["marca"] == "artisjet" and "dominio" not in fichas_correo[1]
    assert (f"Fichas del correo: «{TITULO_DESKTOP}», del índice de mboprinters.com "
            f"({URL_DESKTOP}; precio de la web: {PRECIO_DESKTOP}); «artisJet 6090 Trust», del "
            f"catálogo ({URL_TRUST}).") in b["aviso"]
    # La web de la Trust no tiene productos en el índice: no se juzga su enlace.
    assert "no lleva a una página" not in b["aviso"]
    assert "enlace_no_producto" not in fichas_correo[1]


# --- (4) sin precio en la web -------------------------------------------------------------------


def test_sin_precio_en_la_web_la_ficha_dice_precio_a_consultar(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeQueLee(PLAN_DESKTOP, lambda refs: _compuesto(refs[TITULO_DESKTOP]))
    with session_factory() as s:
        _indice(s, precio=False)
        thread_id = _hilo_laura(s, "Hola, ¿tenéis la MBO 6090 Desktop?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con la ficha", formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    material = _material(_partes(falso)[2])
    pieza = material.split(f"[W1] {TITULO_DESKTOP}\n", 1)[1].split("\n[W", 1)[0]
    assert "Precio: a consultar (la web no da precio)" in pieza
    assert "Precio en la web" not in pieza
    b = r.json()["borrador"]
    html = b["html"]
    assert re.search(r">Precio a consultar</p>", html) and "Precio a consultar" in b["texto"]
    assert not re.search(r"\d[\d.,]*\s*€", html)                     # ninguna cifra
    # Sin precio, sin las frases del IVA en el pie.
    for clave in ("sin", "con", "sin_declarar", "vies"):
        assert avisos_en("es")[clave] not in html
    [ficha] = b["contexto"]["fichas"]
    assert (ficha["precio_texto"], ficha["precio_origen"], ficha["precio_iva"]) == (
        None, "consultar", None)
    assert "precio a consultar: la web no da precio" in b["aviso"]


# --- (5) el precio que dice quien envía --------------------------------------------------------


def test_un_precio_que_dice_quien_envia_se_usa_y_uno_que_no_dice_no(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """«Pon la Desktop a 14.900 € + IVA»: la IA lo pone a la ficha
    (`{"ref": "W1", "precio": …}`) y vale, porque sale de las instrucciones; el
    aviso dice de dónde sale. Un precio que no ha dicho nadie no se usa: la
    ficha lleva el de la web y el aviso lo cuenta."""
    instruccion = "contéstale con la ficha de la Desktop y pónsela a 14.900 € + IVA"
    falso = _ClaudeQueLee(PLAN_DESKTOP, lambda refs: _compuesto(
        {"ref": refs[TITULO_DESKTOP], "precio": "14.900 € + IVA"}))
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Hola, ¿tenéis la MBO 6090 Desktop?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion=instruccion, formato="compuesto", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    assert "{\"ref\": \"W2\", \"precio\"" in _partes(falso)[0]           # la regla lo explica
    b = r.json()["borrador"]
    assert _fila_productos(b) == [{"fuente": "web", "origen_id": "mboprinters:4202",
                                   "precio": "14.900 € + IVA"}]
    html = b["html"]
    assert ">14.900 € + IVA</p>" in html and "7.990" not in html
    # Su IVA es el que dice su precio: el pie no lo da por pendiente.
    assert avisos_en("es")["sin"] in html and avisos_en("es")["sin_declarar"] not in html
    [ficha] = b["contexto"]["fichas"]
    assert (ficha["precio_texto"], ficha["precio_origen"], ficha["precio_iva"]) == (
        "14.900 € + IVA", "usuario", None)
    assert (f"«{TITULO_DESKTOP}»: precio que me has indicado tú, no sale de la web ni del "
            "catálogo.") in b["aviso"]
    assert "precio que me has indicado tú: 14.900 € + IVA" in b["aviso"]
    # Un precio que no sale de las instrucciones (deducido, inventado): no se usa.
    falso.construir = lambda refs: _compuesto({"ref": refs[TITULO_DESKTOP], "precio": "6.500 €"})
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con la ficha de la Desktop",
                   formato="compuesto", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert _fila_productos(b) == [{"fuente": "web", "origen_id": "mboprinters:4202"}]
    assert PRECIO_DESKTOP in b["html"] and "6.500" not in b["html"]
    assert ("La IA puso a la ficha «W1» un precio («6.500 €») que no sale de tus instrucciones: "
            "no se usa (va el de la web, o «precio a consultar»).") in b["aviso"]
    assert "precio que me has indicado tú" not in b["aviso"]
    assert b["contexto"]["fichas"][0]["precio_origen"] == "web"


# --- (6) el idioma de la ficha ----------------------------------------------------------------


def test_la_ficha_va_en_el_idioma_del_correo_si_la_web_lo_tiene(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeQueLee(PLAN_PEDIDA, lambda refs: _compuesto(refs[TITULO_DESKTOP_EN],
                                                               idioma="en"))
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Hi, do you have the MBO 6090 Desktop? What does it cost?",
                                idioma="en")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="answer with the desktop card",
                   formato="compuesto", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    material = _material(_partes(falso)[2])
    assert f"[W1] {TITULO_DESKTOP_EN}\nEnlace: {URL_DESKTOP_EN}" in material
    assert "Idioma:" not in material
    b = r.json()["borrador"]
    html = b["html"]
    assert f'alt="{TITULO_DESKTOP_EN}"' in html and "Desktop version of the MBO 6090" in html
    assert _img_enlazada(html, URL_DESKTOP_EN, IMG_DESKTOP)
    assert "€7,990.00 (+ VAT)" in html and ">More info</span>" in html
    assert URL_DESKTOP not in html and TITULO_DESKTOP not in html
    [ficha] = b["contexto"]["fichas"]
    assert (ficha["idioma"], ficha["sin_idioma_pedido"], ficha["enlace"]) == (
        "en", False, URL_DESKTOP_EN)
    assert "la web no la tiene" not in b["aviso"]


def test_si_la_web_no_tiene_la_ficha_en_el_idioma_del_correo_va_en_la_que_hay_y_se_dice(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Un correo en francés: la Desktop está en castellano y en inglés. Va la
    del idioma por defecto de la web (castellano), y la pieza y el aviso lo
    dicen."""
    falso = _ClaudeQueLee(PLAN_PEDIDA, lambda refs: _compuesto(refs[TITULO_DESKTOP],
                                                               idioma="fr"))
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Bonjour, avez-vous la MBO 6090 Desktop ? Quel est son prix ?",
                                idioma="fr")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="réponds avec la fiche", formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    material = _material(_partes(falso)[2])
    assert f"[W1] {TITULO_DESKTOP}\nEnlace: {URL_DESKTOP}" in material
    assert "Idioma: es (no está en el índice en fr)" in material
    b = r.json()["borrador"]
    assert b["idioma"] == "fr"
    assert _img_enlazada(b["html"], URL_DESKTOP, IMG_DESKTOP)
    [ficha] = b["contexto"]["fichas"]
    assert (ficha["idioma"], ficha["sin_idioma_pedido"]) == ("es", True)
    assert (f"La ficha de «{TITULO_DESKTOP}» va en castellano: no está en el índice en "
            "francés.") in b["aviso"]


# --- (8) el enlace del catálogo que no es un producto ------------------------------------------


def _hilo_freebird(s: Session) -> str:
    _boprint(s)
    _catalogo(s)
    _freebird(s)
    return _hilo_laura(s, "Hola, ¿qué precio tienen la artisJet 3000Pro Freebird y la artisJet "
                          "6090 Trust?")


def test_una_ficha_del_catalogo_con_un_enlace_que_no_es_un_producto_se_avisa_y_no_se_corrige(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """La ficha de la Freebird apunta a la página de contacto de boprint.net,
    que tiene productos en el índice: el borrador lo avisa y «Lo que ha usado»
    lo marca, pero la ficha va con su enlace (el catálogo se edita en el
    compositor). La Trust es de una web sin productos en el índice: no se
    juzga."""
    falso = _ClaudeQueLee(PLAN_SIN_WEBS, lambda refs: _compuesto(
        refs["artisJet 3000Pro Freebird"], refs["artisJet 6090 Trust"]))
    with session_factory() as s:
        thread_id = _hilo_freebird(s)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con las fichas", formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    aviso = procedencia.aviso_enlace_no_producto("artisJet 3000Pro Freebird", URL_CONTACTO)
    assert aviso == ("El enlace de la ficha «artisJet 3000Pro Freebird» del catálogo "
                     f"({URL_CONTACTO}) va a una página de servicio (contacto, categoría, "
                     "carrito…), no a la de la máquina: revísalo en el compositor.")
    assert aviso in b["aviso"] and b["aviso"].count("página de servicio") == 1
    assert "Fichas del correo" not in b["aviso"]                  # todas son del catálogo
    assert f'href="{URL_CONTACTO}"' in b["html"]                  # no se corrige
    freebird, trust = b["contexto"]["fichas"]
    assert freebird["enlace_no_producto"] is True and freebird["enlace"] == URL_CONTACTO
    assert "enlace_no_producto" not in trust
    assert b["contexto"]["enlaces_no_producto"] == [
        {"nombre": "artisJet 3000Pro Freebird", "enlace": URL_CONTACTO, "motivo": "servicio"}]
    # Con «Webs» desmarcada no se comprueba nada.
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con las fichas", formato="compuesto",
                   fuentes=NINGUNA)
    assert r.status_code == 201, r.text
    assert len(falso.planes) == 1                                  # ni plan
    b = r.json()["borrador"]
    assert f'href="{URL_CONTACTO}"' in b["html"]
    assert "no lleva a una página" not in (b["aviso"] or "")
    assert "fichas" not in b["contexto"] and "enlaces_no_producto" not in b["contexto"]


def test_un_correo_de_texto_que_usa_el_enlace_que_no_es_un_producto_lo_avisa(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    def _texto(enlace: str | None) -> dict[str, Any]:
        cuerpo = ("<p>Hola Klaus,</p><p>La artisJet 3000Pro Freebird cuesta 6.490 €"
                  + (f": <a href='{enlace}'>aquí la tienes</a>" if enlace else "")
                  + ".</p><p>Un saludo,<br>Admin</p>")
        return {"asunto": "Re: Impresora UV", "idioma": "es", "aviso": "", "pendiente": [],
                "enlaces_usados": [enlace] if enlace else [], "cuerpo": cuerpo}

    aviso = procedencia.aviso_enlace_no_producto("artisJet 3000Pro Freebird", URL_CONTACTO)
    falso = _ClaudeConPlan(PLAN_SIN_WEBS, _texto(URL_CONTACTO))
    with session_factory() as s:
        thread_id = _hilo_freebird(s)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con el precio", formato="normal",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert b["modo"] == "normal" and f'href="{URL_CONTACTO}"' in b["html"]
    assert aviso in b["aviso"]
    assert "fichas" not in b["contexto"]
    # El mismo correo sin ese enlace: nada que avisar (la lista sigue en el contexto).
    falso.respuesta = _texto(None)
    with patch.object(llm, "_invoke_claude", falso):
        b = _pedir(http, thread_id, instruccion="contéstale con el precio", formato="normal",
                   fuentes=AMBAS).json()["borrador"]
    assert "no lleva a una página" not in (b["aviso"] or "")
    assert b["contexto"]["enlaces_no_producto"][0]["enlace"] == URL_CONTACTO
    # Y con «Webs» desmarcada, aunque lo use, tampoco.
    falso.respuesta = _texto(URL_CONTACTO)
    with patch.object(llm, "_invoke_claude", falso):
        b = _pedir(http, thread_id, instruccion="contéstale con el precio", formato="normal",
                   fuentes=NINGUNA).json()["borrador"]
    assert f'href="{URL_CONTACTO}"' in b["html"]
    assert "no lleva a una página" not in (b["aviso"] or "")


# --- (9) la comprobación del Cuadre ------------------------------------------------------------


def _ctx(s: Session) -> Contexto:
    return Contexto(s, ahora=AHORA, config=normalizar_config({"checks": {}}))


def test_el_cuadre_avisa_de_las_fichas_del_catalogo_cuyo_enlace_no_es_un_producto(
    s: Session,
) -> None:
    """La Freebird (la página de contacto) y un enlace a una categoría de
    boprint.net disparan; la MBO 6090 (su página, con `www.`, el prefijo de
    idioma y sin la barra) no; la Trust es de una web sin productos en el
    índice: no se juzga y queda en `no_mirados`. Un producto descartado, uno
    oculto, uno que ya no está y uno con un enlace fuera de nuestras webs no se
    miran."""
    _indice(s)
    _boprint(s)
    _catalogo(s)
    _freebird(s)
    _maquina(s, "mbo-6090", "MBO 6090", idioma="en", sitio="mboprinters",
             enlace="https://www.mboprinters.com/en/product/mbo-6090-uv-led")
    _maquina(s, "bp-5000u", "Boprint 5000U", sitio="boprint",
             enlace="https://boprint.net/categoria/impresoras-uv/")
    _maquina(s, "bp-oculta", "Boprint oculta", sitio="boprint", enlace=URL_CONTACTO,
             visible=False)
    _maquina(s, "bp-premio", "Premio Boprint", sitio="boprint", enlace=URL_CONTACTO,
             descartado=True)
    _maquina(s, "bp-ida", "Boprint retirada", sitio="boprint", enlace=URL_CONTACTO, ido_en=AHORA)
    _maquina(s, "otra", "Otra", sitio="", enlace="https://ejemplo.com/contacto/")
    # Un consumible de la tienda: los consumibles se dejan fuera del índice a propósito.
    s.add(ProductoCatalogo(
        fuente=FUENTE_WOOCOMMERCE, tipo=TIPO_CONSUMIBLE, marca="Boprint", origen_id="boprint:901",
        nombre="Tinta UV blanca 1 L", enlace=URL_TINTA, idioma="es", sitio="boprint",
        precio_texto="49,00 €", visto_en=AHORA))
    s.commit()
    comp = registro()[ENLACE_CATALOGO_NO_PRODUCTO]
    assert (comp.fuente, comp.grupo, comp.severidad) == (FUENTE_MYSQL, "crm", "media")
    ctx = _ctx(s)
    hallazgos = sorted(enlace_catalogo_no_producto(ctx), key=lambda h: h.entidad_id)
    assert [h.entidad_id for h in hallazgos] == [
        "compositor:aj-3000pro:es", "compositor:bp-5000u:es"]
    freebird, categoria = hallazgos
    assert freebird.entidad_tipo == ENTIDAD_PRODUCTO_CATALOGO
    assert freebird.etiqueta == "«artisJet 3000Pro Freebird» (es)"
    assert freebird.detalle == (f"Su enlace {URL_CONTACTO} va a la página «Contacto», una página "
                                "de servicio, no a la de la máquina.")
    assert (freebird.enlace, freebird.ambito) == (URL_CONTACTO, "compositor:aj-3000pro:es")
    assert freebird.huella_datos["enlace"] == "boprint:/contacto"
    assert "compositor" in freebird.pista_de_arreglo
    # Una categoría que el índice no tiene: no es una ruta de producto.
    assert categoria.detalle == ("Su enlace https://boprint.net/categoria/impresoras-uv/ va a una "
                                 "página de servicio de boprint.net (contacto, categoría, "
                                 "carrito…), no a la de la máquina.")
    assert categoria.pista_de_arreglo.startswith("El catálogo se edita en el compositor")
    # La Trust es de una web sin nada en el índice: esa ficha no se mira.
    assert ctx.no_mirados[ENLACE_CATALOGO_NO_PRODUCTO] == {"compositor:aj-6090-trust:es"}


def test_lo_avisado_de_una_web_que_deja_de_tener_productos_en_el_indice_no_se_da_por_resuelto(
    s: Session,
) -> None:
    """Si boprint.net deja de tener nada en el índice (una pasada que no la
    leyó, la web caída), el enlace de una ficha que no casaba con nada no se
    puede juzgar: lo avisado se queda abierto. Si el enlace se arregla en el
    compositor, se resuelve."""
    _boprint(s)
    _freebird(s)
    freebird = s.scalar(select(ProductoCatalogo).where(ProductoCatalogo.origen_id == "aj-3000pro"))
    freebird.enlace = "https://boprint.net/producto/artisjet-3000pro-freebird/"   # sin indexar
    s.flush()
    comp = registro()[ENLACE_CATALOGO_NO_PRODUCTO]

    def pasada(cuando: datetime) -> dict[str, int]:
        ctx = _ctx(s)
        hallazgos = list(enlace_catalogo_no_producto(ctx))
        return cuadre_engine.aplicar(s, comp, hallazgos, cuando,
                                     no_mirados=ctx.no_mirados.get(comp.id, set()))

    assert pasada(AHORA)["nuevos"] == 1
    for fila in s.scalars(select(WebIndiceEntrada).where(WebIndiceEntrada.sitio == "boprint")):
        fila.ido_en = AHORA
    s.flush()
    assert pasada(AHORA + timedelta(days=1)) == {"hallazgos": 0, "nuevos": 0, "reabiertos": 0,
                                                 "resueltos": 0}
    [abierto] = s.scalars(select(CuadreFinding))
    assert abierto.estado == "abierto"
    for fila in s.scalars(select(WebIndiceEntrada).where(WebIndiceEntrada.sitio == "boprint")):
        fila.ido_en = None
    freebird.enlace = "https://boprint.net/producto/impresora-uv-led-5000u/"
    s.flush()
    assert pasada(AHORA + timedelta(days=2))["resueltos"] == 1


def test_el_borrador_solo_juzga_los_enlaces_de_las_fichas_del_compositor(s: Session) -> None:
    """Como el Cuadre: un consumible de la tienda no está en el índice a
    propósito, y una web sin productos en el índice no se puede juzgar."""
    _boprint(s)
    freebird = {"fuente": FUENTE_COMPOSITOR, "nombre": "artisJet 3000Pro Freebird",
                "enlace": URL_CONTACTO, "marca": "artisjet", "precio_texto": "6.490 €"}
    tinta = {"fuente": FUENTE_WOOCOMMERCE, "nombre": "Tinta UV blanca 1 L", "enlace": URL_TINTA,
             "marca": "Boprint", "precio_texto": "49,00 €"}
    trust = {"fuente": FUENTE_COMPOSITOR, "nombre": "artisJet 6090 Trust", "enlace": URL_TRUST}
    assert procedencia.enlaces_catalogo_no_producto(s, [
        freebird, {**freebird, "nombre": "La misma otra vez"}, tinta, trust,
        {"fuente": FUENTE_COMPOSITOR, "nombre": "Sin enlace", "enlace": None}]) == [
        {"nombre": "artisJet 3000Pro Freebird", "enlace": URL_CONTACTO, "motivo": "servicio"}]
    claves = [{"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-3000pro"},
              {"fuente": FUENTE_WOOCOMMERCE, "origen_id": "boprint:901"},
              {"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"}]
    estructura = bq.normalizar({"idioma": "es", "bloques": [
        {"tipo": "productos_fila", "productos": claves}]})
    productos = {"compositor|aj-3000pro": freebird, "woocommerce|boprint:901": tinta,
                 "compositor|aj-6090-trust": trust}
    fichas_correo, avisos = procedencia.fichas_del_correo(s, estructura, productos, idioma="es")
    assert [(f["procedencia"], f["nombre"], f.get("enlace_no_producto"))
            for f in fichas_correo] == [
        ("catalogo", "artisJet 3000Pro Freebird", True), ("catalogo", "Tinta UV blanca 1 L", None),
        ("catalogo", "artisJet 6090 Trust", None)]
    assert avisos == [procedencia.aviso_enlace_no_producto("artisJet 3000Pro Freebird",
                                                           URL_CONTACTO, "servicio")]


# --- (10) las piezas: URL, búsqueda, variantes, precio, ficha ----------------------------------


def test_ruta_host_y_pagina_reconocen_la_misma_pagina() -> None:
    idiomas = ("es", "en", "de", "fr")
    assert fichas.ruta_de("https://mboprinters.com/en/product/mbo-6090/", idiomas) == (
        "/product/mbo-6090")
    # Solo se quita un prefijo que es un idioma de esa web (o uno de WPML).
    assert fichas.ruta_de("https://boprint.net/uv/impresora-5000u/", ("es",)) == (
        "/uv/impresora-5000u")
    assert fichas.ruta_de("https://boprint.net/en/impresora/", ("es",)) == "/en/impresora"
    assert fichas.ruta_de("https://pimpam-vending.com/pt-pt/produto/x/", ("es",)) == "/produto/x"
    assert fichas.ruta_de("https://mboprinters.com/en/", idiomas) == "/"     # la portada
    # Decodificada, en minúsculas, sin consulta, fragmento ni barra final.
    assert fichas.ruta_de("https://mbolasers.com/es/Producto/L%C3%A1ser-CO2/?x=1#a",
                          ("es",)) == "/producto/láser-co2"
    assert fichas.ruta_de("https://mboprinters.com", idiomas) == "/"
    assert fichas.slug_de("https://mboprinters.com/product/mbo-6090/") == "mbo-6090"
    assert fichas.host_de("https://WWW.FluxLasers.ES/x") == "fluxlasers.es"
    assert fichas.host_de("no es una URL") == "" and fichas.host_de(None) == ""
    # Otro dominio de la misma web, con www, idioma y barra: la misma página.
    assert fichas.pagina("https://www.fluxlasers.eu/en/beambox-ii/") == "fluxlasers:/beambox-ii"
    assert fichas.pagina("https://fluxlasers.es/beambox-ii") == "fluxlasers:/beambox-ii"
    assert fichas.pagina("https://www.mboprinters.com/de/product/mbo-6090-uv-led") == (
        fichas.pagina(URL_6090_WEB))
    assert fichas.pagina("https://ejemplo.com/en/a/") == "ejemplo.com:/en/a"
    assert fichas.pagina("") == ""
    assert fichas.sitio_de("https://www.fluxlasers.eu/x").clave == "fluxlasers"
    assert fichas.sitio_de("https://ejemplo.com/") is None
    assert fichas.urls_de(f"incluye {URL_DESKTOP}. Y (https://boprint.net/a), «{URL_DESKTOP}»") == [
        URL_DESKTOP, "https://boprint.net/a"]
    assert fichas.urls_de(None) == []


def test_el_producto_de_una_url_por_su_ruta_o_por_su_slug(s: Session) -> None:
    _indice(s)
    _boprint(s)
    _web(s, wp_id=4303, idioma="es", url="https://mboprinters.com/product/retirada/",
         slug="retirada", titulo="Retirada", ido_en=AHORA)
    s.commit()
    # Por su ruta, desde cualquier idioma o dominio de la web.
    for url in (URL_DESKTOP, URL_DESKTOP.rstrip("/"), URL_DESKTOP_EN,
                "https://www.mboprinters.com/fr/product/mbo-6090-uv-led-desktop"):
        fila = fichas.producto_por_url(s, url)
        assert fila is not None and fila.wp_id == 4202, url
    # Por su slug, si la ruta es otra (la tienda, la categoría).
    fila = fichas.producto_por_url(s, "https://mboprinters.com/tienda/uv/mbo-6090-uv-led/")
    assert fila is not None and fila.wp_id == 4101
    # Ni una página, ni un producto que ya no está, ni otra web.
    for url in (URL_CONTACTO, "https://mboprinters.com/product/retirada/",
                "https://ejemplo.com/product/mbo-6090-uv-led/", None):
        assert fichas.producto_por_url(s, url) is None, url
    mapa = fichas.MapaProductos(s)
    assert mapa.con_productos == {"mboprinters", "boprint"}
    assert mapa.no_es_producto(URL_CONTACTO) is True
    assert mapa.no_es_producto(URL_6090) is False
    assert mapa.no_es_producto(URL_TRUST) is None              # sin productos en el índice
    assert mapa.no_es_producto("https://ejemplo.com/") is None  # no es de nuestras webs
    assert fichas.enlace_no_es_producto(s, URL_CONTACTO) is True
    assert fichas.dominios_indexados(s) == {"mboprinters", "boprint"}


def test_el_producto_por_su_nombre_lleva_todas_las_palabras(s: Session) -> None:
    _indice(s)
    [desktop] = fichas.productos_por_nombre(s, "MBO 6090 desktop", idioma="es")
    assert desktop.wp_id == 4202
    # La fila del idioma del correo la elige después `en_idioma`.
    assert fichas.en_idioma(s, desktop, "es").url == URL_DESKTOP
    assert fichas.en_idioma(s, desktop, "en").url == URL_DESKTOP_EN
    # Una vez por producto, el más ajustado primero.
    assert [f.wp_id for f in fichas.productos_por_nombre(s, "MBO 6090")] == [4101, 4202]
    assert fichas.productos_por_nombre(s, "MBO 6090 Trust") == []
    assert fichas.productos_por_nombre(s, "") == []
    assert fichas.productos_por_nombre(s, "MBO 6090", sitio="boprint") == []


def _wpml(s: Session, wp_id: int, idioma: str, *, imagen: str = "https://mbolasers.com/l.jpg",
          precio: str | None = "4990.00", titulo: str = "Láser CO2 60W") -> WebIndiceEntrada:
    return fila_web(s, sitio="mbolasers", dominio="mbolasers.com", wp_id=wp_id, idioma=idioma,
                    url=f"https://mbolasers.com/{idioma}/p/{wp_id}/", titulo=titulo,
                    imagen=imagen, precio_web=Decimal(precio) if precio else None,
                    precio_web_texto=f"{precio} €" if precio else None)


def test_las_traducciones_de_wpml_se_reconocen_por_la_imagen_y_el_precio(s: Session) -> None:
    """Con WPML cada traducción tiene su id: van juntas las de la misma imagen
    principal Y el mismo precio, una por idioma. Si en un idioma salen dos
    (variantes de potencia con la misma foto), no se elige ninguna."""
    es = _wpml(s, 965, "es")
    _wpml(s, 2839, "en", titulo="CO2 Laser 60W")
    _wpml(s, 2821, "de", titulo="CO2-Laser 60W")
    _wpml(s, 3001, "fr", titulo="Laser CO2 60W")
    _wpml(s, 3002, "fr", titulo="Laser CO2 80W")                       # dos en francés
    _wpml(s, 3100, "nl", precio="5990.00", titulo="CO2-laser 80W")      # otro precio
    _wpml(s, 3200, "nl", imagen="https://mbolasers.com/otra.jpg")       # otra imagen
    s.commit()
    assert sorted((f.idioma, f.wp_id) for f in fichas.variantes(s, es)) == [
        ("de", 2821), ("en", 2839), ("es", 965)]
    assert fichas.en_idioma(s, es, "en").wp_id == 2839
    assert fichas.en_idioma(s, es, "fr").wp_id == 965                  # el de por defecto
    # La ficha de la clave de una traducción conserva su clave aunque la fila sea otra.
    salida = fichas.fichas_por_clave(s, [{"fuente": "web", "origen_id": "mbolasers:2839"},
                                         {"fuente": FUENTE_COMPOSITOR, "origen_id": "x"},
                                         {"fuente": "web", "origen_id": "mbolasers:1"},
                                         {"fuente": "web", "origen_id": "mal"}], idioma="de")
    assert list(salida) == ["web|mbolasers:2839"]
    ficha = salida["web|mbolasers:2839"]
    assert (ficha["origen_id"], ficha["nombre"], ficha["idioma_ficha"]) == (
        "mbolasers:2839", "CO2-Laser 60W", "de")
    assert ficha["marca"] == "MBO Lasers" and ficha["idiomas_web"] == ["de", "en", "es"]


def test_precio_y_ficha_de_una_fila_del_indice(s: Session) -> None:
    _indice(s)
    desktop = s.scalar(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4202,
                                                      WebIndiceEntrada.idioma == "es"))
    assert fichas.precio_de(desktop) == PRECIO_DESKTOP                  # tal como está guardado
    desktop.precio_web_texto = None
    assert fichas.precio_de(desktop) == "7.990,00 €"
    desktop.precio_web = Decimal("12345.5")
    assert fichas.precio_de(desktop) == "12.345,50 €"
    desktop.moneda = "USD"
    assert fichas.precio_de(desktop) == "12.345,50 USD"
    desktop.precio_web = None
    assert fichas.precio_de(desktop) is None
    desktop.precio_web = Decimal("0")
    assert fichas.precio_de(desktop) is None
    s.rollback()
    filas = list(s.scalars(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4202)))
    ficha = fichas.ficha_de(filas, idioma="fr", idioma_defecto="es", marca="MBO Printers")
    assert ficha == {**ficha,
                     "origen_id": "mboprinters:4202", "fuente": "web", "tipo": "producto",
                     "nombre": TITULO_DESKTOP, "marca": "MBO Printers", "caracteristicas": [],
                     "categoria": None, "imagen": IMG_DESKTOP, "galeria": GALERIA_DESKTOP,
                     "categorias": ["Impresoras UV"], "precio_texto": PRECIO_DESKTOP,
                     "precio_publico": True, "precio_consultar": False, "precio_origen": "web",
                     "precio_iva": "sin_iva", "moneda": "EUR", "enlace": URL_DESKTOP,
                     "idioma": "fr", "idioma_ficha": "es", "idiomas_web": ["en", "es"],
                     "sin_idioma_pedido": True, "sitio": "mboprinters",
                     "dominio": "mboprinters.com"}
    assert ficha["descripcion"].startswith("Versión de sobremesa de la MBO 6090")
    # Sin idioma pedido ni por defecto, la primera por idioma.
    assert fichas.elegir(filas, "nl", None).idioma == "en"


def test_los_importes_de_quien_envia_son_los_unicos_que_valen_como_precio() -> None:
    assert ia.importes_de("Pónsela a 14.900 € + IVA") == {"14900"}
    assert ia.importes_de("14.900,00 €") == {"14900"}
    assert ia.importes_de("1.299,50 € y 489,30 €") == {"1299.50", "489.30"}
    # Solo los que llevan la moneda al lado: un modelo o una cantidad no son un precio.
    assert ia.importes_de("2 unidades a 3990 €") == {"3990"}
    assert ia.importes_de("la MBO 6090 a € 14.900") == {"14900"}
    assert ia.importes_de("incluye la MBO 6090 versión desktop") == set()
    assert ia.importes_de(None) == set()
    refs = ia.Referencias()
    refs.anotar_instrucciones("pónsela a 14.900 € + IVA")
    assert refs.precio_permitido("14.900 € + IVA") and refs.precio_permitido("14.900,00 €")
    assert refs.precio_permitido("14900 EUR")
    assert not refs.precio_permitido("15.900 €")
    # El número de un modelo de la instrucción no hace bueno un precio con esa cifra.
    modelo = ia.Referencias()
    modelo.anotar_instrucciones("incluye la MBO 6090 desktop")
    assert not modelo.precio_permitido("6.090 €")
    assert not refs.precio_permitido("14.900 € y 250 € de portes")      # 250 no lo ha dicho
    assert not refs.precio_permitido("a consultar")                     # sin importe
    assert not refs.precio_permitido("99 €")                            # ni uno que no ha dicho
    assert not ia.Referencias().precio_permitido("14.900 €")            # sin instrucciones


def test_la_clave_de_una_ficha_de_la_web_conserva_su_precio_y_su_imagen() -> None:
    web = {"fuente": "web", "origen_id": "mboprinters:4202", "precio": "  14.900 €   + IVA ",
           "imagen": GALERIA_DESKTOP[0], "otra": "x"}
    assert bq.clave_producto(web) == {"fuente": "web", "origen_id": "mboprinters:4202",
                                      "precio": "14.900 € + IVA", "imagen": GALERIA_DESKTOP[0]}
    # Las del catálogo, solo su clave (el precio sale del catálogo).
    assert bq.clave_producto({**web, "fuente": FUENTE_COMPOSITOR}) == {
        "fuente": FUENTE_COMPOSITOR, "origen_id": "mboprinters:4202"}
    # Un precio con marcas o una imagen que no es una URL no pasan; uno largo no se
    # recorta (saldría un número a medias): fuera.
    assert bq.clave_producto({**web, "precio": "<b>1 €</b>", "imagen": "foto.jpg"}) == {
        "fuente": "web", "origen_id": "mboprinters:4202"}
    largo = bq.clave_producto({**web, "precio": "9" * 100})
    assert largo is not None and "precio" not in largo
    # Normalizada, la estructura los guarda; y la IA los vuelve a ver en la corrección.
    estructura = bq.normalizar({"bloques": [{"tipo": "producto", "producto": web}]})
    producto = estructura["bloques"][0]["producto"]
    assert producto["precio"] == "14.900 € + IVA" and producto["imagen"] == GALERIA_DESKTOP[0]
    refs = ia.Referencias(productos={"W1": {"fuente": "web", "origen_id": "mboprinters:4202"}})
    assert ia.bloque_para_ia(estructura["bloques"][0], refs)["producto"] == {
        "ref": "W1", "precio": "14.900 € + IVA", "imagen": GALERIA_DESKTOP[0]}
    sin_extra = bq.normalizar({"bloques": [{"tipo": "producto", "producto": {
        "fuente": "web", "origen_id": "mboprinters:9"}}]})["bloques"][0]
    assert ia.bloque_para_ia(sin_extra, refs)["producto"] == "?mboprinters:9"


def _render(s: Session, *productos: dict[str, Any], idioma: str = "es") -> generador.Correo:
    estructura = bq.normalizar({"idioma": idioma, "bloques": [
        {"tipo": "productos_fila", "productos": list(productos)}]})
    datos = datos_mod.resolver(s, estructura, ahora=AHORA, config=CONFIG)
    return generador.generar(estructura, datos, asunto="Impresoras UV")


WEB = {"fuente": "web", "origen_id": "mboprinters:4202"}
CATALOGO = {"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-6090"}


def _fila_desktop(s: Session) -> WebIndiceEntrada:
    return s.scalar(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4202,
                                                   WebIndiceEntrada.idioma == "es"))


def test_el_precio_de_la_ficha_de_la_web_lleva_el_iva_que_dice_la_web(s: Session) -> None:
    _indice(s, ingles=False)
    _catalogo(s)
    # Con su IVA en el texto: tal cual; el pie, una sola vez para las dos fuentes «sin».
    correo = _render(s, WEB, CATALOGO)
    assert f">{PRECIO_DESKTOP}</p>" in correo.html
    assert correo.precios == [PRECIO_DESKTOP, "12.900 €"]
    assert correo.pie.count(avisos_en("es")["sin"]) == 1
    # Sin el IVA en el texto: el que dice la web («sin_iva» → «+ IVA»).
    fila = _fila_desktop(s)
    fila.precio_web_texto = None
    s.commit()
    correo = _render(s, WEB)
    assert re.search(r"7\.990,00 € <span[^>]*>\+ IVA</span>", correo.html)
    assert "7.990,00 € + IVA" in correo.texto and avisos_en("es")["sin"] in correo.pie
    fila.precio_iva = "con_iva"
    s.commit()
    correo = _render(s, WEB)
    assert "7.990,00 € IVA incl." in correo.texto and avisos_en("es")["con"] in correo.pie
    # Si la web no dice si lleva IVA: sin sufijo, y el pie dice que está por confirmar.
    fila.precio_iva = "desconocido"
    s.commit()
    correo = _render(s, WEB)
    assert "7.990,00 €\n" in correo.texto + "\n" and "IVA incl." not in correo.texto
    assert avisos_en("es")["sin_declarar"] in correo.pie


def test_sin_precio_la_ficha_de_la_web_dice_precio_a_consultar_en_su_idioma(s: Session) -> None:
    _indice(s, precio=False)
    correo = _render(s, WEB)
    assert re.search(r">Precio a consultar</p>", correo.html)
    assert correo.precios == [] and correo.pie == ""
    assert "Precio a medida" not in correo.html
    en = _render(s, WEB, idioma="en")
    assert ">Price on request</p>" in en.html and "Price on request" in en.texto


def test_el_precio_de_quien_envia_y_otra_imagen_de_la_galeria(s: Session) -> None:
    _indice(s, ingles=False)
    correo = _render(s, {**WEB, "precio": "14.900 €", "imagen": GALERIA_DESKTOP[1]})
    assert ">14.900 €</p>" in correo.html and "7.990" not in correo.html
    # Lo dice quien envía sin decir el IVA: no se le pone ninguno y el pie lo dice.
    assert correo.precios == ["14.900 €"]
    assert avisos_en("es")["sin_declarar"] in correo.pie
    assert f'<img src="{GALERIA_DESKTOP[1]}"' in correo.html
    assert f'<img src="{IMG_DESKTOP}"' not in correo.html
    # Una imagen que no es de su galería no se pone: va la principal.
    correo = _render(s, {**WEB, "imagen": "https://ejemplo.com/otra.jpg"})
    assert f'<img src="{IMG_DESKTOP}"' in correo.html and "ejemplo.com" not in correo.html
    # Si su precio dice el IVA, el pie dice ese.
    for precio, iva in (("14.900 € + IVA", "sin"), ("14.900 € IVA incluido", "con")):
        correo = _render(s, {**WEB, "precio": precio})
        assert f">{precio}</p>" in correo.html, precio
        assert avisos_en("es")[iva] in correo.pie, precio
        assert avisos_en("es")["sin_declarar"] not in correo.pie, precio


def test_una_ficha_de_la_web_que_ya_no_esta_en_el_indice_no_sale_y_se_avisa(s: Session) -> None:
    _indice(s)
    for fila in s.scalars(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4202)):
        fila.ido_en = AHORA
    s.commit()
    correo = _render(s, WEB)
    assert TITULO_DESKTOP not in correo.html
    assert correo.avisos == ["El producto «mboprinters:4202» ya no está en el índice de las "
                             "webs: no sale en el correo."]


# --- (11) una corrección que pide incluir un producto ------------------------------------------


def test_una_correccion_que_pide_la_version_desktop_la_busca_en_el_indice_y_la_trae(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """El primer borrador lleva la MBO 6090 del catálogo. «Incluye tbn la MBO
    6090 versión desktop» pide un producto: se le pregunta al plan cuál (una
    llamada más) y la pieza de la Desktop, pedida, entra en la corrección;
    la IA la pone en la fila. «Añade el enlace a la máquina» no pide ninguno:
    ni plan ni búsqueda."""
    with session_factory() as s:
        _indice(s)
        _catalogo(s)
        thread_id = _hilo_laura(s, "Hola, me interesa la MBO 6090. ¿Qué precio tiene?")
    falso = _ClaudeQueLee(PLAN_SIN_WEBS, lambda refs: _compuesto(refs["MBO 6090"]))
    with patch.object(llm, "_invoke_claude", falso):
        primero = _pedir(http, thread_id, instruccion="contéstale con la ficha",
                         formato="compuesto", fuentes=AMBAS).json()["borrador"]
    assert len(falso.planes) == 1 and "NUESTRAS WEBS" not in _partes(falso)[2]
    [fila] = [x for x in primero["estructura"]["bloques"] if x["tipo"] == bq.TIPO_PRODUCTO]
    falso.plan = PLAN_PEDIDA
    falso.construir = lambda refs: {
        "modo": "compuesto", "asunto": "Re: Impresora UV", "aviso": "", "pendiente": [],
        "operaciones": [{"op": "reemplazar", "id": fila["id"], "bloque": {
            "tipo": "productos_fila", "productos": [refs["MBO 6090"], refs[TITULO_DESKTOP]]}}]}
    with patch.object(llm, "_invoke_claude", falso):
        r = _turno(http, primero["id"], "incluye tbn la MBO 6090 versión desktop",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    assert len(falso.planes) == 2
    assert "incluye tbn la MBO 6090 versión desktop" in falso.planes[-1]["user"]
    material = _material(_partes(falso)[2])
    pieza = material.split(f"[W1] {TITULO_DESKTOP}\n", 1)[1].split("\n[W", 1)[0]
    assert "Ficha: W1" in pieza and "Pedida por quien envía: sí" in pieza
    b = r.json()["borrador"]
    assert _fila_productos(b) == [CATALOGO, WEB]
    assert _img_enlazada(b["html"], URL_DESKTOP, IMG_DESKTOP)
    assert _img_enlazada(b["html"], URL_6090, "https://cdn.example/mbo-6090.jpg")
    assert [f["procedencia"] for f in b["contexto"]["fichas"]] == ["catalogo", "indice"]
    m = b["contexto"]["material"]
    assert m["webs"][0]["pedida"] is True and m["consulta"]["reutilizado"] is True
    # Una corrección que no pide ningún producto: ni plan ni pieza nueva.
    falso.construir = lambda refs: {"modo": "compuesto", "asunto": "Re: Impresora UV",
                                    "aviso": "", "pendiente": [], "operaciones": []}
    with patch.object(llm, "_invoke_claude", falso):
        r = _turno(http, b["id"], "añade el enlace a la máquina", fuentes=AMBAS)
    assert r.status_code == 201, r.text
    assert len(falso.planes) == 2


def test_que_correcciones_piden_un_producto() -> None:
    for instruccion in ("incluye tbn la MBO 6090 versión desktop", "añade la ficha de la Lyra",
                        f"mira esta: {URL_DESKTOP}", "añade la 6090",
                        "include the desktop variant"):
        assert consulta_fuentes.pide_producto(instruccion), instruccion
    for instruccion in ("añade el enlace a la máquina", "añade los pasos", "más corto",
                        "pon un saludo más cercano", "", None):
        assert not consulta_fuentes.pide_producto(instruccion), instruccion


# --- arreglos de después de los tests ------------------------------------------------------


def test_una_fila_que_se_queda_con_una_ficha_conserva_el_precio_de_quien_envia(
        s: Session) -> None:
    """Una fila de dos donde una ficha ya no está se pinta como ficha suelta:
    la de la web sigue con el precio que dijo quien envía."""
    _indice(s, ingles=False)
    estructura = {"idioma": "es", "bloques": [{"id": "b1", "tipo": "productos_fila", "productos": [
        {**WEB, "precio": "14.900 € + IVA"}, {"fuente": "web", "origen_id": "mboprinters:999"}]}]}
    datos = datos_mod.resolver(s, estructura, ahora=AHORA, config=CONFIG)
    correo = generador.generar(estructura, datos, asunto="Impresoras UV")
    assert ">14.900 € + IVA</p>" in correo.html and "7.990" not in correo.html


def test_las_traducciones_no_se_mezclan_con_otro_producto_de_la_misma_foto(s: Session) -> None:
    """Con el mismo id en varios idiomas (WPGlobus) no se agrupa por la foto;
    y sin precio no hay con qué reconocer una traducción: la Desktop solo en
    castellano no se queda con la fila inglesa de la MBO 6090."""
    for idioma, wp_id, titulo in (("es", 4101, "MBO 6090 UV Led"), ("en", 4101, "MBO 6090 UV LED"),
                                  ("es", 4202, "MBO 6090 UV Led – Desktop")):
        _web(s, wp_id=wp_id, idioma=idioma, titulo=titulo, imagen=IMG_6090_WEB,
             url=f"https://mboprinters.com/{'en/' if idioma == 'en' else ''}product/{wp_id}/")
    s.commit()
    desktop = s.scalar(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4202))
    assert [f.wp_id for f in fichas.variantes(s, desktop)] == [4202]
    normal = s.scalar(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 4101,
                                                     WebIndiceEntrada.idioma == "es"))
    assert {(f.wp_id, f.idioma) for f in fichas.variantes(s, normal)} == {(4101, "es"),
                                                                           (4101, "en")}


def test_el_nombre_pedido_da_la_fila_del_idioma_del_correo(s: Session) -> None:
    _indice(s)
    encontradas = fichas.productos_por_nombre(s, "MBO 6090 desktop", idioma="es")
    assert [(f.wp_id, f.idioma) for f in encontradas] == [(4202, "es")]
    encontradas = fichas.productos_por_nombre(s, "MBO 6090 desktop", idioma="en")
    assert [(f.wp_id, f.idioma) for f in encontradas] == [(4202, "en")]


def test_un_enlace_que_no_es_producto_solo_se_avisa_si_se_pedia_su_ficha(s: Session) -> None:
    _boprint(s)
    s.commit()
    for instruccion, avisa in (
            ("ponle el enlace a la página de contacto: https://boprint.net/contacto/", False),
            ("incluye la ficha de https://boprint.net/contacto/", True),
            ("añade https://boprint.net/producto/no-existe/", True)):
        resumen: dict[str, Any] = {}
        avisos: list[str] = []
        consulta_fuentes._productos_pedidos(
            s, instruccion=instruccion, nombres=[], idioma="es",
            catalogo=consulta_fuentes._Catalogo(), resumen=resumen, avisos=avisos)
        assert bool(avisos) is avisa, instruccion


# --- la ronda de revisión ----------------------------------------------------------------------


def test_los_importes_en_formato_ingles_se_leen_enteros() -> None:
    """«€14,900» es catorce mil novecientos, no 14,90; y «14,900 €» no deja
    pasar un «900 €» que sí dijo quien envía."""
    assert ia.importes_de("include the desktop at €14,900 + VAT") == {"14900"}
    assert ia.importes_de("14,900 €") == {"14900"}
    assert ia.importes_de("1,299.50 €") == {"1299.50"}
    assert ia.importes_de("€ 14 900") == {"14900"}
    assert ia.importes_de("14,90 €") == {"14.90"}                       # decimales a la española
    refs = ia.Referencias()
    refs.anotar_instrucciones("include the desktop at €14,900 + VAT")
    assert refs.precio_permitido("€14,900 + VAT") and refs.precio_permitido("14.900 € + IVA")
    portes = ia.Referencias()
    portes.anotar_instrucciones("Incluye la desktop. Los portes son 900 €")
    assert not portes.precio_permitido("14,900 €")


def test_el_precio_de_quien_envia_no_puede_anadir_cifras_ni_un_iva_que_no_dijo() -> None:
    refs = ia.Referencias()
    refs.anotar_instrucciones("pónsela a 14.900 €")
    assert refs.precio_permitido("14.900 €")
    assert not refs.precio_permitido("14.900 € IVA incluido")          # un IVA que no dijo
    assert not refs.precio_permitido("14.900 € (antes 16.500)")        # una cifra que no dijo
    con_iva = ia.Referencias()
    con_iva.anotar_instrucciones("la 6090 normal a 12.000 € + IVA y la desktop a 14.900 € + 21% "
                                 "IVA")
    assert con_iva.precio_permitido("14.900 € + 21% IVA")
    assert con_iva.precio_permitido("14.900 €")                        # sin decir el IVA, vale
    # El IVA de otro importe de la instrucción no cuenta para este.
    otro = ia.Referencias()
    otro.anotar_instrucciones("la 6090 normal a 12.000 € IVA incluido; la desktop a 14.900 €")
    assert not otro.precio_permitido("14.900 € IVA incluido")


def test_un_precio_demasiado_largo_no_se_recorta() -> None:
    """Recortado a 60 caracteres saldría «…: 14»: no se usa, y se dice."""
    refs = ia.Referencias(productos={"W1": {"fuente": "web", "origen_id": "mboprinters:4202"}})
    instruccion = "ponle precio especial de feria para clientes de la zona centro: 14.900 € + IVA"
    refs.anotar_instrucciones(instruccion)
    largo = "Precio especial de feria para clientes de la zona centro: 14.900 € + IVA"
    res = ia.estructura_desde_ia([{"tipo": "producto", "producto": {"ref": "W1",
                                                                   "precio": largo}}],
                                 refs, idioma="es")
    assert res.estructura["bloques"][0]["producto"] == {"fuente": "web",
                                                       "origen_id": "mboprinters:4202"}
    assert "es demasiado largo para una ficha" in res.avisos[0]


def test_el_iva_de_un_precio_se_lee_como_el_de_la_web() -> None:
    for precio, iva in (("14.900 € + IVA incluida la instalación", "sin_iva"),
                        ("14.900 € sin IVA (18.029 € con IVA)", "sin_iva"),
                        ("14.900 € IVA no incluido", "sin_iva"), ("€14,900 plus VAT", "sin_iva"),
                        ("14.900 € exkl. MwSt", "sin_iva"), ("14.900 € netto", "sin_iva"),
                        ("14.900 € IVA incluido", "con_iva"), ("€14,900 including VAT", "con_iva"),
                        ("14.900 €", None), ("14.900 € instalación incluida", None)):
        assert generador.iva_del_texto(precio) == iva, precio


def test_un_texto_que_repite_el_precio_de_la_web_se_avisa_aparte(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """El precio de la web va en la ficha; en un texto se lee como si fuera de
    oferta. Antes la red de «no sale del catálogo» lo dejaba pasar."""
    def _con_texto(refs: dict[str, str]) -> dict[str, Any]:
        correo = _compuesto(refs[TITULO_DESKTOP])
        correo["bloques"][0]["texto"] = f"Hola Laura,\n\nte la puedo ofrecer por {PRECIO_DESKTOP}."
        return correo

    falso = _ClaudeQueLee(PLAN_DESKTOP, _con_texto)
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Hola, ¿tenéis la MBO 6090 Desktop? ¿Qué precio tiene?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion="contéstale con la ficha", formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    aviso = r.json()["borrador"]["aviso"]
    assert ("Un texto del correo menciona «7.990,00 €», el precio de la web de una ficha: no es "
            "un precio de oferta y ya va en la ficha. Quítalo del texto o revísalo.") in aviso
    assert "que no sale del catálogo" not in aviso


def test_una_ficha_de_la_web_de_una_correccion_anterior_se_puede_mover() -> None:
    """Puesta en una corrección, la ficha de la web no tiene `W#` en la
    siguiente (la IA la ve como `?<origen_id>`): reorganizar el correo no la
    pierde, y conserva el precio que dijo quien envía."""
    refs = ia.Referencias(productos={"P1": CATALOGO})
    estructura = bq.normalizar({"idioma": "es", "bloques": [
        {"id": "b1", "tipo": "producto", "producto": CATALOGO},
        {"id": "b2", "tipo": "producto", "producto": {**WEB, "precio": "14.900 € + IVA"}}]})
    assert ia.bloque_para_ia(estructura["bloques"][1], refs)["producto"] == {
        "ref": "?mboprinters:4202", "precio": "14.900 € + IVA"}
    res = ia.aplicar_operaciones(estructura, [
        {"op": "reemplazar", "id": "b1", "bloque": {
            "tipo": "productos_fila", "productos": ["P1", "?mboprinters:4202"]}},
        {"op": "quitar", "id": "b2"}], refs)
    [fila] = res.estructura["bloques"]
    assert fila["productos"] == [CATALOGO, {**WEB, "precio": "14.900 € + IVA"}]
    assert res.avisos == []
    # Un `?` que no está en el correo sigue sin valer.
    res = ia.aplicar_operaciones(estructura, [{"op": "reemplazar", "id": "b1", "bloque": {
        "tipo": "producto", "producto": "?mboprinters:9"}}], refs)
    assert "no está en el catálogo" in res.avisos[0]


def _mbo_3050(s: Session) -> None:
    """La MBO 3050: en el catálogo (8.500 €) y en mboprinters.com (6.000 €)."""
    _maquina(s, "mbo-3050", "MBO 3050", enlace="https://mboprinters.com/product/mbo-3050-uv/",
             sitio="mboprinters", precio="8.500 €")
    _web(s, wp_id=5100, idioma="es", url="https://mboprinters.com/product/mbo-3050-uv/",
         slug="mbo-3050-uv", titulo="MBO 3050 UV", texto="Impresora UV de 30 × 50 cm.",
         imagen="https://mboprinters.com/3050.jpg", precio_web=Decimal("6000.00"),
         precio_web_texto="6.000,00 € (+ IVA)", precio_iva="sin_iva", moneda="EUR")
    s.commit()


def test_un_producto_del_catalogo_que_no_esta_entre_las_maquinas_del_correo_va_del_catalogo(
    s: Session,
) -> None:
    """El CATÁLOGO del correo es una preselección: la MBO 3050 está en el
    catálogo del compositor pero no entre las máquinas del correo. Pedida en
    una corrección, no es una ficha del índice: la consulta la apunta como del
    catálogo y la corrección la trae con su `P#`, su ficha y su precio de
    venta, nunca el de la web."""
    from app.services.asistente import redactor  # noqa: PLC0415

    _indice(s)
    _catalogo(s)
    _mbo_3050(s)
    entrada = consulta_fuentes.Entrada(
        instruccion="incluye también la MBO 3050", idioma="es",
        maquinas=[{"nombre": "artisJet 6090 Trust", "enlace": URL_TRUST}])
    plan = consulta_fuentes.Plan(tecnica=False, productos=["MBO 3050"])
    mat = consulta_fuentes.consultar(
        s, fuentes=consulta_fuentes.Fuentes(webs=True, soporte=False), plan=plan, idioma="es",
        entrada=entrada)
    assert mat is not None
    assert "MBO 3050 UV" not in mat.peticion                       # ni pieza del índice
    assert mat.resumen["catalogo_pedidos"] == ["mbo-3050"]
    assert mat.resumen["fichas_pedidas"] == [{"nombre": "MBO 3050", "encontrada": True,
                                              "catalogo": True, "titulo": "MBO 3050"}]
    refs = ia.Referencias.desde(
        maquinas=[{"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust", "enlace": URL_TRUST}],
        consumibles=[], enlaces=[], videos=[], marcas={})
    bloque, pedidos = redactor._catalogo_pedido_correccion(
        s, refs, instruccion="incluye también la MBO 3050", mat=mat, plan=plan, idioma="es",
        contexto={"catalogo": {"iva_por_fuente": {"compositor": "sin"}}})
    assert [p["origen_id"] for p in pedidos] == ["mbo-3050"]
    assert bloque.startswith("CATÁLOGO · AÑADIDO EN ESTA CORRECCIÓN")
    assert "- [P2] MBO 3050 · mbo" in bloque and "PRECIO: 8.500 €" in bloque
    assert refs.productos["P2"] == {"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-3050"}
    redactor._enlaces_material_en_refs(refs, mat)
    res = ia.estructura_desde_ia([{"tipo": "productos_fila", "productos": ["P1", "P2"]}], refs,
                                 idioma="es")
    assert res.estructura["bloques"][0]["productos"] == [
        {"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"},
        {"fuente": FUENTE_COMPOSITOR, "origen_id": "mbo-3050"}]
    correo = _render(s, *res.estructura["bloques"][0]["productos"])
    assert "8.500 €" in correo.html and "6.000" not in correo.html


def test_la_pagina_de_un_producto_del_catalogo_que_sale_en_la_busqueda_saca_su_ficha(
    s: Session,
) -> None:
    """Lo que trae la búsqueda de las webs y es un producto del catálogo (por
    su página o por su nombre) se ofrece con su `W#`, que saca la ficha del
    catálogo con su precio de venta."""
    from app.services.asistente import redactor  # noqa: PLC0415

    _indice(s)
    _mbo_3050(s)
    fila = s.scalar(select(WebIndiceEntrada).where(WebIndiceEntrada.wp_id == 5100))
    fila.url = "https://mboprinters.com/product/mbo-3050-uv-nueva-pagina/"   # otra página
    s.commit()
    catalogo = consulta_fuentes._catalogo(s, [])
    fragmento = busqueda_fragmento(fila)
    del_catalogo = consulta_fuentes._del_catalogo(s, [fragmento], catalogo)
    assert del_catalogo[fragmento.id]["origen_id"] == "mbo-3050"      # por su nombre
    assert del_catalogo[fragmento.id]["en_correo"] is False
    mat = material_fuera.construir(fragmentos=[fragmento], idioma="es", del_catalogo=del_catalogo)
    assert mat is not None
    assert "En el CATÁLOGO: sí («MBO 3050»): con W1 sale su ficha del catálogo" in mat.peticion
    refs = ia.Referencias()
    redactor._enlaces_material_en_refs(refs, mat)
    res = ia.estructura_desde_ia([{"tipo": "producto", "producto": "W1"}], refs, idioma="es")
    assert res.estructura["bloques"][0]["producto"] == {"fuente": FUENTE_COMPOSITOR,
                                                       "origen_id": "mbo-3050"}


def test_con_wpml_la_pagina_de_otro_idioma_de_una_maquina_del_correo_no_es_ficha(
    s: Session,
) -> None:
    """El enlace del catálogo es la página en castellano; la búsqueda trae la
    inglesa (otro id y otro slug, la misma foto y el mismo precio): es la
    misma máquina, y su ficha es la del catálogo."""
    _wpml(s, 965, "es")
    en = _wpml(s, 2839, "en", titulo="CO2 Laser 60W")
    s.commit()
    catalogo = consulta_fuentes._catalogo(
        s, [{"nombre": "Láser CO2 60W", "enlace": "https://mbolasers.com/es/p/965/"}])
    fragmento = busqueda_fragmento(en)
    del_catalogo = consulta_fuentes._del_catalogo(s, [fragmento], catalogo)
    assert del_catalogo[fragmento.id]["en_correo"] is True
    mat = material_fuera.construir(fragmentos=[fragmento], idioma="en", del_catalogo=del_catalogo)
    assert mat is not None and "Ficha:" not in mat.peticion
    assert "En el CATÁLOGO: sí: su ficha es la del catálogo" in mat.peticion


def busqueda_fragmento(fila: WebIndiceEntrada) -> Any:
    from app.services.webs import busqueda  # noqa: PLC0415

    return busqueda.fragmento_de(fila, [], max_chars=material_fuera.MAX_CHARS_WEB)


def test_un_plan_con_un_solo_producto_como_texto_no_se_parte_en_letras(s: Session) -> None:
    plan = consulta_fuentes._plan_de_json('{"tecnica": false, "productos": "MBO 6090 desktop"}')
    assert plan is not None and plan.productos == ["MBO 6090 desktop"]
    _indice(s)
    assert fichas.productos_por_nombre(s, "M") == []                   # una letra no busca nada


def test_con_wpml_y_sin_precio_la_traduccion_se_reconoce_por_la_imagen(s: Session) -> None:
    """Los láseres «a consultar» de una web con WPML: sin precio, la misma
    imagen (una por idioma) basta. Con dos del mismo idioma con esa foto, no."""
    es = _wpml(s, 965, "es", precio=None)
    _wpml(s, 2839, "en", precio=None, titulo="CO2 Laser 60W")
    s.commit()
    assert sorted((f.idioma, f.wp_id) for f in fichas.variantes(s, es)) == [
        ("en", 2839), ("es", 965)]
    assert fichas.en_idioma(s, es, "en").wp_id == 2839
    _wpml(s, 966, "es", precio=None, titulo="Láser CO2 80W")            # la misma foto
    s.commit()
    assert [f.wp_id for f in fichas.variantes(s, es)] == [965]


def test_el_motivo_de_un_enlace_que_no_es_un_producto(s: Session) -> None:
    """Se dice lo que se sabe: una página de servicio (contacto, una
    categoría), una página que el índice ya no tiene, o una que no está en él
    (puede ser nueva). Una categoría con el mismo slug que un producto no se
    toma por el producto."""
    _boprint(s)
    fila_web(s, sitio="boprint", dominio="boprint.net", wp_id=400, titulo="Retirada",
             url="https://boprint.net/producto/retirada/", slug="retirada", ido_en=AHORA)
    s.commit()
    mapa = fichas.MapaProductos(s)
    assert mapa.motivo(URL_CONTACTO) == fichas.MOTIVO_SERVICIO
    assert mapa.titulo(URL_CONTACTO) == "Contacto"
    assert mapa.motivo("https://boprint.net/producto/retirada/") == fichas.MOTIVO_IDO
    assert mapa.motivo("https://boprint.net/producto/impresora-uv-nueva/") == fichas.MOTIVO_FUERA
    categoria = "https://boprint.net/categoria-producto/impresora-uv-led-5000u/"
    assert mapa.producto(categoria) is None and mapa.motivo(categoria) == fichas.MOTIVO_SERVICIO
    assert mapa.titulo(categoria) is None
    assert mapa.motivo("https://boprint.net/sobre-nosotros/") == fichas.MOTIVO_FUERA
    assert mapa.motivo("https://boprint.net/producto/impresora-uv-led-5000u/") is None
    assert mapa.motivo(URL_TRUST) is None                              # sin productos indexados
    assert procedencia.aviso_enlace_no_producto("X", "https://boprint.net/producto/nueva/",
                                                fichas.MOTIVO_FUERA) == (
        "El enlace de la ficha «X» del catálogo (https://boprint.net/producto/nueva/) no está en "
        "el índice de nuestras webs (ni como producto ni como página): si la página es nueva, "
        "aún no se ha leído («Reindexar ahora» en el panel del índice); si no, revísalo en el "
        "compositor.")
    # El Cuadre: el de un producto que ya no está y el de uno que aún no se ha leído.
    _maquina(s, "bp-retirada", "Retirada", sitio="boprint",
             enlace="https://boprint.net/producto/retirada/")
    _maquina(s, "bp-nueva", "Nueva", sitio="boprint",
             enlace="https://boprint.net/producto/impresora-uv-nueva/")
    s.commit()
    hallazgos = {h.entidad_id: h for h in enlace_catalogo_no_producto(_ctx(s))}
    retirada = hallazgos["compositor:bp-retirada:es"]
    # Que el índice ya no lo tenga no prueba que se quitara de la web (una
    # regla del índice también lo deja fuera): no se afirma.
    assert retirada.detalle.endswith("es el de una página que el índice ya no tiene (se quitó "
                                     "de la web o una regla del índice la deja fuera).")
    assert retirada.pista_de_arreglo.startswith("Mira si el producto sigue en la web")
    nueva = hallazgos["compositor:bp-nueva:es"]
    assert nueva.detalle == ("Su enlace https://boprint.net/producto/impresora-uv-nueva/ no está "
                             "en el índice de boprint.net: ni como producto, ni como página, ni "
                             "como entrada (o es una que aún no se ha leído).")
    assert nueva.pista_de_arreglo.startswith("Si la página se acaba de publicar")
    assert "Reindexar ahora" in nueva.pista_de_arreglo


def test_los_enlaces_del_catalogo_se_miran_con_las_casillas_de_cada_turno(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """Un primer borrador de texto sin «Webs» que enlaza la página de
    contacto: no se mira. La corrección con «Webs» marcada sí lo avisa; y una
    corrección sin ella ya no lo marca."""
    cuerpo = ("<p>Hola Klaus,</p><p>La artisJet 3000Pro Freebird cuesta 6.490 €: "
              f"<a href='{URL_CONTACTO}'>aquí la tienes</a>.</p><p>Un saludo,<br>Admin</p>")
    falso = _ClaudeConPlan(PLAN_SIN_WEBS, {
        "asunto": "Re: Impresora UV", "idioma": "es", "aviso": "", "pendiente": [],
        "enlaces_usados": [URL_CONTACTO], "cuerpo": cuerpo})
    with session_factory() as s:
        thread_id = _hilo_freebird(s)
    with patch.object(llm, "_invoke_claude", falso):
        b = _pedir(http, thread_id, instruccion="contéstale con el precio", formato="normal",
                   fuentes=NINGUNA).json()["borrador"]
        assert "enlaces_no_producto" not in b["contexto"]
        r = _turno(http, b["id"], "más corto", fuentes=AMBAS)
        assert r.status_code == 201, r.text
        b = r.json()["borrador"]
        assert procedencia.aviso_enlace_no_producto(
            "artisJet 3000Pro Freebird", URL_CONTACTO, "pagina") in b["aviso"]
        assert b["contexto"]["enlaces_no_producto"][0]["enlace"] == URL_CONTACTO
        b = _turno(http, b["id"], "más corto", fuentes=NINGUNA).json()["borrador"]
    assert "enlaces_no_producto" not in b["contexto"]
    assert "no lleva a una página" not in (b["aviso"] or "")


def test_las_fichas_del_catalogo_dicen_su_precio_como_el_correo(s: Session) -> None:
    """Sin precio público, el correo dice «a medida»; un orientativo lleva su
    nota: «Lo que ha usado» lo sabe."""
    claves = [{"fuente": FUENTE_COMPOSITOR, "origen_id": "a"},
              {"fuente": FUENTE_COMPOSITOR, "origen_id": "b"}]
    estructura = bq.normalizar({"idioma": "es", "bloques": [
        {"tipo": "productos_fila", "productos": claves}]})
    productos = {"compositor|a": {"nombre": "A medida", "precio_texto": None,
                                  "precio_publico": False},
                 "compositor|b": {"nombre": "Orientativa", "precio_texto": "9.990 €",
                                  "orientativo": True}}
    fichas_correo, _ = procedencia.fichas_del_correo(s, estructura, productos, idioma="es")
    assert fichas_correo[0]["precio_publico"] is False and "orientativo" not in fichas_correo[0]
    assert fichas_correo[1]["orientativo"] is True and "precio_publico" not in fichas_correo[1]


def test_un_producto_pedido_por_su_nombre_que_no_esta_en_el_indice_se_avisa(s: Session) -> None:
    _indice(s)
    for nombres, avisa in ((["Beamo Desktop"], True), (["Trust"], False),
                           (["MBO 6090 desktop"], False)):
        avisos: list[str] = []
        consulta_fuentes._productos_pedidos(s, instruccion="", nombres=nombres, idioma="es",
                                            catalogo=consulta_fuentes._Catalogo(), resumen={},
                                            avisos=avisos,
                                            del_correo_nombres=["artisJet 6090 Trust"])
        assert bool(avisos) is avisa, nombres
    assert avisos == [] and "No he encontrado" not in "".join(avisos)
    avisos = []
    consulta_fuentes._productos_pedidos(s, instruccion="", nombres=["Beamo Desktop"],
                                        idioma="es", catalogo=consulta_fuentes._Catalogo(),
                                        resumen={}, avisos=avisos)
    assert avisos[0].startswith("No he encontrado «Beamo Desktop» en el índice de las webs")


# --- la comprobación de los arreglos ---------------------------------------------------------


def test_el_iva_solo_cuenta_con_el_impuesto_al_lado() -> None:
    """«Portes no incluidos», «envío excluido» o «precio exclusivo» no dicen
    nada del IVA: el pie no contradice a la ficha."""
    for precio, iva in (("14.900 € IVA incluido, portes no incluidos", "con_iva"),
                        ("14.900 € IVA incluido (envío excluido)", "con_iva"),
                        ("Precio exclusivo: 14.900 € IVA incluido", "con_iva"),
                        ("14.900 € más IVA", "sin_iva"), ("14.900 €, IVA aparte", "sin_iva"),
                        ("14.900 € +IVA", "sin_iva"), ("14.900 € HT", "sin_iva"),
                        ("14.900 € TTC", "con_iva"), ("14.900 €, montaje no incluido", None)):
        assert generador.iva_del_texto(precio) == iva, precio


def test_el_precio_de_quien_envia_con_el_iva_en_otra_parte_de_la_frase() -> None:
    for instruccion, precio in (
            ("pónsela a 14.900 € IVA incluido; el transporte no incluido",
             "14.900 € IVA incluido"),
            ("contéstale con la Desktop a 14.900 € y dile que la normal sale a 12.000 €, ambas "
             "+ IVA", "14.900 € + IVA"),
            ("IVA aparte, pónsela a 14.900 €", "14.900 € + IVA"),
            ("pónsela a 14.900 € más IVA", "14.900 € + IVA")):
        refs = ia.Referencias()
        refs.anotar_instrucciones(instruccion)
        assert refs.precio_permitido(precio), instruccion
    # Con dos IVA distintos en la instrucción y ninguno al lado, no se elige.
    refs = ia.Referencias()
    refs.anotar_instrucciones("con IVA o sin IVA, como prefieras; la desktop a 14.900 €")
    assert not refs.precio_permitido("14.900 € + IVA")
    assert refs.precio_permitido("14.900 €")


def test_un_texto_que_repite_el_precio_que_dijo_quien_envia_no_se_avisa(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    def _con_texto(refs: dict[str, str]) -> dict[str, Any]:
        correo = _compuesto({"ref": refs[TITULO_DESKTOP], "precio": "14.900 € + IVA"})
        correo["bloques"][0]["texto"] = ("Hola Laura,\n\nte la dejo en 14.900 € + IVA, como "
                                         "hablamos.")
        return correo

    falso = _ClaudeQueLee(PLAN_DESKTOP, _con_texto)
    with session_factory() as s:
        _indice(s)
        thread_id = _hilo_laura(s, "Hola, ¿tenéis la MBO 6090 Desktop?")
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, formato="compuesto", fuentes=AMBAS,
                   instruccion="contéstale con la ficha de la Desktop y pónsela a 14.900 € + IVA")
    assert r.status_code == 201, r.text
    aviso = r.json()["borrador"]["aviso"]
    assert "precio que me has indicado tú: 14.900 € + IVA" in aviso
    assert "Un texto del correo menciona" not in aviso


def test_un_nombre_pedido_que_es_un_producto_del_correo_no_se_avisa(s: Session) -> None:
    """Un consumible (fuera del índice a propósito), una máquina sin enlace o
    el nombre escrito de otra forma: su ficha es la del catálogo."""
    _indice(s)
    del_correo = ["Tinta UV blanca 1 L MBO 6090", "MBO 3050", "artisJet 6090 Trust"]
    for nombre in ("tinta blanca", "MBO 3050", "la Trust de artisJet", "artisJet Trüst"):
        avisos: list[str] = []
        consulta_fuentes._productos_pedidos(s, instruccion="", nombres=[nombre], idioma="es",
                                            catalogo=consulta_fuentes._Catalogo(), resumen={},
                                            avisos=avisos, del_correo_nombres=del_correo)
        assert avisos == [], nombre
    entrada = consulta_fuentes.Entrada(maquinas=[{"nombre": "MBO 3050"}],
                                       consumibles=[{"nombre": "Tinta UV blanca 1 L"}])
    assert consulta_fuentes.nombres_del_correo(entrada) == ["MBO 3050", "Tinta UV blanca 1 L"]


def test_un_enlace_que_es_solo_el_slug_del_producto_es_el_producto(s: Session) -> None:
    """`/<slug>/` (y `/de/<slug>/`): WordPress lo lleva al producto."""
    _indice(s)
    _boprint(s)
    mapa = fichas.MapaProductos(s)
    for url in ("https://boprint.net/impresora-uv-led-5000u/",
                "https://mboprinters.com/de/mbo-6090-uv-led/"):
        assert mapa.producto(url) is not None and mapa.motivo(url) is None, url
    assert mapa.motivo(URL_CONTACTO) == fichas.MOTIVO_SERVICIO


def test_con_webs_desmarcada_no_se_miran_los_enlaces_de_ningun_motivo(s: Session) -> None:
    _boprint(s)
    nueva = {"fuente": FUENTE_COMPOSITOR, "nombre": "Nueva",
             "enlace": "https://boprint.net/producto/impresora-uv-nueva/"}
    estructura = bq.normalizar({"idioma": "es", "bloques": [{"tipo": "producto", "producto": {
        "fuente": FUENTE_COMPOSITOR, "origen_id": "nueva"}}]})
    productos = {"compositor|nueva": nueva}
    fichas_correo, avisos = procedencia.fichas_del_correo(s, estructura, productos, idioma="es",
                                                          comprobar_enlaces=False)
    assert avisos == [] and "enlace_motivo" not in fichas_correo[0]
    fichas_correo, avisos = procedencia.fichas_del_correo(s, estructura, productos, idioma="es")
    assert fichas_correo[0]["enlace_motivo"] == fichas.MOTIVO_FUERA and len(avisos) == 1


# --- el catálogo manda siempre (28/10/2026, segunda vuelta) ----------------------------------

#: La instrucción del 28/10, tal cual, sobre el hilo de la avería de un Ador 20W.
INSTRUCCION_2810 = ("Prepara una oferta breve. Incluye en fila la ficha de la artisJet 6090 Trust "
                    "y la ficha de la MBO 6090 versión desktop: "
                    "https://mboprinters.com/product/mbo-6090-uv-led-desktop")
PLAN_2810 = {**PLAN_DESKTOP, "webs": "", "productos": ["artisJet 6090 Trust",
                                                       "MBO 6090 desktop"]}
#: Las FLUX del hilo, con el enlace a su página de aterrizaje (una página de la
#: web, no la ficha de tienda): está bien puesto.
FLUX = (("flux-ador", "FLUX Ador", "ador"), ("flux-beambox-ii", "Beambox II", "beambox-ii"),
        ("flux-beamo", "Beamo", "beamo"), ("flux-hexa", "Hexa 60W", "hexa"),
        ("flux-lazervida", "Lazervida", "lazervida"),
        ("flux-autofoco", "Autofoco Beamo", "autofoco-beamo"))
HILO_ADOR = ("Hola, mi FLUX Ador 20W da un error de enfoque desde ayer (ticket 1654). En el taller "
             "tenemos también una Beambox II, una Beamo, una Hexa 60W, una Lazervida y el "
             "Autofoco Beamo, y esas van bien.")


def _caso_ador(s: Session) -> str:
    """El catálogo: seis FLUX (con su página de aterrizaje en fluxlasers.eu,
    indexada como página), la artisJet 6090 Trust a 20.900 € y la MBO 6090.
    El índice: fluxlasers.eu (sus páginas y una ficha de tienda), la Desktop
    sin precio en la web y una página de la Trust en boprint.net sin precio.
    El hilo: la avería del Ador."""
    for n, (origen_id, nombre, slug) in enumerate(FLUX):
        _maquina(s, origen_id, nombre, enlace=f"https://fluxlasers.eu/{slug}/",
                 sitio="fluxlasers", precio="3.049 €", marca="flux")
        fila_web(s, wp_id=900 + n, tipo="pagina", titulo=nombre,
                 url=f"https://fluxlasers.eu/{slug}/", slug=slug, texto=f"{nombre}.")
    fila_web(s, wp_id=3949, titulo="Beambox II 55W", url="https://fluxlasers.eu/producto/"
             "beambox-ii-55w/", slug="beambox-ii-55w", texto="Cortadora láser CO2.")
    _maquina(s, "aj-6090-trust", "artisJet 6090 Trust", enlace=URL_TRUST, sitio="artisjet-eu",
             precio="20.900 €", marca="artisjet")
    _maquina(s, "mbo-6090", "MBO 6090", enlace=URL_6090, sitio="mboprinters")
    fila_web(s, sitio="boprint", dominio="boprint.net", wp_id=510, titulo="ArtisJet 6090 Trust",
             url="https://boprint.net/producto/artisjet-6090-trust/", slug="artisjet-6090-trust",
             texto="Impresora UV de 60 × 90 cm.")
    _indice(s, precio=False)
    hilo = _hilo(s, contacto=_contacto(s, LAURA), mensajes=[{"de": LAURA, "texto": HILO_ADOR}],
                 asunto="Avería Ador 20W")
    s.commit()
    return hilo.id


def test_las_maquinas_que_nombra_la_instruccion_entran_aunque_el_hilo_llene_el_tope(
        s: Session) -> None:
    """Seis FLUX que nombra el hilo llenaban el tope y dejaban fuera la
    artisJet 6090 Trust que pedía la instrucción: ahora entra, con su precio."""
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    _caso_ador(s)
    cat = catalogo_kb.para_asistente(s, intereses=[], idioma="es", maquina=None,
                                     modelo_flux=None, texto_cliente=HILO_ADOR,
                                     instruccion=INSTRUCCION_2810, config=CONFIG)
    por_eleccion = {m["nombre"]: m["eleccion"] for m in cat["maquinas"]}
    assert sum(1 for e in por_eleccion.values() if e == "hilo") == 6
    assert por_eleccion["artisJet 6090 Trust"] == "instruccion"
    trust = next(m for m in cat["maquinas"] if m["nombre"] == "artisJet 6090 Trust")
    assert trust["precio_texto"] == "20.900 €"


def test_el_producto_del_catalogo_se_reconoce_por_su_nombre() -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    def _fila(nombre: str, marca: str) -> Any:
        return SimpleNamespace(nombre=nombre, marca=marca)

    grupos = {"aj-6090-trust": [_fila("artisJet 6090 Trust", "artisjet")],
              "mbo-6090": [_fila("MBO 6090", "mbo")],
              "mbo-6090-pro": [_fila("MBO 6090 Pro", "mbo")],
              "flux-beambox-ii": [_fila("Beambox II", "flux")]}
    assert catalogo_kb.por_nombre("artisJet 6090 Trust", grupos) == "aj-6090-trust"
    assert catalogo_kb.por_nombre("la impresora UV ArtisJet 6090 Trust",
                                  grupos) == "aj-6090-trust"
    assert catalogo_kb.por_nombre("Trust", grupos) == "aj-6090-trust"
    assert catalogo_kb.por_nombre("MBO 6090", grupos) == "mbo-6090"          # la exacta
    assert catalogo_kb.por_nombre("MBO 6090 desktop", grupos) is None          # otra máquina
    assert catalogo_kb.por_nombre("artisJet", grupos) is None                  # solo la marca
    assert catalogo_kb.por_nombre("6090", grupos) == "mbo-6090"   # la que no añade nada
    assert catalogo_kb.por_nombre("MBO", {**grupos, "mbo-3050": [_fila("MBO 3050", "mbo")]}) is None
    assert catalogo_kb.por_nombre("Beambox II", grupos) == "flux-beambox-ii"
    assert catalogo_kb.por_nombre("", grupos) is None
    # El título de una página del índice, exacto: «MBO 3050» no es la «MBO 3050
    # Pro» (ni por el nombre que da el plan: una variante de más).
    con_3050 = {**grupos, "mbo-3050-pro": [_fila("MBO 3050 Pro", "mbo")]}
    assert catalogo_kb.por_nombre("MBO 3050", con_3050, exacto=True) is None
    assert catalogo_kb.por_nombre("MBO 3050", con_3050) is None
    assert catalogo_kb.por_nombre("Impresora UV-LED artisJet 6090 Trust 60x90", grupos,
                                  exacto=True) == "aj-6090-trust"
    assert catalogo_kb.por_nombre("artisJet Trust 6090 UV Led Freebird", grupos,
                                  exacto=True) is None


def _correo_trust_desktop(refs: dict[str, str]) -> dict[str, Any]:
    return _compuesto(refs["artisJet 6090 Trust"], refs[TITULO_DESKTOP])


def _comprobar_trust_desktop(b: dict[str, Any]) -> None:
    """La Trust del catálogo a 20.900 € y la Desktop del índice «a consultar»,
    en fila; y ningún aviso de enlace de las FLUX (sus páginas de aterrizaje
    están en el índice)."""
    assert _fila_productos(b) == [{"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"},
                                  WEB]
    html = b["html"]
    assert "20.900 €" in html and re.search(r">Precio a consultar</p>", html)
    assert _img_enlazada(html, URL_TRUST, "https://cdn.example/aj-6090-trust.jpg")
    assert _img_enlazada(html, URL_DESKTOP, IMG_DESKTOP)
    assert ("«artisJet 6090 Trust», del catálogo") in b["aviso"]
    assert (f"«{TITULO_DESKTOP}», del índice de mboprinters.com ({URL_DESKTOP}; precio a "
            "consultar") in b["aviso"]
    assert "no lleva a" not in b["aviso"] and "página de servicio" not in b["aviso"]
    assert b["contexto"].get("enlaces_no_producto") in (None, [])
    trust = next(f for f in b["contexto"]["fichas"] if f["nombre"] == "artisJet 6090 Trust")
    assert (trust["procedencia"], trust["precio_texto"]) == ("catalogo", "20.900 €")


def test_en_el_primer_borrador_la_trust_sale_del_catalogo_aunque_el_hilo_sea_de_flux(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    falso = _ClaudeQueLee(PLAN_2810, _correo_trust_desktop)
    with session_factory() as s:
        thread_id = _caso_ador(s)
    with patch.object(llm, "_invoke_claude", falso):
        r = _pedir(http, thread_id, instruccion=INSTRUCCION_2810, formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    contexto = _partes(falso)[1]
    assert re.search(r"- \[P\d+\] artisJet 6090 Trust · artisjet · .*PRECIO: 20\.900 €",
                     contexto)
    # La página de la Trust del índice no es una ficha: es del catálogo.
    assert "ArtisJet 6090 Trust\nEnlace:" not in _partes(falso)[2]
    _comprobar_trust_desktop(r.json()["borrador"])


def test_en_una_correccion_la_trust_sale_del_catalogo_con_su_precio_de_venta(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    """El caso del 28/10: el primer borrador contesta la avería (su CATÁLOGO
    son las FLUX); la corrección pide en fila la artisJet 6090 Trust, que no
    viene a cuento del hilo, y la MBO 6090 Desktop. La Trust sale del catálogo
    a 20.900 € (no «a consultar») y la Desktop, del índice."""
    falso = _ClaudeQueLee(PLAN_SIN_WEBS, lambda refs: _compuesto(refs["FLUX Ador"]))
    with session_factory() as s:
        thread_id = _caso_ador(s)
    with patch.object(llm, "_invoke_claude", falso):
        primero = _pedir(http, thread_id, instruccion="contéstale lo de la avería",
                         formato="compuesto", fuentes=AMBAS).json()["borrador"]
    assert "artisJet 6090 Trust" not in _partes(falso)[1]          # no está en su CATÁLOGO
    [fila] = [x for x in primero["estructura"]["bloques"]
              if x["tipo"] in (bq.TIPO_PRODUCTO, bq.TIPO_PRODUCTOS_FILA)]
    falso.plan = PLAN_2810
    falso.construir = lambda refs: {
        "modo": "compuesto", "asunto": "Re: Avería Ador 20W", "aviso": "", "pendiente": [],
        "operaciones": [{"op": "reemplazar", "id": fila["id"], "bloque": {
            "tipo": "productos_fila",
            "productos": [refs["artisJet 6090 Trust"], refs[TITULO_DESKTOP]]}}]}
    with patch.object(llm, "_invoke_claude", falso):
        r = _turno(http, primero["id"], INSTRUCCION_2810, fuentes=AMBAS)
    assert r.status_code == 201, r.text
    peticion = _partes(falso)[2]
    bloque = peticion.split("CATÁLOGO · AÑADIDO EN ESTA CORRECCIÓN", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"- \[P\d+\] artisJet 6090 Trust · artisjet · .*PRECIO: 20\.900 €", bloque)
    assert "MBO 6090 ·" not in bloque                    # la desktop no es la del catálogo
    b = r.json()["borrador"]
    _comprobar_trust_desktop(b)
    # «Lo que ha usado»: la Trust entra entre los productos del catálogo del turno.
    assert any(m["nombre"] == "artisJet 6090 Trust" and m["eleccion"] == "instruccion"
               for m in b["contexto"]["catalogo"]["maquinas"])
    pedidas = b["contexto"]["material"]["fichas_pedidas"]
    assert {"nombre": "artisJet 6090 Trust", "encontrada": True, "catalogo": True,
            "titulo": "artisJet 6090 Trust"} in pedidas


def test_un_producto_que_no_esta_en_el_catalogo_sigue_dando_ficha_del_indice(
        s: Session) -> None:
    _caso_ador(s)
    resumen: dict[str, Any] = {}
    pedidos = consulta_fuentes._productos_pedidos(
        s, instruccion="", nombres=["MBO 6090 desktop"], idioma="es",
        catalogo=consulta_fuentes._catalogo(s, []), resumen=resumen, avisos=[])
    assert [(f.sitio, f.wp_id) for f in pedidos] == [("mboprinters", 4202)]
    assert "catalogo_pedidos" not in resumen


def test_un_enlace_a_una_pagina_indexada_que_no_es_producto_no_se_avisa(s: Session) -> None:
    """La Beambox II apunta a su página de aterrizaje (`fluxlasers.eu/beambox-ii/`,
    en el índice como página), aunque también haya ficha de tienda: vale. La
    Freebird a la de contacto (servicio) y una a una página que el índice no
    tiene, sí se avisan; lo mismo en el borrador y en el Cuadre."""
    _caso_ador(s)
    _boprint(s)
    _freebird(s)
    _maquina(s, "flux-sin-indexar", "FLUX Nueva", sitio="fluxlasers", marca="flux",
             enlace="https://fluxlasers.eu/flux-nueva/")
    s.commit()
    mapa = fichas.MapaProductos(s)
    assert mapa.motivo("https://fluxlasers.eu/beambox-ii/") is None
    assert mapa.motivo("https://www.fluxlasers.es/en/beambox-ii") is None
    assert mapa.motivo(URL_CONTACTO) == fichas.MOTIVO_SERVICIO
    assert mapa.motivo("https://fluxlasers.eu/flux-nueva/") == fichas.MOTIVO_FUERA
    assert mapa.motivo("https://fluxlasers.eu/") is None                      # la portada
    for servicio in ("https://boprint.net/carrito/", "https://boprint.net/mi-cuenta/",
                     "https://boprint.net/finalizar-compra/", "https://fluxlasers.eu/buscar/",
                     "https://fluxlasers.eu/product-category/lasers/"):
        assert mapa.motivo(servicio) == fichas.MOTIVO_SERVICIO, servicio
    # El borrador.
    maquinas = [{"fuente": FUENTE_COMPOSITOR, "nombre": n, "enlace": f"https://fluxlasers.eu/{sl}/"}
                for _, n, sl in FLUX]
    freebird = {"fuente": FUENTE_COMPOSITOR, "nombre": "artisJet 3000Pro Freebird",
                "enlace": URL_CONTACTO}
    nueva = {"fuente": FUENTE_COMPOSITOR, "nombre": "FLUX Nueva",
             "enlace": "https://fluxlasers.eu/flux-nueva/"}
    assert procedencia.enlaces_catalogo_no_producto(s, [*maquinas, freebird, nueva]) == [
        {"nombre": "artisJet 3000Pro Freebird", "enlace": URL_CONTACTO, "motivo": "servicio"},
        {"nombre": "FLUX Nueva", "enlace": "https://fluxlasers.eu/flux-nueva/",
         "motivo": "fuera"}]
    assert "no está en el índice de nuestras webs" in procedencia.aviso_enlace_no_producto(
        "FLUX Nueva", "https://fluxlasers.eu/flux-nueva/", fichas.MOTIVO_FUERA)
    # El Cuadre: la misma regla.
    hallazgos = {h.entidad_id: h for h in enlace_catalogo_no_producto(_ctx(s))}
    assert set(hallazgos) == {"compositor:aj-3000pro:es", "compositor:flux-sin-indexar:es"}
    assert "página de servicio" in hallazgos["compositor:aj-3000pro:es"].detalle
    assert "no está en el índice de fluxlasers.es" in (
        hallazgos["compositor:flux-sin-indexar:es"].detalle)



# --- la ronda de revisión (segunda vuelta) --------------------------------------------------


def _grupos(*nombres: tuple[str, str, str]) -> dict[str, list[Any]]:
    from types import SimpleNamespace  # noqa: PLC0415

    return {o: [SimpleNamespace(nombre=n, marca=m, descripcion="", caracteristicas_json="[]",
                                datos_json="{}", origen_id=o)] for o, n, m in nombres}


def test_lo_que_nombra_una_instruccion_no_arrastra_otras_maquinas() -> None:
    """Un código que comparten («6090»), una variante detrás («versión
    desktop», «Pro») o una hermana nombrada entera («Ador 20W» y no la 10W)."""
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    grupos = _grupos(("trust", "artisJet 6090 Trust", "artisjet"), ("mbo6090", "MBO 6090", "mbo"),
                     ("mbo6090pro", "MBO 6090 Pro", "mbo"), ("ador20", "FLUX Ador 20W", "flux"),
                     ("ador10", "FLUX Ador 10W", "flux"), ("bb2", "Beambox II", "flux"),
                     ("bbpro", "Beambox Pro", "flux"))
    for texto, nombradas in (
            (INSTRUCCION_2810, ["trust"]),
            ("incluye la ficha de la artisJet 6090 Trust", ["trust"]),
            ("incluye la Trust delante", ["trust"]),
            ("añade la MBO 6090 versión desktop", []),
            ("incluye la MBO 6090 desktop", []),
            ("añade la MBO 6090", ["mbo6090"]),
            ("añade la MBO 6090 Pro", ["mbo6090pro"]),
            ("incluye la Ador 20W", ["ador20"]),
            ("incluye la Beambox II", ["bb2"]),
            ("pon la Trust y la MBO 6090 juntas", ["trust", "mbo6090"])):
        assert sorted(catalogo_kb.nombrados_en(texto, grupos)) == sorted(nombradas), texto


def test_en_el_primer_borrador_la_instruccion_no_mete_la_mbo_6090_si_pide_la_desktop(
        s: Session) -> None:
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    _caso_ador(s)
    cat = catalogo_kb.para_asistente(s, intereses=[], idioma="es", maquina=None,
                                     modelo_flux=None, texto_cliente=HILO_ADOR,
                                     instruccion=INSTRUCCION_2810, config=CONFIG)
    nombres = [m["nombre"] for m in cat["maquinas"]]
    assert "artisJet 6090 Trust" in nombres and "MBO 6090" not in nombres


@pytest.mark.parametrize("presupuesto", [5400, 5000, 4600])
def test_tras_el_recorte_por_tokens_cada_p_sigue_siendo_su_maquina(
    session_factory: sessionmaker, http: TestClient, presupuesto: int,
) -> None:
    """Con el presupuesto justo se recortan máquinas del CATÁLOGO. Las
    referencias se numeran antes del recorte: lo que queda no puede moverse
    (se quita por el final) y lo que pide la instrucción va delante. Cada
    «[P#]» que ve la IA es la máquina que guardan las referencias, y la ficha
    que sale es la de la Trust."""
    from app.services.asistente import config as cfg_mod  # noqa: PLC0415
    from app.services.asistente import contexto as ctx_mod  # noqa: PLC0415

    real = cfg_mod.configuracion

    def _cfg(session: Session) -> dict[str, Any]:
        return {**real(session), "presupuesto_tokens": presupuesto}

    falso = _ClaudeQueLee(PLAN_2810, _correo_trust_desktop)
    with session_factory() as s:
        thread_id = _caso_ador(s)
    with (patch.object(llm, "_invoke_claude", falso),
          patch.object(ctx_mod, "config_asistente", _cfg)):
        r = _pedir(http, thread_id, instruccion=INSTRUCCION_2810, formato="compuesto",
                   fuentes=AMBAS)
    assert r.status_code == 201, r.text
    b = r.json()["borrador"]
    assert any(str(x).startswith("catálogo: máquina") for x in b["contexto"]["recortes"])
    origen = {nombre: oid for oid, nombre, _ in FLUX} | {"artisJet 6090 Trust": "aj-6090-trust",
                                                         "MBO 6090": "mbo-6090"}
    guardadas = {k: v["origen_id"] for k, v in b["contexto"]["refs"]["productos"].items()}
    vistas = re.findall(r"- \[(P\d+)\] ([^·]+?) ·", _partes(falso)[1])
    assert vistas and all(guardadas[ref] == origen[nombre] for ref, nombre in vistas), vistas
    assert _fila_productos(b)[0] == {"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"}


def test_la_pagina_de_un_producto_que_solo_esta_en_la_web_no_es_otra_maquina_del_catalogo(
        s: Session) -> None:
    """El catálogo solo tiene la «MBO 3050 Pro»; el índice, la «MBO 3050»:
    pedida por su enlace, va la del índice, no la Pro."""
    _maquina(s, "mbo-3050-pro", "MBO 3050 Pro", sitio="mboprinters", precio="8.900 €",
             enlace="https://mboprinters.com/product/mbo-3050-pro/")
    _web(s, wp_id=5200, idioma="es", url="https://mboprinters.com/product/mbo-3050/",
         slug="mbo-3050", titulo="MBO 3050", texto="Impresora UV de 30 × 50 cm.")
    s.commit()
    resumen: dict[str, Any] = {}
    pedidos = consulta_fuentes._productos_pedidos(
        s, instruccion="incluye la ficha de la MBO 3050: https://mboprinters.com/product/mbo-3050/",
        nombres=["MBO 3050"], idioma="es", catalogo=consulta_fuentes._catalogo(s, []),
        resumen=resumen, avisos=[])
    assert [(f.sitio, f.wp_id) for f in pedidos] == [("mboprinters", 5200)]
    assert "catalogo_pedidos" not in resumen


def test_un_enlace_a_una_pagina_de_servicio_en_la_instruccion_no_es_una_maquina(
        s: Session) -> None:
    """La Freebird del catálogo apunta a /contacto/: «dile que nos escriba
    desde https://boprint.net/contacto/» no pide la Freebird."""
    _boprint(s)
    _freebird(s)
    resumen: dict[str, Any] = {}
    avisos: list[str] = []
    consulta_fuentes._productos_pedidos(
        s, instruccion=f"dile que nos escriba desde {URL_CONTACTO} y le llamamos", nombres=[],
        idioma="es", catalogo=consulta_fuentes._catalogo(s, []), resumen=resumen, avisos=avisos)
    assert "catalogo_pedidos" not in resumen and avisos == []


def test_mas_paginas_de_servicio_y_una_maquina_reacondicionada_no_lo_es(s: Session) -> None:
    _boprint(s)
    for wp_id, url, slug in ((701, "https://boprint.net/contacto-boprint/", "contacto-boprint"),
                             (702, "https://boprint.net/en/contact-us-today/",
                              "contact-us-today")):
        fila_web(s, sitio="boprint", dominio="boprint.net", wp_id=wp_id, tipo="pagina",
                 titulo="Contacto", url=url, slug=slug)
    s.commit()
    mapa = fichas.MapaProductos(s)
    for url in ("https://boprint.net/contacto-boprint/", "https://boprint.net/en/contact-us-today/",
                "https://boprint.net/kontakt-boprint/", "https://boprint.net/?s=freebird",
                "https://boprint.net/?add-to-cart=364", "https://boprint.net/?post_type=product"):
        assert mapa.motivo(url) == fichas.MOTIVO_SERVICIO, url
    # Una máquina reacondicionada (fuera del índice por su categoría) no es una
    # página de condiciones: no está en el índice.
    assert mapa.motivo("https://boprint.net/producto/impresora-reacondicionada/") == (
        fichas.MOTIVO_FUERA)
    assert mapa.motivo("https://boprint.net/producto/impresora-uv-led-5000u/") is None


def test_en_el_cuadre_lo_que_no_se_puede_juzgar_no_deja_abierto_lo_arreglado(s: Session) -> None:
    """Una web con páginas y sin productos en el índice: la ficha A (a su
    página de contacto) se avisa; la B (una ruta de producto, que no se puede
    juzgar) no impide que A se dé por resuelta cuando se arregla."""
    for wp_id, url, titulo in ((1, "https://pimpam-vending.com/contacto/", "Contacto"),
                               (2, "https://pimpam-vending.com/vending/", "Vending")):
        fila_web(s, sitio="pimpam", dominio="pimpam-vending.com", wp_id=wp_id, tipo="pagina",
                 url=url, titulo=titulo)
    _maquina(s, "pp-a", "Pimpam A", sitio="pimpam", marca="pimpam",
             enlace="https://pimpam-vending.com/contacto/")
    _maquina(s, "pp-b", "Pimpam B", sitio="pimpam", marca="pimpam",
             enlace="https://pimpam-vending.com/producto/pimpam-b/")
    s.commit()
    comp = registro()[ENLACE_CATALOGO_NO_PRODUCTO]

    def pasada(cuando: datetime) -> dict[str, int]:
        ctx = _ctx(s)
        hallazgos = list(enlace_catalogo_no_producto(ctx))
        return cuadre_engine.aplicar(s, comp, hallazgos, cuando,
                                     no_mirados=ctx.no_mirados.get(comp.id, set()))

    assert pasada(AHORA)["nuevos"] == 1
    a = s.scalar(select(ProductoCatalogo).where(ProductoCatalogo.origen_id == "pp-a"))
    a.enlace = "https://pimpam-vending.com/vending/"
    s.flush()
    assert pasada(AHORA + timedelta(days=1))["resueltos"] == 1


def test_una_correccion_reutiliza_el_p_y_no_repite_lo_que_ya_trajo(s: Session) -> None:
    """Lo que la IA no ve en el CATÁLOGO del primer borrador (recortado, o que
    solo tiene su P# por una página de la web) se le vuelve a dar con su
    mismo P#; y «Lo que ha usado» no lo repite en la corrección siguiente."""
    from app.services.asistente import redactor  # noqa: PLC0415

    _caso_ador(s)
    refs = ia.Referencias(productos={
        "P1": {"fuente": FUENTE_COMPOSITOR, "origen_id": "flux-ador"},
        "P7": {"fuente": FUENTE_COMPOSITOR, "origen_id": "aj-6090-trust"}})
    raiz = {"catalogo": {"maquinas": [{"nombre": "FLUX Ador", "fuente": FUENTE_COMPOSITOR,
                                       "origen_id": "flux-ador"}]}}
    bloque, pedidos = redactor._catalogo_pedido_correccion(
        s, refs, instruccion="incluye la ficha de la artisJet 6090 Trust", mat=None, plan=None,
        idioma="es", contexto=raiz, raiz_contexto=raiz)
    assert "- [P7] artisJet 6090 Trust · artisjet" in bloque
    assert [p["origen_id"] for p in pedidos] == ["aj-6090-trust"]
    # Sin pedir que se ponga nada, no se busca.
    assert redactor._catalogo_pedido_correccion(
        s, refs, instruccion="dile que la artisJet 6090 Trust la tenemos en stock", mat=None,
        plan=None, idioma="es", contexto=raiz, raiz_contexto=raiz) == ("", [])
    contexto = redactor._con_catalogo_pedido(json.dumps(raiz), pedidos)
    otra_vez = redactor._con_catalogo_pedido(contexto, pedidos)
    nombres = [m["nombre"] for m in json.loads(otra_vez)["catalogo"]["maquinas"]]
    assert nombres == ["artisJet 6090 Trust", "FLUX Ador"]


# --- la comprobación de los arreglos (segunda vuelta) ---------------------------------------


def test_varias_maquinas_hermanas_nombradas_juntas() -> None:
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    grupos = _grupos(("trust", "artisJet 6090 Trust", "artisjet"),
                     ("freebird", "artisJet 3000Pro Freebird", "artisjet"),
                     ("mbo3050", "MBO 3050", "mbo"), ("mbo6090", "MBO 6090", "mbo"),
                     ("ador20", "FLUX Ador 20W", "flux"), ("ador10", "FLUX Ador 10W", "flux"),
                     ("bb2", "Beambox II", "flux"), ("bb2pro", "Beambox II Pro", "flux"),
                     ("bbpro", "Beambox Pro", "flux"), ("lyra", "Lyra 35", "mbo"),
                     ("astra", "Astra 35 Pro", "mbo"))
    for texto, nombradas in (
            ("incluye en fila la MBO 3050 y la MBO 6090", ["mbo3050", "mbo6090"]),
            ("incluye en fila la Ador 20W y la Ador 10W", ["ador20", "ador10"]),
            ("incluye la Beambox II y la Beambox Pro", ["bb2", "bbpro"]),
            ("incluye la FLUX Beambox II y la FLUX Beambox II Pro", ["bb2", "bb2pro"]),
            ("incluye la Lyra 35 y la Astra 35 Pro", ["lyra", "astra"]),
            ("añade en fila la artisJet Trust 6090 Freebird", ["trust"]),
            ("añade la 3000Pro Freebird", ["freebird"])):
        assert sorted(catalogo_kb.nombrados_en(texto, grupos)) == sorted(nombradas), texto


def test_que_correcciones_piden_poner_algo() -> None:
    for instruccion in ("Añádele la artisJet 6090 Trust al final", "Ponle también la Trust",
                        "Ofrécele también la artisJet 6090 Trust",
                        "Sustituye la FLUX Ador por la artisJet 6090 Trust",
                        "Recomiéndale la Trust como alternativa", "incluye la Trust"):
        assert consulta_fuentes.pide_incluir(instruccion), instruccion
    for instruccion in ("dile que la Ador 20W la revisamos el lunes", "quita la Trust",
                        "más corto", "", None):
        assert not consulta_fuentes.pide_incluir(instruccion), instruccion


def test_contacts_y_la_portada_de_la_tienda_son_de_servicio(s: Session) -> None:
    _boprint(s)
    mapa = fichas.MapaProductos(s)
    for url in ("https://boprint.net/en/contacts/", "https://boprint.net/tienda/",
                "https://boprint.net/producto/"):
        assert mapa.motivo(url) == fichas.MOTIVO_SERVICIO, url
    assert mapa.motivo("https://boprint.net/") is None                         # la portada


def test_un_descuadre_de_antes_con_el_ambito_de_su_web_no_se_resuelve_sin_mirarlo(
        s: Session) -> None:
    """Los descuadres de antes guardaban la web como ámbito; ahora es la
    ficha. Uno de antes cuya ficha no se puede juzgar sigue abierto."""
    _boprint(s)
    _freebird(s)
    freebird = s.scalar(select(ProductoCatalogo).where(ProductoCatalogo.origen_id == "aj-3000pro"))
    freebird.enlace = "https://boprint.net/producto/artisjet-3000pro-freebird/"   # sin indexar
    s.flush()
    comp = registro()[ENLACE_CATALOGO_NO_PRODUCTO]

    def pasada(cuando: datetime) -> dict[str, int]:
        ctx = _ctx(s)
        hallazgos = list(enlace_catalogo_no_producto(ctx))
        return cuadre_engine.aplicar(s, comp, hallazgos, cuando,
                                     no_mirados=ctx.no_mirados.get(comp.id, set()))

    assert pasada(AHORA)["nuevos"] == 1
    [abierto] = s.scalars(select(CuadreFinding))
    detalle = json.loads(abierto.detalle_json)
    abierto.detalle_json = json.dumps({**detalle, "ambito": "boprint"})       # como antes
    for fila in s.scalars(select(WebIndiceEntrada).where(WebIndiceEntrada.sitio == "boprint")):
        fila.ido_en = AHORA
    s.flush()
    assert pasada(AHORA + timedelta(days=1))["resueltos"] == 0
    assert abierto.estado == "abierto"
