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
    vea lo escrito. `caida_tras_escribir`: tras la primera escritura, FACTUSOL
    deja de responder a las lecturas (la relectura falla)."""

    def __init__(self, f_fac: list[dict] | None = None, f_alb: list[dict] | None = None):
        self.default_ejercicio = "2026"
        self.f_fac = f_fac or []
        self.f_alb = f_alb or []
        self.updates: list[tuple[str, dict]] = []
        self.writes: list[tuple[str, dict]] = []
        self.caida_tras_escribir = False
        #: La escritura llega a FACTUSOL pero se pierde la respuesta.
        self.respuesta_perdida = False

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if self.caida_tras_escribir and self.updates:
            raise FactusolError("tiempo de espera agotado")
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
        if self.respuesta_perdida:
            raise FactusolError("tiempo de espera agotado")

    def write_record(self, tabla, data, *, ejercicio=None):
        self.writes.append((tabla, dict(data)))


def _fac(**over) -> dict:
    row = {
        "TIPFAC": "2", "CODFAC": 526090, "TOTFAC": 2129.6, "ESTFAC": "0",
        "CLIFAC": 1234, "CNOFAC": "CLIENTE ANTERIOR SA", "CDOFAC": "Calle Vieja 1",
        "CPOFAC": "Valencia", "CCPFAC": "46000", "CPRFAC": "Valencia", "CNIFAC": "A00000009",
        "CPAFAC": "724", "OB1FAC": "Pedido web BOPRIN-EJEMPLO", "OB2FAC": "",
        "IIVA1FAC": 369.6, "IIVA2FAC": 0.0,
    }
    row.update(over)
    return row


#: Clientes de F_CLI como los devuelve `get_customer` (normalizados).
CLIENTES: dict[str, dict[str, Any]] = {
    "1234": {"codcli": "1234", "nombre": "CLIENTE ANTERIOR SA", "nif": "A00000009",
             "domcli": "Calle Vieja 1", "pobcli": "Valencia", "cpocli": "46000",
             "procli": "Valencia", "paicli": "724", "pais_iso2": "ES", "regime": "nacional"},
    "4321": {"codcli": "4321", "nombre": "COMPONENTES EJEMPLO SL", "nif": "B00000001",
             "domcli": "Calle Nueva 2", "pobcli": "Madrid", "cpocli": "28000",
             "procli": "Madrid", "paicli": "724", "pais_iso2": "ES", "regime": "nacional"},
    "7777": {"codcli": "7777", "nombre": "EJEMPLO SARL", "nif": "FR00000000001",
             "domcli": "1 Rue Exemple", "pobcli": "Lyon", "cpocli": "69000",
             "procli": "", "paicli": "250", "pais_iso2": "FR", "regime": "intracomunitario"},
    "7778": {"codcli": "7778", "nombre": "OTRA EJEMPLO SARL", "nif": "FR00000000002",
             "domcli": "2 Rue Exemple", "pobcli": "Lyon", "cpocli": "69000",
             "procli": "", "paicli": "250", "pais_iso2": "FR", "regime": "intracomunitario"},
    # Belga marcado intracomunitario en su ficha: para MQ Europe (serie 2,
    # Bélgica) es nacional pase lo que pase.
    "8888": {"codcli": "8888", "nombre": "VOORBEELD BV", "nif": "BE0000000097",
             "domcli": "Voorbeeldstraat 1", "pobcli": "Gent", "cpocli": "9000",
             "procli": "", "paicli": "056", "pais_iso2": "BE", "regime": "intracomunitario"},
    # Canarias: España como el emisor, pero exenta como una exportación (su
    # ficha lo dice; BoHub no lo distingue por el país).
    "3501": {"codcli": "3501", "nombre": "ISLAS EJEMPLO SL", "nif": "B00000035",
             "domcli": "Calle Ejemplo 3", "pobcli": "Las Palmas", "cpocli": "35001",
             "procli": "Las Palmas", "paicli": "724", "pais_iso2": "ES", "regime": "exportacion"},
    "3502": {"codcli": "3502", "nombre": "OTRA ISLAS EJEMPLO SL", "nif": "B00000036",
             "domcli": "Calle Ejemplo 4", "pobcli": "Santa Cruz", "cpocli": "38001",
             "procli": "Tenerife", "paicli": "724", "pais_iso2": "ES", "regime": "exportacion"},
    "9999": {"codcli": "9999", "nombre": "BEISPIEL AG", "nif": "CHE000000001",
             "domcli": "Beispielweg 1", "pobcli": "Basel", "cpocli": "4000",
             "procli": "", "paicli": "756", "pais_iso2": "CH", "regime": "exportacion"},
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


def test_mencion_si_falla_la_relectura_consta_en_auditoria(http, session_factory) -> None:
    """FACTUSOL acepta la escritura y deja de responder al releer: la mención
    ya está en Auditoría (sin comprobar) y el error lo dice; reintentar no la
    escribe otra vez."""
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    fake.caida_tras_escribir = True
    h = auth_headers(http, "pedidos")
    body = {"texto": "Mención libre de prueba", "campo": "OB2FAC", "confirm": True}
    with _factusol(fake):
        r = http.post("/api/erp/orders/o-1/factura/mencion", headers=h, json=body)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "relectura_fallida"
    assert "se escribió" in r.json()["detail"]["detail"]
    assert len(fake.updates) == 1
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.invoice_mention_added")).one()
        assert json.loads(log.metadata_json)["comprobada"] is None
    fake.caida_tras_escribir = False
    with _factusol(fake):
        r = http.post("/api/erp/orders/o-1/factura/mencion", headers=h, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ya_esta"
    assert len(fake.updates) == 1


def test_mencion_escrita_con_la_respuesta_perdida_cuenta_como_escrita(
    http, session_factory,
) -> None:
    """La escritura llega a FACTUSOL pero la respuesta se pierde: se relee, la
    mención está, y queda en Auditoría como escrita."""
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    fake.respuesta_perdida = True
    body = {"texto": "Mención libre de prueba", "campo": "OB2FAC", "confirm": True}
    with _factusol(fake):
        r = http.post("/api/erp/orders/o-1/factura/mencion",
                      headers=auth_headers(http, "pedidos"), json=body)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        meta = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.invoice_mention_added")).one())
    assert meta["respuesta_perdida"] is True and meta["comprobada"] is True


def test_una_factura_abonada_no_se_toca(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, invoice_status="credit_note")
    fake = FakeFactusol([_fac()])
    h = auth_headers(http, "pedidos")
    with _factusol(fake):
        for url, body in (
            ("/factura/mencion/vista-previa", {"texto": "Mención libre de prueba"}),
            ("/factura/mencion", {"texto": "Mención libre de prueba", "campo": "OB2FAC",
                                  "confirm": True}),
        ):
            r = http.post(f"/api/erp/orders/o-1{url}", headers=h, json=body)
            assert r.status_code == 409 and r.json()["detail"]["code"] == "factura_abonada"
    plan = _relink(http, "4321", fake, preview=True).json()
    assert "abonada" in plan["factura"]["bloqueo"]
    campos = {c["campo"]: c["despues"] for c in plan["factura"]["cambios"]}
    r = _relink(http, "4321", fake, campos=campos)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "factura_abonada"
    assert fake.updates == []
    with session_factory() as s:
        assert s.get(Order, "o-1").company_id == "c-old"


def test_una_mencion_sin_revisar_no_se_aplica(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    with _factusol(FakeFactusol([_fac()])):
        r = http.post("/api/erp/orders/o-1/factura/mencion/vista-previa",
                      headers=auth_headers(http, "pedidos"), json={"mencion_id": "canarias"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "mencion_sin_revisar"


def _cab_web(**pcl) -> dict:
    return pcl_row_to_fac_payload(
        {"CLIPCL": 4321, "TOTPCL": 100.0, **pcl}, "526100", "2026",
        fecha_emision="2026-10-30", options=FacturaOptions(serie=2, mencion=TEXTO_UE),
    )


def test_la_mencion_se_elige_al_emitir_en_la_primera_observacion_vacia() -> None:
    """Al emitir: la mención va a la primera observación vacía de la factura
    nueva; la que se copió del pedido de cliente no se pisa."""
    assert _cab_web()["OB1FAC"] == TEXTO_UE
    cab = _cab_web(OB1PCL="Entregar por la tarde", OB2PCL="")
    assert cab["OB1FAC"] == "Entregar por la tarde" and cab["OB2FAC"] == TEXTO_UE
    cab = _cab_web(OB1PCL=TEXTO_UE)                                 # ya la lleva: no se repite
    assert cab["OB1FAC"] == TEXTO_UE and not cab.get("OB2FAC")
    with pytest.raises(FactusolError, match="no cabe"):              # las dos llenas: no se emite
        _cab_web(OB1PCL="Entregar por la tarde", OB2PCL="Llamar antes")
    # Un job encolado antes del cambio trae la mención como `ob1fac`.
    assert FacturaOptions.from_payload({"ob1fac": "Libre"}).mencion == "Libre"


def test_la_mencion_al_emitir_desde_el_albaran_no_pisa_sus_observaciones(
    session_factory,
) -> None:
    from app.integrations.factusol import service

    alb = {"TIPALB": "1", "CODALB": 123, "CLIALB": 1234, "OB1ALB": "Entrega en almacén",
           "OB2ALB": "", "IIVA1ALB": 21.0, "CNOALB": "CLIENTE ANTERIOR SA"}
    with session_factory() as s:
        o = _pedido(s, factura=False, factusol_albaran_number="1-000123")

        class Parada(Exception):
            pass

        with patch("app.integrations.factusol.chain.convert_document",
                   side_effect=Parada) as conv, patch(
            "app.integrations.factusol.albaran_manual.get_customer", _get_customer,
        ):
            with pytest.raises(Parada):
                service._emit_from_albaran(
                    s, o, FakeFactusol(f_alb=[dict(alb)]), ejercicio="2026", actor=None,
                    options=FacturaOptions(mencion=TEXTO_UE, codcli="4321"),
                )
            over = conv.call_args.kwargs["header_overrides"]
            assert over["OB2FAC"] == TEXTO_UE and "OB1FAC" not in over
            assert over["CLIFAC"] == 4321 and over["CNOFAC"] == "COMPONENTES EJEMPLO SL"
            conv.reset_mock()
            # Las dos observaciones del albarán ya tienen texto: no se escribe nada.
            lleno = FakeFactusol(f_alb=[{**alb, "OB2ALB": "Llamar antes"}])
            with pytest.raises(FactusolError, match="no cabe"):
                service._emit_from_albaran(s, o, lleno, ejercicio="2026", actor=None,
                                           options=FacturaOptions(mencion=TEXTO_UE))
            conv.assert_not_called()


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
    # Lo que leerá el worker al desencolar.
    assert FacturaOptions.from_payload(enq.call_args.kwargs["options"]).mencion == "Libre"
    assert "mencion_texto" not in enq.call_args.kwargs["options"]


# --- cambiar el cliente -----------------------------------------------------------------


def _relink(http, codcli: str, fake, *, campos: dict[str, str] | None = None,
            preview: bool = False, role: str = "pedidos"):
    a, b = _clientes()
    url = "/api/erp/orders/o-1/cliente" + ("/vista-previa" if preview else "")
    body: dict[str, Any] = {"codcli": codcli}
    if not preview:
        body.update(confirm=True, campos_factura=campos or {})
    with _factusol(fake), a, b:
        return http.post(url, headers=auth_headers(http, role), json=body)


def _campos(plan: dict) -> dict[str, str]:
    """Lo que manda la ficha al confirmar: campo → valor nuevo de la vista previa."""
    return {c["campo"]: c["despues"] for c in plan["factura"]["cambios"]}


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

    # La vista previa enseñó estos campos y valores: si no coinciden, no se
    # escribe nada (ni con otros campos ni con los mismos y otro valor).
    r = _relink(http, "4321", fake, campos={"CLIFAC": "4321"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cambio_en_factusol"
    r = _relink(http, "4321", fake,
                campos={**_campos(plan), "CNOFAC": "COMPONENTES EJEMPLO SL (antes)"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cambio_en_factusol"
    assert fake.updates == []
    r = _relink(http, "4321", fake, campos=_campos(plan))
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
        # La escritura en FACTUSOL tiene su propio registro, comprobado al releer.
        fac_log = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.invoice_client_changed")).one())
        assert fac_log["comprobada"] is True and fac_log["to_codcli"] == "4321"
        assert {"campo": "CLIFAC", "antes": "1234", "despues": "4321"} in fac_log["cambios"]


def test_la_factura_no_se_cambia_si_el_iva_no_casa_o_hay_verifactu(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    # Factura con IVA → cliente intracomunitario: llevaría el IVA mal.
    plan = _relink(http, "7777", fake, preview=True).json()
    assert "IVA" in plan["factura"]["bloqueo"]
    r = _relink(http, "7777", fake, campos=_campos(plan))
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
    r = _relink(http, "4321", fake, campos={"CLIFAC": "4321"}, role="user")
    assert r.status_code == 403 and fake.updates == []


def test_el_regimen_es_el_de_quien_emite_la_serie(http, session_factory) -> None:
    """Factura de la serie 2 (MQ Europe, Bélgica) sin IVA, de un cliente
    francés intracomunitario. Un cliente belga marcado intracomunitario en su
    ficha es NACIONAL para MQ Europe: la factura llevaría el IVA mal."""
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac(CLIFAC=7777, IIVA1FAC=0.0)])
    plan = _relink(http, "8888", fake, preview=True).json()
    assert "no lleva IVA" in plan["factura"]["bloqueo"]
    assert "serie 2" in plan["factura"]["bloqueo"]
    r = _relink(http, "8888", fake, campos=_campos(plan))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "factura_bloqueada"
    # Otro francés intracomunitario: mismo régimen, sí se puede.
    assert _relink(http, "7778", fake, preview=True).json()["factura"]["bloqueo"] is None
    assert fake.updates == []


def test_intracomunitario_y_exportacion_no_se_cambian_entre_si(http, session_factory) -> None:
    """Las dos sin IVA, pero no son el mismo régimen (ni la misma mención):
    del cliente francés al suizo, no. Si el cliente de la factura no se puede
    leer en F_CLI, queda solo la comprobación del IVA."""
    with session_factory() as s:
        _pedido(s, factusol_invoice_serie=1)
    fake = FakeFactusol([_fac(TIPFAC="1", CLIFAC=7777, IIVA1FAC=0.0)])
    bloqueo = _relink(http, "9999", fake, preview=True).json()["factura"]["bloqueo"]
    assert "intracomunitario" in bloqueo and "exportación" in bloqueo
    fake.f_fac[0]["CLIFAC"] = 5555                          # no está en F_CLI
    assert _relink(http, "9999", fake, preview=True).json()["factura"]["bloqueo"] is None


def test_si_falla_la_relectura_la_factura_consta_cambiada_y_reintentar_completa(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    plan = _relink(http, "4321", fake, preview=True).json()
    fake.caida_tras_escribir = True
    r = _relink(http, "4321", fake, campos=_campos(plan))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "relectura_fallida"
    assert "ya se cambió en FACTUSOL; el pedido no" in r.json()["detail"]["detail"]
    assert len(fake.updates) == 1
    with session_factory() as s:
        assert s.get(Order, "o-1").company_id == "c-old"
        log = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.invoice_client_changed")).one())
        assert log["comprobada"] is None and log["factura"] == "2-526090"
    # Reintento con la misma vista previa: la factura ya está; no se escribe
    # otra vez y el pedido se completa.
    fake.caida_tras_escribir = False
    r = _relink(http, "4321", fake, campos=_campos(plan))
    assert r.status_code == 200, r.text
    assert r.json()["escrito_en_factura"] is True and len(fake.updates) == 1
    with session_factory() as s:
        o = s.get(Order, "o-1")
        assert s.get(Company, o.company_id).factusol_company_id == "4321"
        meta = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.order_client_relinked")).one())
        assert meta["factura_ya_cambiada"] is True
        assert fc.cliente_revinculado(s, o) == "4321"


def test_cliente_escrito_con_la_respuesta_perdida_cuenta_como_escrito(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    plan = _relink(http, "4321", fake, preview=True).json()
    fake.respuesta_perdida = True
    r = _relink(http, "4321", fake, campos=_campos(plan))
    assert r.status_code == 200, r.text
    assert r.json()["escrito_en_factura"] is True
    with session_factory() as s:
        meta = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.invoice_client_changed")).one())
        assert meta["respuesta_perdida"] is True and meta["comprobada"] is True
        assert {"campo": "CLIFAC", "antes": "1234", "despues": "4321"} in meta["cambios"]


def test_canarias_ceuta_y_melilla_mandan_su_ficha(http, session_factory) -> None:
    """Factura sin IVA de la serie 1 (España) a un cliente de Canarias marcado
    «exportación»: otro de Canarias igual sí se puede poner; uno de la
    Península (nacional), no."""
    with session_factory() as s:
        _pedido(s, factusol_invoice_serie=1)
    fake = FakeFactusol([_fac(TIPFAC="1", CLIFAC=3501, IIVA1FAC=0.0)])
    assert _relink(http, "3502", fake, preview=True).json()["factura"]["bloqueo"] is None
    assert _relink(http, "4321", fake, preview=True).json()["factura"]["bloqueo"]


def test_si_falla_bohub_tras_escribir_lo_dice_y_reintentar_completa(
    http, session_factory,
) -> None:
    with session_factory() as s:
        _pedido(s)
    fake = FakeFactusol([_fac()])
    plan = _relink(http, "4321", fake, preview=True).json()
    with patch("app.erp.web_order_company._company_from_customer",
               side_effect=RuntimeError("base de datos ocupada")):
        r = _relink(http, "4321", fake, campos=_campos(plan))
    assert r.status_code == 500 and r.json()["detail"]["code"] == "pedido_sin_cambiar"
    assert "ya se cambió en FACTUSOL" in r.json()["detail"]["detail"]
    with session_factory() as s:
        assert s.get(Order, "o-1").company_id == "c-old"
        log = json.loads(s.scalars(select(AuditLog.metadata_json).where(
            AuditLog.action == "erp.invoice_client_changed")).one())
        assert log["comprobada"] is True
    r = _relink(http, "4321", fake, campos=_campos(plan))
    assert r.status_code == 200, r.text
    assert len(fake.updates) == 1
    with session_factory() as s:
        assert s.get(Order, "o-1").company_id != "c-old"


def test_empresa_del_mismo_nif_con_otro_codigo_la_factura_sale_al_elegido(
    http, session_factory,
) -> None:
    """Hay dos códigos en FACTUSOL con el mismo NIF. La empresa de BoHub está
    vinculada al otro: el pedido se queda con ella, pero al emitir la factura
    sale al cliente elegido."""
    with session_factory() as s:
        s.add(Company(id="c-dup", name="Componentes Ejemplo SL", factusol_company_id="4320",
                      country="ES", tax_id="B00000001"))
        s.commit()
        _pedido(s, factura=False, company_id="c-dup")
    fake = FakeFactusol()
    plan = _relink(http, "4321", fake, preview=True).json()
    assert plan["empresa"]["company_id"] == "c-dup" and plan["sin_cambios_crm"] is True
    assert plan["sin_cambios"] is False               # nunca se revinculó a 4321
    assert any("vinculada al cliente 4320" in a and "cliente 4321" in a for a in plan["avisos"])
    r = _relink(http, "4321", fake)
    assert r.status_code == 200, r.text
    with session_factory() as s:
        o = s.get(Order, "o-1")
        assert o.company_id == "c-dup"
        assert s.get(Company, "c-dup").factusol_company_id == "4320"     # el vínculo no cambia
        assert fc.cliente_revinculado(s, o) == "4321"
    r = _relink(http, "4321", fake)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sin_cambios"


def test_mismo_cliente_que_la_empresa_pero_el_documento_es_de_otro(
    http, session_factory,
) -> None:
    """El pedido web ya está con su empresa (1234), pero su F_PCL lleva otro
    cliente: elegir 1234 no es «sin cambios», es decidir a quién se factura."""
    with session_factory() as s:
        _pedido(s, factura=False)
    pcl = {"TIPPCL": "1", "CODPCL": 77, "CLIPCL": 5555, "IIVA1PCL": 21.0}
    with patch("app.integrations.factusol.service.find_pcl_by_order", return_value=pcl):
        plan = _relink(http, "1234", FakeFactusol(), preview=True).json()
        assert plan["sin_cambios_crm"] is True and plan["sin_cambios"] is False
        assert plan["documento"]["codcli"] == "5555" and plan["documento"]["bloqueo"] is None
        r = _relink(http, "1234", FakeFactusol())
    assert r.status_code == 200, r.text
    with session_factory() as s:
        assert fc.cliente_revinculado(s, s.get(Order, "o-1")) == "1234"


def test_crear_cliente_con_un_cif_de_relleno_no_sigue(http, session_factory) -> None:
    with session_factory() as s:
        _pedido(s, factura=False)
    h = auth_headers(http, "pedidos")
    datos = {"nombre": "Componentes Ejemplo SL", "pais": "ES"}
    with _factusol(FakeFactusol()), patch(
        "app.integrations.factusol.customers.search_customers", return_value=[],
    ) as buscar, patch("app.integrations.factusol.customers.create_customer") as crear:
        for nif in ("   ", "...", "ABCDEF", "B-1"):
            for url, extra in (("/vista-previa", {}), ("", {"confirm": True})):
                r = http.post(f"/api/erp/orders/o-1/cliente/nuevo{url}", headers=h,
                              json={**datos, "nif": nif, **extra})
                assert r.status_code == 400, (nif, r.text)
                assert r.json()["detail"]["code"] == "nif_no_valido"
    buscar.assert_not_called()
    crear.assert_not_called()


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


def _para_emitir(s: Session, codcli: str, origen: dict, *, serie: int) -> dict:
    cabecera = {"CLIFAC": 1234, "CNOFAC": "CLIENTE ANTERIOR SA", "CNIFAC": "A00000009",
                "CDOFAC": "Calle Vieja 1", "IIVA1FAC": origen.get("IIVA1PCL", 0.0)}
    return fc.cliente_para_emitir(
        s, object(), cabecera=cabecera, codcli=codcli, ejercicio="2026", origen=origen,
        codcli_origen=origen["CLIPCL"], documento="El pedido de cliente", serie=serie,
    )


def test_al_emitir_tras_revincular_la_factura_sale_al_cliente_nuevo(session_factory) -> None:
    pcl = {"CLIPCL": 1234, "IIVA1PCL": 21.0}
    with session_factory() as s, patch(
        "app.integrations.factusol.albaran_manual.get_customer", _get_customer,
    ):
        cambios = _para_emitir(s, "4321", pcl, serie=1)
        assert cambios["CLIFAC"] == 4321 and cambios["CNOFAC"] == "COMPONENTES EJEMPLO SL"
        # Un cliente nuevo con otro régimen de IVA: no se emite.
        with pytest.raises(FactusolError, match="IVA"):
            _para_emitir(s, "7777", pcl, serie=1)
        # Sin IVA, del francés intracomunitario al suizo (exportación): tampoco.
        sin_iva = {"CLIPCL": 7777, "IIVA1PCL": 0.0}
        with pytest.raises(FactusolError, match="exportación"):
            _para_emitir(s, "9999", sin_iva, serie=1)
        assert _para_emitir(s, "7778", sin_iva, serie=1)["CLIFAC"] == 7778
        # Serie 2 (MQ Europe, Bélgica): el belga es nacional para ella.
        with pytest.raises(FactusolError, match="serie 2"):
            _para_emitir(s, "8888", sin_iva, serie=2)
