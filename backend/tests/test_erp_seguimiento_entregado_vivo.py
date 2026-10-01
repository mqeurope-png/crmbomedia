"""Seguimiento — ENTREGADO NO ES COMPLETADO (bug del 01/10/2026).

Cuatro pedidos vivos pasaron a entregados (sin «Marcar completado») y sus
filas desaparecieron de «Seguimiento (app)»: la selección «en curso» los
sacaba al estar entregados y facturados, y los completados solo recogen los
que tienen `completed_at`. Ahora:

- un pedido sigue en la zona viva hasta que se marca completado (o se quita,
  se anula o se gestiona fuera), con Envío «Entregado» si ya lo está;
- al marcarlo completado baja a los completados;
- invariante del volcado: una fila de BoHub que estaba en la hoja nunca
  desaparece sin uno de esos motivos (se conserva y queda un aviso).

Sin red: la hoja es un doble en memoria; la selección es la real de la BD.
"""
from __future__ import annotations

import copy
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp.api.seguimiento import drive_completados_rows, drive_live_rows
from app.erp.drive_managed import DEFAULT_MANAGED_TAB, is_separator, push_managed_tabs
from app.erp.models import (
    InvoiceStatus,
    Order,
    OrderSource,
    PreparationStatus,
    TransportStatus,
)
from app.erp.seguimiento import ID_INDEX, SEGUIMIENTO_COLUMNS_V2
from tests.test_erp_seguimiento_courier import FakeTabs

TAB = DEFAULT_MANAGED_TAB
HISTORICA = "Pedidos Bomedia 2020-2026"
ENVIO = SEGUIMIENTO_COLUMNS_V2.index("Envío")


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


def _entregado(s: Session, numero: str, **kw: object) -> Order:
    """Pedido entregado y facturado, SIN completar (como los cuatro del bug)."""
    o = Order(order_number=numero, payment_status="paid",
              external_source=OrderSource.FACTUSOL_FACTURA,
              preparation_status=PreparationStatus.PACKED,
              transport_status=TransportStatus.DELIVERED,
              invoice_status=InvoiceStatus.INVOICED_BY_ERP,
              factusol_invoice_number=f"2-{numero[-6:]}", **kw)
    s.add(o)
    s.flush()
    return o


def _pasada(s: Session, sheets: FakeTabs, **kw: object) -> dict:
    res = push_managed_tabs(s, sheets, drive_live_rows(s),
                            completados=drive_completados_rows(s), **kw)
    s.commit()
    return res


def _fila(sheets: FakeTabs, order_id: str) -> tuple[int, list]:
    filas = sheets.written[TAB]
    return next((i, f) for i, f in enumerate(filas)
                if len(f) > ID_INDEX and f[ID_INDEX] == order_id)


def _separador(sheets: FakeTabs) -> int | None:
    return next((i for i, f in enumerate(sheets.written[TAB]) if is_separator(f)), None)


def test_entregado_sin_completar_sigue_en_la_zona_viva_con_envio_entregado(factory):
    with factory() as s:
        o = _entregado(s, "FAC-2-526109")
        s.commit()
        assert [r["order_number"] for r in drive_live_rows(s)] == ["FAC-2-526109"]
        sheets = FakeTabs({HISTORICA: []})
        _pasada(s, sheets)
        i, fila = _fila(sheets, o.id)
        assert fila[ENVIO] == "Entregado"
        sep = _separador(sheets)
        assert sep is None or i < sep                       # zona viva


def test_al_marcarlo_completado_baja_a_los_completados(factory):
    with factory() as s:
        o = _entregado(s, "ARTISJ-9492")
        s.commit()
        sheets = FakeTabs({HISTORICA: []})
        _pasada(s, sheets)
        o.completed_at = datetime.now(UTC)
        s.commit()
        assert drive_live_rows(s) == []
        _pasada(s, sheets)
        i, fila = _fila(sheets, o.id)
        sep = _separador(sheets)
        assert sep is not None and i > sep                  # bajo el separador
        assert fila[0] == "Completado"


def test_una_fila_de_bohub_que_estaba_en_la_hoja_nunca_desaparece(factory):
    """Si la selección (por un fallo) deja fuera un pedido que estaba en la hoja y
    que no se ha quitado, anulado, completado ni gestionado fuera, su fila se
    conserva y queda el aviso en el resumen."""
    with factory() as s:
        o = _entregado(s, "FLUXLA-5787")
        s.commit()
        sheets = FakeTabs({HISTORICA: []})
        _pasada(s, sheets)
        antes = copy.deepcopy(sheets.written[TAB])
        res = push_managed_tabs(s, sheets, [], completados=[])     # selección «rota»
        s.commit()
        assert res["espejo"]["filas_rescatadas"] == ["FLUXLA-5787"]
        assert any(len(f) > ID_INDEX and f[ID_INDEX] == o.id for f in sheets.written[TAB])
        assert len(sheets.written[TAB]) == len(antes)
        # La vista previa avisa igual (sin escribir).
        previa = push_managed_tabs(s, sheets, [], completados=[], dry_run=True)
        assert previa["espejo"]["filas_rescatadas"] == ["FLUXLA-5787"]


def test_quitarlo_del_seguimiento_si_lo_saca_de_la_hoja(factory):
    """Las salidas legítimas no se «rescatan»: quitado del seguimiento."""
    with factory() as s:
        o = _entregado(s, "ARTISJ-9489")
        s.commit()
        sheets = FakeTabs({HISTORICA: []})
        _pasada(s, sheets)
        o.seguimiento_excluded_at = datetime.now(UTC)
        s.commit()
        res = _pasada(s, sheets)
        assert "filas_rescatadas" not in res["espejo"]
        assert not any(len(f) > ID_INDEX and f[ID_INDEX] == o.id
                       for f in sheets.written[TAB])


def test_gestionado_fuera_tambien_sale_sin_aviso(factory):
    with factory() as s:
        o = _entregado(s, "BOP-777001")
        s.commit()
        sheets = FakeTabs({HISTORICA: []})
        _pasada(s, sheets)
        o.externally_processed_at = datetime.now(UTC)
        s.commit()
        res = _pasada(s, sheets)
        assert "filas_rescatadas" not in res["espejo"]
        assert not any(len(f) > ID_INDEX and f[ID_INDEX] == o.id
                       for f in sheets.written[TAB])
