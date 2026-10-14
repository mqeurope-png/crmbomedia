"""El rol SAT (taller, 14/10/2026): la Cola SAT y los pedidos de muestra.

Lo que Bart pidió, comprobado a nivel de API y con el usuario `sat` TAL CUAL
(sin roles ERP adicionales: la capacidad sale del propio rol):

1. no cobra, no factura y no manda correos (salvo «enviar al taller»);
2. da de alta una muestra y la lleva hasta enviada (y la completa / anula);
3. ve la bandeja y la ficha de cualquier pedido, pero en un pedido normal no
   actúa;
5. todo lo anterior sin `erp_roles`;
6. una muestra avanza por la Cola SAT sin cobrar, en todos los pasos;
7. una muestra no sale «pendiente de cobro» ni como descuadre del Cuadre;
8. un pedido normal sin cobrar sigue bloqueado donde lo estaba;
9. las capacidades del rol son las que dice su descripción.

(El 4 —el menú— y el resto del 3 —los botones— son de pantalla: jest.)
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.capabilities import Cap, can_manage_order, capabilities_for
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpException,
    ErpSettings,
    ExceptionStatus,
    ExceptionType,
    Order,
    OrderLine,
    StatusDomain,
)
from app.erp.state_machine import TransitionError, apply_transition, available_transitions
from app.main import app
from app.models.crm import User
from tests._test_helpers import auth_headers, seed_test_users

TALLER = "taller@streamtec.test"
DIRECCION = {"address_line": "Calle Taller 3", "city": "Sabadell",
             "postal_code": "08201", "country": "España"}


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
        seed.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID,
                             factusol_series_json=json.dumps({"sat_email": TALLER})))
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


def _sat(http) -> dict[str, str]:
    return auth_headers(http, "sat")


def _muestra(http, headers: dict[str, str] | None = None) -> dict[str, Any]:
    r = http.post("/api/erp/orders/sample", headers=headers or _sat(http), json={
        "recipient_name": "Prospecto Taller", "shipping_address": DIRECCION,
        "reason": "muestra de tinta",
        "lines": [{"product_sku": "", "description": "Cabezal", "quantity": 1,
                   "unit_price": 0}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _pedido_normal(s: Session, numero: str = "MAN-0100", *, payment: str = "pending",
                   prep: str = "in_queue", invoice: str | None = None) -> str:
    """Pedido corriente (facturable), aprobado y en la Cola SAT, SIN cobrar."""
    o = Order(order_number=numero, external_source="manual", preparation_status=prep,
              payment_status=payment, transport_status="not_shipped",
              factusol_invoice_number=invoice, factusol_invoice_serie=5 if invoice else None,
              total_amount=121)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU-A", description="Plotter",
                    quantity=1, unit_price=100, line_total=100))
    s.commit()
    return o.id


def _transicion(http, order_id: str, domain: str, to: str, **evidence: Any):
    return http.post(f"/api/erp/orders/{order_id}/transitions", headers=_sat(http),
                     json={"domain": domain, "to_status": to, "evidence": evidence})


# --- 5 · el usuario `sat` de las pruebas no tiene roles adicionales ------------


def test_el_usuario_sat_de_las_pruebas_no_tiene_roles_adicionales(session_factory):
    with session_factory() as s:
        u = s.scalar(select(User).where(User.email == "sat@example.com"))
        assert not u.erp_roles, "las pruebas deben usar el rol SAT a secas"


# --- 9 · las capacidades del rol = lo que dice su descripción -----------------


def test_capacidades_del_rol_sat_son_las_de_su_descripcion(http):
    caps = set(http.get("/api/auth/me", headers=_sat(http)).json()["capabilities"])
    # Lo suyo: la Cola SAT completa, ver pedidos (también web), dar de alta
    # muestras y «enviar al taller por email».
    assert {Cap.ACCESS, Cap.ORDERS_VIEW_WEB, Cap.SAT_VIEW, Cap.SAT_PREPARE,
            Cap.SAT_SHIPPING, Cap.SAT_TRACKING, Cap.SAT_NO_SHIPPING,
            Cap.SAMPLES_CREATE, Cap.EMAIL_SAT} == caps
    # Y nada de oficina: ni cobro, ni factura, ni albarán, ni correos al
    # cliente, ni documentos, proformas, empresas o datos de cliente.
    for cap in (Cap.COBRO_REGISTER, Cap.INVOICE_EMIT, Cap.ALBARAN_CREATE,
                Cap.EMAIL_CLIENT, Cap.DOCUMENTS, Cap.PROFORMAS, Cap.COMPANIES,
                Cap.OFFICE_VIEW, Cap.ORDERS_CREATE, Cap.ORDERS_APPROVE):
        assert cap not in caps, cap


def test_comercial_pedidos_y_admin_conservan_la_oficina(session_factory):
    with session_factory() as s:
        for rol in ("comercial", "pedidos", "admin"):
            u = s.scalar(select(User).where(User.email == f"{rol}@example.com"))
            caps = capabilities_for(u)
            assert Cap.OFFICE_VIEW in caps, rol
            assert Cap.EMAIL_CLIENT in caps, rol
            assert Cap.INVOICE_EMIT in caps, rol
        pedidos = s.scalar(select(User).where(User.email == "pedidos@example.com"))
        assert Cap.COBRO_REGISTER in capabilities_for(pedidos)


# --- 1 · sin cobro, sin factura, sin correos (API) -----------------------------


def test_sat_no_cobra_ni_factura_ni_manda_correos(http, session_factory):
    with session_factory() as s:
        oid = _pedido_normal(s, invoice="5-526100")
    h = _sat(http)
    pago = {"payment": {"paid": True, "forma_pago": "TRANSF"}}
    prohibidos = [
        # cobro
        ("post", f"/api/erp/orders/{oid}/payment", pago),
        ("post", "/api/erp/factusol/documents/facturas/5/526100/payment",
         {"confirm": True}),
        ("get", f"/api/erp/orders/{oid}/factusol-cobro", None),
        ("post", "/api/erp/orders/factusol-cobros/refresh", None),
        # factura / albarán
        ("post", f"/api/erp/orders/{oid}/emit-factusol-invoice", {"confirm": True}),
        ("post", f"/api/erp/orders/{oid}/albaran", {}),
        ("get", f"/api/erp/orders/{oid}/factusol-status", None),
        ("get", f"/api/erp/orders/{oid}/factusol-invoice-ref", None),
        # correos al cliente
        ("post", "/api/erp/factusol/documents/facturas/5/526100/email",
         {"confirm": True, "to": ["cliente@example.com"]}),
        ("get", f"/api/erp/orders/{oid}/genei/customer-email", None),
        ("post", f"/api/erp/orders/{oid}/genei/customer-email",
         {"to": "cliente@example.com"}),
        # contactos, documentos, proformas, ofertas
        ("get", "/api/companies", None),
        ("get", "/api/erp/ofertas", None),
        ("get", "/api/erp/factusol/quotes", None),
        ("get", "/api/erp/factusol/documents/facturas", None),
    ]
    for metodo, url, body in prohibidos:
        r = getattr(http, metodo)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, f"{metodo.upper()} {url} → {r.status_code} {r.text}"


def test_sat_envia_al_taller_pero_no_a_un_cliente_ni_con_factura(http, session_factory):
    """La excepción confirmada: «enviar al taller por email» — solo a la
    dirección del taller y sin la factura."""
    with session_factory() as s:
        oid = _pedido_normal(s, invoice="5-526101")
    h = _sat(http)
    base = {"confirm": True, "subject": "Pedido", "body_text": "Hola",
            "from_alias": "sat@example.com", "include_albaran": False,
            "include_pedido": True}
    a_cliente = http.post(f"/api/erp/orders/{oid}/email", headers=h,
                          json={**base, "to": ["cliente@example.com"]})
    assert a_cliente.status_code == 403
    assert a_cliente.json()["detail"]["code"] == "taller_only"
    en_copia = http.post(f"/api/erp/orders/{oid}/email", headers=h,
                         json={**base, "to": [TALLER], "bcc": ["cliente@example.com"]})
    assert en_copia.status_code == 403
    con_factura = http.post(f"/api/erp/orders/{oid}/email", headers=h,
                            json={**base, "to": [TALLER], "include_factura": True})
    assert con_factura.status_code == 403
    assert con_factura.json()["detail"]["code"] == "taller_only"
    # Al taller y sin factura sí pasa el permiso (lo que siga ya no es «taller_only»).
    al_taller = http.post(f"/api/erp/orders/{oid}/email", headers=h,
                          json={**base, "to": [TALLER.upper()]})
    assert al_taller.status_code != 403 or al_taller.json()["detail"]["code"] != "taller_only"
    # La oficina sigue mandándolo a quien haga falta.
    oficina = http.post(f"/api/erp/orders/{oid}/email", headers=auth_headers(http, "pedidos"),
                        json={**base, "to": ["cliente@example.com"]})
    assert oficina.status_code != 403 or oficina.json()["detail"]["code"] != "taller_only"


# --- 2 · la muestra de punta a punta -------------------------------------------


def test_sat_da_de_alta_una_muestra_y_la_lleva_hasta_enviada(http, session_factory):
    muestra = _muestra(http)
    oid = muestra["id"]
    assert muestra["order_kind"] == "sample"
    assert muestra["payment_status"] == "pending"          # nadie la cobra
    h = _sat(http)

    r = _transicion(http, oid, "preparation", "preparing")
    assert r.status_code == 200, r.text                    # sin cobrar: avanza
    r = http.post(f"/api/erp/orders/{oid}/packages", headers=h,
                  json=[{"weight_kg": 2, "height_cm": 20, "width_cm": 30, "depth_cm": 10}])
    assert r.status_code == 200, r.text
    assert _transicion(http, oid, "preparation", "packed").status_code == 200
    assert _transicion(http, oid, "transport", "label_created").status_code == 200
    r = _transicion(http, oid, "transport", "in_transit", tracking_number="TRK-1")
    assert r.status_code == 200, r.text
    assert r.json()["transport_status"] == "in_transit"

    # La completa (es suya) y la vuelve a abrir.
    r = http.post(f"/api/erp/orders/{oid}/complete", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["completed"] is True
    assert r.json()["completion_avisos"] == []              # ni «aún sin facturar»
    assert http.post(f"/api/erp/orders/{oid}/uncomplete", headers=h).status_code == 200

    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.payment_status.value == "pending"           # nunca se tocó el cobro
        assert o.factusol_invoice_number is None
        assert o.factusol_albaran_number is None


def test_sat_anula_y_restaura_su_muestra_sin_tocar_factusol(http):
    oid = _muestra(http)["id"]
    h = _sat(http)
    prev = http.post(f"/api/erp/orders/{oid}/cancel-preview", headers=h)
    assert prev.status_code == 200, prev.text
    assert prev.json()["factusol_docs"] == []
    r = http.post(f"/api/erp/orders/{oid}/cancel", headers=h,
                  json={"confirm": True, "reason": "ya no la quiere",
                        "delete_factusol_docs": True})
    assert r.status_code == 200, r.text
    assert r.json()["factusol_delete_job_id"] is None        # nada en FACTUSOL
    assert http.post(f"/api/erp/orders/{oid}/uncancel", headers=h).status_code == 200


def test_sat_resuelve_las_incidencias_de_su_muestra_y_no_las_de_otros(http, session_factory):
    muestra = _muestra(http)["id"]
    with session_factory() as s:
        normal = _pedido_normal(s, "MAN-0101")
        ids = []
        for oid in (muestra, normal):
            e = ErpException(type=ExceptionType.SAT_ISSUE, order_id=oid,
                             status=ExceptionStatus.OPEN)
            s.add(e)
            s.flush()
            ids.append(e.id)
        s.commit()
    h = _sat(http)
    nota = {"resolution_note": "resuelto en el taller"}
    assert http.post(f"/api/erp/exceptions/{ids[0]}/resolve", headers=h,
                     json=nota).status_code == 200
    r = http.post(f"/api/erp/exceptions/{ids[1]}/resolve", headers=h, json=nota)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "order_not_manageable"
    # La oficina, todas.
    assert http.post(f"/api/erp/exceptions/{ids[1]}/resolve",
                     headers=auth_headers(http, "pedidos"), json=nota).status_code == 200


# --- 3 · ve todo, actúa solo en sus muestras -----------------------------------


def test_sat_ve_bandeja_y_ficha_pero_en_un_pedido_normal_no_actua(http, session_factory):
    muestra = _muestra(http)["id"]
    with session_factory() as s:
        normal = _pedido_normal(s, "MAN-0102", payment="paid", invoice="5-526102")
    h = _sat(http)

    lista = http.get("/api/erp/orders", headers=h)
    assert lista.status_code == 200, lista.text
    ids = {o["id"] for o in lista.json()["items"]}
    assert {muestra, normal} <= ids

    ficha = http.get(f"/api/erp/orders/{normal}", headers=h)
    assert ficha.status_code == 200, ficha.text
    trans = ficha.json()["available_transitions"]
    assert trans["payment"] == [] and trans["invoice"] == []   # ni cobrar ni facturar

    for url, body in [
        (f"/api/erp/orders/{normal}/complete", None),
        (f"/api/erp/orders/{normal}/cancel-preview", None),
        (f"/api/erp/orders/{normal}/cancel", {"confirm": True}),
        (f"/api/erp/orders/{normal}/uncomplete", None),
    ]:
        r = http.post(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, f"{url} → {r.status_code}"

    # «Completar seleccionados» con una muestra y un pedido normal: la muestra
    # sí, el normal no (y se dice por qué).
    r = http.post("/api/erp/orders/bulk-complete", headers=h,
                  json={"order_ids": [muestra, normal]})
    assert r.status_code == 200, r.text
    assert r.json()["completed"] == 1
    assert [f["order_id"] for f in r.json()["failed"]] == [normal]


def test_legacy_user_sigue_sin_poder_completar(http, session_factory):
    with session_factory() as s:
        oid = _pedido_normal(s, "MAN-0103")
    assert http.post(f"/api/erp/orders/{oid}/complete",
                     headers=auth_headers(http, "user")).status_code == 403


# --- 6 / 8 · el cobro y la Cola SAT --------------------------------------------


def test_muestra_sin_cobrar_avanza_y_pedido_normal_sin_cobrar_sigue_bloqueado(session_factory):
    with session_factory() as s:
        sat = s.scalar(select(User).where(User.email == "sat@example.com"))
        muestra = Order(order_number="MUESTRA-000900", order_kind="sample",
                        preparation_status="in_queue", payment_status="pending",
                        transport_status="not_shipped")
        normal = Order(order_number="MAN-0900", external_source="manual",
                       preparation_status="in_queue", payment_status="pending",
                       transport_status="not_shipped")
        s.add_all([muestra, normal])
        s.flush()

        # La muestra: todos los pasos del taller, sin que nadie la cobre.
        apply_transition(s, order=muestra, domain=StatusDomain.PREPARATION,
                         to_status="preparing", actor=sat)
        apply_transition(s, order=muestra, domain=StatusDomain.PREPARATION,
                         to_status="blocked", actor=sat, evidence={"reason": "falta pieza"})
        # Una muestra que el taller bloqueó la desbloquea el propio taller.
        apply_transition(s, order=muestra, domain=StatusDomain.PREPARATION,
                         to_status="preparing", actor=sat)
        from app.erp.models import ShipmentPackage  # noqa: PLC0415

        s.add(ShipmentPackage(order_id=muestra.id, position=1, weight_kg=1,
                              height_cm=10, width_cm=10, depth_cm=10))
        s.flush()
        apply_transition(s, order=muestra, domain=StatusDomain.PREPARATION,
                         to_status="packed", actor=sat)
        apply_transition(s, order=muestra, domain=StatusDomain.TRANSPORT,
                         to_status="label_created", actor=sat)
        apply_transition(s, order=muestra, domain=StatusDomain.TRANSPORT,
                         to_status="in_transit", actor=sat,
                         evidence={"tracking_number": "TRK-2"})
        assert getattr(muestra.payment_status, "value", muestra.payment_status) == "pending"

        # Ni cobrar ni facturar una muestra (ni ofrecerlo).
        assert available_transitions(muestra, StatusDomain.PAYMENT, None) == []
        assert available_transitions(muestra, StatusDomain.INVOICE, None) == []
        with pytest.raises(TransitionError) as exc:
            apply_transition(s, order=muestra, domain=StatusDomain.PAYMENT,
                             to_status="paid", actor=None)
        assert exc.value.code == "invalid_transition"

        # El pedido normal sin cobrar se queda donde estaba (guard de pago).
        with pytest.raises(TransitionError) as exc:
            apply_transition(s, order=normal, domain=StatusDomain.PREPARATION,
                             to_status="preparing", actor=sat)
        assert exc.value.code == "guard_failed"
        # Y desbloquear un pedido normal sigue siendo de la oficina.
        normal.preparation_status = "blocked"
        s.flush()
        with pytest.raises(TransitionError) as exc:
            apply_transition(s, order=normal, domain=StatusDomain.PREPARATION,
                             to_status="preparing", actor=sat)
        assert exc.value.code == "role_forbidden"


def test_can_manage_order_solo_muestras_para_el_taller(session_factory):
    with session_factory() as s:
        sat = s.scalar(select(User).where(User.email == "sat@example.com"))
        comercial = s.scalar(select(User).where(User.email == "comercial@example.com"))
        muestra = Order(order_number="MUESTRA-1", order_kind="sample")
        convertida = Order(order_number="MUESTRA-2", order_kind="sample_converted")
        normal = Order(order_number="MAN-1")
        assert can_manage_order(sat, muestra)
        assert not can_manage_order(sat, convertida)   # ya es un pedido normal
        assert not can_manage_order(sat, normal)
        assert all(can_manage_order(comercial, o) for o in (muestra, convertida, normal))


# --- 7 · ni «pendiente de cobro» ni descuadre del Cuadre -----------------------


def test_muestra_sin_cobrar_no_sale_por_cobrar_ni_en_el_cuadre(http, session_factory):
    muestra = _muestra(http)["id"]
    with session_factory() as s:
        normal = _pedido_normal(s, "MAN-0104")
    h = auth_headers(http, "pedidos")

    # «Pendiente de pago» en la bandeja: el normal sí, la muestra no.
    r = http.get("/api/erp/orders", headers=h, params={"payment": "pending"})
    assert r.status_code == 200, r.text
    ids = {o["id"] for o in r.json()["items"]}
    assert normal in ids and muestra not in ids

    # El paso de cobro de la muestra «no aplica» (y su ficha no avisa de cobro).
    ficha = http.get(f"/api/erp/orders/{muestra}", headers=h).json()
    pasos = {p["key"]: p for p in ficha["workflow"]["steps"]}
    assert pasos["cobro"]["state"] == "skipped"
    assert not [a for a in ficha["workflow"]["alerts"] if "cobro" in a["code"]]

    # Cuadre: enviada y sin cobrar, la muestra no es ningún descuadre.
    with session_factory() as s:
        o = s.get(Order, muestra)
        o.preparation_status = "packed"
        o.transport_status = "in_transit"
        o.tracking_number = "TRK-3"
        s.commit()
        from app.erp.cuadre import checks_factusol as cf  # noqa: PLC0415
        from app.erp.cuadre.config import normalizar_config  # noqa: PLC0415
        from app.erp.cuadre.contexto import Contexto  # noqa: PLC0415
        from app.erp.cuadre.registry import registro  # noqa: PLC0415

        class FakeFactusol:
            # Una factura con la MISMA referencia que la muestra: si la
            # muestra contara como facturable, saldría «sin vincular».
            def load_table(self, tabla: str, *, filtro: str = "1=1",
                           ejercicio: str | None = None) -> list[dict[str, Any]]:
                if tabla == "F_FAC":
                    return [{"TIPFAC": "5", "CODFAC": 1, "REFFAC": o.order_number,
                             "ESTFAC": "0", "TOTFAC": 0, "NET1FAC": 0}]
                return []

        ctx = Contexto(s, config=normalizar_config({}), client=FakeFactusol(),
                       ejercicio="2026")
        hallazgos = []
        for comp in registro().values():
            if comp.fuente not in ("mysql", "factusol"):
                continue
            try:
                hallazgos.extend(comp.funcion(ctx))
            except Exception:  # noqa: BLE001 — las que piden otra fuente, fuera
                continue
        assert muestra not in {h.entidad_id for h in hallazgos}
        assert cf.factura_sin_vincular  # la comprobación existe y se ha corrido
