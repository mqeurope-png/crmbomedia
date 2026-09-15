"""Lote ERP · Bloque 2d — «Anular pedido» (manual / FACTUSOL; reversible;
distinto de «quitar»), con borrado opcional del albarán / presupuesto en
FACTUSOL tras aviso y confirmación. La factura nunca se toca.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderSource
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient


class DeletingFakeClient(FakeClient):
    """FakeClient + `delete_records` (la primitiva real no devuelve conteo)."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        super().__init__(tables)
        self.deletes: list[tuple[str, str]] = []

    def delete_records(self, tabla: str, filtro: str, *, ejercicio: str | None = None):
        self.deletes.append((tabla, filtro))
        return {"respuesta": "OK"}


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


def _tables(*, estalb: int = 0, estpre: int = 0, invoiced_child: bool = False):
    lfa = []
    if invoiced_child:
        lfa = [{"TIPLFA": "5", "CODLFA": 260200, "POSLFA": 1,
                "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 500010}]
    return {
        "F_ALB": [{"TIPALB": "5", "CODALB": 500010, "ESTALB": estalb,
                   "CLIALB": 3001, "CNOALB": "Escola", "REFALB": "MANUAL-000777"}],
        "F_LAL": [{"TIPLAL": "5", "CODLAL": 500010, "POSLAL": 1,
                   "DOCLAL": "P", "DTPLAL": "1", "DCOLAL": 9001}],
        "F_PRE": [{"TIPPRE": "1", "CODPRE": 9001, "ESTPRE": estpre,
                   "CLIPRE": 3001, "CNOPRE": "Escola"}],
        "F_LPS": [{"TIPLPS": "1", "CODLPS": 9001, "POSLPS": 1}],
        "F_LFA": lfa, "F_FAC": [],
    }


def _seed_order(session: Session, *, source: OrderSource = OrderSource.FACTUSOL_PROFORMA,
                invoice: str | None = None, with_docs: bool = True) -> Order:
    company = Company(name="Escola La Muntanyeta", source="manual",
                      factusol_company_id="3001", country="ES")
    session.add(company)
    session.flush()
    packing: dict[str, Any] = {}
    if with_docs:
        packing = {
            "factusol_source": {"doc_type": "presupuestos", "serie": 1, "codigo": 9001,
                                "numero": "1-009001", "cliente_codigo": "3001"},
            "factusol_albaran": {"serie": 5, "codigo": 500010, "numero": "5-500010"},
        }
    order = Order(
        external_source=source, order_number="MANUAL-000777",
        company_id=company.id, total_amount=300, currency="EUR",
        factusol_albaran_number="5-500010" if with_docs else None,
        factusol_invoice_number=invoice,
        packing_json=json.dumps(packing) if packing else None,
    )
    session.add(order)
    session.commit()
    return order


def _patched(tables=None):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=DeletingFakeClient(tables if tables is not None else _tables()),
    )


# ---------------------------------------------------------------------------


def test_cancel_preview_lists_deletable_docs(http, session_factory) -> None:
    with session_factory() as s:
        oid = _seed_order(s).id
    with _patched():
        r = http.post(f"/api/erp/orders/{oid}/cancel-preview",
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_cancel"] is True and body["blockers"] == []
    docs = {d["doc_type"]: d for d in body["factusol_docs"]}
    assert docs["albaranes"]["numero"] == "5-500010"
    assert docs["albaranes"]["deletable"] is True
    assert docs["presupuestos"]["numero"] == "1-009001"
    assert docs["presupuestos"]["deletable"] is True


def test_cancel_preview_marks_invoiced_albaran_not_deletable(http, session_factory) -> None:
    with session_factory() as s:
        oid = _seed_order(s).id
    with _patched(_tables(estalb=1)):
        docs = {d["doc_type"]: d for d in http.post(
            f"/api/erp/orders/{oid}/cancel-preview", headers=auth_headers(http, "pedidos"),
        ).json()["factusol_docs"]}
    assert docs["albaranes"]["deletable"] is False
    assert "facturado" in docs["albaranes"]["reason"].lower()
    # El presupuesto sí, salvo que tenga otros albaranes o no esté pendiente.
    assert docs["presupuestos"]["deletable"] is True
    with _patched(_tables(estpre=1)):
        docs2 = {d["doc_type"]: d for d in http.post(
            f"/api/erp/orders/{oid}/cancel-preview", headers=auth_headers(http, "pedidos"),
        ).json()["factusol_docs"]}
    assert docs2["presupuestos"]["deletable"] is False


def test_cancel_web_or_invoiced_is_blocked(http, session_factory) -> None:
    with session_factory() as s:
        web = _seed_order(s, source=OrderSource.WOOCOMMERCE, with_docs=False)
        web.order_number = "FLUXLA-1"
        s.commit()
        web_id = web.id
        inv = Order(external_source=OrderSource.MANUAL, order_number="MANUAL-000778",
                    total_amount=1, currency="EUR", factusol_invoice_number="260090")
        s.add(inv)
        s.commit()
        inv_id = inv.id
    pre = http.post(f"/api/erp/orders/{web_id}/cancel-preview",
                    headers=auth_headers(http, "pedidos")).json()
    assert pre["can_cancel"] is False and any("web" in b for b in pre["blockers"])
    r = http.post(f"/api/erp/orders/{inv_id}/cancel", json={"confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "cannot_cancel"
    assert "factura" in r.json()["detail"]["detail"].lower()


def test_cancel_requires_confirmation(http, session_factory) -> None:
    with session_factory() as s:
        oid = _seed_order(s).id
    r = http.post(f"/api/erp/orders/{oid}/cancel", json={"confirm": False},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"


def test_cancel_marks_order_hides_it_and_is_reversible(http, session_factory) -> None:
    with session_factory() as s:
        oid = _seed_order(s).id
    headers = auth_headers(http, "pedidos")
    r = http.post(f"/api/erp/orders/{oid}/cancel",
                  json={"confirm": True, "reason": "cliente se echa atrás"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] is True and body["cancelled_reason"] == "cliente se echa atrás"
    assert body["cancelled_by_name"] and body["already_cancelled"] is False
    assert body["factusol_delete_job_id"] is None  # no se pidió borrar
    assert body["workflow"]["next_action"] == "ninguna"
    assert "anulado" in body["workflow"]["next_action_hint"].lower()
    # Fuera de la bandeja por defecto; visible con «Ver anulados».
    ids = [o["id"] for o in http.get("/api/erp/orders", headers=headers).json()["items"]]
    assert oid not in ids
    only = http.get("/api/erp/orders?show_cancelled=true", headers=headers).json()["items"]
    assert [o["id"] for o in only] == [oid]
    # Timeline: evento de auditoría con el motivo.
    tl = http.get(f"/api/erp/orders/{oid}/timeline", headers=headers).json()
    evento = next(e for e in tl["items"] if e.get("title") == "erp.order_cancelled")
    assert evento["detail"]["reason"] == "cliente se echa atrás"
    # Idempotente.
    again = http.post(f"/api/erp/orders/{oid}/cancel", json={"confirm": True}, headers=headers)
    assert again.json()["already_cancelled"] is True
    # Restaurar.
    back = http.post(f"/api/erp/orders/{oid}/uncancel", headers=headers)
    assert back.status_code == 200 and back.json()["cancelled"] is False
    assert oid in [o["id"] for o in http.get("/api/erp/orders", headers=headers).json()["items"]]


def test_cancel_with_delete_enqueues_only_deletable_docs(http, session_factory) -> None:
    with session_factory() as s:
        oid = _seed_order(s).id
    with _patched(_tables(estalb=1)), patch(
        "app.integrations.factusol.jobs.enqueue_cancel_order_documents", return_value="job-9",
    ) as enq:
        r = http.post(f"/api/erp/orders/{oid}/cancel",
                      json={"confirm": True, "delete_factusol_docs": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["factusol_delete_job_id"] == "job-9"
    # Solo el presupuesto (el albarán está facturado → no se borra).
    assert [d["doc_type"] for d in body["factusol_docs_to_delete"]] == ["presupuestos"]
    enq.assert_called_once()
    args = enq.call_args.args
    assert args[0] == oid and [d["doc_type"] for d in args[1]] == ["presupuestos"]


def test_delete_cancelled_order_documents_deletes_lines_then_headers(session_factory) -> None:
    """El borrado real: albarán antes que presupuesto, líneas antes que
    cabecera, filtro por (serie, código); desvincula en BoHub y queda en el
    timeline. Un documento que dejó de ser borrable se omite."""
    from app.erp.order_cancel import delete_cancelled_order_documents, mark_cancelled

    with session_factory() as s:
        order = _seed_order(s)
        mark_cancelled(s, order, None, "prueba")
        s.commit()
        client = DeletingFakeClient(_tables())
        docs = [
            {"doc_type": "presupuestos", "serie": 1, "codigo": 9001},
            {"doc_type": "albaranes", "serie": 5, "codigo": 500010},
        ]
        result = delete_cancelled_order_documents(s, client, order, docs, ejercicio="2026")
        assert result["deleted"] == ["albaranes:5-500010", "presupuestos:1-009001"]
        assert result["skipped"] == []
        assert client.deletes == [
            ("F_LAL", "TIPLAL='5' AND CODLAL='500010'"),
            ("F_ALB", "TIPALB='5' AND CODALB='500010'"),
            ("F_LPS", "TIPLPS='1' AND CODLPS='9001'"),
            ("F_PRE", "TIPPRE='1' AND CODPRE='9001'"),
        ]
        s.refresh(order)
        assert order.factusol_albaran_number is None
        packing = json.loads(order.packing_json)
        assert "factusol_albaran" not in packing
        assert packing["factusol_source"]["deleted_at"]
        # Nada se ha escrito ni borrado en F_FAC / F_LFA.
        assert not any(t in ("F_FAC", "F_LFA") for t, _ in client.deletes)


def test_delete_cancelled_order_documents_skips_invoiced_albaran(session_factory) -> None:
    from app.erp.order_cancel import delete_cancelled_order_documents, mark_cancelled

    with session_factory() as s:
        order = _seed_order(s)
        mark_cancelled(s, order, None, None)
        s.commit()
        client = DeletingFakeClient(_tables(invoiced_child=True))
        result = delete_cancelled_order_documents(
            s, client, order, [{"doc_type": "albaranes", "serie": 5, "codigo": 500010}],
            ejercicio="2026",
        )
        assert result["deleted"] == []
        assert result["skipped"][0]["numero"] == "5-500010"
        assert "5-260200" in result["skipped"][0]["reason"]
        assert client.deletes == []
        s.refresh(order)
        assert order.factusol_albaran_number == "5-500010"  # sigue vinculado
