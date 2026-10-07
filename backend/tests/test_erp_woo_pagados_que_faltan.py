"""ERP · WooCommerce — pedidos PAGADOS en la tienda que nunca llegaron a BoHub.

Caso 07/10/2026: el 99976 de boprint se creó `on-hold`, el plugin de TSM lo
marcó pagado sin disparar el webhook y no entró. Aquí:

- la importación de lo que falta va por el MISMO camino que el webhook (evento
  `backfill:{id}` + `import_order_from_event` → `import_woo_order`): empresa,
  líneas, método de pago y Cola SAT como un pedido normal; idempotente;
- un `on-hold` no se importa; uno que ya está en BoHub no se duplica ni se pisa;
- el Cuadre lo avisa antes de importarlo y lo da por resuelto después;
- el repaso periódico respeta su interruptor y su intervalo;
- «Poner al día estados Woo…» incluye lo importado en su resumen.

Ninguna llamada sale a una tienda real: el cliente es un doble.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.crypto import encrypt
from app.db.base import Base
from app.db.session import get_session
from app.erp.cuadre import engine as cuadre_engine
from app.erp.models import (
    CuadreFinding,
    ErpSettings,
    IntegrationEvent,
    IntegrationEventStatus,
    Order,
    OrderLine,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
)
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
from app.integrations.woocommerce import jobs as woo_jobs
from app.integrations.woocommerce import missing_job
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.mapper import import_woo_order
from app.integrations.woocommerce.missing import import_missing_paid_orders
from app.main import app
from app.models.crm import Company, ExternalSystem, SyncLog
from app.models.integration_settings import (
    IntegrationAccount,
    IntegrationMode,
    IntegrationStatus,
)
from tests._test_helpers import auth_headers, seed_test_users

AHORA = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    # Los jobs abren su propia sesión: que usen esta misma BD en memoria.
    with patch.object(woo_jobs, "_session_factory", return_value=f):
        yield f
    Base.metadata.drop_all(engine)


def _store(s: Session, slug: str = "boprint") -> IntegrationAccount:
    a = IntegrationAccount(
        system=ExternalSystem.WOOCOMMERCE, account_id=slug, display_name=slug.capitalize(),
        enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
        base_url=f"https://{slug}.example",
        consumer_key_encrypted=encrypt("ck"), consumer_secret_encrypted=encrypt("cs"),
        credential_status="configured",
    )
    s.add(a)
    s.commit()
    return a


def _pedido(woo_id: int, *, status: str = "processing", modificado: str = "2026-10-07T07:02:11",
            empresa: str = "Gráficas Norte SL", nif: str = "B12345678") -> dict[str, Any]:
    return {
        "id": woo_id, "number": str(woo_id), "status": status,
        "total": "129.00", "currency": "EUR",
        "date_created": "2026-10-06T18:40:00", "date_created_gmt": "2026-10-06T16:40:00",
        "date_paid": "2026-10-07T09:02:11", "date_paid_gmt": "2026-10-07T07:02:11",
        "date_modified_gmt": modificado,
        "payment_method": "bacs", "payment_method_title": "Transferencia bancaria",
        "billing": {
            "first_name": "Laura", "last_name": "Pérez", "email": "laura@ejemplo.com",
            "company": empresa, "vat": nif, "country": "ES", "city": "Bilbao",
            "postcode": "48001", "address_1": "Calle Falsa 1", "phone": "600111222",
        },
        "line_items": [
            {"id": 1, "product_id": 42, "sku": "SKU-MBO-3050", "quantity": 1,
             "total": "129.00", "name": "MBO 3050 80W"},
        ],
        "meta_data": [],
    }


class Tienda:
    """Doble de `WooHTTPClient`: responde al listado con los pedidos que tiene
    en los estados pedidos (o con todos si `ignora_filtro`) y registra las
    llamadas."""

    def __init__(self, store, pedidos: list[dict], llamadas: list[dict], *,
                 caida: bool = False, ignora_filtro: bool = False) -> None:
        self.store = store
        self.pedidos = pedidos
        self.llamadas = llamadas
        self.caida = caida
        self.ignora_filtro = ignora_filtro

    def list_orders(self, *, status="processing", since=None, per_page=50, page=1,
                    modified_after=None, dates_are_gmt=False):
        self.llamadas.append({"tienda": self.store.account_id, "status": status,
                              "since": since, "modified_after": modified_after,
                              "gmt": dates_are_gmt, "page": page})
        if self.caida:
            raise WooError("tienda caída", status=503)
        if page > 1:
            return []
        estados = set(str(status).split(","))
        return [dict(p) for p in self.pedidos if self.ignora_filtro or p["status"] in estados]


def _tiendas(pedidos: dict[str, list[dict]], llamadas: list[dict], **kw):
    def factory(store):
        return Tienda(store, pedidos.get(store.account_id, []), llamadas,
                      caida=store.account_id in kw.get("caidas", ()),
                      ignora_filtro=kw.get("ignora_filtro", False))
    return factory


def _importar(factory, pedidos, *, dry_run=False, llamadas=None, **kw) -> dict:
    with factory() as s:
        return import_missing_paid_orders(
            s, dry_run=dry_run, client_factory=_tiendas(pedidos, llamadas if llamadas is not None
                                                        else [], **kw),
            now=kw.pop("now", AHORA), days=90,
        )


# --- importar lo que falta -----------------------------------------------------------


def test_pagado_que_falta_entra_como_por_el_webhook_y_no_se_duplica(factory):
    with factory() as s:
        _store(s)
    llamadas: list[dict] = []
    r = _importar(factory, {"boprint": [_pedido(99976)]}, llamadas=llamadas)

    assert (r["faltan"], r["importados"]) == (1, 1)
    item = r["items"][0]
    assert item["resultado"] == "importado" and item["order_number"] == "BOPRIN-99976"
    assert item["cliente"] == "Gráficas Norte SL (Laura Pérez)"
    assert item["enlace"] == "https://boprint.example/wp-admin/post.php?post=99976&action=edit"
    # UNA consulta por tienda: estados pagados juntos, por fecha, en UTC.
    assert len(llamadas) == 1
    assert llamadas[0]["status"] == "completed,processing,refunded"
    assert llamadas[0]["since"] == "2026-07-09T10:00:00" and llamadas[0]["gmt"] is True
    with factory() as s:
        o = s.scalar(select(Order))
        assert (o.external_source, o.external_id) == (OrderSource.WOOCOMMERCE, "99976")
        assert s.get(Company, o.company_id).name == "Gráficas Norte SL"          # empresa
        lineas = s.scalars(select(OrderLine).where(OrderLine.order_id == o.id)).all()
        assert [(ln.product_sku, float(ln.quantity)) for ln in lineas] == [("SKU-MBO-3050", 1.0)]
        assert (o.payment_method, o.payment_method_title) == ("bacs", "Transferencia bancaria")
        assert o.payment_status == PaymentStatus.PAID
        assert o.preparation_status == PreparationStatus.IN_QUEUE                # Cola SAT
        ev = s.scalar(select(IntegrationEvent))
        assert ev.external_event_id == "backfill:99976"
        assert ev.status == IntegrationEventStatus.PROCESSED
        log = s.scalar(select(SyncLog).where(SyncLog.operation == "import_missing"))
        assert log.status == "success" and log.records_processed == 1
        assert "BOPRIN-99976" in log.metadata_json

    # Otra pasada: ya está en BoHub → nada que importar, ningún duplicado.
    r2 = _importar(factory, {"boprint": [_pedido(99976)]})
    assert (r2["faltan"], r2["importados"]) == (0, 0)
    with factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 1


def test_queda_igual_que_si_hubiera_llegado_el_webhook(factory):
    """Mismo pedido: uno por la importación de lo que falta (boprint) y otro
    por el núcleo del webhook (fluxlasers, otra tienda): mismos datos."""
    with factory() as s:
        _store(s, "boprint")
        flux = _store(s, "fluxlasers")
        payload = _pedido(5790, nif="B99999999")
        payload["_store_slug"] = "fluxlasers"
        import_woo_order(s, store=flux, woo_order=payload)
        s.commit()
    _importar(factory, {"boprint": [_pedido(5790, nif="B99999999")]})

    def resumen(o: Order, s: Session) -> tuple:
        lineas = s.scalars(select(OrderLine).where(OrderLine.order_id == o.id)).all()
        return (s.get(Company, o.company_id).name if o.company_id else None,
                [(ln.product_sku, float(ln.quantity), float(ln.unit_price)) for ln in lineas],
                o.payment_method, o.payment_method_title, o.payment_status,
                o.preparation_status, float(o.total_amount or 0), o.woo_status)

    with factory() as s:
        por_numero = {o.order_number: o for o in s.scalars(select(Order))}
        assert set(por_numero) == {"BOPRIN-5790", "FLUXLA-5790"}
        assert resumen(por_numero["BOPRIN-5790"], s) == resumen(por_numero["FLUXLA-5790"], s)


def test_un_on_hold_no_se_importa(factory):
    with factory() as s:
        _store(s)
    # Aunque la tienda ignore el filtro de estado y lo devuelva.
    r = _importar(factory, {"boprint": [_pedido(99977, status="on-hold"),
                                        _pedido(99978, status="pending")]},
                  ignora_filtro=True)
    assert (r["faltan"], r["importados"]) == (0, 0)
    with factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 0
        assert s.scalar(select(func.count(IntegrationEvent.id))) == 0


def test_el_que_ya_esta_en_bohub_no_se_duplica_ni_se_pisa(factory):
    with factory() as s:
        store = _store(s)
        s.add(Order(external_source=OrderSource.WOOCOMMERCE, external_id="99976",
                    store_id=store.id, order_number="BOPRIN-99976", total_amount=1,
                    notes="tocado a mano"))
        # Uno dado de alta a mano con el mismo nº de pedido (sin id de la tienda).
        s.add(Order(order_number="BOPRIN-99979", total_amount=2))
        s.commit()
    importados: list[int] = []

    def espia(session, store, wo):
        importados.append(wo["id"])
        return {}

    with factory() as s:
        r = import_missing_paid_orders(
            s, dry_run=False, days=90, now=AHORA, importer=espia,
            client_factory=_tiendas({"boprint": [_pedido(99976), _pedido(99979)]}, []),
        )
    assert r["faltan"] == 0 and importados == []
    with factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 2
        o = s.scalar(select(Order).where(Order.external_id == "99976"))
        assert (float(o.total_amount), o.notes) == (1.0, "tocado a mano")


def test_la_vista_previa_lista_sin_escribir_nada(factory):
    with factory() as s:
        _store(s)
    r = _importar(factory, {"boprint": [_pedido(99976)]}, dry_run=True)
    assert r["faltan"] == 1 and r["importados"] == 0
    assert r["items"][0]["resultado"] == "a_importar"
    assert r["items"][0]["pagado_el"] == "2026-10-07T07:02:11+00:00"
    with factory() as s:
        assert s.scalar(select(func.count(Order.id))) == 0
        assert s.scalar(select(func.count(IntegrationEvent.id))) == 0
        assert s.scalar(select(func.count(SyncLog.id))) == 0


def test_el_recien_modificado_espera_a_la_siguiente_pasada(factory):
    """Pagado hace 2 min: su webhook puede estar en camino; no se importa ya."""
    with factory() as s:
        _store(s)
    r = _importar(factory, {"boprint": [_pedido(99976, modificado="2026-10-07T09:58:00")]})
    assert r["faltan"] == 0


def test_una_tienda_caida_no_para_las_demas(factory):
    with factory() as s:
        _store(s, "boprint")
        _store(s, "fluxlasers")
    r = _importar(factory, {"fluxlasers": [_pedido(5790)]}, caidas={"boprint"})
    assert r["importados"] == 1
    assert [e["store"] for e in r["errores"]] == ["boprint"]
    with factory() as s:
        estados = dict(s.execute(select(SyncLog.account_id, SyncLog.status)).all())
        assert estados == {"boprint": "failed", "fluxlasers": "success"}


def test_si_la_importacion_falla_se_informa_y_el_resto_sigue(factory):
    with factory() as s:
        _store(s)

    def importador(session, store, wo):
        if wo["id"] == 1:
            raise RuntimeError("FACTUSOL no responde")
        return woo_jobs.import_order_from_event(
            woo_jobs.upsert_backfill_event(session, store, wo["id"], wo))

    with factory() as s:
        r = import_missing_paid_orders(
            s, dry_run=False, days=90, now=AHORA, importer=importador,
            client_factory=_tiendas({"boprint": [_pedido(1, nif="B1"), _pedido(2, nif="B2")]},
                                    []),
        )
    assert r["importados"] == 1
    assert {i["numero"]: i["resultado"] for i in r["items"]} == {"1": "error", "2": "importado"}
    assert "FACTUSOL no responde" in r["errores"][0]["error"]


# --- Cuadre: «Pedido pagado en WooCommerce que no está en BoHub» --------------------------


def _cuadre(factory, monkeypatch, pedidos, **kw) -> None:
    monkeypatch.setattr("app.integrations.woocommerce.client.WooHTTPClient",
                        _tiendas(pedidos, [], **kw))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="woocommerce", origen="manual", ahora=AHORA)


def _hallazgos(factory) -> dict[str, CuadreFinding]:
    with factory() as s:
        return {f.entidad_id: f for f in s.scalars(select(CuadreFinding).where(
            CuadreFinding.check_id == "pedido_woo_pagado_sin_bohub"))}


def test_el_cuadre_lo_avisa_antes_y_lo_resuelve_despues_de_importarlo(factory, monkeypatch):
    with factory() as s:
        _store(s)
    tienda = {"boprint": [_pedido(99976)]}
    _cuadre(factory, monkeypatch, tienda)
    f = _hallazgos(factory)["boprint:99976"]
    assert (f.estado, f.severidad, f.entidad_tipo) == ("abierto", "alta", "pedido_woo")
    det = json.loads(f.detalle_json)
    assert det["etiqueta"] == "Boprint #99976"
    assert "Gráficas Norte SL (Laura Pérez)" in det["detalle"] and "129.00 EUR" in det["detalle"]
    assert "07/10/2026 07:02 UTC" in det["detalle"]
    assert det["enlace"] == "https://boprint.example/wp-admin/post.php?post=99976&action=edit"
    assert det["arreglo_enlace"] == "/erp/seguimiento"
    assert det["datos"]["importe"] == "129.00" and det["ambito"] == "boprint"

    _importar(factory, tienda)                       # el repaso / «Poner al día…»
    _cuadre(factory, monkeypatch, tienda)
    assert _hallazgos(factory)["boprint:99976"].estado == "resuelto"


def test_el_cuadre_avisa_aunque_la_importacion_falle(factory, monkeypatch):
    with factory() as s:
        _store(s)
    tienda = {"boprint": [_pedido(99976)]}
    with factory() as s:
        import_missing_paid_orders(
            s, dry_run=False, days=90, now=AHORA, client_factory=_tiendas(tienda, []),
            importer=lambda *_a: {"ok": False, "error": "boom"},
        )
    _cuadre(factory, monkeypatch, tienda)
    assert _hallazgos(factory)["boprint:99976"].estado == "abierto"


def test_una_tienda_que_no_responde_no_da_por_resueltos_sus_avisos(factory, monkeypatch):
    with factory() as s:
        _store(s, "boprint")
        _store(s, "fluxlasers")
    _cuadre(factory, monkeypatch, {"boprint": [_pedido(99976)], "fluxlasers": [_pedido(5790)]})
    assert {k: f.estado for k, f in _hallazgos(factory).items()} == {
        "boprint:99976": "abierto", "fluxlasers:5790": "abierto"}
    # boprint no responde y fluxlasers ya no tiene el suyo pendiente.
    _cuadre(factory, monkeypatch, {"fluxlasers": []}, caidas={"boprint"})
    assert {k: f.estado for k, f in _hallazgos(factory).items()} == {
        "boprint:99976": "abierto", "fluxlasers:5790": "resuelto"}


def test_si_ninguna_tienda_responde_la_comprobacion_falla(factory, monkeypatch):
    with factory() as s:
        _store(s)
    _cuadre(factory, monkeypatch, {}, caidas={"boprint"})
    with factory() as s:
        run = s.scalars(select(cuadre_engine.CuadreRun)).first()
        resumen = json.loads(run.resumen_json)
    assert "Ninguna tienda ha respondido" in resumen["pedido_woo_pagado_sin_bohub"]["error"]


# --- repaso periódico --------------------------------------------------------------------


def _ajustes(factory, **valores) -> None:
    with factory() as s:
        cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID) or ErpSettings(
            id=ERP_SETTINGS_SINGLETON_ID)
        blob = json.loads(cfg.factusol_series_json or "{}")
        blob.update(valores)
        cfg.factusol_series_json = json.dumps(blob)
        s.add(cfg)
        s.commit()


def _repaso(factory, pedidos, llamadas, ahora=AHORA):
    with factory() as s:
        return missing_job.run_periodic_check(
            s, client_factory=_tiendas(pedidos, llamadas), now=ahora)


def test_el_repaso_respeta_el_interruptor(factory):
    with factory() as s:
        _store(s)
    _ajustes(factory, woo_missing_check_enabled=False)
    llamadas: list[dict] = []
    assert _repaso(factory, {"boprint": [_pedido(99976)]}, llamadas) is None
    assert llamadas == []
    _ajustes(factory, woo_missing_check_enabled=True)
    r = _repaso(factory, {"boprint": [_pedido(99976)]}, llamadas)
    assert r["importados"] == 1 and len(llamadas) == 1


def test_el_repaso_respeta_su_intervalo_y_solo_mira_lo_modificado(factory):
    with factory() as s:
        _store(s)
    llamadas: list[dict] = []
    assert _repaso(factory, {}, llamadas) is not None
    # Primera pasada de la tienda: solo el último día (los 90, «Poner al día…»).
    assert llamadas[-1]["modified_after"] == "2026-10-06T10:00:00"
    # Antes de medio intervalo (60 min por defecto) no repite.
    assert _repaso(factory, {}, llamadas, AHORA + timedelta(minutes=20)) is None
    assert len(llamadas) == 1
    # Pasada la hora: mira desde la anterior menos el margen.
    assert _repaso(factory, {}, llamadas, AHORA + timedelta(minutes=60)) is not None
    assert llamadas[-1]["modified_after"] == "2026-10-07T09:45:00"
    with factory() as s:
        assert [t for (t,) in s.execute(select(SyncLog.triggered_by))] == ["cron", "cron"]


def test_el_intervalo_sale_de_configuracion_y_arma_el_tic(factory, monkeypatch):
    engine = factory.kw["bind"]
    monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
    assert missing_job.interval() == timedelta(minutes=60)
    _ajustes(factory, woo_missing_check_interval_minutes=30)
    assert missing_job.interval() == timedelta(minutes=30)
    _ajustes(factory, woo_missing_check_interval_minutes=3)       # por debajo del mínimo
    assert missing_job.interval() == timedelta(minutes=15)

    captado: dict[str, Any] = {}

    class FakeRedis:
        def __init__(self) -> None:
            self.claves: set[str] = set()

        def set(self, key, _v, nx=False, ex=None):
            if nx and key in self.claves:
                return False
            self.claves.add(key)
            captado["ttl"] = ex
            return True

        def delete(self, key):
            self.claves.discard(key)

    class FakeQueue:
        def __init__(self, name, connection=None, default_timeout=None):
            captado["cola"] = name

        def enqueue_in(self, delay, fn):
            captado["delay"], captado["fn"] = delay, fn
            captado["n"] = captado.get("n", 0) + 1

    conn = FakeRedis()
    with patch("app.workers.queues.redis_connection", return_value=conn), \
            patch("rq.Queue", FakeQueue):
        missing_job.schedule_check()
        missing_job.schedule_check()                 # latido vivo → no arma otro
    assert captado["cola"] == "woocommerce:backfill"   # worker-web
    assert captado["delay"] == timedelta(minutes=15)
    assert captado["fn"] is missing_job._check_runner
    assert captado["n"] == 1


# --- «Poner al día estados Woo…» -----------------------------------------------------


def test_poner_al_dia_incluye_lo_importado(factory, monkeypatch):
    vistos: list[bool] = []

    def falso(session, *, dry_run, store_account_id):
        vistos.append(dry_run)
        return {"faltan": 1, "importados": 0 if dry_run else 1, "items": []}

    monkeypatch.setattr("app.integrations.woocommerce.missing.import_missing_paid_orders", falso)
    previa = woo_jobs.run_woo_reconcile(dry_run=True)
    hecho = woo_jobs.run_woo_reconcile(dry_run=False)
    assert vistos == [True, False]
    assert previa["missing"]["importados"] == 0 and hecho["missing"]["importados"] == 1
    assert previa["refreshed_total"] == 0 and "to_cancel" in previa


def test_si_la_importacion_revienta_la_puesta_al_dia_sigue(factory, monkeypatch):
    def roto(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.integrations.woocommerce.missing.import_missing_paid_orders", roto)
    r = woo_jobs.run_woo_reconcile(dry_run=True)
    assert r["ok"] is True and "boom" in r["missing"]["error"]


# --- Configuración ERP y cliente -----------------------------------------------------


@pytest.fixture()
def http(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_ajustes_del_repaso_en_configuracion_erp(http):
    h = auth_headers(http, "admin")
    cfg = http.get("/api/erp/settings", headers=h).json()
    assert (cfg["woo_missing_check_enabled"], cfg["woo_missing_check_interval_minutes"],
            cfg["woo_missing_days"]) == (True, 60, 90)
    r = http.patch("/api/erp/settings", headers=h, json={
        "woo_missing_check_enabled": False, "woo_missing_check_interval_minutes": 120,
        "woo_missing_days": 30,
    })
    assert r.status_code == 200, r.text
    assert (r.json()["woo_missing_check_enabled"], r.json()["woo_missing_check_interval_minutes"],
            r.json()["woo_missing_days"]) == (False, 120, 30)
    r = http.patch("/api/erp/settings", headers=h,
                   json={"woo_missing_check_interval_minutes": 5})
    assert r.status_code == 422


def test_el_cliente_filtra_por_modificacion_en_utc(factory):
    vistos: list[httpx.Request] = []

    def responde(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json=[])

    with factory() as s:
        client = WooHTTPClient(_store(s), transport=httpx.MockTransport(responde))
    client.list_orders(status="completed,processing", since="2026-07-09T10:00:00",
                       modified_after="2026-10-07 09:45:00", dates_are_gmt=True, per_page=100)
    q = vistos[0].url.params
    assert q["status"] == "completed,processing"
    assert q["after"] == "2026-07-09T10:00:00"
    assert q["modified_after"] == "2026-10-07T09:45:00"
    assert q["dates_are_gmt"] == "true"
