"""BoHub ERP Fase C · C-4 — proformas F_PRE + artículos F_ART.

F_PRE es **mono-línea** (verificado en la base real: 653 presupuestos, sin
tabla de líneas), así que casi todo lo que se prueba aquí gira alrededor de
esa restricción: el desglose viaja resumido en REFPRE y el detalle real vive
en la caché local del CRM.

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
from app.erp.models import FactusolQuoteLineCache, Order, OrderLine
from app.integrations.factusol.client import FactusolError
from app.integrations.factusol.quotes import (
    REFPRE_MAX_LENGTH,
    build_refpre_from_lines,
    convert_quote_to_order,
    create_quote,
    duplicate_quote,
    get_quote,
    list_quotes,
    next_codpre,
    quote_lines_for_order,
    search_articles,
)
from app.models.crm import Company


class _FakeFactusol:
    """Doble del cliente: sirve F_PRE/F_ART y guarda las escrituras."""

    def __init__(self, *, quotes=None, articles=None):
        self.default_ejercicio = "2026"
        self._quotes = list(quotes or [])
        self._articles = list(articles or [])
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.filters: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.filters.append((tabla, filtro))
        if tabla == "F_ART":
            return list(self._articles)
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
        self.writes.append((tabla, dict(data)))
        return {"respuesta": "OK"}


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


# --- REFPRE: el desglose cabe o se trunca visiblemente ----------------------


def test_build_refpre_concatena_lineas_legibles():
    text = build_refpre_from_lines([
        {"description": "Cable HDMI 3m", "quantity": 2},
        {"description": "Montaje", "quantity": 1},
    ])
    assert text == "2x Cable HDMI 3m; 1x Montaje"


def test_build_refpre_trunca_a_250_con_marca():
    lines = [{"description": f"Artículo largo número {i}", "quantity": 1}
             for i in range(40)]
    text = build_refpre_from_lines(lines)
    assert len(text) <= REFPRE_MAX_LENGTH
    # Truncar en silencio ocultaría que se ha perdido detalle.
    assert text.endswith("…")


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


def test_get_quote_usa_la_cache_cuando_la_creo_el_crm(session):
    fake = _FakeFactusol(quotes=[_quote_row(42)])
    session.add(FactusolQuoteLineCache(
        factusol_codpre="42", ejercicio="2026", position=1, artlpc="ART-1",
        description="Cable HDMI", quantity=2, unit_price=10, discount_pct=0,
        line_total=20, iva_pct=21, created_at=date(2026, 8, 1),
    ))
    session.commit()
    quote = get_quote(fake, session, "42", ejercicio="2026")
    assert quote["line_source"] == "cache"
    assert len(quote["lines"]) == 1
    assert quote["lines"][0]["description"] == "Cable HDMI"


def test_get_quote_degrada_a_ref_text_sin_cache(session):
    """Una proforma hecha en el FACTUSOL de escritorio no tiene desglose: es
    la consecuencia directa de que F_PRE sea mono-línea."""
    fake = _FakeFactusol(quotes=[_quote_row(42, ref="Reparación pantalla")])
    quote = get_quote(fake, session, "42", ejercicio="2026")
    assert quote["line_source"] == "ref_text"
    assert quote["lines"] == []
    assert quote["referencia"] == "Reparación pantalla"


def test_get_quote_devuelve_none_si_no_existe(session):
    assert get_quote(_FakeFactusol(), session, "999", ejercicio="2026") is None


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


def test_search_articles_busca_en_codigo_ean_y_descripcion():
    fake = _FakeFactusol(articles=[])
    search_articles(fake, "hdmi", ejercicio="2026")
    _, filtro = fake.filters[0]
    assert "CODART" in filtro and "EANART" in filtro and "DESART" in filtro


# --- creación ---------------------------------------------------------------


def test_create_quote_escribe_una_sola_fila_con_totales(session):
    fake = _FakeFactusol(quotes=[_quote_row(50)])
    result = create_quote(
        fake, session, ejercicio="2026",
        customer={"codcli": "55555", "nombre": "Acme SL", "nif": "B12345678"},
        lines=[
            {"description": "Cable HDMI", "quantity": 2, "unit_price": 10,
             "iva_pct": 21},
            {"description": "Montaje", "quantity": 1, "unit_price": 30,
             "iva_pct": 21},
        ],
    )
    # Mono-línea: UNA escritura en F_PRE, ninguna tabla de líneas.
    assert [t for t, _ in fake.writes] == ["F_PRE"]
    payload = fake.writes[0][1]
    assert payload["CODPRE"] == "51"
    assert payload["CLIPRE"] == "55555"
    assert payload["NET1PRE"] == 50.0
    assert payload["IIVA1PRE"] == 10.5
    assert payload["TOTPRE"] == 60.5
    assert payload["REFPRE"] == "2x Cable HDMI; 1x Montaje"
    assert result["codpre"] == "51"


def test_create_quote_cachea_el_desglose_que_f_pre_no_puede_guardar(session):
    fake = _FakeFactusol()
    create_quote(
        fake, session, ejercicio="2026",
        customer={"codcli": "55555", "nombre": "Acme SL"},
        lines=[{"codart": "ART-1", "description": "Cable", "quantity": 3,
                "unit_price": 10, "discount_pct": 10, "iva_pct": 21}],
    )
    rows = list(session.scalars(select(FactusolQuoteLineCache)))
    assert len(rows) == 1
    assert rows[0].factusol_codpre == "1"
    assert rows[0].position == 1
    assert rows[0].artlpc == "ART-1"
    assert float(rows[0].line_total) == 27.0  # 3 × 10 − 10 %


def test_create_quote_no_falla_si_la_cache_local_peta(session, monkeypatch):
    """La proforma ya está escrita en FACTUSOL cuando se cachea el desglose.
    Propagar el error haría que el operador reintentase y creara un DUPLICADO
    en la contabilidad; perder el desglose solo degrada a modo simple."""
    from app.integrations.factusol import quotes as mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("BD caída")

    monkeypatch.setattr(mod, "_save_lines_cache", boom)
    fake = _FakeFactusol()
    result = create_quote(
        fake, session, ejercicio="2026", customer={"codcli": "55555"},
        lines=[{"description": "Cable", "quantity": 1, "unit_price": 10}],
    )
    assert result["codpre"] == "1"
    assert result["cached"] is False
    assert len(fake.writes) == 1  # la proforma sí se escribió


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
    """Modo «rápido»: F_PRE es mono-línea, así que una proforma de una frase
    es su forma nativa y debe poder crearse sin desglose."""
    fake = _FakeFactusol()
    create_quote(fake, session, ejercicio="2026",
                 customer={"codcli": "55555", "nombre": "Acme SL"},
                 lines=[], referencia="Presupuesto instalación sala 3")
    assert fake.writes[0][1]["REFPRE"] == "Presupuesto instalación sala 3"


# --- duplicar ---------------------------------------------------------------


def test_duplicate_quote_copia_la_fila_con_codigo_y_fecha_nuevos(session):
    fake = _FakeFactusol(quotes=[_quote_row(60, ref="Original")])
    result = duplicate_quote(fake, session, "60", ejercicio="2026",
                             fecha="2026-08-05")
    payload = fake.writes[0][1]
    assert payload["CODPRE"] == "61"
    assert payload["FECPRE"] == "2026-08-05"
    # El resto de la fila se arrastra intacto (importes, cliente, columnas
    # que ni siquiera mapeamos).
    assert payload["REFPRE"] == "Original"
    assert payload["CLIPRE"] == "55555"
    assert result["source_codpre"] == "60"


def test_duplicate_quote_duplica_tambien_el_desglose_cacheado(session):
    fake = _FakeFactusol(quotes=[_quote_row(60)])
    session.add(FactusolQuoteLineCache(
        factusol_codpre="60", ejercicio="2026", position=1, artlpc="",
        description="Cable", quantity=1, unit_price=10, discount_pct=0,
        line_total=10, iva_pct=21, created_at=date(2026, 8, 1),
    ))
    session.commit()
    duplicate_quote(fake, session, "60", ejercicio="2026")
    copied = list(session.scalars(
        select(FactusolQuoteLineCache)
        .where(FactusolQuoteLineCache.factusol_codpre == "61")
    ))
    assert len(copied) == 1
    assert copied[0].description == "Cable"


def test_duplicate_quote_falla_si_la_proforma_no_existe(session):
    with pytest.raises(FactusolError, match="no existe"):
        duplicate_quote(_FakeFactusol(), session, "404", ejercicio="2026")


# --- volcado a pedido -------------------------------------------------------


def test_quote_lines_for_order_reconstruye_una_linea_sin_cache(session):
    fake = _FakeFactusol(quotes=[_quote_row(70, ref="Reparación pantalla")])
    data = quote_lines_for_order(fake, session, "70", ejercicio="2026")
    assert data["line_source"] == "ref_text"
    assert len(data["lines"]) == 1
    assert data["lines"][0]["description"] == "Reparación pantalla"
    assert data["lines"][0]["unit_price"] == 100.0  # la base imponible


def test_convert_quote_to_order_crea_pedido_manual_del_crm(session):
    company = Company(name="Acme SL", factusol_company_id="55555")
    session.add(company)
    session.commit()
    fake = _FakeFactusol(quotes=[_quote_row(80)])
    session.add(FactusolQuoteLineCache(
        factusol_codpre="80", ejercicio="2026", position=1, artlpc="ART-1",
        description="Cable HDMI", quantity=2, unit_price=10, discount_pct=0,
        line_total=20, iva_pct=21, created_at=date(2026, 8, 1),
    ))
    session.commit()

    result = convert_quote_to_order(fake, session, "80", ejercicio="2026")

    order = session.get(Order, result["order_id"])
    assert order.order_number.startswith("MANUAL-")
    assert order.company_id == company.id  # resuelto por el vínculo CODCLI
    assert float(order.total_amount) == 20.0
    lines = list(session.scalars(
        select(OrderLine).where(OrderLine.order_id == order.id)
    ))
    assert len(lines) == 1
    assert lines[0].description == "Cable HDMI"
    assert lines[0].product_codart == "ART-1"


def test_convert_quote_to_order_no_escribe_nada_en_factusol(session):
    """La conversión crea el pedido del CRM. Escribir un F_PCL exigiría un
    mapeo F_PRE→F_PCL no verificado, y en escritura una columna inexistente
    sí revienta (en lectura devuelve [] en silencio)."""
    fake = _FakeFactusol(quotes=[_quote_row(81)])
    convert_quote_to_order(fake, session, "81", ejercicio="2026")
    assert fake.writes == []
