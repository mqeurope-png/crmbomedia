"""Cuadre «Trabajo en cola fallido sin revisar» y la puesta al día de Woo
que sabe esperar a su trabajo (progreso, última pasada, trabajo caducado)."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.cuadre.checks_colas as checks_colas
import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.cuadre import engine as cuadre_engine
from app.erp.cuadre.checks_colas import TRABAJO_COLA_FALLIDO, TrabajoFallido
from app.erp.cuadre.checks_colas import leer_fallidos as leer_fallidos_real
from app.erp.models.cuadre import CuadreFinding
from app.integrations.woocommerce import progreso
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


AGOSTO = TrabajoFallido(
    id="9f0c2a1e-0000-4000-8000-000000000001", cola="woocommerce:backfill",
    funcion="app.integrations.woocommerce.jobs.sync_orders_backfill",
    argumentos="'boprint', '2026-07-04'", fecha=datetime(2026, 8, 3, 4, 12, tzinfo=UTC),
    error="WooError: GET /orders → 400",
)


def _hallazgos(s: Session) -> list[CuadreFinding]:
    return list(s.scalars(select(CuadreFinding).where(
        CuadreFinding.check_id == TRABAJO_COLA_FALLIDO)))


def test_el_fallido_de_agosto_sale_en_el_cuadre_y_se_puede_revisar(factory, monkeypatch):
    monkeypatch.setattr(checks_colas, "leer_fallidos", lambda *a, **k: [AGOSTO])
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        assert h.estado == "abierto" and h.severidad == "media"
        assert h.entidad_tipo == "trabajo_cola" and h.entidad_id == AGOSTO.id
        d = json.loads(h.detalle_json)
        texto = json.dumps(d, ensure_ascii=False)
        assert "sync_orders_backfill" in texto and "woocommerce:backfill" in texto
        assert "03/08/2026" in texto and "WooError: GET /orders → 400" in texto
        assert "'boprint', '2026-07-04'" in texto
        cuadre_engine.revisar(s, h.id, motivo="Antiguo, ya importado a mano", user_id=None)
        s.commit()
    # Sigue en el registro de fallidos, pero ya revisado: no vuelve a avisar.
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        assert _hallazgos(s)[0].estado == "revisado"


def test_sin_redis_la_comprobacion_falla_y_no_da_por_resueltos_los_que_habia(factory,
                                                                             monkeypatch):
    monkeypatch.setattr(checks_colas, "leer_fallidos", lambda *a, **k: [AGOSTO])
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")

    def sin_redis(*a, **k):
        raise ConnectionError("Redis no responde")

    monkeypatch.setattr(checks_colas, "leer_fallidos", sin_redis)
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        assert _hallazgos(s)[0].estado == "abierto"     # no se resuelve por no poder mirar


def test_leer_fallidos_lee_todas_las_colas_los_mas_recientes_primero(monkeypatch):
    """Sin Redis de verdad: se sustituyen las piezas de RQ. Se leen los más
    nuevos de cada cola (sin limpiar el registro) y se mezclan por fecha."""
    import rq
    import rq.job
    import rq.registry

    colas = [SimpleNamespace(name="woocommerce:backfill"), SimpleNamespace(name="genei:shipments")]
    # zrevrange: ya en orden del más nuevo al más viejo.
    ids = {"rq:failed:woocommerce:backfill": [b"j1"], "rq:failed:genei:shipments": [b"j3", b"j2"]}
    trabajos = {
        "j1": SimpleNamespace(func_name="app.x.sync_orders_backfill", args=("boprint",),
                              kwargs={"since": "2026-07-04"}, ended_at=datetime(2026, 8, 3),
                              enqueued_at=None,
                              exc_info="Traceback...\n  File x\nWooError: GET /orders → 400\n"),
        "j2": None,                                           # datos caducados
        "j3": SimpleNamespace(func_name="app.y.fetch_label_job", args=(), kwargs={},
                              ended_at=None, enqueued_at=datetime(2026, 10, 7), exc_info=None),
    }
    pedidos: list[tuple] = []

    class _Conn:
        def zrevrange(self, key, start, end):
            pedidos.append((key, start, end))
            return ids[key]

    monkeypatch.setattr(rq.Queue, "all", classmethod(lambda cls, connection=None: colas))
    monkeypatch.setattr(rq.registry, "FailedJobRegistry",
                        lambda queue: SimpleNamespace(key=f"rq:failed:{queue.name}"))
    monkeypatch.setattr(rq.job.Job, "fetch_many",
                        classmethod(lambda cls, js, connection=None: [trabajos[j] for j in js]))
    out = leer_fallidos_real(conn=_Conn(), limite=2)
    assert all(end == 1 for _k, _s, end in pedidos)          # solo los 2 más nuevos por cola
    # Mezclados por fecha, el más nuevo primero, y con el tope total.
    assert [t.id for t in out] == ["j3", "j1"]
    j1 = out[1]
    assert j1.error == "WooError: GET /orders → 400"
    assert j1.argumentos == "'boprint', since='2026-07-04'"
    assert j1.fecha.tzinfo is not None
    todos = leer_fallidos_real(conn=_Conn())
    assert todos[-1].funcion.startswith("(datos")              # sin fecha, al final


# --- «Poner al día estados Woo…» ---------------------------------------------------------------


@pytest.fixture()
def http(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class _Job:
    def __init__(self, estado, *, resultado=None, meta=None, args=(True, None)):
        self._estado = estado
        self.result = resultado
        self.meta = meta or {}
        self.args = args
        self.ended_at = datetime(2026, 10, 7, 10, 0, tzinfo=UTC) if estado == "finished" else None

    def get_status(self, refresh=True):
        return self._estado

    def get_meta(self, refresh=True):
        return self.meta


def test_estado_con_progreso_y_trabajo_caducado(http, monkeypatch):
    import rq.job
    from rq.exceptions import NoSuchJobError

    trabajos = {"vivo": _Job("started", meta={"progreso": {
        "fase": "Pedidos sin estado, uno a uno", "hechos": 40, "total": 86}}),
        "hecho": _Job("finished", resultado={"ok": True})}

    def fetch(cls, job_id, connection=None):
        if job_id not in trabajos:
            raise NoSuchJobError(job_id)
        return trabajos[job_id]

    monkeypatch.setattr(rq.job.Job, "fetch", classmethod(fetch))
    h = auth_headers(http, "admin")
    vivo = http.get("/api/erp/seguimiento/reconcile-woo-status/vivo", headers=h).json()
    assert vivo["status"] == "pending"
    assert vivo["progress"] == {"fase": "Pedidos sin estado, uno a uno", "hechos": 40,
                                "total": 86}
    hecho = http.get("/api/erp/seguimiento/reconcile-woo-status/hecho", headers=h).json()
    assert hecho["status"] == "finished" and hecho["preview"] is True and hecho["ended_at"]
    ido = http.get("/api/erp/seguimiento/reconcile-woo-status/caducado", headers=h).json()
    assert ido == {"status": "missing"}


def test_la_pantalla_recupera_la_ultima_pasada(http, monkeypatch):
    import rq.job

    import app.integrations.woocommerce.jobs as woo_jobs

    h = auth_headers(http, "admin")
    monkeypatch.setattr(woo_jobs, "ultima_puesta_al_dia", lambda: None)
    assert http.get("/api/erp/seguimiento/reconcile-woo-last", headers=h).json() == \
        {"job_id": None}
    monkeypatch.setattr(woo_jobs, "ultima_puesta_al_dia", lambda: {
        "job_id": "hecho", "preview": True, "store": None,
        "enqueued_at": "2026-10-07T09:55:00+00:00"})
    monkeypatch.setattr(rq.job.Job, "fetch", classmethod(
        lambda cls, j, connection=None: _Job("finished", resultado={"ok": True, "scanned": 86})))
    u = http.get("/api/erp/seguimiento/reconcile-woo-last", headers=h).json()
    assert u["job_id"] == "hecho" and u["status"] == "finished"
    assert u["result"]["scanned"] == 86 and u["preview"] is True


def test_el_trabajo_informa_de_por_donde_va(monkeypatch):
    import app.integrations.woocommerce.jobs as woo_jobs

    vistos: list[tuple] = []
    meta: dict = {}
    job = SimpleNamespace(meta=meta, save_meta=lambda: vistos.append(dict(meta["progreso"])))
    monkeypatch.setattr("rq.get_current_job", lambda: job)

    def falso_reconcile(session, **kw):
        for i in (1, 2, 3):
            progreso.informar("Pedidos sin estado, uno a uno", i, 3)
        return {"ok": True}

    monkeypatch.setattr("app.integrations.woocommerce.reconcile.reconcile_open_order_statuses",
                        falso_reconcile)
    monkeypatch.setattr("app.integrations.woocommerce.missing.import_missing_paid_orders",
                        lambda session, **kw: {"faltan": 0})

    class _S:
        def __enter__(self):
            return SimpleNamespace(rollback=lambda: None)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(woo_jobs, "_session_factory", lambda: (lambda: _S()))
    r = woo_jobs.run_woo_reconcile(True, None)
    assert r["missing"] == {"faltan": 0}
    assert [(v["hechos"], v["total"]) for v in vistos[:3]] == [(1, 3), (2, 3), (3, 3)]
    assert vistos[-1]["fase"] == "Terminado"
    # Fuera del trabajo, informar no hace nada.
    progreso.informar("nada")


def test_al_encolar_recuerda_la_ultima_pasada(monkeypatch):
    import rq

    import app.integrations.woocommerce.jobs as woo_jobs

    guardado: dict = {}

    class _Redis:
        def set(self, k, v, ex=None):
            guardado.update(k=k, v=json.loads(v), ex=ex)

    class _Cola:
        def __init__(self, *a, **k):
            pass

        def enqueue(self, *a, **k):
            return SimpleNamespace(id="job-1")

    monkeypatch.setattr("app.workers.queues.redis_connection", lambda: _Redis())
    monkeypatch.setattr(rq, "Queue", _Cola)
    assert woo_jobs.enqueue_woo_reconcile(dry_run=True) == "job-1"
    assert guardado["k"] == woo_jobs.ULTIMA_PUESTA_AL_DIA_KEY
    assert guardado["v"]["job_id"] == "job-1" and guardado["v"]["preview"] is True
    assert guardado["ex"] >= woo_jobs.RECONCILE_RESULT_TTL
