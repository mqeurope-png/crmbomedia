"""BoHub ERP Fase A PR 5 — Cola SAT: queue, report-exception, packing-info,
attach-document (DocumentStorage local) + selección de backend de storage.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import (
    ErpException,
    ExceptionStatus,
    ExceptionType,
    Order,
    OrderLine,
    OrderStatusHistory,
)
from app.erp.storage import (
    HiDriveDocumentStorage,
    LocalDocumentStorage,
    get_document_storage,
)
from app.main import app
from app.models.crm import AuditLog
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


def _mk_order(s: Session, *, number="MAN-0001", prep="in_queue",
              transport="not_shipped") -> str:
    o = Order(order_number=number, preparation_status=prep, payment_status="paid",
              transport_status=transport)
    s.add(o)
    s.flush()
    s.add(OrderLine(order_id=o.id, product_sku="SKU-A", product_codart="A1",
                    description="Art A", quantity=1, unit_price=10, line_total=10))
    s.commit()
    return o.id


# --- cola SAT ----------------------------------------------------------------


def test_sat_queue_priorities_blocked_then_preparing_then_in_queue(
    client, session_factory
):
    with session_factory() as s:
        _mk_order(s, number="Q-INQUEUE", prep="in_queue")
        _mk_order(s, number="Q-PREP", prep="preparing")
        _mk_order(s, number="Q-BLOCKED", prep="blocked")
        _mk_order(s, number="Q-PACKED", prep="packed")  # va a «listos para envío»
    r = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat"))
    assert r.status_code == 200
    nums = [i["order_number"] for i in r.json()["preparing"]]
    assert nums == ["Q-BLOCKED", "Q-PREP", "Q-INQUEUE"]  # packed no está en «por embalar»


def test_erp_sat_queue_returns_customer_name(client, session_factory):
    """D-2: las cards del taller muestran el cliente, no solo el número."""
    from app.models.crm import Company, Contact  # noqa: PLC0415

    with session_factory() as s:
        comp = Company(name="Duplicoder SL")
        cont = Contact(first_name="Ana", last_name="Pi", email="ana@example.com")
        s.add_all([comp, cont])
        s.commit()
        comp_id, cont_id = comp.id, cont.id
        o1 = Order(order_number="Q-EMPRESA", preparation_status="preparing",
                   payment_status="paid", company_id=comp_id)
        o2 = Order(order_number="Q-CONTACTO", preparation_status="packed",
                   payment_status="paid", contact_id=cont_id)
        s.add_all([o1, o2])
        s.commit()
    body = client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).json()
    prep = {i["order_number"]: i for i in body["preparing"]}
    ready = {i["order_number"]: i for i in body["ready_for_pickup"]}
    assert prep["Q-EMPRESA"]["company_name"] == "Duplicoder SL"
    assert ready["Q-CONTACTO"]["contact_name"] == "Ana Pi"


def test_sat_queue_returns_two_sections(client, session_factory):
    """D-1-fix1: preparing (por embalar) + ready_for_pickup (packed sin salir)."""
    with session_factory() as s:
        _mk_order(s, number="PREP-1", prep="preparing")
        _mk_order(s, number="READY-1", prep="packed")            # not_shipped → listo
        _mk_order(s, number="GONE-1", prep="packed", transport="in_transit")  # ya salió
    r = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat"))
    body = r.json()
    assert [i["order_number"] for i in body["preparing"]] == ["PREP-1"]
    assert [i["order_number"] for i in body["ready_for_pickup"]] == ["READY-1"]
    # El item de listos lleva el estado de transporte para decidir «recogido».
    assert body["ready_for_pickup"][0]["transport_status"] == "not_shipped"


def test_sat_queue_visible_to_sat_role(client, session_factory):
    with session_factory() as s:
        _mk_order(s)
    assert client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "sat")).status_code == 200
    assert client.get("/api/erp/sat/queue",
                      headers=auth_headers(client, "viewer")).status_code == 403


def test_sat_queue_item_lleva_observaciones_y_datos_tecnicos(client, session_factory):
    """Lote 2 · PR-2: la cola expone lo que el taller lee de pie — las
    observaciones del comercial, el nº de serie, la licencia WhiteRIP y el
    origen del envío (campos de seguimiento de la ficha, solo lectura aquí).
    Vacío o solo espacios llega como None; nunca se inventa."""
    with session_factory() as s:
        s.add_all([
            Order(order_number="T-FULL", preparation_status="preparing", payment_status="paid",
                  notes="  Cliente pide embalaje reforzado y manual en alemán.  ",
                  serial_number="FLX-7741-2026", whiterip_license="WR-4C-88231",
                  shipping_origin="SAT", tracking_number="1Z999-TRACK"),
            Order(order_number="T-EMPTY", preparation_status="packed", payment_status="paid",
                  notes="   ", serial_number=None, whiterip_license="", shipping_origin=None),
        ])
        s.commit()
    body = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat")).json()
    full = next(i for i in body["preparing"] if i["order_number"] == "T-FULL")
    assert full["notes"] == "Cliente pide embalaje reforzado y manual en alemán."
    assert full["serial_number"] == "FLX-7741-2026"
    assert full["whiterip_license"] == "WR-4C-88231"
    assert full["shipping_origin"] == "SAT"
    # Lote 5 · #3 — nº de seguimiento en el item (precarga la casilla de tracking).
    assert full["tracking_number"] == "1Z999-TRACK"
    empty = next(i for i in body["ready_for_pickup"] if i["order_number"] == "T-EMPTY")
    assert empty["notes"] is None
    assert empty["serial_number"] is None
    assert empty["whiterip_license"] is None
    assert empty["shipping_origin"] is None
    assert empty["tracking_number"] is None
    # Las claves están siempre (contrato estable para la card).
    for key in ("notes", "serial_number", "whiterip_license", "shipping_origin",
                "tracking_number"):
        assert key in empty


# --- Lote B6: filtros de la cola ---------------------------------------------


def _mk_store(s: Session, slug: str) -> str:
    from app.models.crm import ExternalSystem  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    acc = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug.title(),
    )
    s.add(acc)
    s.commit()
    return acc.id


def _nums(client: TestClient, params: dict) -> tuple[list[str], list[str]]:
    r = client.get("/api/erp/sat/queue", params=params, headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    body = r.json()
    return (
        [i["order_number"] for i in body["preparing"]],
        [i["order_number"] for i in body["ready_for_pickup"]],
    )


def test_sat_queue_filters_fecha_tienda_estado_y_texto(client, session_factory):
    """Los filtros se aplican a las DOS secciones y no rompen el orden de
    prioridad (bloqueados → preparando → en cola)."""
    from app.models.crm import Company  # noqa: PLC0415

    with session_factory() as s:
        art, bop = _mk_store(s, "artisjet"), _mk_store(s, "boprint")
        comp = Company(name="Duplicoder SL")
        s.add(comp)
        s.commit()
        s.add_all([
            Order(order_number="ART-1", preparation_status="in_queue", payment_status="paid",
                  store_id=art, company_id=comp.id,
                  placed_at=datetime(2026, 9, 1, 10, tzinfo=UTC)),
            Order(order_number="BOP-2", preparation_status="blocked", payment_status="paid",
                  store_id=bop, placed_at=datetime(2026, 9, 5, 10, tzinfo=UTC)),
            Order(order_number="BOP-3", preparation_status="packed", payment_status="paid",
                  store_id=bop, placed_at=datetime(2026, 9, 10, 10, tzinfo=UTC)),
        ])
        s.commit()

    assert _nums(client, {}) == (["BOP-2", "ART-1"], ["BOP-3"])
    assert _nums(client, {"store_slug": "BoPrint"}) == (["BOP-2"], ["BOP-3"])
    assert _nums(client, {"desde": "2026-09-05", "hasta": "2026-09-05"}) == (["BOP-2"], [])
    assert _nums(client, {"hasta": "2026-09-04"}) == (["ART-1"], [])
    assert _nums(client, {"desde": "2026-09-06"}) == ([], ["BOP-3"])
    assert _nums(client, {"estado": "blocked"}) == (["BOP-2"], [])
    assert _nums(client, {"estado": "in_queue"}) == (["ART-1"], [])
    assert _nums(client, {"estado": "por_embalar"}) == (["BOP-2", "ART-1"], [])
    assert _nums(client, {"estado": "ready"}) == ([], ["BOP-3"])
    assert _nums(client, {"estado": "packed"}) == ([], ["BOP-3"])
    # Texto: nº de pedido o cliente (empresa), sin distinguir mayúsculas.
    assert _nums(client, {"q": "duplic"}) == (["ART-1"], [])
    assert _nums(client, {"q": "bop-"}) == (["BOP-2"], ["BOP-3"])
    assert _nums(client, {"q": "nada-que-ver"}) == ([], [])
    # Estado desconocido → 422 (no se ignora en silencio).
    r = client.get("/api/erp/sat/queue", params={"estado": "loquesea"},
                   headers=auth_headers(client, "sat"))
    assert r.status_code == 422
    # El item lleva tienda y fecha para la vista lista.
    body = client.get("/api/erp/sat/queue", headers=auth_headers(client, "sat")).json()
    art_item = next(i for i in body["preparing"] if i["order_number"] == "ART-1")
    assert art_item["store_slug"] == "artisjet"
    assert art_item["placed_at"].startswith("2026-09-01")


def test_sat_queue_orden_por_fecha_recientes_primero(client, session_factory):
    """C4: dentro de cada grupo la cola ordena por FECHA del pedido; por
    defecto los más recientes primero, y `sort=fecha_asc` los invierte. La
    prioridad por estado (bloqueado antes que en cola) sigue mandando."""
    with session_factory() as s:
        s.add_all([
            Order(order_number="IQ-VIEJO", preparation_status="in_queue",
                  payment_status="paid", placed_at=datetime(2026, 9, 1, 10, tzinfo=UTC)),
            Order(order_number="IQ-NUEVO", preparation_status="in_queue",
                  payment_status="paid", placed_at=datetime(2026, 9, 20, 10, tzinfo=UTC)),
            Order(order_number="BLOQ", preparation_status="blocked",
                  payment_status="paid", placed_at=datetime(2026, 9, 10, 10, tzinfo=UTC)),
            Order(order_number="RD-VIEJO", preparation_status="packed",
                  payment_status="paid", placed_at=datetime(2026, 9, 2, 10, tzinfo=UTC)),
            Order(order_number="RD-NUEVO", preparation_status="packed",
                  payment_status="paid", placed_at=datetime(2026, 9, 15, 10, tzinfo=UTC)),
        ])
        s.commit()
    # Por defecto: recientes primero dentro del grupo (bloqueado sigue el 1º).
    prep, ready = _nums(client, {})
    assert prep == ["BLOQ", "IQ-NUEVO", "IQ-VIEJO"]
    assert ready == ["RD-NUEVO", "RD-VIEJO"]
    # `fecha_asc`: los más antiguos primero (FIFO), sin tocar la prioridad.
    prep_asc, ready_asc = _nums(client, {"sort": "fecha_asc"})
    assert prep_asc == ["BLOQ", "IQ-VIEJO", "IQ-NUEVO"]
    assert ready_asc == ["RD-VIEJO", "RD-NUEVO"]


# --- Lote B6: historial de enviados al taller --------------------------------


def test_sat_history_une_emails_y_aprobaciones(client, session_factory):
    from app.core.audit import record_event  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    with session_factory() as s:
        pedidos = s.query(User).filter_by(email="pedidos@example.com").one()
        o_mail = Order(order_number="H-MAIL", preparation_status="in_queue",
                       payment_status="paid", factusol_albaran_number="5-500001")
        o_appr = Order(order_number="H-APPR", preparation_status="pending_review",
                       payment_status="paid")
        s.add_all([o_mail, o_appr])
        s.commit()
        record_event(
            s, action="erp.order_emailed", target_type="order", target_id=o_mail.id,
            actor=pedidos, message="Pedido H-MAIL enviado por email",
            metadata={"to": ["taller@bomedia.net"], "cc": [], "subject": "Pedido H-MAIL",
                      "attachment_kinds": ["albaran"]},
        )
        s.commit()
        appr_id = o_appr.id

    r = client.post(f"/api/erp/orders/{appr_id}/approve",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text

    r = client.get("/api/erp/sat/history", headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert {(i["order_number"], i["kind"]) for i in items} == {
        ("H-MAIL", "email_sat"), ("H-APPR", "aprobado"),
    }
    # Más reciente primero: la aprobación ocurrió después del email.
    assert items[0]["order_number"] == "H-APPR"
    mail = next(i for i in items if i["kind"] == "email_sat")
    assert mail["to"] == ["taller@bomedia.net"]
    assert mail["subject"] == "Pedido H-MAIL"
    assert mail["actor_name"] == "Pedidos User"
    assert mail["factusol_albaran_number"] == "5-500001"
    assert mail["preparation_status"] == "in_queue"
    appr = next(i for i in items if i["kind"] == "aprobado")
    assert appr["reason"] == "aprobado en Cola PEDIDOS"
    assert appr["from_status"] == "pending_review"
    assert appr["actor_name"] == "Pedidos User"
    assert appr["preparation_status"] == "in_queue"

    # Filtros (mismos que la cola) y límite.
    r = client.get("/api/erp/sat/history", params={"q": "h-mail"},
                   headers=auth_headers(client, "sat"))
    assert [i["order_number"] for i in r.json()["items"]] == ["H-MAIL"]
    r = client.get("/api/erp/sat/history", params={"limit": 1},
                   headers=auth_headers(client, "sat"))
    assert len(r.json()["items"]) == 1
    r = client.get("/api/erp/sat/history", params={"desde": "2030-01-01"},
                   headers=auth_headers(client, "sat"))
    assert r.json()["items"] == []
    assert client.get("/api/erp/sat/history",
                      headers=auth_headers(client, "viewer")).status_code == 403


# --- Lote B6: añadir a mano ---------------------------------------------------


def test_sat_find_order_by_number(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, number="BOP-777", prep="pending_review")
    r = client.get("/api/erp/sat/find-order", params={"number": " bop-777 "},
                   headers=auth_headers(client, "sat"))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == oid
    assert r.json()["already_queued"] is False
    assert r.json()["cancelled"] is False and r.json()["excluded"] is False
    r = client.get("/api/erp/sat/find-order", params={"number": "NOPE-1"},
                   headers=auth_headers(client, "sat"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "order_not_found"


def test_sat_enqueue_pending_review_aprueba_y_es_idempotente(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, number="ENQ-PR", prep="pending_review")
    r = client.post(f"/api/erp/orders/{oid}/sat-enqueue",
                    headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preparation_status"] == "in_queue"
    assert body["approved"] is True and body["via"] == "approve"
    assert body["already_queued"] is False
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.preparation_status == "in_queue"
        assert o.approved_at is not None and o.approved_by_user_id is not None
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == oid,
        )).all()
        assert [h.to_status for h in hist] == ["in_queue"]
        assert hist[0].reason == "añadido a mano a la Cola SAT"
    # Idempotente: ya en cola.
    r2 = client.post(f"/api/erp/orders/{oid}/sat-enqueue",
                     headers=auth_headers(client, "pedidos"))
    assert r2.status_code == 200
    assert r2.json()["already_queued"] is True
    # Y aparece en «Por embalar».
    assert "ENQ-PR" in _nums(client, {})[0]
    # El historial lo cuenta como enviado al taller.
    hist = client.get("/api/erp/sat/history", headers=auth_headers(client, "sat")).json()
    assert [(i["order_number"], i["kind"], i["reason"]) for i in hist["items"]] == [
        ("ENQ-PR", "aprobado", "añadido a mano a la Cola SAT"),
    ]


def test_sat_enqueue_packed_y_externalizado_vuelven_a_la_cola(client, session_factory):
    """packed → in_queue por el arco «Reabrir» si el rol puede (admin); si el
    arco no existe (externalizado) o es de otro rol (pedidos) se fuerza el
    estado dejando historial + auditoría igualmente."""
    with session_factory() as s:
        a = _mk_order(s, number="ENQ-PK-ADMIN", prep="packed")
        p = _mk_order(s, number="ENQ-PK-PEDIDOS", prep="packed")
        x = _mk_order(s, number="ENQ-EXT", prep="already_completed_externally")
    r = client.post(f"/api/erp/orders/{a}/sat-enqueue", headers=auth_headers(client, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["via"] == "transition" and r.json()["preparation_status"] == "in_queue"
    r = client.post(f"/api/erp/orders/{p}/sat-enqueue", headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["via"] == "direct" and r.json()["preparation_status"] == "in_queue"
    r = client.post(f"/api/erp/orders/{x}/sat-enqueue", headers=auth_headers(client, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["via"] == "direct" and r.json()["preparation_status"] == "in_queue"
    with session_factory() as s:
        for oid, from_status in ((a, "packed"), (p, "packed"),
                                 (x, "already_completed_externally")):
            o = s.get(Order, oid)
            assert o.preparation_status == "in_queue"
            assert o.approved_at is None  # no era una aprobación
            h = s.scalars(select(OrderStatusHistory).where(
                OrderStatusHistory.order_id == oid,
            )).one()
            assert (h.from_status, h.to_status) == (from_status, "in_queue")
            assert h.reason == "añadido a mano a la Cola SAT"
        audits = s.scalars(select(AuditLog).where(
            AuditLog.action == "erp.order_status_changed",
        )).all()
        assert sorted(json.loads(x_.metadata_json)["order_number"] for x_ in audits) == [
            "ENQ-EXT", "ENQ-PK-ADMIN", "ENQ-PK-PEDIDOS",
        ]
    assert sorted(_nums(client, {})[0]) == ["ENQ-EXT", "ENQ-PK-ADMIN", "ENQ-PK-PEDIDOS"]


def test_sat_enqueue_rechaza_anulado_quitado_bloqueado_y_roles(client, session_factory):
    with session_factory() as s:
        c = _mk_order(s, number="ENQ-CANC", prep="pending_review")
        e = _mk_order(s, number="ENQ-EXCL", prep="pending_review")
        b = _mk_order(s, number="ENQ-BLK", prep="pending_review")
        now = datetime.now(UTC)
        s.get(Order, c).cancelled_at = now
        s.get(Order, e).seguimiento_excluded_at = now
        s.add(ErpException(type=ExceptionType.SAT_ISSUE, order_id=b))
        s.commit()
    h = auth_headers(client, "pedidos")
    r = client.post(f"/api/erp/orders/{c}/sat-enqueue", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cancelled"
    r = client.post(f"/api/erp/orders/{e}/sat-enqueue", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "excluded"
    r = client.post(f"/api/erp/orders/{b}/sat-enqueue", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "blocked"
    assert r.json()["detail"]["blockers"][0]["code"] == "open_exceptions"
    # SAT (solo lectura del ERP) no añade; inexistente → 404.
    assert client.post(f"/api/erp/orders/{b}/sat-enqueue",
                       headers=auth_headers(client, "sat")).status_code == 403
    assert client.post("/api/erp/orders/no-existe/sat-enqueue", headers=h).status_code == 404
    with session_factory() as s:
        for oid in (c, e, b):
            assert s.get(Order, oid).preparation_status == "pending_review"


# --- reportar excepción ------------------------------------------------------


def test_report_exception_creates_row_and_blocks_preparation(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s, prep="preparing")
    r = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "sat_issue", "description": "falta un tornillo"},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["preparation_status"] == "blocked"
    with session_factory() as s:
        exc = s.scalar(select(ErpException).where(ErpException.order_id == oid))
        assert exc.type.value == "sat_issue"
        assert exc.status == ExceptionStatus.OPEN
        assert json.loads(exc.metadata_json)["description"] == "falta un tornillo"
        o = s.scalar(select(Order).where(Order.id == oid))
        assert o.preparation_status == "blocked"


def test_report_exception_validates_type_and_subtype(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    bad_type = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "no_existe"}, headers=auth_headers(client, "sat"),
    )
    assert bad_type.status_code == 400
    bad_sub = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "stock_shortage", "subtype": "inventado"},
        headers=auth_headers(client, "sat"),
    )
    assert bad_sub.status_code == 400
    ok = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "stock_shortage", "subtype": "eta_set",
              "metadata": {"eta_date": "2026-08-20", "provider": "MBO"}},
        headers=auth_headers(client, "sat"),
    )
    assert ok.status_code == 201


def test_report_exception_on_packed_order_records_without_transition(
    client, session_factory
):
    with session_factory() as s:
        oid = _mk_order(s, prep="packed")
    r = client.post(
        f"/api/erp/orders/{oid}/report-exception",
        json={"type": "material_defective"}, headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201
    assert r.json()["preparation_status"] == "packed"  # no fuerza transición inválida


# --- packing info ------------------------------------------------------------


def test_packing_info_stored_in_packing_json(client, session_factory):
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/packing-info",
        json={"weight_kg": 12.5, "dimensions_cm": "60x40x30", "packages": 2},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 200
    assert r.json()["packing"] == {
        "weight_kg": 12.5, "dimensions_cm": "60x40x30", "packages": 2,
    }
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == oid))
        assert json.loads(o.packing_json)["weight_kg"] == 12.5


# --- adjuntos (DocumentStorage local) ---------------------------------------


def test_attach_document_saves_to_local_storage_and_records_ref(
    client, session_factory, tmp_path, monkeypatch
):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "erp_uploads_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "hidrive_webdav_url", "")
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/attach-document",
        files={"file": ("foto.jpg", b"\xff\xd8\xff binary", "image/jpeg")},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert doc["backend"] == "local"
    assert doc["filename"] == "foto.jpg"
    assert doc["size_bytes"] > 0
    # El archivo existe en disco y la ref queda en packing_json.
    saved = list(tmp_path.glob(f"{oid}/*_foto.jpg"))
    assert len(saved) == 1
    with session_factory() as s:
        o = s.scalar(select(Order).where(Order.id == oid))
        docs = json.loads(o.packing_json)["documents"]
        assert len(docs) == 1 and docs[0]["storage_key"].endswith("foto.jpg")


def test_attach_document_rejects_empty_file(client, session_factory, tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "erp_uploads_dir", str(tmp_path))
    with session_factory() as s:
        oid = _mk_order(s)
    r = client.post(
        f"/api/erp/orders/{oid}/attach-document",
        files={"file": ("vacio.txt", b"", "text/plain")},
        headers=auth_headers(client, "sat"),
    )
    assert r.status_code == 400


# --- selección de backend de storage ----------------------------------------


def test_get_document_storage_picks_local_without_credentials(monkeypatch):
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "hidrive_webdav_url", "")
    monkeypatch.setattr(s, "hidrive_user", "")
    monkeypatch.setattr(s, "hidrive_password", "")
    assert isinstance(get_document_storage(), LocalDocumentStorage)


def test_get_document_storage_picks_hidrive_with_credentials(monkeypatch):
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "hidrive_webdav_url", "https://webdav.hidrive.strato.com")
    monkeypatch.setattr(s, "hidrive_user", "bomedia")
    monkeypatch.setattr(s, "hidrive_password", "secret")
    assert isinstance(get_document_storage(), HiDriveDocumentStorage)


def test_local_storage_writes_file(tmp_path):
    store = LocalDocumentStorage(str(tmp_path))
    doc = store.save(order_id="o1", filename="../evil name.png",
                     content_type="image/png", data=b"data")
    assert doc.backend == "local"
    assert "evil_name.png" in doc.filename  # nombre saneado
    assert (tmp_path / doc.storage_key).read_bytes() == b"data"


# --- «No requiere envío» en lote --------------------------------------------


def test_bulk_no_shipping_saca_de_la_cola_y_es_reversible(client, session_factory):
    with session_factory() as s:
        a = _mk_order(s, number="NS-A", prep="in_queue")
        b = _mk_order(s, number="NS-B", prep="packed")  # iría a «listos»
    h = auth_headers(client, "pedidos")
    # Marcar en lote → salen de la Cola SAT.
    r = client.post("/api/erp/sat/bulk-no-shipping",
                    json={"order_ids": [a, b], "value": True}, headers=h)
    assert r.status_code == 200 and r.json()["changed"] == 2
    body = client.get("/api/erp/sat/queue", headers=h).json()
    nums = {o["order_number"] for o in body["preparing"] + body["ready_for_pickup"]}
    assert "NS-A" not in nums and "NS-B" not in nums
    # La vista «No requieren envío» (no_shipping=true) los enseña.
    marked = client.get("/api/erp/sat/queue?no_shipping=true", headers=h).json()
    mnums = {o["order_number"] for o in marked["preparing"] + marked["ready_for_pickup"]}
    assert mnums == {"NS-A", "NS-B"}
    # Auditoría: evento con los order_ids afectados.
    with session_factory() as s:
        logs = s.scalars(
            select(AuditLog).where(AuditLog.action == "erp.sat_no_shipping")
        ).all()
        assert logs and json.loads(logs[0].metadata_json)["order_ids"] == [a, b]
    # Desmarcar en lote → vuelven a la Cola SAT.
    r = client.post("/api/erp/sat/bulk-no-shipping",
                    json={"order_ids": [a, b], "value": False}, headers=h)
    assert r.status_code == 200 and r.json()["changed"] == 2
    body = client.get("/api/erp/sat/queue", headers=h).json()
    nums = {o["order_number"] for o in body["preparing"] + body["ready_for_pickup"]}
    assert "NS-A" in nums and "NS-B" in nums


def test_bulk_no_shipping_requires_edit(client, session_factory):
    with session_factory() as s:
        a = _mk_order(s, number="NS-C")
    # `viewer` es solo lectura: no puede marcar.
    r = client.post("/api/erp/sat/bulk-no-shipping",
                    json={"order_ids": [a], "value": True},
                    headers=auth_headers(client, "viewer"))
    assert r.status_code == 403


def test_bulk_no_shipping_no_toca_factura_ni_completado(client, session_factory):
    with session_factory() as s:
        a = _mk_order(s, number="NS-D", prep="packed")
        o = s.get(Order, a)
        o.invoice_status = "invoiced_by_erp"
        o.factusol_invoice_number = "260200"
        o.factusol_cobro_status = "cobrada"
        s.commit()
    h = auth_headers(client, "pedidos")
    client.post("/api/erp/sat/bulk-no-shipping",
                json={"order_ids": [a], "value": True}, headers=h)
    with session_factory() as s:
        o = s.get(Order, a)
        assert o.shipping_not_required is True
        # Factura / cobro / completado intactos.
        assert o.factusol_invoice_number == "260200"
        assert o.factusol_cobro_status == "cobrada"
        assert o.completed_at is None
