"""«PDF del pedido (FACTUSOL)» — documento de ORIGEN real del pedido.

`GET /api/erp/orders/{id}/factusol-pedido-pdf` imprime el documento de origen
según el pedido (Fase 1 guarda tipo + serie + nº en
`packing_json.factusol_source`): presupuesto → F_PRE, pedido de cliente → F_PCL
por serie+nº, **pedido web → F_PCL por `REFPCL`** (`find_pcl_by_order`: su
único enlace con FACTUSOL, no tiene `factusol_source`). Sin documento de
origen → 404 controlado sin consultar FACTUSOL y `factusol_document = null`
para deshabilitar el botón.

FIX «regresión web» (tras #397): el pedido web FLUXLA-5789 tiene su F_PCL
`5-000026` con `REFPCL = FLE-005789`, pero la referencia se compone con el
prefijo de la tienda y, si no está configurado, con las 3 primeras letras del
nº de pedido (`FLU-005789`) → «aún no existe». Ahora el prefijo se configura
por tienda en Ajustes ERP (`factusol_ref_prefix_by_store`, sin SQL) y el 404
dice QUÉ referencia se buscó y, si el mismo nº existe bajo otro prefijo, qué
prefijo configurar — sin adivinar nunca el documento (un homónimo de otra
tienda comparte número).
"""
from __future__ import annotations

import json
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
from app.erp.orders_from_factusol import factusol_source_block
from app.integrations.factusol.client import FactusolError
from app.main import app
from app.models.integration_settings import (
    ExternalSystem,
    IntegrationAccount,
    IntegrationMode,
)
from tests._test_helpers import auth_headers, seed_test_users
from tests.test_factusol_documents import FakeClient

PRESUPUESTO = {
    "TIPPRE": "1", "CODPRE": 4352, "CLIPRE": 2458, "CNOPRE": "DUPLICODER, S.L.",
    "FECPRE": "2026-09-10T00:00:00", "ESTPRE": 1, "REFPRE": "Obra X",
    "TOTPRE": 186.34, "NET1PRE": 154.0, "PIVA1PRE": 21.0, "IIVA1PRE": 32.34,
    "BAS1PRE": 154.0, "FOPPRE": "002", "CPAPRE": "724",
}
LINEAS_LPS = [
    {"TIPLPS": "1", "CODLPS": 4352, "POSLPS": 1, "ARTLPS": "99cy",
     "DESLPS": "Tinta cyan", "CANLPS": 2, "PRELPS": 40.0, "TOTLPS": 80.0, "IVALPS": 21},
    {"TIPLPS": "1", "CODLPS": 4352, "POSLPS": 2, "ARTLPS": "",
     "DESLPS": "Portes", "CANLPS": 1, "PRELPS": 74.0, "TOTLPS": 74.0, "IVALPS": 21},
]
PEDIDO_CLIENTE = {
    "TIPPCL": "5", "CODPCL": 123, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "ENCARGO TALLER 7",
    "TOTPCL": 60.5, "NET1PCL": 50.0, "PIVA1PCL": 21.0, "FOPPCL": "011",
}
PEDIDO_WEB = {
    "TIPPCL": "5", "CODPCL": 5, "CLIPCL": 2458, "CNOPCL": "DUPLICODER, S.L.",
    "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": "BOP-099917",
    "TOTPCL": 99.0, "NET1PCL": 81.82, "PIVA1PCL": 21.0, "FOPPCL": "002",
}
# El caso real: FLUXLA-5789 (Persoregala SL) → F_PCL 5-000026, «Su
# referencia» FLE-005789 (la app Woo→FACTUSOL usa FLE, no FLU).
PEDIDO_WEB_FLUX = {
    "TIPPCL": "5", "CODPCL": 26, "CLIPCL": 3101, "CNOPCL": "PERSOREGALA SL",
    "FECPCL": "2026-09-11T00:00:00", "ESTPCL": 0, "REFPCL": "FLE-005789",
    "TOTPCL": 121.0, "NET1PCL": 100.0, "PIVA1PCL": 21.0, "FOPPCL": "002",
}
LINEAS_LPC = [
    {"TIPLPC": "5", "CODLPC": 123, "POSLPC": 1, "ARTLPC": "CDR80WPT",
     "DESLPC": "CD TQ 700 MB", "CANLPC": 100, "PRELPC": 0.5, "TOTLPC": 50.0},
    {"TIPLPC": "5", "CODLPC": 5, "POSLPC": 1, "ARTLPC": "99cy",
     "DESLPC": "Tinta", "CANLPC": 1, "PRELPC": 81.82, "TOTLPC": 81.82},
    {"TIPLPC": "5", "CODLPC": 26, "POSLPC": 1, "ARTLPC": "LASER-GRB",
     "DESLPC": "Grabado láser", "CANLPC": 1, "PRELPC": 100.0, "TOTLPC": 100.0},
]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_PRE": [dict(PRESUPUESTO)], "F_LPS": [dict(r) for r in LINEAS_LPS],
        "F_PCL": [dict(PEDIDO_CLIENTE), dict(PEDIDO_WEB), dict(PEDIDO_WEB_FLUX)],
        "F_LPC": [dict(r) for r in LINEAS_LPC],
        "F_FPA": [{"CODFPA": "002", "DESFPA": "Transferencia"}],
    }


def _packing(doc_type: str, serie: int, codigo: int) -> str:
    return json.dumps({"factusol_source": factusol_source_block(
        doc_type=doc_type, serie=serie, codigo=codigo, referencia=None,
        forma_pago=None, forma_pago_nombre=None,
    )})


def _store(slug: str, label: str, prefix: str | None) -> IntegrationAccount:
    return IntegrationAccount(
        id=f"store-{slug}", system=ExternalSystem.WOOCOMMERCE, account_id=slug,
        display_name=label, enabled=True, mode=IntegrationMode.LIVE,
        base_url=f"https://{slug}.example",
        metadata_json=json.dumps({"factusol_ref_prefix": prefix}) if prefix else None,
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
        seed.add_all([
            # boprin: prefijo fijado a mano en el metadata_json de la cuenta.
            _store("boprin", "BoPrint", "BOP"),
            # fluxlasers: SIN prefijo en la cuenta (el caso real) → se
            # configura en Ajustes ERP o se deriva («FLU»).
            _store("fluxlasers", "Flux Lasers", None),
        ])
        seed.flush()
        seed.add_all([
            # PRO-004352: creado desde el presupuesto 1-004352 (Fase 1); la
            # Fase 2 le creó el albarán 1-100327. NO tiene F_PCL.
            Order(id="o-pro", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="4352", order_number="PRO-004352", total_amount=186.34,
                  factusol_albaran_number="1-100327",
                  packing_json=_packing("presupuestos", 1, 4352)),
            Order(id="o-pcl", external_source=OrderSource.FACTUSOL_PEDIDO,
                  external_id="5-000123", order_number="PCL-5-000123", total_amount=60.5,
                  packing_json=_packing("pedidos", 5, 123)),
            Order(id="o-web", external_source=OrderSource.WOOCOMMERCE,
                  external_id="99917", order_number="BOPRIN-99917", total_amount=99.0,
                  store_id="store-boprin"),
            Order(id="o-web-flux", external_source=OrderSource.WOOCOMMERCE,
                  external_id="5789", order_number="FLUXLA-5789", total_amount=121.0,
                  store_id="store-fluxlasers"),
            Order(id="o-web-nuevo", external_source=OrderSource.WOOCOMMERCE,
                  external_id="5790", order_number="FLUXLA-5790", total_amount=10.0,
                  store_id="store-fluxlasers"),
            Order(id="o-manual", external_source=OrderSource.MANUAL,
                  order_number="MANUAL-000001", total_amount=40.0),
            Order(id="o-pro-borrado", external_source=OrderSource.FACTUSOL_PROFORMA,
                  external_id="9999", order_number="PRO-009999", total_amount=1.0,
                  packing_json=_packing("presupuestos", 1, 9999)),
        ])
        seed.commit()
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


def _patched(fake: FakeClient):
    return patch(
        "app.integrations.factusol.client.FactusolClient.from_settings",
        return_value=fake,
    )


def _pdf(http, order_id: str, **params):
    return http.get(f"/api/erp/orders/{order_id}/factusol-pedido-pdf",
                    params=params, headers=auth_headers(http, "user"))


def _detail(http, order_id: str) -> dict[str, Any]:
    r = http.get(f"/api/erp/orders/{order_id}", headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()


def _set_ref_prefixes(http, mapping: dict[str, str]) -> dict[str, Any]:
    r = http.patch("/api/erp/settings",
                   json={"factusol_ref_prefix_by_store": mapping},
                   headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    return r.json()


def test_pdf_pedido_desde_proforma_usa_presupuesto(http) -> None:
    """Origen proforma → el PDF es el del presupuesto F_PRE de origen (serie +
    nº guardados en el pedido), sin buscar ningún F_PCL por REFPCL. La ficha
    recibe el documento de origen (el botón se etiqueta igual, pero el tooltip
    y el fichero saben qué se imprime)."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-pro")
        r = _pdf(http, "o-pro", lang="es")
    assert detail["factusol_document"] == {
        "doc_type": "presupuestos", "serie": 1, "codigo": 4352, "numero": "1-004352",
        "label": "presupuesto", "by_ref": False, "ref": None,
    }
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    disposition = r.headers["content-disposition"]
    assert "Presupuesto" in disposition and "1-004352" in disposition
    assert ("F_PRE", "CODPRE=4352") in fake.calls
    assert ("F_LPS", "CODLPS=4352") in fake.calls
    assert not any(t == "F_PCL" for t, _ in fake.calls)      # ni un REFPCL
    # El presupuesto ya no está en FACTUSOL → 404 propio, no un 502 ni un 500.
    with _patched(FakeClient(_tables())):
        gone = _pdf(http, "o-pro-borrado")
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "document_not_in_factusol"
    assert "1-009999" in gone.json()["detail"]["detail"]


def test_pdf_pedido_desde_pedido_cliente_usa_fpcl(http) -> None:
    """Origen pedido de cliente (manual) → sigue con el F_PCL por serie + nº
    guardados (clave compuesta), sin buscar por REFPCL."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-pcl")
        r = _pdf(http, "o-pcl")
    assert detail["factusol_document"]["doc_type"] == "pedidos"
    assert detail["factusol_document"]["numero"] == "5-000123"
    assert detail["factusol_document"]["by_ref"] is False
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    assert "5-000123" in r.headers["content-disposition"]
    assert ("F_PCL", "CODPCL=123") in fake.calls
    assert ("F_LPC", "CODLPC=123") in fake.calls
    assert not any("REFPCL" in f for _, f in fake.calls)


def test_pdf_pedido_web_usa_fpcl_por_ref(http) -> None:
    """Regresión cubierta: un pedido WEB (sin `factusol_source`) localiza su
    F_PCL por `REFPCL` (`find_pcl_by_order`) y descarga el PDF E4 — con el
    prefijo de la tienda fijado en la cuenta (BOP) o configurado en Ajustes
    ERP (FLE para fluxlasers, el caso FLUXLA-5789). La ficha NO consulta
    FACTUSOL al cargar (nada de falsos negativos: `by_ref` + la referencia que
    se buscará) y el botón siempre intenta la descarga."""
    # (a) prefijo en el metadata_json de la cuenta (boprin → BOP).
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-web")
        assert fake.calls == []                       # la ficha no toca FACTUSOL
        r = _pdf(http, "o-web")
    assert detail["factusol_document"] == {
        "doc_type": "pedidos", "serie": None, "codigo": None, "numero": None,
        "label": "pedido de cliente", "by_ref": True, "ref": "BOP-099917",
    }
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    assert "5-000005" in r.headers["content-disposition"]
    assert fake.calls[0] == ("F_PCL", "REFPCL='BOP-099917'")
    assert ("F_LPC", "CODLPC=5") in fake.calls
    assert not any("LIKE" in f for _, f in fake.calls)   # sin sondeo si aparece

    # (b) tienda SIN prefijo en la cuenta: configurado en Ajustes ERP → FLE.
    _set_ref_prefixes(http, {"fluxlasers": "FLE"})
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-web-flux")
        r = _pdf(http, "o-web-flux")
    assert detail["factusol_document"]["by_ref"] is True
    assert detail["factusol_document"]["ref"] == "FLE-005789"
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
    assert "5-000026" in r.headers["content-disposition"]
    assert fake.calls[0] == ("F_PCL", "REFPCL='FLE-005789'")
    assert ("F_LPC", "CODLPC=26") in fake.calls


def test_pdf_pedido_web_sin_fpcl_aviso_controlado(http) -> None:
    """Web cuyo F_PCL aún no existe → 404 controlado `pedido_not_in_factusol`
    (la ficha lo enseña como aviso discreto, no como error rojo) que dice qué
    referencia se buscó y en qué ejercicio. Si el mismo nº Woo existe bajo
    OTRO prefijo (FLE-005789 cuando se buscó FLU-005789: prefijo de tienda
    sin configurar, el caso real), el aviso dice qué prefijo configurar y NO
    imprime ese documento (un homónimo de otra tienda comparte número)."""
    _set_ref_prefixes(http, {"fluxlasers": "FLE"})
    fake = FakeClient(_tables())
    with _patched(fake):
        r = _pdf(http, "o-web-nuevo")
    assert r.status_code == 404
    body = r.json()["detail"]
    assert body["code"] == "pedido_not_in_factusol"
    assert "aún no existe" in body["detail"]
    assert "FLE-005790" in body["detail"] and "ejercicio 2026" in body["detail"]
    assert body["ref"] == "FLE-005790" and body["candidates"] == []
    assert fake.calls == [
        ("F_PCL", "REFPCL='FLE-005790'"),
        ("F_PCL", "REFPCL LIKE '%-005790'"),
    ]

    # Prefijo sin configurar: se busca FLU-005789 (derivado de FLUXLA), no
    # está; sí existe FLE-005789 → el aviso lo dice, sin imprimirlo.
    _set_ref_prefixes(http, {"fluxlasers": ""})
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-web-flux")
        r = _pdf(http, "o-web-flux")
    assert detail["factusol_document"]["ref"] == "FLU-005789"
    assert r.status_code == 404
    body = r.json()["detail"]
    assert body["code"] == "pedido_not_in_factusol"
    assert body["ref"] == "FLU-005789"
    assert body["candidates"] == ["FLE-005789"]
    assert "FLU-005789" in body["detail"]
    assert "Sí existe FLE-005789" in body["detail"]
    assert "«FLE»" in body["detail"] and "Flux Lasers" in body["detail"]
    assert "Ajustes ERP" in body["detail"]
    assert not any(t == "F_LPC" for t, _ in fake.calls)   # no se imprimió nada

    # El sondeo es best-effort: si LIKE falla, el 404 de siempre (no un 502).
    class Flaky(FakeClient):
        def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
            if "LIKE" in filtro:
                raise FactusolError("timeout")
            return super().load_table(tabla, filtro=filtro, ejercicio=ejercicio)

    with _patched(Flaky(_tables())):
        r = _pdf(http, "o-web-flux")
    assert r.status_code == 404
    assert r.json()["detail"]["candidates"] == []
    assert "FLU-005789" in r.json()["detail"]["detail"]


def test_pdf_pedido_sin_origen_factusol_no_error(http) -> None:
    """Alta manual sin origen FACTUSOL: la ficha recibe `factusol_document =
    null` (botón deshabilitado, sin banner rojo) y el endpoint responde un
    404 controlado SIN consultar FACTUSOL."""
    fake = FakeClient(_tables())
    with _patched(fake):
        detail = _detail(http, "o-manual")
        r = _pdf(http, "o-manual")
    assert detail["factusol_document"] is None
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "pedido_not_in_factusol"
    assert "no hay PDF" in r.json()["detail"]["detail"]
    assert fake.calls == []


def test_prefijo_referencia_por_tienda_en_ajustes(http, session_factory) -> None:
    """El prefijo de referencia FACTUSOL se configura por tienda en Ajustes
    ERP (antes solo a mano en `metadata_json`): se guarda en mayúsculas, vacío
    = derivado, valor inválido → 400; el GET expone el de la cuenta (manda) y
    el derivado; y el guardarraíl web de la Fase 2 lo reconoce."""
    from app.erp.factusol_albaran import store_ref_prefixes  # noqa: PLC0415

    body = _set_ref_prefixes(http, {"fluxlasers": " fle ", "boprin": ""})
    assert body["factusol_ref_prefix_by_store"] == {"fluxlasers": "FLE"}
    stores = {s["slug"]: s for s in body["woocommerce_stores"]}
    assert stores["boprin"]["ref_prefix_metadata"] == "BOP"
    assert stores["boprin"]["derived_ref_prefix"] == "BOP"
    assert stores["fluxlasers"]["ref_prefix_metadata"] is None
    assert stores["fluxlasers"]["derived_ref_prefix"] == "FLU"

    r = http.get("/api/erp/settings", headers=auth_headers(http, "user"))
    assert r.json()["factusol_ref_prefix_by_store"] == {"fluxlasers": "FLE"}

    bad = http.patch("/api/erp/settings",
                     json={"factusol_ref_prefix_by_store": {"fluxlasers": "F-L"}},
                     headers=auth_headers(http, "admin"))
    assert bad.status_code == 400
    assert "fluxlasers" in bad.json()["detail"]

    with session_factory() as s:
        assert {"BOP", "FLE"} <= store_ref_prefixes(s)
