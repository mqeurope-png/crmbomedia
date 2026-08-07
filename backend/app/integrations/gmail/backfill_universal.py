"""CRM-GMAIL-BACKFILL — reprocesar el histórico de Gmail con captura universal.

El backfill original (PR #246) solo guardó mails de remitentes que ya eran
contacto del CRM. CRM-GMAIL (#329) retiró ese filtro para el flujo real-time,
pero el histórico anterior sigue sin los mails «huérfanos». Este módulo recorre
un rango de fechas y guarda TODO mail dirigido a un alias ACTIVO del CRM
(`user_email_aliases`), sea o no de un contacto conocido — reutilizando
`service._persist_inbound` (misma semántica que el push en tiempo real).

Se invoca desde `python -m app.integrations.gmail_watch backfill_universal`.
Idempotente: los mails ya guardados se saltan por dedupe (unique
`(gmail_account_user_id, gmail_message_id)`), así que re-ejecutar con la misma
fecha solo importa lo que faltaba.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm import EmailMessage
from app.services.email_aliases import active_alias_map

logger = logging.getLogger(__name__)

# Etiqueta de origen de los rows importados por este backfill (columna
# email_messages.imported_via), distinta de 'incoming_realtime'.
BACKFILL_IMPORTED_VIA = "historic_backfill_universal"


@dataclass
class BackfillReport:
    since: date
    until: date
    labels: list[str]
    dry_run: bool
    imported_linked: int = 0
    imported_orphan: int = 0
    spam: int = 0
    skipped_dedupe: int = 0
    skipped_no_alias: int = 0
    skipped_ndr: int = 0
    errors: int = 0
    # {alias_descartado: nº de mails que iban ahí y NO está configurado}
    discard_by_alias: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    @property
    def total_processed(self) -> int:
        return (
            self.imported_linked
            + self.imported_orphan
            + self.skipped_dedupe
            + self.skipped_no_alias
            + self.skipped_ndr
            + self.errors
        )

    def progress_line(self) -> str:
        return (
            f"  … {self.total_processed} procesados "
            f"(link {self.imported_linked} / orphan {self.imported_orphan} / "
            f"dedupe {self.skipped_dedupe} / no-alias {self.skipped_no_alias})"
        )

    def render(self) -> str:
        mins, secs = divmod(int(self.duration_seconds), 60)
        hours, mins = divmod(mins, 60)
        dur = f"{hours}h {mins:02d}min" if hours else f"{mins}min {secs:02d}s"
        bar = "━" * 41
        lines = [
            f"Backfill {'(DRY-RUN) ' if self.dry_run else ''}completo. "
            f"Periodo: {self.since.isoformat()} → {self.until.isoformat()}. "
            f"Labels: {', '.join(self.labels)}.",
            bar,
            f"Total procesados:            {self.total_processed:>8}",
            f"├── Importados con contacto: {self.imported_linked:>8}",
            f"├── Importados huérfanos:    {self.imported_orphan:>8}",
            f"├── Marcados como spam:      {self.spam:>8}",
            f"├── Descartados por dedupe:  {self.skipped_dedupe:>8} (ya existían)",
            f"└── Descartados por alias:   {self.skipped_no_alias:>8} "
            f"(a alias no configurado)",
        ]
        if self.skipped_ndr:
            lines.append(
                f"    (NDR/bounce ignorados:   {self.skipped_ndr:>8})"
            )
        lines.append(f"Errores:                     {self.errors:>8}")
        lines.append(f"Duración:                    {dur:>8}")
        lines.append(bar)
        if self.discard_by_alias:
            lines.append("")
            lines.append("Alias que descartaron mails (baja por número):")
            for alias, count in sorted(
                self.discard_by_alias.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(
                    f"  {alias:<32} {count} mails descartados "
                    f"(no está en /admin/users)"
                )
            lines.append("")
            lines.append(
                "→ Añade esos alias en /admin/users y vuelve a ejecutar con la "
                "MISMA fecha para reprocesar solo esos (el resto se salta por "
                "dedupe)."
            )
        return "\n".join(lines)


def _load_seen(session: Session, user_id: str) -> set[str]:
    """IDs de Gmail ya almacenados para la cuenta (dedupe barato pre-fetch)."""
    return set(
        session.scalars(
            select(EmailMessage.gmail_message_id).where(
                EmailMessage.gmail_account_user_id == user_id,
                EmailMessage.gmail_message_id.is_not(None),
            )
        )
    )


def _build_query(since: date, until: date) -> str:
    # Gmail `after:` es inclusivo; `before:` es exclusivo → +1 día para que
    # `until` sea inclusivo.
    before = until + timedelta(days=1)
    return f"after:{since:%Y/%m/%d} before:{before:%Y/%m/%d}"


def run_backfill_universal(
    session: Session,
    *,
    user_id: str,
    since: date,
    until: date,
    dry_run: bool = False,
    dry_run_limit: int = 500,
    labels: Sequence[str] = ("INBOX", "SPAM"),
    batch_size: int = 100,
    alias_map: dict[str, str] | None = None,
    sleep_between_pages: float = 0.0,
    progress: Callable[[str], None] | None = None,
) -> BackfillReport:
    """Recorre el histórico y persiste (o cuenta, en dry-run) los mails a alias
    activos. Reutiliza `service._persist_inbound` con `emit_activity=False`
    (no re-dispara workflows ni ensucia timelines con correo viejo)."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    emit = progress or (lambda _msg: None)
    if alias_map is None:
        alias_map = active_alias_map(session)
    if not alias_map:
        raise ValueError(
            "Sin alias activos en user_email_aliases. Configura los alias en "
            "/admin/users antes de reprocesar."
        )

    client = gmail_service._client_for(session, user_id)
    report = BackfillReport(
        since=since, until=until, labels=list(labels), dry_run=dry_run
    )
    seen = _load_seen(session, user_id)
    query = _build_query(since, until)
    started = time.monotonic()
    examined = 0  # mensajes a los que se les pidió get_message (post-dedupe)

    for label in labels:
        page_token: str | None = None
        while True:
            page = client.list_messages(
                query=query,
                page_size=batch_size,
                page_token=page_token,
                label_ids=[label],
            )
            for stub in page.get("messages", []):
                mid = stub.get("id")
                if not mid:
                    continue
                if mid in seen:
                    report.skipped_dedupe += 1
                    continue
                try:
                    raw = client.get_message(mid)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "gmail.backfill.get_failed msg=%s", mid, exc_info=True
                    )
                    report.errors += 1
                    continue

                examined += 1
                delivered = gmail_service.compute_delivered_to(raw, alias_map)
                if delivered is None:
                    report.skipped_no_alias += 1
                    key = gmail_service.primary_recipient(raw) or "(desconocido)"
                    report.discard_by_alias[key] = (
                        report.discard_by_alias.get(key, 0) + 1
                    )
                else:
                    try:
                        if dry_run:
                            result = gmail_service._persist_inbound(
                                session,
                                user_id=user_id,
                                raw=raw,
                                gmail_thread_id=raw.get("threadId", ""),
                                alias_map=alias_map,
                                dry_run=True,
                                emit_activity=False,
                                imported_via=BACKFILL_IMPORTED_VIA,
                            )
                        else:
                            with session.begin_nested():
                                result = gmail_service._persist_inbound(
                                    session,
                                    user_id=user_id,
                                    raw=raw,
                                    gmail_thread_id=raw.get("threadId", ""),
                                    alias_map=alias_map,
                                    dry_run=False,
                                    emit_activity=False,
                                    imported_via=BACKFILL_IMPORTED_VIA,
                                )
                    except IntegrityError:
                        # Carrera con el unique → ya existía. El savepoint se
                        # deshizo solo; contamos dedupe y seguimos.
                        report.skipped_dedupe += 1
                        continue
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "gmail.backfill.persist_failed msg=%s",
                            mid,
                            exc_info=True,
                        )
                        report.errors += 1
                        continue

                    if result is None:
                        report.skipped_ndr += 1
                    else:
                        if result.contact_id:
                            report.imported_linked += 1
                        else:
                            report.imported_orphan += 1
                        if result.is_spam:
                            report.spam += 1
                        seen.add(mid)

                if examined % 100 == 0:
                    emit(report.progress_line())
                if dry_run and examined >= dry_run_limit:
                    report.duration_seconds = time.monotonic() - started
                    return report

            if not dry_run:
                session.commit()
            page_token = page.get("nextPageToken")
            if not page_token:
                break
            if sleep_between_pages:
                time.sleep(sleep_between_pages)

    report.duration_seconds = time.monotonic() - started
    return report
