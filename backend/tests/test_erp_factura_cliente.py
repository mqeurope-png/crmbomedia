"""Corregir lo emitido desde la ficha del pedido (30/10/2026): la mención legal
de una factura emitida y el cliente del pedido (y de su factura).

Son escrituras NUEVAS en FACTUSOL, autorizadas por Bart: siempre con vista
previa, confirmación explícita, relectura en vivo antes de escribir y
Auditoría. FACTUSOL no se toca (cliente en memoria que guarda lo que se
actualiza) y los clientes son FIGURANTES.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp import factura_cliente as fc
from app.erp.models import Order, OrderSource
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.mapper import FacturaOptions, pcl_row_to_fac_payload
from app.main import app
from app.models.crm import AuditLog, Company
from tests._test_helpers import auth_headers, seed_test_users

TEXTO_UE = "Operación exenta de IVA por exportación, artículo 21.1º de la Ley 37/1992."

# --- FACTUSOL simulado ---------------------------------------------------------------


class FakeFactusol:
    """F_FAC / F_ALB / F_PCL en memoria. `update_record` cambia la fila (como
    `ActualizarRegistro`: solo las columnas que llegan) para que la relectura
    vea lo escrito."""

    def __init__(self, f_fac: list[dict] | None = None, f_alb: list[dict] | None = None):
        self.default_ejercicio = "2026"
        self.f_fac = f_fac or []
        self.f_alb = f_alb or []
        self.updates: list[tuple[str, dict]] = []
        self.writes: list[tuple[str, dict]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        filas = {"F_FAC": self.f_fac, "F_ALB": self.f_alb}.get(tabla, [])
        if "=" in filtro and filtro != "1=1":
            col, val = filtro.split("=", 1)
            return [r for r in filas if str(r.get(col.strip())) == val.strip()]
        return list(filas)

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, dict(data)))
        for r in self.f_fac:
            if r["TIPFAC"] == data["TIPFAC"] and r["CODFAC"] == data["CODFAC"]:
                r.update(data)

    def write_record(self, tabla, data, *, ejercicio=None):
        self.writes.append((tabla, dict(data)))


def _fac(**over) -> dict:
    row = {
        "TIPFAC": "2", "CODFAC": 526090, "TOTFAC": 2129.6, "ESTFAC": "0",
        "CLIFAC": 1234, "CNOFAC": "CLIENTE ANTERIOR SA", "CDOFAC": "Calle Vieja 1",
        "CPOFAC": "Valencia", "CCPFAC": "46000", "CPRFAC": "Valencia", "CNIFAC": "A00000009",
        "CPAFAC": "ESPAÑA", "OB1FAC": "Pedido web BOPRIN-EJEMPLO", "OB2FAC": "",
        "IIVA1FAC": 369.6, "IIVA2FAC": 0.0,
    }
    row.update(over)
    return row


#: Clientes de F_CLI como los devuelve `get_customer` (normalizados).
CLIENTES: dict[str, dict[str, Any]] = {
    "4321": {"codcli": "4321", "nombre": "COMPONENTES EJEMPLO SL", "nif": "B00000001",
             "domcli": "Calle Nueva 2", "pobcli": "Madrid", "cpocli": "28000",
             "procli": "Madrid", "paicli": "724", "pais_iso2": "ES", "regime": "nacional"},
    "7777": {"codcli": "7777", "nombre": "EJEMPLO SARL", "nif": "FR00000000001",
             "domcli": "1 Rue Exemple", "pobcli": "Lyon", "cpocli": "69000",
             "procli": "", "paicli": "250", "pais_iso2": "FR", "regime": "intracomunitario"},
}


def _get_customer(_client, codcli, *, ejercicio):
    return CLIENTES.get(str(codcli).strip())


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    eng = create_engine("sqlite+pysqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    with factory() as s:
        seed_test_users(s)
        s.add(Company(id="c-old", name="Cliente Anterior SA", factusol_company_id="1234",
                      country="ES", tax_id="A00000009"))
        s.commit()
    yield factory
    Base.metadata.drop_all(eng)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _pedido(s: Session, *, factura: bool = True, **over) -> Order:
    campos = {
        "id": "o-1", "external_source": OrderSource.WOOCOMMERCE, "external_id": "100015",
        "order_number": "BOPRIN-100015", "company_id": "c-old", "total_amount": 2129.6,
        "currency": "EUR", "payment_status": "paid",
        "approved_at": datetime(2026, 10, 20, tzinfo=UTC),
        "invoice_status": "invoiced_by_erp" if factura else "not_invoiced",
        "factusol_invoice_number": "526090" if factura else None,
        "factusol_invoice_serie": 2 if factura else None,
    }
    o = Order(**{**campos, **over})
    s.add(o)
    s.commit()
    return o


def _factusol(fake):
    return patch("app.integrations.factusol.client.FactusolClient.from_settings",
                 return_value=fake)


def _clientes():
    return patch.multiple(
        "app.integrations.factusol.albaran_manual", get_customer=_get_customer,
    ), patch("app.integrations.factusol.customers.get_customer", _get_customer)


def _menciones(http, items, verifactu: bool | None = None):
    body: dict[str, Any] = {"menciones_legales": items}
    if verifactu is not None:
        body["verifactu_activo"] = verifactu
    r = http.patch("/api/erp/settings", headers=auth_headers(http, "admin"), json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- configuración: menciones editables sin código -----------------------------------


def test_menciones_de_partida_son_de_referencia_y_se_editan_en_configuracion(http) -> None:
    h = auth_headers(http, "pedidos")
    cat = http.get("/api/erp/catalogs/menciones-legales", headers=h).json()
    assert [m["id"] for m in cat["items"]] == ["ceuta-melilla", "canarias", "fuera-ue"]
    assert all(m["revisado"] is False for m in cat["items"])     # textos sin revisar
    assert cat["verifactu_activo"] is False

    out = _menciones(http, [
        {"id": "fuera-ue", "nombre": "Fuera de la UE", "texto": TEXTO_UE, "revisado": True},
        {"nombre": "Una que añade Bart", "texto": "Texto de prueba", "revisado": True},
        {"nombre": "Sin texto aún", "texto": "", "revisado": True},
    ], verifactu=True)
    assert out["verifactu_activo"] is True
    ms = {m["nombre"]: m for m in out["menciones_legales"]}
    assert ms["Una que añade Bart"]["id"] == "una-que-anade-bart"
    assert ms["Sin texto aún"]["revisado"] is False              # sin texto no se revisa
    r = http.patch("/api/erp/settings", headers=auth_headers(http, "admin"),
                   json={"menciones_legales": [{"nombre": "Larga", "texto": "x" * 256}]})
    assert r.status_code == 400 and "255" in r.json()["detail"]


# --- mención legal en una factura emitida ---------------------------------------------


def test_mencion_vista_previa_confirmacion_escribe_lo_previsto_y_audita(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _pedido(s)
    _menciones(http, [{"id": "fuera-ue", "nombre": "Fuera de la UE", "texto": TEXTO_UE,
                       "revisado": True}])
    fake = FakeFactusol([_fac()])
    h = auth_headers(http, "pedidos")
    with _factusol(fake):
        previa = http.post("/api/erp/orders/o-1/factura/mencion/vista-previa", headers=h,
                           json={"mencion_id": "fuera-ue"})
        assert previa.status_code == 200, previa.text
        p = previa.json()
        assert p["campo"] == "OB2FAC" and p["texto"] == TEXTO_UE and p["ya_esta"] is False
        assert p["actuales"]["OB1FAC"] == "Pedido web BOPRIN-EJEMPLO"
        assert p["enviada"] is None and p["avisos"] == []
        assert fake.updates == []                                   # la vista previa no escribe

        sin = http.post("/api/erp/orders/o-1/factura/mencion", headers=h,
                        json={"mencion_id": "fuera-ue", "campo": "OB2FAC"})
        assert sin.status_code == 400 and fake.updates == []        # sin confirmar, nada
        r = http.post("/api/erp/orders/o-1/factura/mencion", headers=h,
                      json={"mencion_id": "fuera-ue", "campo": "OB2FAC", "confirm": True})
    assert r.status_code == 200, r.text
    # SOLO la observación, con la clave compuesta tal cual la devolvió FACTUSOL.
    assert fake.updates == [("F_FAC", {"TIPFAC": "2", "CODFAC": 526090, "OB2FAC": TEXTO_UE})]
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.invoice_mention_added")).one()
        meta = json.loads(log.metadata_json)
        assert log.target_id == "o-1" and log.actor_email == "pedidos@example.com"
        assert meta["campo"] == "OB2FAC" and meta["texto"] == TEXTO_UE
        assert meta["factura"] == "2-526090" and meta["comprobada"] is True


def test_mencion_avisa_si_la_factura_ya_se_envio(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
        s.add(AuditLog(action="erp.invoice_emailed", target_type="order", target_id="o-1"))
        s.commit()
    with _factusol(FakeFactusol([_fac()])):
        p = http.post("/api/erp/orders/o-1/factura/mencion/vista-previa",
                      headers=auth_headers(http, "pedidos"),
                      json={"texto": "Mención libre de prueba"}).json()
    assert p["enviada"]["via"] == "email"
    assert any("el PDF que tiene NO lleva la mención" in a for a in p["avisos"])


def test_mencion_no_pisa_ni_escribe_si_cambio_desde_la_vista_previa(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    h = auth_headers(http, "pedidos")
    # Alguien escribió OB2FAC en FACTUSOL entre la vista previa y la confirmación.
    fake = FakeFactusol([_fac(OB2FAC="Escrito a mano en FACTUSOL")])
    with _factusol(fake):
        r = http.post("/api/erp/orders/o-1/factura/mencion", headers=h,
                      json={"texto": "Mención libre de prueba", "campo": "OB2FAC",
                            "confirm": True})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "cambio_en_factusol"
        # Las dos llenas: no hay hueco, no se ofrece escribir.
        p = http.post("/api/erp/orders/o-1/factura/mencion/vista-previa", headers=h,
                      json={"texto": "Mención libre de prueba"}).json()
    assert p["campo"] is None and fake.updates == []


def test_una_mencion_sin_revisar_no_se_aplica(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    with _factusol(FakeFactusol([_fac()])):
        r = http.post("/api/erp/orders/o-1/factura/mencion/vista-previa",
                      headers=auth_headers(http, "pedidos"), json={"mencion_id": "canarias"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "mencion_sin_revisar"


def test_la_mencion_se_elige_al_emitir() -> None:
    """Al emitir: la mención va a OB1FAC de la factura nueva."""
    cab = pcl_row_to_fac_payload(
        {"CLIPCL": 4321, "TOTPCL": 100.0}, "526100", "2026", fecha_emision="2026-10-30",
        options=FacturaOptions(serie=2, ob1fac=TEXTO_UE),
    )
    assert cab["OB1FAC"] == TEXTO_UE


def test_emitir_con_mencion_sin_revisar_se_rechaza(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, factura=False)
    with patch("app.integrations.factusol.jobs.enqueue_emit_invoice") as enq:
        r = http.post("/api/erp/orders/o-1/emit-factusol-invoice",
                      headers=auth_headers(http, "pedidos"), json={"mencion_id": "canarias"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "mencion_sin_revisar"
        enq.return_value = "job-1"
        r = http.post("/api/erp/orders/o-1/emit-factusol-invoice",
                      headers=auth_headers(http, "pedidos"), json={"mencion_texto": "Libre"})
    assert r.status_code == 202, r.text
    assert enq.call_args.kwargs["options"]["ob1fac"] == "Libre"
    assert "mencion_texto" not in enq.call_args.kwargs["options"]


# --- cambiar el cliente -----------------------------------------------------------------


def _relink(http, codcli: str, fake, *, campos: list[str] | None = None, preview: bool = False,
            role: str = "pedidos"):
    a, b = _clientes()
    url = "/api/erp/orders/o-1/cliente" + ("/vista-previa" if preview else "")
    body: dict[str, Any] = {"codcli": codcli}
    if not preview:
        body.update(confirm=True, campos_factura=campos or [])
    with _factusol(fake), a, b:
        return http.post(url, headers=auth_headers(http, role), json=body)


def test_revincular_sin_factura_a_un_cliente_que_existe(http, session_factory) -> None:
    """BOPRIN-100015: desde la ficha, sin tocar la base de datos."""
    with session_factory() as s:
        _pedido(s, factura=False)
    fake = FakeFactusol()
    p = _relink(http, "4321", fake, preview=True)
    assert p.status_code == 200, p.text
    plan = p.json()
    assert plan["actual"]["nombre"] == "Cliente Anterior SA"
    assert plan["cliente"]["codcli"] == "4321" and plan["empresa"]["crear"] is True
    assert plan["factura"] is None
    r = _relink(http, "4321", fake)
    assert r.status_code == 200, r.text
    assert r.json()["empresa_creada"] is True and r.json()["escrito_en_factura"] is False
    assert fake.updates == []                         # sin factura, FACTUSOL no se toca
    with session_factory() as s:
        o = s.get(Order, "o-1")
        nueva = s.get(Company, o.company_id)
        assert nueva.factusol_company_id == "4321" and nueva.name == "COMPONENTES EJEMPLO SL"
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_client_relinked")).one()
        meta = json.loads(log.metadata_json)
        assert (meta["from_company"], meta["from_codcli"]) == ("c-old", "1234")
        assert (meta["to_codcli"], meta["to_name"]) == ("4321", "COMPONENTES EJEMPLO SL")
        assert log.actor_email == "pedidos@example.com"
        # Revinculado: al emitir, la factura sale a ESTE cliente.
        assert fc.cliente_revinculado(s, o) == "4321"


def test_con_factura_emitida_avisa_ensena_los_cambios_y_los_escribe(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    plan = _relink(http, "4321", fake, preview=True).json()
    fac = plan["factura"]
    assert fac["numero"] == "2-526090" and fac["bloqueo"] is None and fac["puedes"] is True
    cambios = {c["campo"]: (c["antes"], c["despues"]) for c in fac["cambios"]}
    assert cambios["CLIFAC"] == ("1234", "4321")
    assert cambios["CNOFAC"] == ("CLIENTE ANTERIOR SA", "COMPONENTES EJEMPLO SL")
    assert cambios["CNIFAC"] == ("A00000009", "B00000001")
    assert any("rectificativa" in a for a in fac["avisos"])
    assert fake.updates == []

    # La vista previa enseñó estos campos: si no coinciden, no se escribe nada.
    r = _relink(http, "4321", fake, campos=["CLIFAC"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cambio_en_factusol"
    assert fake.updates == []
    r = _relink(http, "4321", fake, campos=list(cambios))
    assert r.status_code == 200, r.text
    assert r.json()["escrito_en_factura"] is True
    [(tabla, registro)] = fake.updates
    assert tabla == "F_FAC" and registro["TIPFAC"] == "2" and registro["CODFAC"] == 526090
    assert registro["CLIFAC"] == 4321 and isinstance(registro["CLIFAC"], int)   # mismo tipo
    assert registro["CNOFAC"] == "COMPONENTES EJEMPLO SL"
    assert "TOTFAC" not in registro and "OB1FAC" not in registro    # solo el bloque de cliente
    with session_factory() as s:
        meta = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.order_client_relinked")).one())
        assert meta["factura"] == "2-526090"
        assert {"campo": "CLIFAC", "antes": "1234", "despues": "4321"} in meta["cambios_factura"]


def test_la_factura_no_se_cambia_si_el_iva_no_casa_o_hay_verifactu(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    # Factura con IVA → cliente intracomunitario: llevaría el IVA mal.
    plan = _relink(http, "7777", fake, preview=True).json()
    assert "IVA" in plan["factura"]["bloqueo"]
    campos = [c["campo"] for c in plan["factura"]["cambios"]]
    r = _relink(http, "7777", fake, campos=campos)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "factura_bloqueada"
    assert fake.updates == []
    with session_factory() as s:
        assert s.get(Order, "o-1").company_id == "c-old"            # nada cambia en BoHub
    # VERI*FACTU activo: tampoco con un cliente que casa.
    _menciones(http, [], verifactu=True)
    plan = _relink(http, "4321", fake, preview=True).json()
    assert "VERI*FACTU" in plan["factura"]["bloqueo"]


def test_cambiar_el_cliente_de_una_factura_es_de_quien_emite(http, session_factory) -> None:
    """El rol «user» (solo lectura) ni siquiera puede revincular; y la factura
    exige además emitir facturas."""
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    r = _relink(http, "4321", fake, campos=["CLIFAC"], role="user")
    assert r.status_code == 403 and fake.updates == []


def test_crear_cliente_comprueba_antes_el_cif(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, factura=False)
    datos = {"nombre": "Componentes Ejemplo SL", "nif": "B00000001", "direccion": "Calle Nueva 2",
             "cp": "28000", "ciudad": "Madrid", "provincia": "Madrid", "pais": "ES"}
    h = auth_headers(http, "pedidos")
    ya = [{"codcli": "4321", "nombre": "COMPONENTES EJEMPLO SL", "nif": "B00000001"}]
    with _factusol(FakeFactusol()), patch(
        "app.integrations.factusol.customers.search_customers", return_value=ya,
    ), patch("app.integrations.factusol.customers.create_customer") as crear:
        p = http.post("/api/erp/orders/o-1/cliente/nuevo/vista-previa", headers=h, json=datos)
        assert p.status_code == 200, p.text
        assert p.json()["existe"] is True and p.json()["campos"] == []
        assert p.json()["factusol"][0]["codcli"] == "4321"
        r = http.post("/api/erp/orders/o-1/cliente/nuevo", headers=h,
                      json={**datos, "confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ya_existe"
    assert r.json()["detail"]["codcli"] == "4321"
    crear.assert_not_called()                       # un duplicado en FACTUSOL dura años

    nuevo = {**datos, "nif": "B00000002", "nombre": "Componentes Nuevos SL"}
    with _factusol(FakeFactusol()), patch(
        "app.integrations.factusol.customers.search_customers", return_value=[],
    ), patch("app.integrations.factusol.customers.create_customer",
             return_value=("5000", True)) as crear:
        p = http.post("/api/erp/orders/o-1/cliente/nuevo/vista-previa", headers=h, json=nuevo)
        campos = {c["campo"]: c["valor"] for c in p.json()["campos"]}
        assert campos["NOFCLI"] == "Componentes Nuevos SL" and campos["NIFCLI"] == "B00000002"
        assert p.json()["regime"] == "nacional"
        r = http.post("/api/erp/orders/o-1/cliente/nuevo", headers=h,
                      json={**nuevo, "confirm": True})
    assert r.status_code == 201, r.text
    assert r.json() == {"codcli": "5000", "creado": True}
    assert crear.call_args.args[1]["nif"] == "B00000002"
    with session_factory() as s:
        assert s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_customer_created")).one().target_id == "o-1"


# --- al emitir tras revincular -----------------------------------------------------------


def test_al_emitir_tras_revincular_la_factura_sale_al_cliente_nuevo() -> None:
    cabecera = {"CLIFAC": 1234, "CNOFAC": "CLIENTE ANTERIOR SA", "CNIFAC": "A00000009",
                "CDOFAC": "Calle Vieja 1", "IIVA1FAC": 21.0}
    pcl = {"CLIPCL": 1234, "IIVA1PCL": 21.0}
    with patch("app.integrations.factusol.albaran_manual.get_customer", _get_customer):
        cambios = fc.cliente_para_emitir(object(), cabecera=cabecera, codcli="4321",
                                         ejercicio="2026", origen=pcl,
                                         documento="El pedido de cliente")
        assert cambios["CLIFAC"] == 4321 and cambios["CNOFAC"] == "COMPONENTES EJEMPLO SL"
        # Un cliente nuevo con otro régimen de IVA: no se emite.
        with pytest.raises(FactusolError, match="IVA"):
            fc.cliente_para_emitir(object(), cabecera=cabecera, codcli="7777", ejercicio="2026",
                                   origen=pcl, documento="El pedido de cliente")
