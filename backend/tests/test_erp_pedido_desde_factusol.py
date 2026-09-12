"""ERP · Fase 1 — pedido de BoHub desde un presupuesto / pedido de cliente de
FACTUSOL (solo lectura allí), desde la ficha de empresa, y proforma → pedido.

Cliente FACTUSOL simulado (F_PRE/F_LPS, F_PCL/F_LPC, F_FPA). Nada se escribe en
FACTUSOL; el pedido entra en la bandeja, la Cola PEDIDOS y el seguimiento como
uno más, y el filtro «solo processing» de #387 (ingesta Woo) ni lo ve.
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

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.models import Order, OrderLine, OrderSource, OrderStatusHistory
from app.erp.orders_from_factusol import (
    AlreadyImported,
    CustomerUnlinked,
    DocumentNotFound,
    create_order_from_factusol_document,
    external_id_for,
    order_number_for,
    preview_factusol_document,
)
from app.integrations.factusol.quotes import convert_quote_to_order
from app.integrations.woocommerce.cleanup import cleanup_non_processing_orders
from app.main import app
from app.models.crm import Company
from tests._test_helpers import auth_headers, seed_test_users


class FakeClient:
    """Tablas en memoria con el `filtro` de igualdad del lector (gotcha nº1:
    columna inexistente → [] en silencio). Registra escrituras: debe quedar
    vacío siempre (Fase 1 no escribe en FACTUSOL)."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self.tables = tables
        self.default_ejercicio = "2026"
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((tabla, filtro))
        rows = list(self.tables.get(tabla, []))
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        column, _, raw = predicate.partition("=")
        column, wanted = column.strip(), raw.strip().strip("'")
        if rows and column not in rows[0]:
            return []
        return [r for r in rows if str(r.get(column)) == wanted]

    def write_record(self, tabla, data, *, ejercicio=None):  # pragma: no cover
        self.writes.append((tabla, dict(data)))
        return {"respuesta": "OK"}


def _pre(codpre: int, *, clipre="55555", total=121.0, ref="Cabezal + SAT") -> dict:
    return {
        "CODPRE": codpre, "TIPPRE": "1", "CLIPRE": clipre, "CNOPRE": "Acme SL",
        "FECPRE": "2026-08-01T00:00:00", "ESTPRE": 0, "REFPRE": ref,
        "NET1PRE": 100.0, "PIVA1PRE": 21.0, "IIVA1PRE": 21.0, "TOTPRE": total,
        "FOPPRE": "002", "CPAPRE": "724",
    }


def _lps(codpre: int, pos: int, *, art="", desc="Línea", cant=1.0, precio=10.0,
         dto=0.0, iva=0.0) -> dict:
    return {
        "TIPLPS": "1", "CODLPS": codpre, "POSLPS": pos, "ARTLPS": art,
        "DESLPS": desc, "CANLPS": cant, "DT1LPS": dto, "PRELPS": precio,
        "TOTLPS": round(cant * precio * (1 - dto / 100), 2), "IVALPS": iva,
    }


def _pcl(codigo: int, *, serie="5", clipcl="55555", total=60.5, ref="") -> dict:
    return {
        "CODPCL": codigo, "TIPPCL": serie, "CLIPCL": clipcl, "CNOPCL": "Acme SL",
        "FECPCL": "2026-09-02T00:00:00", "ESTPCL": 0, "REFPCL": ref,
        "TOTPCL": total, "NET1PCL": 50.0, "FOPPCL": "011",
    }


def _lpc(codigo: int, pos: int, *, serie="5", art="", desc="Línea", cant=1.0,
         precio=10.0, dto=0.0) -> dict:
    return {
        "TIPLPC": serie, "CODLPC": codigo, "POSLPC": pos, "ARTLPC": art,
        "DESLPC": desc, "CANLPC": cant, "DT1LPC": dto, "PRELPC": precio,
        "TOTLPC": round(cant * precio * (1 - dto / 100), 2),
    }


FPA = [{"CODFPA": "002", "DESFPA": "Transferencia"},
       {"CODFPA": "011", "DESFPA": "Recibo domiciliado"}]


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "F_PRE": [_pre(574), _pre(575, clipre="99999")],
        "F_LPS": [
            _lps(574, 1, art="MBO", desc="Cabezal MBO 250", cant=1, precio=250),
            _lps(574, 2, art="SAT", desc="Hora SAT", cant=2, precio=60, dto=50),
            _lps(575, 1, desc="Sin vincular", precio=10),
        ],
        "F_PCL": [_pcl(123), _pcl(123, serie="1", total=999.0)],
        "F_LPC": [
            _lpc(123, 1, art="CDR80WPT", desc="CD TQ 700 MB", cant=100, precio=0.5),
            _lpc(123, 2, desc="Portes", precio=10.5),
            _lpc(123, 1, serie="1", desc="Del homónimo de la serie 1", precio=999),
        ],
        "F_FPA": FPA,
    }


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
        seed.add(Company(id="acme", name="Acme SL", factusol_company_id="55555"))
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


def _post_from_factusol(http, body: dict, role: str = "pedidos"):
    return http.post("/api/erp/orders/from-factusol", json=body,
                     headers=auth_headers(http, role))


def _bandeja(http, **params) -> list[dict]:
    r = http.get("/api/erp/orders", params=params, headers=auth_headers(http, "user"))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _numeros(items: list[dict]) -> set[str]:
    return {it["order_number"] for it in items}


# --- helpers puros --------------------------------------------------------------


def test_numeros_y_external_id() -> None:
    assert external_id_for("presupuestos", 1, 574) == "574"
    assert external_id_for("pedidos", 5, 123) == "5-000123"
    assert order_number_for("presupuestos", 1, 574) == "PRO-000574"
    assert order_number_for("pedidos", 5, 123) == "PCL-5-000123"


# --- 1) desde un presupuesto ---------------------------------------------------


def test_crear_pedido_desde_presupuesto_sin_iva(session_factory, http) -> None:
    """Tarea C: una proforma intracomunitaria / de exportación (`PIVA1PRE=0`
    con base > 0, hecha en el escritorio o por BoHub) deja las líneas del
    pedido al 0 % — la cabecera manda sobre el `IVALPS` de línea, que es un
    código — y el importe final es la base."""
    tables = _tables()
    tables["F_PRE"].append({**_pre(576, total=100.0), "PIVA1PRE": 0.0, "IIVA1PRE": 0.0})
    tables["F_LPS"].append(_lps(576, 1, art="MBO", desc="Cabezal", cant=1, precio=100))
    fake = FakeClient(tables)
    with _patched(fake):
        r = _post_from_factusol(http, {"doc_type": "presupuestos", "serie": 1, "codigo": 576})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lines"][0]["tax_rate"] == 0
    assert body["total_amount"] == 100.0
    assert fake.writes == []


def test_crear_pedido_desde_presupuesto_factusol(session_factory, http) -> None:
    fake = FakeClient(_tables())
    with _patched(fake):
        r = _post_from_factusol(http, {"doc_type": "presupuestos", "serie": 1, "codigo": 574})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["external_source"] == "factusol_proforma"
    assert body["order_number"] == "PRO-000574"
    assert body["company_id"] == "acme" and body["company_name"] == "Acme SL"
    assert body["placed_at"].startswith("2026-08-01")
    # Líneas con artículo, descripción, cantidad, precio y descuento aplicado.
    lines = body["lines"]
    assert [ln["product_sku"] for ln in lines] == ["MBO", "SAT"]
    assert lines[0]["description"] == "Cabezal MBO 250"
    assert lines[0]["quantity"] == 1 and lines[0]["unit_price"] == 250
    assert lines[1]["quantity"] == 2 and lines[1]["unit_price"] == 60
    assert lines[1]["line_total"] == 60.0      # 2 × 60 con 50 % de dto
    assert lines[1]["notes"] == "dto. 50%"
    assert lines[0]["tax_rate"] == 21          # IVALPS=0 → IVA por defecto
    # Importe FINAL = TOTPRE (con IVA) del presupuesto, no la suma de líneas.
    assert body["total_amount"] == 121.0
    # Origen + forma de pago (informativa, Fase 2) + referencia en packing.
    src = body["packing"]["factusol_source"]
    assert src["doc_type"] == "presupuestos" and src["numero"] == "1-000574"
    assert src["forma_pago"] == "002" and src["forma_pago_nombre"] == "Transferencia"
    assert src["referencia"] == "Cabezal + SAT" and src["cliente_codigo"] == "55555"
    assert "presupuesto FACTUSOL 1-000574" in body["notes"]
    # Historial con el origen, y NADA escrito en FACTUSOL.
    assert any("presupuesto FACTUSOL 1-000574" in (h["reason"] or "")
               for h in body["status_history"])
    assert fake.writes == []
    with session_factory() as s:
        o = s.get(Order, body["id"])
        assert o.external_source == OrderSource.FACTUSOL_PROFORMA
        assert o.external_id == "574"
    # Entra en la bandeja como uno más y en el seguimiento con su nº de proforma.
    assert _numeros(_bandeja(http)) == {"PRO-000574"}
    seg = http.get("/api/erp/seguimiento", headers=auth_headers(http, "user")).json()
    assert [row["proforma"] for row in seg["items"]] == ["574"]


# --- 2) desde un pedido de cliente ----------------------------------------------


def test_crear_pedido_desde_pedido_cliente_factusol(session_factory, http) -> None:
    fake = FakeClient(_tables())
    with _patched(fake):
        r = _post_from_factusol(http, {"doc_type": "pedidos", "serie": 5, "codigo": 123})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["external_source"] == "factusol_pedido"
    assert body["order_number"] == "PCL-5-000123"
    assert body["company_id"] == "acme"
    # Join compuesto (serie, código): NO se cuelan las líneas del 1-000123.
    assert [ln["description"] for ln in body["lines"]] == ["CD TQ 700 MB", "Portes"]
    assert body["lines"][0]["product_sku"] == "CDR80WPT"
    assert body["lines"][0]["quantity"] == 100 and body["lines"][0]["unit_price"] == 0.5
    assert body["total_amount"] == 60.5
    src = body["packing"]["factusol_source"]
    assert src["numero"] == "5-000123"
    assert src["forma_pago"] == "011" and src["forma_pago_nombre"] == "Recibo domiciliado"
    assert fake.writes == []
    with session_factory() as s:
        o = s.get(Order, body["id"])
        assert o.external_source == OrderSource.FACTUSOL_PEDIDO
        assert o.external_id == "5-000123"
    # Bandeja + Cola PEDIDOS (pendiente de revisión, como cualquier alta).
    assert _numeros(_bandeja(http)) == {"PCL-5-000123"}
    cola = http.get("/api/erp/orders/pending-approval",
                    headers=auth_headers(http, "user")).json()
    assert _numeros(cola["items"]) == {"PCL-5-000123"}


# --- 3) el filtro «solo processing» de #387 no toca estos pedidos ----------------


def test_pedido_manual_origen_no_woo_no_lo_filtra_processing(session_factory, http) -> None:
    fake = FakeClient(_tables())
    with _patched(fake), patch(
        "app.integrations.woocommerce.mapper.should_create_order",
    ) as gate:
        assert _post_from_factusol(
            http, {"doc_type": "pedidos", "serie": 5, "codigo": 123},
        ).status_code == 201
        assert _post_from_factusol(
            http, {"doc_type": "presupuestos", "serie": 1, "codigo": 574},
        ).status_code == 201
        # Y un alta manual normal, también sin pasar por Woo.
        manual = http.post("/api/erp/orders", json={
            "company_id": "acme",
            "lines": [{"description": "Reparación", "quantity": 1, "unit_price": 40}],
        }, headers=auth_headers(http, "pedidos"))
        assert manual.status_code == 201, manual.text
    # La puerta de Woo (#387) ni se consulta: vive en la ingesta de WooCommerce.
    gate.assert_not_called()
    items = _bandeja(http)
    assert _numeros(items) == {"PCL-5-000123", "PRO-000574", "MANUAL-000001"}
    assert {it["external_source"] for it in items} == {
        "factusol_pedido", "factusol_proforma", "manual",
    }
    assert all(it["woo_status"] is None for it in items) if "woo_status" in items[0] else True
    # La limpieza de #387 solo mira pedidos Woo: ninguno de estos es candidato.
    with session_factory() as s:
        res = cleanup_non_processing_orders(s, dry_run=True)
        assert res["candidates"] == [] and res["protected"] == [] and res["sin_estado"] == 0


# --- 4) desde la ficha de empresa: la empresa viene precargada ------------------


def test_nuevo_pedido_desde_ficha_empresa_precarga_empresa(session_factory, http) -> None:
    """El botón de la ficha abre el alta con `?company_id=`; el alta manda esa
    empresa. (El formulario se prueba en jest; aquí, que el pedido queda con la
    empresa y, si además parte de un documento FACTUSOL, con ese origen.)"""
    r = http.post("/api/erp/orders", json={
        "company_id": "acme",
        "lines": [{"description": "Montaje", "quantity": 1, "unit_price": 30}],
        "factusol_source": {"doc_type": "presupuestos", "serie": 1, "codigo": 574,
                            "referencia": "Cabezal + SAT", "forma_pago": "002",
                            "forma_pago_nombre": "Transferencia"},
    }, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["company_id"] == "acme" and body["company_name"] == "Acme SL"
    assert body["external_source"] == "factusol_proforma"
    assert body["order_number"] == "PRO-000574"
    assert body["packing"]["factusol_source"]["forma_pago_nombre"] == "Transferencia"
    assert any("presupuesto FACTUSOL 1-000574 (alta manual)" in (h["reason"] or "")
               for h in body["status_history"])
    # Dedup: el mismo documento no se importa dos veces (ni a mano ni directo).
    again = http.post("/api/erp/orders", json={
        "company_id": "acme",
        "lines": [{"description": "x", "quantity": 1, "unit_price": 1}],
        "factusol_source": {"doc_type": "presupuestos", "serie": 1, "codigo": 574},
    }, headers=auth_headers(http, "pedidos"))
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_imported"
    assert again.json()["detail"]["order_number"] == "PRO-000574"
    with _patched(FakeClient(_tables())):
        direct = _post_from_factusol(http, {"doc_type": "presupuestos", "serie": 1, "codigo": 574})
    assert direct.status_code == 409 and direct.json()["detail"]["code"] == "already_imported"


# --- 5) proforma → pedido (Fase C, ahora idempotente y con origen) --------------


def test_convertir_proforma_en_pedido(session_factory) -> None:
    fake = FakeClient(_tables())
    with session_factory() as s:
        first = convert_quote_to_order(fake, s, "574", ejercicio="2026", actor_user_id=None)
        assert first["already_existed"] is False
        assert first["order_number"] == "PRO-000574" and first["lines"] == 2
        o = s.get(Order, first["order_id"])
        assert o.external_source == OrderSource.FACTUSOL_PROFORMA
        assert o.external_id == "574" and o.company_id == "acme"
        assert float(o.total_amount) == 121.0      # TOTPRE (con IVA)
        lines = list(s.scalars(select(OrderLine).where(OrderLine.order_id == o.id)
                               .order_by(OrderLine.position)))
        assert [ln.product_codart for ln in lines] == ["MBO", "SAT"]
        assert any("proforma FACTUSOL 574" in (h.reason or "") for h in s.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == o.id)))
        # Convertir otra vez NO duplica: devuelve el mismo pedido.
        second = convert_quote_to_order(fake, s, "574", ejercicio="2026")
        assert second["already_existed"] is True
        assert second["order_id"] == first["order_id"]
        assert s.scalar(select(Order).where(Order.external_id == "574")) is not None
        assert len(list(s.scalars(select(Order)))) == 1
        # Nada escrito en FACTUSOL (ni F_PCL ni albarán: Fase 2).
        assert fake.writes == []


# --- previsualización, errores y permisos ---------------------------------------


def test_preview_lee_el_documento_sin_crear_nada(session_factory, http) -> None:
    fake = FakeClient(_tables())
    with _patched(fake):
        r = http.get("/api/erp/orders/from-factusol/preview",
                     params={"doc_type": "pedidos", "serie": 5, "codigo": 123},
                     headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["numero"] == "5-000123" and p["order_number"] == "PCL-5-000123"
    assert p["company_linked"] is True and p["company_name"] == "Acme SL"
    assert p["forma_pago_nombre"] == "Recibo domiciliado"
    assert p["already_imported"] is None
    assert [ln["description"] for ln in p["lines"]] == ["CD TQ 700 MB", "Portes"]
    assert p["lines"][0]["discount_pct"] == 0.0
    assert _bandeja(http) == [] and fake.writes == []
    # Solo lectura no puede ni previsualizar.
    assert http.get("/api/erp/orders/from-factusol/preview",
                    params={"doc_type": "pedidos", "serie": 5, "codigo": 123},
                    headers=auth_headers(http, "user")).status_code in (401, 403)


def test_cliente_sin_vincular_avisa_y_permite_elegir_empresa(session_factory, http) -> None:
    fake = FakeClient(_tables())
    with _patched(fake):
        r = _post_from_factusol(http, {"doc_type": "presupuestos", "serie": 1, "codigo": 575})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "factusol_customer_unlinked"
        assert detail["codcli"] == "99999" and detail["cliente_nombre"] == "Acme SL"
        # Con la empresa elegida a mano, se crea.
        ok = _post_from_factusol(http, {"doc_type": "presupuestos", "serie": 1,
                                        "codigo": 575, "company_id": "acme"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["order_number"] == "PRO-000575"


def test_documento_inexistente_y_tipo_no_soportado(session_factory, http) -> None:
    with _patched(FakeClient(_tables())):
        r = _post_from_factusol(http, {"doc_type": "pedidos", "serie": 5, "codigo": 999})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "factusol_document_not_found"
        bad = _post_from_factusol(http, {"doc_type": "albaranes", "serie": 5, "codigo": 1})
        assert bad.status_code == 422
        forbidden = _post_from_factusol(
            http, {"doc_type": "pedidos", "serie": 5, "codigo": 123}, role="user",
        )
        assert forbidden.status_code in (401, 403)


def test_funciones_puras_excepciones(session_factory) -> None:
    fake = FakeClient(_tables())
    with session_factory() as s:
        with pytest.raises(DocumentNotFound):
            preview_factusol_document(s, fake, doc_type="pedidos", serie=9, codigo=1,
                                      ejercicio="2026")
        with pytest.raises(CustomerUnlinked):
            create_order_from_factusol_document(
                s, fake, doc_type="presupuestos", serie=1, codigo=575, ejercicio="2026",
            )
        create_order_from_factusol_document(
            s, fake, doc_type="presupuestos", serie=1, codigo=574, ejercicio="2026",
        )
        s.commit()
        with pytest.raises(AlreadyImported):
            create_order_from_factusol_document(
                s, fake, doc_type="presupuestos", serie=1, codigo=574, ejercicio="2026",
            )
