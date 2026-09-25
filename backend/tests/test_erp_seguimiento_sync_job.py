"""Reconcile automático del espejo en worker-sync (Fase 2): interruptor + intervalo."""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.erp import seguimiento_sync_job as job
from app.erp.models import ErpSettings
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


def _config(s, **series: Any) -> None:
    cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        s.add(cfg)
    cfg.factusol_series_json = json.dumps(series)
    s.commit()


def test_apagado_por_defecto_y_intervalo_por_defecto(factory):
    with factory() as s:
        assert job.reconcile_config(s) == (False, 10)


def test_interruptor_e_intervalo_con_minimo(factory):
    with factory() as s:
        _config(s, seguimiento_reconcile_enabled=True,
                seguimiento_reconcile_interval_minutes=2)
        assert job.reconcile_config(s) == (True, job.MIN_INTERVAL_MINUTES)


def test_apagado_no_hace_nada(factory, monkeypatch):
    llamado = []
    monkeypatch.setattr("app.erp.drive_managed.push_managed_tabs",
                        lambda *a, **k: llamado.append(1))
    with factory() as s:
        assert job.run_reconcile(s) is None
    assert llamado == []


def test_encendido_sin_drive_configurado_no_hace_nada(factory):
    with factory() as s:
        _config(s, seguimiento_reconcile_enabled=True)
        assert job.run_reconcile(s) is None


def test_encendido_corre_el_mismo_reconcile_que_el_boton(factory, monkeypatch):
    llamadas: list[dict[str, Any]] = []

    def fake_push(session, sheets, rows, *, completados=None, dry_run=False):
        llamadas.append({"rows": rows, "completados": completados, "dry_run": dry_run})
        return {"rows": 0, "manuales": 0, "espejo": {}}

    monkeypatch.setattr("app.erp.drive_managed.push_managed_tabs", fake_push)
    monkeypatch.setattr("app.erp.drive_sheets.drive_config", lambda cfg: ({"x": 1}, "sheet"))
    monkeypatch.setattr("app.erp.drive_sheets.GoogleSheetsClient", lambda info, sid: object())
    monkeypatch.setattr("app.erp.api.seguimiento.drive_live_rows", lambda s: ["vivo"])
    monkeypatch.setattr("app.erp.api.seguimiento.drive_completados_rows", lambda s: ["comp"])

    class _SinRedis:
        def __enter__(self):
            return True

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(job, "reconcile_lock", lambda: _SinRedis())
    with factory() as s:
        _config(s, seguimiento_reconcile_enabled=True)
        res = job.run_reconcile(s)
    assert res is not None
    assert llamadas == [{"rows": ["vivo"], "completados": ["comp"], "dry_run": False}]


def test_con_otra_sincronizacion_en_curso_se_salta(factory, monkeypatch):
    monkeypatch.setattr("app.erp.drive_managed.push_managed_tabs",
                        lambda *a, **k: pytest.fail("no debería correr"))
    monkeypatch.setattr("app.erp.drive_sheets.drive_config", lambda cfg: ({"x": 1}, "sheet"))

    class _Ocupado:
        def __enter__(self):
            return False

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(job, "reconcile_lock", lambda: _Ocupado())
    with factory() as s:
        _config(s, seguimiento_reconcile_enabled=True)
        assert job.run_reconcile(s) is None
