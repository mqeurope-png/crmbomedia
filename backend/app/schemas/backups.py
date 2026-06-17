"""Pydantic schemas para el endpoint admin de Backups."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

BackupStatusValue = Literal["running", "success", "failed"]
BackupTriggerValue = Literal["cron", "manual"]


class BackupRead(BaseModel):
    id: str
    filename: str
    filepath: str
    size_bytes: int
    status: BackupStatusValue
    drive_url: str | None
    error_summary: str | None
    triggered_by: BackupTriggerValue
    started_at: datetime
    finished_at: datetime | None
    created_by_user_id: str | None

    model_config = ConfigDict(from_attributes=True)


class BackupCreateResponse(BaseModel):
    """Devuelto por `POST /api/admin/backups/create`. La row se crea en
    estado `running`; el RQ job lo terminará — el frontend hace
    polling de la lista para ver la transición a success/failed."""

    backup_id: str
    job_id: str
    status: BackupStatusValue
