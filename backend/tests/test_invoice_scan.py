"""Escaneo SOLO LECTURA de facturas con líneas contaminadas (secuela del bug de
emisión #382). Prueba el detector (Σ líneas F_LFA por clave compuesta ≠ base
neta de la cabecera), la clasificación de origen (BoHub vs a mano), el corte
por el fix #382, el mapa de emisión desde el historial del CRM, y el CSV.

El detector es lógica pura sobre diccionarios (sin tocar FACTUSOL): las pruebas
construyen las filas de F_FAC / F_LFA a mano.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.invoice_scan import (
    FIX_382_AT,
    EmitInfo,
    bohub_emit_map,
    build_report,
    format_report,
    header_base,
    write_csv,
)
from app.erp.models import Order, OrderSource, OrderStatusHistory, StatusDomain


def _fac(codigo: int, serie: str = "5", *, net: float = 100.0, **over: Any) -> dict[str, Any]:
    """Cabecera F_FAC. Por defecto lleva origen BoHub (PEDFAC) y NET1FAC=net."""
    row: dict[str, Any] = {
        "TIPFAC": serie, "CODFAC": codigo, "CNOFAC": "DUPLICODER, S.L.",
        "FECFAC": "2026-08-01T00:00:00",
        "NET1FAC": net, "NET2FAC": 0.0, "NET3FAC": 0.0, "NET4FAC": 0.0,
        "REFFAC": f"BOP-{codigo:06d}", "PEDFAC": str(codigo),
    }
    row.update(over)
    return row


def _lfa(codigo: int, total: float, serie: str = "5") -> dict[str, Any]:
    return {"TIPLFA": serie, "CODLFA": codigo, "TOTLFA": total}


# --- base neta de la cabecera ------------------------------------------------


def test_header_base_sums_net_bands_not_bas_nor_portes() -> None:
    # NET1=80 (mercancía) + NET4=20 (exenta) = 100. BAS1=99 (=NET+portes) e
    # IPOR1=19 NO cuentan: los portes no son línea de F_LFA.
    fac = _fac(1, net=0.0, NET1FAC=80.0, NET4FAC=20.0, BAS1FAC=99.0, IPOR1FAC=19.0)
    assert header_base(fac) == 100.0


# --- detector ----------------------------------------------------------------


def test_factura_limpia_no_contaminada() -> None:
    fac = [_fac(100, net=280.0)]
    lfa = [_lfa(100, 100.0), _lfa(100, 180.0)]  # Σ = 280 = base
    report = build_report(fac, lfa, ejercicio="2026")
    (row,) = report.rows
    assert row.origen == "bohub"
    assert row.suma_lineas == 280.0 and row.base_neta == 280.0
    assert row.diferencia == 0.0
    assert row.contaminada is False
    assert report.contaminadas == []


def test_factura_contaminada_por_linea_de_mas() -> None:
    # La línea contaminante lleva la MISMA serie y nº que la factura (así la
    # escribía el bug), así que entra en la suma → base 100, Σ líneas 150.
    fac = [_fac(101, net=100.0)]
    lfa = [_lfa(101, 100.0), _lfa(101, 50.0)]
    report = build_report(fac, lfa, ejercicio="2026")
    (row,) = report.rows
    assert row.contaminada is True
    assert row.suma_lineas == 150.0 and row.base_neta == 100.0
    assert row.diferencia == 50.0
    assert row.num_lineas == 2


def test_clave_compuesta_no_cuenta_lineas_de_otra_serie() -> None:
    # Factura 5-102 con su línea (100) y una HOMÓNIMA de la serie 1 (999): la de
    # la serie 1 NO cuenta para la 5 → limpia.
    fac = [_fac(102, "5", net=100.0)]
    lfa = [_lfa(102, 100.0, "5"), _lfa(102, 999.0, "1")]
    report = build_report(fac, lfa, ejercicio="2026")
    (row,) = report.rows
    assert row.suma_lineas == 100.0
    assert row.num_lineas == 1
    assert row.contaminada is False


def test_tolerancia_de_redondeo() -> None:
    fac = [_fac(103, net=100.0)]
    lfa = [_lfa(103, 100.004)]  # dentro de 0.005 → limpia
    assert build_report(fac, lfa, ejercicio="2026").rows[0].contaminada is False
    lfa2 = [_lfa(103, 100.02)]  # fuera → contaminada
    assert build_report(fac, lfa2, ejercicio="2026").rows[0].contaminada is True


# --- origen + corte por el fix #382 ------------------------------------------


def test_origen_manual_sin_senal_bohub_y_exclusion() -> None:
    # Sin PEDFAC ni REFFAC con patrón BoHub → creada a mano.
    fac = [_fac(200, net=100.0, PEDFAC="", REFFAC="")]
    lfa = [_lfa(200, 130.0)]
    incl = build_report(fac, lfa, ejercicio="2026", include_manual=True)
    assert incl.rows[0].origen == "manual"
    assert incl.rows[0].contaminada is True   # se detecta igual, pero aparte
    excl = build_report(fac, lfa, ejercicio="2026", include_manual=False)
    assert excl.rows == []


def test_origen_bohub_por_pedfac_sin_crm_usa_fecfac() -> None:
    fac = [_fac(201, net=100.0, REFFAC="", FECFAC="2026-08-15T00:00:00")]  # PEDFAC sí
    report = build_report(fac, [_lfa(201, 120.0)], ejercicio="2026")
    row = report.rows[0]
    assert row.origen == "bohub"
    assert row.fecha_origen == "fecfac"
    assert row.antes_del_fix is True   # 2026-08-15 < 2026-09-10


def test_corte_antes_y_despues_del_fix_por_emision_crm() -> None:
    antes = EmitInfo("PRO-1", datetime(2026, 8, 1, tzinfo=timezone.utc))
    despues = EmitInfo("PRO-2", datetime(2026, 9, 20, tzinfo=timezone.utc))
    fac = [_fac(300, net=100.0), _fac(301, net=100.0)]
    lfa = [_lfa(300, 150.0), _lfa(301, 150.0)]  # ambas contaminadas
    emit_map = {(5, 300): antes, (5, 301): despues}
    report = build_report(fac, lfa, ejercicio="2026", bohub_map=emit_map)
    by_num = {r.codigo: r for r in report.rows}
    assert by_num[300].antes_del_fix is True and by_num[300].order_number == "PRO-1"
    assert by_num[301].antes_del_fix is False and by_num[301].fecha_origen == "emision_crm"
    # El informe avisa de la posterior al fix.
    text = format_report(report)
    assert "POSTERIORES al fix #382: 1" in text
    assert "REVISAR" in text


def test_fix_instant_es_el_del_commit_382() -> None:
    assert FIX_382_AT == datetime(2026, 9, 10, 9, 54, 35, tzinfo=timezone.utc)


# --- CSV ----------------------------------------------------------------------


def test_csv_una_fila_por_contaminada(tmp_path) -> None:
    fac = [_fac(400, net=100.0), _fac(401, net=100.0)]
    lfa = [_lfa(400, 100.0), _lfa(401, 175.0)]  # 400 limpia, 401 contaminada
    report = build_report(fac, lfa, ejercicio="2026")
    path = write_csv(report, tmp_path / "cont.csv")
    body = path.read_text(encoding="utf-8").splitlines()
    assert body[0].startswith("numero;serie;codigo;cliente")
    # Solo la contaminada (401), no la limpia (400).
    data_rows = [ln for ln in body[1:] if ln.strip()]
    assert len(data_rows) == 1
    assert data_rows[0].startswith("5-000401;5;401;")
    assert "75.0" in data_rows[0]  # diferencia


# --- mapa de emisión desde el CRM (historial de estado) ----------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


def _order(s: Session, oid: str, number: str) -> None:
    s.add(Order(
        id=oid, order_number=number, external_source=OrderSource.MANUAL,
        total_amount=100.0, currency="EUR", payment_status="paid",
        preparation_status="pending_review",
    ))


def _emit_history(
    s: Session, oid: str, *, serie: int, codfac: int, at: datetime, reason: str,
    with_serie: bool = True,
) -> None:
    meta = {"factusol_codfac": codfac}
    if with_serie:
        meta["factusol_serie"] = serie
    s.add(OrderStatusHistory(
        order_id=oid, domain=StatusDomain.INVOICE, to_status="invoiced_by_erp",
        changed_at=at, reason=reason, metadata_json=json.dumps(meta),
    ))


def test_bohub_emit_map_solo_emitidas_no_autovinculadas(session_factory) -> None:
    with session_factory() as s:
        _order(s, "o1", "PRO-000001")
        _order(s, "o2", "PRO-000002")
        # Emitida por BoHub (escribe líneas) → cuenta.
        _emit_history(s, "o1", serie=5, codfac=260001,
                      at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                      reason="Factura emitida en FACTUSOL")
        # Auto-vinculada (NO escribe líneas) → NO cuenta.
        _emit_history(s, "o2", serie=2, codfac=520002,
                      at=datetime(2026, 8, 2, tzinfo=timezone.utc),
                      reason="Factura localizada en FACTUSOL (vinculada automáticamente)")
        s.commit()
        m = bohub_emit_map(s)
    assert (5, 260001) in m
    assert m[(5, 260001)].order_number == "PRO-000001"
    assert m[(5, 260001)].emitted_at == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert (2, 520002) not in m
    assert len(m) == 1


def test_bohub_emit_map_used_by_build_report(session_factory) -> None:
    with session_factory() as s:
        _order(s, "o1", "PRO-000009")
        _emit_history(s, "o1", serie=5, codfac=260009,
                      at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                      reason="Factura emitida en FACTUSOL")
        s.commit()
        emit_map = bohub_emit_map(s)
    # Factura sin señal FACTUSOL-side (ni PEDFAC ni REFFAC) pero en el mapa CRM.
    fac = [_fac(260009, "5", net=100.0, PEDFAC="", REFFAC="")]
    report = build_report(fac, [_lfa(260009, 140.0)], ejercicio="2026", bohub_map=emit_map)
    row = report.rows[0]
    assert row.origen == "bohub"
    assert row.order_number == "PRO-000009"
    assert row.fecha_origen == "emision_crm"
    assert row.antes_del_fix is True
    assert row.contaminada is True
