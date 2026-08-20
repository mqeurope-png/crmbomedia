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

