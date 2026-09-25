"""Backfill supervisado de ids estables de «Seguimiento (app)» (Fase 1)."""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.models import Order, SeguimientoLegacy
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS_V2
from app.erp.seguimiento_backfill import (
    OrderRef,
    apply_backfill,
    backfill_report,
    bare_numero,
    import_legacy_rows,
    norm_numero,
    propose_one,
)

_NUM = SEGUIMIENTO_COLUMNS_V2.index("Nº pedido")
_CLI = SEGUIMIENTO_COLUMNS_V2.index("Cliente")


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


def _hist_row(numero: str = "", cliente: str = "") -> list[str]:
    fila = [""] * len(SEGUIMIENTO_COLUMNS_V2)
    fila[0] = "Histórico"
    fila[_NUM], fila[_CLI] = numero, cliente
    return fila


def _mk_order(s: Session, *, number: str, cliente: str | None = None) -> str:
    from app.models.crm import Company  # noqa: PLC0415

    company_id = None
    if cliente:
        c = Company(name=cliente)
        s.add(c)
        s.flush()
        company_id = c.id
    o = Order(order_number=number, payment_status="paid", company_id=company_id)
    s.add(o)
    s.flush()
    return o.id


# --- normalización de números --------------------------------------------------


def test_norm_y_bare_numero():
    assert norm_numero("9562.0") == "9562"           # el .0 de la hoja se cae
    assert norm_numero(" BOPRIN-99927 ") == "boprin-99927"
    assert bare_numero("BOPRIN-99927") == "99927"
    assert bare_numero("9562.0") == "9562"
    assert bare_numero("MANUAL-3") == ""             # menos de 4 dígitos: sin desnudo
    assert norm_numero("") == "" and bare_numero("") == ""


# --- el matcher (puro): claro / dudoso / sintético -----------------------------


def _idx(orders: list[OrderRef]):
    from app.erp.seguimiento_backfill import _index_orders

    return _index_orders(orders)


def test_numero_exacto_unico_es_claro():
    by_full, by_bare = _idx([OrderRef("o1", "BOPRIN-99927", "Acme")])
    p = propose_one(legacy_id="L1", numero_raw="BOPRIN-99927", cliente_raw="Acme",
                    by_full=by_full, by_bare=by_bare)
    assert p.status == "proposed" and p.order_id == "o1"


def test_numero_exacto_ambiguo_es_dudoso():
    by_full, by_bare = _idx([OrderRef("o1", "9562", "A"), OrderRef("o2", "9562", "B")])
    p = propose_one(legacy_id="L1", numero_raw="9562.0", cliente_raw="A",
                    by_full=by_full, by_bare=by_bare)
    assert p.status == "dubious" and set(p.candidates) == {"o1", "o2"}


def test_387_desnudo_con_prefijo_distinto_sin_corroborar_cliente_es_dudoso():
    """#387: la fila trae `BOPRIN-99927` pero el único pedido con el desnudo
    `99927` es de OTRA tienda (`FLUXLA-99927`) y el cliente no corrobora → NO se
    casa solo: es dudoso, lo revisa una persona."""
    by_full, by_bare = _idx([OrderRef("o1", "FLUXLA-99927", "Otra Cosa SL")])
    p = propose_one(legacy_id="L1", numero_raw="BOPRIN-99927", cliente_raw="Acme SL",
                    by_full=by_full, by_bare=by_bare)
    assert p.status == "dubious" and p.candidates == ["o1"]


def test_desnudo_unico_con_cliente_que_corrobora_es_claro():
    by_full, by_bare = _idx([OrderRef("o1", "FLUXLA-99927", "Acme SL")])
    p = propose_one(legacy_id="L1", numero_raw="99927", cliente_raw="Acme SL",
                    by_full=by_full, by_bare=by_bare)
    assert p.status == "proposed" and p.order_id == "o1"


def test_sin_numero_o_sin_pedido_detras_es_sintetico():
    by_full, by_bare = _idx([OrderRef("o1", "BOP-1", "A")])
    solo_cliente = propose_one(legacy_id="L1", numero_raw="", cliente_raw="Un cliente",
                               by_full=by_full, by_bare=by_bare)
    assert solo_cliente.status == "synthetic"
    sin_pedido = propose_one(legacy_id="L2", numero_raw="ZZ-8888", cliente_raw="X",
                             by_full=by_full, by_bare=by_bare)
    assert sin_pedido.status == "synthetic"


# --- import + informe + aplicar (con sesión) -----------------------------------


def test_import_legacy_es_verbatim_e_idempotente(factory):
    with factory() as s:
        rows = [_hist_row("9562.0", "Acme"), _hist_row("", "Solo cliente")]
        n = import_legacy_rows(s, rows)
        s.commit()
        assert n == 2
        leg = s.scalars(select(SeguimientoLegacy).order_by(SeguimientoLegacy.row_index)).all()
        assert leg[0].numero_raw == "9562.0"          # sin limpiar
        assert json.loads(leg[0].raw_json)[_NUM] == "9562.0"
        assert leg[1].numero_raw == "" and leg[1].cliente_raw == "Solo cliente"
        # Reimportar no duplica (idempotente por row_index).
        import_legacy_rows(s, rows)
        s.commit()
        assert len(s.scalars(select(SeguimientoLegacy)).all()) == 2


def test_report_lista_las_dudosas_para_revision(factory):
    with factory() as s:
        _mk_order(s, number="FLUXLA-99927", cliente="Otra Cosa SL")   # #387
        _mk_order(s, number="BOP-100", cliente="Acme SL")             # claro
        import_legacy_rows(s, [
            _hist_row("BOPRIN-99927", "Acme SL"),   # dudoso (prefijo distinto)
            _hist_row("BOP-100", "Acme SL"),        # claro
            _hist_row("", "Cliente suelto"),        # sintético
        ])
        s.commit()
        rep = backfill_report(s)
        assert rep["total"] == 3
        assert rep["claras"] == 1 and rep["dudosas"] == 1 and rep["sinteticas"] == 1
        assert rep["dudosas_detalle"][0]["numero"] == "BOPRIN-99927"


def test_apply_solo_escribe_claras_y_sinteticas_deja_dudosas(factory):
    with factory() as s:
        _mk_order(s, number="FLUXLA-99927", cliente="Otra Cosa SL")
        oid = _mk_order(s, number="BOP-100", cliente="Acme SL")
        import_legacy_rows(s, [
            _hist_row("BOPRIN-99927", "Acme SL"),   # dudoso
            _hist_row("BOP-100", "Acme SL"),        # claro
            _hist_row("", "Cliente suelto"),        # sintético
        ])
        s.commit()
        res = apply_backfill(s)
        s.commit()
        assert res["aplicadas"] == 1 and res["sinteticas"] == 1
        assert res["dudosas_pendientes"] == 1
        by_num = {r.numero_raw: r for r in s.scalars(select(SeguimientoLegacy))}
        # La clara casó con el pedido real; la dudosa sigue PENDIENTE de revisión
        # (ni casada ni sintetizada), con su motivo y candidatos en la nota.
        assert by_num["BOP-100"].match_status == "confirmed"
        assert by_num["BOP-100"].matched_order_id == oid
        assert by_num["BOPRIN-99927"].match_status == "pending"
        assert by_num["BOPRIN-99927"].matched_order_id is None
        assert "candidatos" in (by_num["BOPRIN-99927"].match_note or "")


def test_apply_confirma_una_dudosa_que_bart_revisa(factory):
    with factory() as s:
        oid = _mk_order(s, number="FLUXLA-99927", cliente="Otra Cosa SL")
        import_legacy_rows(s, [_hist_row("BOPRIN-99927", "Acme SL")])  # dudoso
        s.commit()
        dudosa = s.scalars(select(SeguimientoLegacy)).one()
        res = apply_backfill(s, confirmar={dudosa.id})
        s.commit()
        assert res["confirmadas_a_mano"] == 1
        s.refresh(dudosa)
        assert dudosa.match_status == "confirmed" and dudosa.matched_order_id == oid


# --- arreglo Fase 2: dudosas pendientes + --confirm que sobrescribe ------------


def test_parse_confirmar_formas():
    from app.erp.seguimiento_backfill import parse_confirmar

    assert parse_confirmar("a, b=ord-1 ,c=none,,") == {"a": "", "b": "ord-1", "c": "none"}
    assert parse_confirmar("") == {}


def test_una_dudosa_sigue_pendiente_en_corridas_sucesivas(factory):
    """Bug Fase 1: tras un `--apply` la dudosa salía del informe y ya no se
    podía confirmar. Ahora sigue pendiente y se vuelve a proponer."""
    with factory() as s:
        _mk_order(s, number="FLUXLA-99927", cliente="Otra Cosa SL")
        import_legacy_rows(s, [_hist_row("BOPRIN-99927", "Acme SL")])
        s.commit()
        for _ in range(2):
            res = apply_backfill(s)
            s.commit()
            assert res["dudosas_pendientes"] == 1 and res["sinteticas"] == 0
            assert backfill_report(s)["dudosas"] == 1        # sigue en el informe
        fila = s.scalars(select(SeguimientoLegacy)).one()
        assert fila.match_status == "pending" and fila.matched_order_id is None


def test_filas_dubious_de_corridas_antiguas_se_vuelven_a_proponer(factory):
    with factory() as s:
        _mk_order(s, number="FLUXLA-99927", cliente="Otra Cosa SL")
        import_legacy_rows(s, [_hist_row("BOPRIN-99927", "Acme SL")])
        s.commit()
        fila = s.scalars(select(SeguimientoLegacy)).one()
        fila.match_status = "dubious"                      # estado de una corrida vieja
        s.commit()
        assert backfill_report(s)["dudosas"] == 1


def test_confirm_sobrescribe_una_fila_ya_resuelta(factory):
    """Bug Fase 1: `--confirm` no reenganchaba una fila ya resuelta. Ahora manda
    sobre el estado actual: sintética → casada con un pedido, y casada → sin
    pedido."""
    with factory() as s:
        oid = _mk_order(s, number="BOP-100", cliente="Acme SL")
        import_legacy_rows(s, [_hist_row("", "Cliente suelto"),      # sintética
                               _hist_row("BOP-100", "Acme SL")])     # clara
        s.commit()
        apply_backfill(s)
        s.commit()
        suelta, clara = s.scalars(
            select(SeguimientoLegacy).order_by(SeguimientoLegacy.row_index)).all()
        assert suelta.match_status == "synthetic" and clara.match_status == "confirmed"

        res = apply_backfill(s, confirmar={suelta.id: oid, clara.id: "none"})
        s.commit()
        assert res["confirmadas_a_mano"] == 2 and res["errores"] == []
        s.refresh(suelta)
        s.refresh(clara)
        assert suelta.match_status == "confirmed" and suelta.matched_order_id == oid
        assert clara.match_status == "synthetic" and clara.matched_order_id is None
        # Idempotente: otra corrida sin --confirm no deshace la corrección.
        apply_backfill(s)
        s.commit()
        s.refresh(suelta)
        s.refresh(clara)
        assert suelta.matched_order_id == oid and clara.match_status == "synthetic"


def test_confirm_con_errores_no_toca_nada(factory):
    with factory() as s:
        _mk_order(s, number="9562", cliente="A")
        _mk_order(s, number="9562", cliente="B")                       # ambiguo
        import_legacy_rows(s, [_hist_row("9562", "A")])
        s.commit()
        fila = s.scalars(select(SeguimientoLegacy)).one()
        res = apply_backfill(s, confirmar={fila.id: "", "no-existe": "", fila.id + "x": ""})
        s.commit()
        assert res["confirmadas_a_mano"] == 0
        assert len(res["errores"]) == 3
        s.refresh(fila)
        assert fila.match_status == "pending"                          # sin tocar
        res = apply_backfill(s, confirmar={fila.id: "ord-inexistente"})
        assert any("no existe" in e for e in res["errores"])
