"""BoHub ERP Fase C · C-4 — proformas (F_PRE + F_LPS) y artículos (F_ART + F_LTA).

C-4-fix3 corrigió el error de base de C-4: dábamos por hecho que F_PRE era
mono-línea porque habíamos buscado su tabla de líneas como F_LPRE/F_LPR/F_LPP.
Se llama **F_LPS** (`CODLPS` → `CODPRE`) y sí existe, así que buena parte de lo
que se prueba aquí es que las líneas van y vienen de ahí, no de la caché local
(que quedó obsoleta). El precio de venta hace lo propio con **F_LTA**.

Sin red: el cliente FACTUSOL es un doble que sirve las filas configuradas y
registra lo que se le pide escribir.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401  — registra todos los modelos en Base.metadata
from app.db.base import Base
from app.erp.models import Order, OrderLine
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.quotes import (
    ARTICLE_SEARCH_LIMIT,
    build_quote_payload,
    convert_quote_to_order,
    create_quote,
    duplicate_quote,
    get_quote,
    list_quote_lines,
    list_quotes,
    next_codpre,
    quote_lines_for_order,
    resolve_codarts,
    search_articles,
)
from app.models.crm import Company


class _FakeFactusol:
    """Doble del cliente: sirve F_PRE/F_LPS/F_ART/F_LTA y guarda las escrituras.

    `write_fails_for` permite simular que una tabla rechaza la escritura, para
    probar la política de «proforma incompleta antes que duplicada»."""

    def __init__(self, *, quotes=None, articles=None, lines=None, tariffs=None,
                 write_fails_for=None):
        self.default_ejercicio = "2026"
        self._quotes = list(quotes or [])
        self._articles = list(articles or [])
        self._lines = list(lines or [])
        self._tariffs = list(tariffs or [])
        self._write_fails_for = write_fails_for
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.filters: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.filters.append((tabla, filtro))
        if tabla == "F_ART":
            return list(self._articles)
        if tabla == "F_LTA":
            rows = list(self._tariffs)
            if "TARLTA=" in filtro:
                wanted = filtro.split("TARLTA=", 1)[1].split(" ")[0]
                rows = [r for r in rows if str(r.get("TARLTA")) == wanted]
            if "ARTLTA IN (" in filtro:
                inside = filtro.split("ARTLTA IN (", 1)[1].split(")")[0]
                codarts = {c.strip().strip("'") for c in inside.split(",")}
                rows = [r for r in rows if str(r.get("ARTLTA")) in codarts]
            return rows
        if tabla == "F_LPS":
            rows = list(self._lines)
            if filtro.startswith("CODLPS="):
                wanted = filtro.split("=", 1)[1].split(" ")[0]
                rows = [r for r in rows if str(r.get("CODLPS")) == wanted]
            if "ORDER BY POSLPS" in filtro:
                rows = sorted(rows, key=lambda r: int(r.get("POSLPS", 0)))
            return rows
        if tabla != "F_PRE":
            return []
        rows = list(self._quotes)
        if filtro.startswith("CODPRE="):
            wanted = filtro.split("=", 1)[1].split(" ")[0]
            rows = [r for r in rows if str(r.get("CODPRE")) == wanted]
        elif filtro.startswith("CLIPRE="):
            wanted = filtro.split("=", 1)[1].split(" ")[0].strip("'")
            rows = [r for r in rows if str(r.get("CLIPRE")) == wanted]
        if "ORDER BY CODPRE DESC" in filtro:
            rows = sorted(rows, key=lambda r: int(r.get("CODPRE", 0)), reverse=True)
        return rows

    def write_record(self, tabla, data, *, ejercicio=None):
        if tabla == self._write_fails_for:
            raise FactusolError(f"BDEscribirRegistroError en {tabla}")
        self.writes.append((tabla, dict(data)))
        return {"respuesta": "OK"}

    def writes_to(self, tabla: str) -> list[dict[str, Any]]:
        return [data for t, data in self.writes if t == tabla]


def _quote_row(codpre: int, *, clipre="55555", fecha="2026-08-01",
               ref="Proforma de prueba", total=121.0) -> dict[str, Any]:
    return {
        "CODPRE": codpre, "TIPPRE": "1", "REFPRE": ref,
        "FECPRE": f"{fecha}T00:00:00", "CLIPRE": clipre,
        "CNOPRE": "Acme SL", "CDOPRE": "C/ Mayor 1", "CPOPRE": "Madrid",
        "CCPPRE": "28001", "CPRPRE": "Madrid", "CNIPRE": "B12345678",
        "NET1PRE": 100.0, "PIVA1PRE": 21.0, "IIVA1PRE": 21.0,
        "TOTPRE": total, "ALMPRE": "GEN",
    }


def _line_row(codpre: int, pos: int, *, art="", desc="Línea", cant=1.0,
              precio=10.0, dto=0.0, iva=21.0) -> dict[str, Any]:
    """Fila de F_LPS. Columnas verificadas contra la base real."""
    return {
        "TIPLPS": "1", "CODLPS": codpre, "POSLPS": pos, "ARTLPS": art,
        "DESLPS": desc, "CANLPS": cant, "DT1LPS": dto, "DT2LPS": 0.0,
        "DT3LPS": 0.0, "PRELPS": precio,
        "TOTLPS": round(cant * precio * (1 - dto / 100), 2), "IVALPS": iva,
    }


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session(session_factory) -> Generator[Session, None, None]:
    with session_factory() as s:
        yield s


# --- numeración -------------------------------------------------------------


def test_next_codpre_es_max_mas_uno():
    fake = _FakeFactusol(quotes=[_quote_row(30), _quote_row(31), _quote_row(12)])
    assert next_codpre(fake, "2026") == "32"


def test_next_codpre_arranca_en_uno_sin_presupuestos():
    assert next_codpre(_FakeFactusol(), "2026") == "1"


# --- lectura ----------------------------------------------------------------


def test_list_quotes_filtra_por_cliente_y_ventana_temporal():
    fake = _FakeFactusol(quotes=[
        _quote_row(10, clipre="55555", fecha="2026-08-01"),
        _quote_row(11, clipre="55555", fecha="2025-01-01"),  # fuera de ventana
        _quote_row(12, clipre="99999", fecha="2026-08-01"),  # otro cliente
    ])
    items = list_quotes(fake, ejercicio="2026", codcli="55555", days_back=180,
                        today=date(2026, 8, 5))
    assert [q["codpre"] for q in items] == ["10"]
    assert items[0]["fecha"] == "2026-08-01"
    assert items[0]["total"] == 121.0


def test_list_quotes_ordena_descendente_por_codpre():
    fake = _FakeFactusol(quotes=[_quote_row(5), _quote_row(9), _quote_row(7)])
    items = list_quotes(fake, ejercicio="2026", days_back=0)
    assert [q["codpre"] for q in items] == ["9", "7", "5"]


def test_list_quotes_global_no_customer_filter():
    """C-4-fix1: sin codcli devuelve proformas de TODOS los clientes — el 80 %
    de las plantillas que se reutilizan son de otro cliente parecido."""
    fake = _FakeFactusol(quotes=[
        _quote_row(10, clipre="55555"),
        _quote_row(11, clipre="66666"),
        _quote_row(12, clipre="77777"),
    ])
    items = list_quotes(fake, ejercicio="2026", codcli=None, days_back=0)
    assert {q["clipre"] for q in items} == {"55555", "66666", "77777"}
    # Y el filtro que va a la API no menciona CLIPRE.
    _, filtro = fake.filters[0]
    assert "CLIPRE" not in filtro


def test_list_quotes_global_filters_by_refpre_and_cnopre():
    fake = _FakeFactusol(quotes=[
        _quote_row(10, ref="Rotulación ador para nave"),          # casa REFPRE
        {**_quote_row(11, ref="Otra cosa"),
         "CNOPRE": "LABORATORIOS ADOR"},                          # casa CNOPRE
        _quote_row(12, ref="Nada que ver"),                       # no casa
    ])
    items = list_quotes(fake, ejercicio="2026", days_back=0, text="ador")
    assert [q["codpre"] for q in items] == ["11", "10"]


def test_list_quotes_text_filter_matches_codpre():
    fake = _FakeFactusol(quotes=[_quote_row(512), _quote_row(77)])
    items = list_quotes(fake, ejercicio="2026", days_back=0, text="512")
    assert [q["codpre"] for q in items] == ["512"]


def test_list_quotes_text_filter_applies_before_limit():
    """El recorte va DESPUÉS del filtro: si truncara primero, una plantilla
    antigua no aparecería nunca (las recientes se la comerían)."""
    rows = [_quote_row(i, ref="relleno") for i in range(200, 100, -1)]
    rows.append(_quote_row(5, ref="plantilla rotulación"))
    items = list_quotes(_FakeFactusol(quotes=rows), ejercicio="2026",
                        days_back=0, text="rotulación", limit=50)
    assert [q["codpre"] for q in items] == ["5"]


def test_get_quote_lee_las_lineas_reales_de_f_lps(session):
    """C-4-fix3: las líneas salen de F_LPS, no de la caché local. Funciona con
    cualquier proforma, también las hechas en el FACTUSOL de escritorio."""
    fake = _FakeFactusol(
        quotes=[_quote_row(42)],
        lines=[_line_row(42, 1, art="ART-1", desc="Cable HDMI", cant=2, precio=10)],
    )
    quote = get_quote(fake, session, "42", ejercicio="2026")
    assert quote["line_source"] == "F_LPS"
    assert len(quote["lines"]) == 1
    assert quote["lines"][0]["description"] == "Cable HDMI"
    assert quote["lines"][0]["codart"] == "ART-1"


def test_get_quote_sin_lineas_devuelve_lista_vacia(session):
    fake = _FakeFactusol(quotes=[_quote_row(42, ref="Reparación pantalla")])
    quote = get_quote(fake, session, "42", ejercicio="2026")
    assert quote["lines"] == []
    assert quote["referencia"] == "Reparación pantalla"


def test_get_quote_devuelve_none_si_no_existe(session):
    assert get_quote(_FakeFactusol(), session, "999", ejercicio="2026") is None


# --- líneas de presupuesto (F_LPS) ------------------------------------------


def test_list_quote_lines_reads_f_lps():
    """El presupuesto 574 real (Roca Joiers) tiene 4 líneas que suman 355, la
    base NET1PRE de su cabecera. Es la comprobación que cerró el hallazgo."""
    fake = _FakeFactusol(lines=[
        _line_row(574, 2, art="CAP", desc="Capping", precio=25),
        _line_row(574, 1, art="MBO", desc="Cabezal MBO", precio=250),
        _line_row(574, 4, art="", desc="Hora SAT", precio=60),
        _line_row(574, 3, art="WIP", desc="Wiper", precio=20),
        _line_row(999, 1, desc="De otra proforma"),
    ])
    lines = list_quote_lines(fake, "574", ejercicio="2026")
    # Ordenadas por POSLPS y filtradas por CODLPS.
    assert [line["position"] for line in lines] == [1, 2, 3, 4]
    assert [line["description"] for line in lines] == [
        "Cabezal MBO", "Capping", "Wiper", "Hora SAT",
    ]
    assert sum(line["line_total"] for line in lines) == 355.0
    # Línea de texto libre: sin artículo.
    assert lines[3]["codart"] is None


def test_list_quote_lines_empty_returns_empty_list():
    assert list_quote_lines(_FakeFactusol(), "404", ejercicio="2026") == []


def test_list_quote_lines_iva_vacio_cae_a_21():
    fake = _FakeFactusol(lines=[_line_row(10, 1, iva=0)])
    assert list_quote_lines(fake, "10", ejercicio="2026")[0]["iva_pct"] == 21.0


# --- artículos --------------------------------------------------------------


def test_search_articles_normaliza_columnas_reales():
    fake = _FakeFactusol(articles=[{
        "CODART": "ART-1", "EANART": "8412345678905", "DESART": "Cable HDMI 3m",
        "FAMART": "CAB", "TIVART": 21, "PCOART": 8.5, "STOART": 12,
    }])
    items = search_articles(fake, "hdmi", ejercicio="2026")
    assert items[0]["codart"] == "ART-1"
    assert items[0]["descripcion"] == "Cable HDMI 3m"
    assert items[0]["precio"] == 8.5
    assert items[0]["iva_pct"] == 21


def test_search_articles_filter_includes_all_6_columns():
    """C-4-fix1: buscar solo en CODART/EANART/DESART dejaba fuera el SKU
    comercial (EQUART) y las descripciones media/corta."""
    fake = _FakeFactusol(articles=[])
    search_articles(fake, "hdmi", ejercicio="2026")
    _, filtro = fake.filters[0]
    for col in ("CODART", "EANART", "EQUART", "DESART", "DEEART", "DETART"):
        assert f"UPPER({col})" in filtro, col


def test_search_articles_matches_equart():
    """El artículo real: CODART '00001' pero SKU comercial 'CDR80WPT'. Teclear
    «CDR80» no aparece en ninguna descripción — solo casa por EQUART."""
    fake = _FakeFactusol(articles=[{
        "CODART": "00001", "EQUART": "CDR80WPT",
        "DESART": "CD TQ 700 MB white Thermal WPT",
        "DEEART": "CD TQ 700 MB white Thermal WPT",
        "DETART": "CD TQ 700 MB white T", "PCOART": 0.25, "TIVART": 21,
    }])
    items = search_articles(fake, "CDR80", ejercicio="2026")
    _, filtro = fake.filters[0]
    assert "UPPER(EQUART) LIKE UPPER('%CDR80%')" in filtro
    assert items[0]["equart"] == "CDR80WPT"
    # `sku` es el alias que ve la UI: comercial por delante del interno.
    assert items[0]["sku"] == "CDR80WPT"


def test_search_articles_reads_price_from_f_lta():
    """C-4-fix3: el precio de VENTA vive en F_LTA (tarifa 1), no en F_ART —
    ahí `PCOART` es el coste. Verificado en la base real: 99cy → 80,00."""
    fake = _FakeFactusol(
        articles=[{"CODART": "99cy", "DESART": "CYAN 0,5L", "PCOART": 40.0,
                   "TIVART": 21}],
        tariffs=[{"TARLTA": 1, "ARTLTA": "99cy", "MARLTA": 0, "PRELTA": 80.0},
                 {"TARLTA": 2, "ARTLTA": "99cy", "MARLTA": 0, "PRELTA": 0.0}],
    )
    item = search_articles(fake, "cyan", ejercicio="2026")[0]
    assert item["precio_venta"] == 80.0
    assert item["precio_venta_source"] == "F_LTA_TAR1"
    assert item["precio_coste"] == 40.0
    assert item["precio"] == 80.0
    # Se pide en LOTE: una sola consulta a F_LTA para todo el autocomplete.
    lta_filters = [f for t, f in fake.filters if t == "F_LTA"]
    assert len(lta_filters) == 1
    assert "TARLTA=1" in lta_filters[0]


def test_search_articles_null_price_when_no_lta():
    """Artículo sin fila en tarifa 1 → precio vacío, NO 0.00: el operador lo
    teclea. Forzar cero dejaría emitir proformas a cero sin que nadie lo note."""
    fake = _FakeFactusol(
        articles=[{"CODART": "00003", "DESART": "Servicio", "PCOART": 0.0,
                   "TIVART": 21}],
        tariffs=[],
    )
    item = search_articles(fake, "servicio", ejercicio="2026")[0]
    assert item["precio_venta"] is None
    assert item["precio_venta_source"] is None


def test_search_articles_null_price_when_tarifa1_is_zero():
    """PRELTA=0 es «tarifa sin configurar» (pasa con los que solo tienen
    tarifa 2), no un artículo gratis."""
    fake = _FakeFactusol(
        articles=[{"CODART": "1503", "DESART": "Wiper", "TIVART": 21}],
        tariffs=[{"TARLTA": 1, "ARTLTA": "1503", "PRELTA": 0.0}],
    )
    assert search_articles(fake, "wiper", ejercicio="2026")[0]["precio_venta"] is None


def test_search_articles_survives_f_lta_failure():
    """Si F_LTA falla, el autocomplete sigue siendo usable sin precio: es
    preferible a quedarse sin buscador."""
    class _NoTariffs(_FakeFactusol):
        def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
            if tabla == "F_LTA":
                raise FactusolError("F_LTA caída")
            return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)

    fake = _NoTariffs(articles=[{"CODART": "99cy", "DESART": "CYAN", "TIVART": 21}])
    item = search_articles(fake, "cyan", ejercicio="2026")[0]
    assert item["codart"] == "99cy"
    assert item["precio_venta"] is None


def test_search_articles_limit_200():
    """C-4-fix2: el tope sube de 50 a 200 — «tinta» devuelve más de 100
    artículos y el desplegable tiene scroll interno."""
    rows = [{"CODART": f"{i:05d}", "DESART": "tinta"} for i in range(300)]
    items = search_articles(_FakeFactusol(articles=rows), "tinta",
                            ejercicio="2026")
    assert len(items) == ARTICLE_SEARCH_LIMIT == 200


def test_search_articles_matches_deeart():
    fake = _FakeFactusol(articles=[{
        "CODART": "00002", "EQUART": "", "DESART": "",
        "DEEART": "Tinta negra pigmentada", "DETART": "Tinta negra",
        "PCOART": 12.0, "TIVART": 21,
    }])
    items = search_articles(fake, "tinta", ejercicio="2026")
    _, filtro = fake.filters[0]
    assert "UPPER(DEEART) LIKE UPPER('%tinta%')" in filtro
    # Sin DESART, la descripción cae a la media y el SKU al código interno.
    assert items[0]["descripcion"] == "Tinta negra pigmentada"
    assert items[0]["sku"] == "00002"


# --- creación ---------------------------------------------------------------


def test_create_quote_writes_header_and_lines_to_f_lps(session):
    """C-4-fix3: cabecera en F_PRE + una fila por línea en F_LPS, con POSLPS
    correlativo. Antes solo se escribía la cabecera."""
    fake = _FakeFactusol(quotes=[_quote_row(50)], articles=[
        {"CODART": "HDMI", "EQUART": ""}, {"CODART": "SAT", "EQUART": ""},
    ])
    result = create_quote(
        fake, session, ejercicio="2026",
        customer={"codcli": "55555", "nombre": "Acme SL", "nif": "B12345678"},
        lines=[
            {"codart": "HDMI", "description": "Cable HDMI", "quantity": 2,
             "unit_price": 10, "iva_pct": 21},
            {"description": "Montaje", "quantity": 1, "unit_price": 30,
             "iva_pct": 21},
            {"codart": "SAT", "description": "Hora SAT", "quantity": 1,
             "unit_price": 60, "iva_pct": 21},
        ],
    )
    assert [t for t, _ in fake.writes] == ["F_PRE", "F_LPS", "F_LPS", "F_LPS"]

    header = fake.writes_to("F_PRE")[0]
    assert header["CODPRE"] == "51"
    assert header["CLIPRE"] == "55555"
    assert header["NET1PRE"] == 110.0
    # C-4-fix5: sin referencia explícita del operador, REFPRE no se escribe.
    assert "REFPRE" not in header

    lines = fake.writes_to("F_LPS")
    assert [line["POSLPS"] for line in lines] == [1, 2, 3]
    assert all(line["CODLPS"] == "51" for line in lines)
    assert all(line["TIPLPS"] == "1" for line in lines)
    assert lines[0]["ARTLPS"] == "HDMI"
    assert lines[0]["CANLPS"] == 2
    assert lines[0]["TOTLPS"] == 20.0
    # Línea de texto libre: sin artículo, pero se escribe igual.
    assert lines[1]["ARTLPS"] == ""
    assert result["lines"] == 3


def test_build_quote_payload_uses_cempre_not_emapre_for_email():
    """Regresión de C-4-fix4. El email de F_PRE es CEMPRE; `EMAPRE` existe en
    F_CLI/F_ART pero NO en F_PRE, y enviarlo hace fallar el EscribirRegistro
    ENTERO — bloqueó la creación de proformas en producción."""
    payload = build_quote_payload(
        "4400", ejercicio="2026",
        customer={"codcli": "1", "nombre": "TEST", "email": "test@test.com"},
        refpre="TEST", lines=[],
    )
    assert "EMAPRE" not in payload, "EMAPRE no existe en F_PRE; usar CEMPRE"
    assert payload["CEMPRE"] == "test@test.com"


def test_create_quote_writes_email_as_cempre(session):
    """El mismo guard, pero sobre lo que llega de verdad a EscribirRegistro."""
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026",
        customer={"codcli": "55555", "nombre": "Acme SL",
                  "email": "compras@acme.example", "telefono": "934000000"},
        lines=[{"description": "Cable", "quantity": 1, "unit_price": 10}],
    )
    header = fake.writes_to("F_PRE")[0]
    assert "EMAPRE" not in header
    assert header["CEMPRE"] == "compras@acme.example"
    # TELPRE sí existe en F_PRE: el bisecado en vivo lo descartó como causa.
    assert header["TELPRE"] == "934000000"


def test_write_quote_line_translates_equart_to_codart(session):
    """C-4-fix5: el autocomplete devuelve el EQUART comercial («Ink500mlCY»),
    pero ARTLPS tiene que llevar el CODART interno («99cy»). Con el EQUART, el
    FACTUSOL de escritorio CRASHEA al abrir la proforma (le pasó a la 4350)."""
    fake = _FakeFactusol(articles=[
        {"CODART": "99cy", "EQUART": "Ink500mlCY", "DESART": "UV INK CYAN"},
    ])
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"codart": "Ink500mlCY", "description": "UV INK CYAN",
                "quantity": 1, "unit_price": 80}],
    )
    assert fake.writes_to("F_LPS")[0]["ARTLPS"] == "99cy"


def test_write_quote_line_keeps_valid_codart(session):
    """Un CODART que ya es interno pasa tal cual."""
    fake = _FakeFactusol(articles=[
        {"CODART": "1712", "EQUART": "CAB-HDMI", "DESART": "Cable"},
    ])
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"codart": "1712", "description": "Cable", "quantity": 1,
                "unit_price": 10}],
    )
    assert fake.writes_to("F_LPS")[0]["ARTLPS"] == "1712"


def test_write_quote_line_unknown_sku_becomes_free_text(session):
    """Un SKU que no casa con nada va como línea de texto libre (ARTLPS=''),
    que FACTUSOL admite — mejor eso que un código que rompa la proforma."""
    fake = _FakeFactusol(articles=[])
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"codart": "NO-EXISTE", "description": "Mano de obra",
                "quantity": 1, "unit_price": 60}],
    )
    line = fake.writes_to("F_LPS")[0]
    assert line["ARTLPS"] == ""
    assert line["DESLPS"] == "Mano de obra"


def test_resolve_codarts_uses_a_single_query_for_all_lines():
    """Una consulta por proforma, no dos por línea: el autocomplete puede
    meter muchas líneas y cada llamada a DELSOL cuesta."""
    fake = _FakeFactusol(articles=[
        {"CODART": "99cy", "EQUART": "Ink500mlCY"},
        {"CODART": "1712", "EQUART": "CAB-HDMI"},
    ])
    mapping = resolve_codarts(
        fake, ["Ink500mlCY", "1712", "NO-EXISTE"], ejercicio="2026",
    )
    assert mapping == {"Ink500mlCY": "99cy", "1712": "1712"}
    assert len([f for t, f in fake.filters if t == "F_ART"]) == 1


def test_create_quote_does_not_write_ivalps(session):
    """IVALPS no se escribe: no está confirmado si guarda el % o el CÓDIGO de
    tipo de IVA. La proforma 574, que abre bien, tiene IVALPS=0 en todas sus
    líneas — un 0 % no tiene sentido, un código «general» sí. Se deja que
    FACTUSOL ponga su default; el IVA real viaja en la cabecera."""
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"description": "Cable", "quantity": 1, "unit_price": 10,
                "iva_pct": 21}],
    )
    assert "IVALPS" not in fake.writes_to("F_LPS")[0]
    # El IVA sí va en la cabecera, que es de donde salen los totales.
    assert fake.writes_to("F_PRE")[0]["PIVA1PRE"] == 21.0


def test_read_line_ignores_ivalps_that_cannot_be_a_spanish_rate():
    """Si IVALPS resulta ser un código, un 1 se leería como «1 % de IVA», que
    no existe. Solo se acepta el valor si puede ser un tipo español."""
    fake = _FakeFactusol(lines=[
        _line_row(10, 1, iva=1),    # código «reducido», no un 1 %
        _line_row(10, 2, iva=10),   # sí es un tipo español
    ])
    lines = list_quote_lines(fake, "10", ejercicio="2026")
    assert lines[0]["iva_pct"] == 21.0
    assert lines[1]["iva_pct"] == 10.0


def test_create_quote_leaves_refpre_empty_without_explicit_reference(session):
    """C-4-fix5: sin referencia del operador, REFPRE se queda vacío. Antes se
    auto-rellenaba con un resumen de las líneas, que desde que existe F_LPS es
    ruido duplicado en el campo «Su ref.» del documento."""
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"description": "UV INK", "quantity": 1, "unit_price": 80}],
    )
    assert "REFPRE" not in fake.writes_to("F_PRE")[0]


def test_create_quote_keeps_explicit_reference(session):
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"description": "UV INK", "quantity": 1, "unit_price": 80}],
        referencia="Pedido telefónico Marta",
    )
    assert fake.writes_to("F_PRE")[0]["REFPRE"] == "Pedido telefónico Marta"


def test_create_quote_line_payload_applies_discount(session):
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"codart": "ART-1", "description": "Cable", "quantity": 3,
                "unit_price": 10, "discount_pct": 10, "iva_pct": 21}],
    )
    line = fake.writes_to("F_LPS")[0]
    assert line["DT1LPS"] == 10
    assert line["TOTLPS"] == 27.0  # 3 × 10 − 10 %


def test_create_quote_incomplete_lines_do_not_fail_the_job(session):
    """La cabecera ya existe cuando se escriben las líneas. Propagar el error
    haría que el operador reintentase y creara una proforma DUPLICADA en la
    contabilidad; una proforma incompleta es reparable, el duplicado no."""
    fake = _FakeFactusol(write_fails_for="F_LPS")
    result = create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"description": "Cable", "quantity": 1, "unit_price": 10}],
    )
    assert result["codpre"] == "1"
    assert result["lines"] == 0
    assert "1 líneas" in result["warning"]
    assert fake.writes_to("F_PRE")  # la cabecera sí se escribió


def test_create_quote_exige_cliente_de_factusol(session):
    with pytest.raises(FactusolError, match="CODCLI"):
        create_quote(_FakeFactusol(), session, ejercicio="2026",
                     customer={"nombre": "Sin código"},
                     lines=[{"description": "X", "quantity": 1, "unit_price": 1}])


def test_create_quote_rechaza_proforma_vacia(session):
    with pytest.raises(FactusolError, match="al menos una línea"):
        create_quote(_FakeFactusol(), session, ejercicio="2026",
                     customer={"codcli": "55555"}, lines=[])


def test_create_quote_acepta_referencia_libre_sin_lineas(session):
    """Una proforma de una frase sin desglose sigue siendo válida."""
    fake = _FakeFactusol()
    create_quote(fake, session, ejercicio="2026",
                 customer={"codcli": "55555", "nombre": "Acme SL"},
                 lines=[], referencia="Presupuesto instalación sala 3")
    assert fake.writes_to("F_PRE")[0]["REFPRE"] == "Presupuesto instalación sala 3"
    assert fake.writes_to("F_LPS") == []


# --- duplicar ---------------------------------------------------------------


def test_duplicate_quote_copia_la_fila_con_codigo_y_fecha_nuevos(session):
    fake = _FakeFactusol(quotes=[_quote_row(60, ref="Original")])
    result = duplicate_quote(fake, session, "60", ejercicio="2026",
                             fecha="2026-08-05")
    payload = fake.writes_to("F_PRE")[0]
    assert payload["CODPRE"] == "61"
    assert payload["FECPRE"] == "2026-08-05"
    # El resto de la fila se arrastra intacto (importes, cliente, columnas
    # que ni siquiera mapeamos).
    assert payload["REFPRE"] == "Original"
    assert payload["CLIPRE"] == "55555"
    assert result["source_codpre"] == "60"


def test_duplicate_quote_copies_lines_from_f_lps(session):
    """C-4-fix3: las líneas salen de F_LPS, así que duplicar funciona también
    con las proformas creadas en el FACTUSOL de escritorio."""
    fake = _FakeFactusol(
        quotes=[_quote_row(60)],
        lines=[
            _line_row(60, 1, art="MBO", desc="Cabezal MBO", precio=250),
            _line_row(60, 2, art="CAP", desc="Capping", precio=25),
            _line_row(60, 3, art="WIP", desc="Wiper", precio=20),
            _line_row(60, 4, desc="Hora SAT", precio=60),
        ],
        articles=[{"CODART": "MBO"}, {"CODART": "CAP"}, {"CODART": "WIP"}],
    )
    result = duplicate_quote(fake, session, "60", ejercicio="2026")

    copied = fake.writes_to("F_LPS")
    assert len(copied) == 4
    assert all(line["CODLPS"] == "61" for line in copied)
    assert [line["POSLPS"] for line in copied] == [1, 2, 3, 4]
    assert [line["DESLPS"] for line in copied] == [
        "Cabezal MBO", "Capping", "Wiper", "Hora SAT",
    ]
    # Los CODART del origen ya son internos: sobreviven la ida y vuelta.
    assert [line["ARTLPS"] for line in copied] == ["MBO", "CAP", "WIP", ""]
    assert result["lines"] == 4


def test_duplicate_quote_falla_si_la_proforma_no_existe(session):
    with pytest.raises(FactusolError, match="no existe"):
        duplicate_quote(_FakeFactusol(), session, "404", ejercicio="2026")


# --- volcado a pedido -------------------------------------------------------


def test_quote_lines_for_order_usa_las_lineas_de_f_lps(session):
    fake = _FakeFactusol(
        quotes=[_quote_row(70)],
        lines=[_line_row(70, 1, art="ART-1", desc="Cable HDMI", cant=2, precio=10),
               _line_row(70, 2, desc="Montaje", precio=30)],
    )
    data = quote_lines_for_order(fake, session, "70", ejercicio="2026")
    assert data["line_source"] == "F_LPS"
    assert [line["description"] for line in data["lines"]] == ["Cable HDMI", "Montaje"]


def test_quote_lines_for_order_reconstruye_una_linea_si_no_hay_ninguna(session):
    """Edge case: proforma sin filas en F_LPS. Se reconstruye desde la cabecera
    para no dejar el pedido vacío."""
    fake = _FakeFactusol(quotes=[_quote_row(70, ref="Reparación pantalla")])
    data = quote_lines_for_order(fake, session, "70", ejercicio="2026")
    assert len(data["lines"]) == 1
    assert data["lines"][0]["description"] == "Reparación pantalla"
    assert data["lines"][0]["unit_price"] == 100.0  # la base imponible


def test_convert_to_order_populates_lines(session):
    """C-4-fix3: el pedido hereda las líneas reales de F_LPS, con SKU."""
    company = Company(name="Acme SL", factusol_company_id="55555")
    session.add(company)
    session.commit()
    fake = _FakeFactusol(
        quotes=[_quote_row(80)],
        lines=[_line_row(80, 1, art="ART-1", desc="Cable HDMI", cant=2, precio=10),
               _line_row(80, 2, art="SAT", desc="Hora SAT", cant=1, precio=60)],
    )

    result = convert_quote_to_order(fake, session, "80", ejercicio="2026")

    order = session.get(Order, result["order_id"])
    assert order.order_number.startswith("MANUAL-")
    assert order.company_id == company.id  # resuelto por el vínculo CODCLI
    assert float(order.total_amount) == 80.0
    lines = list(session.scalars(
        select(OrderLine).where(OrderLine.order_id == order.id)
        .order_by(OrderLine.position)
    ))
    assert len(lines) == 2
    assert lines[0].description == "Cable HDMI"
    assert lines[0].product_codart == "ART-1"
    assert lines[0].product_sku == "ART-1"
    assert float(lines[1].unit_price) == 60.0


def test_convert_quote_to_order_no_escribe_nada_en_factusol(session):
    """La conversión crea el pedido del CRM. Escribir un F_PCL exigiría un
    mapeo F_PRE→F_PCL no verificado, y en escritura una columna inexistente
    sí revienta (en lectura devuelve [] en silencio)."""
    fake = _FakeFactusol(quotes=[_quote_row(81)])
    convert_quote_to_order(fake, session, "81", ejercicio="2026")
    assert fake.writes == []
