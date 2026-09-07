"""ERP-E4-fix1 Parte I — idioma persistido y su cascada de resolución.

El idioma es un dato que el sistema conoce y arrastra: detectado al importar
de WooCommerce, editable en pedido y empresa, y resuelto en cascada al
generar el PDF (selector → pedido → cliente → empresa emisora → español).
La «herencia por la cadena» no escribe nada en FACTUSOL: viaja por la
referencia común (REF*) que las conversiones de E3-B ya copian por sufijo.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.db.session import get_session
from app.erp.factusol_pdf import suggest_pdf_language
from app.erp.models import Order, OrderSource
from app.integrations.woocommerce.mapper import detect_order_language
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users


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


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _order(session: Session, *, order_number: str = "BOPRIN-99917",
           language: str | None = None, **over: Any) -> Order:
    order = Order(
        external_source=OrderSource.WOOCOMMERCE,
        order_number=order_number,
        total_amount=100, currency="EUR",
        language=language,
        **over,
    )
    session.add(order)
    session.commit()
    return order


def _company(session: Session, *, codcli: str = "2458",
             language: str | None = None, country: str | None = None) -> Company:
    company = Company(
        name="DUPLICODER, S.L.", source="manual",
        factusol_company_id=codcli, language=language, country=country,
    )
    session.add(company)
    session.commit()
    return company


def _doc(**over: Any) -> dict[str, Any]:
    """Detalle normalizado mínimo de un documento FACTUSOL."""
    return {
        "serie": 5, "codigo": 260063, "referencia": "BOP-099917",
        "cliente_codigo": "2458",
        **over,
    }


# ---------------------------------------------------------------------------
# Detección en la importación Woo
# ---------------------------------------------------------------------------


def test_order_language_captured_on_woo_import() -> None:
    # WPML en meta_data (la clave más habitual).
    assert detect_order_language({
        "meta_data": [{"key": "wpml_language", "value": "fr"}],
    }) == ("fr", "meta:wpml_language")
    # Variantes: _wpml_language, locale con región, Polylang.
    assert detect_order_language({
        "meta_data": [{"key": "_wpml_language", "value": "de_DE"}],
    })[0] == "de"
    assert detect_order_language({
        "meta_data": [{"key": "_locale", "value": "nl-NL"}],
    })[0] == "nl"
    assert detect_order_language({
        "meta_data": [{"key": "pll_language", "value": "en"}],
    })[0] == "en"
    # Campo de primer nivel.
    assert detect_order_language({"customer_locale": "fr_FR"}) == (
        "fr", "customer_locale",
    )
    # Fallback: país de facturación (solo países sin ambigüedad).
    assert detect_order_language({"billing": {"country": "FR"}}) == (
        "fr", "billing.country:FR",
    )
    assert detect_order_language({"billing": {"country": "ES"}})[0] == "es"
    # E4-fix2: BE→fr y CH→en dejan de quedar vacíos (decisión de Bart).
    assert detect_order_language({"billing": {"country": "BE"}}) == (
        "fr", "billing.country:BE",
    )
    assert detect_order_language({"billing": {"country": "CH"}}) == (
        "en", "billing.country:CH",
    )
    # Un país con valor pero fuera del mapa → inglés (no vacío).
    assert detect_order_language({"billing": {"country": "JP"}}) == (
        "en", "billing.country:JP",
    )
    # Idioma no soportado → se ignora y se sigue sondeando.
    assert detect_order_language({
        "meta_data": [{"key": "wpml_language", "value": "it"}],
        "billing": {"country": "DE"},
    }) == ("de", "billing.country:DE")
    # Pedido SIN país → vacío (no hay nada de lo que deducir).
    assert detect_order_language({}) == (None, None)
    assert detect_order_language({"billing": {"country": ""}}) == (None, None)


def test_woo_import_sets_language_on_created_order(db) -> None:
    from unittest.mock import MagicMock

    from app.integrations.woocommerce.mapper import _create_order

    contact = MagicMock(id="contact-1")
    store = MagicMock(id="store-1")
    woo = {
        "id": 991, "number": "99917", "_store_slug": "boprin",
        "total": "100.0", "currency": "EUR",
        "meta_data": [{"key": "wpml_language", "value": "fr"}],
    }
    order = _create_order(db, woo, store, contact, None)
    assert order.language == "fr"


# ---------------------------------------------------------------------------
# Cascada de resolución
# ---------------------------------------------------------------------------


def test_language_cascade_order(db) -> None:
    """Los niveles de la cascada, cada uno ganando al siguiente. (El nivel 1
    — el selector de la descarga — lo aplica la UI por encima de todo.)"""
    # Nivel 5: emisora sin idioma configurado (serie 7) → español.
    assert suggest_pdf_language(db, "facturas", _doc(serie=7)) == {
        "lang": "es", "source": "defecto",
    }
    # Nivel 4: idioma por defecto de la empresa emisora (serie 2 = MQ → en;
    # serie 5 = Streamtec → es, también «empresa»).
    assert suggest_pdf_language(db, "facturas", _doc(serie=2)) == {
        "lang": "en", "source": "empresa",
    }
    assert suggest_pdf_language(db, "facturas", _doc(serie=5)) == {
        "lang": "es", "source": "empresa",
    }
    # Nivel 3: idioma del cliente (empresa CRM vinculada por CODCLI).
    _company(db, language="de")
    assert suggest_pdf_language(db, "facturas", _doc(serie=2)) == {
        "lang": "de", "source": "cliente",
    }
    # Nivel 2: idioma del pedido — gana al cliente.
    _order(db, language="fr")
    assert suggest_pdf_language(db, "facturas", _doc(serie=2)) == {
        "lang": "fr", "source": "pedido",
    }


def test_language_inherited_through_conversion_chain(db) -> None:
    """Pedido FR → albarán FR → factura FR: la referencia común (REF*) que
    las conversiones de E3-B copian por sufijo liga cada documento de la
    cadena con su pedido del CRM — sin escribir nada en FACTUSOL."""
    _order(db, language="fr")
    for doc_type in ("pedidos", "albaranes", "facturas"):
        out = suggest_pdf_language(db, doc_type, _doc())
        assert out == {"lang": "fr", "source": "pedido"}, doc_type
    # Y la factura emitida desde BoHub se liga también por su CODFAC.
    _order(db, order_number="MANUAL-000123", language="nl")
    order = db.query(Order).filter_by(order_number="MANUAL-000123").one()
    order.factusol_invoice_number = "260099"
    db.commit()
    out = suggest_pdf_language(
        db, "facturas", _doc(codigo=260099, referencia="", cliente_codigo=""),
    )
    assert out == {"lang": "nl", "source": "pedido"}


def test_language_falls_back_to_company_default_then_spanish(db) -> None:
    # Cliente vinculado SIN idioma → cae a la emisora; emisora sin default
    # conocido (serie 7) → español.
    _company(db, language=None)
    assert suggest_pdf_language(db, "albaranes", _doc(serie=2))["lang"] == "en"
    assert suggest_pdf_language(db, "albaranes", _doc(serie=7)) == {
        "lang": "es", "source": "defecto",
    }
    # Un idioma no soportado guardado en el pedido no rompe la cascada.
    _order(db, language="it")
    assert suggest_pdf_language(db, "albaranes", _doc(serie=2))["lang"] == "en"


# ---------------------------------------------------------------------------
# E4-fix2 — idioma por país del cliente
# ---------------------------------------------------------------------------


def test_country_language_map_covers_ambiguous() -> None:
    from app.erp.language import language_for_country

    assert language_for_country("BE") == "fr"    # Bélgica → francés
    assert language_for_country("CH") == "en"    # Suiza → inglés
    assert language_for_country("ES") == "es"
    assert language_for_country("AT") == "de"
    # Cualquier país con valor pero no listado → inglés (no vacío).
    assert language_for_country("JP") == "en"
    assert language_for_country("us") == "en"    # tolera minúsculas


def test_missing_country_leaves_language_empty() -> None:
    from app.erp.language import language_for_country

    # Sin país no hay nada de lo que deducir → None (la cascada sigue).
    assert language_for_country(None) is None
    assert language_for_country("") is None
    assert language_for_country("   ") is None


def test_cascade_includes_client_country_step(db) -> None:
    """Empresa cliente SIN idioma pero CON país → idioma derivado del país,
    con procedencia «país del cliente» (gana a la emisora)."""
    _company(db, language=None, country="BE")
    # serie 5 = Streamtec (emisora → es); el país del cliente (BE→fr) manda.
    assert suggest_pdf_language(db, "facturas", _doc(serie=5)) == {
        "lang": "fr", "source": "pais_cliente",
    }


def test_explicit_client_language_beats_country(db) -> None:
    """Si el cliente tiene idioma puesto a mano, el país NO manda (validación
    4 de Bart: suizo con «alemán» explícito → alemán, no inglés)."""
    _company(db, language="de", country="CH")
    assert suggest_pdf_language(db, "facturas", _doc(serie=2)) == {
        "lang": "de", "source": "cliente",
    }


def test_backfill_dry_run_writes_nothing(db) -> None:
    from scripts.backfill_company_language import run

    _company(db, codcli="A", language=None, country="BE")
    _company(db, codcli="B", language=None, country="CH")
    _company(db, codcli="C", language=None, country=None)  # sin país
    stats = run(apply=False, session=db)
    assert stats["afectadas"] == 2 and stats["sin_pais"] == 1
    # Nada escrito.
    langs = {c.factusol_company_id: c.language
             for c in db.query(Company).all()}
    assert langs == {"A": None, "B": None, "C": None}


def test_backfill_does_not_overwrite_existing(db) -> None:
    from scripts.backfill_company_language import run

    _company(db, codcli="A", language=None, country="BE")   # → fr
    _company(db, codcli="B", language="nl", country="CH")   # ya tiene: intacto
    stats = run(apply=True, session=db)
    assert stats["afectadas"] == 1 and stats["ya_tenian"] == 1
    db.expire_all()
    langs = {c.factusol_company_id: c.language
             for c in db.query(Company).all()}
    assert langs == {"A": "fr", "B": "nl"}   # B no se pisó


# ---------------------------------------------------------------------------
# E4-fix3 — normalización del país antes de derivar idioma
# ---------------------------------------------------------------------------


def test_normalize_country_iso_passthrough() -> None:
    from app.erp.language import normalize_country

    assert normalize_country("ES") == "ES"
    assert normalize_country("fr") == "FR"     # minúsculas
    assert normalize_country(" BE ") == "BE"   # espacios


def test_normalize_country_names_multiple_languages() -> None:
    from app.erp.language import normalize_country

    for raw in ("ESPAÑA", "SPAIN", "España", " españa ", "Espagne"):
        assert normalize_country(raw) == "ES", raw
    assert normalize_country("FRANCE") == "FR"
    assert normalize_country("FRANCIA") == "FR"
    assert normalize_country("ALEMANIA") == "DE"
    assert normalize_country("GERMANY") == "DE"
    assert normalize_country("PAÍSES BAJOS") == "NL"
    assert normalize_country("NETHERLANDS") == "NL"
    assert normalize_country("HOLANDA") == "NL"
    for raw in ("BÉLGICA", "BELGIQUE", "BELGIUM", "België"):
        assert normalize_country(raw) == "BE", raw
    assert normalize_country("SUISSE") == "CH"
    assert normalize_country("SWITZERLAND") == "CH"


def test_normalize_country_strips_accents() -> None:
    from app.erp.language import normalize_country

    assert normalize_country("ALGÉRIE") == "DZ"
    assert normalize_country("RÉUNION, ÎLE DE LA") == "RE"
    assert normalize_country("CÔTE D'IVOIRE") == "CI"
    assert normalize_country("NOUVELLE-CALÉDONIE") == "NC"


def test_unknown_country_returns_none() -> None:
    from app.erp.language import language_for_country, normalize_country

    assert normalize_country("Pepe no es un país") is None
    assert normalize_country("XX") is None      # 2 letras pero no ISO2
    # Y no se inventa idioma: la cascada seguirá hacia la empresa emisora.
    assert language_for_country("valor basura") is None


def test_francophone_territories_map_to_french() -> None:
    from app.erp.language import language_for_country

    for pais in ("MQ", "RE", "YT", "GP", "PF", "NC", "GF",
                 "MA", "DZ", "TN", "CI", "SN", "MARTINIQUE", "RÉUNION",
                 "MAYOTTE", "MOROCCO", "ALGÉRIE"):
        assert language_for_country(pais) == "fr", pais


def test_andorra_maps_to_spanish() -> None:
    from app.erp.language import language_for_country

    assert language_for_country("AD") == "es"
    assert language_for_country("ANDORRA") == "es"


def test_switzerland_maps_to_english() -> None:
    from app.erp.language import language_for_country

    assert language_for_country("CH") == "en"
    assert language_for_country("SUISSE") == "en"


def test_backfill_spanish_companies_get_spanish(db) -> None:
    """Regresión del fallo de fix2: 3.000 empresas con país «ESPAÑA» (nombre,
    no ISO2) deben quedar en `es`, NO en `en`."""
    from scripts.backfill_company_language import run

    for i in range(3000):
        _company(db, codcli=f"ES{i}", language=None, country="ESPAÑA")
    _company(db, codcli="FR1", language=None, country="FRANCE")
    stats = run(apply=True, session=db)
    db.expire_all()
    por_idioma: dict[str, int] = {}
    for c in db.query(Company).all():
        por_idioma[c.language] = por_idioma.get(c.language, 0) + 1
    assert por_idioma.get("es") == 3000     # NO en inglés
    assert por_idioma.get("fr") == 1
    assert "en" not in por_idioma
    assert stats["afectadas"] == 3001 and stats["no_reconocidos"] == 0


def test_backfill_reports_unrecognized_countries(db) -> None:
    from scripts.backfill_company_language import run

    _company(db, codcli="A", language=None, country="ESPAÑA")
    _company(db, codcli="B", language=None, country="Marcianolandia")
    _company(db, codcli="C", language=None, country="Marcianolandia")
    _company(db, codcli="D", language=None, country=None)   # sin país
    stats = run(apply=False, session=db)
    # 2 con texto no reconocido (Marcianolandia x2) + 1 sin país = 3 «sin
    # país reconocido»; el reporte cuenta los 2 no-reconocidos aparte.
    assert stats["afectadas"] == 1        # solo ESPAÑA
    assert stats["no_reconocidos"] == 2
    assert stats["sin_pais"] == 3
    # Dry-run no escribe.
    db.expire_all()
    assert all(c.language is None for c in db.query(Company).all())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_order_language_patch_endpoint(http, session_factory) -> None:
    with session_factory() as s:
        order = _order(s, language=None)
        order_id = order.id
    headers = auth_headers(http, "pedidos")
    r = http.patch(
        f"/api/erp/orders/{order_id}/language", json={"language": "fr"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": order_id, "language": "fr"}
    detail = http.get(f"/api/erp/orders/{order_id}", headers=headers)
    assert detail.json()["language"] == "fr"
    # Vaciar = desconocido; idioma inválido = 422; solo-lectura = 403.
    assert http.patch(
        f"/api/erp/orders/{order_id}/language", json={"language": None},
        headers=headers,
    ).json()["language"] is None
    assert http.patch(
        f"/api/erp/orders/{order_id}/language", json={"language": "klingon"},
        headers=headers,
    ).status_code == 422
    assert http.patch(
        f"/api/erp/orders/{order_id}/language", json={"language": "fr"},
        headers=auth_headers(http, "user"),
    ).status_code == 403


def test_company_language_roundtrip(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "admin")
    created = http.post("/api/companies", json={
        "name": "Cliente Francés SARL", "language": "fr",
    }, headers=headers)
    assert created.status_code in (200, 201), created.text
    body = created.json()
    assert body["language"] == "fr"
    updated = http.put(f"/api/companies/{body['id']}", json={
        "name": "Cliente Francés SARL", "language": "de",
    }, headers=headers)
    assert updated.json()["language"] == "de"


def test_document_detail_includes_pdf_lang(http, session_factory) -> None:
    from unittest.mock import patch

    from tests.test_factusol_documents import FakeClient

    with session_factory() as s:
        _company(s, language="fr")
    tables = {
        "F_FAC": [{"TIPFAC": "5", "CODFAC": 260063, "CLIFAC": 2458,
                   "CNOFAC": "DUPLICODER", "TOTFAC": 1.0}],
        "F_LFA": [], "F_ALB": [], "F_LAL": [],
    }
    with patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=FakeClient(tables),
    ):
        r = http.get(
            "/api/erp/factusol/documents/facturas/5/260063",
            headers=auth_headers(http, "user"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["pdf_lang"] == {"lang": "fr", "source": "cliente"}


def test_pickup_warehouses_settings_roundtrip(http, session_factory) -> None:
    _ = session_factory
    headers = auth_headers(http, "admin")
    r = http.get("/api/erp/settings", headers=headers)
    almacenes = r.json()["factusol_pickup_warehouses"]
    assert almacenes[0]["nombre"] == "Almacén TERLO 2000"   # default A-321
    almacenes.append({"nombre": "Almacén Madrid", "direccion": "C/ Sur, 1"})
    r2 = http.patch("/api/erp/settings", json={
        "factusol_pickup_warehouses": almacenes,
    }, headers=headers)
    assert [w["nombre"] for w in r2.json()["factusol_pickup_warehouses"]] == [
        "Almacén TERLO 2000", "Almacén Madrid",
    ]
