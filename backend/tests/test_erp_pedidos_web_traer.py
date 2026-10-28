"""ERP · «Traer pedido web»: un `on-hold` de la tienda traído a mano.

Figurante: el pedido 5873 de fluxlasers.es (el anexo de Bart, 28/10): compra
sin cuenta (`customer_id` 0), transferencia sin cobrar (`date_paid` null), CIF
en `meta_data._billing_nif`, portes con su IVA, una línea SIN sku, provincia
«T». Total 44,77 €.

Lo que se comprueba:

- traerlo crea UN pedido completo (líneas, portes, cliente/empresa, totales,
  `FLUXLA-5873`, referencias externas) por el camino del importador;
- traído y luego pagado queda IGUAL, campo a campo, que el mismo pedido
  importado solo al llegar como `processing`;
- el webhook de pago ACTUALIZA el traído (no duplica) y el de cancelación lo
  anula; traer dos veces (o dos a la vez) no crea dos;
- cancelado en la web sin traer, `pending`, `failed`: ni en la lista ni se
  traen; lo que cambió en la web al pagarlo queda anotado;
- el veredicto de FACTUSOL (enlazado / no_encontrado / posible / dudoso /
  sin_factusol) sin escribir NADA en FACTUSOL;
- permisos y la forma de la API.

Ninguna llamada sale a una tienda ni a FACTUSOL reales: son dobles.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp import pedidos_web
from app.erp.models import (
    IntegrationEvent,
    IntegrationEventStatus,
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
)
from app.erp.order_cancel import AUTO_CANCEL_REASON
from app.erp.pedidos_web import (
    PedidoWebError,
    es_traido_a_mano,
    pedidos_en_espera,
    traer_pedido_web,
)
from app.erp.sat_autoenqueue import AUTO_ENQUEUE_REASON
from app.integrations.factusol.client import FactusolError
from app.integrations.woocommerce import jobs as woo_jobs
from app.integrations.woocommerce import mapper as woo_mapper
from app.integrations.woocommerce.client import WooError
from app.main import app
from app.models.crm import Company, Contact, ExternalSystem, User
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_woo_pagados_que_faltan import Tienda
from tests.test_factusol_documents import FakeClient

CIF = "F43123456"
CODCLI = 3854
REFPCL = "FLE-005873"          # prefijo de fluxlasers configurado (FLE) + nº a 6


# --- el figurante ------------------------------------------------------------------


def _pedido_5873(**over: Any) -> dict[str, Any]:
    """`pedido_5873_on_hold.json` tal cual (con datos de mentira en «»)."""
    base: dict[str, Any] = {
        "id": 5873, "number": "5873", "status": "on-hold",
        "order_key": "wc_order_mki3tFQhSok7T",
        "currency": "EUR", "date_created": "2026-10-23T09:18:59", "date_paid": None,
        "discount_total": "0.00", "shipping_total": "6.00",
        "total": "44.77", "total_tax": "7.77", "prices_include_tax": False,
        "customer_id": 0,
        "payment_method": "bacs", "payment_method_title": "Transferencia bancaria directa",
        "transaction_id": "",
        "customer_note": "Es un centro educativo. Horario de 9.00 a 16.30 de lunes a viernes",
        "billing": {"first_name": "Montse", "last_name": "Ferrer Vidal",
                    "company": "Escola de Ponent",
                    "address_1": "Carrer Major 12", "address_2": "Edifici B",
                    "city": "Reus", "state": "T", "postcode": "43201", "country": "ES",
                    "email": "secretaria@escoladeponent.cat", "phone": "977000111"},
        "shipping": {"first_name": "Montse", "last_name": "Ferrer Vidal",
                     "company": "Escola de Ponent", "address_1": "Carrer Major 12",
                     "city": "Reus", "state": "T", "postcode": "43201", "country": "ES",
                     "phone": "977000111"},
        "line_items": [
            {"id": 3866, "name": "Ador & BB2 AF Plastic Probe Cubierta", "product_id": 4972,
             "variation_id": 0, "quantity": 1, "sku": "PMP-12003", "price": 5,
             "subtotal": "5.00", "total": "5.00", "total_tax": "1.05"},
            {"id": 3867, "name": "Ador & BB2 AF Plastic Probe", "product_id": 4971,
             "variation_id": 0, "quantity": 1, "sku": "PMP-12002", "price": 5,
             "subtotal": "5.00", "total": "5.00", "total_tax": "1.05"},
            {"id": 3868, "name": "Pack 50 Láminas A4 Cartón Canal 3 mm", "product_id": 4326,
             "variation_id": 0, "quantity": 1, "sku": "", "price": 21,
             "subtotal": "21.00", "total": "21.00", "total_tax": "4.41"},
        ],
        "shipping_lines": [
            {"id": 3869, "method_title": "Envío 48/72h (Una vez procesado el pedido)",
             "method_id": "flexible_shipping_single", "total": "6.00", "total_tax": "1.26"},
        ],
        "tax_lines": [{"id": 3870, "rate_code": "ES-IVA-1", "rate_percent": 21,
                       "tax_total": "6.51"}],
        "meta_data": [
            {"key": "_billing_nif", "value": CIF},
            {"key": "_shipping_nif", "value": CIF},
            {"key": "_shipping_email", "value": "secretaria@escoladeponent.cat"},
            {"key": "is_vat_exempt", "value": "no"},
            {"key": "_wc_order_attribution_source_type", "value": "referral"},
        ],
    }
    base.update(over)
    return base


def _pagado(**over: Any) -> dict[str, Any]:
    """El mismo pedido cuando la tienda cobra la transferencia (`processing`)."""
    datos: dict[str, Any] = {"status": "processing", "date_paid": "2026-10-24T08:30:00",
                             "date_paid_gmt": "2026-10-24T08:30:00"}
    return _pedido_5873(**{**datos, **over})


# --- dobles ------------------------------------------------------------------------


class TiendaWoo(Tienda):
    """El doble de tienda de «pagados que faltan» + `get_order` con 404 de
    verdad (la tienda dice que no existe) y caída (503)."""

    def get_order(self, order_id: int):
        self.llamadas.append({"tienda": self.store.account_id, "get_order": order_id})
        if self.caida:
            raise WooError("tienda caída", status=503)
        for p in self.pedidos:
            if p["id"] == order_id:
                return copy.deepcopy(p)
        raise WooError(f"GET /orders/{order_id} → 404", status=404)


def _tiendas(pedidos: dict[str, list[dict]], llamadas: list[dict] | None = None,
             *, caidas: tuple[str, ...] = (), ignora_filtro: bool = False):
    def factory(store, **_kw):
        return TiendaWoo(store, pedidos.get(store.account_id, []),
                         llamadas if llamadas is not None else [],
                         caida=store.account_id in caidas, ignora_filtro=ignora_filtro)
    return factory


class Factusol(FakeClient):
    """El `FakeClient` de siempre (lectura de tablas en memoria) + el filtro
    `IN (…)` con el que se busca el cliente por NIF. Cualquier escritura queda
    apuntada: en todo este flujo NO debe haber ninguna."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]], *, caido: bool = False):
        super().__init__(tables)
        self.caido = caido
        self.escrituras: list[tuple[str, Any]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if self.caido:
            raise FactusolError("FACTUSOL no responde", status=503)
        if " IN (" in filtro:
            self.calls.append((tabla, filtro))
            columna, _, lista = filtro.partition(" IN (")
            valores = {v.strip().strip("'") for v in lista.rstrip(")").split(",")}
            campo = "NIFCLI" if "NIFCLI" in columna else columna.strip()
            return [
                r for r in self.tables.get(tabla, [])
                if str(r.get(campo) or "").upper().replace(" ", "").replace("-", "") in valores
            ]
        return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)

    def write_record(self, tabla, data, *, ejercicio=None):
        self.escrituras.append(("write_record", tabla))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.escrituras.append(("update_record", tabla))
        return {"ok": True}

    def delete_records(self, tabla, filtro, *, ejercicio=None):
        self.escrituras.append(("delete_records", tabla))
        return {"ok": True}


def _pcl(**over: Any) -> dict[str, Any]:
    row = {"CODPCL": 431, "TIPPCL": "5", "REFPCL": REFPCL, "CLIPCL": CODCLI,
           "CNOPCL": "ESCOLA DE PONENT", "TOTPCL": 44.77, "FECPCL": "2026-10-23",
           "ESTPCL": 0}
    row.update(over)
    return row


def _cli(**over: Any) -> dict[str, Any]:
    row = {"CODCLI": CODCLI, "NIFCLI": f"ES{CIF}", "NOFCLI": "ESCOLA DE PONENT",
           "NOCCLI": "ESCOLA DE PONENT", "DOMCLI": "CARRER MAJOR 12", "POBCLI": "REUS",
           "CPOCLI": "43201", "PROCLI": "TARRAGONA", "PAICLI": "724",
           "EMACLI": "secretaria@escoladeponent.cat", "TELCLI": "977000111"}
    row.update(over)
    return row


def _factusol(pcl: list[dict] | None = None, cli: list[dict] | None = None,
              **kw: Any) -> Factusol:
    return Factusol({"F_PCL": [_pcl()] if pcl is None else pcl,
                     "F_CLI": [_cli()] if cli is None else cli}, **kw)


# --- BD, tiendas, sesiones ------------------------------------------------------------


def _engine_factory() -> tuple[Any, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
    return engine, f


@pytest.fixture()
def fs(monkeypatch) -> Factusol:
    """FACTUSOL simulado (el F_PCL de la web y su cliente), el mismo para el
    importador (empresa) y para el veredicto."""
    fake = _factusol()
    monkeypatch.setattr("app.erp.web_order_company.factusol_client_and_ejercicio",
                        lambda _s: (fake, "2026"))
    return fake


@pytest.fixture()
def factory(fs) -> Generator[sessionmaker, None, None]:
    engine, f = _engine_factory()
    # Los jobs del webhook abren su propia sesión: que usen esta misma BD.
    with patch.object(woo_jobs, "_session_factory", return_value=f):
        yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str = "fluxlasers", nombre: str = "fluxlasers.es",
           base_url: str = "https://fluxlasers.es") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=nombre,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=base_url,
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
        # La app Woo→FACTUSOL usa FLE para fluxlasers (el derivado FLU no vale).
        metadata_json=json.dumps({"factusol_ref_prefix": "FLE"}) if slug == "fluxlasers"
        else None,
    )
    s.add(a)
    s.commit()
    return a


def _user(s: Session, role: str = "pedidos") -> User:
    return s.scalar(select(User).where(User.email == f"{role}@example.com"))


def _traer(factory, store_id: str, pedidos: dict[str, list[dict]], *, woo_id: int = 5873,
           role: str = "pedidos") -> dict:
    with factory() as s:
        return traer_pedido_web(s, store_id=store_id, woo_id=woo_id, user=_user(s, role),
                                client_factory=_tiendas(pedidos))


def _webhook(factory, payload: dict, *, delivery: str, topic: str = "order.updated",
             account_id: str = "fluxlasers") -> dict:
    """Un webhook de la tienda procesado por el job de siempre (refetch +
    `import_woo_order`): el pedido «fresco» es `payload`."""
    with factory() as s:
        ev = IntegrationEvent(system="woocommerce", account_id=account_id,
                              external_event_id=delivery, event_type=topic,
                              payload_json=json.dumps({"id": payload["id"]}))
        s.add(ev)
        s.commit()
        event_id = ev.id
    fake = MagicMock()
    fake.get_order.return_value = copy.deepcopy(payload)
    with patch.object(woo_jobs, "WooHTTPClient", return_value=fake):
        out = woo_jobs.process_webhook_event(event_id)
    with factory() as s:
        assert s.get(IntegrationEvent, event_id).status == IntegrationEventStatus.PROCESSED
    return out


def _count(s: Session) -> int:
    return int(s.scalar(select(func.count(Order.id))) or 0)


def _packing(o: Order) -> dict:
    return json.loads(o.packing_json) if o.packing_json else {}


# --- traer un on-hold ----------------------------------------------------------------


def test_traer_un_on_hold_crea_un_pedido_completo(factory, client, fs):
    with factory() as s:
        store_id = _store(s).id
    tienda = _tiendas({"fluxlasers": [_pedido_5873()]})
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        r = client.post("/api/erp/pedidos-web/traer",
                        json={"store_id": store_id, "woo_id": 5873},
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"order_id", "order_number", "creado", "factusol"}
    assert body["order_number"] == "FLUXLA-5873" and body["creado"] is True
    assert set(body["factusol"]) == {"codigo", "mensaje", "referencia"}
    assert body["factusol"]["codigo"] == "enlazado"
    assert body["factusol"]["referencia"] == REFPCL

    with factory() as s:
        assert _count(s) == 1
        o = s.get(Order, body["order_id"])
        # Referencias externas: la llave del importador y el nº de siempre.
        assert (o.external_source, o.external_id, o.store_id) == (
            OrderSource.WOOCOMMERCE, "5873", store_id)
        assert o.order_number == "FLUXLA-5873"
        assert float(o.total_amount) == pytest.approx(44.77) and o.currency == "EUR"
        assert o.woo_status == "on-hold"
        # Pago honesto (aún no cobrado) y a revisar, como un pedido manual.
        assert o.payment_status == PaymentStatus.PENDING
        assert o.preparation_status == PreparationStatus.PENDING_REVIEW
        assert (o.payment_method, o.payment_method_title) == (
            "bacs", "Transferencia bancaria directa")
        assert es_traido_a_mano(o)
        assert o.manual_web_import_by_user_id == _user(s).id
        # Cliente y empresa (CIF de meta_data), ya enlazada a su F_CLI.
        contacto = s.get(Contact, o.contact_id)
        assert contacto.email == "secretaria@escoladeponent.cat"
        empresa = s.get(Company, o.company_id)
        assert (empresa.name, empresa.tax_id) == ("Escola de Ponent", CIF)
        assert empresa.factusol_company_id == str(CODCLI)
        # Líneas: las 3 de mercancía (la del pack SIN sku incluida) + portes.
        lineas = s.scalars(select(OrderLine).where(OrderLine.order_id == o.id)
                           .order_by(OrderLine.position)).all()
        assert [(ln.product_sku, float(ln.quantity), float(ln.line_total),
                 float(ln.tax_rate), bool(ln.is_shipping), ln.line_kind) for ln in lineas] == [
            ("PMP-12003", 1.0, 5.0, 21.0, False, None),
            ("PMP-12002", 1.0, 5.0, 21.0, False, None),
            ("woo-pid-4326", 1.0, 21.0, 21.0, False, None),
            ("woo-shipping-3869", 1.0, 6.0, 21.0, True, "shipping"),
        ]
        assert lineas[2].description == "Pack 50 Láminas A4 Cartón Canal 3 mm"
        # Base + IVA de las líneas = el total de la web.
        assert sum(float(ln.line_total) * (1 + float(ln.tax_rate) / 100)
                   for ln in lineas) == pytest.approx(44.77, abs=0.01)
        destino = _packing(o)["shipping_address"]
        assert (destino["city"], destino["state"], destino["nif"]) == ("Reus", "T", CIF)
        assert _packing(o)["traido_a_mano"] == {"factusol": body["factusol"],
                                                "diferencias": None}

    # La ficha lo cuenta: quién, cuándo y qué dijo FACTUSOL.
    d = client.get(f"/api/erp/orders/{body['order_id']}",
                   headers=auth_headers(client, "pedidos"))
    assert d.status_code == 200, d.text
    traido = d.json()["traido_a_mano"]
    assert set(traido) == {"at", "por", "factusol", "diferencias"}
    assert traido["por"] == "Pedidos User"
    assert traido["factusol"]["codigo"] == "enlazado" and traido["diferencias"] is None
    assert fs.escrituras == []


def test_un_pedido_que_entra_solo_no_lleva_traido_a_mano(factory, client):
    with factory() as s:
        _store(s)
    _webhook(factory, _pagado(), delivery="d-1")
    with factory() as s:
        o = s.scalar(select(Order))
        assert not es_traido_a_mano(o) and "traido_a_mano" not in _packing(o)
    d = client.get(f"/api/erp/orders/{o.id}", headers=auth_headers(client, "pedidos"))
    assert d.json()["traido_a_mano"] is None


def _foto(s: Session, o: Order) -> dict[str, Any]:
    """Todo lo que importa de un pedido, sin ids ni sellos de tiempo de fila, y
    sin la marca de traído a mano (lo único que debe distinguirlos)."""
    fuera = {"id", "created_at", "updated_at", "contact_id", "company_id", "store_id",
             "packing_json", "manual_web_import_at", "manual_web_import_by_user_id"}
    columnas = {c.key: getattr(o, c.key) for c in Order.__table__.columns
                if c.key not in fuera}
    packing = _packing(o)
    packing.pop("traido_a_mano", None)
    contacto = s.get(Contact, o.contact_id)
    empresa = s.get(Company, o.company_id)
    lineas = [
        {c.key: getattr(ln, c.key) for c in OrderLine.__table__.columns
         if c.key not in {"id", "order_id", "created_at", "updated_at"}}
        for ln in s.scalars(select(OrderLine).where(OrderLine.order_id == o.id)
                            .order_by(OrderLine.position))
    ]
    historia = [
        (h.domain, h.from_status, h.to_status, h.reason, h.changed_by_user_id)
        for h in s.scalars(select(OrderStatusHistory)
                           .where(OrderStatusHistory.order_id == o.id))
    ]
    return {
        "columnas": columnas, "packing": packing, "lineas": lineas, "historia": historia,
        "tienda": s.get(IntegrationAccount, o.store_id).account_id,
        "contacto": (contacto.email, contacto.first_name, contacto.last_name,
                     contacto.origin, contacto.company_id is not None),
        "empresa": (empresa.name, empresa.tax_id, empresa.factusol_company_id,
                    empresa.country),
    }


def test_traido_y_pagado_es_identico_al_que_entra_solo_al_pagarse(factory, fs):
    # BD 1: se trae en on-hold y luego la tienda lo cobra (webhook de siempre).
    with factory() as s:
        store_id = _store(s).id
    _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    _webhook(factory, _pagado(), delivery="pago-1")

    # BD 2: nadie lo trae; entra solo cuando llega `processing`.
    engine2, factory2 = _engine_factory()
    try:
        with factory2() as s:
            _store(s)
        with patch.object(woo_jobs, "_session_factory", return_value=factory2):
            _webhook(factory2, _pagado(), delivery="pago-1")
        with factory() as s1, factory2() as s2:
            traido = s1.scalar(select(Order))
            solo = s2.scalar(select(Order))
            assert es_traido_a_mano(traido) and not es_traido_a_mano(solo)
            a, b = _foto(s1, traido), _foto(s2, solo)
            for clave in a:
                assert a[clave] == b[clave], clave
            # Y lo que se compara no es trivial.
            assert a["columnas"]["order_number"] == "FLUXLA-5873"
            assert a["columnas"]["payment_status"] == PaymentStatus.PAID
            assert a["columnas"]["preparation_status"] == PreparationStatus.IN_QUEUE
            assert len(a["lineas"]) == 4 and a["historia"]
    finally:
        Base.metadata.drop_all(engine2)
    assert fs.escrituras == []


def test_con_el_pedido_traido_el_webhook_de_pago_actualiza_y_no_duplica(factory):
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})

    out = _webhook(factory, _pagado(), delivery="pago-1")
    assert out["created"] is False and out["order_id"] == r["order_id"]
    # `completed` después: sigue siendo el mismo.
    _webhook(factory, _pagado(status="completed"), delivery="servido-1")

    with factory() as s:
        assert _count(s) == 1
        o = s.get(Order, r["order_id"])
        assert o.payment_status == PaymentStatus.PAID
        assert o.woo_status == "completed"
        # Encolado como el automático: a la Cola SAT, con su fila de historial.
        assert o.preparation_status == PreparationStatus.IN_QUEUE
        motivos = [h.reason for h in s.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == o.id))]
        assert motivos == [AUTO_ENQUEUE_REASON]
        assert es_traido_a_mano(o)
        assert _packing(o)["traido_a_mano"]["diferencias"] is None


def test_cancelado_en_la_web_despues_de_traerlo_se_anula_en_bohub(factory):
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    with patch("app.integrations.factusol.jobs.enqueue_autocancel_order_documents",
               return_value="job-x") as enq:
        _webhook(factory, _pedido_5873(status="cancelled"), delivery="cancelado-1")
    with factory() as s:
        assert _count(s) == 1
        o = s.get(Order, r["order_id"])
        assert o.cancelled_at is not None and o.cancelled_reason == AUTO_CANCEL_REASON
        assert o.woo_status == "cancelled"
    enq.assert_called_once_with(r["order_id"])


def test_cancelado_en_la_web_sin_traer_no_sale_ni_se_puede_traer(factory, client):
    with factory() as s:
        store_id = _store(s).id
    pedidos = {"fluxlasers": [_pedido_5873(status="cancelled"),
                              _pedido_5873(id=5874, number="5874")]}
    tienda = _tiendas(pedidos, ignora_filtro=True)   # aunque la tienda ignore el filtro
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        lista = client.get("/api/erp/pedidos-web/en-espera",
                           headers=auth_headers(client, "pedidos")).json()
        assert [it["numero"] for it in lista["items"]] == ["5874"]
        r = client.post("/api/erp/pedidos-web/traer",
                        json={"store_id": store_id, "woo_id": 5873},
                        headers=auth_headers(client, "pedidos"))
        # Pedido por número: se dice qué es.
        consulta = client.get("/api/erp/pedidos-web/en-espera", params={"q": "5873"},
                              headers=auth_headers(client, "pedidos")).json()
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "cancelado_en_la_web"
    assert consulta["items"] == []
    [c] = consulta["consulta"]
    assert (c["numero"], c["estado"], c["se_puede_traer"]) == ("5873", "cancelled", False)
    assert c["motivo"] == "cancelado en la web: no se puede traer"
    with factory() as s:
        assert _count(s) == 0


@pytest.mark.parametrize(("estado", "codigo"), [
    ("pending", "no_es_un_pedido"),
    ("failed", "no_es_un_pedido"),
    ("refunded", "estado_no_admitido"),
    ("trash", "estado_no_admitido"),
])
def test_pending_failed_y_otros_ni_salen_ni_se_traen(factory, estado, codigo):
    with factory() as s:
        store_id = _store(s).id
    pedidos = {"fluxlasers": [_pedido_5873(status=estado)]}
    with factory() as s:
        lista = pedidos_en_espera(s, client_factory=_tiendas(pedidos, ignora_filtro=True))
    assert lista["items"] == []
    with pytest.raises(PedidoWebError) as exc:
        _traer(factory, store_id, pedidos)
    assert (exc.value.status, exc.value.code) == (409, codigo)
    with factory() as s:
        assert _count(s) == 0


def test_un_processing_que_no_llego_se_trae_por_el_camino_normal(factory):
    """Pagado en la tienda pero sin webhook: se importa como siempre (pagado,
    a la Cola SAT) y SIN marca de traído a mano."""
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pagado()]})
    assert r["creado"] is True and r["factusol"]["codigo"] == "enlazado"
    with factory() as s:
        o = s.get(Order, r["order_id"])
        assert not es_traido_a_mano(o) and "traido_a_mano" not in _packing(o)
        assert o.payment_status == PaymentStatus.PAID
        assert o.preparation_status == PreparationStatus.IN_QUEUE


# --- sin duplicados --------------------------------------------------------------------


def test_traer_dos_veces_no_crea_dos(factory):
    with factory() as s:
        store_id = _store(s).id
    pedidos = {"fluxlasers": [_pedido_5873()]}
    primero = _traer(factory, store_id, pedidos)
    segundo = _traer(factory, store_id, pedidos)
    assert primero["creado"] is True and segundo["creado"] is False
    assert segundo["order_id"] == primero["order_id"]
    assert segundo["factusol"] == primero["factusol"]
    with factory() as s:
        assert _count(s) == 1


def test_dos_traer_a_la_vez_no_crean_dos(factory):
    """El segundo clic comprobó «¿ya está?» ANTES de que el primero confirmara:
    pasa la comprobación previa y llega al importador, que (bajo el cerrojo de
    la tienda) encuentra el que creó el primero y lo devuelve."""
    with factory() as s:
        store_id = _store(s).id
    pedidos = {"fluxlasers": [_pedido_5873()]}
    primero = _traer(factory, store_id, pedidos)
    bloqueos: list[str] = []
    real = woo_mapper._bloquear_tienda

    def espia(session, store):
        bloqueos.append(store.account_id)
        return real(session, store)

    with patch.object(pedidos_web, "_pedido_de_la_tienda", return_value=None), \
         patch.object(woo_mapper, "_bloquear_tienda", side_effect=espia):
        segundo = _traer(factory, store_id, pedidos)
    assert bloqueos == ["fluxlasers"]
    assert segundo["creado"] is False and segundo["order_id"] == primero["order_id"]
    with factory() as s:
        assert _count(s) == 1
        o = s.get(Order, primero["order_id"])
        assert _packing(o)["traido_a_mano"]["factusol"]["codigo"] == "enlazado"


def test_dos_traer_a_la_vez_en_mysql_devuelve_el_que_creo_el_otro(factory):
    """En MySQL el segundo, tras esperar el cerrojo, aún lee con su foto vieja:
    el importador lo detecta (`ImportacionSimultanea`) y «Traer» devuelve el
    que creó el primero, sin crear nada."""
    with factory() as s:
        store_id = _store(s).id
    pedidos = {"fluxlasers": [_pedido_5873()]}
    primero = _traer(factory, store_id, pedidos)
    with patch.object(pedidos_web, "_pedido_de_la_tienda", return_value=None), \
         patch.object(woo_mapper, "_pedido_existente",
                      side_effect=woo_mapper.ImportacionSimultanea(primero["order_id"])):
        segundo = _traer(factory, store_id, pedidos)
    assert segundo == {**primero, "creado": False}
    with factory() as s:
        assert _count(s) == 1


def test_en_mysql_el_pedido_creado_mientras_se_esperaba_no_se_duplica():
    """La comprobación tras el cerrojo mira con una conexión aparte (foto
    nueva): si el pedido ya está, no sigue con la foto vieja."""
    session = MagicMock()
    session.scalar.return_value = None                  # la foto vieja no lo ve
    bind = session.get_bind.return_value
    bind.dialect.name = "mysql"
    conn = bind.engine.connect.return_value.__enter__.return_value
    conn.scalar.return_value = "pedido-del-otro"        # la nueva, sí
    store = IntegrationAccount(id="t-1", account_id="fluxlasers")
    with pytest.raises(woo_mapper.ImportacionSimultanea) as exc:
        woo_mapper._pedido_existente(session, store, "5873")
    assert exc.value.order_id == "pedido-del-otro"
    conn.scalar.return_value = None                     # de verdad no está
    assert woo_mapper._pedido_existente(session, store, "5873") is None


def test_el_cerrojo_de_la_tienda_es_un_select_for_update():
    session = MagicMock()
    woo_mapper._bloquear_tienda(session, IntegrationAccount(id="t-1", account_id="x"))
    stmt = session.execute.call_args[0][0]
    sql = str(stmt.compile(dialect=mysql.dialect()))
    assert "FROM integration_accounts" in sql and sql.rstrip().endswith("FOR UPDATE")


def test_el_webhook_tambien_coge_el_cerrojo(factory):
    with factory() as s:
        _store(s)
    bloqueos: list[str] = []
    real = woo_mapper._bloquear_tienda

    def espia(session, store):
        bloqueos.append(store.account_id)
        return real(session, store)

    with patch.object(woo_mapper, "_bloquear_tienda", side_effect=espia):
        _webhook(factory, _pagado(), delivery="d-1")
    assert bloqueos == ["fluxlasers"]


@pytest.mark.parametrize("origen", ["mismo_numero", "desde_factusol"])
def test_si_ya_hay_otro_pedido_en_bohub_no_se_trae(factory, client, origen):
    with factory() as s:
        store_id = _store(s).id
        if origen == "mismo_numero":
            otro = Order(order_number="FLUXLA-5873", external_source=OrderSource.MANUAL,
                         total_amount=44.77)
        else:
            otro = Order(order_number="PCL-5-000431", external_source=OrderSource.FACTUSOL_PEDIDO,
                         total_amount=44.77, packing_json=json.dumps({
                             "factusol_source": {"doc_type": "pedidos", "serie": 5,
                                                 "codigo": 431, "referencia": REFPCL}}))
        s.add(otro)
        s.commit()
        otro_id = otro.id
    tienda = _tiendas({"fluxlasers": [_pedido_5873()]})
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        r = client.post("/api/erp/pedidos-web/traer",
                        json={"store_id": store_id, "woo_id": 5873},
                        headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert (detail["code"], detail["order_id"]) == ("ya_hay_pedido_en_bohub", otro_id)
    assert detail["detail"]
    with factory() as s:
        assert _count(s) == 1


# --- lo que la web cambió al pagarlo -----------------------------------------------------


def test_traido_con_el_pedido_cambiado_al_pagarse_deja_constancia(factory, client):
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    # Entre on-hold y processing la tienda le suma una unidad de PMP-12002.
    cambiado = _pagado(total="50.82", total_tax="8.82")
    cambiado["line_items"][1].update(quantity=2, subtotal="10.00", total="10.00",
                                     total_tax="2.10")
    _webhook(factory, cambiado, delivery="pago-1")

    with factory() as s:
        o = s.get(Order, r["order_id"])
        diferencias = _packing(o)["traido_a_mano"]["diferencias"]
        assert diferencias == [
            "Total: 44,77 € en BoHub; la web dice 50,82 €.",
            "Ador & BB2 AF Plastic Probe (PMP-12002): cantidad 1 en BoHub; la web dice 2.",
            "Ador & BB2 AF Plastic Probe (PMP-12002): importe 5,00 € en BoHub; "
            "la web dice 10,00 €.",
        ]
        # Las líneas NO se reescriben en silencio y nada se bloquea.
        linea = s.scalar(select(OrderLine).where(OrderLine.order_id == o.id,
                                                 OrderLine.product_sku == "PMP-12002"))
        assert float(linea.quantity) == 1.0
        assert o.payment_status == PaymentStatus.PAID
        assert o.preparation_status == PreparationStatus.IN_QUEUE
        # El veredicto de FACTUSOL sigue ahí.
        assert _packing(o)["traido_a_mano"]["factusol"]["codigo"] == "enlazado"

    # Otro webhook igual no repite los avisos.
    _webhook(factory, cambiado, delivery="servido-1")
    d = client.get(f"/api/erp/orders/{r['order_id']}", headers=auth_headers(client, "pedidos"))
    assert d.json()["traido_a_mano"]["diferencias"] == diferencias


def test_sin_cambios_en_la_web_no_hay_diferencias(factory):
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    _webhook(factory, _pedido_5873(), delivery="nota-1")    # sigue on-hold, sin cambios
    with factory() as s:
        o = s.get(Order, r["order_id"])
        assert _packing(o)["traido_a_mano"]["diferencias"] is None
        assert o.payment_status == PaymentStatus.PENDING


# --- FACTUSOL: veredicto, solo lectura ----------------------------------------------------


@pytest.mark.parametrize(("tablas", "codigo", "en_el_mensaje"), [
    # Su F_PCL por la referencia, del cliente de ese CIF y con ese total.
    ({}, "enlazado", "5-000431"),
    # La web aún no lo ha pasado a FACTUSOL.
    ({"pcl": []}, "no_encontrado", REFPCL),
    # Con el prefijo derivado (FLU) en vez del de la app (FLE): mismo CIF e importe.
    ({"pcl": [_pcl(REFPCL="FLU-005873")]}, "posible", "FLU-005873"),
    # La referencia casa pero el importe no.
    ({"pcl": [_pcl(TOTPCL=40.0)]}, "dudoso", "importe"),
    # La referencia casa pero el cliente es otro.
    ({"cli": [_cli(NIFCLI="B99999999")]}, "dudoso", "cliente"),
    # Dos pedidos de cliente con la misma referencia.
    ({"pcl": [_pcl(), _pcl(CODPCL=432)]}, "dudoso", "Hay 2"),
    # Dos candidatos por CIF + importe y ninguno por referencia.
    ({"pcl": [_pcl(REFPCL="FLU-005873"), _pcl(CODPCL=500, REFPCL="")]}, "dudoso", "hay 2"),
])
def test_veredicto_factusol(factory, monkeypatch, tablas, codigo, en_el_mensaje):
    fake = _factusol(tablas.get("pcl"), tablas.get("cli"))
    monkeypatch.setattr("app.erp.web_order_company.factusol_client_and_ejercicio",
                        lambda _s: (fake, "2026"))
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    assert r["creado"] is True
    assert r["factusol"]["codigo"] == codigo, r["factusol"]
    assert en_el_mensaje in r["factusol"]["mensaje"]
    assert "No se crea nada en FACTUSOL" in r["factusol"]["mensaje"]
    assert r["factusol"]["referencia"] == REFPCL
    with factory() as s:
        o = s.get(Order, r["order_id"])
        assert _packing(o)["traido_a_mano"]["factusol"] == r["factusol"]
    # Ninguna escritura, solo lecturas de F_PCL / F_CLI.
    assert fake.escrituras == []
    assert {t for t, _ in fake.calls} <= {"F_PCL", "F_CLI"}


@pytest.mark.parametrize("como", ["sin_configurar", "caido"])
def test_veredicto_sin_factusol(factory, monkeypatch, como):
    fake = _factusol(caido=True)
    monkeypatch.setattr(
        "app.erp.web_order_company.factusol_client_and_ejercicio",
        (lambda _s: None) if como == "sin_configurar" else (lambda _s: (fake, "2026")),
    )
    with factory() as s:
        store_id = _store(s).id
    r = _traer(factory, store_id, {"fluxlasers": [_pedido_5873()]})
    # Se trae igual; solo se dice que no se pudo comprobar.
    assert r["creado"] is True and r["factusol"]["codigo"] == "sin_factusol"
    assert r["factusol"]["referencia"] == REFPCL
    assert fake.escrituras == []


def test_veredicto_busca_en_el_ejercicio_del_pedido(factory, monkeypatch):
    """Un pedido de diciembre traído en enero: su pedido de cliente está en el
    ejercicio viejo."""
    fake = _factusol()
    ejercicios: list[str | None] = []
    original = fake.load_table

    def load_table(tabla, *, filtro="1=1", ejercicio=None):
        ejercicios.append(ejercicio)
        return original(tabla, filtro=filtro, ejercicio=ejercicio)

    fake.load_table = load_table
    with factory() as s:
        store = _store(s)
        v = pedidos_web.veredicto_factusol(s, store, order_number="FLUXLA-5873", cif=CIF,
                                           total=44.77, fecha="2026-12-30T10:00:00+00:00")
        assert v["codigo"] == "enlazado"
    monkeypatch.setattr("app.erp.web_order_company.factusol_client_and_ejercicio",
                        lambda _s: (fake, "2027"))
    ejercicios.clear()
    with factory() as s:
        store = s.scalar(select(IntegrationAccount))
        pedidos_web.veredicto_factusol(s, store, order_number="FLUXLA-5873", cif=CIF,
                                       total=44.77, fecha="2026-12-30T10:00:00+00:00")
    assert ejercicios and set(ejercicios) == {"2026"}


# --- la lista ---------------------------------------------------------------------------


def test_lista_en_espera_forma_del_contrato_y_filtro(factory, client):
    with factory() as s:
        flux = _store(s).id
        _store(s, "boprint", "boprint.es", "https://boprint.es")
    otro = _pedido_5873(id=5880, number="5880", date_created="2026-10-25T12:00:00",
                        total="129.00", payment_method_title="Transferencia",
                        billing={**_pedido_5873()["billing"], "company": "",
                                 "first_name": "José", "last_name": "López",
                                 "email": "jose@lopez.es"})
    pedidos = {"fluxlasers": [_pedido_5873(), otro]}
    tienda = _tiendas(pedidos, caidas=("boprint",))
    h = auth_headers(client, "pedidos")
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        r = client.get("/api/erp/pedidos-web/en-espera", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"items", "tiendas", "consulta"}
        # La más reciente primero; boprint caída no tumba a fluxlasers.
        assert [it["numero"] for it in body["items"]] == ["5880", "5873"]
        item = body["items"][1]
        assert item == {
            "store_id": flux, "tienda": "fluxlasers", "tienda_nombre": "fluxlasers.es",
            "woo_id": 5873, "numero": "5873", "fecha": "2026-10-23T09:18:59+00:00",
            "cliente": "Montse Ferrer Vidal", "empresa": "Escola de Ponent",
            "email": "secretaria@escoladeponent.cat", "total": 44.77, "moneda": "EUR",
            "forma_pago": "Transferencia bancaria directa",
            "enlace_tienda": "https://fluxlasers.es/wp-admin/post.php?post=5873&action=edit",
            "en_bohub": None,
        }
        tiendas = {t["tienda"]: t for t in body["tiendas"]}
        assert tiendas["fluxlasers"] == {"store_id": flux, "tienda": "fluxlasers",
                                         "tienda_nombre": "fluxlasers.es", "ok": True,
                                         "error": None, "en_espera": 2}
        assert tiendas["boprint"]["ok"] is False and tiendas["boprint"]["error"]
        assert body["consulta"] == []

        # Filtro sin tildes ni mayúsculas: cliente, empresa, email, número.
        for q, esperado in (("lopez", ["5880"]), ("JOSÉ", ["5880"]), ("ponent", ["5873"]),
                            ("escoladeponent.cat", ["5873"]), ("587", ["5873"]),
                            ("#5880", ["5880"])):
            got = client.get("/api/erp/pedidos-web/en-espera", params={"q": q}, headers=h)
            assert [it["numero"] for it in got.json()["items"]] == esperado, q

        # Traído: sigue en la lista, marcado.
        t = client.post("/api/erp/pedidos-web/traer",
                        json={"store_id": flux, "woo_id": 5873}, headers=h).json()
        marcada = client.get("/api/erp/pedidos-web/en-espera", headers=h).json()
    en_bohub = {it["numero"]: it["en_bohub"] for it in marcada["items"]}
    assert en_bohub == {"5880": None,
                        "5873": {"order_id": t["order_id"], "order_number": "FLUXLA-5873"}}


def test_consulta_de_un_numero_que_no_esta_en_espera(factory):
    with factory() as s:
        _store(s)
        _store(s, "boprint", "boprint.es", "https://boprint.es")
    pedidos = {"fluxlasers": [_pagado(), _pedido_5873(id=5874, number="5874")],
               "boprint": [_pedido_5873(status="pending")]}
    with factory() as s:
        r = pedidos_en_espera(s, q="5873", client_factory=_tiendas(pedidos))
    assert r["items"] == []
    por_tienda = {c["tienda"]: c for c in r["consulta"]}
    assert set(por_tienda) == {"fluxlasers", "boprint"}
    assert (por_tienda["fluxlasers"]["estado"], por_tienda["fluxlasers"]["se_puede_traer"]) == (
        "processing", True)
    assert por_tienda["fluxlasers"]["motivo"].startswith("procesando: entra solo")
    assert por_tienda["boprint"]["motivo"] == "pendiente de pago (carrito sin pagar): no se trae"
    assert por_tienda["boprint"]["se_puede_traer"] is False

    # Ya en BoHub: se dice.
    _webhook(factory, _pagado(), delivery="d-1")
    with factory() as s:
        r = pedidos_en_espera(s, q="5873", client_factory=_tiendas(pedidos))
    flux = next(c for c in r["consulta"] if c["tienda"] == "fluxlasers")
    assert flux["motivo"] == "ya está en BoHub" and flux["en_bohub"]["order_number"] == \
        "FLUXLA-5873"
    # Un número que está en espera no lanza la consulta.
    with factory() as s:
        r = pedidos_en_espera(s, q="5874", client_factory=_tiendas(pedidos))
    assert [it["numero"] for it in r["items"]] == ["5874"] and r["consulta"] == []


def test_la_lista_no_espera_a_una_tienda_que_no_responde(factory, monkeypatch):
    import threading

    soltar = threading.Event()

    class Lenta(TiendaWoo):
        def list_orders(self, **kw):
            soltar.wait(5)
            return []

    def fabrica(store, **_kw):
        if store.account_id == "boprint":
            return Lenta(store, [], [])
        return TiendaWoo(store, [_pedido_5873()], [])

    with factory() as s:
        _store(s)
        _store(s, "boprint", "boprint.es", "https://boprint.es")
    monkeypatch.setattr(pedidos_web, "TIEMPO_LISTADO", 0.3)
    try:
        with factory() as s:
            r = pedidos_en_espera(s, client_factory=fabrica)
    finally:
        soltar.set()
    tiendas = {t["tienda"]: t for t in r["tiendas"]}
    assert tiendas["fluxlasers"]["ok"] is True and [it["numero"] for it in r["items"]] == ["5873"]
    assert tiendas["boprint"]["ok"] is False
    assert "no ha respondido a tiempo" in tiendas["boprint"]["error"]


def test_la_lista_no_lleva_reintentos_y_tiene_tiempo_acotado(factory):
    with factory() as s:
        store = _store(s)
        c = pedidos_web._cliente_listado(store)
    assert c._max_retries == 0 and c._timeout == pedidos_web.TIEMPO_POR_PETICION


# --- errores de la API ----------------------------------------------------------------------


def test_errores_de_traer(factory, client):
    with factory() as s:
        store_id = _store(s).id
        _store(s, "boprint", "boprint.es", "https://boprint.es")
        boprint = s.scalar(select(IntegrationAccount).where(
            IntegrationAccount.account_id == "boprint")).id
    h = auth_headers(client, "pedidos")
    tienda = _tiendas({"fluxlasers": [_pedido_5873()]}, caidas=("boprint",))
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        desconocida = client.post("/api/erp/pedidos-web/traer",
                                  json={"store_id": "no-existe", "woo_id": 5873}, headers=h)
        no_esta = client.post("/api/erp/pedidos-web/traer",
                              json={"store_id": store_id, "woo_id": 9999}, headers=h)
        caida = client.post("/api/erp/pedidos-web/traer",
                            json={"store_id": boprint, "woo_id": 5873}, headers=h)
    assert desconocida.status_code == 404
    assert desconocida.json()["detail"]["code"] == "tienda_desconocida"
    assert no_esta.status_code == 404
    assert no_esta.json()["detail"]["code"] == "pedido_no_encontrado"
    assert caida.status_code == 502
    assert caida.json()["detail"]["code"] == "tienda_no_responde"
    for r in (desconocida, no_esta, caida):
        assert set(r.json()["detail"]) == {"code", "detail"}
    with factory() as s:
        assert _count(s) == 0


# --- permisos -------------------------------------------------------------------------------


@pytest.mark.parametrize(("role", "esperado"), [
    ("pedidos", 200), ("admin", 200),
    # comercial crea pedidos pero no ve los web; sat ve los web pero no crea.
    ("comercial", 403), ("sat", 403), ("user", 403),
])
def test_permisos(factory, client, role, esperado):
    with factory() as s:
        store_id = _store(s).id
    h = auth_headers(client, role)
    tienda = _tiendas({"fluxlasers": [_pedido_5873()]})
    with patch.object(pedidos_web, "WooHTTPClient", side_effect=tienda):
        lista = client.get("/api/erp/pedidos-web/en-espera", headers=h)
        traer = client.post("/api/erp/pedidos-web/traer",
                            json={"store_id": store_id, "woo_id": 5873}, headers=h)
    assert lista.status_code == esperado
    assert traer.status_code == esperado
    with factory() as s:
        assert _count(s) == (1 if esperado == 200 else 0)


def test_sin_sesion_no_hay_nada(client):
    assert client.get("/api/erp/pedidos-web/en-espera").status_code == 401
    assert client.post("/api/erp/pedidos-web/traer",
                       json={"store_id": "x", "woo_id": 1}).status_code == 401
