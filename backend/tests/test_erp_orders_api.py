"""BoHub ERP Fase A PR 3 — API de pedidos: CRUD, Cola PEDIDOS, transiciones,
timeline y matriz de permisos por rol.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpException,
    ErpSettings,
    ExceptionStatus,
    ExceptionType,
    Order,
)
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users


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
        # D-2: todo pedido necesita cliente — empresa fija para los payloads.
        seed.add(Company(id=SEED_COMPANY_ID, name="Cliente Demo SL"))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


#: Empresa sembrada en el fixture — todo pedido manual necesita cliente (D-2).
SEED_COMPANY_ID = "seed-company-d2"


def _payload(**over) -> dict:
    base = {
        "order_number": "MAN-0001",
        "company_id": SEED_COMPANY_ID,
        "lines": [
            {"product_sku": "SKU-MBO-3050", "product_codart": "MBO3050",
             "description": "MBO 3050", "quantity": 1, "unit_price": 4500},
            {"product_sku": "SKU-ROT", "product_codart": "ROT01",
             "description": "Rotativo", "quantity": 2, "unit_price": 195},
        ],
    }
    base.update(over)
    return base


def _create(client, role="pedidos", **over) -> dict:
    r = client.post("/api/erp/orders", json=_payload(**over),
                    headers=auth_headers(client, role))
    assert r.status_code == 201, r.text
    return r.json()


def _fire(client, oid, domain, to, role="admin", **kw):
    return client.post(
        f"/api/erp/orders/{oid}/transitions",
        json={"domain": domain, "to_status": to, **kw},
        headers=auth_headers(client, role),
    )


# --- crear -------------------------------------------------------------------


def test_pedidos_creates_manual_order_with_computed_total(client):
    body = _create(client)
    assert body["external_source"] == "manual"
    assert body["total_amount"] == pytest.approx(4890.0)
    assert body["preparation_status"] == "pending_review"
    assert len(body["lines"]) == 2
    assert body["blockers"] == []  # todo mapeado, sin empresa


def test_duplicate_order_number_rejected_409(client):
    _create(client)
    r = client.post("/api/erp/orders", json=_payload(),
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409


def test_create_forbidden_for_sat_user_and_viewer(client):
    for role in ("sat", "user", "viewer"):
        r = client.post("/api/erp/orders", json=_payload(order_number=f"X-{role}"),
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


# --- D-2: alta manual desde la UI -------------------------------------------


def test_erp_orders_create_manual_success(client, session_factory):
    """Alta manual completa: número autogenerado, líneas, direcciones en
    packing_json e historial con el evento de creación."""
    body = _create(client, order_number=None, tax_id="B12345678",
                   shipping_address={"address_line": "C Aribau 171",
                                     "city": "Barcelona", "postal_code": "08036"},
                   billing_address={"address_line": "C Aribau 171",
                                    "city": "Barcelona", "postal_code": "08036"})
    assert body["external_source"] == "manual"
    # Número autogenerado con el patrón MANUAL-000001.
    assert body["order_number"].startswith("MANUAL-")
    assert body["order_number"][len("MANUAL-"):].isdigit()
    assert body["total_amount"] == pytest.approx(4890.0)
    assert body["company_name"] == "Cliente Demo SL"
    # Direcciones + NIF guardados sin migración (packing_json).
    packing = body["packing"]
    assert packing["tax_id"] == "B12345678"
    assert packing["shipping_address"]["city"] == "Barcelona"
    # Historial: evento de creación manual con el autor.
    created = [h for h in body["status_history"]
               if (h["metadata"] or {}).get("event") == "order_created_manual"]
    assert len(created) == 1
    assert created[0]["metadata"]["origin_source"] == "manual"
    assert created[0]["changed_by_user_id"]


def test_erp_orders_create_manual_autonumber_increments(client):
    a = _create(client, order_number=None)["order_number"]
    b = _create(client, order_number=None)["order_number"]
    assert a != b
    assert int(b[len("MANUAL-"):]) == int(a[len("MANUAL-"):]) + 1


def test_erp_orders_create_manual_requires_customer(client):
    payload = _payload(order_number="NO-CUST")
    payload.pop("company_id", None)
    r = client.post("/api/erp/orders", json=payload,
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 422


def test_erp_orders_create_manual_requires_lines(client):
    r = client.post("/api/erp/orders", json=_payload(order_number="NO-LINES", lines=[]),
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 422


def test_line_without_sku_accepted_if_description_present(client):
    """C-4: el SKU es opcional — servicios, reparaciones y muestras no lo tienen."""
    body = _create(client, order_number="SIN-SKU", lines=[
        {"product_sku": "", "description": "Reparación láser (mano de obra)",
         "quantity": 2, "unit_price": 45},
    ])
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["product_sku"] == ""
    assert line["description"] == "Reparación láser (mano de obra)"
    assert body["total_amount"] == pytest.approx(90.0)


def test_line_without_sku_nor_description_rejected(client):
    """Una línea sin nada que la identifique no es preparable ni facturable."""
    r = client.post("/api/erp/orders", json=_payload(
        order_number="LINEA-VACIA",
        lines=[{"product_sku": "", "description": "", "quantity": 1,
                "unit_price": 10}],
    ), headers=auth_headers(client, "pedidos"))
    assert r.status_code == 422


def test_line_with_sku_and_no_description_still_works(client):
    """Retrocompatibilidad: las líneas de Woo llegan con SKU y sin descripción."""
    body = _create(client, order_number="SOLO-SKU", lines=[
        {"product_sku": "SKU-X", "quantity": 1, "unit_price": 10},
    ])
    # La descripción cae al SKU cuando no viene (comportamiento previo).
    assert body["lines"][0]["description"] == "SKU-X"


def test_erp_orders_list_returns_customer_name(client, session_factory):
    """La bandeja devuelve contact_name/company_name para pintar el cliente."""
    from app.models.crm import Contact  # noqa: PLC0415

    with session_factory() as s:
        c = Contact(first_name="Ana", last_name="Pi", email="ana@example.com")
        s.add(c)
        s.commit()
        cid = c.id
    _create(client, order_number="CLI-EMPRESA")
    _create(client, order_number="CLI-CONTACTO", contact_id=cid)

    r = client.get("/api/erp/orders", headers=auth_headers(client, "pedidos"))
    by_number = {o["order_number"]: o for o in r.json()["items"]}
    assert by_number["CLI-EMPRESA"]["company_name"] == "Cliente Demo SL"
    assert by_number["CLI-CONTACTO"]["contact_name"] == "Ana Pi"


# --- bandeja + permisos de vista --------------------------------------------


def test_list_filters_by_preparation_status(client):
    _create(client)
    _create(client, order_number="MAN-0002")
    r = client.get("/api/erp/orders?preparation=pending_review",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
    r2 = client.get("/api/erp/orders?preparation=packed",
                    headers=auth_headers(client, "user"))
    assert r2.json()["items"] == []


def test_viewer_cannot_access_erp(client):
    assert client.get("/api/erp/orders",
                      headers=auth_headers(client, "viewer")).status_code == 403


def test_sat_and_user_can_view_detail(client):
    oid = _create(client)["id"]
    for role in ("sat", "user", "manager"):
        r = client.get(f"/api/erp/orders/{oid}", headers=auth_headers(client, role))
        assert r.status_code == 200, role


# --- Cola PEDIDOS: bloqueos + aprobación ------------------------------------


def test_pending_approval_only_real_exceptions_block(client, session_factory):
    """B-2-fix5: el ERP confía en la fuente. Un SKU sin mapear o una empresa
    sin vincular a FACTUSOL NO generan bloqueos ni warnings. Solo una
    excepción operativa abierta (SAT/transporte/facturación) bloquea."""
    with session_factory() as s:
        company = Company(name="Sin Factusol SL")  # sin factusol_company_id
        s.add(company)
        s.commit()
        cid = company.id
    body = _create(client, order_number="MAN-B1", company_id=cid, lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=body["id"]))
        s.commit()
    r = client.get("/api/erp/orders/pending-approval",
                   headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["id"] == body["id"])
    # Solo la excepción SAT real bloquea; ni sku_unmapped ni company_missing.
    assert {b["code"] for b in item["blockers"]} == {"open_exceptions"}
    assert item["warnings"] == []


def test_pending_approval_no_blockers_without_real_exceptions(
    client, session_factory
):
    """SKU sin mapear + empresa sin FACTUSOL pero SIN excepciones operativas
    → sin bloqueos ni warnings (aprobable). El ERP confía en la fuente."""
    with session_factory() as s:
        company = Company(name="Sin Factusol SL")
        s.add(company)
        s.commit()
        cid = company.id
    body = _create(client, order_number="MAN-B1C", company_id=cid, lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.get("/api/erp/orders/pending-approval",
                   headers=auth_headers(client, "pedidos"))
    item = next(i for i in r.json()["items"] if i["id"] == body["id"])
    assert item["blockers"] == []
    assert item["warnings"] == []


def test_approve_unmapped_sku_never_blocks(client):
    """B-2-fix5: un SKU sin mapear ya no impide aprobar en ningún caso."""
    body = _create(client, order_number="MAN-B2", lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])
    r = client.post(f"/api/erp/orders/{body['id']}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["preparation_status"] == "in_queue"


def test_approve_blocked_by_open_operational_exception(client, session_factory):
    """Una excepción operativa abierta (SAT) sí devuelve 409 al aprobar."""
    body = _create(client, order_number="MAN-B2X")
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=body["id"]))
        s.commit()
    r = client.post(f"/api/erp/orders/{body['id']}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 409
    assert any(
        b["code"] == "open_exceptions" for b in r.json()["detail"]["blockers"]
    )


def test_approve_clean_order_sets_approved_and_moves_to_queue(
    client, session_factory
):
    body = _create(client)
    r = client.post(f"/api/erp/orders/{body['id']}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["preparation_status"] == "in_queue"
    assert r.json()["approved_at"] is not None
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == body["id"]))
        assert o.approved_by_user_id is not None


def test_approve_forbidden_for_sat_manager_and_user(client):
    oid = _create(client)["id"]
    for role in ("sat", "manager", "user"):
        r = client.post(f"/api/erp/orders/{oid}/approve",
                        headers=auth_headers(client, role))
        assert r.status_code == 403, role


# --- transiciones vía API ----------------------------------------------------


def test_transition_endpoint_maps_engine_errors_to_http(client):
    oid = _create(client)["id"]
    # guard_failed → 409 (empezar preparación sin pago, tras aprobar).
    client.post(f"/api/erp/orders/{oid}/approve",
                headers=auth_headers(client, "pedidos"))
    r_guard = _fire(client, oid, "preparation", "preparing", role="sat")
    assert r_guard.status_code == 409
    assert r_guard.json()["detail"]["code"] == "guard_failed"
    # role_forbidden → 403 (user intenta cobrar... user no está en el arco).
    r_role = _fire(client, oid, "payment", "paid", role="user")
    assert r_role.status_code == 403
    # evidencia faltante → 422 (reembolso sin motivo).
    _fire(client, oid, "payment", "paid", role="manager")
    r_ev = _fire(client, oid, "payment", "refunded", role="admin")
    assert r_ev.status_code == 422
    # dominio inválido → 400.
    assert _fire(client, oid, "nope", "x").status_code == 400
    # arco inexistente → 409.
    r_arc = _fire(client, oid, "transport", "delivered", role="admin")
    assert r_arc.status_code == 409
    assert r_arc.json()["detail"]["code"] == "invalid_transition"


def test_full_flow_via_api_and_available_transitions(client):
    oid = _create(client)["id"]
    _fire(client, oid, "payment", "paid", role="manager")
    client.post(f"/api/erp/orders/{oid}/approve",
                headers=auth_headers(client, "pedidos"))
    _fire(client, oid, "preparation", "preparing", role="sat")
    detail = client.get(f"/api/erp/orders/{oid}",
                        headers=auth_headers(client, "sat")).json()
    # SAT desde preparing ve packed y blocked como siguientes pasos.
    prep_next = {t["to_status"] for t in detail["available_transitions"]["preparation"]}
    assert prep_next == {"packed", "blocked"}
    # Fase D: embalar exige ≥1 bulto medido.
    client.post(f"/api/erp/orders/{oid}/packages",
                json=[{"weight_kg": 2, "height_cm": 10, "width_cm": 10,
                       "depth_cm": 10}],
                headers=auth_headers(client, "sat"))
    r = _fire(client, oid, "preparation", "packed", role="sat")
    assert r.status_code == 200
    assert r.json()["preparation_status"] == "packed"
    # El historial acumula el alta manual (D-2) + las 4 transiciones.
    assert len(r.json()["status_history"]) == 5


# --- timeline ----------------------------------------------------------------


def test_timeline_unifies_status_and_exceptions_sorted_desc(
    client, session_factory
):
    oid = _create(client)["id"]
    _fire(client, oid, "payment", "paid", role="manager")
    with session_factory() as s:
        s.add(ErpException(
            type=ExceptionType.STOCK_SHORTAGE, subtype="eta_set", order_id=oid,
            metadata_json='{"eta_date": "2026-08-15"}',
        ))
        s.commit()
    r = client.get(f"/api/erp/orders/{oid}/timeline",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 200
    body = r.json()
    types = [e["type"] for e in body["items"]]
    assert "status" in types and "exception" in types
    ats = [e["at"] for e in body["items"]]
    assert ats == sorted(ats, reverse=True)  # descendente
    # Filtro por tipo.
    only_exc = client.get(
        f"/api/erp/orders/{oid}/timeline?types=exception",
        headers=auth_headers(client, "user"),
    ).json()
    assert {e["type"] for e in only_exc["items"]} == {"exception"}


def test_timeline_404_for_missing_order(client):
    r = client.get("/api/erp/orders/nope/timeline",
                   headers=auth_headers(client, "user"))
    assert r.status_code == 404


# --- procesado externamente (B-2-fix4) --------------------------------------


def test_mark_externally_processed_sets_terminal_states_and_stamp(
    client, session_factory
):
    body = _create(client, order_number="MAN-EXT1")
    r = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={"note": "Gestionado en el Excel de siempre"},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["preparation_status"] == "already_completed_externally"
    assert detail["transport_status"] == "already_shipped_externally"
    assert detail["invoice_status"] == "already_invoiced_externally"
    assert detail["payment_status"] == "paid"  # pending → paid al externalizar
    assert detail["externally_processed_at"] is not None
    assert detail["externally_processed_note"] == "Gestionado en el Excel de siempre"
    assert detail["externally_processed_by_user_id"] is not None
    # Historial: una fila por cada dominio cambiado (pago + 3 estados).
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == body["id"]))
        assert o.externally_processed_at is not None


def test_mark_externally_processed_is_idempotent(client):
    body = _create(client, order_number="MAN-EXT2")
    first = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    ).json()
    second = client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    assert second.status_code == 200
    # El timestamp no se re-estampa (no-op en la 2ª llamada).
    assert second.json()["externally_processed_at"] == first["externally_processed_at"]


def test_mark_externally_processed_forbidden_for_viewer_roles(client):
    oid = _create(client, order_number="MAN-EXT3")["id"]
    for role in ("sat", "user", "viewer"):
        r = client.post(
            f"/api/erp/orders/{oid}/mark-externally-processed",
            json={}, headers=auth_headers(client, role),
        )
        assert r.status_code == 403, role


def test_bulk_mark_externally_processed_by_ids(client):
    a = _create(client, order_number="MAN-BULK1")["id"]
    b = _create(client, order_number="MAN-BULK2")["id"]
    r = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={"order_ids": [a, b], "note": "Migración inicial"},
        headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "marked": 2}
    # Re-run: ya externalizados → marked 0 (idempotente).
    again = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={"order_ids": [a, b]},
        headers=auth_headers(client, "pedidos"),
    )
    assert again.json()["marked"] == 0


def test_bulk_mark_requires_ids_or_store(client):
    r = client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    assert r.status_code == 400


def test_list_hides_externalized_by_default_and_shows_with_flag(client):
    oid = _create(client, order_number="MAN-EXT-HIDE")["id"]
    client.post(
        f"/api/erp/orders/{oid}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    # Por defecto la bandeja no lo muestra.
    default = client.get("/api/erp/orders",
                         headers=auth_headers(client, "user")).json()
    assert oid not in {o["id"] for o in default["items"]}
    # Con show_external=true sí aparece.
    shown = client.get("/api/erp/orders?show_external=true",
                       headers=auth_headers(client, "user")).json()
    assert oid in {o["id"] for o in shown["items"]}


_AUTO_RESOLVE_NOTE = "Auto-resuelta: pedido marcado como procesado externamente"


def test_mark_externally_processed_auto_resolves_open_exceptions(
    client, session_factory
):
    """B-2-fix5: al externalizar, las excepciones abiertas del pedido se
    auto-resuelven con la nota estándar (no quedan huérfanas)."""
    body = _create(client, order_number="MAN-AR1")
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=body["id"]))
        s.add(ErpException(type=ExceptionType.CARRIER_INCIDENT, order_id=body["id"]))
        s.commit()
    client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    with session_factory() as s:
        excs = list(s.scalars(
            select(ErpException).where(ErpException.order_id == body["id"])
        ))
        assert len(excs) == 2
        assert all(e.status == ExceptionStatus.RESOLVED for e in excs)
        assert all(e.resolved_at is not None for e in excs)
        assert all(e.resolution_note == _AUTO_RESOLVE_NOTE for e in excs)


def test_bulk_mark_externally_processed_auto_resolves_exceptions(
    client, session_factory
):
    a = _create(client, order_number="MAN-AR2")["id"]
    b = _create(client, order_number="MAN-AR3")["id"]
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=a))
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=b))
        s.commit()
    client.post(
        "/api/erp/orders/bulk-mark-externally-processed",
        json={"order_ids": [a, b]}, headers=auth_headers(client, "pedidos"),
    )
    with session_factory() as s:
        excs = list(s.scalars(
            select(ErpException).where(ErpException.order_id.in_([a, b]))
        ))
        assert len(excs) == 2
        assert all(e.status == ExceptionStatus.RESOLVED for e in excs)
        assert all(e.resolution_note == _AUTO_RESOLVE_NOTE for e in excs)


def test_mark_externally_processed_leaves_dismissed_exceptions_untouched(
    client, session_factory
):
    """Solo se auto-resuelven las ABIERTAS; una descartada no se toca."""
    body = _create(client, order_number="MAN-AR4")
    with session_factory() as s:
        s.add(ErpException(
            type=ExceptionType.SAT_ISSUE, order_id=body["id"],
            status=ExceptionStatus.DISMISSED,
        ))
        s.commit()
    client.post(
        f"/api/erp/orders/{body['id']}/mark-externally-processed",
        json={}, headers=auth_headers(client, "pedidos"),
    )
    with session_factory() as s:
        exc = s.scalar(select(ErpException).where(ErpException.order_id == body["id"]))
        assert exc.status == ExceptionStatus.DISMISSED
        assert exc.resolution_note is None


# --- Fase C · C-2-fix3: factusol_live NO reactiva bloqueos SKU/empresa -------


def _set_factusol_live(session_factory, live: bool) -> None:
    with session_factory() as s:
        cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
        if cfg is None:
            cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
            s.add(cfg)
        cfg.factusol_live = live
        s.commit()


def test_factusol_live_does_not_add_sku_or_company_blockers(client, session_factory):
    """C-2-fix3: aunque `factusol_live` esté activo (necesario para el sync en
    vivo de factura), un SKU sin mapear + una empresa sin `factusol_company_id`
    ya NO generan bloqueos ni warnings. El ERP confía en la fuente; solo una
    excepción operativa abierta bloquea."""
    with session_factory() as s:
        company = Company(name="Sin Factusol SL")  # sin factusol_company_id
        s.add(company)
        s.commit()
        cid = company.id
    body = _create(client, order_number="MAN-FL1", company_id=cid, lines=[
        {"product_sku": "SKU-NUEVO", "description": "Sin mapear",
         "quantity": 1, "unit_price": 100},
    ])

    _set_factusol_live(session_factory, True)
    r = client.get(f"/api/erp/orders/{body['id']}",
                   headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200
    detail = r.json()
    assert detail["blockers"] == []
    assert detail["warnings"] == []
    # Y la aprobación NO se rechaza por SKU/empresa.
    ap = client.post(f"/api/erp/orders/{body['id']}/approve",
                     headers=auth_headers(client, "pedidos"))
    assert ap.status_code == 200, ap.text


def test_factusol_live_real_exception_still_blocks(client, session_factory):
    """Con factusol_live ON, una excepción operativa abierta (SAT) sigue siendo
    el ÚNICO bloqueo real de la Cola PEDIDOS."""
    body = _create(client, order_number="MAN-FL2")
    with session_factory() as s:
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=body["id"]))
        s.commit()
    _set_factusol_live(session_factory, True)
    r = client.get(f"/api/erp/orders/{body['id']}",
                   headers=auth_headers(client, "pedidos"))
    assert {b["code"] for b in r.json()["blockers"]} == {"open_exceptions"}
    assert r.json()["warnings"] == []
