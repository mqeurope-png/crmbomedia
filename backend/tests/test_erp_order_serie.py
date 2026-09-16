"""Lote 7 · P1 — elegir la SERIE (empresa emisora) de un pedido MANUAL al
crearlo y cambiarla luego (borra y recrea el albarán en la serie nueva).

Series: 1 Bomedia / 2 MQ Europe / 4 Lambert / 5 Streamtec. `resolve_serie`
respeta `factusol_manual_serie` (por encima del `by_source`/default), así que
TODO documento que BoHub emita desde el pedido sale en la empresa elegida.
"""
from __future__ import annotations

import json
from collections.abc import Generator
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
from app.erp.factusol_albaran import change_order_serie, create_albaran_for_order
from app.erp.models import Order, OrderLine, OrderSource, OrderStatusHistory
from app.integrations.factusol import chain
from app.integrations.factusol.chain import ALB_REFERENCE_COLUMNS, LAL_REFERENCE_COLUMNS
from app.integrations.factusol.service import resolve_serie
from app.main import app
from app.models.crm import AuditLog, Company, User, UserRole
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_erp_albaran_manual import ARTICULOS, CLIENTE, AlbaranFakeClient
from tests.test_factusol_chain import _live_alb_row, _live_lal_row

# --- tablas del fake --------------------------------------------------------


def _base_tables() -> dict[str, list[dict[str, Any]]]:
    """F_ALB/F_LAL con una fila viva en serie 5 (500003) como plantilla del
    guard y para el contador MAX+1; F_CLI + F_ART del pedido."""
    return {
        "F_ALB": [_live_alb_row(500003, "5", CLIALB=2458, CNOALB="OTRO SL",
                                CPAALB="724", ALMALB="GEN", FOPALB="002")],
        "F_LAL": [_live_lal_row(500003, "5", ARTLAL="x", DESLAL="y", CANLAL=1.0,
                                PRELAL=2.0, TOTLAL=2.0, DT1LAL=0.0)],
        "F_CLI": [dict(CLIENTE)],
        "F_ART": [dict(a) for a in ARTICULOS],
    }


def _tables_with_serie1_albaran() -> dict[str, list[dict[str, Any]]]:
    """+ el albarán viejo `1-100331` (serie 1) que se debe borrar al cambiar."""
    tables = _base_tables()
    tables["F_ALB"].append(
        _live_alb_row(100331, "1", CLIALB=2458, CNOALB="ESCOLA",
                      CPAALB="724", ALMALB="GEN", FOPALB="002"),
    )
    tables["F_LAL"].append(
        _live_lal_row(100331, "1", ARTLAL="x", DESLAL="y", CANLAL=1.0,
                      PRELAL=2.0, TOTLAL=2.0, DT1LAL=0.0),
    )
    return tables


def _client(tables: dict[str, list[dict[str, Any]]] | None = None) -> AlbaranFakeClient:
    # La allowlist de columnas vivas se cachea por proceso: cada fake parte de
    # cero (mismo patrón que el test de albarán manual).
    chain._LIVE_COLUMNS_CACHE.clear()
    return AlbaranFakeClient(
        tables if tables is not None else _base_tables(),
        known_columns={"F_ALB": ALB_REFERENCE_COLUMNS, "F_LAL": LAL_REFERENCE_COLUMNS},
    )


def _patched(fake: AlbaranFakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session_factory(engine) -> Generator[sessionmaker, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.add(Company(id="dupli", name="Duplicoder", factusol_company_id="2458",
                         country="ES"))
        seed.commit()
    yield factory


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _pedidos_user(s: Session) -> User:
    return s.scalar(select(User).where(User.role == UserRole.PEDIDOS))


def _manual(s: Session, oid: str, number: str, lines: list[dict[str, Any]], *,
            serie: int | None = None, albaran: str | None = None,
            invoice: str | None = None) -> Order:
    o = Order(
        id=oid, external_source=OrderSource.MANUAL, external_id=number.split("-")[-1],
        order_number=number, company_id="dupli", total_amount=0,
        factusol_manual_serie=serie, factusol_albaran_number=albaran,
        factusol_invoice_number=invoice,
    )
    s.add(o)
    s.flush()
    for i, ln in enumerate(lines):
        s.add(OrderLine(
            order_id=oid, position=i, product_sku=ln.get("sku", ""),
            product_codart=ln.get("codart"), description=ln["desc"],
            quantity=ln.get("qty", 1), unit_price=ln["price"],
            tax_rate=ln.get("iva", 21), line_total=round(ln.get("qty", 1) * ln["price"], 2),
        ))
    return o


# --- 1) crear con serie -----------------------------------------------------


def test_create_manual_order_with_serie_5_stores_and_resolves(http, session_factory) -> None:
    """El alta manual con `factusol_serie=5` guarda la serie, `resolve_serie`
    la devuelve y el albarán se crea en la serie 5."""
    body = {
        "company_id": "dupli",
        "factusol_serie": 5,
        "lines": [{"product_sku": "Ink500mlCY", "description": "Tinta cyan",
                   "quantity": 2, "unit_price": 40}],
    }
    r = http.post("/api/erp/orders", json=body, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert r.json()["factusol_manual_serie"] == 5

    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.factusol_manual_serie == 5
        assert resolve_serie(s, o) == 5

    fake = _client()
    with session_factory() as s:
        result = create_albaran_for_order(s, fake, s.get(Order, oid), ejercicio="2026")
    assert result["serie"] == 5 and result["numero"].startswith("5-")
    with session_factory() as s:
        assert s.get(Order, oid).factusol_albaran_number == result["numero"]


def test_create_manual_order_rejects_invalid_serie(http) -> None:
    """`factusol_serie=3` (no es empresa emisora) → 422 de validación."""
    body = {
        "company_id": "dupli", "factusol_serie": 3,
        "lines": [{"product_sku": "CDR80", "description": "CD", "quantity": 1, "unit_price": 1}],
    }
    r = http.post("/api/erp/orders", json=body, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 422, r.text


# --- 2) cambiar la serie: borra + recrea el albarán -------------------------


def test_change_serie_deletes_old_albaran_and_recreates_in_new_serie(session_factory) -> None:
    """MANUAL-000006 en serie 1 con albarán `1-100331` → serie 5: BORRA el
    `1-100331` (delete_factusol_document con serie 1, código 100331) y RECREA un
    `5-X`; el pedido queda con `factusol_manual_serie==5`, el nuevo nº y su
    evento de auditoría."""
    with session_factory() as s:
        _manual(s, "o-6", "MANUAL-000006",
                [{"sku": "Ink500mlCY", "desc": "Tinta cyan", "qty": 2, "price": 40.0}],
                serie=1, albaran="1-100331")
        s.commit()

    fake = _client(_tables_with_serie1_albaran())
    with session_factory() as s:
        order = s.get(Order, "o-6")
        result = change_order_serie(
            s, order, 5, client=fake, ejercicio="2026", actor=_pedidos_user(s),
        )

    # Se borró el albarán viejo en FACTUSOL: líneas + cabecera, por (serie, código).
    assert ("F_LAL", "TIPLAL='1' AND CODLAL='100331'") in fake.deleted
    assert ("F_ALB", "TIPALB='1' AND CODALB='100331'") in fake.deleted
    # NUNCA se toca la factura.
    assert not any(t in ("F_FAC", "F_LFA") for t, _ in fake.deleted)
    # Se recreó en serie 5.
    assert result["deleted"] is True and result["recreated"] is True
    assert result["old_serie"] == 1 and result["new_serie"] == 5
    assert result["old_albaran_number"] == "1-100331"
    assert result["albaran_number"].startswith("5-")
    assert result["error"] is None

    with session_factory() as s:
        o = s.get(Order, "o-6")
        assert o.factusol_manual_serie == 5
        assert o.factusol_albaran_number == result["albaran_number"]
        # Auditoría del cambio de serie (viejo → nuevo).
        audits = s.scalars(
            select(AuditLog).where(AuditLog.action == "erp.order_serie_changed")
        ).all()
        assert len(audits) == 1 and audits[0].target_id == "o-6"
        meta = json.loads(audits[0].metadata_json)
        assert meta["old_serie"] == 1 and meta["new_serie"] == 5
        assert meta["new_albaran_number"] == result["albaran_number"]
        # Traza en el historial del pedido.
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-6",
        )).all()
        assert any(
            (h.metadata_json or "").find("factusol_serie_changed") != -1 for h in hist
        )


def test_change_serie_delete_ok_recreate_fails_leaves_order_without_albaran(
    session_factory,
) -> None:
    """Robustez: si el borrado sale pero la re-creación falla (aquí, F_CLI ya no
    responde), el pedido queda SIN albarán (estado conocido) y el fallo se
    anota — BoHub no reclama un albarán que no está en FACTUSOL."""
    with session_factory() as s:
        _manual(s, "o-ko", "MANUAL-000009",
                [{"sku": "Ink500mlCY", "desc": "Tinta cyan", "qty": 1, "price": 40.0}],
                serie=1, albaran="1-100331")
        s.commit()

    tables = _tables_with_serie1_albaran()
    tables["F_CLI"] = []  # la re-creación no podrá leer el cliente → FactusolError
    fake = _client(tables)
    with session_factory() as s:
        result = change_order_serie(
            s, s.get(Order, "o-ko"), 5, client=fake, ejercicio="2026",
            actor=_pedidos_user(s),
        )
    assert result["deleted"] is True and result["recreated"] is False
    assert result["error"] and result["albaran_number"] is None
    # El albarán viejo SÍ se borró.
    assert ("F_ALB", "TIPALB='1' AND CODALB='100331'") in fake.deleted
    with session_factory() as s:
        o = s.get(Order, "o-ko")
        assert o.factusol_manual_serie == 5          # la serie sí quedó fijada
        assert o.factusol_albaran_number is None     # sin albarán reclamado
        hist = s.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == "o-ko",
        )).all()
        assert any("No se" in (h.reason or "") or "recrear" in (h.reason or "").lower()
                   or "albarán" in (h.reason or "").lower() for h in hist)


def test_change_serie_without_albaran_only_records(session_factory) -> None:
    """Sin albarán no hay nada que borrar / recrear: solo se registra la serie
    (sin cliente FACTUSOL)."""
    with session_factory() as s:
        _manual(s, "o-noalb", "MANUAL-000010",
                [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        s.commit()
    with session_factory() as s:
        result = change_order_serie(
            s, s.get(Order, "o-noalb"), 2, client=None, ejercicio="2026",
            actor=_pedidos_user(s),
        )
    assert result["new_serie"] == 2 and result["deleted"] is False
    assert result["recreated"] is False and result["albaran_number"] is None
    with session_factory() as s:
        assert s.get(Order, "o-noalb").factusol_manual_serie == 2


# --- 3) endpoint ------------------------------------------------------------


def test_change_serie_endpoint_refused_with_invoice(http, session_factory) -> None:
    """Con factura emitida → 409 (la serie se cambia anulando la factura desde
    FACTUSOL); NO se borra nada y la serie no cambia."""
    with session_factory() as s:
        _manual(s, "o-inv", "MANUAL-000007",
                [{"sku": "CDR80", "desc": "CD", "price": 1.0}],
                albaran="1-100331", invoice="260090")
        s.commit()
    fake = _client(_tables_with_serie1_albaran())
    with _patched(fake):
        r = http.post("/api/erp/orders/o-inv/factusol-serie",
                      json={"serie": 5, "confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invoice_present"
    assert fake.deleted == []
    with session_factory() as s:
        assert s.get(Order, "o-inv").factusol_manual_serie is None


def test_change_serie_endpoint_requires_confirm(http, session_factory) -> None:
    with session_factory() as s:
        _manual(s, "o-c", "MANUAL-000011", [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        s.commit()
    r = http.post("/api/erp/orders/o-c/factusol-serie",
                  json={"serie": 5, "confirm": False},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "confirmation_required"


def test_change_serie_endpoint_with_albaran_enqueues_job(http, session_factory) -> None:
    """Con albarán, el endpoint encola el borrado+recreación en el worker serial
    (`factusol:writes`) y devuelve el job_id."""
    with session_factory() as s:
        _manual(s, "o-alb", "MANUAL-000012",
                [{"sku": "CDR80", "desc": "CD", "price": 1.0}], serie=1, albaran="1-100331")
        s.commit()
    with patch("app.integrations.factusol.jobs.enqueue_change_order_serie",
               return_value="job-serie") as enq:
        r = http.post("/api/erp/orders/o-alb/factusol-serie",
                      json={"serie": 5, "confirm": True},
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["factusol_serie_job_id"] == "job-serie"
    assert r.json()["requested_serie"] == 5
    enq.assert_called_once()
    assert enq.call_args.args[0] == "o-alb" and enq.call_args.args[1] == 5


def test_change_serie_endpoint_no_albaran_records_synchronously(http, session_factory) -> None:
    with session_factory() as s:
        _manual(s, "o-sync", "MANUAL-000013", [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        s.commit()
    r = http.post("/api/erp/orders/o-sync/factusol-serie",
                  json={"serie": 4, "confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    assert r.json()["factusol_serie_job_id"] is None
    assert r.json()["factusol_manual_serie"] == 4
    with session_factory() as s:
        assert s.get(Order, "o-sync").factusol_manual_serie == 4


def test_change_serie_endpoint_web_order_refused(http, session_factory) -> None:
    with session_factory() as s:
        o = _manual(s, "o-web", "BOPRIN-99990", [{"sku": "CDR80", "desc": "CD", "price": 1.0}])
        o.external_source = OrderSource.WOOCOMMERCE
        s.commit()
    r = http.post("/api/erp/orders/o-web/factusol-serie",
                  json={"serie": 5, "confirm": True},
                  headers=auth_headers(http, "pedidos"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "web_order"
