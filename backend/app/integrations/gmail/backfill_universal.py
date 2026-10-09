"""CRM-GMAIL-BACKFILL — reprocesar el histórico de Gmail con captura universal.

El backfill original (PR #246) solo guardó mails de remitentes que ya eran
contacto del CRM. CRM-GMAIL (#329) retiró ese filtro para el flujo real-time,
pero el histórico anterior sigue sin los mails «huérfanos». Este módulo recorre
un rango de fechas y guarda TODO mail dirigido a un alias ACTIVO del CRM
(`user_email_aliases`), sea o no de un contacto conocido — reutilizando
`service._persist_message` (misma semántica que el push en tiempo real).

CRM-BACKFILL-SENT: la label SENT entra en el default — los mails ENVIADOS
desde Gmail directo se guardan con `direction=outbound`.

Captura de SALIDA universal (10/10/2026): lo que está en SENT se guarda
SIEMPRE, sea cual sea el From. Del 20/07 al 10/10/2026 los enviados desde
direcciones sin registrar en `user_email_aliases` se descartaron en silencio
(866 + 204 mensajes de Bart, entre ellos una oferta completa a un lead). El
informe dice cuántos enviados recupera —o recuperaría, en seco— por remitente
y por usuario atribuido, para mirarlo antes de escribir.

Se invoca desde `python -m app.integrations.gmail_watch backfill_universal`
(en primer plano) o como job `universal` de `gmail_backfill_jobs`
(`run_universal_job`, cola `gmail:backfill_historic`, worker-gmail).
Idempotente: los mails ya guardados se saltan por dedupe (unique
`(gmail_account_user_id, gmail_message_id)`), así que re-ejecutar con la misma
fecha solo importa lo que faltaba.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as _sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.gmail.backfill import is_not_found_error as _is_not_found
from app.models.crm import EmailDirection, EmailMessage, GmailBackfillJob, User
from app.services.email_aliases import active_alias_map

logger = logging.getLogger(__name__)

# Etiqueta de origen de los rows importados por este backfill (columna
# email_messages.imported_via), distinta de 'incoming_realtime'.
BACKFILL_IMPORTED_VIA = "historic_backfill_universal"

LABELS_POR_DEFECTO: tuple[str, ...] = ("INBOX", "SPAM", "SENT")
#: Tope del dry-run del job (el CLI tiene el suyo, 500): tres meses de
#: enviados de una cuenta caben de sobra.
DRY_RUN_LIMIT_JOB = 5000
#: Entradas que se conservan en cada desglose del `result_json`.
TOPE_DESGLOSE = 100


def _recortar(desglose: dict[str, int], tope: int = TOPE_DESGLOSE) -> dict[str, int]:
    if len(desglose) <= tope:
        return dict(desglose)
    orden = sorted(desglose.items(), key=lambda kv: kv[1], reverse=True)
    recortado = dict(orden[:tope])
    resto = orden[tope:]
    recortado[f"(otras {len(resto)} direcciones)"] = sum(c for _, c in resto)
    return recortado


@dataclass
class BackfillReport:
    since: date
    until: date
    labels: list[str]
    dry_run: bool
    imported_linked: int = 0
    imported_orphan: int = 0
    spam: int = 0
    # CRM-BACKFILL-SENT — mensajes capturados con direction=outbound
    # (label SENT). Subconjunto de linked+orphan.
    outbound: int = 0
    skipped_dedupe: int = 0
    skipped_no_alias: int = 0
    # CRM-ADJUNTOS-PURGE — mensajes marcados gmail_status='deleted_gmail'
    # (404 de Gmail con --purge-not-found; aquí solo cubre carreras entre
    # list y get — el purge efectivo del histórico lo hace
    # backfill_attachments, que itera mensajes de la BD).
    purged_not_found: int = 0
    skipped_ndr: int = 0
    errors: int = 0
    # {alias_descartado: nº de mails que iban ahí y NO está configurado}
    discard_by_alias: dict[str, int] = field(default_factory=dict)
    # Enviados importados (o que se importarían, en seco) por remitente y
    # por usuario al que se atribuyen: lo que Bart mira antes de escribir.
    enviados_por_remitente: dict[str, int] = field(default_factory=dict)
    enviados_por_usuario: dict[str, int] = field(default_factory=dict)
    cancelado: bool = False
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

    @property
    def imported(self) -> int:
        return self.imported_linked + self.imported_orphan

    @property
    def skipped(self) -> int:
        return self.skipped_dedupe + self.skipped_no_alias + self.skipped_ndr

    def progress_line(self) -> str:
        return (
            f"  … {self.total_processed} procesados "
            f"(link {self.imported_linked} / orphan {self.imported_orphan} / "
            f"enviados {self.outbound} / dedupe {self.skipped_dedupe} / "
            f"no-alias {self.skipped_no_alias})"
        )

    def como_dict(self) -> dict[str, Any]:
        """Para `gmail_backfill_jobs.result_json` (columna TEXT): los
        desgloses se recortan a las entradas con más mensajes y el resto se
        agrupa, que un repaso de INBOX de un buzón «catch-all» puede
        descartar a miles de direcciones distintas."""
        datos = asdict(self)
        datos["since"] = self.since.isoformat()
        datos["until"] = self.until.isoformat()
        datos["total_processed"] = self.total_processed
        datos["imported"] = self.imported
        datos["skipped"] = self.skipped
        for clave in ("discard_by_alias", "enviados_por_remitente", "enviados_por_usuario"):
            datos[clave] = _recortar(datos[clave])
        return datos

    def render(self) -> str:
        mins, secs = divmod(int(self.duration_seconds), 60)
        hours, mins = divmod(mins, 60)
        dur = f"{hours}h {mins:02d}min" if hours else f"{mins}min {secs:02d}s"
        bar = "━" * 41
        lines = [
            f"Backfill {'(DRY-RUN) ' if self.dry_run else ''}"
            f"{'CANCELADO' if self.cancelado else 'completo'}. "
            f"Periodo: {self.since.isoformat()} → {self.until.isoformat()}. "
            f"Labels: {', '.join(self.labels)}.",
            bar,
            f"Total procesados:            {self.total_processed:>8}",
            f"├── Importados con contacto: {self.imported_linked:>8}",
            f"├── Importados huérfanos:    {self.imported_orphan:>8}",
            f"├── Enviados (outbound):     {self.outbound:>8}",
            f"├── Marcados como spam:      {self.spam:>8}",
            f"├── Descartados por dedupe:  {self.skipped_dedupe:>8} (ya existían)",
            f"└── Descartados por alias:   {self.skipped_no_alias:>8} "
            f"(entrantes a alias no configurado)",
        ]
        if self.purged_not_found:
            lines.append(
                f"Marcados como borrados en Gmail: {self.purged_not_found}"
            )
        if self.skipped_ndr:
            lines.append(
                f"    (NDR/bounce ignorados:   {self.skipped_ndr:>8})"
            )
        lines.append(f"Errores:                     {self.errors:>8}")
        lines.append(f"Duración:                    {dur:>8}")
        lines.append(bar)
        if self.enviados_por_remitente:
            verbo = "se recuperarían" if self.dry_run else "recuperados"
            lines.append("")
            lines.append(f"Enviados que {verbo}, por remitente:")
            for remitente, count in sorted(
                self.enviados_por_remitente.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"  {remitente:<40} {count}")
            lines.append(f"Enviados que {verbo}, por usuario atribuido:")
            for usuario, count in sorted(
                self.enviados_por_usuario.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"  {usuario:<40} {count}")
        if self.discard_by_alias:
            lines.append("")
            lines.append("Alias que descartaron mails entrantes (baja por número):")
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


def _load_seen(session: Session) -> set[str]:
    """IDs de Gmail ya almacenados en CUALQUIER cuenta (dedupe barato
    pre-fetch). Hay un solo buzón: un envío del compositor guardado bajo
    otro usuario trae el mismo id que su copia en SENT."""
    from app.integrations.gmail.service import ids_guardados_en_el_buzon  # noqa: PLC0415

    return ids_guardados_en_el_buzon(session)


def _build_query(since: date, until: date) -> str:
    # Gmail `after:` es inclusivo; `before:` es exclusivo → +1 día para que
    # `until` sea inclusivo.
    before = until + timedelta(days=1)
    return f"after:{since:%Y/%m/%d} before:{before:%Y/%m/%d}"


def _anotar_enviado(
    report: BackfillReport, result: EmailMessage, emails_por_usuario: dict[str, str],
) -> None:
    report.outbound += 1
    remitente = (result.from_email or "").strip().lower() or "(sin remitente)"
    report.enviados_por_remitente[remitente] = (
        report.enviados_por_remitente.get(remitente, 0) + 1
    )
    usuario = emails_por_usuario.get(result.created_by_user_id or "", "(sin atribuir)")
    report.enviados_por_usuario[usuario] = report.enviados_por_usuario.get(usuario, 0) + 1


def run_backfill_universal(
    session: Session,
    *,
    user_id: str,
    since: date,
    until: date,
    dry_run: bool = False,
    dry_run_limit: int = 500,
    purge_not_found: bool = False,
    # CRM-BACKFILL-SENT: SENT en el default — trae también los mails
    # ENVIADOS desde Gmail directo (direction=outbound).
    labels: Sequence[str] = LABELS_POR_DEFECTO,
    batch_size: int = 100,
    alias_map: dict[str, str] | None = None,
    sleep_between_pages: float = 0.0,
    progress: Callable[[str], None] | None = None,
    on_progress: Callable[[BackfillReport], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> BackfillReport:
    """Recorre el histórico y persiste (o cuenta, en dry-run) los mails a alias
    activos y TODO lo enviado. Reutiliza `service._persist_message` con
    `emit_activity=False` (no re-dispara workflows ni ensucia timelines con
    correo viejo).

    `on_progress` recibe el informe parcial cada 100 mensajes examinados (el
    job lo vuelca en sus contadores); `cancel_check` se consulta en cada
    página y, si devuelve True, el informe vuelve con `cancelado=True`."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    emit = progress or (lambda _msg: None)
    if alias_map is None:
        alias_map = active_alias_map(session)
    if not alias_map and any(lbl != "SENT" for lbl in labels):
        # Lo ENVIADO se captura sin alias; lo entrante no (su gate los
        # necesita), así que un repaso de INBOX/SPAM sin alias no tiene sentido.
        raise ValueError(
            "Sin alias activos en user_email_aliases. Configura los alias en "
            "/admin/users antes de reprocesar la entrada (solo SENT no los necesita)."
        )
    emails_por_usuario: dict[str, str] = {
        uid: email for uid, email in session.execute(select(User.id, User.email)).all()
    }

    client = gmail_service._client_for(session, user_id)
    report = BackfillReport(
        since=since, until=until, labels=list(labels), dry_run=dry_run
    )
    seen = _load_seen(session)
    query = _build_query(since, until)
    started = time.monotonic()
    examined = 0  # mensajes a los que se les pidió get_message (post-dedupe)

    def _terminar() -> BackfillReport:
        report.duration_seconds = time.monotonic() - started
        return report

    for label in labels:
        page_token: str | None = None
        while True:
            if cancel_check is not None and cancel_check():
                report.cancelado = True
                return _terminar()
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
                except Exception as exc:  # noqa: BLE001
                    # CRM-ADJUNTOS-PURGE — 404 entre list y get (mensaje
                    # borrado en Gmail en la ventana). Con el flag, si el
                    # mensaje YA está en nuestra BD lo marcamos huérfano.
                    if purge_not_found and _is_not_found(exc):
                        result_marked = session.execute(
                            _sa_update(EmailMessage)
                            .where(EmailMessage.gmail_message_id == mid)
                            .values(gmail_status="deleted_gmail")
                        )
                        session.commit()
                        if result_marked.rowcount:
                            report.purged_not_found += 1
                            # Al dedupe: que las pasadas de otras labels no
                            # re-marquen (ni re-llamen a Gmail) por este mid.
                            seen.add(mid)
                            continue
                    logger.warning(
                        "gmail.backfill.get_failed msg=%s", mid, exc_info=True
                    )
                    report.errors += 1
                    continue

                examined += 1
                # El mensaje «es nuestro» si llegó a un alias activo (inbound),
                # si lo envió un alias activo, o si está en SENT: lo que sale
                # del buzón conectado es correo escrito por alguien de la
                # casa, sea cual sea el From. Solo la ENTRADA pasa por el
                # gate de alias.
                delivered = gmail_service.compute_delivered_to(raw, alias_map)
                sender_alias = gmail_service.sender_alias_of(raw, alias_map)
                enviado = label == "SENT" or gmail_service.es_mensaje_enviado(raw)
                if delivered is None and sender_alias is None and not enviado:
                    report.skipped_no_alias += 1
                    key = gmail_service.primary_recipient(raw) or "(desconocido)"
                    report.discard_by_alias[key] = (
                        report.discard_by_alias.get(key, 0) + 1
                    )
                else:
                    if enviado and "SENT" not in (raw.get("labelIds") or []):
                        # Listado bajo SENT pero el full no trae la label (raro):
                        # que `_persist_message` lo vea como enviado igual.
                        raw = {**raw, "labelIds": [*(raw.get("labelIds") or []), "SENT"]}
                    try:
                        if dry_run:
                            result = gmail_service._persist_message(
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
                                result = gmail_service._persist_message(
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
                        if result.direction == EmailDirection.OUTBOUND:
                            _anotar_enviado(report, result, emails_por_usuario)
                        if result.is_spam:
                            report.spam += 1
                        seen.add(mid)

                if examined % 100 == 0:
                    emit(report.progress_line())
                    if on_progress is not None:
                        on_progress(report)
                if dry_run and examined >= dry_run_limit:
                    return _terminar()

            if not dry_run:
                session.commit()
            page_token = page.get("nextPageToken")
            if not page_token:
                break
            if sleep_between_pages:
                time.sleep(sleep_between_pages)

    return _terminar()


# ---------------------------------------------------------------------------
# Job `universal` de gmail_backfill_jobs (worker-gmail, cola backfill_historic)
# ---------------------------------------------------------------------------


def _volcar_contadores(job: GmailBackfillJob, report: BackfillReport) -> None:
    job.total_processed = report.total_processed
    job.total_imported = report.imported
    job.total_skipped = report.skipped
    job.total_errors = report.errors


def run_universal_job(session: Session, job: GmailBackfillJob) -> None:
    """Modo `universal`: el backfill universal acotado por fechas sobre la
    cuenta Gmail de la organización, con `dry_run` para ver en seco cuántos
    enviados recuperaría por remitente y por usuario. Config (`config_json`):
    `{since, until, labels, dry_run, dry_run_limit}`. El informe entero va a
    `result_json`. Relanzable: lo ya guardado se salta por dedupe."""
    from app.integrations.gmail.backfill import (  # noqa: PLC0415
        _check_cancel,
        _start_running,
    )
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        GmailScopeMissingError,
    )
    from app.integrations.google_calendar.service import (  # noqa: PLC0415
        get_org_integration,
    )
    from app.models.crm import GmailBackfillStatus  # noqa: PLC0415

    if not _start_running(session, job):
        return
    config = json.loads(job.config_json or "{}")
    since = date.fromisoformat(str(config["since"]))
    until = date.fromisoformat(str(config.get("until") or date.today().isoformat()))
    labels = [str(lbl).upper() for lbl in (config.get("labels") or LABELS_POR_DEFECTO)]
    dry_run = bool(config.get("dry_run"))
    dry_run_limit = int(config.get("dry_run_limit") or DRY_RUN_LIMIT_JOB)

    def _fallar(motivo: str) -> None:
        job.status = GmailBackfillStatus.FAILED.value
        job.error_summary = motivo
        job.finished_at = datetime.now(UTC)
        session.commit()

    org = get_org_integration(session)
    if org is None or org.status != "active" or not org.connected_by_user_id:
        _fallar("No hay integración Google de la organización activa: conéctala "
                "desde /account antes de rellenar.")
        return
    user_id = org.connected_by_user_id
    logger.info(
        "gmail.backfill.universal started job=%s since=%s until=%s labels=%s dry_run=%s",
        job.id, since, until, labels, dry_run,
    )

    def _progreso(parcial: BackfillReport) -> None:
        _volcar_contadores(job, parcial)
        job.updated_at = datetime.now(UTC)
        session.commit()

    try:
        report = run_backfill_universal(
            session,
            user_id=user_id,
            since=since,
            until=until,
            dry_run=dry_run,
            dry_run_limit=dry_run_limit,
            labels=labels,
            sleep_between_pages=0.0 if dry_run else 0.5,
            on_progress=_progreso,
            cancel_check=lambda: _check_cancel(session, job),
        )
    except (ValueError, GmailNotConnectedError, GmailScopeMissingError) as exc:
        _fallar(str(exc))
        return
    if not dry_run:
        session.commit()
    _volcar_contadores(job, report)
    job.result_json = json.dumps(report.como_dict(), ensure_ascii=False)
    if report.cancelado:
        # `_check_cancel` ya dejó el job en CANCELLED; solo guardamos lo hecho.
        job.finished_at = job.finished_at or datetime.now(UTC)
    else:
        job.status = GmailBackfillStatus.COMPLETED.value
        job.finished_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "gmail.backfill.universal done job=%s dry_run=%s imported=%d outbound=%d "
        "dedupe=%d errors=%d",
        job.id, dry_run, report.imported, report.outbound, report.skipped_dedupe,
        report.errors,
    )
