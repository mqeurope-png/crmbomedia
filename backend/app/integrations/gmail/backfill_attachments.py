"""CRM-ADJUNTOS-BACKFILL — backfill metadata-only de adjuntos (Opción B).

Los ~15k mensajes importados (backfill junio #246 + agosto #331 + go-forward)
se trajeron con `incluir_adjuntos=off`: no tienen ni filas en
`email_message_attachments` ni sumario `attachments_json`. Este módulo
recorre esos mensajes, pide a Gmail el payload (`messages.get` full) y
registra SOLO la metadata de cada adjunto (filename, mime, tamaño,
`gmail_attachment_id`). `storage_path` queda NULL — el binario se descarga
on-demand desde Gmail cuando el operador pulsa «Descargar» en el thread
detail. Cero storage local en el VPS.

Trade-off aceptado (decisión Bart 2026-08-10): si el mail se borra en Gmail
(papelera vaciada), el adjunto deja de ser descargable desde el CRM.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.gmail.backfill import (
    _is_attachment_part,
    _part_content_id,
    _walk_parts,
    _with_backoff,
    is_inline_part,
    is_not_found_error,
)
from app.integrations.gmail.service import _client_for
from app.models.crm import EmailMessage, EmailMessageAttachment

logger = logging.getLogger(__name__)


def extract_attachments_from_gmail_payload(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Recorre recursivamente `parts[]` del payload de Gmail y devuelve
    `[{filename, mime_type, size, gmail_attachment_id, is_inline}]`.

    Cuentan las partes con `body.attachmentId` y tamaño > 0 y `filename`
    no vacío. CRM-ADJUNTOS-UX: cada parte se marca `is_inline` (imagen
    embebida vs adjunto real) — se sigue devolviendo para no perder el
    binario ni la capacidad de re-descarga, pero las surfaces de usuario
    filtran las inline."""
    out: list[dict[str, Any]] = []
    for part in _walk_parts(payload or {}):
        if not _is_attachment_part(part):
            continue
        filename = (part.get("filename") or "").strip()
        if not filename:
            continue
        body = part.get("body") or {}
        out.append(
            {
                "filename": filename,
                "mime_type": part.get("mimeType"),
                "size": int(body.get("size") or 0),
                "gmail_attachment_id": body.get("attachmentId"),
                "is_inline": is_inline_part(part),
                "content_id": _part_content_id(part),
            }
        )
    return out


@dataclass
class AttachmentsBackfillReport:
    since: date
    until: date
    dry_run: bool
    processed: int = 0
    with_attachments: int = 0
    without_attachments: int = 0
    attachments_total: int = 0
    imported: int = 0
    skipped_dedupe: int = 0
    # CRM-ADJUNTOS-PURGE — mensajes de la BD cuyo gmail_message_id ya no
    # existe en Gmail, marcados gmail_status='deleted_gmail' (solo con
    # --purge-not-found).
    purged_not_found: int = 0
    errors: int = 0
    total_size_bytes: int = 0
    duration_seconds: float = 0.0

    def progress_line(self) -> str:
        return (
            f"  … {self.processed} mensajes "
            f"(adjuntos {self.attachments_total} / "
            f"metadata {self.imported} / dedupe {self.skipped_dedupe} / "
            f"errores {self.errors})"
        )

    def render(self) -> str:
        mins, secs = divmod(int(self.duration_seconds), 60)
        hours, mins = divmod(mins, 60)
        dur = f"{hours}h {mins:02d}min" if hours else f"{mins}min {secs:02d}s"
        gb = self.total_size_bytes / (1024**3)
        size_h = (
            f"{gb:.2f} GB"
            if gb >= 1
            else f"{self.total_size_bytes / (1024**2):.1f} MB"
        )
        bar = "━" * 41
        verb = "habría por importar" if self.dry_run else "guardada"
        lines = [
            f"Backfill adjuntos (metadata-only){' [DRY-RUN]' if self.dry_run else ''} "
            f"completo. Periodo: {self.since.isoformat()} → {self.until.isoformat()}.",
            bar,
            f"Mensajes procesados:         {self.processed:>8}",
            f"├── Con adjuntos:            {self.with_attachments:>8}",
            f"└── Sin adjuntos:            {self.without_attachments:>8}",
            f"Adjuntos totales:            {self.attachments_total:>8}",
            f"├── Metadata {verb:<18}{self.imported:>8}",
            f"├── Descartados por dedupe:  {self.skipped_dedupe:>8}",
            f"├── Marcados como borrados en Gmail: {self.purged_not_found:>3}",
            f"└── Errores:                 {self.errors:>8}",
            f"Tamaño total (si se descargaran): {size_h}",
            "Storage local usado:              0 B ← Opción B: cero descarga",
            f"Duración:                    {dur}",
            bar,
            "",
            "Los adjuntos se descargan on-demand desde Gmail cuando el usuario",
            "pulsa «Descargar» en el CRM. No consumen disco del VPS.",
        ]
        return "\n".join(lines)


def run_backfill_attachments(
    session: Session,
    *,
    user_id: str,
    since: date,
    until: date,
    dry_run: bool = False,
    batch_size: int = 100,
    purge_not_found: bool = False,
    progress: Callable[[str], None] = print,
    sleep_between_messages: float = 0.0,
) -> AttachmentsBackfillReport:
    """Registra metadata de adjuntos para los mensajes del rango que aún no
    tienen NINGUNA fila en `email_message_attachments` (idempotente a nivel
    mensaje; la UNIQUE (message_id, gmail_attachment_id) es el segundo
    cinturón a nivel adjunto)."""
    started = time.monotonic()
    report = AttachmentsBackfillReport(since=since, until=until, dry_run=dry_run)
    client = _client_for(session, user_id)

    since_dt = datetime.combine(since, dt_time.min, tzinfo=UTC)
    # `until` inclusivo (mismo criterio que backfill_universal).
    until_dt = datetime.combine(until + timedelta(days=1), dt_time.min, tzinfo=UTC)

    has_attachment = (
        exists()
        .where(EmailMessageAttachment.message_id == EmailMessage.id)
        .correlate(EmailMessage)
    )
    stmt = (
        select(EmailMessage.id, EmailMessage.gmail_message_id)
        .where(
            EmailMessage.gmail_message_id.is_not(None),
            EmailMessage.sent_at >= since_dt,
            EmailMessage.sent_at < until_dt,
            ~has_attachment,
        )
        .order_by(EmailMessage.sent_at.asc())
    )

    # Snapshot completo al inicio (2 columnas × ~15k filas — trivial).
    # NO paginamos con OFFSET sobre el predicado NOT EXISTS: al insertar
    # metadata los mensajes dejan de casar y el offset saltaría filas.
    all_rows = session.execute(stmt).all()
    progress(
        f"Mensajes a procesar (sin adjuntos ya cargados): {len(all_rows)}"
    )

    for batch_start in range(0, len(all_rows), batch_size):
        rows = all_rows[batch_start : batch_start + batch_size]
        for message_id, gmail_message_id in rows:
            try:
                raw = _with_backoff(
                    lambda mid=gmail_message_id: client.get_message(mid),
                    label=f"get_message[{gmail_message_id}]",
                )
            except Exception as exc:  # noqa: BLE001
                report.processed += 1
                # CRM-ADJUNTOS-PURGE — este loop itera mensajes de NUESTRA
                # BD, así que un 404 aquí = huérfano real (borrado en
                # Gmail). Con --purge-not-found lo marcamos y seguimos.
                if purge_not_found and is_not_found_error(exc):
                    session.execute(
                        update(EmailMessage)
                        .where(EmailMessage.id == message_id)
                        .values(gmail_status="deleted_gmail")
                    )
                    session.commit()
                    report.purged_not_found += 1
                    continue
                report.errors += 1
                logger.exception(
                    "adjuntos.backfill get_message failed mid=%s",
                    gmail_message_id,
                )
                continue

            attachments = extract_attachments_from_gmail_payload(
                raw.get("payload")
            )
            report.processed += 1
            if attachments:
                report.with_attachments += 1
            else:
                report.without_attachments += 1

            for att in attachments:
                report.attachments_total += 1
                report.total_size_bytes += att["size"]
                if dry_run:
                    continue
                try:
                    with session.begin_nested():
                        session.add(
                            EmailMessageAttachment(
                                message_id=message_id,
                                filename=att["filename"][:255],
                                mime_type=att["mime_type"],
                                size_bytes=att["size"],
                                storage_path=None,  # Opción B: sin binario
                                gmail_attachment_id=att["gmail_attachment_id"],
                                is_inline=att.get("is_inline", False),
                                content_id=att.get("content_id"),
                                created_at=datetime.now(UTC),
                            )
                        )
                    report.imported += 1
                except IntegrityError:
                    report.skipped_dedupe += 1

            if not dry_run and report.processed % 50 == 0:
                session.commit()
            if report.processed % 100 == 0:
                progress(report.progress_line())
            if sleep_between_messages:
                time.sleep(sleep_between_messages)

    if not dry_run:
        session.commit()
    report.duration_seconds = time.monotonic() - started
    return report
