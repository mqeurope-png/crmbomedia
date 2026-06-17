"""Sprint Backup. Tests para `/api/admin/backups` + el runner.

Mockeamos el bash script para no necesitar mysqldump/gpg/rclone en CI
— el contrato real es la línea `STATS|...`, y eso lo verifica la
unit test del parser. Los endpoints HTTP solo exigen que la row de
`backups` se transicione correctamente.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.backups.service import (
    _extract_stats,
    _parse_stats_line,
    create_backup_row,
    run_backup,
)
from app.db.session import get_session
from app.main import app
from app.models.crm import Backup, BackupStatus, BackupTrigger, Base
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def stack() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    """Engine en memoria compartido por el TestClient y por
    `run_backup` (que abre su propia Session vía `get_engine()`).
    Parche con monkeypatch en lugar de override de FastAPI para que
    el runner del worker vea las mismas filas que el cliente HTTP."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as seed:
        seed_test_users(seed)

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with patch("app.backups.service.get_engine", return_value=engine):
        with TestClient(app) as client:
            yield client, session_factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Unit: parser de STATS|...
# ---------------------------------------------------------------------------


def test_parse_stats_line_success() -> None:
    parsed = _parse_stats_line(
        "STATS|status=success|filename=backup_X.tar.gz.gpg"
        "|filepath=/var/backups/crmbo/backup_X.tar.gz.gpg"
        "|size_bytes=12345|drive_url=https://drive.google.com/abc"
    )
    assert parsed["status"] == "success"
    assert parsed["filename"] == "backup_X.tar.gz.gpg"
    assert parsed["size_bytes"] == "12345"
    assert parsed["drive_url"] == "https://drive.google.com/abc"


def test_parse_stats_line_failed_no_drive() -> None:
    parsed = _parse_stats_line("STATS|status=failed|error=mysqldump muerto")
    assert parsed == {"status": "failed", "error": "mysqldump muerto"}


def test_parse_stats_line_not_stats() -> None:
    assert _parse_stats_line("[backup-crmbo 2026-06-17T20:00Z] 1/8 ...") == {}


def test_extract_stats_picks_last() -> None:
    """Si el script imprime varias líneas STATS| (no debería, pero…),
    nos quedamos con la última — refleja el estado final del run."""
    stdout = (
        "[log] paso 1\n"
        "STATS|status=running|filename=tmp\n"
        "[log] paso 2\n"
        "STATS|status=success|filename=final.tar.gz.gpg|size_bytes=99|drive_url=\n"
    )
    parsed = _extract_stats(stdout)
    assert parsed["status"] == "success"
    assert parsed["filename"] == "final.tar.gz.gpg"


# ---------------------------------------------------------------------------
# Integration: run_backup con subprocess mockeado
# ---------------------------------------------------------------------------


def _seed_running_backup(
    session_factory: sessionmaker, *, triggered_by: BackupTrigger | str
) -> str:
    with session_factory() as session:
        row = create_backup_row(
            session, triggered_by=triggered_by, user_id=None
        )
        session.commit()
        return row.id


def test_run_backup_success(
    stack: tuple[TestClient, sessionmaker], tmp_path: Path
) -> None:
    _client, session_factory = stack
    backup_id = _seed_running_backup(
        session_factory, triggered_by=BackupTrigger.MANUAL
    )

    fake_stdout = (
        "[backup-crmbo] 1/8 mysqldump\n"
        "[backup-crmbo] 8/8 done\n"
        "STATS|status=success|filename=backup_TS.tar.gz.gpg"
        "|filepath=/var/backups/crmbo/backup_TS.tar.gz.gpg"
        "|size_bytes=8421376|drive_url=https://drive.google.com/x\n"
    )
    mock_result = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
    # Forzamos a `_resolve_script_path` a devolver un path EXISTENTE
    # — el contenido no importa porque subprocess.run está mockeado.
    fake_script = tmp_path / "fake_backup.sh"
    fake_script.write_text("#!/bin/sh\necho fake\n")
    with patch(
        "app.backups.service._resolve_script_path", return_value=fake_script
    ), patch("app.backups.service.subprocess.run", return_value=mock_result):
        result = run_backup(backup_id)

    assert result["status"] == "success"
    assert result["filename"] == "backup_TS.tar.gz.gpg"
    assert result["size_bytes"] == 8421376
    assert result["drive_url"] == "https://drive.google.com/x"

    with session_factory() as session:
        row = session.get(Backup, backup_id)
        assert row is not None
        assert row.status == BackupStatus.SUCCESS.value
        assert row.size_bytes == 8421376
        assert row.finished_at is not None
        assert row.drive_url == "https://drive.google.com/x"


def test_run_backup_failed_returncode(
    stack: tuple[TestClient, sessionmaker], tmp_path: Path
) -> None:
    _client, session_factory = stack
    backup_id = _seed_running_backup(
        session_factory, triggered_by=BackupTrigger.CRON
    )

    mock_result = MagicMock(
        returncode=1,
        stdout="STATS|status=failed|error=mysqldump muerto\n",
        stderr="ERROR mysqldump killed\n",
    )
    fake_script = tmp_path / "fake_backup.sh"
    fake_script.write_text("#!/bin/sh\necho fake\n")
    with patch(
        "app.backups.service._resolve_script_path", return_value=fake_script
    ), patch("app.backups.service.subprocess.run", return_value=mock_result):
        result = run_backup(backup_id)

    assert result["status"] == "failed"
    with session_factory() as session:
        row = session.get(Backup, backup_id)
        assert row is not None
        assert row.status == BackupStatus.FAILED.value
        assert row.error_summary is not None
        assert "mysqldump" in row.error_summary


def test_run_backup_script_missing(stack: tuple[TestClient, sessionmaker]) -> None:
    """Si el bash no existe en VPS (instalación incompleta), la row
    debe marcarse FAILED con error_summary informativo. NO hace
    subprocess."""
    _client, session_factory = stack
    backup_id = _seed_running_backup(
        session_factory, triggered_by=BackupTrigger.CRON
    )
    with patch(
        "app.backups.service._resolve_script_path",
        return_value=Path("/nonexistent/path/backup.sh"),
    ):
        result = run_backup(backup_id)

    assert result["status"] == "failed"
    with session_factory() as session:
        row = session.get(Backup, backup_id)
        assert row is not None
        assert row.status == BackupStatus.FAILED.value
        assert "no encontrado" in (row.error_summary or "")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_list_backups_admin_only(stack: tuple[TestClient, sessionmaker]) -> None:
    client, _ = stack
    headers = auth_headers(client, "user")
    response = client.get("/api/admin/backups", headers=headers)
    assert response.status_code == 403


def test_list_backups_empty(stack: tuple[TestClient, sessionmaker]) -> None:
    client, _ = stack
    headers = auth_headers(client, "admin")
    response = client.get("/api/admin/backups", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_list_backups_orders_desc(
    stack: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = stack
    with session_factory() as session:
        for offset_minutes in (10, 5, 0):
            session.add(
                Backup(
                    id=f"id-{offset_minutes}",
                    filename=f"backup_{offset_minutes}.tar.gz.gpg",
                    filepath=f"/tmp/backup_{offset_minutes}.tar.gz.gpg",
                    size_bytes=offset_minutes * 100,
                    status=BackupStatus.SUCCESS.value,
                    triggered_by=BackupTrigger.CRON.value,
                    started_at=datetime(
                        2026, 6, 17, 12, 30 - offset_minutes, tzinfo=UTC
                    ),
                    finished_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
                )
            )
        session.commit()
    headers = auth_headers(client, "admin")
    rows = client.get("/api/admin/backups", headers=headers).json()
    assert [r["id"] for r in rows] == ["id-0", "id-5", "id-10"]


def test_create_backup_enqueues(stack: tuple[TestClient, sessionmaker]) -> None:
    client, session_factory = stack
    headers = auth_headers(client, "admin")

    fake_job = MagicMock(id="job-abc")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = fake_job
    with patch("app.backups.service.queue_for", return_value=fake_queue):
        response = client.post("/api/admin/backups/create", headers=headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["job_id"] == "job-abc"
    backup_id = body["backup_id"]

    with session_factory() as session:
        row = session.get(Backup, backup_id)
        assert row is not None
        assert row.status == BackupStatus.RUNNING.value
        assert row.triggered_by == BackupTrigger.MANUAL.value


def test_create_backup_blocks_concurrent_running(
    stack: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = stack
    headers = auth_headers(client, "admin")
    with session_factory() as session:
        session.add(
            Backup(
                id="running",
                filename="",
                filepath="",
                size_bytes=0,
                status=BackupStatus.RUNNING.value,
                triggered_by=BackupTrigger.MANUAL.value,
                started_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.post("/api/admin/backups/create", headers=headers)
    assert response.status_code == 409


def test_download_backup_streams_file(
    stack: tuple[TestClient, sessionmaker], tmp_path: Path
) -> None:
    client, session_factory = stack
    headers = auth_headers(client, "admin")
    blob_path = tmp_path / "backup_test.tar.gz.gpg"
    blob_path.write_bytes(b"fake-encrypted-bytes")
    with session_factory() as session:
        session.add(
            Backup(
                id="ok",
                filename="backup_test.tar.gz.gpg",
                filepath=str(blob_path),
                size_bytes=blob_path.stat().st_size,
                status=BackupStatus.SUCCESS.value,
                triggered_by=BackupTrigger.MANUAL.value,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.get("/api/admin/backups/ok/download", headers=headers)
    assert response.status_code == 200
    assert response.content == b"fake-encrypted-bytes"
    assert (
        "backup_test.tar.gz.gpg" in response.headers["content-disposition"]
    )


def test_download_backup_gone_if_file_missing(
    stack: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = stack
    headers = auth_headers(client, "admin")
    with session_factory() as session:
        session.add(
            Backup(
                id="rotated",
                filename="backup_rot.tar.gz.gpg",
                filepath="/nope/backup_rot.tar.gz.gpg",
                size_bytes=10,
                status=BackupStatus.SUCCESS.value,
                triggered_by=BackupTrigger.CRON.value,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.get("/api/admin/backups/rotated/download", headers=headers)
    assert response.status_code == 410


def test_delete_backup_removes_row_and_file(
    stack: tuple[TestClient, sessionmaker], tmp_path: Path
) -> None:
    client, session_factory = stack
    headers = auth_headers(client, "admin")
    blob_path = tmp_path / "backup_del.tar.gz.gpg"
    blob_path.write_bytes(b"x")
    with session_factory() as session:
        session.add(
            Backup(
                id="del",
                filename="backup_del.tar.gz.gpg",
                filepath=str(blob_path),
                size_bytes=1,
                status=BackupStatus.SUCCESS.value,
                triggered_by=BackupTrigger.MANUAL.value,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    response = client.delete("/api/admin/backups/del", headers=headers)
    assert response.status_code == 204
    assert not blob_path.exists()
    with session_factory() as session:
        assert session.get(Backup, "del") is None
