"""Sprint-Backfill-Gmail — orchestración del backfill histórico.

Itera (user con Gmail) × (alias `is_allowed=True`) × (contacto con email)
y descubre la conversación histórica entre cada pareja vía
`users.messages.list(q='from:X to:Y OR from:Y to:X newer_than:NN m')`.

Dos modos sobre la misma cola `gmail:backfill_historic`:

- `estimate`: solo cuenta mensajes y suma tamaños de adjuntos llamando
  `messages.get(format='metadata')`. No toca DB ni disco. Rellena
  `gmail_backfill_jobs.result_json` con el desglose por usuario que
  la UI pinta antes del confirm.

- `execute`: importa cada mensaje a `email_messages` + threads,
  asociando `contact_id` correcto y `gmail_account_user_id` del owner
  del alias. Si `include_attachments=True` baja los binarios <= cap
  y los guarda bajo `/var/lib/crmbo/attachments/{contact_id}/{msg_id}/
  {filename}` + crea fila en `email_message_attachments`.

Ambos modos:

- Saltan usuarios sin Gmail conectado o con scope expirado — reportan
  `needs_reconnect=True` en el breakdown.
- Cooperan con `gmail_backfill_jobs.status='cancelling'`: comprueban
  el flag cada N iteraciones y finalizan limpio.
- Persisten progreso cada 100 mensajes procesados via session.commit.
- Dedup por `gmail_message_id` antes de crear (el backfill se puede
  re-ejecutar sin duplicar)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.gmail.client import GmailClient
from app.integrations.gmail.service import (
    GmailNotConnectedError,
    GmailScopeMissingError,
    _client_for,
    _extract_bodies,
    _get_or_create_thread,
    _index_headers,
    _parse_date,
)
from app.models.crm import (
    Contact,
    EmailDirection,
    EmailMessage,
    EmailMessageAttachment,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
    UserEmailAliasPref,
    UserGoogleIntegration,
)

logger = logging.getLogger(__name__)

#: Volume mount point para los adjuntos descargados por el backfill.
#: docker-compose monta el named volume `crmbo_email_attachments` aquí
#: en api + worker-sync para que los dos containers vean los mismos
#: ficheros — el worker escribe y el endpoint de download los sirve.
ATTACHMENT_ROOT = Path(
    os.environ.get("CRMBO_ATTACHMENT_ROOT", "/var/lib/crmbo/attachments")
)

#: Cuántos mensajes procesar antes de un commit + cancel-check. 100
#: balance entre overhead de commits y latencia para que un cancel
#: surta efecto rápido.
PROGRESS_COMMIT_EVERY = 100

#: Cuántos mensajes acumulamos por (alias, contacto) antes de saltar
#: al siguiente par. Conversaciones legítimas raramente cruzan este
#: número con un solo contacto; lo definimos defensivamente para que
#: un bug del query no se coma 100k mensajes.
MAX_MESSAGES_PER_PAIR = 5000

#: Sanitiza el filename para evitar path traversal. Rechaza `..`,
#: `/`, `\` y caracteres null. Mantiene espacios y unicode (Bart
#: tiene equipo en castellano).
_FILENAME_BAD = re.compile(r"[\\/\x00]")


def _safe_filename(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        return "attachment"
    name = name.replace("..", "_")
    name = _FILENAME_BAD.sub("_", name)
    # Cap a 200 chars — algunos FS limitan a 255 y queremos margen.
    return name[:200] or "attachment"


def _build_query(alias_email: str, contact_email: str, months_back: int) -> str:
    # Gmail query syntax: paréntesis + AND/OR explicitos. `newer_than`
    # acepta {N}d/m/y. Doble cobertura inbound + outbound.
    safe_alias = alias_email.replace('"', "")
    safe_contact = contact_email.replace('"', "")
    return (
        f'((from:"{safe_alias}" to:"{safe_contact}") '
        f'OR (from:"{safe_contact}" to:"{safe_alias}")) '
        f"newer_than:{months_back}m"
    )


def _iter_aliases(
    session: Session, user_id: str
) -> list[UserEmailAliasPref]:
    return list(
        session.scalars(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == user_id,
                UserEmailAliasPref.is_allowed.is_(True),
            )
        )
    )


def _iter_connected_users(
    session: Session,
) -> list[UserGoogleIntegration]:
    """Users con Gmail conectado Y scope gmail.send. Si el scope falta,
    el client raise GmailScopeMissingError; aún así devolvemos el row
    para que el reporte por-usuario marque needs_reconnect=True."""
    return list(
        session.scalars(
            select(UserGoogleIntegration).where(
                UserGoogleIntegration.scopes.is_not(None),
            )
        )
    )


def _iter_crm_contacts(session: Session) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact).where(
                Contact.email.is_not(None),
                Contact.is_active.is_(True),
            )
        )
    )


# ---------------------------------------------------------------------------
# Estimate mode
# ---------------------------------------------------------------------------


def run_estimate(session: Session, job: GmailBackfillJob) -> None:
    """Modo `estimate`: cuenta mensajes y suma tamaños de adjuntos.
    Rellena `job.result_json` y deja `status=completed`."""
    config = json.loads(job.config_json or "{}")
    months_back = int(config.get("months_back", 36))
    breakdown: dict[str, dict[str, Any]] = {}
    total_emails = 0
    total_attachments_count = 0
    total_attachments_bytes = 0

    # Race: si el admin clickeó cancelar entre encolar y arrancar,
    # respetar el CANCELLING en vez de pisarlo con RUNNING.
    if job.status == GmailBackfillStatus.CANCELLING.value:
        job.status = GmailBackfillStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        session.commit()
        return
    job.status = GmailBackfillStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    session.commit()

    contacts = _iter_crm_contacts(session)
    integrations = _iter_connected_users(session)
    logger.info(
        "gmail.backfill.estimate started job=%s users=%d contacts=%d",
        job.id, len(integrations), len(contacts),
    )

    for integ in integrations:
        if _check_cancel(session, job):
            return
        user = session.get(User, integ.user_id)
        if user is None:
            continue
        user_row = breakdown.setdefault(
            integ.user_id,
            {
                "user_id": integ.user_id,
                "email": user.email,
                "emails": 0,
                "attachments_count": 0,
                "attachments_mb": 0.0,
                "needs_reconnect": False,
            },
        )
        try:
            client = _client_for(session, integ.user_id)
        except (GmailNotConnectedError, GmailScopeMissingError) as exc:
            logger.warning(
                "gmail.backfill skip user=%s: %s", integ.user_id, exc
            )
            user_row["needs_reconnect"] = True
            continue

        aliases = _iter_aliases(session, integ.user_id)
        if not aliases:
            continue

        for alias in aliases:
            for contact in contacts:
                if not contact.email:
                    continue
                if _check_cancel(session, job):
                    return
                pair_count, pair_attach_count, pair_attach_bytes = (
                    _estimate_pair(
                        client, alias.alias_email, contact.email, months_back
                    )
                )
                user_row["emails"] += pair_count
                user_row["attachments_count"] += pair_attach_count
                user_row["attachments_mb"] += pair_attach_bytes / (1024 * 1024)
                total_emails += pair_count
                total_attachments_count += pair_attach_count
                total_attachments_bytes += pair_attach_bytes
                job.total_processed = total_emails
                if total_emails % PROGRESS_COMMIT_EVERY == 0:
                    session.commit()

    # Estimación temporal: Gmail rate-limit funciona como 250 quota/s
    # por usuario; cada msg.get(format=full) ≈ 5 units → ≈ 50 msg/s
    # por usuario con un solo worker. Para N usuarios concurrentes
    # mejoraría pero el worker es serial.
    estimated_minutes = round(total_emails / 50 / 60, 1) if total_emails else 0.0
    result = {
        "total_emails": total_emails,
        "total_attachments_count": total_attachments_count,
        "total_attachments_size_mb": round(
            total_attachments_bytes / (1024 * 1024), 1
        ),
        "estimated_storage_gb": round(
            total_attachments_bytes / (1024 ** 3), 2
        ),
        "estimated_duration_minutes": estimated_minutes,
        "per_user_breakdown": list(breakdown.values()),
        "months_back": months_back,
    }
    job.result_json = json.dumps(result)
    job.status = GmailBackfillStatus.COMPLETED.value
    job.finished_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "gmail.backfill.estimate done job=%s emails=%d attach_mb=%.1f",
        job.id, total_emails, total_attachments_bytes / (1024 * 1024),
    )


def _estimate_pair(
    client: GmailClient,
    alias_email: str,
    contact_email: str,
    months_back: int,
) -> tuple[int, int, int]:
    """Para un par alias↔contacto: cuenta msgs y suma bytes de adjuntos.
    Devuelve `(emails, attach_count, attach_bytes)`."""
    query = _build_query(alias_email, contact_email, months_back)
    page_token: str | None = None
    msg_ids: list[str] = []
    fetched_pages = 0
    while True:
        if fetched_pages * 100 >= MAX_MESSAGES_PER_PAIR:
            break
        try:
            page = client.list_messages(
                query=query, page_size=100, page_token=page_token
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill.estimate list failed alias=%s contact=%s: %s",
                alias_email, contact_email, exc,
            )
            return 0, 0, 0
        for m in page["messages"]:
            mid = m.get("id")
            if mid:
                msg_ids.append(mid)
        page_token = page.get("nextPageToken")
        fetched_pages += 1
        if not page_token:
            break

    attach_count = 0
    attach_bytes = 0
    for mid in msg_ids:
        try:
            meta = client.get_message_metadata(mid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill.estimate get_metadata failed mid=%s: %s",
                mid, exc,
            )
            continue
        for part in _walk_parts(meta.get("payload") or {}):
            if part.get("filename"):
                size = int((part.get("body") or {}).get("size") or 0)
                if size > 0:
                    attach_count += 1
                    attach_bytes += size
    return len(msg_ids), attach_count, attach_bytes


def _walk_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = [payload]
    while queue:
        part = queue.pop()
        out.append(part)
        for child in part.get("parts") or []:
            queue.append(child)
    return out


# ---------------------------------------------------------------------------
# Execute mode
# ---------------------------------------------------------------------------


def run_execute(session: Session, job: GmailBackfillJob) -> None:
    """Modo `execute`: importa los mensajes a la DB. Asocia
    `contact_id` (del contacto del CRM cuyo email matchea el otro
    extremo del thread) y `gmail_account_user_id` (del comercial
    dueño del alias). Dedup por `gmail_message_id`."""
    config = json.loads(job.config_json or "{}")
    months_back = int(config.get("months_back", 36))
    include_attachments = bool(config.get("include_attachments", True))
    max_attachment_mb = int(config.get("max_attachment_size_mb", 25))
    max_attachment_bytes = max_attachment_mb * 1024 * 1024

    # Race: si el admin clickeó cancelar entre encolar y arrancar,
    # respetar el CANCELLING en vez de pisarlo con RUNNING.
    if job.status == GmailBackfillStatus.CANCELLING.value:
        job.status = GmailBackfillStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        session.commit()
        return
    job.status = GmailBackfillStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    session.commit()

    contacts = _iter_crm_contacts(session)
    integrations = _iter_connected_users(session)
    errors_by_user: dict[str, str] = {}
    users_skipped: list[str] = []

    for integ in integrations:
        if _check_cancel(session, job):
            return
        try:
            client = _client_for(session, integ.user_id)
        except (GmailNotConnectedError, GmailScopeMissingError) as exc:
            errors_by_user[integ.user_id] = str(exc)
            users_skipped.append(integ.user_id)
            continue
        aliases = _iter_aliases(session, integ.user_id)
        if not aliases:
            continue

        for alias in aliases:
            for contact in contacts:
                if not contact.email:
                    continue
                if _check_cancel(session, job):
                    return
                _import_pair(
                    session,
                    client=client,
                    job=job,
                    owner_user_id=integ.user_id,
                    alias_email=alias.alias_email,
                    contact=contact,
                    months_back=months_back,
                    include_attachments=include_attachments,
                    max_attachment_bytes=max_attachment_bytes,
                )

    result = {
        "users_processed": len(integrations) - len(users_skipped),
        "users_skipped": users_skipped,
        "errors_by_user": errors_by_user,
        "months_back": months_back,
        "include_attachments": include_attachments,
    }
    job.result_json = json.dumps(result)
    job.status = GmailBackfillStatus.COMPLETED.value
    job.finished_at = datetime.now(UTC)
    session.commit()


def _import_pair(
    session: Session,
    *,
    client: GmailClient,
    job: GmailBackfillJob,
    owner_user_id: str,
    alias_email: str,
    contact: Contact,
    months_back: int,
    include_attachments: bool,
    max_attachment_bytes: int,
) -> None:
    query = _build_query(alias_email, contact.email, months_back)
    page_token: str | None = None
    fetched_pages = 0
    while True:
        if fetched_pages * 100 >= MAX_MESSAGES_PER_PAIR:
            break
        try:
            page = client.list_messages(
                query=query, page_size=100, page_token=page_token
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill.execute list failed user=%s alias=%s contact=%s: %s",
                owner_user_id, alias_email, contact.email, exc,
            )
            job.total_errors += 1
            return
        for entry in page["messages"]:
            mid = entry.get("id")
            if not mid:
                continue
            if _check_cancel(session, job):
                return
            _import_one_message(
                session,
                client=client,
                job=job,
                owner_user_id=owner_user_id,
                contact=contact,
                gmail_message_id=mid,
                gmail_thread_id=entry.get("threadId") or mid,
                include_attachments=include_attachments,
                max_attachment_bytes=max_attachment_bytes,
            )
        page_token = page.get("nextPageToken")
        fetched_pages += 1
        if not page_token:
            break


def _import_one_message(
    session: Session,
    *,
    client: GmailClient,
    job: GmailBackfillJob,
    owner_user_id: str,
    contact: Contact,
    gmail_message_id: str,
    gmail_thread_id: str,
    include_attachments: bool,
    max_attachment_bytes: int,
) -> None:
    job.total_processed += 1
    if job.total_processed % PROGRESS_COMMIT_EVERY == 0:
        session.commit()

    # Dedup. La unique constraint `(gmail_account_user_id,
    # gmail_message_id)` también lo garantiza, pero comprobar primero
    # nos ahorra la llamada `get_message(full)`.
    existing = session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_account_user_id == owner_user_id,
            EmailMessage.gmail_message_id == gmail_message_id,
        )
    )
    if existing is not None:
        job.total_skipped += 1
        return

    try:
        raw = client.get_message(gmail_message_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gmail.backfill get_message failed mid=%s: %s",
            gmail_message_id, exc,
        )
        job.total_errors += 1
        return

    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    from_header = headers.get("from") or ""
    to_header = headers.get("to") or ""
    cc_header = headers.get("cc")
    subject = headers.get("subject")
    sent_at = _parse_date(headers.get("date")) or datetime.now(UTC)
    from_addresses = getaddresses([from_header])
    from_name = from_addresses[0][0] if from_addresses else None
    from_email = from_addresses[0][1] if from_addresses else ""
    to_emails = [addr for _, addr in getaddresses([to_header]) if addr]
    cc_emails = (
        [addr for _, addr in getaddresses([cc_header])] if cc_header else None
    )
    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    direction = (
        EmailDirection.INBOUND
        if (from_email or "").lower() == (contact.email or "").lower()
        else EmailDirection.OUTBOUND
    )

    thread = _get_or_create_thread(
        session,
        gmail_account_user_id=owner_user_id,
        gmail_thread_id=gmail_thread_id,
        initiated_by_user_id=owner_user_id,
        contact_id=contact.id,
        subject=subject,
        first_message_at=sent_at,
        participants=[from_email, *to_emails],
    )

    attachments_meta: list[dict[str, Any]] = []
    parts_with_files: list[dict[str, Any]] = []
    for part in _walk_parts(raw.get("payload", {})):
        if part.get("filename"):
            parts_with_files.append(part)
            attachments_meta.append(
                {
                    "filename": part.get("filename"),
                    "mime_type": part.get("mimeType"),
                    "size": int((part.get("body") or {}).get("size") or 0),
                }
            )

    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=gmail_message_id,
        gmail_account_user_id=owner_user_id,
        direction=direction,
        from_email=from_email,
        from_name=from_name,
        to_emails_json=json.dumps(to_emails),
        cc_emails_json=json.dumps(cc_emails) if cc_emails else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=raw.get("snippet"),
        sent_at=sent_at,
        contact_id=contact.id,
        imported_via="historic_backfill",
        imported_at=datetime.now(UTC),
        attachments_json=json.dumps(attachments_meta) if attachments_meta else None,
    )
    session.add(message)
    thread.last_message_at = max(thread.last_message_at, sent_at)
    thread.message_count = (thread.message_count or 0) + 1
    session.flush()

    if include_attachments and parts_with_files:
        _download_attachments(
            session,
            client=client,
            message=message,
            parts=parts_with_files,
            contact_id=contact.id,
            gmail_message_id=gmail_message_id,
            max_attachment_bytes=max_attachment_bytes,
        )

    job.total_imported += 1


def _download_attachments(
    session: Session,
    *,
    client: GmailClient,
    message: EmailMessage,
    parts: list[dict[str, Any]],
    contact_id: str,
    gmail_message_id: str,
    max_attachment_bytes: int,
) -> None:
    base_dir = ATTACHMENT_ROOT / contact_id / gmail_message_id
    base_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        size = int((part.get("body") or {}).get("size") or 0)
        if size <= 0:
            continue
        if max_attachment_bytes and size > max_attachment_bytes:
            logger.info(
                "gmail.backfill skip attachment too big mid=%s size=%d cap=%d",
                gmail_message_id, size, max_attachment_bytes,
            )
            continue
        att_id = (part.get("body") or {}).get("attachmentId")
        if not att_id:
            continue
        try:
            resp = client.get_attachment(
                message_id=gmail_message_id, attachment_id=att_id
            )
            data = resp.get("data") or ""
            binary = base64.urlsafe_b64decode(data.encode())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.backfill attachment download failed mid=%s att=%s: %s",
                gmail_message_id, att_id, exc,
            )
            continue
        filename = _safe_filename(part.get("filename"))
        # Si chocan dos adjuntos con el mismo filename en el mismo
        # mensaje, suffix con index para no sobreescribir.
        target = base_dir / filename
        idx = 1
        while target.exists():
            stem, ext = os.path.splitext(filename)
            target = base_dir / f"{stem}_{idx}{ext}"
            idx += 1
        target.write_bytes(binary)
        rel_path = target.relative_to(ATTACHMENT_ROOT).as_posix()
        session.add(
            EmailMessageAttachment(
                message_id=message.id,
                filename=filename,
                mime_type=part.get("mimeType"),
                size_bytes=size,
                storage_path=rel_path,
                gmail_attachment_id=att_id,
                created_at=datetime.now(UTC),
            )
        )


def _check_cancel(session: Session, job: GmailBackfillJob) -> bool:
    session.refresh(job, attribute_names=["status"])
    if job.status == GmailBackfillStatus.CANCELLING.value:
        job.status = GmailBackfillStatus.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        session.commit()
        logger.info("gmail.backfill cancelled job=%s", job.id)
        return True
    return False


# ---------------------------------------------------------------------------
# RQ entry point
# ---------------------------------------------------------------------------


def run_backfill(job_id: str) -> None:
    """RQ entry. Abre sesión, lee el row, dispatcha por modo."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        job = session.get(GmailBackfillJob, job_id)
        if job is None:
            logger.warning("gmail.backfill job not found id=%s", job_id)
            return
        try:
            if job.mode == GmailBackfillMode.ESTIMATE.value:
                run_estimate(session, job)
            elif job.mode == GmailBackfillMode.EXECUTE.value:
                run_execute(session, job)
            else:
                job.status = GmailBackfillStatus.FAILED.value
                job.error_summary = f"Unknown mode: {job.mode}"
                job.finished_at = datetime.now(UTC)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("gmail.backfill crashed job=%s", job_id)
            job.status = GmailBackfillStatus.FAILED.value
            job.error_summary = str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            session.commit()


def enqueue_backfill(job_id: str) -> None:
    """Encola el job en `gmail:backfill_historic`. El worker-sync
    procesa esta queue."""
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import queue_name, redis_connection  # noqa: PLC0415

    queue = Queue(
        queue_name("gmail", "backfill_historic"),
        connection=redis_connection(),
        default_timeout=14_400,  # 4h — gmail backfill puede ser largo
    )
    queue.enqueue(run_backfill, job_id, job_timeout=14_400)
