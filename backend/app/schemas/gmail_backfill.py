"""Sprint-Backfill-Gmail — schemas Pydantic para el flujo admin."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BackfillExecuteRequest(BaseModel):
    """Config del backfill REAL. Mismos params que estimate por
    simetría — el handler usa los mismos límites para no encontrar
    sorpresas tras la estimación."""

    months_back: int = Field(default=36, ge=1, le=120)
    include_attachments: bool = True
    max_attachment_size_mb: int = Field(default=25, ge=0, le=200)


class BackfillEstimateRequest(BaseModel):
    months_back: int = Field(default=36, ge=1, le=120)


class BackfillPerUserBreakdown(BaseModel):
    """Una fila del desglose. Se rellena al final del modo `estimate`
    para que la UI muestre la tabla por comercial."""

    user_id: str
    email: str
    emails: int = 0
    attachments_count: int = 0
    attachments_mb: float = 0.0
    needs_reconnect: bool = False


class BackfillJobRead(BaseModel):
    """Una fila de `gmail_backfill_jobs` lista para la UI. La UI poll
    este endpoint cada N segundos hasta que `status` sea terminal.

    `result` se rellena cuando el job termina:
    - modo `estimate`: `{total_emails, total_attachments_count,
      total_attachments_size_mb, estimated_storage_gb,
      per_user_breakdown, estimated_duration_minutes}`.
    - modo `execute`: `{users_processed, users_skipped, errors_by_user}`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    mode: str
    status: str
    initiated_by_user_id: str | None = None
    total_estimated: int | None = None
    total_processed: int
    total_imported: int
    total_skipped: int
    total_errors: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    config: dict | None = None
    result: dict | None = None
    created_at: datetime
    updated_at: datetime
