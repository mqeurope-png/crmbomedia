"""Lote 2 · Bloque B — corregir vínculos factura↔pedido cruzados (dry-run /
apply) y prevención: al vincular se guarda serie + número.

Caso real: BOPRIN-99919, BOPRIN-99927 y ARTISJ-9537 apuntaban a la factura de
OTRO cliente. Cada uno se re-vincula a la factura cuya REFFAC es su referencia
(BOP-099919, BOP-099927, ART-009537); sin factura propia → sin vínculo.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.models import Order, OrderSource, OrderStatusHistory
from app.models.crm import AuditLog, Company, ExternalSystem
from app.models.integration_settings import IntegrationAccount, IntegrationMode
from tests._test_helpers import seed_test_users
from tests.test_factusol_documents import FakeClient


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


def _fac(tip: str, cod: int, cli: int, nombre: str, ref: str) -> dict[str, Any]:
    return {"TIPFAC": tip, "CODFAC": cod, "CLIFAC": cli, "CNOFAC": nombre, "REFFAC": ref,
            "FECFAC": "2026-09-01T00:00:00", "TOTFAC": 100.0}


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {"F_FAC": [
        _fac("5", 260090, 4391, "ESCOLA LA MUNTANYETA", "FLE-005784"),  # de Escola
        _fac("5", 260085, 3892, "NEON LED, S.L.", "BOP-099919"),        # la de BOPRIN-99919
        _fac("2", 260070, 4400, "ATELIER ART", "ART-009537"),           # la de ARTISJ-9537
        # BOPRIN-99927 NO tiene factura propia.
    ]}


def _store(session: Session, slug: str, prefix: str | None = None) -> IntegrationAccount:
    store = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug.title(),
        enabled=True, mode=IntegrationMode.LIVE,
        metadata_json=json.dumps({"factusol_ref_prefix": prefix}) if prefix else None,
    )
    session.add(store)
    session.flush()
    return store


def _seed(session: Session) -> dict[str, Order]:
    boprint = _store(session, "boprint")
    artis = _store(session, "artisjet")
    flux = _store(session, "fluxlasers", "FLE")
    cos = {
        "neon": Company(name="NEON LED, S.L.", source="manual", factusol_company_id="3892"),
        "escola": Company(name="Escola La Muntanyeta", source="manual", factusol_company_id="4391"),
        "art": Company(name="Atelier Art", source="manual", factusol_company_id="4400"),
        "otro": Company(name="Otro cliente", source="manual", factusol_company_id="4500"),
    }
    session.add_all(cos.values())
    session.flush()

    def order(num: str, store: IntegrationAccount, co: Company, inv: str | None,
              serie: int | None, cobro: str | None = None) -> Order:
        o = Order(external_source=OrderSource.WOOCOMMERCE, order_number=num,
                  store_id=store.id, company_id=co.id, total_amount=100, currency="EUR",
                  factusol_invoice_number=inv, factusol_invoice_serie=serie,
                  invoice_status="invoiced_by_erp" if inv else "not_invoiced",
                  factusol_cobro_status=cobro,
                  packing_json=json.dumps({"factusol_cobro": {"numero": "x"}}) if cobro else None)
        session.add(o)
        return o

    orders = {
        # Los tres cruzados: apuntan al 260090 de Escola (o al 260070 de Atelier).
        "BOPRIN-99919": order("BOPRIN-99919", boprint, cos["neon"], "260090", 5, cobro="cobrada"),
        "BOPRIN-99927": order("BOPRIN-99927", boprint, cos["otro"], "260090", 5),
        "ARTISJ-9537": order("ARTISJ-9537", artis, cos["art"], "260090", None),
        # El correcto.
        "FLUXLA-5784": order("FLUXLA-5784", flux, cos["escola"], "260090", 5),
    }
    session.commit()
    return orders


def test_plan_relinks_by_reffac_or_unlinks(session_factory) -> None:
    from app.erp.invoice_link_fix import format_plan, plan_relinks

    with session_factory() as s:
        _seed(s)
        client = FakeClient(_tables())
        plan = plan_relinks(s, client, ejercicio="2026")
        assert plan["scan_totales"]["cruzado"] == 3
        by = {p["order_number"]: p for p in plan["plans"]}
        assert set(by) == {"BOPRIN-99919", "BOPRIN-99927", "ARTISJ-9537"}
        assert by["BOPRIN-99919"]["action"] == "relink"
        assert by["BOPRIN-99919"]["nueva"]["numero"] == "5-260085"
        assert by["BOPRIN-99919"]["nueva"]["clifac"] == "3892"
        assert by["ARTISJ-9537"]["action"] == "relink"
        assert by["ARTISJ-9537"]["nueva"]["numero"] == "2-260070"
        assert by["BOPRIN-99927"]["action"] == "unlink"
        assert plan["totals"] == {"relink": 2, "unlink": 1, "ambiguous": 0}
        text = format_plan(plan)
        assert "[RELINK  ] BOPRIN-99919" in text and "5-260085" in text
        assert "[UNLINK  ] BOPRIN-99927" in text
        # Dry-run: nada escrito.
        s.expire_all()
        o = s.scalar(select(Order).where(Order.order_number == "BOPRIN-99919"))
        assert o.factusol_invoice_number == "260090"
        assert {t for t, _ in client.calls} == {"F_FAC"}  # solo lectura de F_FAC


def test_apply_relinks_fixes_and_prevents_reappearance(session_factory) -> None:
    from app.erp.invoice_link_fix import apply_relinks, plan_relinks
    from app.erp.invoice_link_scan import scan_invoice_links

    with session_factory() as s:
        _seed(s)
        client = FakeClient(_tables())
        plan = plan_relinks(
            s, client, ejercicio="2026",
            only={"BOPRIN-99919", "BOPRIN-99927", "ARTISJ-9537"},
        )
        result = apply_relinks(s, plan)
        assert {d["order_number"]: d["numero"] for d in result["done"]} == {
            "BOPRIN-99919": "5-260085", "ARTISJ-9537": "2-260070", "BOPRIN-99927": None,
        }
        assert result["skipped"] == []
        s.expire_all()
        o1 = s.scalar(select(Order).where(Order.order_number == "BOPRIN-99919"))
        assert (o1.factusol_invoice_number, o1.factusol_invoice_serie) == ("260085", 5)
        assert o1.factusol_cobro_status is None  # el cobro era de la factura equivocada
        packing1 = json.loads(o1.packing_json) if o1.packing_json else {}
        assert "factusol_cobro" not in packing1
        o2 = s.scalar(select(Order).where(Order.order_number == "ARTISJ-9537"))
        assert (o2.factusol_invoice_number, o2.factusol_invoice_serie) == ("260070", 2)
        o3 = s.scalar(select(Order).where(Order.order_number == "BOPRIN-99927"))
        assert o3.factusol_invoice_number is None and o3.factusol_invoice_serie is None
        assert str(getattr(o3.invoice_status, "value", o3.invoice_status)) == "not_invoiced"
        # El correcto no se toca.
        ok = s.scalar(select(Order).where(Order.order_number == "FLUXLA-5784"))
        assert (ok.factusol_invoice_number, ok.factusol_invoice_serie) == ("260090", 5)
        # Historial + auditoría en cada pedido corregido.
        hist = s.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == o1.id)).all()
        assert any("corregido" in (h.reason or "") for h in hist)
        audits = s.scalars(select(AuditLog).where(AuditLog.target_id == o3.id)).all()
        assert any(a.action == "erp.invoice_link_removed" for a in audits)
        # No reaparece: el escaneo ya no ve cruzados y el plan queda vacío.
        rescan = scan_invoice_links(s, client, ejercicio="2026")
        assert rescan["totales"].get("cruzado", 0) == 0
        assert plan_relinks(s, client, ejercicio="2026")["plans"] == []
        # Solo lecturas de F_FAC en FACTUSOL.
        assert {t for t, _ in client.calls} == {"F_FAC"}


def test_apply_skips_ambiguous(session_factory) -> None:
    from app.erp.invoice_link_fix import apply_relinks, plan_relinks

    with session_factory() as s:
        _seed(s)
        tables = _tables()
        tables["F_FAC"].append(_fac("1", 260099, 3892, "NEON LED, S.L.", "BOP-099919"))
        client = FakeClient(tables)
        plan = plan_relinks(s, client, ejercicio="2026", only={"BOPRIN-99919"})
        assert plan["plans"][0]["action"] == "ambiguous"
        assert set(plan["plans"][0]["candidatas"]) == {"5-260085", "1-260099"}
        result = apply_relinks(s, plan)
        assert result["done"] == [] and "ambiguo" in result["skipped"][0]["reason"]
        s.expire_all()
        o = s.scalar(select(Order).where(Order.order_number == "BOPRIN-99919"))
        assert o.factusol_invoice_number == "260090"  # intacto


def test_linking_now_stores_serie_with_number(session_factory) -> None:
    """Prevención: `_auto_link_factura` y `attach_invoice` guardan la serie."""
    from app.erp.factusol_albaran import attach_invoice
    from app.integrations.factusol.service import _auto_link_factura

    with session_factory() as s:
        orders = _seed(s)
        o = orders["BOPRIN-99927"]
        _auto_link_factura(s, o, "260085", "2026", ref="BOP-099927", serie=5)
        assert (o.factusol_invoice_number, o.factusol_invoice_serie) == ("260085", 5)
        o2 = orders["ARTISJ-9537"]
        attach_invoice(s, o2, serie=2, codigo=260070, ejercicio="2026", how="por prueba")
        assert (o2.factusol_invoice_number, o2.factusol_invoice_serie) == ("260070", 2)
