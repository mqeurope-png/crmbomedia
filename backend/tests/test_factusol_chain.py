"""ERP-E3-B — crear la cadena de documentos FACTUSOL (PRE→ALB→FAC).

Cliente mockeado con escrituras en memoria. Cubre el contador por serie, la
allowlist de columnas vivas (y su fallback de referencia), la conversión
completa con herencia de serie y enlace `DOC/DTP/DCO` en líneas, el
anti-duplicado (con `force`), la compensación por clave compuesta, el índice
del ciclo (Parte D) y los endpoints HTTP (convert + ciclo en listados).
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.integrations.factusol.chain import (
    ALB_REFERENCE_COLUMNS,
    ALLOWED_CONVERSIONS,
    LAL_REFERENCE_COLUMNS,
    build_target_header,
    build_target_line,
    convert_document,
    cycle_annotator,
    cycle_of,
    find_existing_children,
    live_columns,
    load_chain_index,
    next_doc_code,
)
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.documents import DOC_SPECS, list_documents
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

# ---------------------------------------------------------------------------
# Fake client con escrituras
# ---------------------------------------------------------------------------


class WriteFakeClient(FakeClient):
    """FakeClient + escrituras en memoria.

    `known_columns[tabla]` simula el gotcha nº 13: escribir una columna que
    la tabla no tiene tumba el registro ENTERO con error. `fail_write_at`
    revienta la enésima escritura de una tabla, para probar la compensación.
    """

    def __init__(self, tables: dict[str, list[dict[str, Any]]],
                 *, strict_columns: bool = True,
                 known_columns: dict[str, frozenset[str]] | None = None):
        super().__init__(tables, strict_columns=strict_columns)
        self.known_columns = known_columns or {}
        self.written: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_write_at: dict[str, int] = {}

    def write_record(self, tabla: str, data: dict[str, Any], *,
                     ejercicio: str | None = None) -> dict[str, Any]:
        allowed = self.known_columns.get(tabla)
        if allowed is not None:
            unknown = sorted(set(data) - set(allowed))
            if unknown:
                raise FactusolError(
                    f"BDEscribirRegistroError: {tabla} no tiene {unknown}"
                )
        n = len([t for t, _ in self.written if t == tabla]) + 1
        if self.fail_write_at.get(tabla) == n:
            raise FactusolError(f"KO simulado (escritura {n} de {tabla})")
        self.written.append((tabla, dict(data)))
        self.tables.setdefault(tabla, []).append(dict(data))
        return {"respuesta": "OK"}

    def delete_records(self, tabla: str, filtro: str, *,
                       ejercicio: str | None = None) -> dict[str, Any]:
        self.deleted.append((tabla, filtro))
        conds = []
        for part in filtro.split(" AND "):
            col, _, raw = part.partition("=")
            conds.append((col.strip(), raw.strip().strip("'")))
        rows = self.tables.get(tabla, [])
        self.tables[tabla] = [
            r for r in rows
            if not all(str(r.get(c)) == v for c, v in conds)
        ]
        return {"respuesta": "OK"}


# ---------------------------------------------------------------------------
# Fixtures de datos (espejo de la cadena real 5-000027 → 5-500004 → 5-260063)
# ---------------------------------------------------------------------------


def _live_alb_row(codigo: int, serie: str = "5", **over: Any) -> dict[str, Any]:
    """Fila «viva» de F_ALB: TODAS las columnas de la referencia, como las
    devuelve CargaTabla (la API siempre sirve la tabla completa)."""
    row: dict[str, Any] = {c: "" for c in ALB_REFERENCE_COLUMNS}
    row.update({"TIPALB": serie, "CODALB": codigo})
    row.update(over)
    return row


def _live_lal_row(codigo: int, serie: str = "5", pos: int = 1,
                  **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {c: "" for c in LAL_REFERENCE_COLUMNS}
    row.update({"TIPLAL": serie, "CODLAL": codigo, "POSLAL": pos})
    row.update(over)
    return row


PRESUPUESTO = {
    "TIPPRE": "5", "CODPRE": 27, "CLIPRE": 2458,
    "CNOPRE": "DUPLICODER, S.L.", "FECPRE": "2026-08-01T00:00:00",
    "ESTPRE": 1, "REFPRE": "Obra X", "TOTPRE": 186.34, "FOPPRE": "002",
    # Auditoría/impreso: NO deben copiarse al destino.
    "USUPRE": "BART", "IMPPRE": 1,
}
PRESUPUESTO_HOMONIMO = {  # mismo número, OTRA serie: no puede mezclarse
    "TIPPRE": "2", "CODPRE": 27, "CLIPRE": 7, "CNOPRE": "OTRA SL",
    "ESTPRE": 0, "TOTPRE": 1.0,
}
LINEAS_LPS = [
    {"TIPLPS": "5", "CODLPS": 27, "POSLPS": 1, "ARTLPS": "99cy",
     "DESLPS": "Tinta cyan", "CANLPS": 2, "PRELPS": 40.0, "TOTLPS": 80.0,
     "IVALPS": 21, "USULPS": "BART"},
    # Línea de TEXTO LIBRE (ART*='') — debe pasar tal cual.
    {"TIPLPS": "5", "CODLPS": 27, "POSLPS": 2, "ARTLPS": "",
     "DESLPS": "Portes", "CANLPS": 1, "PRELPS": 106.34, "TOTLPS": 106.34},
    # Línea del presupuesto homónimo de serie 2 (CODLPS=27): NO se copia.
    {"TIPLPS": "2", "CODLPS": 27, "POSLPS": 1, "ARTLPS": "XX",
     "DESLPS": "De otra serie", "CANLPS": 9, "PRELPS": 1.0, "TOTLPS": 9.0},
]


def _chain_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_PRE": [dict(PRESUPUESTO), dict(PRESUPUESTO_HOMONIMO)],
        "F_LPS": [dict(r) for r in LINEAS_LPS],
        # Serie 5 va por 500003; serie 1 por 900001 (contadores separados).
        "F_ALB": [_live_alb_row(500003, "5"), _live_alb_row(900001, "1")],
        "F_LAL": [_live_lal_row(500003, "5")],
        "F_FAC": [],
        "F_LFA": [],
    }


def _write_client(
    tables: dict[str, list[dict[str, Any]]] | None = None,
) -> WriteFakeClient:
    return WriteFakeClient(
        tables if tables is not None else _chain_tables(),
        known_columns={
            "F_ALB": ALB_REFERENCE_COLUMNS,
            "F_LAL": LAL_REFERENCE_COLUMNS,
        },
    )


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
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Contador por serie + allowlist de columnas
# ---------------------------------------------------------------------------


def test_next_doc_code_is_per_series() -> None:
    """Cada tipo de documento lleva su correlativo POR SERIE: el máximo de la
    serie 1 (900001) no arrastra el contador de la serie 5 (500003)."""
    client = _write_client()
    assert next_doc_code(client, "F_ALB", tip_col="TIPALB", cod_col="CODALB",
                         serie=5, ejercicio="2026") == "500004"
    assert next_doc_code(client, "F_ALB", tip_col="TIPALB", cod_col="CODALB",
                         serie=1, ejercicio="2026") == "900002"
    # Serie sin documentos: arranca en 1.
    assert next_doc_code(client, "F_ALB", tip_col="TIPALB", cod_col="CODALB",
                         serie=3, ejercicio="2026") == "1"


def test_live_columns_reads_live_row_and_caches() -> None:
    client = _write_client()
    cols = live_columns(client, "F_ALB", ejercicio="2026")
    assert cols == ALB_REFERENCE_COLUMNS
    calls_before = len(client.calls)
    assert live_columns(client, "F_ALB", ejercicio="2026") == cols
    assert len(client.calls) == calls_before  # cacheado: sin segunda carga


def test_live_columns_falls_back_to_reference_or_errors() -> None:
    client = _write_client({"F_ALB": [], "F_LAL": []})
    # Tabla vacía → lista de referencia del discovery.
    assert live_columns(client, "F_ALB", ejercicio="2026") == ALB_REFERENCE_COLUMNS
    assert live_columns(client, "F_LAL", ejercicio="2026") == LAL_REFERENCE_COLUMNS
    # Sin fila viva NI referencia → error explícito, nunca escribir a ciegas.
    with pytest.raises(FactusolError):
        live_columns(client, "F_XXX", ejercicio="2026")


def test_reference_lists_match_a_live_row_and_cover_the_writes() -> None:
    """El criterio de la allowlist es el de FAC_COLUMNS: lo que devuelve una
    fila REAL. La fila viva de los fixtures (construida de la referencia)
    debe casar 1:1 con la lista, y la lista debe contener todo lo que la
    conversión inyecta (clave, fecha y enlace)."""
    client = _write_client()
    assert frozenset(_live_alb_row(1).keys()) == ALB_REFERENCE_COLUMNS
    assert live_columns(client, "F_ALB", ejercicio="2026") == ALB_REFERENCE_COLUMNS
    assert {"TIPALB", "CODALB", "FECALB", "ESTALB", "TOTALB"} <= ALB_REFERENCE_COLUMNS
    assert {"TIPLAL", "CODLAL", "POSLAL", "ARTLAL", "DESLAL", "CANLAL",
            "PRELAL", "TOTLAL", "DOCLAL", "DTPLAL", "DCOLAL"} <= LAL_REFERENCE_COLUMNS


# ---------------------------------------------------------------------------
# Copia por sufijo
# ---------------------------------------------------------------------------


def test_build_target_header_retags_filters_and_injects() -> None:
    src, dst = DOC_SPECS["presupuestos"], DOC_SPECS["albaranes"]
    payload = build_target_header(
        PRESUPUESTO, src=src, dst=dst, serie=5, codigo="500004",
        fecha="2026-08-21", allowed=ALB_REFERENCE_COLUMNS,
    )
    assert payload["TIPALB"] == "5" and payload["CODALB"] == "500004"
    assert payload["FECALB"] == "2026-08-21"
    assert payload["CNOALB"] == "DUPLICODER, S.L."
    assert payload["ESTALB"] == 1        # estado propagado por sufijo
    assert payload["TOTALB"] == 186.34
    assert payload["FOPALB"] == "002"
    # Auditoría e impreso del ORIGEN excluidos; nada fuera de la allowlist.
    assert "USUALB" not in payload and "IMPALB" not in payload
    assert set(payload) <= ALB_REFERENCE_COLUMNS


def test_build_target_line_injects_link_and_overwrites_stale_doc() -> None:
    """El enlace DOC/DTP/DCO se inyecta SIEMPRE apuntando al origen actual:
    si la línea del albarán ya traía su propio enlace al presupuesto
    (DOCLAL='P'), al facturar ese albarán la línea nueva debe decir
    DOCLFA='A' (el albarán), no arrastrar el 'P' copiado."""
    src, dst = DOC_SPECS["albaranes"], DOC_SPECS["facturas"]
    line_alb = _live_lal_row(500004, "5", DOCLAL="P", DTPLAL="5", DCOLAL=27,
                             ARTLAL="99cy", DESLAL="Tinta", CANLAL=2)
    payload = build_target_line(
        line_alb, src=src, dst=dst, serie=5, codigo="260064", posicion=1,
        origin_code="A", origin_tip=5, origin_cod=500004,
        allowed=frozenset({
            "TIPLFA", "CODLFA", "POSLFA", "ARTLFA", "DESLFA", "CANLFA",
            "DOCLFA", "DTPLFA", "DCOLFA",
        }),
    )
    assert payload["DOCLFA"] == "A"
    assert payload["DTPLFA"] == "5" and payload["DCOLFA"] == 500004
    assert payload["TIPLFA"] == "5" and payload["CODLFA"] == "260064"
    assert payload["POSLFA"] == 1 and payload["ARTLFA"] == "99cy"


# ---------------------------------------------------------------------------
# convert_document — Parte A (PRE→ALB)
# ---------------------------------------------------------------------------


def test_convert_pre_to_alb_inherits_serie_and_links_lines(db) -> None:
    client = _write_client()
    result = convert_document(
        db, client, source_type="presupuestos", target_type="albaranes",
        tip=5, cod=27, ejercicio="2026", fecha="2026-08-21",
    )
    assert result["numero"] == "5-500004"      # contador de la serie 5
    assert result["serie"] == 5                # heredada de TIPPRE
    assert result["lines"] == 2                # la de serie 2 NO se copió

    header = next(p for t, p in client.written if t == "F_ALB")
    assert header["TIPALB"] == "5" and header["CODALB"] == "500004"
    assert header["ESTALB"] == 1 and header["CNOALB"] == "DUPLICODER, S.L."
    assert header["FECALB"] == "2026-08-21"

    lineas = [p for t, p in client.written if t == "F_LAL"]
    assert [ln["POSLAL"] for ln in lineas] == [1, 2]
    for ln in lineas:
        assert ln["TIPLAL"] == "5" and ln["CODLAL"] == "500004"
        # EL ENLACE: vive en la línea, no en la cabecera.
        assert ln["DOCLAL"] == "P"      # origen presupuesto
        assert ln["DTPLAL"] == "5"      # serie del origen
        assert ln["DCOLAL"] == 27       # número del origen
    # La línea de texto libre pasó tal cual.
    assert lineas[1]["ARTLAL"] == "" and lineas[1]["DESLAL"] == "Portes"
    # La cabecera no arrastra ningún enlace (PEDALB vacío como el real).
    assert header.get("PEDALB", "") == ""

    # SyncLog de éxito registrado.
    from app.models.crm import SyncLog
    logs = db.scalars(select(SyncLog)).all()
    assert len(logs) == 1 and "5-500004" in (logs[0].message or "")


def test_convert_serie_override_wins_but_link_keeps_origin_serie(db) -> None:
    client = _write_client()
    result = convert_document(
        db, client, source_type="presupuestos", target_type="albaranes",
        tip=5, cod=27, ejercicio="2026", serie_override=1,
    )
    assert result["numero"] == "1-900002"      # contador de la serie forzada
    header = next(p for t, p in client.written if t == "F_ALB")
    assert header["TIPALB"] == "1"
    linea = next(p for t, p in client.written if t == "F_LAL")
    # El enlace sigue apuntando al ORIGEN real (serie 5), no a la forzada.
    assert linea["DTPLAL"] == "5" and linea["DCOLAL"] == 27


def test_convert_alb_to_fac_and_pre_to_fac_link_codes(db) -> None:
    """Parte B (ALB→FAC, DOC='A') y Parte C (PRE→FAC directa, DOC='P')."""
    tables = _chain_tables()
    tables["F_ALB"].append(_live_alb_row(
        500004, "5", CNOALB="DUPLICODER, S.L.", ESTALB=1, TOTALB=186.34,
    ))
    tables["F_LAL"].append(_live_lal_row(
        500004, "5", ARTLAL="99cy", DESLAL="Tinta", CANLAL=2,
        DOCLAL="P", DTPLAL="5", DCOLAL=27,
    ))
    tables["F_FAC"] = [{"TIPFAC": "5", "CODFAC": 260063, "CLIFAC": 1}]
    tables["F_LFA"] = [{"TIPLFA": "5", "CODLFA": 260063, "POSLFA": 1,
                        "ARTLFA": "", "DESLFA": "x", "DOCLFA": "",
                        "DTPLFA": "", "DCOLFA": ""}]
    client = _write_client(tables)

    fac = convert_document(
        db, client, source_type="albaranes", target_type="facturas",
        tip=5, cod=500004, ejercicio="2026",
    )
    assert fac["numero"] == "5-260064"          # contador de FACTURAS serie 5
    linea = next(p for t, p in client.written if t == "F_LFA")
    assert linea["DOCLFA"] == "A"               # origen albarán
    assert linea["DTPLFA"] == "5" and linea["DCOLFA"] == 500004

    directa = convert_document(
        db, client, source_type="presupuestos", target_type="facturas",
        tip=5, cod=27, ejercicio="2026",
    )
    assert directa["numero"] == "5-260065"
    linea2 = [p for t, p in client.written if t == "F_LFA"][-2]
    assert linea2["DOCLFA"] == "P" and linea2["DCOLFA"] == 27


def test_convert_rejects_unsupported_and_missing(db) -> None:
    client = _write_client()
    with pytest.raises(FactusolError, match="no soportada"):
        convert_document(db, client, source_type="facturas",
                         target_type="albaranes", tip=5, cod=1,
                         ejercicio="2026")
    with pytest.raises(FactusolError, match="No existe"):
        convert_document(db, client, source_type="presupuestos",
                         target_type="albaranes", tip=5, cod=999,
                         ejercicio="2026")


def test_convert_duplicate_raises_unless_forced(db) -> None:
    tables = _chain_tables()
    # El albarán 5-500004 ya apunta al presupuesto 5-27 por sus líneas.
    tables["F_ALB"].append(_live_alb_row(500004, "5"))
    tables["F_LAL"].append(_live_lal_row(
        500004, "5", DOCLAL="P", DTPLAL="5", DCOLAL=27,
    ))
    client = _write_client(tables)
    with pytest.raises(FactusolError, match="5-500004"):
        convert_document(db, client, source_type="presupuestos",
                         target_type="albaranes", tip=5, cod=27,
                         ejercicio="2026")
    assert client.written == []  # no se escribió nada
    # force=True: el operador vio el aviso y quiere el segundo albarán.
    result = convert_document(
        db, client, source_type="presupuestos", target_type="albaranes",
        tip=5, cod=27, ejercicio="2026", force=True,
    )
    assert result["numero"] == "5-500005"


def test_find_existing_children_matches_composite_origin() -> None:
    tables = _chain_tables()
    tables["F_LAL"] += [
        # Hijo REAL del presupuesto 5-27.
        _live_lal_row(500004, "5", DOCLAL="P", DTPLAL="5", DCOLAL=27),
        # Mismo número de origen pero OTRA serie (2-27): no es hijo de 5-27.
        _live_lal_row(500009, "5", DOCLAL="P", DTPLAL="2", DCOLAL=27),
        # Mismo DCO pero origen pedido ('C'): tampoco.
        _live_lal_row(500010, "5", DOCLAL="C", DTPLAL="5", DCOLAL=27),
    ]
    client = _write_client(tables)
    children = find_existing_children(
        client, "presupuestos", "albaranes", tip=5, cod=27, ejercicio="2026",
    )
    assert children == ["5-500004"]


def test_convert_compensation_deletes_only_composite_key(db) -> None:
    """Si una línea falla a mitad, se borra lo escrito filtrando por
    (TIP, COD): el albarán homónimo de OTRA serie sobrevive."""
    tables = _chain_tables()
    # Homónimo preexistente: 1-500004 con una línea.
    tables["F_ALB"].append(_live_alb_row(500004, "1"))
    tables["F_LAL"].append(_live_lal_row(500004, "1", DESLAL="ajena"))
    client = _write_client(tables)
    client.fail_write_at["F_LAL"] = 2  # la 2ª línea revienta

    with pytest.raises(FactusolError, match="KO simulado"):
        convert_document(
            db, client, source_type="presupuestos", target_type="albaranes",
            tip=5, cod=27, ejercicio="2026",
        )
    assert ("F_LAL", "TIPLAL='5' AND CODLAL='500004'") in client.deleted
    assert ("F_ALB", "TIPALB='5' AND CODALB='500004'") in client.deleted
    # Lo escrito a medias se limpió…
    assert not [r for r in client.tables["F_ALB"]
                if str(r["TIPALB"]) == "5" and str(r["CODALB"]) == "500004"]
    # …y el homónimo de la serie 1 sigue intacto (lección E2).
    assert [r for r in client.tables["F_ALB"]
            if str(r["TIPALB"]) == "1" and str(r["CODALB"]) == "500004"]
    assert [r for r in client.tables["F_LAL"]
            if str(r["TIPLAL"]) == "1" and str(r["CODLAL"]) == "500004"]


# ---------------------------------------------------------------------------
# Parte D — índice del ciclo
# ---------------------------------------------------------------------------

LAL_CICLO = [
    # Albarán 5-500004 creado desde el presupuesto 5-27.
    {"TIPLAL": "5", "CODLAL": 500004, "POSLAL": 1, "ARTLAL": "99cy",
     "DOCLAL": "P", "DTPLAL": "5", "DCOLAL": 27},
    # Albarán 5-500005 desde el presupuesto 5-30 (aún sin factura).
    {"TIPLAL": "5", "CODLAL": 500005, "POSLAL": 1, "ARTLAL": "",
     "DOCLAL": "P", "DTPLAL": "5", "DCOLAL": 30},
    # Albarán suelto (sin enlace).
    {"TIPLAL": "5", "CODLAL": 500006, "POSLAL": 1, "ARTLAL": "",
     "DOCLAL": "", "DTPLAL": "", "DCOLAL": ""},
]
LFA_CICLO = [
    # Factura 5-260063 creada desde el albarán 5-500004.
    {"TIPLFA": "5", "CODLFA": 260063, "POSLFA": 1,
     "DOCLFA": "A", "DTPLFA": "5", "DCOLFA": 500004},
    # Factura 5-260070 DIRECTA desde el presupuesto 5-31.
    {"TIPLFA": "5", "CODLFA": 260070, "POSLFA": 1,
     "DOCLFA": "P", "DTPLFA": "5", "DCOLFA": 31},
]


def _ciclo_client(**extra: list[dict[str, Any]]) -> FakeClient:
    return FakeClient({"F_LAL": LAL_CICLO, "F_LFA": LFA_CICLO, **extra})


def test_cycle_of_presupuesto_direct_via_albaran_and_pending() -> None:
    index = load_chain_index(_ciclo_client(), ejercicio="2026")
    # 5-27: albarán + factura A TRAVÉS del albarán → facturado.
    c27 = cycle_of(index, "presupuestos", 5, 27)
    assert [a["numero"] for a in c27["albaranes"]] == ["5-500004"]
    assert [f["numero"] for f in c27["facturas"]] == ["5-260063"]
    assert c27["estado"] == "facturado"
    # 5-30: albarán sin factura → con_albaran.
    c30 = cycle_of(index, "presupuestos", 5, 30)
    assert c30["estado"] == "con_albaran"
    assert c30["facturas"] == []
    # 5-31: factura DIRECTA sin albarán → facturado.
    c31 = cycle_of(index, "presupuestos", 5, 31)
    assert c31["estado"] == "facturado"
    assert [f["numero"] for f in c31["facturas"]] == ["5-260070"]
    # 5-99: sin nada → pendiente.
    assert cycle_of(index, "presupuestos", 5, 99)["estado"] == "pendiente"
    # 2-27 (homónimo de otra serie): NO hereda los hijos de 5-27.
    assert cycle_of(index, "presupuestos", 2, 27)["estado"] == "pendiente"


def test_cycle_of_albaran_and_factura_show_origin() -> None:
    index = load_chain_index(_ciclo_client(), ejercicio="2026")
    alb = cycle_of(index, "albaranes", 5, 500004)
    assert [o["numero"] for o in alb["origen"]] == ["5-000027"]
    assert alb["origen"][0]["doc_type"] == "presupuestos"
    assert [f["numero"] for f in alb["facturas"]] == ["5-260063"]
    assert alb["estado"] == "facturado"
    suelto = cycle_of(index, "albaranes", 5, 500006)
    assert suelto["origen"] == [] and suelto["estado"] == "pendiente"
    fac = cycle_of(index, "facturas", 5, 260063)
    assert [o["numero"] for o in fac["origen"]] == ["5-500004"]
    assert fac["origen"][0]["doc_type"] == "albaranes"
    assert fac["estado"] is None


def test_list_documents_annotates_and_filters_by_ciclo() -> None:
    presupuestos = [
        {"TIPPRE": "5", "CODPRE": 27, "CNOPRE": "A", "TOTPRE": 1.0},
        {"TIPPRE": "5", "CODPRE": 30, "CNOPRE": "B", "TOTPRE": 2.0},
        {"TIPPRE": "5", "CODPRE": 99, "CNOPRE": "C", "TOTPRE": 3.0},
    ]
    client = _ciclo_client(F_PRE=presupuestos)
    annotate = cycle_annotator(client, "presupuestos", ejercicio="2026")
    out = list_documents(client, "presupuestos", ejercicio="2026",
                         annotate=annotate, ciclo="facturado")
    assert out["total"] == 1
    assert out["items"][0]["codigo"] == 27
    assert out["items"][0]["ciclo"]["estado"] == "facturado"
    todos = list_documents(client, "presupuestos", ejercicio="2026",
                           annotate=annotate)
    assert {d["codigo"]: d["ciclo"]["estado"] for d in todos["items"]} == {
        27: "facturado", 30: "con_albaran", 99: "pendiente",
    }


# ---------------------------------------------------------------------------
# Endpoints HTTP
# ---------------------------------------------------------------------------


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _patched_factusol(fake: FakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def test_documents_endpoint_returns_ciclo_and_filters(http, session_factory) -> None:
    _ = session_factory
    presupuestos = [
        {"TIPPRE": "5", "CODPRE": 27, "CNOPRE": "A", "TOTPRE": 1.0},
        {"TIPPRE": "5", "CODPRE": 99, "CNOPRE": "C", "TOTPRE": 3.0},
    ]
    with _patched_factusol(_ciclo_client(F_PRE=presupuestos)):
        r = http.get(
            "/api/erp/factusol/documents/presupuestos?ciclo=facturado",
            headers=auth_headers(http, "user"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["numero"] == "5-000027"
    assert [f["numero"] for f in body["items"][0]["ciclo"]["facturas"]] == [
        "5-260063",
    ]


def test_document_detail_includes_ciclo(http, session_factory) -> None:
    _ = session_factory
    tables = {
        "F_PRE": [{"TIPPRE": "5", "CODPRE": 27, "CNOPRE": "A", "TOTPRE": 1.0}],
        "F_LPS": [], "F_LAL": LAL_CICLO, "F_LFA": LFA_CICLO,
    }
    with _patched_factusol(FakeClient(tables)):
        r = http.get(
            "/api/erp/factusol/documents/presupuestos/5/27",
            headers=auth_headers(http, "user"),
        )
    assert r.status_code == 200, r.text
    ciclo = r.json()["ciclo"]
    assert ciclo["estado"] == "facturado"
    assert [a["numero"] for a in ciclo["albaranes"]] == ["5-500004"]


def test_convert_endpoint_requires_edit_role(http, session_factory) -> None:
    _ = session_factory
    r = http.post(
        "/api/erp/factusol/documents/presupuestos/5/27/convert",
        json={"target": "albaranes"},
        headers=auth_headers(http, "user"),  # rol de solo-lectura en el ERP
    )
    assert r.status_code == 403


def test_convert_endpoint_validates_conversion(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "pedidos")
    r = http.post(
        "/api/erp/factusol/documents/facturas/5/260066/convert",
        json={"target": "albaranes"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "conversion_not_supported"
    # pedidos → albaranes tampoco está soportado (fuera de scope E3-B).
    r2 = http.post(
        "/api/erp/factusol/documents/pedidos/5/1/convert",
        json={"target": "albaranes"},
        headers=headers,
    )
    assert r2.status_code == 400


def test_convert_endpoint_404_when_source_missing(http, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient({"F_PRE": [], "F_LPS": []})):
        r = http.post(
            "/api/erp/factusol/documents/presupuestos/5/999/convert",
            json={"target": "albaranes"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 404


def test_convert_endpoint_409_lists_existing_children(http, session_factory) -> None:
    _ = session_factory
    tables = {
        "F_PRE": [{"TIPPRE": "5", "CODPRE": 27, "CNOPRE": "A", "TOTPRE": 1.0}],
        "F_LPS": [], "F_LAL": LAL_CICLO, "F_LFA": LFA_CICLO,
    }
    with _patched_factusol(FakeClient(tables)):
        r = http.post(
            "/api/erp/factusol/documents/presupuestos/5/27/convert",
            json={"target": "albaranes"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "already_converted"
    assert detail["existing"] == ["5-500004"]
    assert "5-500004" in detail["detail"]


def test_convert_endpoint_enqueues_job_with_options(http, session_factory) -> None:
    _ = session_factory
    tables = {
        "F_PRE": [{"TIPPRE": "5", "CODPRE": 27, "CNOPRE": "A", "TOTPRE": 1.0}],
        "F_LPS": [], "F_LAL": [], "F_LFA": [],
    }
    with (
        _patched_factusol(FakeClient(tables)),
        patch("app.integrations.factusol.jobs.enqueue_create_document",
              return_value="job-42") as enq,
    ):
        r = http.post(
            "/api/erp/factusol/documents/presupuestos/5/27/convert",
            json={"target": "albaranes", "serie": 1, "fecha": "2026-08-21"},
            headers=auth_headers(http, "pedidos"),
        )
    assert r.status_code == 202, r.text
    assert r.json() == {"job_id": "job-42", "status": "queued"}
    args, _kwargs = enq.call_args
    assert args[0:4] == ("presupuestos", "albaranes", 5, 27)
    assert args[4]["serie"] == 1 and args[4]["fecha"] == "2026-08-21"
    assert args[4]["force"] is False and args[4]["ejercicio"]


def test_convert_status_endpoint_pending_without_redis(http, session_factory) -> None:
    _ = session_factory
    r = http.get(
        "/api/erp/factusol/documents/convert-status/job-42",
        headers=auth_headers(http, "user"),
    )
    assert r.status_code == 200
    assert r.json() == {"status": "pending"}


def test_allowed_conversions_shape() -> None:
    assert ALLOWED_CONVERSIONS == {
        "presupuestos": ("albaranes", "facturas"),
        "albaranes": ("facturas",),
    }
