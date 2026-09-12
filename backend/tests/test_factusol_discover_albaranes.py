"""ERP-E1 — helpers del script de discovery de albaranes.

El script se ejecuta contra FACTUSOL real (solo Bart puede), pero su lógica
—detectar la referencia cruzada entre documentos y, sobre todo, comparar el
payload del mapper con las columnas REALES— sí se testea aquí con un cliente
falso. Ese diff es el que diagnostica el bug de emisión de facturas: una
columna inexistente hace fallar el `EscribirRegistro` entero (gotcha nº 13).
"""
from __future__ import annotations

from typing import Any

from scripts.factusol_discover_albaranes import (
    distinct_values,
    find_matching_columns,
    is_safe_probe_path,
    looks_like_reference,
    normalize,
    payload_column_diff,
    probe_table,
    summarize_numbering,
)


class _FakeClient:
    """Cliente FACTUSOL falso: responde con tablas en memoria y registra las
    llamadas. Nunca sale a red."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self.tables = tables
        self.calls: list[tuple[str, str]] = []

    def load_table(
        self, tabla: str, *, filtro: str = "1=1", ejercicio: str = "2026"
    ) -> list[dict[str, Any]]:
        self.calls.append((tabla, filtro))
        if tabla not in self.tables:
            # Igual que la API real: tabla inexistente → [] sin error.
            return []
        return list(self.tables[tabla])


# ---------------------------------------------------------------------------
# normalize / find_matching_columns — la búsqueda empírica de referencias
# ---------------------------------------------------------------------------


def test_normalize_equates_numeric_representations() -> None:
    assert normalize(574) == normalize("574") == normalize(" 574 ")
    assert normalize(574.0) == "574"
    assert normalize(None) == ""


def test_find_matching_columns_locates_reference_regardless_of_name() -> None:
    """El punto del método: no adivinamos si se llama PREALB u ORIALB."""
    albaran = {
        "CODALB": 91,
        "PREALB": "574",
        "CLIALB": 22,
        "TOTALB": 355.0,
        "REFALB": "",
    }
    hits = dict(find_matching_columns(albaran, 574))
    assert "PREALB" in hits
    assert "CODALB" not in hits
    assert "CLIALB" not in hits


def test_find_matching_columns_empty_needle_matches_nothing() -> None:
    """Un needle vacío casaría con todas las columnas vacías — inútil."""
    assert find_matching_columns({"A": "", "B": None}, "") == []
    assert find_matching_columns({"A": "", "B": None}, None) == []


def test_looks_like_reference_ranks_by_column_name() -> None:
    assert looks_like_reference("PREALB")
    assert looks_like_reference("ORIFAC")
    assert not looks_like_reference("CANLAL")


# ---------------------------------------------------------------------------
# payload_column_diff — EL diagnóstico del bug de facturas
# ---------------------------------------------------------------------------


def test_payload_column_diff_flags_columns_missing_in_real_table() -> None:
    payload = {"CODFAC": "1", "EJEFAC": "2026", "CLIFAC": 22, "SERFAC": "A"}
    real = ["CODFAC", "CLIFAC", "TOTFAC", "REFFAC"]
    unknown, unused = payload_column_diff(payload, real)
    # EJEFAC y SERFAC no existen → cada una revienta el registro entero.
    assert unknown == ["EJEFAC", "SERFAC"]
    assert unused == ["REFFAC", "TOTFAC"]


def test_payload_column_diff_is_case_insensitive() -> None:
    unknown, _ = payload_column_diff({"codfac": 1}, ["CODFAC"])
    assert unknown == []


def test_payload_column_diff_clean_payload_has_no_unknowns() -> None:
    unknown, _ = payload_column_diff(
        {"CODFAC": 1, "CLIFAC": 2}, ["CODFAC", "CLIFAC", "TOTFAC"]
    )
    assert unknown == []


def test_payload_column_diff_against_real_mapper_output() -> None:
    """El diagnóstico que este script hizo en ERP-E1 ahora tiene que salir
    LIMPIO: tras el fix de ERP-E2 el payper del mapper no lleva ninguna
    columna que F_FAC no tenga."""
    from app.integrations.factusol.mapper import (
        FAC_COLUMNS,
        FacturaOptions,
        pcl_row_to_fac_payload,
    )

    pcl_row = {"CODPCL": 5, "CLIPCL": 22, "TOTPCL": 100.0, "REFPCL": "BOP-1",
               "PENPCL": 0, "PPOPCL": 0}
    payload = pcl_row_to_fac_payload(
        pcl_row, "526083", "2026", fecha_emision="2026-08-11",
        options=FacturaOptions(serie=5),
    )
    unknown, _ = payload_column_diff(payload, sorted(FAC_COLUMNS))
    assert unknown == []


def test_payload_column_diff_would_have_caught_the_erp_e1_bug() -> None:
    """El diff sigue delatando columnas inventadas — es el guard que evita
    repetir el bug si alguien vuelve a inyectar a mano."""
    payload = {"CODFAC": "1", "CLIFAC": 22, "EJEFAC": "2026", "SERFAC": "A"}
    real = ["CODFAC", "CLIFAC", "TOTFAC", "REFFAC", "TIPFAC", "FECFAC"]
    assert payload_column_diff(payload, real)[0] == ["EJEFAC", "SERFAC"]


# ---------------------------------------------------------------------------
# Sondeo de tablas / numeración
# ---------------------------------------------------------------------------


def test_probe_table_reports_columns_and_sample() -> None:
    client = _FakeClient({"F_ALB": [{"CODALB": 1, "CLIALB": 9}]})
    res = probe_table(client, "F_ALB", "2026")
    assert res["ok"] and res["rows"] == 1
    assert res["columns"] == ["CODALB", "CLIALB"]


def test_probe_table_missing_table_is_indistinguishable_from_empty() -> None:
    """Gotcha nº 11: la API devuelve [] en ambos casos; el script lo dice."""
    client = _FakeClient({})
    res = probe_table(client, "F_NOEXISTE", "2026")
    assert res["ok"] and res["rows"] == 0 and res["columns"] == []


def test_probe_table_captures_error_without_raising() -> None:
    class _Boom:
        def load_table(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
            raise RuntimeError("token rechazado")

    res = probe_table(_Boom(), "F_ALB", "2026")
    assert res["ok"] is False
    assert "token rechazado" in res["error"]


def test_summarize_numbering_detects_max_and_gaps() -> None:
    rows = [{"CODALB": n} for n in (10, 11, 13)]
    stats = summarize_numbering(rows, "CODALB")
    assert stats["numeric"] and stats["max"] == 13
    assert stats["huecos"] == 1  # falta el 12
    assert stats["ultimos"] == [10, 11, 13]


def test_summarize_numbering_handles_non_numeric_pk() -> None:
    stats = summarize_numbering([{"CODALB": "A/1"}], "CODALB")
    assert stats == {"count": 1, "numeric": False}


def test_distinct_values_dedupes_and_normalizes() -> None:
    rows = [{"ESTALB": 0}, {"ESTALB": "0"}, {"ESTALB": 1}]
    assert distinct_values(rows, "ESTALB") == ["0", "1"]


# ---------------------------------------------------------------------------
# Guard de seguridad del sondeo a ciegas
# ---------------------------------------------------------------------------


def test_is_safe_probe_path_blocks_write_verbs() -> None:
    assert is_safe_probe_path("/admin/ImprimirDocumento")
    assert is_safe_probe_path("/admin/GenerarPDF")
    assert not is_safe_probe_path("/admin/EscribirRegistro")
    assert not is_safe_probe_path("/admin/BorrarRegistros")
    assert not is_safe_probe_path("/admin/ActualizarRegistro")


def test_print_endpoint_candidates_are_all_safe() -> None:
    from scripts.factusol_discover_albaranes import PRINT_ENDPOINT_CANDIDATES

    assert all(is_safe_probe_path(p) for p in PRINT_ENDPOINT_CANDIDATES)


# ---------------------------------------------------------------------------
# Smoke end-to-end de los bloques de informe
#
# Sin esto, un typo en una f-string del reporte solo lo descubriría Bart
# ejecutando contra FACTUSOL real — que es justo lo que no queremos.
# ---------------------------------------------------------------------------


class _ChainClient(_FakeClient):
    """Cadena PRE 574 → ALB 91 → FAC 260695, con la referencia escondida en
    columnas de nombre arbitrario (`PREALB` / `ALBFAC`) para comprobar que el
    escaneo las encuentra sin conocerlas de antemano."""

    def __init__(self) -> None:
        super().__init__({
            "F_ALB": [
                {"CODALB": 91, "TIPALB": "1", "PREALB": "574", "CLIALB": 22,
                 "SERALB": "", "ESTALB": 0, "TOTALB": 355.0},
                {"CODALB": 92, "TIPALB": "1", "PREALB": "", "CLIALB": 30,
                 "SERALB": "", "ESTALB": 1, "TOTALB": 12.0},
            ],
            "F_LAL": [{"CODLAL": 91, "POSLAL": 1, "ARTLAL": "99cy"}],
            "F_PRE": [{"CODPRE": 574, "ESTPRE": 1, "TOTPRE": 355.0}],
            "F_FAC": [{"CODFAC": 260695, "ALBFAC": "91", "CLIFAC": 22}],
        })

    def load_table(
        self, tabla: str, *, filtro: str = "1=1", ejercicio: str = "2026"
    ) -> list[dict[str, Any]]:
        rows = super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)
        if filtro.startswith("1=1"):
            return rows
        column, _, wanted = filtro.partition("=")
        return [r for r in rows if str(r.get(column)) == wanted]


def test_discovery_report_runs_end_to_end(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import (
        discover_line_link,
        discover_numbering,
        discover_structure,
    )

    client = _ChainClient()
    results = discover_structure(client, "2026", [])
    discover_line_link(client, "2026", results)
    discover_numbering(results)
    out = capsys.readouterr().out
    assert "F_ALB" in out
    assert "✅ ES LA FK" in out  # CODLAL=91 casa con el albarán 91
    assert "next_codalb = MAX+1 = 93" in out
    assert "ESTALB: valores distintos → ['0', '1']" in out


def test_trace_chain_follows_pre_to_alb_to_fac(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import trace_chain

    trace_chain(_ChainClient(), "2026", "574")
    out = capsys.readouterr().out
    # Encuentra la referencia sin que nadie le diga cómo se llama la columna.
    assert "F_ALB.PREALB" in out
    assert "F_FAC.ALBFAC" in out


def test_trace_chain_reports_missing_proforma(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import trace_chain

    trace_chain(_FakeClient({"F_PRE": []}), "2026", "999")
    assert "No existe F_PRE con CODPRE=999" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Fase 2 — `--alb-row` (volcado real) y `--albaran-dry-run` (registro exacto)
#
# Cliente falso con el filtro de igualdad del lector real (clave compuesta:
# `CODALB=500004` devuelve también el homónimo de otra serie y el caller lo
# casa por TIP*) y SIN `write_record`: cualquier intento de escribir revienta.
# ---------------------------------------------------------------------------


class _ReadOnlyClient(_FakeClient):
    def load_table(
        self, tabla: str, *, filtro: str = "1=1", ejercicio: str = "2026"
    ) -> list[dict[str, Any]]:
        rows = super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        column, _, wanted = predicate.partition("=")
        column, wanted = column.strip(), wanted.strip().strip("'")
        if rows and column not in rows[0]:
            return []  # gotcha nº 1
        return [r for r in rows if str(r.get(column)) == wanted]

    def write_record(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover
        raise AssertionError("el discovery NO escribe en FACTUSOL")

    update_record = delete_records = write_record


def _alb_real(codigo: int, serie: str = "5", **over: Any) -> dict[str, Any]:
    """Fila real de F_ALB tal como la devuelve CargaTabla (tipos mezclados:
    TIPALB str, CLIALB int, fechas con hora, ESTALB int)."""
    from app.integrations.factusol.chain import ALB_REFERENCE_COLUMNS

    row: dict[str, Any] = {c: "" for c in ALB_REFERENCE_COLUMNS}
    row.update({
        "TIPALB": serie, "CODALB": codigo, "FECALB": "2026-08-20T00:00:00",
        "ESTALB": 1, "CLIALB": 2458, "CNOALB": "DUPLICODER, S.L.",
        "TOTALB": 186.34, "NET1ALB": 154.0, "PIVA1ALB": 21.0, "FOPALB": "002",
        "PEDALB": "", "REFALB": "Obra X", "ALMALB": "GEN", "USUALB": "BART",
    })
    row.update(over)
    return row


def _lal_real(codigo: int, pos: int, serie: str = "5", **over: Any) -> dict[str, Any]:
    from app.integrations.factusol.chain import LAL_REFERENCE_COLUMNS

    row: dict[str, Any] = {c: "" for c in LAL_REFERENCE_COLUMNS}
    row.update({
        "TIPLAL": serie, "CODLAL": codigo, "POSLAL": pos, "ARTLAL": "99cy",
        "DESLAL": "Tinta cyan", "CANLAL": 2, "PRELAL": 40.0, "TOTLAL": 80.0,
        "IVALAL": 21, "DOCLAL": "P", "DTPLAL": "5", "DCOLAL": 27, "EJELAL": 2026,
    })
    row.update(over)
    return row


def _fase2_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        # Presupuesto 5-27 (aceptado) y su homónimo de otra serie.
        "F_PRE": [
            {"TIPPRE": "5", "CODPRE": 27, "CLIPRE": 2458, "CNOPRE": "DUPLICODER, S.L.",
             "FECPRE": "2026-08-01T00:00:00", "ESTPRE": 1, "REFPRE": "Obra X",
             "TOTPRE": 186.34, "NET1PRE": 154.0, "PIVA1PRE": 21.0, "FOPPRE": "002",
             "ALMPRE": "GEN", "USUPRE": "BART", "IMPPRE": 1},
            {"TIPPRE": "2", "CODPRE": 27, "CLIPRE": 7, "CNOPRE": "OTRA SL",
             "ESTPRE": 0, "TOTPRE": 1.0},
        ],
        "F_LPS": [
            {"TIPLPS": "5", "CODLPS": 27, "POSLPS": 1, "ARTLPS": "99cy",
             "DESLPS": "Tinta cyan", "CANLPS": 2, "PRELPS": 40.0, "TOTLPS": 80.0,
             "IVALPS": 21},
            {"TIPLPS": "5", "CODLPS": 27, "POSLPS": 2, "ARTLPS": "",
             "DESLPS": "Portes", "CANLPS": 1, "PRELPS": 74.0, "TOTLPS": 74.0},
            {"TIPLPS": "2", "CODLPS": 27, "POSLPS": 1, "ARTLPS": "XX",
             "DESLPS": "De otra serie", "CANLPS": 9, "PRELPS": 1.0, "TOTLPS": 9.0},
        ],
        # Pedido de cliente 5-123 «Enviado» (ESTPCL=2) con una columna que
        # F_ALB no tiene (PENPCL) y sus líneas.
        "F_PCL": [
            {"TIPPCL": "5", "CODPCL": 123, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
             "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 2, "REFPCL": "BOP-099917",
             "TOTPCL": 60.5, "NET1PCL": 50.0, "FOPPCL": "011", "PENPCL": 0,
             "ALMPCL": "GEN"},
        ],
        "F_LPC": [
            {"TIPLPC": "5", "CODLPC": 123, "POSLPC": 1, "ARTLPC": "CDR80WPT",
             "DESLPC": "CD TQ 700 MB", "CANLPC": 100, "PRELPC": 0.5,
             "TOTLPC": 50.0, "PENLPC": 0},
        ],
        # Albarán REAL 5-500004 (hijo del presupuesto 5-27, facturado) + el
        # homónimo de la serie 1 + uno más reciente de la serie 5.
        "F_ALB": [
            _alb_real(500004, "5"),
            _alb_real(500004, "1", CNOALB="AJENO SL", TOTALB=1.0),
            _alb_real(500005, "5", ESTALB=0, TOTALB=9.0),
        ],
        "F_LAL": [
            _lal_real(500004, 1, "5"),
            _lal_real(500004, 2, "5", ARTLAL="", DESLAL="Portes", CANLAL=1,
                      PRELAL=74.0, TOTLAL=74.0),
            _lal_real(500004, 1, "1", DESLAL="ajena", TOTLAL=1.0),
            _lal_real(500005, 1, "5", DOCLAL="", DTPLAL="", DCOLAL=0),
        ],
    }


def test_parse_document_number_requires_series() -> None:
    from scripts.factusol_discover_albaranes import parse_document_number

    assert parse_document_number("5-500004") == (5, 500004)
    assert parse_document_number(" 1-000027 ") == (1, 27)
    import pytest

    for bad in ("500004", "5-", "-5", "A-1", ""):
        with pytest.raises(ValueError):
            parse_document_number(bad)


def test_type_mismatches_flags_json_type_drift() -> None:
    """La lección F-4-B: DELSOL quiere de vuelta el MISMO tipo que devuelve.
    '5' vs 5 y '' vs 0 son desajustes; int vs float y None no."""
    from scripts.factusol_discover_albaranes import type_mismatches

    template = {"TIPALB": "5", "CLIALB": 2458, "ESTALB": 1, "TOTALB": 186.34,
                "PEDALB": "", "REQALB": 0, "FECALB": "2026-08-20T00:00:00"}
    payload = {"TIPALB": "5", "CLIALB": "2458", "ESTALB": 1, "TOTALB": 186,
               "PEDALB": "", "REQALB": "", "FECALB": "2026-09-11", "NUEVA": 1}
    out = type_mismatches(payload, template)
    assert out == [("CLIALB", "str", "int"), ("REQALB", "str", "int")]


def test_date_format_hints_detects_missing_time_part() -> None:
    from scripts.factusol_discover_albaranes import date_format_hints

    template = {"FECALB": "2026-08-20T00:00:00", "REFALB": "2026-01"}
    assert date_format_hints({"FECALB": "2026-09-11"}, template) == [
        ("FECALB", "2026-09-11", "2026-08-20T00:00:00"),
    ]
    assert date_format_hints({"FECALB": "2026-09-11T00:00:00"}, template) == []
    assert date_format_hints({"REFALB": "2026-01-01"}, template) == []


def test_compare_with_origin_separates_expected_and_real_changes() -> None:
    """Lo que el escritorio cambia FUERA de clave/fecha/auditoría es lo que
    BoHub tiene que sobrescribir además de lo mínimo."""
    from scripts.factusol_discover_albaranes import compare_with_origin

    origin = {"TIPPRE": "5", "CODPRE": 27, "CLIPRE": 2458, "ESTPRE": 0,
              "FECPRE": "2026-08-01T00:00:00", "USUPRE": "BART", "TOTPRE": 186.34}
    child = {"TIPALB": "5", "CODALB": 500004, "CLIALB": 2458, "ESTALB": 1,
             "FECALB": "2026-08-20T00:00:00", "USUALB": "API", "TOTALB": 186.34,
             "PEDALB": ""}
    cmp = compare_with_origin(child, origin, child_suffix="ALB", origin_suffix="PRE")
    assert cmp["iguales"] == ["TIPALB", "CLIALB", "TOTALB"]
    assert [c for c, _, _ in cmp["esperadas"]] == ["CODALB", "FECALB", "USUALB"]
    assert cmp["cambiadas"] == [("ESTALB", 0, 1)]
    assert cmp["solo_hijo"] == ["PEDALB"]


def test_pick_template_document_prefers_latest_of_same_series() -> None:
    from scripts.factusol_discover_albaranes import pick_template_document

    rows = _fase2_tables()["F_ALB"]
    tpl = pick_template_document(rows, tip_col="TIPALB", cod_col="CODALB", serie=5)
    assert (tpl["TIPALB"], tpl["CODALB"]) == ("5", 500005)
    other = pick_template_document(rows, tip_col="TIPALB", cod_col="CODALB", serie=9)
    assert other is not None  # sin filas de esa serie: la más reciente de todas
    assert pick_template_document([], tip_col="TIPALB", cod_col="CODALB", serie=5) is None


def test_alb_row_dumps_header_lines_and_origin_diff(capsys: Any) -> None:
    """`--alb-row 5-500004`: cabecera y líneas columna a columna CON tipo,
    solo las de la serie 5 (no la línea ajena del 1-500004), el enlace de las
    líneas al presupuesto y qué cambió el escritorio al convertir."""
    from scripts.factusol_discover_albaranes import dump_albaran

    client = _ReadOnlyClient(_fase2_tables())
    result = dump_albaran(client, "2026", "5-500004")
    out = capsys.readouterr().out
    assert result is not None
    assert "ALBARÁN REAL 5-500004" in out
    assert "TIPALB     str    '5'" in out
    assert "CLIALB     int    2458" in out
    assert "F_LAL — 2 línea(s)" in out
    assert "ajena" not in out                      # homónimo de la serie 1 fuera
    assert "TOTALB=186.34 · suma TOTLAL=154.0" in out
    assert "ESTALB=1 → Facturado" in out
    assert "DOCLAL='P' DTPLAL='5' DCOLAL='27' → presupuesto 5-000027" in out
    # Comparación con el origen: ESTALB cambió (0→1 no: aquí origen 1, hijo 1
    # → igual); FOPALB copiado tal cual; FECALB cambio esperado.
    cmp = result["comparison"]
    assert "FOPALB" in cmp["iguales"] and "CLIALB" in cmp["iguales"]
    assert any(c == "FECALB" for c, _, _ in cmp["esperadas"])
    assert "SOLO LECTURA" in out


def test_alb_row_reports_missing_document(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import dump_albaran

    assert dump_albaran(_ReadOnlyClient(_fase2_tables()), "2026", "5-999999") is None
    assert "No existe el documento 5-999999" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tarea C — `--cli-row`: fila real de F_CLI (tipo de documento / régimen IVA)
# ---------------------------------------------------------------------------


def _cli_tables() -> dict[str, list[dict[str, Any]]]:
    """Dos clientes con TODAS las columnas que devolvería CargaTabla, incluidas
    las que BoHub no conoce (nombres inventados solo para el test: manda el
    volcado real)."""
    base = {
        "CODCLI": 2458, "NIFCLI": "B12345678", "NOFCLI": "DUPLICODER, S.L.",
        "NOCCLI": "Duplicoder", "DOMCLI": "C/ Mayor 1", "POBCLI": "Girona",
        "CPOCLI": "17001", "PROCLI": "Girona", "PAICLI": "724", "EMACLI": "",
        "TELCLI": "", "TPDCLI": 0, "TIVCLI": 0, "REQCLI": 0, "TARCLI": 1,
        "FPACLI": "002", "WEBCLI": "",
    }
    intra = {**base, "CODCLI": 3101, "NIFCLI": "BE0123456789", "NOFCLI": "ACME BV",
             "NOCCLI": "Acme", "POBCLI": "Antwerpen", "PAICLI": "056",
             "TPDCLI": 1, "TIVCLI": 2}
    return {"F_CLI": [base, intra]}


def test_cli_row_dumps_customer_marking_known_and_candidate_columns(capsys: Any) -> None:
    """`--cli-row 2458`: fila completa columna · tipo · valor, marcando lo que
    BoHub escribe (11 columnas) y las candidatas por prefijo a tipo de
    documento / régimen de IVA / recargo. Solo lectura y sin adivinar: el
    volcado real manda."""
    from scripts.factusol_discover_albaranes import dump_customer

    client = _FakeClient(_cli_tables())
    result = dump_customer(client, "2026", "2458")
    out = capsys.readouterr().out
    assert result is not None and result["codcli"] == "2458"
    assert client.calls == [("F_CLI", "CODCLI=2458")]
    assert "CLIENTE REAL F_CLI · CODCLI=2458" in out
    # 11 de siempre + TIVCLI, que desde la Parte 2 BoHub escribe (la fila del
    # test no trae IFICLI/IVACLI).
    assert "BoHub escribe 12 (" in out
    assert "NIFCLI     str    'B12345678'  ← BoHub escribe" in out
    assert "TPDCLI     int    0  ← candidata: tipo de documento del identificador" in out
    assert "TIVCLI     int    0  ← BoHub escribe" in out
    assert "REQCLI     int    0  ← candidata: recargo de equivalencia" in out
    tarcli_line = out.split("TARCLI")[1].split("\n")[0]
    assert "int    1" in tarcli_line and "← candidata" not in tarcli_line
    assert "régimen de IVA" not in result["candidates"]   # ya confirmada: TIVCLI
    assert "SOLO LECTURA" in out


def test_cli_row_compares_customers_and_reports_missing(capsys: Any) -> None:
    """Con 2+ clientes: qué columnas difieren fuera de las que BoHub escribe
    (ahí están el tipo de documento y el régimen). CODCLI inexistente o no
    numérico → aviso, sin excepción."""
    from scripts.factusol_discover_albaranes import (
        compare_customer_dumps,
        dump_customer,
    )

    client = _FakeClient(_cli_tables())
    dumps = [dump_customer(client, "2026", "2458"), dump_customer(client, "2026", "3101")]
    differing = compare_customer_dumps([d for d in dumps if d])
    out = capsys.readouterr().out
    # Nombre/dirección no cuentan, y TIVCLI tampoco desde que BoHub la escribe.
    assert set(differing) == {"TPDCLI"}
    assert differing["TPDCLI"] == [0, 1]
    assert "COLUMNAS QUE DIFIEREN ENTRE LOS CLIENTES VOLCADOS (CODCLI=2458 · CODCLI=3101)" in out
    assert "TPDCLI     0 | 1  ← tipo de documento del identificador" in out
    assert dump_customer(client, "2026", "9999") is None
    assert dump_customer(client, "2026", "abc") is None
    out = capsys.readouterr().out
    assert "No existe el cliente CODCLI=9999" in out and "CODCLI inválido" in out
    assert compare_customer_dumps([dumps[0]]) == {}


def test_regimen_iva_report_flags_customers_and_invoices(capsys: Any) -> None:
    """`--regimen-iva` (Tarea C · Parte 2): clientes con `PAICLI` no numérico,
    régimen incoherente con el país + NIF-IVA o `IFICLI` incoherente, y
    facturas del ejercicio con IVA a clientes intracomunitarios / de
    exportación. Solo lectura (el cliente falso no sabe escribir)."""
    from scripts.factusol_discover_albaranes import regime_report

    def cli(codcli: int, nif: str, pais: str, ifi: int, iva: int, tiv: int) -> dict[str, Any]:
        return {"CODCLI": codcli, "NIFCLI": nif, "NOFCLI": f"Cliente {codcli}",
                "NOCCLI": "", "PAICLI": pais, "IFICLI": ifi, "IVACLI": iva,
                "TIVCLI": tiv}

    def fac(tip: str, cod: int, cli: int, iva: float) -> dict[str, Any]:
        return {"TIPFAC": tip, "CODFAC": cod, "CLIFAC": cli, "FECFAC": "2026-08-01T00:00:00",
                "REFFAC": f"REF-{cod}", "PIVA1FAC": 21.0 if iva else 0.0,
                "IIVA1FAC": iva, "TOTFAC": 100.0 + iva}

    client = _FakeClient({
        "F_CLI": [
            cli(3011, "48288265H", "724", 0, 0, 1),        # nacional bien
            cli(3392, "BE0812240188", "056", 0, 0, 1),     # intracom. como nacional
            cli(525, "NO 976 029 100", "Norway", 0, 3, 3),  # PAICLI literal
            cli(4279, "DE455128445", "276", 2, 2, 4),      # bien (a mano)
            cli(7, "X", "", 0, 0, 1),                      # sin país
        ],
        "F_FAC": [
            fac("1", 260001, 3392, 21.0),    # IVA a un intracomunitario → sale
            fac("1", 260002, 3011, 21.0),    # nacional → no
            fac("5", 500001, 4279, 0.0),     # intracomunitario sin IVA → no
            fac("5", 500002, 525, 4.0),      # exportación con IVA → sale
        ],
    })
    result = regime_report(client, "2026")
    out = capsys.readouterr().out
    flagged = {c["codcli"]: c for c in result["customers"]}
    assert set(flagged) == {"3392", "525", "7"}
    assert flagged["3392"]["problems"] == [
        "régimen Nacional (con IVA) pero por país / NIF debería ser Intracomunitario (exento)",
        "IFICLI=0 (tipo de documento) pero Intracomunitario (exento) lleva 2",
    ]
    assert flagged["525"]["problems"] == ["PAICLI no numérico ('Norway')"]
    assert flagged["7"]["problems"] == ["sin país (PAICLI vacío)"]
    assert [i["numero"] for i in result["invoices"]] == ["1-260001", "5-500002"]
    assert result["invoices"][0]["regime"] == "intracomunitario"
    assert "5 clientes · 3 con el dato mal" in out
    assert "3392   BE0812240188     Cliente 3392" in out
    assert ("FACTURAS DEL EJERCICIO CON IVA A CLIENTES INTRACOMUNITARIOS / DE EXPORTACIÓN "
            "(2 de 4)") in out
    assert "NO se reescribe ninguna factura" in out
    assert "SOLO LECTURA" in out


def test_dry_run_presupuesto_builds_exact_record_without_writing(capsys: Any) -> None:
    """El registro que BoHub enviaría desde el presupuesto 5-27: clave nueva
    (siguiente de la serie 5 → 500006), enlace DOC='P' por línea, columnas
    solo vivas, contraste de tipos con la fila real y aviso de ESTALB
    heredado = 1. Nada escrito (el cliente revienta si se intenta)."""
    from scripts.factusol_discover_albaranes import albaran_dry_run

    client = _ReadOnlyClient(_fase2_tables())
    result = albaran_dry_run(client, "2026", "presupuestos", "5-000027")
    out = capsys.readouterr().out
    assert result is not None
    cab = result["cabecera"]
    assert cab["TIPALB"] == "5" and cab["CODALB"] == 500006      # entero
    assert cab["ESTALB"] == 0                                    # no heredado
    assert cab["CNOALB"] == "DUPLICODER, S.L." and cab["FOPALB"] == "002"
    assert "USUALB" not in cab and "IMPALB" not in cab   # auditoría fuera
    assert len(result["lineas"]) == 2                    # sin la línea de la serie 2
    assert all(ln["DOCLAL"] == "P" and ln["DTPLAL"] == "5" and ln["DCOLAL"] == 27
               for ln in result["lineas"])
    assert result["unknown"] == []
    assert "Destino: F_ALB 5-500006" in out
    assert "REGISTRO F_ALB que se enviaría" in out
    assert "plantilla real: F_ALB 5-500005" in out
    assert "✅ ESTALB=0 (Pendiente)" in out               # fijado por el builder
    assert "FECALB: payload '" in out                    # fecha sin hora (aviso)
    assert "✅ todos los tipos coinciden" in out and result["ok"] is True
    assert "SOLO LECTURA" in out


def test_dry_run_pedido_uses_origin_c_and_flags_estpcl(capsys: Any) -> None:
    """Desde un pedido de cliente: enlace DOC='C', las columnas que F_ALB no
    tiene (PENPCL/PENLPC) se descartan y ESTPCL=2 NO se hereda (nace 0)."""
    from scripts.factusol_discover_albaranes import albaran_dry_run

    client = _ReadOnlyClient(_fase2_tables())
    result = albaran_dry_run(client, "2026", "pedidos", "5-000123")
    out = capsys.readouterr().out
    assert result is not None
    cab = result["cabecera"]
    assert cab["REFALB"] == "BOP-099917" and cab["FOPALB"] == "011"
    assert "PENALB" not in cab
    assert result["lineas"][0]["DOCLAL"] == "C"
    assert result["lineas"][0]["DCOLAL"] == 123
    assert "PENLAL" not in result["lineas"][0]
    assert cab["ESTALB"] == 0 and "✅ ESTALB=0" in out
    assert "sin equivalente en F_ALB (se descartan): PENPCL" in out
    assert result["unknown"] == [] and result["ok"] is True


def test_dry_run_rejects_bad_source_or_missing_document(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import albaran_dry_run

    client = _ReadOnlyClient(_fase2_tables())
    assert albaran_dry_run(client, "2026", "albaranes", "5-500004") is None
    assert albaran_dry_run(client, "2026", "presupuestos", "5-000999") is None
    out = capsys.readouterr().out
    assert "origen no soportado" in out and "No existe el documento 5-999" in out


def test_dry_run_serie_override_changes_counter(capsys: Any) -> None:
    from scripts.factusol_discover_albaranes import albaran_dry_run

    client = _ReadOnlyClient(_fase2_tables())
    result = albaran_dry_run(
        client, "2026", "presupuestos", "5-000027", serie_override=1,
    )
    _ = capsys.readouterr()
    assert result["cabecera"]["TIPALB"] == "1"
    assert result["cabecera"]["CODALB"] == 500005     # siguiente de la serie 1
    # El enlace sigue apuntando al ORIGEN real (serie 5), no a la serie destino.
    assert result["lineas"][0]["DTPLAL"] == "5"

