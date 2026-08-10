"""High-level Gmail operations.

The route layer + worker layer call these. Each function takes a
SQLAlchemy session and is responsible for its own flushes; the
caller decides when to commit.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.gmail.client import GmailClient
from app.integrations.google_calendar import service as google_service
from app.integrations.google_calendar.client import GoogleAuthExpiredError
from app.models.crm import (
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    GmailPubsubWatch,
    SyncLog,
    UserEmailAlias,
)
from app.services.email_aliases import active_alias_map, resolve_delivered_to

logger = logging.getLogger(__name__)


class GmailNotConnectedError(RuntimeError):
    """Raised when the operator tries to act on Gmail before
    granting the gmail.send scope."""


class GmailScopeMissingError(RuntimeError):
    """Raised when the integration row exists but lacks a required
    scope — typically because the user is still on the Fase 2
    scopes."""


def _has_gmail_send(scopes: str) -> bool:
    return "https://www.googleapis.com/auth/gmail.send" in scopes.split()


def _client_for(session: Session, user_id: str) -> GmailClient:
    integration = google_service.get_integration(session, user_id)
    if integration is None:
        raise GmailNotConnectedError("Gmail no está conectado para este usuario.")
    # PR-OAuth-Permisos-Admin Item 12. Una integración marcada
    # needs_reconnect / disconnected_by_user NO debe usarse: sus tokens
    # están caducados o vaciados. La tratamos como "no conectada" para
    # que sync/backfill la skipeen con su manejo habitual.
    if getattr(integration, "status", "active") != "active":
        raise GmailNotConnectedError(
            f"Gmail status={integration.status} para el usuario — "
            "requiere reconexión."
        )
    if not _has_gmail_send(integration.scopes or ""):
        raise GmailScopeMissingError(
            "Falta el permiso gmail.send. Vuelve a autorizar Google en /account."
        )
    return GmailClient(session, integration)


def list_aliases(session: Session, user_id: str) -> list[dict[str, Any]]:
    """Wrap `client.list_send_as_aliases` with the error mapping the
    API layer expects."""
    return _client_for(session, user_id).list_send_as_aliases()


def _extract_subject_from_headers(headers: list[dict[str, Any]]) -> str:
    """Saca el Subject de la lista de headers que devuelve Gmail con
    `format=metadata`. Case-insensitive porque la API a veces ship
    `Subject` y a veces `subject`."""
    for h in headers or []:
        if str(h.get("name", "")).lower() == "subject":
            return str(h.get("value") or "")
    return ""


# Investigación post-deploy (Bart, 2026-06-16): la Gmail API NO
# expone qué drafts son templates. TODOS los drafts (templates y
# borradores normales) vienen con `labelIds = ["DRAFT"]` o
# `["DRAFT", "IMPORTANT"]`. Por eso filtrar por label no funciona.
#
# Heurística: lo que el operador considera "template" es un draft
# CREADO DESDE CERO sin ser respuesta ni reenvío. Las pistas:
#   - Subject NO empieza por Re:/Fwd:/AW:/WG:/RV: (variantes idioma).
#   - Snippet/body NO contiene la cabecera típica del quoted reply
#     ("On … wrote:", "El … escribió:", "Am … schrieb:", "Le … a
#     écrit:").
#   - Snippet NO empieza con `>` (texto citado).
#
# `re` ya está importado al top del módulo. `RE:`, `Re:`, `RE :`,
# `Re :`, `Fwd:`, `Fw:`, `Tr:` (FR), `AW:` (DE), `WG:` (DE),
# `RV:` (ES), `R:` (IT). Case-insensitive.
_REPLY_FORWARD_PREFIX = re.compile(
    r"^\s*(re|fwd?|tr|aw|wg|rv|r)\s*:\s*", re.IGNORECASE
)
# "On Mon, Jun 16 2026 at 10:00, Person <…> wrote:"
# "El 16 jun 2026, a las 10:00, Person escribió:"
# "Am 16.06.2026 schrieb Person:"
# "Le 16 juin 2026 à 10:00, Person a écrit:"
_QUOTED_HEADER = re.compile(
    r"(wrote\s*:|escribi[oó]\s*:|schrieb\s*:|a\s+[ée]crit\s*:|scriveva\s*:)",
    re.IGNORECASE,
)


def _looks_like_template(subject: str, snippet: str) -> bool:
    """Aplica la heurística reply/forward/quoted al subject + snippet
    de un draft. True == el operador lo consideraría un template;
    False == es respuesta/forward/draft en progreso."""
    if subject and _REPLY_FORWARD_PREFIX.match(subject):
        return False
    text = snippet or ""
    if text.lstrip().startswith(">"):
        return False
    if text and _QUOTED_HEADER.search(text):
        return False
    return True


# Captura `src="cid:..."` y `src='cid:...'` con grupos para quote
# y cid. Compartido entre el importador y el send-path (en el send
# se usa otra expresión que reconoce URLs del CRM, pero el flujo
# de extracción comparte la misma idea).
_CID_SRC_PATTERN = re.compile(
    r"""src=(['"])cid:([^'"]+)\1""", re.IGNORECASE
)


def _extract_cid_attachments(
    parsed_message,
) -> dict[str, tuple[str, str | None, bytes]]:
    """Walk del MIME para sacar cada part con `Content-ID`. Devuelve
    `{cid: (content_type, filename, raw_bytes)}`. Vacío si no hay
    inline attachments. El cid llega sin los `<>` envolventes."""
    out: dict[str, tuple[str, str | None, bytes]] = {}
    for part in parsed_message.walk():
        cid_header = part.get("Content-ID") or part.get("X-Attachment-Id")
        if not cid_header:
            continue
        cid = cid_header.strip("<>").strip()
        if not cid:
            continue
        content_type = part.get_content_type() or "application/octet-stream"
        filename = part.get_filename()
        try:
            data = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            continue
        out[cid] = (content_type, filename, data)
    return out


def _rewrite_cid_to_crm_urls(
    body_html: str,
    attachments: dict[str, tuple[str, str | None, bytes]],
    template_id: str,
) -> tuple[str, set[str]]:
    """Reescribe cada `src="cid:X"` por la URL del CRM que sirve el
    binario. Devuelve `(new_html, referenced_cids)` — el set sólo
    contiene los cids que realmente aparecen en el HTML; los que se
    quedaron sólo en el MIME pero no se referenciaban no se persisten.
    """
    if not attachments:
        return body_html, set()

    referenced: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        quote = match.group(1)
        cid = match.group(2).strip()
        if cid not in attachments:
            return match.group(0)
        referenced.add(cid)
        return (
            f"src={quote}/api/email-templates/{template_id}"
            f"/attachments/by-cid/{cid}{quote}"
        )

    new_html = _CID_SRC_PATTERN.sub(_replace, body_html)
    return new_html, referenced


# Match `src="…/api/email-templates/{template_id}/attachments/by-cid/
# {cid}…"` con prefijo opcional (origin absoluto o root-relative) y
# query/fragment opcionales después del cid. Inverso del rewrite que
# hace `_rewrite_cid_to_crm_urls` en el import.
_CRM_ATTACHMENT_SRC_PATTERN = re.compile(
    r"""src=(['"])(?:https?://[^'"]+?)?/api/email-templates/"""
    r"""([^/'"?#]+)/attachments/by-cid/([^'"?#]+)(?:[?#][^'"]*)?\1""",
    re.IGNORECASE,
)


def _swap_crm_urls_to_cid(
    session: Session, body_html: str
) -> tuple[str, list[dict[str, Any]]]:
    """Para cada `src="…/api/email-templates/<id>/attachments/by-cid/
    <cid>…"` en el HTML, busca la fila en `email_template_attachments`,
    sustituye por `src="cid:<cid>"` y devuelve la lista de attachments
    para inline-MIME.

    Devuelve `(new_html, inline_parts)` donde cada item es
    `{cid, content_type, filename, data}`. Si la fila no existe la
    URL se deja intacta — el destinatario fallará al cargarla pero
    no rompemos el envío.

    Idempotente: dedupea por (template_id, cid).
    """
    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplateAttachment,
    )

    cache: dict[tuple[str, str], EmailTemplateAttachment | None] = {}
    parts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        quote = match.group(1)
        tpl_id = match.group(2)
        cid = match.group(3)
        key = (tpl_id, cid)
        if key not in cache:
            cache[key] = session.scalar(
                select(EmailTemplateAttachment).where(
                    EmailTemplateAttachment.template_id == tpl_id,
                    EmailTemplateAttachment.original_cid == cid,
                )
            )
        row = cache[key]
        if row is None:
            return match.group(0)
        if cid not in seen:
            seen.add(cid)
            parts.append(
                {
                    "cid": cid,
                    "content_type": row.content_type,
                    "filename": row.filename,
                    "data": bytes(row.data),
                }
            )
        return f"src={quote}cid:{cid}{quote}"

    new_html = _CRM_ATTACHMENT_SRC_PATTERN.sub(_replace, body_html)
    return new_html, parts


# Timeout por request a la Gmail API. La librería googleapiclient
# usa httplib2 por debajo, que NO trae timeout por defecto — un
# `drafts.get` que se cuelga en el socket bloquea el worker
# indefinidamente. Lo envolvemos en un thread y abortamos con
# `Future.result(timeout=…)` para garantizar progreso.
_GMAIL_REQUEST_TIMEOUT_S = 30.0
_GMAIL_REQUEST_RETRIES = 2


class GmailRequestTimeout(RuntimeError):
    """`drafts.get` (u otro request Gmail) no completó dentro del
    timeout tras N reintentos."""


def _gmail_call_with_timeout(
    fn,
    *args,
    timeout_s: float = _GMAIL_REQUEST_TIMEOUT_S,
    retries: int = _GMAIL_REQUEST_RETRIES,
    **kwargs,
):
    """Corre `fn(*args, **kwargs)` en un thread con timeout duro.

    Si vence el timeout reintenta hasta `retries` veces. Si ninguna
    intentona termina dentro del timeout, levanta
    `GmailRequestTimeout`. Cualquier otra excepción del callee se
    propaga sin reintento (los retries son sólo para colgues de red).

    El thread del intento expirado se cierra con `cancel_futures=True`
    pero httplib2 está en un syscall bloqueante — el hilo queda como
    daemon hasta que el SO desaloje el socket. Para un import one-shot
    es asumible.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "gmail.timeout fn=%s attempt=%d/%d timeout_s=%.1f",
                    getattr(fn, "__name__", repr(fn)),
                    attempt + 1,
                    retries + 1,
                    timeout_s,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    raise GmailRequestTimeout(
        f"Gmail request timed out after {retries + 1} attempts "
        f"({timeout_s:.0f}s each)"
    ) from last_exc


def import_gmail_templates_with_tpl_prefix(
    session: Session,
    *,
    user_id: str,
    created_by_user_id: str,
    delete_after: bool = False,
    sync_log: SyncLog | None = None,
) -> dict[str, Any]:
    """One-shot import de drafts Gmail con subject `[TPL] …` a la
    tabla `email_templates` (Sprint Email v2.2). Pensado para correr
    una vez tras lo cual el operador limpia Gmail.

    Idempotente: si ya existe un template CRM con el mismo `name`
    dentro de la folder "Gmail (importadas)", se salta. Re-runs no
    duplican.

    `delete_after=True` borra el draft Gmail tras un INSERT exitoso
    — útil para hacer la limpieza desde la misma llamada en vez de
    a mano.

    `sync_log` (opcional): cuando el caller es el worker, se pasa la
    fila SyncLog para que el loop pueda hacer commit por draft +
    refrescar `records_processed/skipped/failed`. El commit por
    draft también actualiza `updated_at` → la UI puede usar ese
    campo como heartbeat para detectar zombies (> 10 min sin tocar).

    Devuelve `{imported, skipped, errors, deleted, total_drafts_
    scanned, tpl_drafts_found}`.
    """
    import base64  # noqa: PLC0415
    from email import message_from_bytes  # noqa: PLC0415
    from email.policy import default as _default_policy  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
        EmailTemplateAttachment,
        EmailTemplateFolder,
    )

    client = _client_for(session, user_id)

    # Folder destino: "Gmail (importadas)" como is_global. Se crea
    # si no existe — idempotente.
    folder = session.scalar(
        select(EmailTemplateFolder).where(
            EmailTemplateFolder.name == "Gmail (importadas)",
            EmailTemplateFolder.is_global.is_(True),
        )
    )
    if folder is None:
        folder = EmailTemplateFolder(
            name="Gmail (importadas)",
            is_global=True,
        )
        session.add(folder)
        session.flush()
    folder_id = folder.id

    # Commit del folder + estado inicial antes del loop. Si una
    # iteración revienta el rollback resetea sólo el trabajo del
    # draft fallido, no la folder.
    if sync_log is not None:
        session.commit()

    # Set de names ya existentes en esta folder para idempotencia O(1).
    existing_names = {
        row.name
        for row in session.scalars(
            select(EmailTemplate).where(EmailTemplate.folder_id == folder_id)
        )
    }

    try:
        all_draft_ids = _gmail_call_with_timeout(client.list_all_drafts)
    except GmailRequestTimeout as exc:
        logger.error("gmail.import.list_all_drafts timeout: %s", exc)
        raise

    counters = {
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "deleted": 0,
        "total_drafts_scanned": len(all_draft_ids),
        "tpl_drafts_found": 0,
    }

    for draft_id in all_draft_ids:
        # Cada draft tiene su propio try/except. Una excepción aquí
        # (timeout de red, MIME inválido, fallo de DB en este insert)
        # se contiene al draft: rollback de SU transacción, log,
        # contador de errores, sigue con el siguiente. Imprescindible
        # para evitar zombies — antes un cuelgue en `drafts.get` paraba
        # el worker entero sin liberar el SyncLog.
        try:
            try:
                meta = _gmail_call_with_timeout(
                    client.get_draft_metadata, draft_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gmail.import.meta failed draft_id=%s err=%s",
                    draft_id,
                    exc,
                )
                counters["errors"] += 1
                _refresh_sync_log_counters(session, sync_log, counters)
                continue
            meta_msg = meta.get("message", {})
            headers = (meta_msg.get("payload") or {}).get("headers") or []
            subject = _extract_subject_from_headers(headers)
            if not subject.startswith("[TPL] "):
                continue
            counters["tpl_drafts_found"] += 1
            name = subject[len("[TPL] ") :].strip()
            if not name:
                counters["skipped"] += 1
                _refresh_sync_log_counters(session, sync_log, counters)
                continue
            if name in existing_names:
                counters["skipped"] += 1
                _refresh_sync_log_counters(session, sync_log, counters)
                continue

            try:
                full = _gmail_call_with_timeout(
                    client.get_draft_template, draft_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gmail.import.get failed draft_id=%s err=%s",
                    draft_id,
                    exc,
                )
                counters["errors"] += 1
                _refresh_sync_log_counters(session, sync_log, counters)
                continue
            raw_b64 = (full.get("message") or {}).get("raw")
            body_html = ""
            attachments_map: dict[str, tuple[str, str | None, bytes]] = {}
            if raw_b64:
                try:
                    raw_bytes = base64.urlsafe_b64decode(
                        raw_b64.encode("ascii")
                    )
                    parsed = message_from_bytes(
                        raw_bytes, policy=_default_policy
                    )
                    html_part = parsed.get_body(preferencelist=("html",))
                    plain_part = parsed.get_body(preferencelist=("plain",))
                    if html_part is not None:
                        body_html = html_part.get_content() or ""
                    elif plain_part is not None:
                        text = plain_part.get_content() or ""
                        body_html = "<p>" + text.replace("\n", "<br>") + "</p>"
                    attachments_map = _extract_cid_attachments(parsed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "gmail.import.parse failed draft_id=%s err=%s",
                        draft_id,
                        exc,
                    )
            if not body_html.strip():
                counters["errors"] += 1
                _refresh_sync_log_counters(session, sync_log, counters)
                continue

            template_id = str(uuid4())
            body_html, referenced_cids = _rewrite_cid_to_crm_urls(
                body_html, attachments_map, template_id
            )

            template = EmailTemplate(
                id=template_id,
                name=name,
                subject=name,
                body_html=body_html,
                folder_id=folder_id,
                is_global=True,
                owner_user_id=created_by_user_id,
            )
            session.add(template)
            now = datetime.now(UTC)
            for cid in referenced_cids:
                content_type, filename, data = attachments_map[cid]
                session.add(
                    EmailTemplateAttachment(
                        template_id=template_id,
                        original_cid=cid,
                        filename=filename,
                        content_type=content_type,
                        data=data,
                        created_at=now,
                    )
                )
            session.flush()
            existing_names.add(name)
            counters["imported"] += 1

            if delete_after:
                try:
                    _gmail_call_with_timeout(client.delete_draft, draft_id)
                    counters["deleted"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "gmail.import.delete failed draft_id=%s err=%s",
                        draft_id,
                        exc,
                    )

            # Commit por draft → persiste trabajo parcial y refresca
            # `sync_log.updated_at` (heartbeat). Si el worker muere en
            # el draft siguiente, los anteriores se quedan en BD y la
            # idempotencia los salta en el re-run.
            _refresh_sync_log_counters(session, sync_log, counters)
            if sync_log is not None:
                session.commit()
        except Exception as exc:  # noqa: BLE001
            # Cualquier excepción no esperada (DB lock, OOM parcial,
            # decode error fuera del bloque interno) se contiene al
            # draft: rollback, contador, sigue.
            session.rollback()
            logger.exception(
                "gmail.import.draft_failed draft_id=%s err=%s",
                draft_id,
                exc,
            )
            counters["errors"] += 1
            _refresh_sync_log_counters(session, sync_log, counters)
            if sync_log is not None:
                # Re-attach + commit del contador failed. La sesión
                # tras un rollback queda limpia pero sync_log podría
                # estar detached: merge para asegurar.
                sync_log = session.merge(sync_log)
                _refresh_sync_log_counters(session, sync_log, counters)
                session.commit()

    return counters


def _refresh_sync_log_counters(
    session: Session,
    sync_log: SyncLog | None,
    counters: dict[str, int],
) -> None:
    """Volca los counters al SyncLog si está conectado. El commit
    posterior actualiza `updated_at` (heartbeat). No-op si no se pasó
    sync_log (caller síncrono de tests sin async)."""
    if sync_log is None:
        return
    sync_log.records_processed = counters["imported"]
    sync_log.records_skipped = counters["skipped"]
    sync_log.records_failed = counters["errors"]
    session.flush()


def list_gmail_templates(
    session: Session,
    user_id: str,
    *,
    query: str | None = None,
    max_results: int = 30,
    debug: bool = False,
) -> list[dict[str, Any]]:
    """Devuelve las plantillas Gmail (drafts auto-creados desde la UI
    Templates de Gmail) del user autenticado.

    Mecánica (post 2026-06-16): la Gmail API NO expone qué drafts son
    templates — todos vienen con `labelIds=["DRAFT"]`. Aplicamos una
    heurística sobre subject + snippet: un draft es template si NO
    parece respuesta (Re:/Fwd:) NI tiene cabecera de quoted reply
    (`… wrote:` / `… escribió:` / `… schrieb:` / `… a écrit:`).

    El resultado se ordena por `updated_at DESC` (más reciente
    primero) — coherente con la UI de Gmail.

    Si `debug=True`, devolvemos metadata cruda de TODOS los drafts
    (sin filtrar) con `label_ids`, `thread_id`, `is_template` (decision
    de la heurística) para validación visual.
    """
    import base64  # noqa: PLC0415
    from email import message_from_bytes  # noqa: PLC0415
    from email.policy import default as _default_policy  # noqa: PLC0415

    client = _client_for(session, user_id)
    listing = client.list_draft_templates(query=query, max_results=max_results)
    out: list[dict[str, Any]] = []

    for entry in listing:
        draft_id = entry["id"]
        # Paso 1: metadata (rápido, sin raw) para inspeccionar labelIds
        # + subject + snippet. Usado tanto para debug como para filtrar
        # antes de pedir el body completo.
        try:
            meta = client.get_draft_metadata(draft_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.template.meta failed draft_id=%s err=%s",
                draft_id,
                exc,
            )
            continue
        message = meta.get("message", {})
        label_ids = list(message.get("labelIds") or [])
        snippet = message.get("snippet") or ""
        headers = (message.get("payload") or {}).get("headers") or []
        subject = _extract_subject_from_headers(headers)
        internal_ms = message.get("internalDate")
        updated_at = None
        if internal_ms:
            try:
                updated_at = datetime.fromtimestamp(
                    int(internal_ms) / 1000, tz=UTC
                )
            except (TypeError, ValueError):
                updated_at = None

        is_template = _looks_like_template(subject, snippet)

        if debug:
            out.append(
                {
                    "id": draft_id,
                    "subject": subject,
                    "body_html": "",  # debug skip body
                    "snippet": snippet,
                    "updated_at": updated_at,
                    "label_ids": label_ids,
                    "thread_id": message.get("threadId"),
                    "is_template": is_template,
                }
            )
            continue

        # Paso 2: filtro por heurística reply/forward/quoted. Si el
        # draft tiene pinta de respuesta o borrador en progreso, no
        # es template.
        if not is_template:
            continue

        # Paso 3: bajar body completo solo para los que pasan el filtro.
        try:
            full = client.get_draft_template(draft_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.template.get failed draft_id=%s err=%s",
                draft_id,
                exc,
            )
            continue
        full_message = full.get("message", {})
        raw_b64 = full_message.get("raw")
        body_html = ""
        if raw_b64:
            try:
                raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
                parsed = message_from_bytes(raw_bytes, policy=_default_policy)
                # Subject del raw si headers metadata era vacío.
                if not subject:
                    subject = str(parsed.get("subject") or "")
                html_part = parsed.get_body(preferencelist=("html",))
                plain_part = parsed.get_body(preferencelist=("plain",))
                if html_part is not None:
                    body_html = html_part.get_content() or ""
                elif plain_part is not None:
                    text = plain_part.get_content() or ""
                    body_html = "<p>" + text.replace("\n", "<br>") + "</p>"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gmail.template.parse failed draft_id=%s err=%s",
                    draft_id,
                    exc,
                )

        out.append(
            {
                "id": draft_id,
                "subject": subject,
                "body_html": body_html,
                "snippet": snippet,
                "updated_at": updated_at,
            }
        )
    # Orden estable: más reciente primero (paridad con la UI Gmail).
    # `updated_at=None` cae al final.
    out.sort(
        key=lambda item: (
            item.get("updated_at") or datetime(1970, 1, 1, tzinfo=UTC)
        ),
        reverse=True,
    )
    return out


def send_email(
    session: Session,
    *,
    sender_user_id: str,
    from_alias: str,
    from_name: str | None,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_html: str | None,
    body_text: str | None,
    contact_id: str | None,
    in_reply_to_message_id: str | None = None,
    include_unsubscribe: bool = False,
    tracking_base_url: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> EmailMessage:
    """Send a new outbound email and persist the thread + message rows.

    `in_reply_to_message_id` is OUR `EmailMessage.id`; when set we
    look up the upstream Gmail thread + headers so the recipient's
    client recognises the reply.

    PR-DisplayName-Remitente. Si el caller NO mandó `from_name` (caso
    común: `EmailComposerModal` que solo manda el email), resolvemos
    el display name desde las prefs del user:
        display_name_override > gmail_display_name > ""
    Esto deja el header `From:` como
    `"Scott, Artisjet Europe" <scott@x.eu>` por defecto en lugar de
    quedarse en `<scott@x.eu>` (que algunos clientes muestran como
    "scott" derivado del local-part).
    """
    if not from_name:
        from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

        pref = session.scalar(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == sender_user_id,
                UserEmailAliasPref.alias_email == from_alias,
            )
        )
        if pref is not None:
            override = (pref.display_name_override or "").strip()
            gmail_name = (pref.gmail_display_name or "").strip()
            from_name = override or gmail_name or None
    client = _client_for(session, sender_user_id)

    in_reply_to_header: str | None = None
    references_header: list[str] | None = None
    thread_id: str | None = None
    existing_thread: EmailThread | None = None

    if in_reply_to_message_id:
        existing = session.get(EmailMessage, in_reply_to_message_id)
        if existing is not None:
            # Gmail's send API documents three requirements to chain
            # onto an existing thread: a valid `threadId`, a matching
            # `Subject`, and `In-Reply-To` + `References` headers in
            # RFC 2822 form. The parent's `gmail_message_id` we have
            # in the DB is the API id (a hex token like
            # `1893a8c5b1f2dac3`) — NOT the angle-bracketed RFC
            # Message-Id (`<CABc…@mail.gmail.com>`) — so a header
            # built from it gets rejected as malformed and Gmail
            # silently breaks the conversation chain.
            #
            # Pull the actual Message-Id out of the parent message's
            # headers right now. One extra round-trip per reply, but
            # it's the only way to thread reliably without persisting
            # a new column on every message we have.
            rfc_message_id: str | None = None
            try:
                parent_meta = client.get_message(existing.gmail_message_id)
                parent_headers = _index_headers(
                    parent_meta.get("payload", {}).get("headers", []) or []
                )
                # Gmail returns header names case-preserved; _index_headers
                # lower-cases the keys so this lookup is canonical.
                rfc_message_id = parent_headers.get("message-id")
            except Exception:  # noqa: BLE001
                # If Gmail 404s the parent (deleted, expired) we still
                # try with the threadId — better a partial chain than
                # outright failure.
                rfc_message_id = None
            existing_thread = existing.thread
            thread_id = existing_thread.gmail_thread_id
            if rfc_message_id:
                in_reply_to_header = rfc_message_id
                references_header = [rfc_message_id]

    # Sprint Email v2.3a — link wrap + open pixel + optional
    # List-Unsubscribe. The body we end up sending differs from the
    # body we persist (Tiptap output stays clean; the recipient
    # version gets the redirect URLs and pixel).
    from app.core.config import get_settings  # noqa: PLC0415
    from app.email_tracking.services import (  # noqa: PLC0415
        build_unsubscribe_block,
        generate_token,
        inject_open_pixel,
        persist_tracking_token,
        record_event,
        wrap_links_for_tracking,
    )
    from app.models.crm import EmailEventType  # noqa: PLC0415

    base_url = tracking_base_url or get_settings().frontend_base_url
    track_token = generate_token()
    extra_headers: dict[str, str] = {}
    skip_links: set[str] = set()
    unsubscribe_token: str | None = None
    unsubscribe_url: str | None = None
    if include_unsubscribe:
        unsubscribe_token = generate_token()
        unsub_html, unsub_headers, unsubscribe_url = build_unsubscribe_block(
            token=unsubscribe_token, base_url=base_url
        )
        skip_links.add(unsubscribe_url)
        extra_headers.update(unsub_headers)
    outbound_html = body_html
    if outbound_html:
        outbound_html = wrap_links_for_tracking(
            outbound_html,
            token=track_token,
            base_url=base_url,
            extra_skip=skip_links,
        )
        outbound_html = inject_open_pixel(
            outbound_html, token=track_token, base_url=base_url
        )
        if include_unsubscribe:
            outbound_html += unsub_html

    # Sustituye los `src="/api/email-templates/.../by-cid/X"` que las
    # plantillas Gmail importadas dejan en el body por `src="cid:X"`,
    # recoge los blobs para adjuntarlos como inline parts. Si la
    # plantilla no usa attachments, no-op.
    inline_attachments: list[dict[str, Any]] = []
    if outbound_html:
        outbound_html, inline_attachments = _swap_crm_urls_to_cid(
            session, outbound_html
        )

    response = client.send_message(
        from_alias=from_alias,
        from_name=from_name,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_html=outbound_html,
        body_text=body_text,
        in_reply_to_message_id=in_reply_to_header,
        references=references_header,
        thread_id=thread_id,
        extra_headers=extra_headers or None,
        inline_attachments=inline_attachments or None,
        attachments=attachments or None,
    )

    gmail_message_id = response["id"]
    gmail_thread_id = response["threadId"]
    now = datetime.now(UTC)

    thread = existing_thread or _get_or_create_thread(
        session,
        gmail_account_user_id=sender_user_id,
        gmail_thread_id=gmail_thread_id,
        initiated_by_user_id=sender_user_id,
        contact_id=contact_id,
        subject=subject,
        first_message_at=now,
        participants=[*to, *(cc or []), from_alias],
    )

    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=gmail_message_id,
        gmail_account_user_id=sender_user_id,
        direction=EmailDirection.OUTBOUND,
        from_email=from_alias,
        from_name=from_name,
        to_emails_json=json.dumps(to),
        cc_emails_json=json.dumps(cc) if cc else None,
        bcc_emails_json=json.dumps(bcc) if bcc else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=_snippet(body_text, body_html),
        sent_at=now,
        contact_id=contact_id,
        created_by_user_id=sender_user_id,
    )
    session.add(message)
    thread.message_count = (thread.message_count or 0) + 1
    thread.last_message_at = now
    session.flush()

    # Tracking trail: one token row for the open + click endpoints,
    # one `sent` event we can later aggregate against. The unsubscribe
    # token (when set) reuses the same column on the unsubscribe row
    # so we don't need a separate table.
    persist_tracking_token(
        session, message_id=message.id, token=track_token
    )
    if unsubscribe_token is not None:
        # Same token table — the row exists ahead of the actual opt
        # out so the /api/unsubscribe/{token} GET / POST can resolve
        # the message. The opt-out itself only materialises as an
        # EmailUnsubscribe row once the recipient submits.
        persist_tracking_token(
            session, message_id=message.id, token=unsubscribe_token
        )
    record_event(
        session,
        message_id=message.id,
        event_type=EmailEventType.SENT,
        metadata={"to": to, "subject": subject},
        now=now,
    )
    return message


def process_history(
    session: Session,
    *,
    user_id: str,
    new_history_id: int | None = None,
) -> int:
    """Fetch the upstream history slice and import inbound messages
    delivered to an active alias. Returns the number of messages
    persisted.

    `new_history_id` es el `historyId` que llegó por el push de Pub/Sub;
    se usa como punto al que avanzar el cursor. El poller de respaldo
    (Parte D, sin push) lo pasa como None: en ese caso avanzamos al
    `historyId` que devuelve la propia `history.list`.
    """
    watch = session.scalar(
        select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == user_id)
    )
    if watch is None:
        logger.warning("gmail.process_history.no_watch user_id=%s", user_id)
        return 0

    client = _client_for(session, user_id)
    try:
        history = client.list_history(watch.history_id)
    except GoogleAuthExpiredError:
        logger.warning("gmail.process_history.auth_expired user_id=%s", user_id)
        # PR-OAuth-Permisos-Admin Item 12. Marcar needs_reconnect para
        # que el banner + digest avisen al user; antes solo se logueaba.
        from app.integrations.google_calendar.service import (  # noqa: PLC0415
            mark_needs_reconnect,
        )

        mark_needs_reconnect(session, user_id=user_id, error="invalid_grant")
        session.commit()
        return 0

    # CRM-GMAIL — captura universal. Ya no filtramos por «thread que el CRM
    # ya conoce»: guardamos cualquier mail dirigido a un alias ACTIVO (sea o
    # no un contacto conocido; `contact_id=NULL` si no casa). Además
    # sincronizamos la label SPAM sobre los mensajes ya almacenados.
    alias_map = active_alias_map(session)
    seen_messages = {
        m.gmail_message_id
        for m in session.scalars(
            select(EmailMessage).where(
                EmailMessage.gmail_account_user_id == user_id
            )
        )
    }

    # Late import: googleapiclient is heavy and tests sometimes
    # patch the whole gmail client out, so importing at module top
    # would create an import-order dependency.
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    imported = 0
    for entry in history.get("history", []):
        for added in entry.get("messagesAdded", []):
            msg_meta = added.get("message", {})
            mid = msg_meta.get("id")
            tid = msg_meta.get("threadId")
            stub_labels = msg_meta.get("labelIds") or []
            if not mid or not tid or mid in seen_messages:
                continue
            # Pre-filtro barato: si el stub trae labels y NO es INBOX,
            # SPAM ni SENT (DRAFT/CHAT), lo ignoramos sin gastar un
            # get_message. CRM-BACKFILL-SENT: SENT ahora también se
            # captura (mails enviados desde Gmail directo → outbound).
            # Cuando el stub no trae labels (p.ej. en tests) dejamos que
            # `_persist_message` decida por From-alias / `delivered_to`.
            if (
                stub_labels
                and "INBOX" not in stub_labels
                and "SPAM" not in stub_labels
                and "SENT" not in stub_labels
            ):
                continue
            if not alias_map:
                # Sin alias activos no hay nada que capturar; seguimos
                # procesando labelsAdded/Removed más abajo.
                continue
            try:
                full = client.get_message(mid)
                message = _persist_message(
                    session,
                    user_id=user_id,
                    raw=full,
                    gmail_thread_id=tid,
                    alias_map=alias_map,
                )
                seen_messages.add(mid)
                if message is not None:
                    imported += 1
            except HttpError as exc:
                gone_status = (
                    getattr(exc, "status_code", None)
                    or getattr(exc.resp, "status", None)
                )
                if gone_status in (404, 410):
                    # Message was deleted between Gmail's history.list
                    # and our get_message call — common with drafts,
                    # spam moves, Trash retention. Log and carry on;
                    # leaving the whole batch un-advanced because of
                    # one ghost message used to trap the watch on the
                    # same range forever.
                    logger.info(
                        "gmail.process_history.message_gone "
                        "user_id=%s msg=%s status=%s",
                        user_id,
                        mid,
                        gone_status,
                    )
                    continue
                logger.warning(
                    "gmail.process_history.fetch_failed "
                    "user_id=%s msg=%s status=%s",
                    user_id,
                    mid,
                    gone_status,
                    exc_info=True,
                )
                continue
            except Exception:  # noqa: BLE001
                logger.warning(
                    "gmail.process_history.persist_failed user_id=%s msg=%s",
                    user_id,
                    mid,
                    exc_info=True,
                )
                continue

        # Sync de spam: reclasificaciones de la label SPAM en Gmail se
        # reflejan sobre el mensaje ya almacenado (no lo ocultan, lo
        # marcan). `labelsAdded`/`labelsRemoved` traen la lista de labels
        # que cambiaron en `entry["labelIds"]`.
        for lbl_entry in entry.get("labelsAdded", []):
            _sync_spam_label(session, user_id=user_id, entry=lbl_entry, spam=True)
        for lbl_entry in entry.get("labelsRemoved", []):
            _sync_spam_label(session, user_id=user_id, entry=lbl_entry, spam=False)

    # Always advance the watch — even when every message in the
    # range failed individually. Otherwise a single ghost message
    # would trap us reprocessing the same history forever. El poller
    # (new_history_id None) avanza al historyId que reporta la respuesta.
    if new_history_id is not None:
        watch.history_id = new_history_id
    else:
        resp_history_id = history.get("historyId")
        if resp_history_id:
            watch.history_id = int(resp_history_id)
    session.flush()
    return imported


def _sync_spam_label(
    session: Session,
    *,
    user_id: str,
    entry: dict[str, Any],
    spam: bool,
) -> bool:
    """Aplica un cambio de label SPAM (add/remove) al `EmailMessage`
    correspondiente. `entry["labelIds"]` es el delta de labels; solo
    actuamos si incluye SPAM. Idempotente. Devuelve True si tocó una fila."""
    delta = entry.get("labelIds") or []
    if "SPAM" not in delta:
        return False
    mid = entry.get("message", {}).get("id")
    if not mid:
        return False
    message = session.scalar(
        select(EmailMessage).where(
            EmailMessage.gmail_account_user_id == user_id,
            EmailMessage.gmail_message_id == mid,
        )
    )
    if message is None:
        return False
    message.is_spam = spam
    full_labels = entry.get("message", {}).get("labelIds")
    if full_labels is not None:
        message.gmail_labels = json.dumps(full_labels)
    session.flush()
    return True


_NDR_FROM_PREFIXES = (
    "mailer-daemon@",
    "postmaster@",
    "noreply-daemon@",
    "noreply@bounces.",
    "mail-delivery-subsystem@",
    "mail-daemon@",
    "bounce@",
    "bounces@",
)

# Subject phrases that, by themselves, make us treat the message as a
# bounce. We match case-insensitive substrings (Spanish + English).
_NDR_SUBJECT_NEEDLES = (
    "delivery failed",
    "delivery status notification",
    "undelivered",
    "undeliverable",
    "returning message to sender",
    "could not be delivered",
    "failure notice",
    "no se ha podido entregar",
    "mensaje no entregado",
    "devolución del correo",
)


def _is_ndr(from_email: str, headers: dict[str, str]) -> bool:
    """Best-effort: classify an inbound message as a non-delivery
    report.

    We accept any of several independent signals — sender prefix,
    subject keywords, the `X-Failed-Recipients` header (Gmail / SES),
    `Auto-Submitted: auto-replied`, or a `Content-Type:
    multipart/report; report-type=delivery-status` boundary. A single
    hit is enough; the consequences (skipping inbound persistence,
    looking for the original) are conservative so over-detecting just
    means the operator doesn't see a bounce message in their thread,
    which is arguably an improvement.
    """
    if from_email and any(
        from_email.lower().startswith(p) for p in _NDR_FROM_PREFIXES
    ):
        return True
    if headers.get("x-failed-recipients"):
        return True
    auto = (headers.get("auto-submitted") or "").lower()
    if auto.startswith("auto-replied") or auto.startswith("auto-generated"):
        return True
    content_type = (headers.get("content-type") or "").lower()
    if (
        "multipart/report" in content_type
        and "delivery-status" in content_type
    ):
        return True
    subject = (headers.get("subject") or "").lower()
    if any(needle in subject for needle in _NDR_SUBJECT_NEEDLES):
        return True
    # An empty Return-Path (`<>`) is the SMTP convention for "this is a
    # bounce; do not bounce me back". It's only set on the envelope so
    # Gmail surfaces it as a header.
    if (headers.get("return-path") or "").strip() == "<>":
        return True
    return False


_NDR_FINAL_RE = re.compile(
    r"(?:final|original)-recipient:\s*rfc822\s*;\s*([^\s\r\n]+)",
    re.IGNORECASE,
)
_NDR_STATUS_RE = re.compile(
    r"status:\s*(\d\.\d+\.\d+)", re.IGNORECASE
)
_NDR_DIAG_RE = re.compile(
    r"diagnostic-code:\s*(.+?)(?:\r?\n(?:\S|$)|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# IONOS / kundenserver and Exim's classic "The following address(es)
# failed" body, plus generic `<addr>: reason` lines.
_NDR_FAILED_BLOCK_RE = re.compile(
    r"following\s+address\(?es\)?\s+failed:\s*\n+\s*([^\s<>,]+@[^\s<>,]+)",
    re.IGNORECASE,
)
_NDR_ANGLE_ADDR_RE = re.compile(
    r"<([^\s<>@]+@[^\s<>]+)>:\s*(.+?)$", re.IGNORECASE | re.MULTILINE
)


def _parse_ndr(
    headers: dict[str, str], body_text: str | None
) -> dict[str, Any]:
    """Extract failed recipient + reason from an NDR.

    Tries the three formats we see in the wild:
      - SMTP DSN (`Final-Recipient: rfc822;…`, `Status: 5.x.x`).
      - Gmail's `X-Failed-Recipients` header.
      - Postfix / Exim / IONOS text bodies that list `<addr>: reason`.

    Anything we can't pin down stays absent — empty result still
    surfaces as a bounce event keyed off the message we found, just
    without metadata.
    """
    info: dict[str, Any] = {}
    failed = headers.get("x-failed-recipients")
    if failed:
        info["failed_to"] = failed.split(",")[0].strip()
    haystack = body_text or ""
    if "failed_to" not in info:
        m = _NDR_FINAL_RE.search(haystack)
        if m:
            info["failed_to"] = m.group(1).strip("<>")
    if "failed_to" not in info:
        m = _NDR_FAILED_BLOCK_RE.search(haystack)
        if m:
            info["failed_to"] = m.group(1).strip("<>")
    if "failed_to" not in info:
        m = _NDR_ANGLE_ADDR_RE.search(haystack)
        if m:
            info["failed_to"] = m.group(1)
            # The reason often sits on the same line as the angle addr.
            info.setdefault(
                "reason", " ".join(m.group(2).split())[:200]
            )
    status_match = _NDR_STATUS_RE.search(haystack)
    if status_match:
        info["status"] = status_match.group(1)
    diag = _NDR_DIAG_RE.search(haystack + "\n ")
    if diag and "reason" not in info:
        info["reason"] = " ".join(diag.group(1).split())[:200]
    return info


def _find_bounced_message(
    session: Session,
    *,
    user_id: str,
    gmail_thread_id: str,
    failed_to: str | None,
) -> EmailMessage | None:
    """Locate the outbound EmailMessage whose recipient just bounced.

    Strategy: most NDRs land in the SAME Gmail thread as the original
    send (Gmail's threading heuristic matches Subject + References),
    so we walk this thread's outbound messages newest-first. As a
    fallback we look up by sender_account + recipient address.
    """
    thread = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if thread is not None:
        # Most recent outbound on the same thread. Pending
        # scheduled messages can't have bounced (they haven't
        # been sent), so we filter them out before the ORDER BY
        # to keep the comparison happy too.
        candidate = session.scalar(
            select(EmailMessage)
            .where(EmailMessage.thread_id == thread.id)
            .where(EmailMessage.direction == EmailDirection.OUTBOUND)
            .where(EmailMessage.sent_at.is_not(None))
            .order_by(EmailMessage.sent_at.desc())
        )
        if candidate is not None:
            return candidate
    if failed_to:
        # Fallback: any outbound from this user whose to_emails_json
        # contains the failed address. Case-insensitive substring is
        # enough; emails aren't case-sensitive on the local part by
        # convention.
        return session.scalar(
            select(EmailMessage)
            .where(EmailMessage.gmail_account_user_id == user_id)
            .where(EmailMessage.direction == EmailDirection.OUTBOUND)
            .where(EmailMessage.sent_at.is_not(None))
            .where(EmailMessage.to_emails_json.ilike(f"%{failed_to}%"))
            .order_by(EmailMessage.sent_at.desc())
        )
    return None


def compute_delivered_to(
    raw: dict[str, Any], alias_map: dict[str, str]
) -> str | None:
    """CRM-GMAIL — alias del CRM al que llegó el mail. Preferimos los headers
    Delivered-To / X-Original-To (fiables) y, si no, casamos To/Cc/Bcc contra
    los alias activos. Devuelve el alias en su forma canónica o None. Es la
    MISMA lógica que aplica `_persist_inbound`, extraída para que el backfill
    universal clasifique idéntico (Parte C del spec)."""
    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    candidates = [
        addr.strip()
        for addr in (headers.get("delivered-to"), headers.get("x-original-to"))
        if addr
    ]
    for key in ("to", "cc", "bcc"):
        header_val = headers.get(key)
        if header_val:
            candidates += [a for _, a in getaddresses([header_val]) if a]
    return resolve_delivered_to(candidates, alias_map)


def sender_alias_of(
    raw: dict[str, Any], alias_map: dict[str, str]
) -> str | None:
    """CRM-BACKFILL-SENT — alias activo del CRM en el `From` del mensaje
    (forma canónica) o None. Es el trigger de `direction=outbound`: la misma
    comprobación que hace `_persist_message`, extraída para que el backfill
    universal clasifique idéntico ANTES de decidir persistir."""
    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    from_addresses = getaddresses([headers.get("from") or ""])
    from_email = from_addresses[0][1] if from_addresses else ""
    return resolve_delivered_to([from_email], alias_map)


def primary_recipient(raw: dict[str, Any]) -> str | None:
    """Destinatario «principal» (lowercased) para el report de alias
    descartados: Delivered-To / X-Original-To, o el primer To."""
    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    for key in ("delivered-to", "x-original-to", "to"):
        val = headers.get(key)
        if val:
            parsed = getaddresses([val])
            if parsed and parsed[0][1]:
                return parsed[0][1].lower()
    return None


def _persist_message(
    session: Session,
    *,
    user_id: str,
    raw: dict[str, Any],
    gmail_thread_id: str,
    alias_map: dict[str, str] | None = None,
    dry_run: bool = False,
    emit_activity: bool = True,
    imported_via: str = "incoming_realtime",
) -> EmailMessage | None:
    """Persiste un mensaje capturado de Gmail — inbound U outbound.

    CRM-BACKFILL-SENT: antes solo manejaba inbound (`_persist_inbound`).
    Ahora, si el `From` es un alias activo del CRM, el mensaje se guarda
    como OUTBOUND (mail enviado desde Gmail directo, no desde el
    compositor): el dueño del alias es el propietario, `delivered_to` no
    aplica y el contacto se casa por los destinatarios."""
    headers = _index_headers(raw.get("payload", {}).get("headers", []))
    from_header = headers.get("from") or ""
    to_header = headers.get("to") or ""
    cc_header = headers.get("cc")
    bcc_header = headers.get("bcc")
    subject = headers.get("subject")
    sent_at = _parse_date(headers.get("date")) or datetime.now(UTC)

    from_addresses = getaddresses([from_header])
    from_name = from_addresses[0][0] if from_addresses else None
    from_email = from_addresses[0][1] if from_addresses else ""
    to_emails = [addr for _, addr in getaddresses([to_header]) if addr]
    cc_emails = [addr for _, addr in getaddresses([cc_header])] if cc_header else None
    bcc_emails = (
        [addr for _, addr in getaddresses([bcc_header])] if bcc_header else None
    )
    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    labels = raw.get("labelIds") or []
    # CRM-BACKFILL-SENT — detección de dirección. Si el From es un alias
    # activo → OUTBOUND. Cubre también el auto-forward / CC a uno mismo
    # (From alias Y To alias): outbound gana.
    sender_alias = (
        resolve_delivered_to([from_email], alias_map)
        if alias_map is not None
        else None
    )
    is_outbound = sender_alias is not None

    delivered_to: str | None = None
    owner_user_id: str | None = None
    if is_outbound:
        owner_user_id = session.scalar(
            select(UserEmailAlias.user_id).where(
                UserEmailAlias.alias_email == sender_alias,
                UserEmailAlias.active.is_(True),
            )
        )
    else:
        # CRM-GMAIL — captura universal. `delivered_to` = alias del CRM al
        # que llegó el mail (misma lógica compartida con el backfill vía
        # `compute_delivered_to`). Si el mail no va a ninguno de nuestros
        # alias, no es nuestro → no lo guardamos (solo cuando el caller pasa
        # `alias_map`; un llamador legacy sin alias_map conserva el
        # comportamiento anterior).
        delivered_to = (
            compute_delivered_to(raw, alias_map) if alias_map is not None else None
        )
        if alias_map is not None and delivered_to is None:
            return None

    # Sprint Email v2.3a — NDR detection. When this looks like a
    # bounce we attach the event to the ORIGINAL outbound message and
    # SKIP persisting the NDR itself: the operator doesn't want a
    # "Mail delivery failed" row cluttering their thread. The original
    # send still lives in the thread and now has a BOUNCE event next
    # to it, which is what the timeline UI surfaces.
    if not is_outbound and _is_ndr(from_email, headers):
        # El backfill (emit_activity=False) y el dry-run NO adjuntan eventos
        # de bounce a mensajes históricos: solo señalan «nada que insertar».
        if emit_activity and not dry_run:
            ndr = _parse_ndr(headers, body_text)
            original = _find_bounced_message(
                session,
                user_id=user_id,
                gmail_thread_id=gmail_thread_id,
                failed_to=ndr.get("failed_to"),
            )
            from app.email_tracking.services import record_event  # noqa: PLC0415
            from app.models.crm import EmailEventType  # noqa: PLC0415

            if original is not None:
                record_event(
                    session,
                    message_id=original.id,
                    event_type=EmailEventType.BOUNCE,
                    metadata={
                        **(ndr or {}),
                        "from": from_email,
                        "subject": subject,
                    },
                )
                session.commit()
            else:
                logger.info(
                    "gmail.ndr.original_not_found user=%s subject=%r failed_to=%s",
                    user_id,
                    (subject or "")[:80],
                    ndr.get("failed_to"),
                )
        # Signal the caller: nothing to insert.
        return None

    if is_outbound:
        # OUTBOUND: el contacto se casa por los destinatarios (To y Cc),
        # no por el From (que es nuestro alias).
        contact = None
        for addr in [*to_emails, *(cc_emails or [])]:
            contact = session.scalar(
                select(Contact).where(Contact.email == addr)
            )
            if contact is not None:
                break
    else:
        contact = session.scalar(
            select(Contact).where(Contact.email == from_email)
        )

    thread = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if thread is None and not dry_run:
        # Should not happen en real-time (process_history) pero sí en el
        # backfill universal de un thread nunca visto. En dry-run NO creamos
        # el thread (sería una escritura): el outcome linked/orphan solo
        # depende del contacto.
        #
        # CRM-BACKFILL-SENT: en un thread nuevo iniciado por un mail
        # enviado, el propietario es el DUEÑO del alias del From (así el
        # comercial lo ve en su bandeja via initiated_by), no la cuenta org.
        thread = _get_or_create_thread(
            session,
            gmail_account_user_id=user_id,
            gmail_thread_id=gmail_thread_id,
            initiated_by_user_id=(
                owner_user_id if is_outbound and owner_user_id else user_id
            ),
            contact_id=contact.id if contact else None,
            subject=subject,
            first_message_at=sent_at,
            participants=[from_email, *to_emails],
        )

    message = EmailMessage(
        thread_id=thread.id if thread is not None else "dry-run",
        gmail_message_id=raw["id"],
        gmail_account_user_id=user_id,
        direction=(
            EmailDirection.OUTBOUND if is_outbound else EmailDirection.INBOUND
        ),
        from_email=from_email,
        from_name=from_name,
        to_emails_json=json.dumps(to_emails),
        cc_emails_json=json.dumps(cc_emails) if cc_emails else None,
        bcc_emails_json=json.dumps(bcc_emails) if bcc_emails else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=raw.get("snippet"),
        sent_at=sent_at,
        contact_id=contact.id if contact else None,
        created_by_user_id=owner_user_id if is_outbound else None,
        delivered_to=delivered_to,
        is_spam="SPAM" in labels,
        gmail_labels=json.dumps(labels) if labels else None,
        imported_via=imported_via,
    )
    # dry-run: devolvemos el mensaje TRANSITORIO (sin persistir) para que el
    # caller clasifique el resultado; no tocamos la sesión.
    if dry_run:
        return message
    session.add(message)
    thread.last_message_at = sent_at
    thread.message_count = (thread.message_count or 0) + 1
    # Un mail ENVIADO por nosotros no marca el hilo como «no leído».
    if not is_outbound:
        thread.has_unread_replies = True
    session.flush()
    # Mirror the reply onto the contact's activity timeline. Skipped when el
    # inbound vino de una dirección desconocida (sin contact_id), cuando el
    # caller pide no emitir actividad (backfill histórico: no re-disparar
    # workflows ni ensuciar el timeline con correo viejo), o cuando el
    # mensaje es OUTBOUND capturado (no es una «respuesta recibida» — no
    # debe disparar email.crm.replied ni el evento reply_received).
    if emit_activity and contact is not None and not is_outbound:
        _emit_inbound_activity(
            session,
            contact_id=contact.id,
            thread_id=thread.id,
            message_id=message.id,
            subject=subject,
            from_email=from_email,
            snippet=raw.get("snippet"),
            occurred_at=sent_at,
        )
    return message


# Alias retro-compatible: el nombre histórico sigue funcionando para
# cualquier caller/test que lo referencie.
_persist_inbound = _persist_message


def _emit_inbound_activity(
    session: Session,
    *,
    contact_id: str,
    thread_id: str,
    message_id: str,
    subject: str | None,
    from_email: str,
    snippet: str | None,
    occurred_at: datetime,
) -> None:
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    session.add(
        ActivityEvent(
            contact_id=contact_id,
            system="crm",
            account_id="emails",
            external_id=f"email:{message_id}:reply_received",
            event_type="email.reply_received",
            subject=(subject or "")[:200],
            body=(snippet or "")[:200] or None,
            metadata_json=json.dumps(
                {
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "from_email": from_email,
                    "snippet": (snippet or "")[:300],
                    "direction": "inbound",
                },
                default=str,
            ),
            occurred_at=occurred_at,
            synced_at=datetime.now(UTC),
        )
    )
    # Sprint Workflows. Productor de `email.crm.replied`. Payload sin
    # owner_user_id (no disponible aquí) — el matcher lo tolera.
    try:
        from app.workflows.dispatcher import dispatch_event  # noqa: PLC0415

        dispatch_event(
            session,
            "email.crm.replied",
            contact_id,
            {"source": "crm_inbound", "thread_id": thread_id},
        )
    except Exception:  # noqa: BLE001
        logger.warning("gmail.inbound workflow dispatch failed", exc_info=True)


def _get_or_create_thread(
    session: Session,
    *,
    gmail_account_user_id: str,
    gmail_thread_id: str,
    initiated_by_user_id: str,
    contact_id: str | None,
    subject: str | None,
    first_message_at: datetime,
    participants: list[str],
) -> EmailThread:
    existing = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == gmail_account_user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if existing is not None:
        return existing
    thread = EmailThread(
        contact_id=contact_id,
        initiated_by_user_id=initiated_by_user_id,
        gmail_thread_id=gmail_thread_id,
        gmail_account_user_id=gmail_account_user_id,
        subject=subject,
        participants_json=json.dumps(sorted(set(participants))),
        first_message_at=first_message_at,
        last_message_at=first_message_at,
        message_count=0,
    )
    session.add(thread)
    session.flush()
    return thread


def register_watch(session: Session, *, user_id: str) -> GmailPubsubWatch:
    """Register a Gmail Push Notifications watch + persist the
    bookkeeping row. Idempotent — re-registering updates the
    expiry."""
    settings = get_settings()
    if not settings.gmail_pubsub_topic:
        raise RuntimeError(
            "GMAIL_PUBSUB_TOPIC not configured — set it in .env to enable Gmail"
            " push notifications."
        )
    client = _client_for(session, user_id)
    # CRM-GMAIL: vigilar INBOX + SPAM para recibir push tanto de mail
    # nuevo como de reclasificaciones de spam (sync de is_spam).
    # CRM-BACKFILL-SENT: + SENT para que los mails enviados desde Gmail
    # directo aparezcan en el CRM en tiempo real (<10s), no solo por
    # backfill. Tras deploy hay que re-registrar el watch (CLI
    # register_watch) para que Gmail empiece a empujar SENT.
    response = client.watch_mailbox(
        settings.gmail_pubsub_topic, label_ids=["INBOX", "SPAM", "SENT"]
    )
    history_id = int(response.get("historyId", 0))
    expiration_ms = int(response.get("expiration", 0))
    expires_at = datetime.fromtimestamp(expiration_ms / 1000, tz=UTC)
    now = datetime.now(UTC)
    watch = session.scalar(
        select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == user_id)
    )
    if watch is None:
        watch = GmailPubsubWatch(
            user_id=user_id,
            history_id=history_id,
            watch_expires_at=expires_at,
            last_renewed_at=now,
            topic_name=settings.gmail_pubsub_topic,
        )
        session.add(watch)
    else:
        watch.history_id = history_id
        watch.watch_expires_at = expires_at
        watch.last_renewed_at = now
        watch.topic_name = settings.gmail_pubsub_topic
    session.flush()
    return watch


def unregister_watch(session: Session, *, user_id: str) -> bool:
    """CRM-GMAIL — para el Watch en Gmail y borra la fila de bookkeeping.
    Para cleanup / migraciones. Devuelve True si había un watch."""
    watch = session.scalar(
        select(GmailPubsubWatch).where(GmailPubsubWatch.user_id == user_id)
    )
    try:
        client = _client_for(session, user_id)
        client.stop_watch()
    except Exception:  # noqa: BLE001
        logger.warning("gmail.unregister_watch stop failed", exc_info=True)
    if watch is not None:
        session.delete(watch)
        session.flush()
        return True
    return False


# ---------------------------------------------------------------------------
# Helpers

def _snippet(text: str | None, html: str | None, max_chars: int = 200) -> str | None:
    """Plain-text snippet for inbox + activity-timeline previews.

    `text` (multipart text body) is preferred when present. When the
    only body we have is HTML — every TinyMCE-authored send now
    (`body_text=null`) — we route it through `extract_text_from_html`
    so the CSS reset block + `<style>` boilerplate the editor adds
    don't bleed into the preview as raw CSS source. Without that
    pass, the snippet for a fresh send rendered as e.g.
    `<style>body,table,td,p,a,h1,h2,h3,h4{margin:0;…` instead of the
    actual first sentence the operator typed.
    """
    if text and text.strip():
        flat = " ".join(text.split())
        return flat[:max_chars] or None
    if html:
        # Local import — `extract_text_from_html` lives in the
        # email_templates module and pulls SQLAlchemy via its
        # neighbours; deferring keeps the gmail.service import graph
        # the same as before.
        from app.email_templates.services import (  # noqa: PLC0415
            extract_text_from_html,
        )

        clean = extract_text_from_html(html)
        if clean:
            return clean[:max_chars]
    return None


def _index_headers(headers: list[dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in headers:
        name = h.get("name", "").lower()
        if name and "value" in h:
            out[name] = h["value"]
    return out


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Walk the MIME payload tree, prefer text/plain + text/html."""
    text: str | None = None
    html: str | None = None
    queue: list[dict[str, Any]] = [payload]
    while queue:
        part = queue.pop()
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            decoded = _b64decode(data)
            if mime == "text/plain" and text is None:
                text = decoded
            elif mime == "text/html" and html is None:
                html = decoded
        for child in part.get("parts", []) or []:
            queue.append(child)
    return text, html


def _b64decode(data: str) -> str:
    import base64  # noqa: PLC0415

    try:
        return base64.urlsafe_b64decode(data.encode()).decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
