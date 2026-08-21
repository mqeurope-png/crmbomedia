"""BoHub ERP Fase C · C-2-fix2 — mapper + servicio FACTUSOL (F_PCL → F_FAC,
detección de factura/albarán existente, auto-vinculación).

El cliente FACTUSOL se sustituye por un fake en memoria — sin red. La BD es
SQLite in-memory.
"""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.models import InvoiceStatus, Order
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.mapper import (
    FacturaOptions,
    lpc_row_to_lfa_payload,
    pcl_row_to_fac_payload,
)
from app.integrations.factusol.service import (
    check_factusol_status,
    emit_invoice,
    find_pcl_by_order,
    get_and_link_factusol_status,
    next_codfac,
)


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


class FakeFactusol:
    """Cliente FACTUSOL simulado: sirve F_PCL/F_LPC/F_FAC/F_ALB y registra
    escrituras. Distingue las dos consultas a F_FAC: por REFFAC (¿existe ya la
    factura?) vs. ORDER BY CODFAC DESC (siguiente número)."""

    def __init__(self, *, pcl_row=None, lpc_rows=None, f_fac_last=None,
                 fac_by_ref=None, alb_by_ref=None, fail_on_line=None,
                 f_fac_rows=None, f_fac_serie="5"):
        self.default_ejercicio = "2026"
        self.writes: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []
        self.updates: list[tuple[str, dict]] = []
        self.pcl_filters: list[str] = []
        self.fac_filters: list[str] = []
        self.alb_filters: list[str] = []
        self._pcl_row = pcl_row
        self._lpc_rows = lpc_rows or []
        self._f_fac_last = f_fac_last
        #: ERP-E2 — varias facturas de series distintas, para comprobar que
        #: `next_codfac` numera dentro del rango de la serie pedida.
        self._f_fac_rows = f_fac_rows
        #: Serie (TIPFAC) que se atribuye a las filas de `f_fac_last`.
        self._f_fac_serie = f_fac_serie
        self._fac_by_ref = fac_by_ref or []
        self._alb_by_ref = alb_by_ref or []
        self._fail_on_line = fail_on_line
        self._line_calls = 0

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        if tabla == "F_FAC":
            if "REFFAC" in filtro:
                self.fac_filters.append(filtro)
                return list(self._fac_by_ref)
            # next_codfac: 1=1 ORDER BY CODFAC DESC
            if self._f_fac_rows is not None:
                return list(self._f_fac_rows)
            if self._f_fac_last is None:
                return []
            return [{"CODFAC": self._f_fac_last, "TIPFAC": self._f_fac_serie}]
        if tabla == "F_ALB":
            self.alb_filters.append(filtro)
            return list(self._alb_by_ref)
        if tabla == "F_PCL":
            self.pcl_filters.append(filtro)
            return [self._pcl_row] if self._pcl_row is not None else []
        if tabla == "F_LPC":
            return list(self._lpc_rows)
        return []

    def write_record(self, tabla, data, *, ejercicio=None):
        if tabla == "F_LFA":
            self._line_calls += 1
            if self._fail_on_line and self._line_calls == self._fail_on_line:
                raise FactusolError("line write failed", status=500)
        self.writes.append((tabla, data))
        return {"ok": True}

    def update_record(self, tabla, data, *, ejercicio=None):
        self.updates.append((tabla, data))
        return {"ok": True}

    def delete_records(self, tabla, filtro, *, ejercicio=None):
        self.deletes.append((tabla, filtro))
        return {"ok": True}


def _pcl_row(**over) -> dict:
    base = {
        # TIPPCL = la SERIE del pedido (5 = Streamtec), la que hereda la
        # factura. Es lo que la app Woo→FACTUSOL deja puesto.
        "CODPCL": 2765, "TIPPCL": "5", "REFPCL": "BOP-099866", "CLIPCL": 2458,
        "CNOPCL": "DUPLICODER, S.L.", "TOTPCL": 186.34, "FECPCL": "2026-08-01",
        "NET1PCL": 100.0, "PIVA1PCL": 21.0, "IIVA1PCL": 21.0,
        "NET2PCL": 40.0, "PIVA2PCL": 10.0, "IIVA2PCL": 4.0,
        "NET3PCL": 10.0, "PIVA3PCL": 4.0, "IIVA3PCL": 0.4,
        # columnas de pedido que NO deben copiarse:
        "ESTPCL": "S", "USUPCL": "admin", "HORPCL": "10:00",
    }
    base.update(over)
    return base


def _order(s: Session, *, number="BOPRIN-99866", invoice_status=None) -> str:
    o = Order(order_number=number, total_amount=186.34)
    if invoice_status is not None:
        o.invoice_status = invoice_status
    s.add(o)
    s.commit()
    return o.id


# --- next_codfac ------------------------------------------------------------


def test_next_codfac_empty_series_starts_at_one():
    """ERP-E2-fix1: la serie es el TIPO, no un rango. Una serie sin facturas
    arranca su correlativo en 1 (primer documento de esa empresa)."""
    assert next_codfac(FakeFactusol(f_fac_last=None), "2026", 5) == "1"


def test_next_codfac_is_last_plus_one():
    assert next_codfac(FakeFactusol(f_fac_last=526066), "2026", 5) == "526067"


def test_next_codfac_counts_within_its_series_only():
    """Cada serie lleva su propio contador: el número solo es único por
    `(TIPFAC, CODFAC)`. Bart lo confirmó — hay una factura `2-526082` y un
    pedido `5-000005`, así que el número NO delata la serie."""
    client = FakeFactusol(f_fac_rows=[
        {"TIPFAC": "2", "CODFAC": 526082},   # serie 2, número ALTO
        {"TIPFAC": "5", "CODFAC": 5},        # serie 5, número BAJO
        {"TIPFAC": "5", "CODFAC": 4},
        {"TIPFAC": "1", "CODFAC": 260695},
    ])
    assert next_codfac(client, "2026", 2) == "526083"
    assert next_codfac(client, "2026", 5) == "6"
    assert next_codfac(client, "2026", 1) == "260696"
    # Serie sin facturas → su propio contador desde 1, sin heredar de otra.
    assert next_codfac(client, "2026", 7) == "1"


def test_next_codfac_tolerates_int_and_str_tipfac():
    """No sabemos si TIPFAC llega como '5' o como 5 — el filtro se hace en
    Python justo para no depender de eso (un filtro que no casa devolvería []
    en silencio y reiniciaría el contador sobre facturas existentes)."""
    client = FakeFactusol(f_fac_rows=[
        {"TIPFAC": 5, "CODFAC": 10}, {"TIPFAC": "5", "CODFAC": 11},
    ])
    assert next_codfac(client, "2026", 5) == "12"


# --- find_pcl_by_order ------------------------------------------------------


def test_find_pcl_by_order_hit_composes_refpcl():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(pcl_row=_pcl_row())
    row = find_pcl_by_order(client, order, "2026")
    assert row is not None and row["CODPCL"] == 2765
    # REFPCL compuesto: prefijo BOP + nº Woo con padding 6.
    assert client.pcl_filters == ["REFPCL='BOP-099866'"]


def test_find_pcl_by_order_miss_returns_none():
    order = Order(order_number="BOPRIN-99866")
    assert find_pcl_by_order(FakeFactusol(pcl_row=None), order, "2026") is None


# --- check_factusol_status --------------------------------------------------


def test_check_factusol_status_detects_existing_factura():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(
        fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866", "TOTFAC": 186.34}],
    )
    info = check_factusol_status(client, order, "2026")
    assert info["has_factura"] is True
    assert info["factura"]["CODFAC"] == 260695
    assert info["has_albaran"] is False
    assert info["ref"] == "BOP-099866"
    # Filtra por la referencia común.
    assert client.fac_filters == ["REFFAC='BOP-099866'"]
    assert client.alb_filters == ["REFALB='BOP-099866'"]


def test_check_factusol_status_detects_albaran_only():
    order = Order(order_number="BOPRIN-99866")
    client = FakeFactusol(alb_by_ref=[{"CODALB": 5001, "REFALB": "BOP-099866"}])
    info = check_factusol_status(client, order, "2026")
    assert info["has_factura"] is False
    assert info["has_albaran"] is True
    assert info["albaran"]["CODALB"] == 5001


def test_check_factusol_status_neither():
    order = Order(order_number="BOPRIN-99866")
    info = check_factusol_status(FakeFactusol(), order, "2026")
    assert info["has_factura"] is False and info["has_albaran"] is False


# --- get_and_link_factusol_status (auto-vinculación) ------------------------


def test_get_and_link_auto_links_existing_factura(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866"}],
        )
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, client, "2026")
        assert result == {"status": "invoiced", "codfac": "260695",
                          "ref": "BOP-099866", "auto_linked": True}
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "260695"
        hist = [h for h in o.status_history
                if h.to_status == InvoiceStatus.INVOICED_BY_ERP.value]
        assert len(hist) == 1
        meta = json.loads(hist[0].metadata_json)
        assert meta["source"] == "auto_linked_from_factusol"
        assert meta["factusol_codfac"] == "260695"


def test_get_and_link_reports_albaran(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(alb_by_ref=[{"CODALB": 5001, "REFALB": "BOP-099866"}])
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, client, "2026")
        assert result["status"] == "albaran"
        assert result["albaran_codigo"] == "5001"
    with session_factory() as s:
        # Un albarán NO marca el pedido como facturado.
        assert s.get(Order, oid).factusol_invoice_number is None


def test_get_and_link_pending_when_nothing(session_factory):
    with session_factory() as s:
        oid = _order(s)
        order = s.get(Order, oid)
        result = get_and_link_factusol_status(s, order, FakeFactusol(), "2026")
        assert result["status"] == "pending" and result["ref"] == "BOP-099866"


# --- mapper: F_PCL → F_FAC / F_LPC → F_LFA -----------------------------------


def test_pcl_row_to_fac_payload_maps_all_iva_bands_and_injects():
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "526067", "2026", fecha_emision="2026-08-04",
    )
    # Bandas de IVA copiadas por sufijo (*PCL → *FAC).
    assert fac["NET1FAC"] == 100.0 and fac["PIVA1FAC"] == 21.0 and fac["IIVA1FAC"] == 21.0
    assert fac["NET2FAC"] == 40.0 and fac["IIVA2FAC"] == 4.0
    assert fac["NET3FAC"] == 10.0 and fac["IIVA3FAC"] == 0.4
    assert fac["CLIFAC"] == 2458 and fac["TOTFAC"] == 186.34
    assert fac["REFFAC"] == "BOP-099866"          # REFPCL → REFFAC (el enlace)
    # Inyecciones.
    assert fac["CODFAC"] == "526067"
    # ERP-E2-fix1: TIPFAC = la SERIE, heredada del TIPPCL del pedido. Antes se
    # pisaba con "1" y por eso un pedido de Streamtec salía como Bomedia.
    assert fac["TIPFAC"] == "5"
    assert fac["FECFAC"] == "2026-08-04"          # fecha de emisión, no la del pedido
    # ERP-E2: el ejercicio NO es columna de cabecera (va como parámetro de
    # write_record). Mandarlo tumbaba el EscribirRegistro entero.
    assert "EJEFAC" not in fac
    # C-2-fix2: NO se inyecta PEDFAC (la factura real lo tiene vacío).
    assert "PEDFAC" not in fac
    # Columnas de estado del pedido NO copiadas.
    assert "ESTFAC" not in fac and "USUFAC" not in fac and "HORFAC" not in fac
    # El CODPCL NO se copia como CODFAC (se inyecta el nuevo).
    assert fac["CODFAC"] != 2765


def test_pcl_row_to_fac_payload_applies_operator_options():
    opts = FacturaOptions(serie=2, fecfac="2026-07-29",
                          fopfac="03", comfac="Pago a 30 días")
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "260003", "2026", fecha_emision="2026-08-04", options=opts,
    )
    # TIPFAC = la serie elegida (2 = MQ Europe), no un «tipo de documento».
    assert fac["TIPFAC"] == "2"
    assert fac["FECFAC"] == "2026-07-29"          # la opción gana a la fecha de hoy
    assert fac["FOPFAC"] == "03"
    assert fac["COMFAC"] == "Pago a 30 días"
    # ERP-E2: la serie NO viaja como columna; va en el rango del CODFAC.
    assert "SERFAC" not in fac


def test_pcl_row_to_fac_payload_defaults_omit_optional_columns():
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "1", "2026", fecha_emision="2026-08-04",
    )
    # Sin opciones no se inyectan forma de pago / observaciones.
    assert "FOPFAC" not in fac and "COMFAC" not in fac


# --- ERP-E2: columnas fantasma que rompían TODAS las emisiones --------------


def test_fac_payload_excludes_phantom_columns():
    """Las 7 columnas que no existen en F_FAC no pueden salir en el payload:
    una sola tumba el EscribirRegistro entero (gotcha nº 13)."""
    from app.integrations.factusol.mapper import PHANTOM_FAC_COLUMNS  # noqa: PLC0415

    # F_PCL sí tiene las contrapartidas — la copia por sufijo las arrastraría.
    pcl = _pcl_row(PENPCL=1, PPOPCL=2, INCPCL=3, CEWPCL=4, SMDPCL=5)
    fac = pcl_row_to_fac_payload(
        pcl, "526067", "2026", fecha_emision="2026-08-04",
        options=FacturaOptions(serie=5),
    )
    assert PHANTOM_FAC_COLUMNS.isdisjoint(fac)


def test_lfa_payload_excludes_phantom_columns_but_keeps_ejelfa():
    """En LÍNEAS el ejercicio SÍ es columna (`EJELFA`); las que no existen
    (`ANULFA`, `PENLFA`) se quedan fuera."""
    lpc = {"ARTLPC": "A1", "CANLPC": 1, "CODLPC": 2765, "PENLPC": 0, "ANULPC": 0}
    lfa = lpc_row_to_lfa_payload(lpc, "526067", 1, "2026")
    assert "PENLFA" not in lfa and "ANULFA" not in lfa
    assert lfa["EJELFA"] == "2026"


def test_fac_payload_only_contains_real_columns():
    """Invariante: TODA columna del payload existe en F_FAC (lista canónica de
    167 volcada en vivo). Si alguien inyecta una nueva sin verificarla, salta
    aquí y no en producción."""
    from app.integrations.factusol.mapper import FAC_COLUMNS  # noqa: PLC0415

    pcl = _pcl_row(PENPCL=1, PPOPCL=2, INVENTADAPCL="x")
    fac = pcl_row_to_fac_payload(
        pcl, "526067", "2026", fecha_emision="2026-08-04",
        options=FacturaOptions(serie=5, fopfac="03", comfac="obs"),
    )
    assert set(fac) <= FAC_COLUMNS


def test_lfa_payload_only_contains_real_columns():
    from app.integrations.factusol.mapper import LFA_COLUMNS  # noqa: PLC0415

    lpc = {"ARTLPC": "A1", "CANLPC": 1, "CODLPC": 2765, "INVENTADALPC": "x"}
    assert set(lpc_row_to_lfa_payload(lpc, "1", 1, "2026")) <= LFA_COLUMNS


def test_canonical_column_lists_match_live_dump():
    """Guard de transcripción: el discovery contó 167 y 36 columnas."""
    from app.integrations.factusol.mapper import FAC_COLUMNS, LFA_COLUMNS  # noqa: PLC0415

    assert len(FAC_COLUMNS) == 167
    assert len(LFA_COLUMNS) == 36
    # Las fantasma no pueden colarse en las listas canónicas.
    assert "EJEFAC" not in FAC_COLUMNS and "SERFAC" not in FAC_COLUMNS
    assert "EJELFA" in LFA_COLUMNS


def test_emit_invoice_still_sends_required_fields(session_factory):
    """Regresión: quitar columnas no puede haberse llevado por delante lo que
    la factura necesita (cliente, referencia, totales y bandas de IVA)."""
    lpc_rows = [{"ARTLPC": "A1", "CANLPC": 1, "TOTLPC": 100, "CODLPC": 2765}]
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=lpc_rows,
                              f_fac_last=526066)
        emit_invoice(s, oid, client)
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["CLIFAC"] == 2458
    assert cabecera["REFFAC"] == "BOP-099866"
    assert cabecera["TOTFAC"] == 186.34
    for banda in ("NET1FAC", "PIVA1FAC", "IIVA1FAC", "NET2FAC", "IIVA2FAC",
                  "NET3FAC", "IIVA3FAC"):
        assert banda in cabecera, f"falta la banda {banda}"
    linea = next(rec for t, rec in client.writes if t == "F_LFA")
    assert linea["ARTLFA"] == "A1" and linea["TOTLFA"] == 100


def test_ejercicio_passed_as_parameter_not_column(session_factory):
    """El ejercicio viaja como argumento de `write_record`, nunca en el
    payload de cabecera."""
    calls: list[dict] = []

    class _RecordingClient(FakeFactusol):
        def write_record(self, tabla, data, *, ejercicio=None):
            calls.append({"tabla": tabla, "ejercicio": ejercicio, "data": data})
            return super().write_record(tabla, data, ejercicio=ejercicio)

    with session_factory() as s:
        oid = _order(s)
        client = _RecordingClient(pcl_row=_pcl_row(), lpc_rows=[],
                                  f_fac_last=526066)
        emit_invoice(s, oid, client)
    cabecera = next(c for c in calls if c["tabla"] == "F_FAC")
    assert cabecera["ejercicio"] == "2026"
    assert "EJEFAC" not in cabecera["data"]


def test_lpc_row_to_lfa_payload_copies_line():
    lpc = {"ARTLPC": "ART-1", "DESLPC": "Producto 1", "CANLPC": 2.0,
           "PRELPC": 50.0, "IVALPC": 21.0, "TOTLPC": 100.0, "CODLPC": 2765}
    lfa = lpc_row_to_lfa_payload(lpc, "526067", 1, "2026")
    assert lfa["ARTLFA"] == "ART-1" and lfa["DESLFA"] == "Producto 1"
    assert lfa["CANLFA"] == 2.0 and lfa["TOTLFA"] == 100.0
    assert lfa["CODLFA"] == "526067" and lfa["POSLFA"] == 1 and lfa["EJELFA"] == "2026"


# --- emit_invoice -----------------------------------------------------------


def test_emit_invoice_when_pcl_missing_raises_clear_error(session_factory):
    with session_factory() as s:
        oid = _order(s, number="BOPRIN-99999")
        client = FakeFactusol(pcl_row=None)   # el pedido no está en F_PCL
        with pytest.raises(FactusolError, match="aún no está en FACTUSOL"):
            emit_invoice(s, oid, client)


def test_emit_invoice_end_to_end_success(session_factory):
    from app.models.crm import SyncLog  # noqa: PLC0415

    lpc_rows = [
        {"ARTLPC": "A1", "DESLPC": "L1", "CANLPC": 1, "TOTLPC": 100, "CODLPC": 2765},
        {"ARTLPC": "A2", "DESLPC": "L2", "CANLPC": 2, "TOTLPC": 86.34, "CODLPC": 2765},
    ]
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=lpc_rows, f_fac_last=526066)
        result = emit_invoice(s, oid, client)
    tablas = [t for t, _ in client.writes]
    assert tablas.count("F_FAC") == 1 and tablas.count("F_LFA") == 2
    assert result == {"codfac": "526067", "codpcl": "2765",
                      "ejercicio": "2026", "lines": 2, "serie": 5,
                      "pcl_marked": False}
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["REFFAC"] == "BOP-099866" and cabecera["CODFAC"] == "526067"
    # Serie heredada del pedido + enlace factura→pedido (ERP-E2-fix1).
    assert cabecera["TIPFAC"] == "5"
    assert cabecera["PEDFAC"] == 2765
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "526067"
        hist = [h for h in o.status_history
                if h.to_status == InvoiceStatus.INVOICED_BY_ERP.value]
        assert len(hist) == 1
        meta = json.loads(hist[0].metadata_json)
        assert meta["factusol_codfac"] == "526067" and meta["factusol_codpcl"] == "2765"
        sync = s.query(SyncLog).filter(
            SyncLog.operation == "factusol_emit_invoice").one()
        assert sync.status == "success"


def test_emit_invoice_recheck_auto_links_existing_factura(session_factory):
    """Anti-duplicado: si la factura ya existe en FACTUSOL (creada a mano o por
    carrera), emit la auto-vincula en vez de crear un duplicado."""
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            pcl_row=_pcl_row(),
            fac_by_ref=[{"CODFAC": 260695, "REFFAC": "BOP-099866"}],
            f_fac_last=526066,
        )
        result = emit_invoice(s, oid, client)
    # NO se escribió ninguna factura nueva.
    assert client.writes == []
    assert result["already_existed"] is True and result["codfac"] == "260695"
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status == InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number == "260695"
        meta = json.loads(o.status_history[0].metadata_json)
        assert meta["source"] == "auto_linked_from_factusol"


def test_emit_invoice_threads_options_to_header(session_factory):
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=100)
        emit_invoice(s, oid, client,
                     options=FacturaOptions(serie=5, fopfac="03",
                                            comfac="obs"))
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["FOPFAC"] == "03"
    assert cabecera["COMFAC"] == "obs"
    # La serie viaja en TIPFAC (el «tipo» del documento).
    assert cabecera["TIPFAC"] == "5"


def test_emit_invoice_compensation_on_line_write_failure(session_factory):
    lpc_rows = [{"ARTLPC": "A1", "CODLPC": 2765}, {"ARTLPC": "A2", "CODLPC": 2765}]
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=lpc_rows,
                              f_fac_last=100, fail_on_line=2)
        with pytest.raises(FactusolError):
            emit_invoice(s, oid, client)
    # Compensación: borra líneas + cabecera.
    assert any(t == "F_LFA" for t, _ in client.deletes)
    assert any(t == "F_FAC" for t, _ in client.deletes)
    with session_factory() as s:
        o = s.get(Order, oid)
        assert o.invoice_status != InvoiceStatus.INVOICED_BY_ERP
        assert o.factusol_invoice_number is None


# --- C-2: serie de facturación configurable ---------------------------------


def _set_series(s, *, default=None, by_source=None, estpcl_invoiced=None):
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

    cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        s.add(cfg)
    payload = {}
    if default is not None:
        payload["default"] = default
    if by_source is not None:
        payload["by_source"] = by_source
    if estpcl_invoiced is not None:
        payload["estpcl_invoiced"] = estpcl_invoiced
    cfg.factusol_series_json = json.dumps(payload)
    s.commit()


def test_resolve_serie_prefers_source_override(session_factory):
    from app.integrations.factusol.service import resolve_serie  # noqa: PLC0415

    with session_factory() as s:
        _set_series(s, default=5, by_source={"manual": 2})
        manual = Order(order_number="MANUAL-000001", external_source="manual")
        woo = Order(order_number="BOPRIN-1", external_source="woocommerce")
        s.add_all([manual, woo])
        s.commit()
        assert resolve_serie(s, manual) == 2   # override por origen
        assert resolve_serie(s, woo) == 5      # cae al default
        # La elección del modal gana a todo.
        assert resolve_serie(s, manual, 1) == 1


def test_resolve_serie_falls_back_to_streamtec_without_config(session_factory):
    from app.integrations.factusol.service import resolve_serie  # noqa: PLC0415

    with session_factory() as s:
        order = Order(order_number="BOPRIN-2", external_source="woocommerce")
        s.add(order)
        s.commit()
        assert resolve_serie(s, order) == 5   # Streamtec


def test_resolve_serie_ignores_legacy_letter_config(session_factory):
    """C-2 guardaba la serie como letra («A») para escribir la columna
    `SERFAC`, que no existe. Esa config heredada no puede romper la emisión:
    se ignora y se cae al default."""
    from app.integrations.factusol.service import resolve_serie  # noqa: PLC0415

    with session_factory() as s:
        _set_series(s, default="A", by_source={"manual": "M"})
        order = Order(order_number="MANUAL-1", external_source="manual")
        s.add(order)
        s.commit()
        assert resolve_serie(s, order) == 5


def test_emit_invoice_inherits_series_from_pcl(session_factory):
    """EL BUG DE LA VALIDACIÓN: el pedido ya existe en FACTUSOL con su serie
    (TIPPCL=5, Streamtec). La factura tiene que salir en ESA serie, aunque la
    config diga otra cosa — antes ganaba la config y un pedido de Streamtec
    salió facturado como Bomedia (`1-100000`)."""
    with session_factory() as s:
        _set_series(s, default=1, by_source={"manual": 1})
        oid = _order(s)
        client = FakeFactusol(
            pcl_row=_pcl_row(), lpc_rows=[],
            f_fac_rows=[{"TIPFAC": "5", "CODFAC": 4},
                        {"TIPFAC": "1", "CODFAC": 260695}],
        )
        result = emit_invoice(s, oid, client)
    assert result["serie"] == 5           # heredada del pedido, no la config
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["TIPFAC"] == "5"
    # Contador de la serie 5, no el número alto de la serie 1.
    assert cabecera["CODFAC"] == "5"


def test_emit_invoice_number_from_series_counter(session_factory):
    """El número es el siguiente de SU serie; no hereda el máximo de otra ni
    arranca por debajo del mínimo existente (lo que produjo el `100000`)."""
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            pcl_row=_pcl_row(), lpc_rows=[],
            f_fac_rows=[{"TIPFAC": "2", "CODFAC": 526082},
                        {"TIPFAC": "5", "CODFAC": 41}],
        )
        emit_invoice(s, oid, client)
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["CODFAC"] == "42"


def test_emit_invoice_fallback_to_config_when_pcl_has_no_series(session_factory):
    """Pedido sin serie utilizable en FACTUSOL → manda la config (y en su
    defecto, Streamtec)."""
    with session_factory() as s:
        _set_series(s, default=1, by_source={"manual": 2})
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(TIPPCL=""), lpc_rows=[],
                              f_fac_last=None)
        result = emit_invoice(s, oid, client)
    assert result["serie"] == 2   # override por origen `manual`


def test_emit_invoice_modal_series_wins_over_inherited(session_factory):
    """El operador puede forzar otra empresa emisora desde el modal."""
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(
            pcl_row=_pcl_row(), lpc_rows=[],
            f_fac_rows=[{"TIPFAC": "2", "CODFAC": 526082}],
        )
        result = emit_invoice(s, oid, client, options=FacturaOptions(serie=2))
    assert result["serie"] == 2
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["TIPFAC"] == "2" and cabecera["CODFAC"] == "526083"


def test_emit_invoice_marks_pcl_as_invoiced(session_factory):
    """Con el ESTPCL de «facturado» configurado, el pedido se cierra en
    FACTUSOL con la PK COMPUESTA (tipo + código)."""
    with session_factory() as s:
        _set_series(s, estpcl_invoiced="E")
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=4)
        result = emit_invoice(s, oid, client)
    assert result["pcl_marked"] is True
    assert client.updates == [
        ("F_PCL", {"TIPPCL": 5, "CODPCL": 2765, "ESTPCL": "E"}),
    ]


def test_emit_invoice_skips_marking_when_estpcl_unknown(session_factory):
    """Sin el valor configurado NO se inventa un código de estado: la factura
    se emite igual y el pedido se queda como estaba, con aviso."""
    with session_factory() as s:
        oid = _order(s)
        client = FakeFactusol(pcl_row=_pcl_row(), lpc_rows=[], f_fac_last=4)
        result = emit_invoice(s, oid, client)
    assert result["pcl_marked"] is False
    assert client.updates == []
    assert any(t == "F_FAC" for t, _ in client.writes)   # la factura sí salió


def test_emit_invoice_rejects_already_invoiced(session_factory):
    with session_factory() as s:
        oid = _order(s, invoice_status=InvoiceStatus.INVOICED_BY_ERP.value)
        with pytest.raises(FactusolError, match="ya tiene factura"):
            emit_invoice(s, oid, FakeFactusol(pcl_row=_pcl_row()))


# --- ERP-E2-fix2: TIPFAC = serie ------------------------------------------


def test_fac_payload_tipfac_equals_serie():
    """La cabecera se sella con la SERIE resuelta, no con un «tipo»."""
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "260066", "2026", fecha_emision="2026-08-20",
        options=FacturaOptions(serie=5),
    )
    assert fac["TIPFAC"] == "5"


def test_fac_payload_ignores_legacy_tipfac_option():
    """Un job encolado antes de fix2 trae `tipfac="1"`. Ese valor NO puede
    volver a pisar la serie: era exactamente lo que sellaba toda factura como
    Bomedia y provocaba `BDExisteRegistro`."""
    opts = FacturaOptions.from_payload({"serie": 5, "tipfac": "1"})
    fac = pcl_row_to_fac_payload(
        _pcl_row(), "260066", "2026", fecha_emision="2026-08-20", options=opts,
    )
    assert fac["TIPFAC"] == "5"


def test_emit_invoice_writes_series5_number(session_factory):
    """Reproduce el caso real de MOVIATICOS: pedido F_PCL serie 5, facturas de
    la serie 5 hasta 260065 → escribe (TIPFAC=5, CODFAC=260066)."""
    with session_factory() as s:
        oid = _order(s, number="BOPRIN-99917")
        client = FakeFactusol(
            pcl_row=_pcl_row(CODPCL=5, REFPCL="BOP-099917"), lpc_rows=[],
            f_fac_rows=[
                {"TIPFAC": "1", "CODFAC": 260736},   # Bomedia, número mayor
                {"TIPFAC": "5", "CODFAC": 260065},   # Streamtec, el que manda
                {"TIPFAC": "2", "CODFAC": 526082},
            ],
        )
        result = emit_invoice(s, oid, client)
    assert result["serie"] == 5
    cabecera = next(rec for t, rec in client.writes if t == "F_FAC")
    assert cabecera["TIPFAC"] == "5"
    assert cabecera["CODFAC"] == "260066"


def test_next_codfac_anti_collision():
    """Si el `max+1` estuviera ocupado en la serie, avanza al primer libre en
    vez de morir con BDExisteRegistro contra la clave (TIPFAC, CODFAC)."""
    client = FakeFactusol(f_fac_rows=[
        {"TIPFAC": "5", "CODFAC": 10},
        {"TIPFAC": "5", "CODFAC": 11},
        {"TIPFAC": "5", "CODFAC": 9},
    ])
    # max=11 → 12 libre.
    assert next_codfac(client, "2026", 5) == "12"
    client2 = FakeFactusol(f_fac_rows=[
        {"TIPFAC": "5", "CODFAC": 10}, {"TIPFAC": "5", "CODFAC": 12},
    ])
    # max=12 → 13 libre (no reutiliza el hueco 11: la numeración no retrocede).
    assert next_codfac(client2, "2026", 5) == "13"


def test_estpcl_invoiced_from_settings_json(session_factory):
    from app.integrations.factusol.service import (  # noqa: PLC0415
        estpcl_invoiced_value,
    )

    with session_factory() as s:
        assert estpcl_invoiced_value(s) is None      # sin configurar
        _set_series(s, estpcl_invoiced="2")
        s.commit()
        assert estpcl_invoiced_value(s) == "2"


def test_emit_job_failure_records_error_and_keeps_order_reemitible(session_factory):
    """El job falla → el pedido NO queda `invoiced_by_erp` y el fallo se
    registra en el historial. Sin esto la ficha se quedaba en «Generando…»
    esperando un job muerto."""
    from app.erp.models import OrderStatusHistory  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        record_emit_failure,
    )

    with session_factory() as s:
        oid = _order(s)
        record_emit_failure(s, oid, "BDExisteRegistro")
        order = s.get(Order, oid)
        assert order.invoice_status != InvoiceStatus.INVOICED_BY_ERP.value
        assert order.factusol_invoice_number is None
        hist = list(s.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == oid)
        ))
        assert len(hist) == 1
        assert "BDExisteRegistro" in hist[0].metadata_json
