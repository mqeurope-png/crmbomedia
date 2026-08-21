"""ERP-E3-A — explorador de documentos FACTUSOL (solo lectura, en vivo).

Cliente FACTUSOL mockeado. Cubre la normalización, los filtros combinados,
la red anti-gotcha-1 (filtro SQL que devuelve `[]` → refetch + Python), la
paginación, el detalle con join compuesto y los endpoints HTTP.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.integrations.factusol.documents import (
    DOC_SPECS,
    estado_label,
    get_document,
    list_documents,
    normalize_header,
    visible_number,
)
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class FakeClient:
    """Sirve tablas en memoria aplicando el `filtro` de igualdad más común.
    `strict_columns=True` simula el gotcha nº 1: un filtro que referencia una
    columna que la tabla no tiene devuelve `[]` en silencio."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]],
                 *, strict_columns: bool = True):
        self.tables = tables
        self.strict = strict_columns
        self.calls: list[tuple[str, str]] = []
        self.default_ejercicio = "2026"

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((tabla, filtro))
        rows = list(self.tables.get(tabla, []))
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        column, _, raw = predicate.partition("=")
        column = column.strip()
        wanted = raw.strip().strip("'")
        if self.strict and rows and column not in rows[0]:
            return []  # gotcha nº 1: columna inexistente → [] sin error
        return [r for r in rows if str(r.get(column)) == wanted]


def _fac(codigo: int, serie: str = "5", **over: Any) -> dict[str, Any]:
    row = {
        "TIPFAC": serie, "CODFAC": codigo, "CLIFAC": 2458,
        "CNOFAC": "DUPLICODER, S.L.", "FECFAC": "2026-08-01T00:00:00",
        "ESTFAC": 0, "REFFAC": f"BOP-{codigo:06d}", "TOTFAC": 186.34,
    }
    row.update(over)
    return row


FACTURAS = [
    _fac(260066, "5"),
    _fac(260065, "5", CLIFAC=99, CNOFAC="MOVIATICOS", FECFAC="2026-07-15",
         TOTFAC=50.0),
    _fac(260736, "1", CNOFAC="ACME SL", FECFAC="2026-06-01"),
    _fac(526082, "2", CNOFAC="OTRA SL"),
]


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------


def test_visible_number_pads_and_prefixes_serie() -> None:
    assert visible_number("5", 5) == "5-000005"
    assert visible_number(5, 260066) == "5-260066"
    assert visible_number(None, 7) == "000007"


def test_normalize_header_tolerates_missing_columns() -> None:
    """F_ALB aún no tiene columnas confirmadas: una fila con nombres
    distintos no revienta — los campos ausentes salen a None."""
    doc = normalize_header("albaranes", {"CODALB": 91, "TIPALB": "5"})
    assert doc["numero"] == "5-000091"
    assert doc["cliente_nombre"] is None and doc["total"] is None
    assert doc["estado_label"] == "—"


def test_estado_labels_confirmed_and_raw() -> None:
    assert estado_label("pedidos", 0) == "Pendiente"
    assert estado_label("pedidos", "2.0") == "Enviado (facturado)"
    assert estado_label("presupuestos", 1) == "Aceptado"
    # Sin confirmar → crudo neutro, nunca adivinar (criterio E2/gotcha 17).
    assert estado_label("albaranes", 3) == "Estado 3"
    assert estado_label("facturas", 0) == "Estado 0"


# ---------------------------------------------------------------------------
# Listado + filtros
# ---------------------------------------------------------------------------


def test_list_documents_normalizes_and_sorts_desc() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    out = list_documents(client, "facturas", ejercicio="2026")
    assert out["total"] == 4
    assert [d["codigo"] for d in out["items"]] == [526082, 260736, 260066, 260065]
    first = out["items"][0]
    assert first["numero"] == "2-526082"
    assert first["cliente_nombre"] == "OTRA SL"
    assert first["fecha"] == "2026-08-01"


def test_list_documents_pushes_client_filter_to_sql() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    out = list_documents(client, "facturas", ejercicio="2026", codcli="99")
    assert out["total"] == 1
    assert out["items"][0]["cliente_nombre"] == "MOVIATICOS"
    # El predicado fue server-side (CLIFAC=99), no un fetch completo.
    assert client.calls[0][1].startswith("CLIFAC=99")


def test_list_documents_combined_filters() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    out = list_documents(
        client, "facturas", ejercicio="2026",
        serie=5, fecha_desde="2026-07-01", fecha_hasta="2026-07-31",
    )
    assert [d["codigo"] for d in out["items"]] == [260065]


def test_list_documents_text_search_matches_ref_and_name() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    by_ref = list_documents(client, "facturas", ejercicio="2026", q="BOP-260736")
    assert [d["codigo"] for d in by_ref["items"]] == [260736]
    by_name = list_documents(client, "facturas", ejercicio="2026", q="moviaticos")
    assert [d["codigo"] for d in by_name["items"]] == [260065]


def test_list_documents_pagination() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    page = list_documents(client, "facturas", ejercicio="2026", limit=2, offset=2)
    assert page["total"] == 4
    assert [d["codigo"] for d in page["items"]] == [260066, 260065]


def test_list_documents_fallback_when_sql_filter_hits_unknown_column() -> None:
    """Red anti-gotcha-1: la tabla no tiene la columna del predicado (el fake
    en modo estricto devuelve `[]`, como la API real) → refetch con 1=1 y
    filtrado en Python. El resultado es correcto, no un falso vacío."""
    rows = [
        # F_ALB "real" con columnas distintas a la convención de cliente.
        {"TIPALB": "5", "CODALB": 91, "CCLALB": "2458", "TOTALB": 10.0},
        {"TIPALB": "1", "CODALB": 90, "CCLALB": "7", "TOTALB": 20.0},
    ]
    client = FakeClient({"F_ALB": rows}, strict_columns=True)
    out = list_documents(client, "albaranes", ejercicio="2026", serie=5)
    # TIPALB sí existe → el predicado de serie funciona normal.
    assert [d["codigo"] for d in out["items"]] == [91]
    # Ahora un filtro de cliente sobre CLIALB (que esta tabla no tiene):
    out2 = list_documents(client, "albaranes", ejercicio="2026", codcli="2458")
    # El fallback refetch se disparó (dos llamadas a F_ALB en esta query)...
    calls = [f for t, f in client.calls if t == "F_ALB"]
    assert any(f.startswith("CLIALB=") for f in calls)
    assert any(f.startswith("1=1") for f in calls)
    # ...y el filtro en Python, al no poder casar CLIALB, devuelve 0 — pero
    # NUNCA por el `[]` silencioso del predicado: por el matching explícito.
    assert out2["total"] == 0


def test_list_documents_no_fallback_when_genuinely_empty() -> None:
    """`1=1` que devuelve [] NO re-consulta (la tabla está vacía de verdad)."""
    client = FakeClient({"F_ALB": []})
    out = list_documents(client, "albaranes", ejercicio="2026")
    assert out == {"items": [], "total": 0}
    assert len([c for c in client.calls if c[0] == "F_ALB"]) == 1


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


LINEAS_LFA = [
    {"TIPLFA": "5", "CODLFA": 260066, "POSLFA": 2, "ARTLFA": "99cy",
     "DESLFA": "Tinta cyan", "CANLFA": 2, "PRELFA": 40.0, "TOTLFA": 80.0},
    {"TIPLFA": "5", "CODLFA": 260066, "POSLFA": 1, "ARTLFA": "",
     "DESLFA": "Servicio", "CANLFA": 1, "PRELFA": 106.34, "TOTLFA": 106.34},
    # Línea de la factura HOMÓNIMA de otra serie: no puede colarse.
    {"TIPLFA": "1", "CODLFA": 260066, "POSLFA": 1, "ARTLFA": "XX",
     "DESLFA": "De otra serie", "CANLFA": 9, "PRELFA": 1.0, "TOTLFA": 9.0},
]


def test_get_document_returns_header_and_series_scoped_lines() -> None:
    client = FakeClient({
        "F_FAC": FACTURAS + [_fac(260066, "1", CNOFAC="HOMÓNIMA")],
        "F_LFA": LINEAS_LFA,
    })
    doc = get_document(client, "facturas", serie=5, codigo=260066,
                       ejercicio="2026")
    assert doc is not None
    assert doc["numero"] == "5-260066"
    assert doc["cliente_nombre"] == "DUPLICODER, S.L."
    # Solo las líneas de la serie 5, ordenadas por posición.
    assert [ln["description"] for ln in doc["lines"]] == ["Servicio", "Tinta cyan"]


def test_get_document_missing_returns_none() -> None:
    client = FakeClient({"F_FAC": FACTURAS})
    assert get_document(client, "facturas", serie=7, codigo=1,
                        ejercicio="2026") is None


# ---------------------------------------------------------------------------
# Endpoints HTTP
# ---------------------------------------------------------------------------


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
def client(session_factory) -> Generator[TestClient, None, None]:
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


def test_documents_endpoint_lists_with_filters(client, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        r = client.get(
            "/api/erp/factusol/documents/facturas?serie=5",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [d["numero"] for d in body["items"]] == ["5-260066", "5-260065"]
    assert body["items"][0]["estado_label"] == "Estado 0"


def test_documents_endpoint_rejects_unknown_type(client) -> None:
    r = client.get(
        "/api/erp/factusol/documents/nominas",
        headers=auth_headers(client, "user"),
    )
    assert r.status_code == 404


def test_documents_endpoint_requires_auth(client) -> None:
    assert client.get(
        "/api/erp/factusol/documents/facturas"
    ).status_code in (401, 403)


def test_document_detail_endpoint(client, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS, "F_LFA": LINEAS_LFA})):
        r = client.get(
            "/api/erp/factusol/documents/facturas/5/260066",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["numero"] == "5-260066"
    assert len(body["lines"]) == 2
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        missing = client.get(
            "/api/erp/factusol/documents/facturas/7/1",
            headers=auth_headers(client, "user"),
        )
    assert missing.status_code == 404


def test_all_doc_specs_cover_the_four_tables() -> None:
    assert {s.table for s in DOC_SPECS.values()} == {
        "F_PCL", "F_PRE", "F_ALB", "F_FAC",
    }
    assert {s.lines_table for s in DOC_SPECS.values()} == {
        "F_LPC", "F_LPS", "F_LAL", "F_LFA",
    }
