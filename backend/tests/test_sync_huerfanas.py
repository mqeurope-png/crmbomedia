"""Ejecuciones de sincronización «en curso» que ya no lo están (05/10/2026).

Un `sync_logs` que se queda en pending/running porque su worker murió no puede
bloquear nada para siempre:

- se decide mirando RQ (el job no existe, terminó sin cerrar la fila o está
  «started» en un worker que ya no late), nunca suponiendo;
- al arrancar un worker se cierran como fallidas las huérfanas de SUS colas;
- el RUNNING de un target de Brevo caduca con su cerrojo.

Sin Redis real: `Job.fetch` y `Worker.find_by_key` se sustituyen por dobles.
"""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from rq.exceptions import NoSuchJobError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.models.brevo import BrevoSyncTarget, TargetRunStatus
from app.models.crm import ExternalSystem, SyncLog, SyncStatus
from app.workers import huerfanas
from app.workers.worker import BoHubWorker

AHORA = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


@pytest.fixture()
def s() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    Base.metadata.drop_all(engine)


class FakeRQ:
    """Jobs y workers de RQ en memoria."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[str, str | None]] = {}      # id → (estado, worker)
        self.workers: dict[str, datetime | None] = {}           # nombre → último latido

    def instalar(self, monkeypatch) -> None:
        from rq.job import Job
        from rq.worker import Worker

        def fetch(job_id: str, connection: Any = None) -> Any:
            if job_id not in self.jobs:
                raise NoSuchJobError(job_id)
            estado, worker = self.jobs[job_id]
            return SimpleNamespace(get_status=lambda refresh=False: estado, worker_name=worker)

        def find_by_key(key: str, connection: Any = None, **_k: Any) -> Any:
            nombre = key.removeprefix(Worker.redis_worker_namespace_prefix)
            if nombre not in self.workers:
                return None
            return SimpleNamespace(last_heartbeat=self.workers[nombre])

        monkeypatch.setattr(Job, "fetch", staticmethod(fetch))
        monkeypatch.setattr(Worker, "find_by_key", staticmethod(find_by_key))


@pytest.fixture()
def rq(monkeypatch) -> FakeRQ:
    fake = FakeRQ()
    fake.instalar(monkeypatch)
    return fake


def _log(s: Session, *, status: str, job_id: str | None, system=ExternalSystem.AGILECRM,
         operation: str = "sync_contacts", account: str = "es",
         creada: datetime = AHORA - timedelta(days=30), payload: dict | None = None) -> SyncLog:
    log = SyncLog(system=system, account_id=account, operation=operation, status=status,
                  job_id=job_id, created_at=creada,
                  started_at=creada if status == "running" else None,
                  metadata_json=json.dumps(payload) if payload else None)
    s.add(log)
    s.commit()
    return log


# --- ¿viva o huérfana? -------------------------------------------------------------


def test_ejecucion_viva_segun_rq(s, rq):
    rq.jobs = {
        "encolado": ("queued", None),
        "programado": ("scheduled", None),
        "terminado": ("finished", None),
        "fallido": ("failed", None),
        "en_vivo": ("started", "w-vivo"),
        "en_muerto": ("started", "w-muerto"),
        "sin_latido": ("started", "w-callado"),
    }
    rq.workers = {"w-vivo": AHORA - timedelta(seconds=40),
                  "w-callado": AHORA - timedelta(minutes=20)}
    viva = {
        jid: huerfanas.ejecucion_viva(_log(s, status="running", job_id=jid), None, AHORA)
        for jid in [*rq.jobs, "no_existe"]
    }
    assert viva == {
        "encolado": True, "programado": True, "en_vivo": True,
        "terminado": False, "fallido": False, "en_muerto": False, "sin_latido": False,
        "no_existe": False,
    }


def test_sin_job_id_se_respeta_un_rato(s, rq):
    reciente = _log(s, status="pending", job_id=None, creada=AHORA - timedelta(minutes=1))
    vieja = _log(s, status="pending", job_id=None, creada=AHORA - timedelta(hours=2))
    assert huerfanas.ejecucion_viva(reciente, None, AHORA) is True
    assert huerfanas.ejecucion_viva(vieja, None, AHORA) is False


# --- cierre de huérfanas -----------------------------------------------------------


def test_cierra_solo_las_huerfanas_de_las_colas_indicadas(s, rq):
    rq.jobs = {"vivo": ("queued", None), "muerto": ("started", "w-muerto")}
    viva = _log(s, status="pending", job_id="vivo")
    huerfana = _log(s, status="running", job_id="muerto", account="uk")
    perdida = _log(s, status="pending", job_id="borrado-de-redis", account="fr")
    de_otra_cola = _log(s, status="running", job_id="borrado-2", system=ExternalSystem.BREVO)
    terminada = _log(s, status="success", job_id="borrado-3", account="de")

    n = huerfanas.cerrar_huerfanas(s, conn=None, colas={"agilecrm:sync_contacts"},
                                   motivo="prueba", ahora=AHORA)

    assert n == 2
    assert {log.id: log.status for log in (viva, huerfana, perdida, de_otra_cola, terminada)} == {
        viva.id: "pending", huerfana.id: "failed", perdida.id: "failed",
        de_otra_cola.id: "running", terminada.id: "success",
    }
    assert "Ejecución interrumpida" in huerfana.error_summary
    assert "«running»" in huerfana.error_summary and "prueba" in huerfana.error_summary
    assert huerfana.finished_at.replace(tzinfo=UTC) == AHORA


def test_sin_redis_no_cierra_nada(s, monkeypatch):
    from rq.job import Job

    def caido(*_a, **_k):
        raise ConnectionError("redis caído")

    monkeypatch.setattr(Job, "fetch", staticmethod(caido))
    log = _log(s, status="running", job_id="x")
    assert huerfanas.cerrar_huerfanas(s, conn=None, motivo="prueba", ahora=AHORA) == 0
    assert log.status == "running"


def test_al_cerrar_un_push_target_huerfano_se_libera_el_target(s, rq):
    target = BrevoSyncTarget(name="T", segment_id="seg", brevo_account_id="bv",
                             brevo_list_id="1", last_run_status=TargetRunStatus.RUNNING)
    s.add(target)
    s.commit()
    _log(s, status="running", job_id="muerto", system=ExternalSystem.BREVO,
         operation="push_target", account="bv", payload={"target_id": target.id})
    huerfanas.cerrar_huerfanas(s, conn=None, motivo="prueba", ahora=AHORA)
    assert target.last_run_status == TargetRunStatus.ERROR
    assert "interrumpida" in target.last_run_stats_json


def test_el_worker_cierra_las_huerfanas_de_sus_colas_al_arrancar(monkeypatch):
    from rq.worker import Worker

    llamadas: list[tuple] = []
    monkeypatch.setattr(Worker, "bootstrap", lambda self, *a, **k: None)
    monkeypatch.setattr("app.workers.worker.cerrar_huerfanas_al_arrancar",
                        lambda colas, conn, nombre: llamadas.append((colas, conn, nombre)))
    worker = object.__new__(BoHubWorker)
    worker.name = "w-nuevo"
    worker.connection = "redis"
    monkeypatch.setattr(BoHubWorker, "queue_names",
                        lambda self: ["agilecrm:sync_contacts", "agilecrm:periodic_read"])
    worker.bootstrap()
    assert llamadas == [(["agilecrm:sync_contacts", "agilecrm:periodic_read"], "redis", "w-nuevo")]


def test_un_fallo_al_cerrar_no_impide_arrancar(monkeypatch):
    from app.workers import worker as worker_mod

    def roto(*_a, **_k):
        raise RuntimeError("bd caída")

    monkeypatch.setattr("app.workers.huerfanas.cerrar_huerfanas", roto)
    assert worker_mod.cerrar_huerfanas_al_arrancar(["x:y"], None, "w") == 0


# --- caducidad del «en curso» ------------------------------------------------------


def test_minutos_de_caducidad_configurables(monkeypatch):
    monkeypatch.delenv("SYNC_INFLIGHT_MAX_MINUTES", raising=False)
    assert huerfanas.inflight_max_minutes() == 60
    monkeypatch.setenv("SYNC_INFLIGHT_MAX_MINUTES", "180")
    assert huerfanas.inflight_max_minutes() == 180
    monkeypatch.setenv("SYNC_INFLIGHT_MAX_MINUTES", "nada")
    assert huerfanas.inflight_max_minutes() == 60


def test_running_de_un_target_de_brevo_caduca_con_su_cerrojo():
    from app.integrations.brevo.sync_targets import (
        TARGET_LOCK_TTL_SECONDS,
        target_en_curso,
    )

    def target(estado, hace):
        return SimpleNamespace(last_run_status=estado, updated_at=AHORA - hace)

    assert target_en_curso(target(TargetRunStatus.RUNNING, timedelta(minutes=5)), AHORA)
    viejo = timedelta(seconds=TARGET_LOCK_TTL_SECONDS + 60)
    assert not target_en_curso(target(TargetRunStatus.RUNNING, viejo), AHORA)
    assert not target_en_curso(target(TargetRunStatus.ERROR, timedelta(0)), AHORA)


def test_status_de_sincronizacion():
    assert set(huerfanas.INFLIGHT_STATUSES) == {SyncStatus.PENDING.value, SyncStatus.RUNNING.value}
