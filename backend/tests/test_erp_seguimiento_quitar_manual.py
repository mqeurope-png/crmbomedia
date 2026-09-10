"""ERP · Seguimiento — control MANUAL: quitar / reincluir cualquier pedido.

Bart decide, no una regla: se puede quitar del seguimiento CUALQUIER pedido
(cualquier `woo_status`, con o sin factura/cobro/albarán) con motivo opcional;
si tiene algo aguas abajo se AVISA pero se permite. Reversible con
«Reincluir». Idempotente. Y «escrito en Drive» pasa a ser solo un aviso.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.api.seguimiento import compose_exclusion_reason
from app.erp.downstream import DRIVE_REASON, downstream_reasons, hard_reasons
from app.erp.models import (
    ErpDriveSyncRow,
    InvoiceStatus,
    Order,
    OrderSource,
    PaymentStatus,
    ShipmentPackage,
)
from app.integrations.woocommerce.cleanup import cleanup_non_processing_orders
from app.main import app
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
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
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator:
    from fastapi.testclient import TestClient

    def override():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
    from app.core.crypto import encrypt

    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug,
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.flush()
    return a


def _order(
    s: Session, number: str, *, cliente: str = "Cliente SL",
    woo_status: str | None = None, store: IntegrationAccount | None = None,
    invoiced: bool = False, paid: bool = False, bulto: bool = False,
    drive: bool = False, source: OrderSource = OrderSource.WOOCOMMERCE,
) -> str:
    comp = Company(name=cliente)
    s.add(comp)
    s.flush()
    o = Order(
        order_number=number, external_source=source, external_id=number.split("-")[-1],
        store_id=store.id if store else None, company_id=comp.id,
        woo_status=woo_status, placed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    if invoiced:
        o.invoice_status = InvoiceStatus.GENERATED
        o.factusol_invoice_number = f"5-{number.split('-')[-1]}"
    if paid:
        o.payment_status = PaymentStatus.PAID
    s.add(o)
    s.flush()
    if bulto:
        s.add(ShipmentPackage(order_id=o.id, weight_kg=1, height_cm=10,
                              width_cm=10, depth_cm=10))
    if drive:
        s.add(ErpDriveSyncRow(order_id=o.id, row_key=number.split("-")[-1],
                              synced_at=datetime(2026, 9, 2, tzinfo=UTC)))
    s.flush()
    return o.id


def _list(http, **params) -> dict:
    r = http.get("/api/erp/seguimiento", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()


def _exclude(http, ids: list[str], **body) -> dict:
    r = http.post("/api/erp/seguimiento/exclude", json={"order_ids": ids, **body},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


def _include(http, ids: list[str]) -> dict:
    r = http.post("/api/erp/seguimiento/include", json={"order_ids": ids},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


def _preview(http, ids: list[str]) -> dict:
    r = http.post("/api/erp/seguimiento/exclude-preview", json={"order_ids": ids},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    return r.json()


# --- 1) cualquier pedido, cualquier estado ----------------------------------


def test_quitar_pedido_cualquier_estado(session_factory, http) -> None:
    """Se puede quitar un pedido en CUALQUIER `woo_status` (processing, on-hold,
    completed, cancelled, refunded, sin estado) y también uno que no es de Woo."""
    with session_factory() as s:
        store = _store(s)
        ids = {
            st or "none": _order(s, f"BOPRIN-81{i:03d}", woo_status=st, store=store)
            for i, st in enumerate(
                ["processing", "on-hold", "completed", "cancelled", "refunded", None],
            )
        }
        ids["proforma"] = _order(s, "PRO-81999", source=OrderSource.FACTUSOL_PROFORMA)
        s.commit()

    for key, oid in ids.items():
        res = _exclude(http, [oid])
        assert res["excluded"] == 1 and res["already_excluded"] == 0, key

    # Ninguno se lista ya (ni en curso, ni «todos»)…
    assert _list(http)["total"] == 0
    assert _list(http, en_curso="false")["total"] == 0
    # …y todos están en la vista de excluidos.
    assert _list(http, ver_excluidos="true")["total"] == len(ids)
    # La vista «ocultos por estado» es una lista de revisión aparte (#376): los
    # cancelados/reembolsados siguen ahí, pero marcados como excluidos (y con
    # el botón «Reincluir», no «Quitar»).
    ocultos = _list(http, ver_ocultos_estado="true")
    assert {r["woo_status"] for r in ocultos["items"]} == {"cancelled", "refunded"}
    assert all(r["excluido"] for r in ocultos["items"])
    with session_factory() as s:
        for key, oid in ids.items():
            o = s.get(Order, oid)
            assert o.seguimiento_excluded_at is not None
            assert o.seguimiento_excluded_by_user_id is not None
            # Nada más cambia: ni el estado Woo ni el pedido.
            assert o.woo_status == (None if key in ("none", "proforma") else key)


# --- 2) con factura / cobro / albarán: avisa pero permite -------------------


def test_quitar_pedido_con_factura_avisa_pero_permite(session_factory, http) -> None:
    with session_factory() as s:
        store = _store(s)
        fact = _order(s, "BOPRIN-82001", woo_status="completed", store=store,
                      invoiced=True, paid=True)
        bulto = _order(s, "BOPRIN-82002", woo_status="processing", store=store,
                       bulto=True)
        limpio = _order(s, "BOPRIN-82003", woo_status="on-hold", store=store)
        s.commit()

    # La previsualización AVISA de lo que hay aguas abajo (no bloquea, no escribe).
    pre = _preview(http, [fact, bulto, limpio])
    by_num = {it["order_number"]: it for it in pre["items"]}
    assert by_num["BOPRIN-82001"]["avisos"] == ["facturado", "cobrado/pagado"]
    assert by_num["BOPRIN-82002"]["avisos"] == ["albarán/envío"]
    assert by_num["BOPRIN-82003"]["avisos"] == []
    assert pre["con_avisos"] == 2 and pre["ya_excluidos"] == 0
    assert by_num["BOPRIN-82001"]["cliente"] == "Cliente SL"
    assert _list(http, ver_excluidos="true")["total"] == 0  # no escribió nada

    # Quitar los tres: se permite; la respuesta repite los avisos.
    res = _exclude(http, [fact, bulto, limpio], reason_code="cancelado")
    assert res["excluded"] == 3
    assert res["avisos"] == {
        "BOPRIN-82001": ["facturado", "cobrado/pagado"],
        "BOPRIN-82002": ["albarán/envío"],
    }
    assert res["con_avisos"] == 2
    assert _list(http, ver_excluidos="true")["total"] == 3
    with session_factory() as s:
        o = s.get(Order, fact)
        # Excluido, pero la factura y el cobro siguen intactos.
        assert o.seguimiento_excluded_at is not None
        assert o.factusol_invoice_number == "5-82001"
        assert o.invoice_status == InvoiceStatus.GENERATED
        assert o.payment_status == PaymentStatus.PAID
        assert o.seguimiento_excluded_reason == "cancelado"


# --- 3) reincluir deshace; idempotente ----------------------------------------


def test_reincluir_deshace(session_factory, http) -> None:
    with session_factory() as s:
        store = _store(s)
        oid = _order(s, "BOPRIN-83001", woo_status="cancelled", store=store,
                     invoiced=True)
        s.commit()

    first = _exclude(http, [oid], reason_code="reembolsado", reason="devuelto 10/9")
    assert first["excluded"] == 1
    with session_factory() as s:
        o = s.get(Order, oid)
        stamp, reason = o.seguimiento_excluded_at, o.seguimiento_excluded_reason
        assert reason == "reembolsado: devuelto 10/9"

    # Quitar lo ya quitado: no rompe y NO pisa la fecha ni el motivo originales.
    again = _exclude(http, [oid], reason_code="prueba")
    assert again["excluded"] == 0 and again["already_excluded"] == 1
    with session_factory() as s:
        o = s.get(Order, oid)
        assert (o.seguimiento_excluded_at, o.seguimiento_excluded_reason) == (stamp, reason)

    # Reincluir deshace TODO (fecha, quién, motivo) y vuelve a la vista.
    res = _include(http, [oid])
    assert res["included"] == 1 and res["already_included"] == 0
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.seguimiento_excluded_at is None
        assert o.seguimiento_excluded_by_user_id is None
        assert o.seguimiento_excluded_reason is None
        assert o.factusol_invoice_number == "5-83001"  # el pedido no se toca
    assert _list(http, ver_excluidos="true")["total"] == 0
    # (cancelled queda oculto por ESTADO — regla #376 — no por exclusión manual.)
    assert _list(http, ver_ocultos_estado="true")["total"] == 1

    # Reincluir lo ya incluido: no rompe.
    res2 = _include(http, [oid])
    assert res2["included"] == 0 and res2["already_included"] == 1

    # Y se puede volver a quitar.
    assert _exclude(http, [oid])["excluded"] == 1


# --- 4) en bloque desde las casillas ------------------------------------------


def test_quitar_multiple(session_factory, http) -> None:
    with session_factory() as s:
        store = _store(s)
        ids = [
            _order(s, f"BOPRIN-84{i:03d}", woo_status=st, store=store,
                   invoiced=(i == 1))
            for i, st in enumerate(["processing", "completed", "on-hold"])
        ]
        s.commit()

    res = _exclude(http, ids, reason_code="duplicado", reason="lote del 10/9")
    assert res["excluded"] == 3 and res["already_excluded"] == 0
    assert res["reason"] == "duplicado: lote del 10/9"
    assert list(res["avisos"]) == ["BOPRIN-84001"]  # el facturado avisa, no bloquea
    assert _list(http)["total"] == 0
    excl = _list(http, ver_excluidos="true")
    assert excl["total"] == 3
    assert {r["excluido_motivo"] for r in excl["items"]} == {"duplicado: lote del 10/9"}
    # Quién lo quitó, con nombre (para la vista de excluidos).
    assert all(r["excluido_por_nombre"] for r in excl["items"])
    assert all(r["excluido_en"] for r in excl["items"])

    # Reincluir en bloque.
    assert _include(http, ids)["included"] == 3
    assert _list(http, ver_excluidos="true")["total"] == 0


# --- motivo: rápidos + texto libre, sin migración -----------------------------


def test_motivo_rapido_y_texto() -> None:
    assert compose_exclusion_reason("cancelado", None) == "cancelado"
    assert compose_exclusion_reason("duplicado", " era el  5782 ") == "duplicado: era el 5782"
    assert compose_exclusion_reason("otro", "lo pidió el cliente") == "lo pidió el cliente"
    assert compose_exclusion_reason("otro", "") == "otro"
    assert compose_exclusion_reason(None, "texto libre") == "texto libre"
    assert compose_exclusion_reason(None, "   ") is None
    assert compose_exclusion_reason("inventado", "x") == "x"


def test_reason_code_invalido_422(session_factory, http) -> None:
    with session_factory() as s:
        oid = _order(s, "BOPRIN-85001")
        s.commit()
    r = http.post("/api/erp/seguimiento/exclude",
                  json={"order_ids": [oid], "reason_code": "capricho"},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422
    r = http.post("/api/erp/seguimiento/exclude-preview", json={"order_ids": [oid]},
                  headers=auth_headers(http, "user"))
    assert r.status_code in (401, 403)  # solo lectura no puede


# --- «escrito en Drive»: aviso, no protección ---------------------------------


def test_escrito_en_drive_es_aviso_no_proteccion(session_factory, http) -> None:
    with session_factory() as s:
        store = _store(s)
        drive_only = _order(s, "FLUXLA-5781", woo_status="pending", store=store,
                            drive=True)
        drive_fact = _order(s, "FLUXLA-5749", woo_status="refunded", store=store,
                            drive=True, invoiced=True)
        s.commit()

        o = s.get(Order, drive_only)
        reasons = downstream_reasons(s, o)
        assert reasons == [DRIVE_REASON]
        assert hard_reasons(reasons) == []

        # Limpieza por lotes (#387): el «escrito en Drive» solo ya NO protege.
        res = cleanup_non_processing_orders(s, dry_run=True)
        cands = {c["order_number"]: c for c in res["candidates"]}
        assert "FLUXLA-5781" in cands
        assert cands["FLUXLA-5781"]["motivos"] == []
        assert cands["FLUXLA-5781"]["avisos"] == [DRIVE_REASON]
        # El facturado sigue protegido por la FACTURA (hecho real), no por Drive.
        prot = {p["order_number"]: p for p in res["protected"]}
        assert prot["FLUXLA-5749"]["motivos"] == ["facturado"]
        assert prot["FLUXLA-5749"]["avisos"] == [DRIVE_REASON]
        assert "escrito en Drive" not in res["protegidos_por_motivo"]

    # Control manual: el de Drive+factura se avisa y se quita igual (Bart manda).
    pre = _preview(http, [drive_fact])
    assert pre["items"][0]["avisos"] == ["facturado", DRIVE_REASON]
    assert _exclude(http, [drive_fact], reason_code="reembolsado")["excluded"] == 1
