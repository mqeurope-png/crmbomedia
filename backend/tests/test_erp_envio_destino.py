"""Destino del envío (Genei): dirección, teléfono y email para CUALQUIER origen.

Bug: en pedidos de factura, albarán o proforma el envío Genei salía sin
dirección/teléfono/email (el resolver solo miraba `shipping_address`, el
contacto del pedido y la empresa, que no tiene teléfono ni email; esos pedidos
no traen contacto). Ahora:

- al CREAR el pedido desde el documento se guarda su bloque de entrega
  (`CDO/CPO/CCP/CPR/TEL/CEM*`) en `shipping_contact`;
- el resolver lee también `shipping_contact` (muestras) y el primer contacto
  de la empresa, y con `completar=True` lee FACTUSOL (cabecera del documento
  y ficha F_CLI) para los pedidos creados antes.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order
from app.erp.orders_from_factusol import create_order_from_factusol_document
from app.erp.shipping_destination import resolve_shipping_destination
from app.main import app
from app.models.crm import Company, Contact
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_pedido_desde_factusol import (
    FPA,
    FakeClient,
    _alb,
    _fac,
    _lal,
    _lfa,
    _lps,
    _pre,
)


#: Bloque de entrega de la cabecera, por sufijo (lo que el escritorio copia de
#: F_CLI al elegir el cliente, o la dirección alternativa si se editó).
def _entrega(sfx: str, **over: Any) -> dict[str, Any]:
    base = {
        f"CDO{sfx}": "C/ Industria 7", f"CPO{sfx}": "Sabadell", f"CCP{sfx}": "08201",
        f"CPR{sfx}": "Barcelona", f"CPA{sfx}": "ES", f"TEL{sfx}": "937000000",
        f"CEM{sfx}": "almacen@acme.es", f"CNI{sfx}": "B12345678",
    }
    base.update(over)
    return base


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_ALB": [{**_alb(8), **_entrega("ALB")}],
        "F_LAL": [_lal(8, 1, desc="Cabezal", precio=250)],
        "F_FAC": [{**_fac(8), **_entrega("FAC", CEMFAC=None, EMAFAC="facturas@acme.es")}],
        "F_LFA": [_lfa(8, 1, desc="Cabezal", precio=250)],
        "F_PRE": [{**_pre(574), **_entrega("PRE", CEMPRE="compras@acme.es")}],
        "F_LPS": [_lps(574, 1, desc="Cabezal", precio=250)],
        "F_FPA": FPA,
    }


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        # La empresa (vinculada al CODCLI 55555) NO tiene teléfono ni email.
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
        seed.commit()
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


def _completo(campos: dict[str, str]) -> None:
    for campo in ("address", "postal_code", "city", "country", "phone", "email"):
        assert campos[campo], f"falta {campo}: {campos}"


@pytest.mark.parametrize(("doc_type", "serie", "codigo", "email"), [
    ("albaranes", 5, 8, "almacen@acme.es"),
    ("facturas", 5, 8, "facturas@acme.es"),       # en facturas el email puede ir en EMA*
    ("presupuestos", 1, 574, "compras@acme.es"),  # proforma
])
def test_pedido_de_factura_albaran_o_proforma_guarda_su_destino(
    session_factory, doc_type, serie, codigo, email,
):
    fake = FakeClient(_tables())
    with session_factory() as s:
        order = create_order_from_factusol_document(
            s, fake, doc_type=doc_type, serie=serie, codigo=codigo, ejercicio="2026",
        )
        s.commit()
        destino = json.loads(order.packing_json)["shipping_contact"]
        assert destino["phone"] == "937000000" and destino["email"] == email
        # Sin volver a FACTUSOL: el resolver lo tiene ya en el pedido.
        campos, fuentes = resolve_shipping_destination(s, order)
        _completo(campos)
        assert campos["address"] == "C/ Industria 7" and campos["email"] == email
        assert fuentes["address"] == fuentes["phone"] == "destinatario"
        # No va a `shipping_address`: el albarán sigue leyendo F_CLI.
        assert "shipping_address" not in json.loads(order.packing_json)
        assert fake.writes == []


def test_pedido_web_manual_y_muestra_tienen_destino(session_factory):
    with session_factory() as s:
        persona = Contact(first_name="Ana", last_name="Pi", email="ana@cliente.es",
                          phone="600111222")
        s.add(persona)
        s.add(Contact(first_name="Eva", last_name="Ruiz", company_id="acme",
                      email="eva@acme.es", phone="933111222", is_active=True))
        s.flush()
        pedidos = {
            # WEB: el bloque de Woo lo trae todo.
            "web": Order(order_number="BOP-1", external_source="woocommerce",
                         packing_json=json.dumps({"shipping_address": {
                             "address_line": "Rue 1", "city": "Paris", "postal_code": "75001",
                             "country": "FR", "name": "Ana", "phone": "+33 1",
                             "email": "ana@web.fr"}})),
            # Manual con contacto: dirección del alta + tel/email del contacto.
            "manual": Order(order_number="MAN-1", external_source="manual", contact_id=persona.id,
                            packing_json=json.dumps({"shipping_address": {
                                "address_line": "Av. 2", "city": "Madrid",
                                "postal_code": "28001", "country": "ES"}})),
            # Manual SIN contacto: tel/email del contacto de la empresa.
            "manual_empresa": Order(order_number="MAN-2", external_source="manual",
                                    company_id="acme", packing_json=json.dumps({
                                        "shipping_address": {"address_line": "Av. 3",
                                                             "city": "Madrid",
                                                             "postal_code": "28002",
                                                             "country": "ES"}})),
            # Muestra: dirección + destinatario (`shipping_contact`).
            "muestra": Order(order_number="MUE-1", external_source="manual", order_kind="sample",
                             shipping_name="Taller Pérez", packing_json=json.dumps({
                                 "shipping_address": {"address_line": "C/ 4", "city": "Bilbao",
                                                      "postal_code": "48001", "country": "ES"},
                                 "shipping_contact": {"company": "Taller Pérez", "name": "Luis",
                                                      "email": "luis@taller.es",
                                                      "phone": "944000000"}})),
        }
        s.add_all(pedidos.values())
        s.commit()
        esperado = {
            "web": ("ana@web.fr", "+33 1"),
            "manual": ("ana@cliente.es", "600111222"),
            "manual_empresa": ("eva@acme.es", "933111222"),
            "muestra": ("luis@taller.es", "944000000"),
        }
        for clave, order in pedidos.items():
            campos, _ = resolve_shipping_destination(s, order)
            _completo(campos)
            assert (campos["email"], campos["phone"]) == esperado[clave], clave


def test_pedido_antiguo_de_factusol_completa_leyendo_factusol(session_factory):
    """Creado ANTES de guardar el bloque de entrega: solo `factusol_source`.
    Al pintar la sección no se sale a FACTUSOL; al abrir «Crear envío»
    (`completar`), se lee la cabecera del documento y la ficha del cliente."""
    llamadas: list[str] = []

    def factusol(_s):
        llamadas.append("conexion")
        return FakeClient(_tables()), "2026"

    with session_factory() as s:
        order = Order(order_number="FAC-5-8", external_source="factusol_factura",
                      company_id="acme", packing_json=json.dumps({"factusol_source": {
                          "doc_type": "facturas", "serie": 5, "codigo": 8,
                          "cliente_codigo": "55555"}}))
        s.add(order)
        s.commit()
        campos, _ = resolve_shipping_destination(s, order, factusol=factusol)
        assert not campos["phone"] and not campos["email"]
        assert llamadas == []                            # sin `completar`, no sale
        campos, fuentes = resolve_shipping_destination(
            s, order, completar=True, factusol=factusol)
        _completo(campos)
        assert campos["phone"] == "937000000"
        assert campos["email"] == "facturas@acme.es"
        assert fuentes["address"] == "documento"


def test_si_el_documento_no_trae_email_lo_completa_la_ficha_del_cliente(session_factory,
                                                                          monkeypatch):
    tablas = _tables()
    tablas["F_ALB"] = [{**_alb(8), **_entrega("ALB", CEMALB=None)}]
    monkeypatch.setattr(
        "app.integrations.factusol.customers.get_customer",
        lambda client, codcli, ejercicio: {"codcli": codcli, "emacli": "info@acme.es",
                                           "telcli": "930000000", "domcli": "Sede 1"},
    )
    with session_factory() as s:
        order = Order(order_number="ALB-5-8", external_source="factusol_albaran",
                      company_id="acme", packing_json=json.dumps({"factusol_source": {
                          "doc_type": "albaranes", "serie": 5, "codigo": 8}}))
        s.add(order)
        s.commit()
        campos, fuentes = resolve_shipping_destination(
            s, order, completar=True, factusol=lambda _s: (FakeClient(tablas), "2026"))
        assert campos["address"] == "C/ Industria 7"     # la del documento (bloque)
        assert campos["email"] == "info@acme.es"         # de la ficha F_CLI
        assert fuentes["email"] == "ficha"


def test_prefill_con_completar_rellena_telefono_y_email(http, session_factory, monkeypatch):
    monkeypatch.setattr("app.erp.web_order_company.factusol_client_and_ejercicio",
                        lambda _s: (FakeClient(_tables()), "2026"))
    with session_factory() as s:
        order = Order(order_number="PRO-574", external_source="factusol_proforma",
                      preparation_status="packed", company_id="acme",
                      packing_json=json.dumps({"factusol_source": {
                          "doc_type": "presupuestos", "serie": 1, "codigo": 574}}))
        s.add(order)
        s.commit()
        oid = order.id
    h = auth_headers(http)
    base = http.get(f"/api/erp/orders/{oid}/genei/prefill", headers=h).json()
    assert base["destination"]["phone"] == "" and base["destination"]["email"] == ""
    r = http.get(f"/api/erp/orders/{oid}/genei/prefill?completar=true", headers=h)
    assert r.status_code == 200, r.text
    dest = r.json()["destination"]
    assert dest["phone"] == "937000000"
    assert dest["email"] == "compras@acme.es"
    assert dest["address"] == "C/ Industria 7" and dest["town"] == "Sabadell"
    assert r.json()["destination_sources"]["email"] == "documento"
    assert r.json()["missing"] == []
