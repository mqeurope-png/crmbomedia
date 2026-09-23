"""ERP · WooCommerce — borrado definitivo de los carritos que se colaron
(`scripts.limpiar_pedidos_web_no_procesados`).

Solo web con `woo_status` pending / on-hold / failed / draft / checkout-draft y
SIN huella fiscal ni de trabajo. Con hijas (líneas, historial, fila de Drive)
y sin dejar huérfanos. Dry-run no toca nada; idempotente.
"""
from __future__ import annotations

import io
from collections.abc import Generator
from contextlib import redirect_stdout
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.erp.models import (
    ErpDriveSyncRow,
    ErpException,
    ExceptionStatus,
    ExceptionType,
    InvoiceStatus,
    Order,
    OrderLine,
    OrderSource,
    OrderStatusHistory,
    PaymentStatus,
    PreparationStatus,
    ShipmentPackage,
)
from app.erp.models.orders import StatusDomain
from app.integrations.woocommerce.purge import (
    PURGE_STATUSES,
    purge_unprocessed_web_orders,
)
from app.models.crm import Company, ExternalSystem
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
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
    s: Session, number: str, *, woo_status: str | None, store: IntegrationAccount | None,
    source: OrderSource = OrderSource.WOOCOMMERCE, con_hijas: bool = True, **extra,
) -> Order:
    comp = Company(name=f"Cliente {number}")
    s.add(comp)
    s.flush()
    o = Order(
        external_source=source, external_id=number.split("-")[-1],
        store_id=store.id if store else None, order_number=number, company_id=comp.id,
        woo_status=woo_status, total_amount=50.0, currency="EUR",
        placed_at=datetime(2026, 9, 1, tzinfo=UTC), **extra,
    )
    s.add(o)
    s.flush()
    if con_hijas:
        s.add(OrderLine(order_id=o.id, product_sku="SKU-1", description="Tinta",
                        quantity=1, unit_price=50.0))
        s.add(OrderStatusHistory(order_id=o.id, domain=StatusDomain.PREPARATION,
                                 from_status=None, to_status="pending_review",
                                 changed_at=datetime(2026, 9, 1, tzinfo=UTC)))
        s.add(ErpDriveSyncRow(order_id=o.id, row_key=number, synced_at=None))
        s.flush()
    return o


def _count(s: Session, model) -> int:
    return int(s.scalar(select(func.count()).select_from(model)) or 0)


def _numeros(rows: list[dict]) -> list[str]:
    return sorted(r["order_number"] for r in rows)


# --- qué es candidato y qué no --------------------------------------------------


def test_solo_los_carritos_sin_huella_son_candidatos(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        for i, status in enumerate(
            ["pending", "on-hold", "failed", "draft", "checkout-draft", "wc-on-hold"],
        ):
            _order(s, f"BOPRIN-1{i}", woo_status=status, store=st)
        # Estos NO: han pasado por caja, están cancelados, o no se conocen.
        for i, status in enumerate(["processing", "completed", "refunded", "cancelled", None]):
            _order(s, f"BOPRIN-2{i}", woo_status=status, store=st)
        # Ni un manual ni una muestra, digan lo que digan.
        _order(s, "MANUAL-000001", woo_status=None, store=None, source=OrderSource.MANUAL)
        _order(s, "BOPRIN-99", woo_status="pending", store=st, order_kind="sample")
        s.commit()
        res = purge_unprocessed_web_orders(s, dry_run=True)
    assert res["ok"] and res["preview"] is True
    assert _numeros(res["candidates"]) == [
        "BOPRIN-10", "BOPRIN-11", "BOPRIN-12", "BOPRIN-13", "BOPRIN-14", "BOPRIN-15",
    ]
    assert res["por_estado"] == {"pending": 1, "on_hold": 2, "failed": 1,
                                 "draft": 1, "checkout_draft": 1}
    assert res["total_importe"] == 300.0
    assert res["protected"] == []
    assert "cancelled" not in PURGE_STATUSES


@pytest.mark.parametrize(
    ("campo", "motivo"),
    [
        ({"factusol_invoice_number": "5-260001"}, "facturado"),
        ({"invoice_status": InvoiceStatus.GENERATED}, "facturado"),
        ({"payment_status": PaymentStatus.PAID}, "cobrado/pagado"),
        ({"factusol_albaran_number": "5-9001"}, "albarán FACTUSOL"),
        ({"factusol_cobro_status": "cobrada"}, "cobro FACTUSOL"),
        ({"serial_number": "SN-7"}, "nº de serie"),
        ({"tracking_number": "1Z-ABC"}, "tracking"),
        ({"whiterip_license": "4829"}, "WhiteRIP"),
        ({"preparation_status": PreparationStatus.PREPARING}, "en preparación"),
    ],
)
def test_una_huella_fiscal_o_de_trabajo_protege(session_factory, campo, motivo) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, "BOPRIN-1", woo_status="on-hold", store=st, **campo)
        s.commit()
        res = purge_unprocessed_web_orders(s, dry_run=False)
        assert res["candidates"] == [] and res["deleted"] == 0
        assert [p["order_number"] for p in res["protected"]] == ["BOPRIN-1"]
        assert any(m.startswith(motivo) for m in res["protected"][0]["motivos"])
        assert s.scalar(select(Order).where(Order.order_number == "BOPRIN-1")) is not None


def test_un_paquete_o_una_excepcion_tambien_protegen(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        con_paquete = _order(s, "BOPRIN-1", woo_status="pending", store=st)
        s.add(ShipmentPackage(order_id=con_paquete.id, weight_kg=1.0,
                              height_cm=10, width_cm=10, depth_cm=10))
        con_excepcion = _order(s, "BOPRIN-2", woo_status="pending", store=st)
        s.add(ErpException(order_id=con_excepcion.id, type=ExceptionType.SAT_ISSUE,
                           status=ExceptionStatus.OPEN))
        s.commit()
        res = purge_unprocessed_web_orders(s, dry_run=False)
        assert res["deleted"] == 0
        motivos = {p["order_number"]: p["motivos"] for p in res["protected"]}
        assert "albarán/envío" in motivos["BOPRIN-1"]
        assert "excepción/tarea SAT" in motivos["BOPRIN-2"]


# --- borrado en cascada, sin huérfanos, idempotente -----------------------------


def test_dry_run_no_borra_nada(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, "BOPRIN-1", woo_status="pending", store=st)
        s.commit()
        res = purge_unprocessed_web_orders(s, dry_run=True)
        assert [c["order_number"] for c in res["candidates"]] == ["BOPRIN-1"]
        assert res["deleted"] == 0
        assert _count(s, Order) == 1 and _count(s, OrderLine) == 1


def test_apply_borra_con_sus_hijas_y_no_deja_huerfanos(session_factory) -> None:
    with session_factory() as s:
        st = _store(s)
        _order(s, "BOPRIN-1", woo_status="pending", store=st)
        _order(s, "BOPRIN-2", woo_status="on-hold", store=st)
        vivo = _order(s, "BOPRIN-3", woo_status="processing", store=st)
        s.commit()
        antes = {m: _count(s, m) for m in (Order, OrderLine, OrderStatusHistory, ErpDriveSyncRow)}
        assert antes == {Order: 3, OrderLine: 3, OrderStatusHistory: 3, ErpDriveSyncRow: 3}

        res = purge_unprocessed_web_orders(s, dry_run=False)
        assert res["deleted"] == 2
        assert res["deleted_rows"]["orders"] == 2
        assert res["deleted_rows"]["order_lines"] == 2
        assert res["deleted_rows"]["order_status_history"] == 2
        assert res["deleted_rows"]["erp_drive_sync_rows"] == 2

        s.expire_all()
        assert [o.order_number for o in s.scalars(select(Order))] == ["BOPRIN-3"]
        # Ninguna hija huérfana: todas las que quedan cuelgan del vivo.
        for model in (OrderLine, OrderStatusHistory, ErpDriveSyncRow):
            assert [r.order_id for r in s.scalars(select(model))] == [vivo.id]

        # Idempotente: la segunda pasada no encuentra nada.
        otra = purge_unprocessed_web_orders(s, dry_run=False)
        assert otra["candidates"] == [] and otra["deleted"] == 0


def test_filtra_por_tienda(session_factory) -> None:
    with session_factory() as s:
        a, b = _store(s, "boprint"), _store(s, "fluxlab")
        _order(s, "BOPRIN-1", woo_status="pending", store=a)
        _order(s, "FLUXLA-1", woo_status="pending", store=b)
        s.commit()
        res = purge_unprocessed_web_orders(s, dry_run=False, store_account_id="fluxlab")
        assert [c["order_number"] for c in res["candidates"]] == ["FLUXLA-1"]
        s.expire_all()
        assert [o.order_number for o in s.scalars(select(Order))] == ["BOPRIN-1"]
        assert purge_unprocessed_web_orders(s, dry_run=True, store_account_id="nope")["ok"] is False


# --- la CLI ---------------------------------------------------------------------


def test_cli_dry_run_lista_y_no_borra(monkeypatch) -> None:
    import scripts.limpiar_pedidos_web_no_procesados as cli

    resumen = {
        "ok": True, "preview": True, "total_importe": 75.5,
        "candidates": [{"order_number": "BOPRIN-1", "woo_status": "on-hold",
                        "importe": 75.5, "cliente": "Acme", "motivos": []}],
        "por_estado": {"on_hold": 1}, "protected": [], "protegidos_por_motivo": {},
        "deleted": 0, "deleted_rows": {},
    }
    llamadas: list[bool] = []

    def fake_run(dry_run, store):
        llamadas.append(dry_run)
        return resumen

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr("sys.argv", ["limpiar", "--dry-run"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    out = buf.getvalue()
    assert llamadas == [True]
    assert "PREVISUALIZACIÓN" in out and "BOPRIN-1" in out and "75.50" in out
    assert "Repite con --apply" in out


def test_cli_apply_exige_la_palabra_de_confirmacion(monkeypatch) -> None:
    import scripts.limpiar_pedidos_web_no_procesados as cli

    resumen = {
        "ok": True, "preview": True, "total_importe": 1.0,
        "candidates": [{"order_number": "BOPRIN-1", "woo_status": "pending",
                        "importe": 1.0, "cliente": "Acme", "motivos": []}],
        "por_estado": {"pending": 1}, "protected": [], "protegidos_por_motivo": {},
        "deleted": 0, "deleted_rows": {},
    }
    llamadas: list[bool] = []
    monkeypatch.setattr(cli, "_run", lambda dry_run, store: (llamadas.append(dry_run), resumen)[1])
    monkeypatch.setattr("sys.argv", ["limpiar", "--apply"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    with pytest.raises(SystemExit) as exc, redirect_stdout(io.StringIO()):
        cli.main()
    assert exc.value.code == 1
    assert llamadas == [True]              # solo la previsualización; no borró

    # Con la palabra, borra (y con --yes, sin preguntar).
    llamadas.clear()
    monkeypatch.setattr("builtins.input", lambda _prompt: cli.CONFIRM_WORD)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    assert llamadas == [True, False]
    assert "copia de seguridad" in buf.getvalue()
