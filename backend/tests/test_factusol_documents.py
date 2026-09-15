"""ERP-E3-A — explorador de documentos FACTUSOL (solo lectura, en vivo).

Cliente FACTUSOL mockeado. Cubre la normalización, los filtros combinados,
la red anti-gotcha-1 (filtro SQL que devuelve `[]` → refetch + Python), la
paginación, el detalle con join compuesto y los endpoints HTTP.
"""
from __future__ import annotations

import re
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
from app.erp.models import Order, OrderSource
from app.integrations.factusol.documents import (
    DOC_SPECS,
    estado_label,
    get_document,
    list_documents,
    normalize_header,
    visible_number,
)
from app.main import app
from app.models.crm import Company
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
        if " LIKE " in predicate:
            # `COL LIKE '%-005789'` (comodines SQL `%`/`_`, sin distinguir
            # mayúsculas, como el servidor real).
            column, _, raw = predicate.partition(" LIKE ")
            column = column.strip()
            pattern = raw.strip().strip("'")
            if self.strict and rows and column not in rows[0]:
                return []
            regex = "^" + "".join(
                ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                for ch in pattern
            ) + "$"
            return [
                r for r in rows
                if re.match(regex, str(r.get(column) or ""), flags=re.IGNORECASE)
            ]
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
    # ERP-F3/F3-fix1: ESTFAC confirmado (estado de COBRO): 0 pendiente, 1 cobro
    # parcial, 2 cobrada; el resto sigue crudo.
    assert estado_label("facturas", 0) == "Pendiente de cobro"
    assert estado_label("facturas", 1) == "Cobro parcial"
    assert estado_label("facturas", 2) == "Cobrada"
    assert estado_label("facturas", 3) == "Estado 3"


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
    assert out == {"items": [], "total": 0, "unlinked_total": 0}
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
    # ERP-F3: ESTFAC=0 → «Pendiente de cobro».
    assert body["items"][0]["estado_label"] == "Pendiente de cobro"


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


# ---------------------------------------------------------------------------
# ERP-E3-A-fix1: orden por columnas + cliente por CIF/email + forma de pago
# ---------------------------------------------------------------------------

F_CLI = [
    {"CODCLI": 2458, "NOFCLI": "DUPLICODER, S.L.", "NOCCLI": "Duplicoder",
     "NIFCLI": "B12345678", "EMACLI": "compras@duplicoder.es"},
    {"CODCLI": 99, "NOFCLI": "MOVIATICOS SL", "NOCCLI": "Moviaticos",
     "NIFCLI": "B99887766", "EMACLI": "admin@moviaticos.com"},
]


def test_documents_sort_by_total_number_date() -> None:
    """El orden se aplica al conjunto COMPLETO filtrado antes de paginar,
    numérico donde toca (no orden lexicográfico de strings)."""
    from app.integrations.factusol.documents import list_documents as ld

    rows = [
        _fac(9, "5", TOTFAC=1000.0, FECFAC="2026-01-01"),     # total alto, nº bajo
        _fac(100, "5", TOTFAC=20.0, FECFAC="2026-03-01"),
        _fac(50, "5", TOTFAC=200.0, FECFAC="2026-02-01"),
    ]
    client = FakeClient({"F_FAC": rows})
    by_total = ld(client, "facturas", ejercicio="2026",
                  sort="total", direction="asc")
    assert [d["total"] for d in by_total["items"]] == [20.0, 200.0, 1000.0]
    # 9 < 50 < 100 numérico (lexicográfico daría "100" < "50" < "9").
    by_num = ld(client, "facturas", ejercicio="2026",
                sort="numero", direction="asc")
    assert [d["codigo"] for d in by_num["items"]] == [9, 50, 100]
    by_date = ld(client, "facturas", ejercicio="2026",
                 sort="fecha", direction="desc")
    assert [d["fecha"] for d in by_date["items"]] == [
        "2026-03-01", "2026-02-01", "2026-01-01",
    ]
    # Paginar DESPUÉS de ordenar: la página 2 trae el siguiente del orden.
    page2 = ld(client, "facturas", ejercicio="2026",
               sort="total", direction="asc", limit=1, offset=1)
    assert [d["total"] for d in page2["items"]] == [200.0]
    assert page2["total"] == 3


def test_documents_sort_pushes_missing_values_last() -> None:
    from app.integrations.factusol.documents import list_documents as ld

    rows = [
        {"TIPALB": "5", "CODALB": 2},                       # sin fecha ni total
        {"TIPALB": "5", "CODALB": 1, "FECALB": "2026-05-01", "TOTALB": 7.0},
    ]
    client = FakeClient({"F_ALB": rows})
    out = ld(client, "albaranes", ejercicio="2026", sort="fecha",
             direction="asc")
    assert [d["codigo"] for d in out["items"]] == [1, 2]  # None al final


def test_customer_filter_matches_nif_and_email() -> None:
    from app.integrations.factusol.documents import list_documents as ld

    client = FakeClient({"F_FAC": FACTURAS, "F_CLI": F_CLI})
    by_nif = ld(client, "facturas", ejercicio="2026", cliente_q="b99887766")
    assert by_nif["total"] == 1
    assert by_nif["items"][0]["cliente_nombre"] == "MOVIATICOS"
    by_email = ld(client, "facturas", ejercicio="2026",
                  cliente_q="compras@duplicoder")
    # Las 3 facturas de DUPLICODER (CLIFAC=2458).
    assert by_email["total"] == 3
    by_name = ld(client, "facturas", ejercicio="2026", cliente_q="moviatic")
    assert by_name["total"] == 1


def test_customer_filter_no_match_returns_empty_not_all() -> None:
    from app.integrations.factusol.documents import list_documents as ld

    client = FakeClient({"F_FAC": FACTURAS, "F_CLI": F_CLI})
    out = ld(client, "facturas", ejercicio="2026", cliente_q="no-existe-xyz")
    assert out == {"items": [], "total": 0, "unlinked_total": 0}
    # Cortocircuito: ni siquiera se consulta la tabla de documentos.
    assert not any(t == "F_FAC" for t, _ in client.calls)


def test_forma_pago_in_header_and_resolved_in_detail(client, session_factory) -> None:
    """El listado expone el código FOP*; el detalle lo resuelve a nombre con
    el catálogo F_FPA (ERP-F5; F_FOP está vacía). Vacío → null (la UI pinta
    «—»)."""
    _ = session_factory
    facturas = [
        _fac(260066, "5", FOPFAC="002"),
        _fac(260065, "5"),  # sin forma de pago
    ]
    fpa = [{"CODFPA": "002", "DESFPA": "Transferencia 30 días"}]
    with _patched_factusol(FakeClient({
        "F_FAC": facturas, "F_LFA": [], "F_FOP": [], "F_FPA": fpa,
    })):
        detail = client.get(
            "/api/erp/factusol/documents/facturas/5/260066",
            headers=auth_headers(client, "user"),
        )
        empty = client.get(
            "/api/erp/factusol/documents/facturas/5/260065",
            headers=auth_headers(client, "user"),
        )
    assert detail.status_code == 200, detail.text
    assert detail.json()["forma_pago"] == "002"
    assert detail.json()["forma_pago_nombre"] == "Transferencia 30 días"
    assert empty.json()["forma_pago"] is None
    assert empty.json()["forma_pago_nombre"] is None


def test_documents_endpoint_accepts_sort_and_cliente_q(client, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS, "F_CLI": F_CLI})):
        r = client.get(
            "/api/erp/factusol/documents/facturas"
            "?cliente_q=admin@moviaticos.com&sort=total&dir=asc",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    assert [d["codigo"] for d in r.json()["items"]] == [260065]
    bad = client.get(
        "/api/erp/factusol/documents/facturas?sort=hackme",
        headers=auth_headers(client, "user"),
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# Fase 5 — cruce con el CRM (empresa · país · régimen · pedido de BoHub) +
# estado como pastilla + cobro por factura para el explorador de documentos.
# Todo es SOLO LECTURA sobre FACTUSOL (el fake solo sirve `load_table`).
# ---------------------------------------------------------------------------


F_PRE_27 = {
    "TIPPRE": "5", "CODPRE": 27, "CLIPRE": 2458, "CNOPRE": "DUPLICODER, S.L.",
    "FECPRE": "2026-08-01T00:00:00", "ESTPRE": 1, "TOTPRE": 100.0,
    "REFPRE": "REF-27", "NET1PRE": 100.0, "PIVA1PRE": 21.0,
}


def test_documents_list_annotates_company_regime_and_estado_tone(
    client, session_factory,
) -> None:
    """Cada factura lleva la empresa CRM vinculada por CODCLI, el país·régimen
    (misma regla que proformas/ficha) y el tono de la pastilla de estado. La
    factura de un cliente sin empresa CRM sale sin empresa ni régimen."""
    with session_factory() as s:
        s.add(Company(id="es", name="Duplicoder SL", country="ES",
                      tax_id="B12345678", factusol_company_id="2458"))
        s.commit()
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        r = client.get(
            "/api/erp/factusol/documents/facturas?serie=5",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    rows = {d["numero"]: d for d in r.json()["items"]}
    linked = rows["5-260066"]  # CLIFAC 2458 → Duplicoder SL
    assert linked["company"]["name"] == "Duplicoder SL"
    assert linked["country_iso2"] == "ES"
    assert linked["regime"] == "nacional"
    assert linked["regime_label"]
    assert linked["estado_tone"] == "warn"  # ESTFAC=0 → pendiente de cobro
    assert linked["order"] is None
    unlinked = rows["5-260065"]  # CLIFAC 99 → sin empresa CRM
    assert unlinked["company"] is None
    assert unlinked["regime"] is None


def test_documents_list_regime_intracomunitario(client, session_factory) -> None:
    """Cliente intracomunitario (UE con NIF-IVA) → régimen exento, país ISO2."""
    with session_factory() as s:
        s.add(Company(id="fr", name="La Maison de la Plaque", country="FR",
                      vat="FR16339753527", factusol_company_id="2458"))
        s.commit()
    with _patched_factusol(FakeClient({"F_FAC": [_fac(260066, "5")]})):
        r = client.get(
            "/api/erp/factusol/documents/facturas",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    d = r.json()["items"][0]
    assert d["regime"] == "intracomunitario"
    assert d["exento"] is True
    assert d["country_iso2"] == "FR"
    assert d["regime_source"] == "empresa"


def test_documents_list_links_bohub_order_for_presupuesto(
    client, session_factory,
) -> None:
    """Un presupuesto ya importado a BoHub trae su pedido (para «Abrir
    pedido»); ESTPRE=1 (aceptado) pinta la pastilla en verde."""
    with session_factory() as s:
        s.add(Company(id="es", name="Duplicoder SL", country="ES",
                      tax_id="B12345678", factusol_company_id="2458"))
        s.add(Order(id="o27", order_number="PRO-000027", company_id="es",
                    external_source=OrderSource.FACTUSOL_PROFORMA,
                    external_id="27", total_amount=100.0, currency="EUR",
                    payment_status="pending", preparation_status="pending_review"))
        s.commit()
    with _patched_factusol(FakeClient({"F_PRE": [F_PRE_27]})):
        r = client.get(
            "/api/erp/factusol/documents/presupuestos",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    d = r.json()["items"][0]
    assert d["numero"] == "5-000027"
    assert d["order"] == {"id": "o27", "order_number": "PRO-000027"}
    assert d["estado_tone"] == "ok"  # ESTPRE=1 → aceptado


# ---------------------------------------------------------------------------
# Lote 2 · PR-2 — pedido de BoHub en albaranes y facturas, filtro «solo sin
# vincular» con contador honesto y marca de tiempo de la lectura en vivo.
# ---------------------------------------------------------------------------


def _list(client, path: str) -> dict[str, Any]:
    r = client.get(path, headers=auth_headers(client, "user"))
    assert r.status_code == 200, r.text
    return r.json()


def test_facturas_link_order_by_serie_and_number(client, session_factory) -> None:
    """Un pedido con `factusol_invoice_serie` + `factusol_invoice_number`
    aparece en SU factura (serie + número); la homónima de otra serie no lo
    hereda. El contador de sin vincular cuenta sobre el resto de filtros."""
    with session_factory() as s:
        s.add(Order(id="o1", order_number="MAN-7001", total_amount=186.34, currency="EUR",
                    factusol_invoice_number="260066", factusol_invoice_serie=5,
                    invoice_status="invoiced_by_erp"))
        s.commit()
    rows = FACTURAS + [_fac(260066, "1", CNOFAC="HOMÓNIMA")]
    with _patched_factusol(FakeClient({"F_FAC": rows})):
        body = _list(client, "/api/erp/factusol/documents/facturas")
        solo_sin = _list(client, "/api/erp/factusol/documents/facturas?linked=false")
        solo_con = _list(client, "/api/erp/factusol/documents/facturas?linked=true")
        serie5 = _list(client, "/api/erp/factusol/documents/facturas?serie=5&linked=false")
    by_num = {d["numero"]: d for d in body["items"]}
    assert by_num["5-260066"]["order"] == {"id": "o1", "order_number": "MAN-7001"}
    assert by_num["1-260066"]["order"] is None  # homónima de otra serie
    assert body["total"] == 5 and body["unlinked_total"] == 4
    # `linked=false`: solo las 4 sin pedido; el total es el filtrado y el
    # contador no cambia al activar el chip.
    assert solo_sin["total"] == 4 and solo_sin["unlinked_total"] == 4
    assert all(d["order"] is None for d in solo_sin["items"])
    assert solo_con["total"] == 1 and solo_con["items"][0]["numero"] == "5-260066"
    assert solo_con["unlinked_total"] == 4
    # Combinado con otro filtro: el contador es sobre la serie 5.
    assert serie5["total"] == 1 and serie5["unlinked_total"] == 1


def test_facturas_link_order_by_bare_number_and_reffac(client, session_factory) -> None:
    """Pedidos que solo guardan el CODFAC desnudo (emisión antigua): gana el
    que tiene la REFFAC como referencia común; el homónimo de otra tienda no.
    Es la regla de `find_order_for_invoice` (nunca adivina)."""
    with session_factory() as s:
        # `BOPRIN-260066` → ref `BOP-260066` = REFFAC de la factura 5-260066.
        s.add(Order(id="bop", order_number="BOPRIN-260066", total_amount=1, currency="EUR",
                    factusol_invoice_number="260066"))
        s.add(Order(id="art", order_number="ARTISJ-260066", total_amount=1, currency="EUR",
                    factusol_invoice_number="260066"))
        s.commit()
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        body = _list(client, "/api/erp/factusol/documents/facturas?serie=5")
    by_num = {d["numero"]: d for d in body["items"]}
    assert by_num["5-260066"]["order"]["order_number"] == "BOPRIN-260066"
    assert by_num["5-260065"]["order"] is None


def test_facturas_bare_number_without_evidence_stays_unlinked(client, session_factory) -> None:
    """Dos pedidos con el mismo número desnudo y ninguna prueba (ni REFFAC ni
    cliente) → ambigüedad real: la factura queda SIN pedido."""
    with session_factory() as s:
        s.add(Order(id="a", order_number="XX-1", total_amount=1, currency="EUR",
                    factusol_invoice_number="260066"))
        s.add(Order(id="b", order_number="YY-2", total_amount=1, currency="EUR",
                    factusol_invoice_number="260066"))
        s.commit()
    with _patched_factusol(FakeClient({"F_FAC": [_fac(260066, "5")]})):
        body = _list(client, "/api/erp/factusol/documents/facturas")
    assert body["items"][0]["order"] is None
    assert body["unlinked_total"] == 1


ALBARANES = [
    {"TIPALB": "5", "CODALB": 91, "CLIALB": 2458, "CNOALB": "DUPLICODER, S.L.",
     "FECALB": "2026-08-02T00:00:00", "ESTALB": 0, "TOTALB": 10.0, "REFALB": "BOP-000091"},
    {"TIPALB": "5", "CODALB": 92, "CLIALB": 99, "CNOALB": "MOVIATICOS",
     "FECALB": "2026-08-03T00:00:00", "ESTALB": 1, "TOTALB": 20.0, "REFALB": "MOV-000092"},
]


def test_albaranes_link_order_by_albaran_number(client, session_factory) -> None:
    """Un pedido con `factusol_albaran_number` = `serie-código` aparece en su
    albarán; `linked=false` lo deja fuera."""
    with session_factory() as s:
        s.add(Order(id="o91", order_number="MAN-7091", total_amount=10, currency="EUR",
                    factusol_albaran_number="5-000091"))
        s.commit()
    with _patched_factusol(FakeClient({"F_ALB": ALBARANES})):
        body = _list(client, "/api/erp/factusol/documents/albaranes")
        sin = _list(client, "/api/erp/factusol/documents/albaranes?linked=false")
    by_num = {d["numero"]: d for d in body["items"]}
    assert by_num["5-000091"]["order"] == {"id": "o91", "order_number": "MAN-7091"}
    assert by_num["5-000092"]["order"] is None
    assert body["unlinked_total"] == 1
    assert [d["numero"] for d in sin["items"]] == ["5-000092"]


def test_presupuestos_linked_filter_uses_imported_order(client, session_factory) -> None:
    """En presupuestos «sin vincular» = sin pedido importado (la acción sigue
    siendo «Crear pedido»)."""
    with session_factory() as s:
        s.add(Order(id="o27", order_number="PRO-000027", total_amount=100.0, currency="EUR",
                    external_source=OrderSource.FACTUSOL_PROFORMA, external_id="27"))
        s.commit()
    otro = {**F_PRE_27, "CODPRE": 28, "REFPRE": "REF-28"}
    with _patched_factusol(FakeClient({"F_PRE": [F_PRE_27, otro]})):
        body = _list(client, "/api/erp/factusol/documents/presupuestos?linked=false")
    assert [d["numero"] for d in body["items"]] == ["5-000028"]
    assert body["total"] == 1 and body["unlinked_total"] == 1


def test_documents_list_reports_fetched_at_and_cycle_index_age(client, session_factory) -> None:
    """La respuesta dice CUÁNDO se leyó FACTUSOL (`fetched_at`, ISO con zona)
    y la antigüedad del índice del ciclo cacheado (0 s recién leído; nunca
    negativa) — para «Solo lectura · sincronizado hace X»."""
    _ = session_factory
    from datetime import datetime

    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        body = _list(client, "/api/erp/factusol/documents/facturas?fresh_ciclo=1")
    fetched = datetime.fromisoformat(body["fetched_at"])
    assert fetched.tzinfo is not None
    assert isinstance(body["cycle_index_age_seconds"], int)
    assert 0 <= body["cycle_index_age_seconds"] <= 30


def test_factura_cobro_info_pendiente(client, session_factory) -> None:
    """Cobro por factura (serie+número, sin pedido): saldo, estado pendiente y
    sin avisos cuando no hay cobros previos. Solo lectura."""
    _ = session_factory
    fake = FakeClient({"F_FAC": FACTURAS})
    with _patched_factusol(fake):
        r = client.get(
            "/api/erp/factusol/documents/facturas/5/260066/cobro",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pendiente"
    assert body["numero"] == "5-260066"
    assert body["total"] == 186.34
    assert body["saldo_pendiente"] == 186.34
    assert body["warnings"] == []
    # Nada de escritura: el fake solo expone `load_table` (lecturas).
    assert all(isinstance(c, tuple) for c in fake.calls)


def test_factura_cobro_info_cobrada(client, session_factory) -> None:
    """ESTFAC=2 → la factura consta cobrada (no se ofrece registrar cobro)."""
    _ = session_factory
    fac = _fac(260099, "5", ESTFAC=2, TOTFAC=100.0)
    with _patched_factusol(FakeClient({"F_FAC": [fac]})):
        r = client.get(
            "/api/erp/factusol/documents/facturas/5/260099/cobro",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cobrada"


def test_factura_cobro_info_not_found(client, session_factory) -> None:
    """Factura inexistente → `not_found`, sin escribir nada."""
    _ = session_factory
    with _patched_factusol(FakeClient({"F_FAC": FACTURAS})):
        r = client.get(
            "/api/erp/factusol/documents/facturas/7/1/cobro",
            headers=auth_headers(client, "user"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "not_found"


def test_factura_cobro_info_requires_auth(client) -> None:
    assert client.get(
        "/api/erp/factusol/documents/facturas/5/260066/cobro"
    ).status_code in (401, 403)
