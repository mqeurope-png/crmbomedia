"""Cuadre «Trabajo en cola fallido sin revisar» y la puesta al día de Woo
que sabe esperar a su trabajo (progreso, última pasada, trabajo caducado)."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
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
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: ([AGOSTO], {AGOSTO.cola: 1}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        # Baja: el último fallo del grupo es de hace meses, así que ya no está
        # pasando y lo que queda es limpiar el registro.
        assert h.estado == "abierto" and h.severidad == "baja"
        assert h.entidad_tipo == "trabajo_cola"
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
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: ([AGOSTO], {AGOSTO.cola: 1}))
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
            return ids[key][start : end + 1]

        def zcard(self, key):
            return len(ids[key])

    monkeypatch.setattr(rq.Queue, "all", classmethod(lambda cls, connection=None: colas))
    class _Registro:
        def __init__(self, queue):
            self.key = f"rq:failed:{queue.name}"
            self.name = queue.name

        def __len__(self):
            # `len()` de rq pasa por `cleanup()` → `zremrangebyscore`: BORRA
            # las entradas caducadas. En una comprobación de solo lectura eso
            # es un borrado silencioso, así que aquí se prohíbe.
            raise AssertionError("len(registro) escribe en Redis: usa ZCARD")

    monkeypatch.setattr(rq.registry, "FailedJobRegistry", _Registro)
    monkeypatch.setattr(rq.job.Job, "fetch_many",
                        classmethod(lambda cls, js, connection=None: [trabajos[j] for j in js]))
    out, totales = leer_fallidos_real(conn=_Conn(), max_por_cola=2)
    assert all(start == 0 for _k, start, _e in pedidos)       # los más nuevos primero
    # Cada cola, de lo más nuevo a lo más viejo, y cuántos hay en su registro.
    assert [t.id for t in out] == ["j1", "j3", "j2"]
    assert totales == {"woocommerce:backfill": 1, "genei:shipments": 2}
    j1 = out[0]
    assert j1.error == "WooError: GET /orders → 400"
    assert j1.argumentos == "'boprint', since='2026-07-04'"
    assert j1.fecha.tzinfo is not None
    # El que caducó se cuenta igual, diciendo que no tiene datos.
    assert out[2].funcion.startswith("(datos")


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


# --- agrupado por función y tipo de error -----------------------------------


def test_mil_seiscientos_ochenta_y_siete_fallos_iguales_son_un_solo_hallazgo(
    factory, monkeypatch
):
    """Con 149.603 fallidos el panel listaba 300 líneas de lo mismo y no se
    podía leer. Un grupo por cola + función + tipo de error, con su recuento."""
    ahora = datetime.now(UTC)
    lote = [
        TrabajoFallido(
            id=f"j{i}", cola="brevo:webhook_process",
            funcion="app.integrations.brevo.webhooks.process_brevo_webhook_batch",
            argumentos=f"[{{'event': 'opened', 'id': {i}}}], 'default'",
            fecha=ahora - timedelta(minutes=i),
            # El id cambia en cada uno: si no se normaliza, 1.687 grupos.
            error=f"IntegrityError: Duplicate entry 'brevo-default-{i}:soft_bounce' "
                  "for key 'uq_activity_event_system_account_external_id'",
        )
        for i in range(1687)
    ]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"brevo:webhook_process": 1687}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)                     # UNA línea, no 1.687
        d = json.loads(h.detalle_json)
        # Sigue pasando y es un volumen de tormenta: alta.
        assert h.severidad == "alta"
        assert d["datos"]["cuantos"] == 1687
        assert d["datos"]["sigue_pasando"] is True
        assert "<ref>" in d["datos"]["tipo_error"]      # el id, normalizado
        # Pero el nombre de la clave única NO: dos claves distintas son
        # dos problemas distintos.
        assert "uq_activity_event_system_account_external_id" in d["datos"]["tipo_error"]
        assert "1687" in d["etiqueta"]
        # Y el ejemplo lleva argumentos, que es lo que sirve para repetirlo.
        assert "'event': 'opened'" in d["datos"]["ejemplo_argumentos"]


def test_dos_errores_distintos_de_la_misma_funcion_son_dos_grupos(factory, monkeypatch):
    ahora = datetime.now(UTC)
    lote = [
        TrabajoFallido(id="a1", cola="factusol:writes",
                       funcion="app.x.create_quote_job", argumentos="1, 2",
                       fecha=ahora, error="TypeError: takes 2 positional arguments"),
        TrabajoFallido(id="b1", cola="factusol:writes",
                       funcion="app.x.create_quote_job", argumentos="3, 4",
                       fecha=ahora, error="FactusolError: cliente no existe"),
    ]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"factusol:writes": 2}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        hallazgos = _hallazgos(s)
    # El de arity y el de negocio son problemas distintos: no se mezclan.
    assert len(hallazgos) == 2
    errores = {json.loads(h.detalle_json)["datos"]["tipo_error"] for h in hallazgos}
    assert any("TypeError" in e for e in errores)
    assert any("FactusolError" in e for e in errores)


def test_el_recuento_dice_cuando_es_un_al_menos(factory, monkeypatch):
    """Esto corre dentro de la petición web, así que hay tope. Un tope que no
    se dice se lee como «ya está todo»."""
    ahora = datetime.now(UTC)
    lote = [TrabajoFallido(id=f"j{i}", cola="brevo:push_contact",
                           funcion="app.x.push_contact_job", argumentos="'c'",
                           fecha=ahora, error="400 from brevo/default")
            for i in range(5)]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"brevo:push_contact": 146876}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        d = json.loads(h.detalle_json)
    assert "al menos 5" in d["detalle"]
    assert d["datos"]["recuento_recortado"] is True
    assert d["datos"]["en_el_registro"] == 146876


def test_un_fallo_nuevo_del_mismo_dia_no_reabre_lo_ya_revisado(factory, monkeypatch):
    """Con el recuento en la huella, cada fallo nuevo reabriría un descuadre
    ya revisado. Con el DÍA del último fallo, como mucho una vez al día."""
    ahora = datetime.now(UTC)

    def _lote(cuantos: int):
        return [TrabajoFallido(id=f"j{i}", cola="gmail:process_history",
                               funcion="app.x.process_history_job", argumentos="'u'",
                               fecha=ahora, error="HttpError 404")
                for i in range(cuantos)]

    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (_lote(3), {"gmail:process_history": 3}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        cuadre_engine.revisar(s, h.id, motivo="Arreglado, pendiente de limpiar",
                              user_id=None)
        s.commit()

    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (_lote(9), {"gmail:process_history": 9}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        assert h.estado == "revisado"
        # Pero el recuento de la pantalla sí está al día.
        assert json.loads(h.detalle_json)["datos"]["cuantos"] == 9


def test_la_memoria_de_los_fallidos_se_estima_por_muestreo(monkeypatch):
    """Medir 149.603 trabajos uno a uno costaría más que el dato."""
    import rq
    import rq.registry

    colas = [SimpleNamespace(name="brevo:push_contact")]

    class _Registro:
        def __init__(self, queue):
            self.key = f"rq:failed:{queue.name}"

        def __len__(self):
            raise AssertionError("len(registro) escribe en Redis: usa ZCARD")

    class _Conn:
        def zcard(self, _key):
            return 146_876

        def zrevrange(self, _key, _start, _end):
            return [f"j{i}".encode() for i in range(25)]

        def memory_usage(self, _clave):
            return 1_024

        def info(self, _seccion):
            return {"used_memory": 500 * 1024 * 1024}

    monkeypatch.setattr(rq.Queue, "all", classmethod(lambda cls, connection=None: colas))
    monkeypatch.setattr(rq.registry, "FailedJobRegistry", _Registro)
    out = checks_colas.memoria_de_colas(conn=_Conn())
    assert out["fallidos_total"] == 146_876
    assert out["bytes_por_trabajo_medio"] == 1_024
    assert out["bytes_estimados"] == 146_876 * 1_024      # ~143 MB
    assert out["redis_usada_bytes"] == 500 * 1024 * 1024
    assert out["muestra"] == 25


def test_un_id_numerico_suelto_no_parte_el_grupo(factory, monkeypatch):
    """Un `KeyError: 12345` por pedido serían miles de grupos (y miles de
    filas en `cuadre_findings`). Pero un 400 y un 404 sí son distintos."""
    from app.erp.cuadre.checks_colas import tipo_de_error

    assert tipo_de_error("KeyError: 12345") == tipo_de_error("KeyError: 998877")
    assert tipo_de_error("WooError: GET /orders/12345 → 404") == \
        tipo_de_error("WooError: GET /orders/998877 → 404")
    # Tres cifras o menos se quedan: ahí están los estados y las aridades.
    assert tipo_de_error("400 from brevo") != tipo_de_error("404 from brevo")
    assert tipo_de_error("takes 2 arguments") != tipo_de_error("takes 3 arguments")

    ahora = datetime.now(UTC)
    lote = [TrabajoFallido(id=f"j{i}", cola="woocommerce:backfill",
                           funcion="app.x.sync_order", argumentos=f"{10000 + i}",
                           fecha=ahora, error=f"KeyError: {10000 + i}")
            for i in range(40)]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"woocommerce:backfill": 40}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        hallazgos = _hallazgos(s)
    assert len(hallazgos) == 1                       # un grupo, no cuarenta
    assert json.loads(hallazgos[0].detalle_json)["datos"]["cuantos"] == 40


def test_con_muchisimos_grupos_se_sacan_los_gordos_y_se_dice_el_resto(
    factory, monkeypatch
):
    """Cada grupo es una fila en `cuadre_findings`: no se meten miles en
    silencio."""
    ahora = datetime.now(UTC)
    lote = [TrabajoFallido(id=f"j{i}", cola="woocommerce:backfill",
                           funcion=f"app.x.job_{i}", argumentos="",
                           fecha=ahora, error="BoomError: distinto cada vez")
            for i in range(checks_colas.MAX_GRUPOS + 7)]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"woocommerce:backfill": len(lote)}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        hallazgos = _hallazgos(s)
    # Los 50 más gordos + UNA línea que dice cuántos quedan fuera.
    assert len(hallazgos) == checks_colas.MAX_GRUPOS + 1
    resto = [h for h in hallazgos if h.entidad_id == "otros-grupos-pequenos"]
    assert len(resto) == 1
    assert json.loads(resto[0].detalle_json)["datos"]["grupos"] == 7
    assert resto[0].severidad == "baja"


def test_una_funcion_sin_argumentos_tambien_tiene_ejemplo(factory, monkeypatch):
    """La pista dice «mira el error del ejemplo»: si la función no lleva
    argumentos, el ejemplo no puede faltar."""
    ahora = datetime.now(UTC)
    lote = [TrabajoFallido(id="j1", cola="genei:shipments",
                           funcion="app.x.fetch_label_job", argumentos="",
                           fecha=ahora, error="GeneiError: sin etiqueta")]
    monkeypatch.setattr(checks_colas, "leer_fallidos",
                        lambda *a, **k: (lote, {"genei:shipments": 1}))
    with factory() as s:
        cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
        (h,) = _hallazgos(s)
        d = json.loads(h.detalle_json)
    assert d["datos"]["ejemplo_error"] == "GeneiError: sin etiqueta"
