"""Un pedido web que llega sin cliente no puede pasar desapercibido (28/10/2026).

BOPRIN-100015 (2.129,60 €) entró de la web sin empresa, sin NIF y sin
dirección: lo creó una persona desde una intervención de SAT
(`_tsm_manual=yes`), el billing solo traía «Nadine» y el email. BoHub no pudo
emparejar la empresa, el plugin de la web no creó el pedido de cliente en
FACTUSOL, y el pedido se quedó en la bandeja con pinta de normal hasta que
Bart fue a facturarlo. Hubo que vincularlo a mano por SQL a COMPONENTS FRANCE
SPORT, S.L. (CIF B17643313, FACTUSOL 1611).

Ahora el pedido canta en la lista (dice qué le falta), la ficha propone la
empresa si hay con qué (el NIF de la web, el pedido de cliente de FACTUSOL) y
se puede vincular desde ahí. FACTUSOL está simulado y solo se lee.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.erp.workflow import datos_cliente_que_faltan, order_workflow
from app.integrations.woocommerce.mapper import import_woo_order
from app.main import app
from app.models.crm import AuditLog, Base, Company, Contact, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

NIF = "B17643313"
CODCLI = "1611"
EMPRESA = "COMPONENTS FRANCE SPORT, S.L."


class FactusolFalso:
    """F_PCL y F_CLI simulados: solo lectura."""

    def __init__(self, *, pcl: dict[str, Any] | None = None, cli: dict[str, Any] | None = None):
        self.pcl, self.cli = pcl, cli
        self.lecturas: list[tuple[str, str]] = []

    def load_table(self, tabla: str, *, filtro: str = "1=1", ejercicio: str | None = None):
        self.lecturas.append((tabla, filtro))
        if tabla == "F_PCL":
            if self.pcl and f"REFPCL='{self.pcl['REFPCL']}'" == filtro:
                return [self.pcl]
            return []
        if tabla == "F_CLI" and self.cli:
            if filtro == f"CODCLI={int(self.cli['CODCLI'])}":
                return [self.cli]
            if "NIFCLI" in filtro and self.cli["NIFCLI"] in filtro:
                return [self.cli]
        return []


def _cli(nif: str = NIF) -> dict[str, Any]:
    return {"CODCLI": int(CODCLI), "NIFCLI": nif, "NOFCLI": EMPRESA, "NOCCLI": EMPRESA,
            "DOMCLI": "C/ MAJOR 1", "POBCLI": "GIRONA", "CPOCLI": "17001", "PROCLI": "",
            "PAICLI": "", "EMACLI": "", "TELCLI": ""}


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
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


@pytest.fixture()
def factusol(monkeypatch):
    """Sin FACTUSOL por defecto; `factusol.usar(fake)` lo enchufa."""
    estado: dict[str, Any] = {"cliente": None}

    def _par(_session):
        return (estado["cliente"], "2026") if estado["cliente"] is not None else None

    for ruta in ("app.erp.cliente_pedido_web.factusol_client_and_ejercicio",
                 "app.erp.web_order_company.factusol_client_and_ejercicio"):
        monkeypatch.setattr(ruta, _par)

    class _Ctl:
        def usar(self, fake: FactusolFalso | None) -> None:
            estado["cliente"] = fake

    return _Ctl()


def _nadine(s: Session, *, nif: str = "", direccion: str = "", **campos: Any) -> str:
    """BOPRIN-100015 como entró: sin empresa, con «Nadine» y su email."""
    numero = campos.pop("order_number", "BOPRIN-100015")
    contacto = Contact(first_name="Nadine", email=f"nadine.{numero.lower()}@example.com")
    s.add(contacto)
    s.flush()
    o = Order(
        order_number=numero,
        external_source=OrderSource.WOOCOMMERCE, external_id="100015",
        total_amount=2129.60, currency="EUR", payment_status="paid",
        preparation_status="in_queue", approved_at=datetime.now(UTC),
        contact_id=contacto.id,
        packing_json=json.dumps({"shipping_address": {
            "name": "Nadine", "email": "nadine@example.com", "address_line": direccion,
            "city": "", "postal_code": "", "state": "", "country": "", "nif": nif}}),
        **campos,
    )
    s.add(o)
    s.commit()
    return o.id


def _empresa(s: Session, **campos: Any) -> str:
    c = Company(name=campos.pop("name", EMPRESA), tax_id=campos.pop("tax_id", NIF), **campos)
    s.add(c)
    s.commit()
    return c.id


# --- se ve en la lista y dice qué falta --------------------------------------------


def test_un_pedido_web_sin_empresa_ni_nif_se_ve_marcado_en_la_lista(http, session_factory) -> None:
    with session_factory() as s:
        oid = _nadine(s)
        _nadine(s, order_number="BOPRIN-100016", nif=NIF)
        # Un particular: sin empresa, pero con su dirección. Se le factura con ella.
        _nadine(s, order_number="BOPRIN-100017", direccion="C/ Major 1")
    r = http.get("/api/erp/orders", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    filas = {i["order_number"]: i for i in r.json()["items"]}
    wf = filas["BOPRIN-100015"]["workflow"]
    [alerta] = [a for a in wf["alerts"] if a["code"] == "datos_cliente_incompletos"]
    assert alerta["faltan"] == ["empresa", "nif", "direccion"]
    assert alerta["text"] == ("Pedido web sin empresa, sin NIF y sin dirección de facturación: "
                              "vincula la empresa antes de facturar.")
    assert (wf["queue"], wf["next_action"], wf["next_action_label"]) == (
        "por_revisar", "vincular_cliente", "Vincular empresa al pedido")
    # Con NIF pero sin dirección también se ve, y dice lo que falta.
    [otro] = [a for a in filas["BOPRIN-100016"]["workflow"]["alerts"]
              if a["code"] == "datos_cliente_incompletos"]
    assert otro["faltan"] == ["empresa", "direccion"]
    assert not [a for a in filas["BOPRIN-100017"]["workflow"]["alerts"]
                if a["code"] == "datos_cliente_incompletos"]
    # La cola «Por revisar» lo cuenta.
    assert r.json()["queue_counts"]["por_revisar"] >= 2
    with session_factory() as s:
        assert order_workflow(s, s.get(Order, oid))["queue"] == "por_revisar"


def test_lo_que_no_cuenta_como_pedido_web_sin_cliente(session_factory) -> None:
    with session_factory() as s:
        cid = _empresa(s)
        casos = {
            "con empresa": _nadine(s, order_number="W-1", company_id=cid),
            "facturado": _nadine(s, order_number="W-2", factusol_invoice_number="260090"),
            "anulado": _nadine(s, order_number="W-3", cancelled_at=datetime.now(UTC)),
            "completado": _nadine(s, order_number="W-4", completed_at=datetime.now(UTC)),
        }
        s.add(Order(order_number="M-1", external_source=OrderSource.MANUAL, total_amount=1))
        s.commit()
        for nombre, oid in casos.items():
            o = s.get(Order, oid)
            company = s.get(Company, o.company_id) if o.company_id else None
            assert datos_cliente_que_faltan(o, company) == [], nombre
        manual = s.scalar(select(Order).where(Order.order_number == "M-1"))
        assert datos_cliente_que_faltan(manual, None) == []


# --- la ficha propone (o dice que no hay con qué) -----------------------------------


def test_un_pedido_web_con_nif_en_meta_data_propone_su_empresa(
    http, session_factory, factusol,
) -> None:
    """El NIF que el plugin «WC - APG Campo NIF/CIF/NIE» guarda en
    `_billing_nif` llega al pedido aunque no haya razón social, y la ficha
    propone la empresa que lo tiene."""
    with session_factory() as s:
        cid = _empresa(s)
        store = IntegrationAccount(
            system=ExternalSystem.WOOCOMMERCE, account_id="boprint", display_name="Boprint",
            enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
            base_url="https://boprint.example", consumer_key_encrypted=encrypt("ck"),
            consumer_secret_encrypted=encrypt("cs"), credential_status="configured",
        )
        s.add(store)
        s.commit()
        woo = {
            "id": 100015, "number": "100015", "status": "processing", "total": "2129.60",
            "currency": "EUR", "date_created": "2026-10-28T08:00:00Z",
            "date_paid": "2026-10-28T08:01:00Z",
            "billing": {"first_name": "Nadine", "last_name": "", "email": "nadine@example.com",
                        "company": "", "vat": "", "address_1": "", "city": "",
                        "postcode": "", "country": ""},
            "line_items": [{"id": 1, "product_id": 1, "sku": "PACK", "quantity": 1,
                            "total": "2129.60", "name": "Pack servicio"}],
            "meta_data": [{"key": "_tsm_manual", "value": "yes"},
                          {"key": "_billing_nif", "value": "B-17643313"}],
            "_store_slug": "boprint",
        }
        out = import_woo_order(s, store=store, woo_order=woo)
        s.commit()
        oid = out.order_id
        assert s.get(Order, oid).company_id is None          # no se vincula sola
    r = http.get(f"/api/erp/orders/{oid}/cliente-web", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["faltan"] == ["empresa", "direccion"]
    assert body["nif"] == "B-17643313"
    [prop] = body["propuestas"]
    assert (prop["company_id"], prop["nombre"], prop["origen"]) == (cid, EMPRESA, "nif")
    assert prop["motivo"] == "El NIF del pedido (B-17643313) es el de esta empresa."
    assert body["mensaje"] is None


def test_si_el_pedido_llego_a_factusol_como_pedido_de_cliente_se_propone_su_cliente(
    http, session_factory, factusol,
) -> None:
    with session_factory() as s:
        oid = _nadine(s)
    falso = FactusolFalso(
        pcl={"REFPCL": "BOP-100015", "CLIPCL": int(CODCLI), "TIPPCL": "5", "CODPCL": 120},
        cli=_cli(),
    )
    factusol.usar(falso)
    body = http.get(f"/api/erp/orders/{oid}/cliente-web",
                    headers=auth_headers(http, "pedidos")).json()
    assert body["factusol"] == {"disponible": True, "referencia": "BOP-100015",
                                "pedido_encontrado": True, "pedido": "5-120"}
    [prop] = body["propuestas"]
    assert (prop["company_id"], prop["codcli"], prop["nombre"], prop["origen"]) == (
        None, CODCLI, EMPRESA, "pedido_factusol")
    assert "se creará con sus datos al vincular" in prop["motivo"]

    # Vincularla crea su ficha desde el cliente de FACTUSOL y la deja vinculada.
    r = http.post(f"/api/erp/orders/{oid}/company", json={"codcli": CODCLI,
                                                          "origen": "pedido_factusol"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["creada"] is True
    with session_factory() as s:
        o = s.get(Order, oid)
        empresa = s.get(Company, o.company_id)
        assert (empresa.name, empresa.tax_id, empresa.factusol_company_id) == (EMPRESA, NIF, CODCLI)
        assert s.get(Contact, o.contact_id).company_id == empresa.id
        assert order_workflow(s, o)["queue"] != "por_revisar"
    # A FACTUSOL solo se le ha leído (el falso no sabe escribir: fallaría).
    assert falso.lecturas and {t for t, _ in falso.lecturas} <= {"F_PCL", "F_CLI"}


def test_sin_con_que_emparejar_lo_dice_y_no_inventa_nada(http, session_factory, factusol) -> None:
    with session_factory() as s:
        oid = _nadine(s)
        _empresa(s, name="Otra SL", tax_id="B99999999")
    factusol.usar(FactusolFalso())
    body = http.get(f"/api/erp/orders/{oid}/cliente-web",
                    headers=auth_headers(http, "pedidos")).json()
    assert body["propuestas"] == []
    assert body["mensaje"] == (
        "La web no trae NIF y el pedido no está en FACTUSOL como pedido de cliente "
        "(BOP-100015): no hay con qué emparejarlo. Búscala a mano.")
    factusol.usar(None)
    body = http.get(f"/api/erp/orders/{oid}/cliente-web",
                    headers=auth_headers(http, "pedidos")).json()
    assert body["factusol"]["disponible"] is False
    assert "no se ha podido consultar FACTUSOL" in body["mensaje"]


# --- vincular desde la ficha ----------------------------------------------------------


def test_se_vincula_la_empresa_desde_la_ficha_y_queda_auditado(http, session_factory) -> None:
    with session_factory() as s:
        oid = _nadine(s)
        cid = _empresa(s, factusol_company_id=CODCLI)
    r = http.post(f"/api/erp/orders/{oid}/company", json={"company_id": cid, "origen": "busqueda"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == EMPRESA
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.company_id == cid
        evento = s.scalar(select(AuditLog).where(AuditLog.action == "erp.order_company_linked"))
        assert (evento.target_type, evento.target_id, evento.actor_email) == (
            "order", oid, "pedidos@example.com")
        meta = json.loads(evento.metadata_json)
        assert (meta["from"], meta["to"], meta["origen"]) == (None, cid, "busqueda")
        assert datos_cliente_que_faltan(o, s.get(Company, cid)) == []
    # Sale en el historial del pedido.
    r = http.get(f"/api/erp/orders/{oid}/timeline", headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200
    assert EMPRESA in json.dumps(r.json(), ensure_ascii=False)


def test_no_se_vincula_lo_que_no_se_debe(http, session_factory) -> None:
    with session_factory() as s:
        facturado = _nadine(s, order_number="W-9", factusol_invoice_number="260090")
        oid = _nadine(s)
        cid = _empresa(s)
        archivada = _empresa(s, name="Vieja SL", tax_id="B11111111", is_archived=True)
    admin = auth_headers(http, "pedidos")
    r = http.post(f"/api/erp/orders/{facturado}/company", json={"company_id": cid}, headers=admin)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "ya_facturado")
    r = http.post(f"/api/erp/orders/{oid}/company", json={"company_id": archivada}, headers=admin)
    assert r.json()["detail"]["code"] == "empresa_archivada"
    r = http.post(f"/api/erp/orders/{oid}/company", json={}, headers=admin)
    assert r.status_code == 422
    # Quien no edita pedidos (o no ve los web) no vincula.
    for rol in ("user", "viewer", "sat"):
        r = http.post(f"/api/erp/orders/{oid}/company", json={"company_id": cid},
                      headers=auth_headers(http, rol))
        assert r.status_code == 403, rol
    with session_factory() as s:
        assert s.get(Order, oid).company_id is None


def test_vincular_es_solo_para_un_pedido_web_sin_empresa(
    http, session_factory, factusol,
) -> None:
    with session_factory() as s:
        cid = _empresa(s)
        con_empresa = _nadine(s, order_number="W-5", company_id=cid)
        manual = Order(order_number="M-2", external_source=OrderSource.MANUAL, total_amount=1)
        s.add(manual)
        s.commit()
        manual_id = manual.id
        oid = _nadine(s)
        _empresa(s, name="Vieja SL", tax_id="B11111111", factusol_company_id=CODCLI,
                 is_archived=True)
    pedidos = auth_headers(http, "pedidos")
    r = http.post(f"/api/erp/orders/{con_empresa}/company", json={"company_id": cid},
                  headers=pedidos)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "ya_tiene_empresa")
    r = http.post(f"/api/erp/orders/{manual_id}/company", json={"company_id": cid},
                  headers=pedidos)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "no_es_web")
    # El cliente de FACTUSOL cuya ficha en BoHub está archivada: ni se propone
    # ni se vincula.
    factusol.usar(FactusolFalso(
        pcl={"REFPCL": "BOP-100015", "CLIPCL": int(CODCLI), "TIPPCL": "5", "CODPCL": 120},
        cli=_cli(),
    ))
    body = http.get(f"/api/erp/orders/{oid}/cliente-web", headers=pedidos).json()
    assert body["propuestas"] == []
    assert body["mensaje"] == (
        "La web no trae NIF y el pedido está en FACTUSOL como pedido de cliente (BOP-100015), "
        f"pero la ficha de su cliente {CODCLI} en BoHub está archivada: no hay con qué "
        "emparejarlo. Búscala a mano.")
    r = http.post(f"/api/erp/orders/{oid}/company", json={"codcli": CODCLI}, headers=pedidos)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "empresa_archivada")

    # FACTUSOL que se cae al leer el cliente: 503 y se dice, no un 500.
    class FactusolCaido(FactusolFalso):
        def load_table(self, tabla: str, **kw: Any):
            if tabla == "F_CLI":
                raise RuntimeError("DELSOL no responde")
            return super().load_table(tabla, **kw)

    factusol.usar(FactusolCaido())
    r = http.post(f"/api/erp/orders/{oid}/company", json={"codcli": CODCLI}, headers=pedidos)
    assert (r.status_code, r.json()["detail"]["code"]) == (503, "sin_factusol")
    with session_factory() as s:
        assert s.get(Order, oid).company_id is None


def test_con_nif_y_sin_factusol_no_dice_que_nadie_lo_tenga_en_factusol(
    http, session_factory, factusol,
) -> None:
    with session_factory() as s:
        oid = _nadine(s, nif="B22222222")
    body = http.get(f"/api/erp/orders/{oid}/cliente-web",
                    headers=auth_headers(http, "pedidos")).json()
    assert body["mensaje"] == (
        "Nadie en BoHub tiene el NIF B22222222 y no se ha podido consultar FACTUSOL: no hay "
        "con qué emparejarlo. Búscala a mano.")


def test_el_refresco_no_mezcla_dos_direcciones(session_factory) -> None:
    """La dirección va en bloque: si ya había una (aunque a medias), la de la
    web no se le mezcla; el NIF sí se rellena."""
    from app.integrations.woocommerce.mapper import _apply_shipping_address  # noqa: PLC0415

    with session_factory() as s:
        o = s.get(Order, _nadine(s, direccion="C/ Vieja 2"))
        woo = {"billing": {"first_name": "Nadine", "email": "nadine@example.com",
                           "address_1": "C/ Major 1", "city": "Girona", "postcode": "17001"},
               "meta_data": [{"key": "billing_nif", "value": NIF}]}
        _apply_shipping_address(o, woo, overwrite=False)
        d = json.loads(o.packing_json)["shipping_address"]
        assert (d["address_line"], d["city"], d["postal_code"], d["nif"]) == (
            "C/ Vieja 2", "", "", NIF)


def test_el_refresco_de_la_web_rellena_el_nif_que_faltaba(session_factory) -> None:
    """Si la web añade el NIF después, el siguiente webhook lo recoge (antes el
    bloque guardado, aunque estuviera vacío, no se tocaba)."""
    from app.integrations.woocommerce.mapper import _apply_shipping_address  # noqa: PLC0415

    with session_factory() as s:
        o = s.get(Order, _nadine(s))
        woo = {"billing": {"first_name": "Nadine", "email": "nadine@example.com",
                           "address_1": "C/ Major 1", "city": "Girona", "postcode": "17001"},
               "meta_data": [{"key": "billing_nif", "value": NIF}]}
        _apply_shipping_address(o, woo, overwrite=False)
        d = json.loads(o.packing_json)["shipping_address"]
        assert (d["nif"], d["address_line"], d["city"], d["name"]) == (
            NIF, "C/ Major 1", "Girona", "Nadine")
