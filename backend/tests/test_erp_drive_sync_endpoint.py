"""«Actualizar hoja de Drive» (`POST /api/erp/seguimiento/drive-sync`).

- Un fallo de la hoja se LOGUEA (antes era un 502 mudo: solo el cuerpo de la
  respuesta decía qué había pasado) y devuelve el motivo al botón.
- El modo antiguo (`drive_legacy_insert`) añade filas a la PRIMERA pestaña: si
  ahí está ahora la de la app, no escribe.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.base import Base
from app.db.session import get_session
from app.erp.drive_sheets import DriveSyncError
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings
from app.main import app
from tests._test_helpers import auth_headers, seed_test_users

URL = "/api/erp/seguimiento/drive-sync"
_FAKE_SA = {
    "type": "service_account", "project_id": "bohub-drive",
    "client_email": "bohub@bohub-drive.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
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
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator:
    from fastapi.testclient import TestClient

    def override():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _drive(http, session_factory, **series) -> None:
    r = http.patch("/api/erp/settings", json={
        "drive_service_account_json": json.dumps(_FAKE_SA),
        "drive_spreadsheet_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
    }, headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    if series:
        with session_factory() as s:
            cfg = s.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
            datos = json.loads(cfg.factusol_series_json or "{}")
            datos.update(series)
            cfg.factusol_series_json = json.dumps(datos)
            s.commit()


class _SinCerrojo:
    def __enter__(self):
        return True

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize(("dry_run", "modo"), [(True, "vista previa"), (False, "escritura")])
def test_un_fallo_de_la_hoja_se_loguea_y_llega_al_boton(
    session_factory, http, caplog, dry_run, modo,
) -> None:
    _drive(http, session_factory)
    motivo = "la pestaña «Hoja 1» no tiene el formato de la app"

    def falla(*_a, **_k):
        raise DriveSyncError(motivo)

    with patch("app.erp.drive_managed.push_managed_tabs", side_effect=falla), \
         patch("app.erp.drive_sheets.GoogleSheetsClient", return_value=MagicMock()), \
         patch("app.erp.seguimiento_sync_job.reconcile_lock", lambda: _SinCerrojo()), \
         caplog.at_level(logging.WARNING, logger="app.erp.api.seguimiento"):
        r = http.post(f"{URL}?dry_run={str(dry_run).lower()}",
                      headers=auth_headers(http, "pedidos"))
    assert r.status_code == 502
    assert r.json()["detail"] == {"code": "drive_sync_failed", "detail": motivo}
    # Y queda en el log del backend, con el motivo y en qué paso.
    registros = [rec for rec in caplog.records if rec.name == "app.erp.api.seguimiento"]
    assert len(registros) == 1
    assert registros[0].levelno == logging.WARNING
    assert f"drive-sync ({modo}) falló" in registros[0].getMessage()
    assert motivo in registros[0].getMessage()
    # Sin traza: la causa encadenada de un fallo de credenciales podría
    # arrastrar material de la clave.
    assert registros[0].exc_info is None


def test_modo_antiguo_no_escribe_si_la_primera_pestana_es_la_de_la_app(
    session_factory, http,
) -> None:
    _drive(http, session_factory, drive_legacy_insert=True)
    cliente = MagicMock()
    cliente.first_tab_title.return_value = "Seguimiento (app)"
    with patch("app.erp.drive_sheets.GoogleSheetsClient", return_value=cliente), \
         patch("app.erp.drive_sheets.sync_to_sheet",
               side_effect=AssertionError("no debe escribir")) as sync:
        r = http.post(URL, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 502, r.text
    assert "modo antiguo" in r.json()["detail"]["detail"]
    sync.assert_not_called()


def test_modo_antiguo_sigue_escribiendo_en_la_hoja_vieja(session_factory, http) -> None:
    _drive(http, session_factory, drive_legacy_insert=True)
    cliente = MagicMock()
    cliente.first_tab_title.return_value = "Pedidos Bomedia 2020-2026"
    with patch("app.erp.drive_sheets.GoogleSheetsClient", return_value=cliente), \
         patch("app.erp.drive_sheets.sync_to_sheet",
               return_value={"appended_rows": 0, "orders_to_review": 0}) as sync:
        r = http.post(URL, headers=auth_headers(http, "pedidos"))
    assert r.status_code == 200, r.text
    sync.assert_called_once()
