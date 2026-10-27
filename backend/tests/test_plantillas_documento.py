"""Plantillas de documento: un catálogo, tres destinos (oferta, proforma,
pedido), carpetas con acceso, etiquetas, idioma y un buscador que mira dentro
de las líneas.

FACTUSOL es un doble en memoria (el de los pedidos desde documento): guardar
una plantilla desde una proforma o un albarán solo LEE; ninguna prueba
escribe en FACTUSOL.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp import plantillas as svc
from app.erp.models import (
    Order,
    OrderLine,
    PlantillaCarpeta,
    PlantillaCarpetaAcceso,
    PlantillaDocumento,
)
from app.erp.ofertas import normalizar_lineas
from app.main import app
from app.models.crm import Company, Contact, User
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_pedido_desde_factusol import FakeClient, _alb, _lal, _lps, _pre

#: Las líneas de la proforma 1-40: dos artículos de FACTUSOL y una nota.
LINEAS_PROFORMA = [
    _lps(40, 1, art="DM3H", desc="Horno de secado DTF 60 cm", cant=1, precio=1200.0, dto=5,
         iva=21),
    _lps(40, 2, art="", desc="Incluye instalación y formación", cant=0, precio=0),
    _lps(40, 3, art="TINTA-W", desc="Tinta blanca 1 L", cant=4, precio=80.0, iva=21),
]


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


def _fake() -> FakeClient:
    return FakeClient({
        "F_PRE": [_pre(40, ref="Kit DTF escuelas")],
        "F_LPS": LINEAS_PROFORMA,
        "F_ALB": [_alb(77, serie="5")],
        "F_LAL": [_lal(77, 1, serie="5", art="DM3H", desc="Horno de secado DTF 60 cm",
                       cant=2, precio=1100.0),
                  _lal(77, 2, serie="5", art="FILM-30", desc="Film DTF 30 cm", cant=3,
                       precio=45.0, dto=10)],
    })


def _patch(fake: Any):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


def _empresa(s: Session, *, language: str | None = None, country: str | None = None) -> str:
    c = Company(name="Escuela de Arte SL", tax_id="B12345678", factusol_company_id="55555",
                language=language, country=country)
    s.add(c)
    s.commit()
    return c.id


def _plantilla(s: Session, nombre: str, *, idioma: str = "es", carpeta_id: str | None = None,
               lineas: list[dict[str, Any]] | None = None,
               etiquetas: list[str] | None = None) -> str:
    p = svc.crear_plantilla(
        s, nombre=nombre, idioma=idioma, carpeta_id=carpeta_id, usuario=None,
        etiquetas=etiquetas, lineas=normalizar_lineas(lineas or [
            {"codart": "GEN-1", "sku": "GEN-1", "descripcion": "Artículo genérico",
             "cantidad": 1, "precio": 10}]),
    )
    s.commit()
    return p.id


def _desde_proforma(http: TestClient, fake: FakeClient, **extra: Any) -> dict[str, Any]:
    with _patch(fake):
        r = http.post("/api/erp/plantillas", headers=auth_headers(http, "comercial"), json={
            "nombre": "Kit DTF escuelas", "idioma": "es",
            "origen": {"tipo": "presupuesto", "serie": 1, "codigo": 40}, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. Una plantilla desde una proforma sirve para oferta, proforma y pedido,
#    y en los tres conserva los artículos de FACTUSOL
# ---------------------------------------------------------------------------


def test_plantilla_de_proforma_sirve_para_oferta_proforma_y_pedido(http, session_factory):
    fake = _fake()
    plantilla = _desde_proforma(http, fake)
    assert plantilla["origen_tipo"] == "presupuesto" and plantilla["origen_numero"] == "1-000040"
    articulos = [ln["codart"] for ln in plantilla["lineas"] if ln["codart"]]
    assert articulos == ["DM3H", "TINTA-W"]
    h = auth_headers(http, "comercial")
    with session_factory() as s:
        company_id = _empresa(s)
        contacto = Contact(first_name="Ana", last_name="Pi", email="ana@escuela.es",
                           company_id=company_id)
        s.add(contacto)
        s.commit()
        contact_id = contacto.id

    # Oferta: el flujo de siempre con `plantilla_id`.
    r = http.post("/api/erp/ofertas", headers=h, json={
        "contact_id": contact_id, "serie": 1, "plantilla_id": plantilla["id"]})
    assert r.status_code == 201, r.text
    assert [ln["codart"] for ln in r.json()["lineas"] if ln["codart"]] == articulos
    assert r.json()["plantilla_id"] == plantilla["id"]

    # Proforma: las líneas para el destino y el alta de proforma de siempre.
    lineas = http.get(f"/api/erp/plantillas/{plantilla['id']}/lineas",
                      params={"destino": "proforma"}, headers=h).json()["lineas"]
    with patch("app.integrations.factusol.jobs.enqueue_create_quote",
               return_value="job-1") as enq:
        r = http.post("/api/erp/factusol/quotes", headers=h, json={
            "company_id": company_id, "lines": [
                {k: ln[k] for k in ("codart", "description", "quantity", "unit_price",
                                    "discount_pct", "iva_pct")} for ln in lineas]})
    assert r.status_code == 202, r.text
    enviadas = enq.call_args.args[1]
    assert [ln["codart"] for ln in enviadas if ln.get("codart")] == articulos
    assert enviadas[0]["discount_pct"] == 5
    assert enviadas[1]["quantity"] == 0          # la nota sigue siendo una nota

    # Pedido: las líneas para el destino y el alta de pedido de siempre.
    r = http.get(f"/api/erp/plantillas/{plantilla['id']}/lineas",
                 params={"destino": "pedido"}, headers=h)
    cuerpo = r.json()
    assert any("neto" in a for a in cuerpo["avisos"])   # el pedido no lleva dto. por línea
    r = http.post("/api/erp/orders", headers=h, json={
        "company_id": company_id, "lines": cuerpo["lineas"]})
    assert r.status_code == 201, r.text
    with session_factory() as s:
        lineas_pedido = list(s.scalars(select(OrderLine).where(
            OrderLine.order_id == r.json()["id"]).order_by(OrderLine.position)))
        assert [ln.product_codart for ln in lineas_pedido] == articulos
        # La nota va pegada a la descripción del artículo anterior.
        assert "instalación y formación" in lineas_pedido[0].description
        assert float(lineas_pedido[0].unit_price) == 1140.0          # 1200 − 5 %
    assert fake.writes == []


# ---------------------------------------------------------------------------
# 2. Guardar como plantilla desde una proforma, un albarán y un pedido
# ---------------------------------------------------------------------------


def test_guardar_como_plantilla_desde_proforma_albaran_y_pedido(http, session_factory):
    fake = _fake()
    h = auth_headers(http, "comercial")
    de_proforma = _desde_proforma(http, fake)
    assert de_proforma["num_lineas"] == 3

    with _patch(fake):
        r = http.post("/api/erp/plantillas", headers=h, json={
            "nombre": "Reposición DTF", "idioma": "es", "etiquetas": ["DTF", "renting"],
            "origen": {"tipo": "albaran", "serie": 5, "codigo": 77}})
    assert r.status_code == 201, r.text
    de_albaran = r.json()
    assert de_albaran["origen_tipo"] == "albaran" and de_albaran["origen_numero"] == "5-000077"
    assert [(ln["codart"], ln["cantidad"], ln["descuento_pct"]) for ln in de_albaran["lineas"]] \
        == [("DM3H", 2.0, 0.0), ("FILM-30", 3.0, 10.0)]
    assert de_albaran["etiquetas"] == ["DTF", "renting"]
    assert all(ln["iva_pct"] == 21 for ln in de_albaran["lineas"])  # IVALAL=0 es un código

    with session_factory() as s:
        order = Order(order_number="MANUAL-000009", external_source="manual",
                      total_amount=0, language="de")
        s.add(order)
        s.flush()
        s.add_all([
            OrderLine(order_id=order.id, position=0, product_sku="DM3H",
                      product_codart="DM3H", description="Horno de secado", quantity=1,
                      unit_price=1000, tax_rate=21, line_total=900, notes="dto. 10%"),
            OrderLine(order_id=order.id, position=1, product_sku="", description="Portes",
                      quantity=1, unit_price=30, tax_rate=21, line_total=30,
                      is_shipping=True),
        ])
        s.commit()
        order_id = order.id
    r = http.post("/api/erp/plantillas", headers=h, json={
        "nombre": "Horno suelto", "idioma": "de",
        "origen": {"tipo": "pedido", "order_id": order_id}})
    assert r.status_code == 201, r.text
    de_pedido = r.json()
    assert de_pedido["origen_tipo"] == "pedido" and de_pedido["origen_numero"] == "MANUAL-000009"
    # Sin la línea de portes, con el descuento recuperado de su nota.
    assert [(ln["codart"], ln["descuento_pct"]) for ln in de_pedido["lineas"]] == [("DM3H", 10.0)]
    assert de_pedido["idioma"] == "de"
    assert fake.writes == []


# ---------------------------------------------------------------------------
# 4. Idioma del cliente primero, los demás accesibles
# ---------------------------------------------------------------------------


def test_cliente_aleman_ve_primero_las_plantillas_en_aleman(http, session_factory):
    with session_factory() as s:
        _plantilla(s, "Angebot UV", idioma="de")
        _plantilla(s, "Oferta UV", idioma="es")
        _plantilla(s, "Offer UV", idioma="en")
        _plantilla(s, "Angebot DTF", idioma="de")
        company_id = _empresa(s, language="de")
        contacto = Contact(first_name="Lena", last_name="Müller", email="lena@gmbh.de",
                           language="de")
        s.add(contacto)
        s.commit()
        contact_id = contacto.id
    h = auth_headers(http, "comercial")
    for params in ({"contact_id": contact_id}, {"company_id": company_id}):
        r = http.get("/api/erp/plantillas", params=params, headers=h)
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["idioma_cliente"] == "de"
        idiomas = [p["idioma"] for p in cuerpo["items"]]
        assert idiomas[:2] == ["de", "de"]
        assert sorted(idiomas[2:]) == ["en", "es"]       # no se ocultan
        assert [p["idioma_coincide"] for p in cuerpo["items"]] == [True, True, False, False]


# ---------------------------------------------------------------------------
# 5. El buscador mira dentro de las líneas
# ---------------------------------------------------------------------------


def test_buscador_encuentra_por_referencia_y_por_descripcion_de_linea(http, session_factory):
    with session_factory() as s:
        objetivo = _plantilla(s, "Kit escuela básico", etiquetas=["escuelas"], lineas=[
            {"codart": "00071", "sku": "DM3H", "descripcion": "Horno de secado 60 cm",
             "cantidad": 1, "precio": 1200},
            {"codart": "T1", "sku": "TINTA-C", "descripcion": "Tinta cian", "cantidad": 2,
             "precio": 50}])
        _plantilla(s, "Plotter de corte", lineas=[
            {"codart": "PC1", "sku": "CUT-60", "descripcion": "Plotter 60", "cantidad": 1,
             "precio": 900}])
    h = auth_headers(http, "comercial")

    def buscar(q: str) -> list[dict[str, Any]]:
        r = http.get("/api/erp/plantillas", params={"q": q}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    # Por la referencia comercial de una línea (el nombre no la dice)…
    por_ref = buscar("DM3H")
    assert [p["id"] for p in por_ref] == [objetivo]
    assert por_ref[0]["coincidencias"][0]["sku"] == "DM3H"
    # …por el código interno, por una palabra de la descripción de una línea…
    assert [p["id"] for p in buscar("00071")] == [objetivo]
    assert [p["id"] for p in buscar("horno")] == [objetivo]
    assert [p["id"] for p in buscar("HORNO secado")] == [objetivo]   # todas las palabras
    # …por una etiqueta y, claro, por el nombre.
    assert [p["id"] for p in buscar("escuelas")] == [objetivo]
    assert len(buscar("plotter")) == 1
    assert buscar("horno plotter") == []
    assert buscar("100%_") == []                                      # comodines escapados


# ---------------------------------------------------------------------------
# 6 y 7. Acceso por carpeta: el restringido solo ve sus carpetas; sin
#        restricción, todo
# ---------------------------------------------------------------------------


def _carpetas_y_plantillas(s: Session) -> dict[str, str]:
    escuelas = PlantillaCarpeta(nombre="Escuelas")
    ferias = PlantillaCarpeta(nombre="Ferias")
    s.add_all([escuelas, ferias])
    s.flush()
    fespa = PlantillaCarpeta(nombre="FESPA", parent_id=ferias.id)
    s.add(fespa)
    s.commit()
    linea = [{"codart": "DM3H", "sku": "DM3H", "descripcion": "Horno", "cantidad": 1,
              "precio": 10}]
    return {
        "escuelas": escuelas.id, "ferias": ferias.id, "fespa": fespa.id,
        "p_escuelas": _plantilla(s, "Kit escuelas", carpeta_id=escuelas.id, lineas=linea),
        "p_fespa": _plantilla(s, "Stand FESPA", carpeta_id=fespa.id, lineas=linea),
        "p_raiz": _plantilla(s, "Suelta", lineas=linea),
    }


def test_comercial_con_acceso_a_una_carpeta_no_ve_las_demas(http, session_factory):
    with session_factory() as s:
        ids = _carpetas_y_plantillas(s)
        comercial_id = s.scalar(select(User.id).where(User.email == "comercial@example.com"))
        contacto = Contact(first_name="Ana", last_name="Pi", email="ana@x.es")
        s.add(contacto)
        s.commit()
        contact_id = contacto.id
    admin = auth_headers(http, "admin")
    r = http.put(f"/api/erp/plantillas/carpetas/{ids['ferias']}/acceso", headers=admin,
                 json={"user_ids": [comercial_id]})
    assert r.status_code == 200, r.text
    h = auth_headers(http, "comercial")

    # Lista y buscador: solo «Ferias» (y su subcarpeta FESPA).
    todas = http.get("/api/erp/plantillas", headers=h).json()["items"]
    assert [p["id"] for p in todas] == [ids["p_fespa"]]
    assert [p["id"] for p in http.get("/api/erp/plantillas", params={"q": "DM3H"},
                                      headers=h).json()["items"]] == [ids["p_fespa"]]
    assert http.get("/api/erp/plantillas", params={"q": "escuelas"},
                    headers=h).json()["items"] == []
    # Árbol: solo su carpeta.
    arbol = http.get("/api/erp/plantillas/carpetas", headers=h).json()["items"]
    assert [n["nombre"] for n in arbol] == ["Ferias"]
    assert [n["nombre"] for n in arbol[0]["children"]] == ["FESPA"]
    # API: detalle, líneas y crear una oferta con ella → 404.
    for url in (f"/api/erp/plantillas/{ids['p_escuelas']}",
                f"/api/erp/plantillas/{ids['p_raiz']}",
                f"/api/erp/plantillas/{ids['p_escuelas']}/lineas?destino=pedido"):
        assert http.get(url, headers=h).status_code == 404, url
    r = http.post("/api/erp/ofertas", headers=h, json={
        "contact_id": contact_id, "serie": 1, "plantilla_id": ids["p_escuelas"]})
    assert r.status_code == 404
    assert http.get(f"/api/erp/plantillas/{ids['p_fespa']}", headers=h).status_code == 200
    # Tampoco puede sacarla de su carpeta a la raíz, ni meter una en otra.
    r = http.patch(f"/api/erp/plantillas/{ids['p_fespa']}", headers=h,
                   json={"carpeta_id": None})
    assert r.status_code == 422
    r = http.patch(f"/api/erp/plantillas/{ids['p_fespa']}", headers=h,
                   json={"carpeta_id": ids["escuelas"]})
    assert r.status_code == 404
    # El administrador lo sigue viendo todo.
    assert len(http.get("/api/erp/plantillas", headers=admin).json()["items"]) == 3


def test_comercial_sin_ninguna_restriccion_las_ve_todas(http, session_factory):
    with session_factory() as s:
        ids = _carpetas_y_plantillas(s)
        otro_id = s.scalar(select(User.id).where(User.email == "pedidos@example.com"))
        # Hay restricciones, pero para OTRO usuario.
        s.add(PlantillaCarpetaAcceso(carpeta_id=ids["escuelas"], user_id=otro_id,
                                     created_at=svc.datetime.now(svc.UTC)))
        s.commit()
    h = auth_headers(http, "comercial")
    items = http.get("/api/erp/plantillas", headers=h).json()["items"]
    assert {p["id"] for p in items} == {ids["p_escuelas"], ids["p_fespa"], ids["p_raiz"]}
    assert len(http.get("/api/erp/plantillas", params={"q": "DM3H"},
                        headers=h).json()["items"]) == 3
    assert [n["nombre"] for n in http.get("/api/erp/plantillas/carpetas",
                                          headers=h).json()["items"]] == ["Escuelas", "Ferias"]


def test_acceso_a_carpetas_lo_gestiona_administracion(http, session_factory):
    with session_factory() as s:
        ids = _carpetas_y_plantillas(s)
    r = http.put(f"/api/erp/plantillas/carpetas/{ids['ferias']}/acceso",
                 headers=auth_headers(http, "comercial"), json={"user_ids": []})
    assert r.status_code == 403
    r = http.put(f"/api/erp/plantillas/carpetas/{ids['ferias']}/acceso",
                 headers=auth_headers(http, "admin"), json={"user_ids": ["no-existe"]})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Carpetas: niveles, ciclos, borrar no borra plantillas
# ---------------------------------------------------------------------------


def test_carpetas_tres_niveles_sin_ciclos_y_borrar_no_borra_plantillas(http, session_factory):
    h = auth_headers(http, "comercial")

    def crear(nombre: str, parent_id: str | None = None) -> str:
        r = http.post("/api/erp/plantillas/carpetas", headers=h,
                      json={"nombre": nombre, "parent_id": parent_id})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    a = crear("A")
    b = crear("B", a)
    c = crear("C", b)
    r = http.post("/api/erp/plantillas/carpetas", headers=h, json={"nombre": "D", "parent_id": c})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "profundidad_maxima"
    r = http.patch(f"/api/erp/plantillas/carpetas/{a}", headers=h, json={"parent_id": c})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ciclo"
    otra = crear("Otra")
    # Mover A (3 niveles) dentro de «Otra» pasaría de 3: también se rechaza.
    r = http.patch(f"/api/erp/plantillas/carpetas/{a}", headers=h, json={"parent_id": otra})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "profundidad_maxima"
    with session_factory() as s:
        pid = _plantilla(s, "Dentro de B", carpeta_id=b)
    assert http.delete(f"/api/erp/plantillas/carpetas/{b}", headers=h).status_code == 200
    detalle = http.get(f"/api/erp/plantillas/{pid}", headers=h).json()
    assert detalle["carpeta_id"] is None                      # sube a la raíz, no se borra
    arbol = http.get("/api/erp/plantillas/carpetas", headers=h).json()["items"]
    assert {n["nombre"] for n in arbol} == {"A", "C", "Otra"}  # C sube a la raíz


def test_editar_mover_etiquetar_y_desactivar(http, session_factory):
    with session_factory() as s:
        pid = _plantilla(s, "Kit", etiquetas=["DTF"])
        carpeta = PlantillaCarpeta(nombre="Escuelas")
        s.add(carpeta)
        s.commit()
        carpeta_id = carpeta.id
    h = auth_headers(http, "comercial")
    r = http.patch(f"/api/erp/plantillas/{pid}", headers=h, json={
        "nombre": "Kit escuelas", "idioma": "fr", "carpeta_id": carpeta_id,
        "etiquetas": ["dtf", "Escuelas", "escuelas", " FESPA "], "activa": False})
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["nombre"], p["idioma"], p["carpeta"], p["activa"]) == (
        "Kit escuelas", "fr", "Escuelas", False)
    assert p["etiquetas"] == ["dtf", "Escuelas", "FESPA"]       # sin repetidas
    etiquetas = http.get("/api/erp/plantillas/etiquetas", headers=h).json()["items"]
    assert {e["norm"] for e in etiquetas} == {"dtf", "escuelas", "fespa"}
    assert http.get("/api/erp/plantillas", params={"etiqueta": "fespa"},
                    headers=h).json()["total"] == 1
    assert http.get("/api/erp/plantillas", params={"activas": True},
                    headers=h).json()["items"] == []
    # Sin permiso de proformas (taller) no se edita.
    assert http.patch(f"/api/erp/plantillas/{pid}", headers=auth_headers(http, "sat"),
                      json={"nombre": "x"}).status_code == 403


# ---------------------------------------------------------------------------
# Revisión (ronda 1): borrar carpetas, renombrar acotado, rutas, pedidos web,
# comisiones, artículos de un pedido y las líneas del pedido
# ---------------------------------------------------------------------------


def _acotar(http: TestClient, session_factory, carpeta_id: str) -> None:
    with session_factory() as s:
        comercial_id = s.scalar(select(User.id).where(User.email == "comercial@example.com"))
    r = http.put(f"/api/erp/plantillas/carpetas/{carpeta_id}/acceso",
                 headers=auth_headers(http, "admin"), json={"user_ids": [comercial_id]})
    assert r.status_code == 200, r.text


def test_borrar_una_carpeta_nunca_suelta_una_restriccion(http, session_factory):
    with session_factory() as s:
        ids = _carpetas_y_plantillas(s)
    _acotar(http, session_factory, ids["fespa"])
    h = auth_headers(http, "comercial")
    admin = auth_headers(http, "admin")

    def ve() -> list[str]:
        return [p["id"] for p in http.get("/api/erp/plantillas", headers=h).json()["items"]]

    assert ve() == [ids["p_fespa"]]
    # El acotado no borra carpetas (su contenido iría a la raíz, que no ve)…
    r = http.delete(f"/api/erp/plantillas/carpetas/{ids['fespa']}", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "acceso_restringido"
    # …y nadie borra una carpeta que acota a alguien sin quitarle antes el acceso.
    r = http.delete(f"/api/erp/plantillas/carpetas/{ids['fespa']}", headers=admin)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "carpeta_con_acceso"
    assert ve() == [ids["p_fespa"]]                       # sigue acotado
    # Borrar la madre (sin acceso) no le cambia nada: FESPA sube a la raíz.
    assert http.delete(f"/api/erp/plantillas/carpetas/{ids['ferias']}",
                       headers=admin).status_code == 200
    assert ve() == [ids["p_fespa"]]
    # Quitado el acceso, ya se puede borrar.
    assert http.put(f"/api/erp/plantillas/carpetas/{ids['fespa']}/acceso", headers=admin,
                    json={"user_ids": []}).status_code == 200
    assert http.delete(f"/api/erp/plantillas/carpetas/{ids['fespa']}",
                       headers=admin).status_code == 200


def test_el_acotado_renombra_su_carpeta_y_no_ve_el_nombre_de_la_madre(http, session_factory):
    with session_factory() as s:
        ids = _carpetas_y_plantillas(s)
    _acotar(http, session_factory, ids["fespa"])
    h = auth_headers(http, "comercial")
    arbol = http.get("/api/erp/plantillas/carpetas", headers=h).json()["items"]
    assert [(n["nombre"], n["parent_id"]) for n in arbol] == [("FESPA", None)]
    # La pantalla manda el nombre y la madre tal cual: renombrar funciona.
    for cuerpo in ({"nombre": "FESPA 2027", "parent_id": ids["ferias"]},
                   {"nombre": "FESPA 2027", "parent_id": None},
                   {"nombre": "FESPA 2027"}):
        r = http.patch(f"/api/erp/plantillas/carpetas/{ids['fespa']}", headers=h, json=cuerpo)
        if cuerpo.get("parent_id", ids["ferias"]) is None:
            # Sacarla de verdad a la raíz sí es un cambio, y no lo puede hacer.
            assert r.status_code == 422, cuerpo
        else:
            assert r.status_code == 200, (cuerpo, r.text)
    # La ruta de sus plantillas no enseña «Ferias».
    item = http.get("/api/erp/plantillas", headers=h).json()["items"][0]
    assert item["carpeta"] == "FESPA 2027"
    admin = auth_headers(http, "admin")
    assert http.get(f"/api/erp/plantillas/{ids['p_fespa']}",
                    headers=admin).json()["carpeta"] == "Ferias / FESPA 2027"


class _FakeConArticulos(FakeClient):
    """El doble de FACTUSOL con F_ART: devuelve todos los artículos y los
    lectores filtran (las consultas `IN … OR …` no las entiende el doble)."""

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        if tabla == "F_ART":
            self.calls.append((tabla, filtro))
            return list(self.tables.get("F_ART", []))
        return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)


def test_guardar_desde_pedido_web_comisiones_y_articulos(http, session_factory):
    fake = _FakeConArticulos({"F_ART": [
        {"CODART": "00001", "EQUART": "CDR80WPT", "DESART": "Plotter"},
        {"CODART": "00002", "EQUART": "INK-500", "DESART": "Tinta"},
    ]})
    with session_factory() as s:
        web = Order(order_number="WEB-1001", external_source="woocommerce", total_amount=0)
        manual = Order(order_number="MANUAL-000010", external_source="manual", total_amount=0)
        s.add_all([web, manual])
        s.flush()
        s.add_all([
            OrderLine(order_id=web.id, position=0, product_sku="CDR80WPT", description="Plotter",
                      quantity=1, unit_price=900, tax_rate=21, line_total=900),
            OrderLine(order_id=web.id, position=1, product_sku="woo-fee-7",
                      description="PayPal cost 4%", quantity=1, unit_price=36, tax_rate=21,
                      line_total=36, line_kind="fee"),
            OrderLine(order_id=web.id, position=2, product_sku="woo-fee-8",
                      description="Cupón", quantity=1, unit_price=-10, tax_rate=21,
                      line_total=-10, line_kind="fee"),
            OrderLine(order_id=manual.id, position=0, product_sku="00002",
                      product_codart="00002", description="Tinta", quantity=2,
                      unit_price=50, tax_rate=21, line_total=100),
        ])
        s.commit()
        web_id, manual_id = web.id, manual.id
    # El comercial no ve pedidos web: tampoco los guarda como plantilla.
    with _patch(fake):
        r = http.post("/api/erp/plantillas", headers=auth_headers(http, "comercial"), json={
            "nombre": "Plotter web", "origen": {"tipo": "pedido", "order_id": web_id}})
    assert r.status_code == 404
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PlantillaDocumento)) == 0
    # Quien sí los ve: sin las comisiones ni cupones, con el CODART resuelto.
    with _patch(fake):
        r = http.post("/api/erp/plantillas", headers=auth_headers(http, "admin"), json={
            "nombre": "Plotter web", "origen": {"tipo": "pedido", "order_id": web_id}})
        assert r.status_code == 201, r.text
        assert [(ln["codart"], ln["sku"]) for ln in r.json()["lineas"]] == \
            [("00001", "CDR80WPT")]
        # Un pedido que solo guarda el CODART: se completa el SKU comercial.
        r = http.post("/api/erp/plantillas", headers=auth_headers(http, "comercial"), json={
            "nombre": "Tinta", "origen": {"tipo": "pedido", "order_id": manual_id}})
    assert r.status_code == 201, r.text
    assert [(ln["codart"], ln["sku"]) for ln in r.json()["lineas"]] == [("00002", "INK-500")]
    assert fake.writes == []


def test_lineas_del_pedido_textos_cantidad_cero_y_precio_neto_a_centimos(session_factory):
    with session_factory() as s:
        pid = _plantilla(s, "Kit formación", lineas=[
            {"descripcion": "KIT FORMACIÓN — incluye:", "cantidad": 0, "precio": 0},
            {"codart": "OPC1", "sku": "OPC1", "descripcion": "Opcional: segundo horno",
             "cantidad": 0, "precio": 900},
            {"codart": "00001", "sku": "DM3H", "descripcion": "Horno DTF", "cantidad": 3,
             "precio": 33.33, "descuento_pct": 10},
            {"descripcion": "Entrega en 15 días.", "cantidad": 0, "precio": 0},
        ])
        plantilla = s.get(PlantillaDocumento, pid)
        lineas, avisos = svc.lineas_para(plantilla, "pedido")
    assert len(lineas) == 1
    ln = lineas[0]
    assert ln["description"] == "KIT FORMACIÓN — incluye: Horno DTF Entrega en 15 días."
    assert ln["quantity"] == 3 and ln["unit_price"] == 30.0        # 29,997 → 30,00
    assert ln["product_codart"] == "00001" and ln["product_sku"] == "DM3H"
    assert any("cantidad 0" in a for a in avisos)
    assert any("neto" in a for a in avisos)
    with session_factory() as s:
        solo_texto = _plantilla(s, "Solo texto", lineas=[
            {"descripcion": "Nota", "cantidad": 0, "precio": 0}])
        lineas, avisos = svc.lineas_para(s.get(PlantillaDocumento, solo_texto), "pedido")
    assert lineas == [] and any("ninguna línea" in a for a in avisos)
