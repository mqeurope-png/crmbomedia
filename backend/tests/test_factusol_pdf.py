"""ERP-E4 — PDF de los 4 documentos FACTUSOL, multiempresa y multiidioma.

Los PDF generados se parsean con pypdf y se verifica el inventario de campos
de la Parte C del spec (extraído de los modelos reales de impresión): la
garantía de NO perder información respecto a lo que emite el escritorio.
"""
from __future__ import annotations

import io
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.factusol_pdf import (
    COMPANY_DEFAULTS,
    extract_document_data,
    generate_document_pdf,
    merge_companies,
    pdf_filename,
)
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

# ---------------------------------------------------------------------------
# fixtures de datos (espejo de la cadena real 5-000027 → 5-500004 → 5-260063)
# ---------------------------------------------------------------------------


def _texto(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def _pages(pdf: bytes) -> list[str]:
    return [p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages]


def _header(doc_type: str, **over: Any) -> dict[str, Any]:
    sfx = {"facturas": "FAC", "presupuestos": "PRE",
           "albaranes": "ALB", "pedidos": "PCL"}[doc_type]
    row = {
        f"TIP{sfx}": "5", f"COD{sfx}": 260063,
        f"FEC{sfx}": "2026-08-26T00:00:00",
        f"CLI{sfx}": 2458, f"CNI{sfx}": "B12345678",
        f"CNO{sfx}": "DUPLICODER, S.L.",
        f"CDO{sfx}": "C/ Muñoz Seca, 3", f"CCP{sfx}": "08036",
        f"CPO{sfx}": "Barcelona", f"CPR{sfx}": "Barcelona",
        f"CPA{sfx}": "España", f"TEL{sfx}": "932 111 222",
        f"FOP{sfx}": "002", f"REF{sfx}": "BOP-099917",
        f"OB1{sfx}": "Primera línea de observaciones",
        f"OB2{sfx}": "Segunda línea de observaciones",
        f"VEN{sfx}": "2026-09-26T00:00:00",
        f"TOT{sfx}": 225.47,
        f"NET1{sfx}": 186.34, f"BAS1{sfx}": 186.34,
        f"PIVA1{sfx}": 21, f"IIVA1{sfx}": 39.13,
        f"PED{sfx}": "PED-777", f"FPE{sfx}": "2026-08-20T00:00:00",
    }
    row.update(over)
    return row


def _linea(doc_type: str, pos: int, **over: Any) -> dict[str, Any]:
    lsfx = {"facturas": "LFA", "presupuestos": "LPS",
            "albaranes": "LAL", "pedidos": "LPC"}[doc_type]
    row = {
        f"TIP{lsfx}": "5", f"COD{lsfx}": 260063, f"POS{lsfx}": pos,
        f"ART{lsfx}": f"ART-{pos:03d}",
        f"DES{lsfx}": f"Artículo de prueba {pos}",
        f"CAN{lsfx}": 2, f"PRE{lsfx}": 40.0, f"DT1{lsfx}": 10,
        f"TOT{lsfx}": 87.12,
    }
    row.update(over)
    return row


def _alb_resolver() -> FakeClient:
    """FakeClient que resuelve albaranes de origen para facturas."""
    return FakeClient({"F_ALB": [
        {"TIPALB": "5", "CODALB": 500004,
         "FECALB": "2026-08-21T00:00:00", "REFALB": "BOP-099917"},
        {"TIPALB": "5", "CODALB": 500005,
         "FECALB": "2026-08-25T00:00:00", "REFALB": "BOP-099918"},
    ]})


def _pdf(doc_type: str, header: dict, lines: list[dict], *,
         serie: int = 5, lang: str = "es", **kwargs: Any) -> tuple[bytes, dict]:
    data = extract_document_data(
        _alb_resolver(), doc_type, header, lines, ejercicio="2026",
        fop_names={"002": "Transferencia 30 días"},
    )
    company = dict(COMPANY_DEFAULTS[serie])
    return generate_document_pdf(data, company=company, lang=lang, **kwargs), data


# ---------------------------------------------------------------------------
# Parte C — inventario de campos por tipo (no perder información)
# ---------------------------------------------------------------------------


def test_pdf_contains_all_required_fields_per_doc_type() -> None:
    esperado_comun = [
        "B12345678", "DUPLICODER, S.L.", "C/ Muñoz Seca, 3", "08036",
        "Barcelona", "España", "932 111 222",     # cliente completo
        "5-260063",                               # numeración SERIE-NÚMERO
        "26-08-2026",                             # fecha
        "Transferencia 30 días",                  # forma de pago
        "Primera línea de observaciones",
        "Segunda línea de observaciones",
        "1 de 1",                                 # paginación
    ]
    # FACTURA: bandas + total + vencimiento + albarán por línea.
    fac_lines = [_linea("facturas", 1, DOCLFA="A", DTPLFA="5", DCOLFA=500004)]
    pdf, _ = _pdf("facturas", _header("facturas"), fac_lines)
    text = _texto(pdf)
    for needle in esperado_comun + [
        "FACTURA", "186,34", "21", "39,13", "225,47",
        "26-09-2026",                              # 1er vencimiento
        "ART-001", "Artículo de prueba 1", "40,00", "10", "87,12",
        "Albarán 5-500004", "21-08-2026", "BOP-099917",  # agrupación
        "PED-777", "20-08-2026",                   # nº y fecha de su pedido
    ]:
        assert needle in text, f"factura sin {needle!r}"

    # PRESUPUESTO: mismas bandas + texto de validez de 30 días.
    pdf, _ = _pdf("presupuestos", _header("presupuestos"),
                  [_linea("presupuestos", 1)])
    text = _texto(pdf)
    for needle in esperado_comun + [
        "PRESUPUESTO", "válido durante 30 días", "186,34", "225,47",
    ]:
        assert needle in text, f"presupuesto sin {needle!r}"

    # ALBARÁN: SIN importes (modelo estándar), con su referencia.
    pdf, _ = _pdf("albaranes", _header("albaranes"),
                  [_linea("albaranes", 1)])
    text = _texto(pdf)
    for needle in ["ALBARÁN", "BOP-099917", "ART-001", "2",
                   "DUPLICODER, S.L."]:
        assert needle in text, f"albarán sin {needle!r}"
    assert "40,00" not in text and "87,12" not in text  # sin importes

    # ALBARÁN VALORADO (variante prevista): con importes.
    pdf, _ = _pdf("albaranes", _header("albaranes"),
                  [_linea("albaranes", 1)], valued=True)
    assert "87,12" in _texto(pdf)

    # PEDIDO: equivalente valorado con los campos de F_PCL.
    pdf, _ = _pdf("pedidos", _header("pedidos"), [_linea("pedidos", 1)])
    text = _texto(pdf)
    for needle in esperado_comun + ["PEDIDO", "87,12", "225,47"]:
        assert needle in text, f"pedido sin {needle!r}"


def test_pdf_uses_company_identity_from_series() -> None:
    header, lines = _header("facturas"), [_linea("facturas", 1)]
    pdf5, _ = _pdf("facturas", header, lines, serie=5)
    text5 = _texto(pdf5)
    assert "Streamtec SL" in text5
    assert "CIF B64154263" in text5
    assert "IBAN: ES11 0081 0202 1700 0125 9030" in text5

    pdf2, _ = _pdf("facturas", header, lines, serie=2)
    text2 = _texto(pdf2)
    assert "MQ Europe BV" in text2
    assert "VAT nr. BE 0883.002.183" in text2
    assert "BE28068245312320" in text2 and "GKCCBEBB" in text2
    assert "Streamtec" not in text2


def test_pdf_multipage_repeats_header_and_paginates() -> None:
    lines = [_linea("facturas", i) for i in range(1, 81)]
    pdf, _ = _pdf("facturas", _header("facturas"), lines)
    pages = _pages(pdf)
    assert len(pages) >= 2
    total = len(pages)
    for n, page in enumerate(pages, start=1):
        # Cabecera repetida (título + número + cliente) y «página X de Y».
        assert "FACTURA" in page
        assert "5-260063" in page
        assert "DUPLICODER" in page
        assert f"{n} de {total}" in page
        # El pie fijo (banco/legal) también va en todas las páginas.
        assert "IBAN: ES11 0081 0202 1700 0125 9030" in page


def test_pdf_long_multiline_description_flows() -> None:
    parrafo = (
        "Garantía extendida de doce meses sobre todos los componentes.\n"
        "Cobertura de desplazamiento incluida en península.\n"
    ) * 12
    lines = [_linea("facturas", 1, DESLFA=parrafo)]
    pdf, _ = _pdf("facturas", _header("facturas"), lines)
    text = _texto(pdf)
    assert "Garantía extendida de doce meses" in text
    assert "Cobertura de desplazamiento" in text


def test_pdf_all_four_vat_bands_including_exempt() -> None:
    header = _header(
        "facturas",
        NET1FAC=100, BAS1FAC=100, PIVA1FAC=21, IIVA1FAC=21,
        NET2FAC=200, BAS2FAC=200, PIVA2FAC=10, IIVA2FAC=20, IREC2FAC=2.8,
        NET3FAC=300, BAS3FAC=300, PIVA3FAC=4, IIVA3FAC=12,
        NET4FAC=400, BAS4FAC=400,                     # banda EXENTA
        TOTFAC=1055.8,
    )
    pdf, data = _pdf("facturas", header, [_linea("facturas", 1)])
    assert len(data["bands"]) == 4
    text = _texto(pdf)
    for needle in ["100,00", "200,00", "300,00", "400,00",
                   "Exento", "2,80", "1.055,80"]:
        assert needle in text, f"bandas sin {needle!r}"


def test_pdf_invoice_shows_albaran_grouping_per_line() -> None:
    lines = [
        _linea("facturas", 1, DOCLFA="A", DTPLFA="5", DCOLFA=500004),
        _linea("facturas", 2, DOCLFA="A", DTPLFA="5", DCOLFA=500004),
        _linea("facturas", 3, DOCLFA="A", DTPLFA="5", DCOLFA=500005),
    ]
    pdf, _ = _pdf("facturas", _header("facturas"), lines)
    text = _texto(pdf)
    assert "Albarán 5-500004" in text and "21-08-2026" in text
    assert "Albarán 5-500005" in text and "25-08-2026" in text
    assert "BOP-099918" in text
    # Una cabecera de grupo por albarán, no por línea.
    assert text.count("Albarán 5-500004") == 1


def test_pdf_renders_without_logo() -> None:
    from pathlib import Path

    pdf, _ = _pdf("facturas", _header("facturas"), [_linea("facturas", 1)],
                  logo=None)
    assert pdf.startswith(b"%PDF")
    # Ruta de logo inexistente: hueco, no error.
    pdf, _ = _pdf("facturas", _header("facturas"), [_linea("facturas", 1)],
                  logo=Path("/no/existe/logo.png"))
    assert pdf.startswith(b"%PDF")


def test_pdf_accents_and_special_chars() -> None:
    lines = [_linea(
        "facturas", 1,
        DESLFA="Cañón de años — pingüino, öçü, €uro, ¡señal!",
    )]
    pdf, _ = _pdf("facturas", _header("facturas"), lines)
    text = _texto(pdf)
    assert "Cañón de años" in text
    assert "pingüino" in text
    assert "€uro" in text
    assert "Muñoz" in text


def test_language_switch_changes_labels_not_data() -> None:
    header, lines = _header("facturas"), [_linea("facturas", 1)]
    pdf_es, _ = _pdf("facturas", header, lines, lang="es")
    pdf_en, _ = _pdf("facturas", header, lines, lang="en")
    es, en = _texto(pdf_es), _texto(pdf_en)
    assert "FACTURA" in es and "FORMA DE PAGO" in es
    assert "INVOICE" in en and "PAYMENT CONDITIONS" in en
    assert "FACTURA\n" not in en.replace("FACTURA PROFORMA", "")
    # Los DATOS no cambian: mismo cliente, mismo número, mismo total (con
    # formato de números por idioma).
    for text in (es, en):
        assert "DUPLICODER, S.L." in text
        assert "5-260063" in text
        assert "Transferencia 30 días" in text
    assert "225,47" in es
    assert "225.47" in en


def test_pdf_filename_is_readable_ascii() -> None:
    data = extract_document_data(
        _alb_resolver(), "facturas", _header("facturas"),
        [], ejercicio="2026",
    )
    assert pdf_filename("facturas", data, "es") == (
        "Factura_5-260063_DUPLICODER_S_L.pdf"
    )


def test_merge_companies_defaults_and_overrides() -> None:
    merged = merge_companies(None)
    assert merged["5"]["nombre"] == "Streamtec SL"
    assert merged["2"]["intracom"]["en"].startswith("INTRACOMMUNITY")
    edited = merge_companies({
        "5": {"iban": "ES99 9999", "legal": {"en": "Ownership reserved."}},
    })
    assert edited["5"]["iban"] == "ES99 9999"
    assert edited["5"]["nombre"] == "Streamtec SL"      # el resto, default
    assert edited["5"]["legal"]["en"] == "Ownership reserved."


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


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_FAC": [_header("facturas")],
        "F_LFA": [_linea("facturas", 1)],
        "F_ALB": [], "F_FOP": [],
    }


def test_pdf_endpoint_returns_pdf_with_filename(http, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient(_tables())):
        r = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/pdf?lang=en",
            headers=auth_headers(http, "user"),
        )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "Invoice_5-260063" in r.headers["content-disposition"]
    assert "INVOICE" in _texto(r.content)


def test_pdf_endpoint_404_and_lang_validation(http, session_factory) -> None:
    _ = session_factory
    with _patched_factusol(FakeClient({"F_FAC": [], "F_LFA": []})):
        missing = http.get(
            "/api/erp/factusol/documents/facturas/5/1/pdf",
            headers=auth_headers(http, "user"),
        )
    assert missing.status_code == 404
    bad_lang = http.get(
        "/api/erp/factusol/documents/facturas/5/260063/pdf?lang=klingon",
        headers=auth_headers(http, "user"),
    )
    assert bad_lang.status_code == 422


def test_settings_expose_and_save_companies(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "admin")
    r = http.get("/api/erp/settings", headers=headers)
    assert r.status_code == 200, r.text
    companies = r.json()["factusol_companies"]
    assert companies["5"]["nombre"] == "Streamtec SL"
    assert companies["1"]["nif"] == "NIF B63609309"
    assert companies["5"]["logo"] is False

    companies["5"]["iban"] = "ES00 TEST"
    r2 = http.patch(
        "/api/erp/settings", json={"factusol_companies": companies},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["factusol_companies"]["5"]["iban"] == "ES00 TEST"
    # …y el PDF lo usa.
    with _patched_factusol(FakeClient(_tables())):
        pdf = http.get(
            "/api/erp/factusol/documents/facturas/5/260063/pdf",
            headers=headers,
        )
    assert "ES00 TEST" in _texto(pdf.content)


def test_logo_upload_endpoint(http, session_factory, tmp_path, monkeypatch) -> None:
    _ = session_factory
    import app.erp.factusol_pdf as fpdf

    monkeypatch.setattr(fpdf, "logos_dir", lambda: tmp_path)
    # PNG mínimo válido (cabecera): al PDF le basta la ruta; aquí probamos
    # el guardado y el flag de settings.
    png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    r = http.post(
        "/api/erp/factusol/companies/5/logo",
        files={"file": ("logo.png", png, "image/png")},
        headers=auth_headers(http, "admin"),
    )
    assert r.status_code == 201, r.text
    assert (tmp_path / "serie_5.png").exists()
    bad = http.post(
        "/api/erp/factusol/companies/5/logo",
        files={"file": ("logo.gif", b"GIF89a", "image/gif")},
        headers=auth_headers(http, "admin"),
    )
    assert bad.status_code == 422
    # Solo admin.
    forbidden = http.post(
        "/api/erp/factusol/companies/5/logo",
        files={"file": ("logo.png", png, "image/png")},
        headers=auth_headers(http, "user"),
    )
    assert forbidden.status_code == 403
