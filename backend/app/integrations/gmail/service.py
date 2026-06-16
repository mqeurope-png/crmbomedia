"""High-level Gmail operations.

The route layer + worker layer call these. Each function takes a
SQLAlchemy session and is responsible for its own flushes; the
caller decides when to commit.
"""
from __future__ import annotations

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
)

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


def import_gmail_templates_with_tpl_prefix(
    session: Session,
    *,
    user_id: str,
    created_by_user_id: str,
    delete_after: bool = False,
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

    Devuelve `{imported, skipped, errors, deleted, total_drafts_
    scanned, tpl_drafts_found}`.
    """
    import base64  # noqa: PLC0415
    from email import message_from_bytes  # noqa: PLC0415
    from email.policy import default as _default_policy  # noqa: PLC0415

    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
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

    # Set de names ya existentes en esta folder para idempotencia O(1).
    existing_names = {
        row.name
        for row in session.scalars(
            select(EmailTemplate).where(EmailTemplate.folder_id == folder_id)
        )
    }

    all_draft_ids = client.list_all_drafts()
    counters = {
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "deleted": 0,
        "total_drafts_scanned": len(all_draft_ids),
        "tpl_drafts_found": 0,
    }

    for draft_id in all_draft_ids:
        # Paso 1: metadata para inspeccionar Subject. Saltamos los que
        # no tengan prefijo [TPL] para no bajar el raw inútilmente.
        try:
            meta = client.get_draft_metadata(draft_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.import.meta failed draft_id=%s err=%s",
                draft_id,
                exc,
            )
            counters["errors"] += 1
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
            continue
        if name in existing_names:
            counters["skipped"] += 1
            continue

        # Paso 2: full raw para extraer body_html.
        try:
            full = client.get_draft_template(draft_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gmail.import.get failed draft_id=%s err=%s",
                draft_id,
                exc,
            )
            counters["errors"] += 1
            continue
        raw_b64 = (full.get("message") or {}).get("raw")
        body_html = ""
        if raw_b64:
            try:
                raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
                parsed = message_from_bytes(raw_bytes, policy=_default_policy)
                html_part = parsed.get_body(preferencelist=("html",))
                plain_part = parsed.get_body(preferencelist=("plain",))
                if html_part is not None:
                    body_html = html_part.get_content() or ""
                elif plain_part is not None:
                    text = plain_part.get_content() or ""
                    body_html = "<p>" + text.replace("\n", "<br>") + "</p>"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gmail.import.parse failed draft_id=%s err=%s",
                    draft_id,
                    exc,
                )
        if not body_html.strip():
            counters["errors"] += 1
            continue

        template = EmailTemplate(
            name=name,
            subject=name,  # útil como subject default al elegir el template
            body_html=body_html,
            folder_id=folder_id,
            is_global=True,
            owner_user_id=created_by_user_id,
        )
        session.add(template)
        session.flush()
        existing_names.add(name)
        counters["imported"] += 1

        if delete_after:
            try:
                client.delete_draft(draft_id)
                counters["deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gmail.import.delete failed draft_id=%s err=%s",
                    draft_id,
                    exc,
                )

    return counters


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
) -> EmailMessage:
    """Send a new outbound email and persist the thread + message rows.

    `in_reply_to_message_id` is OUR `EmailMessage.id`; when set we
    look up the upstream Gmail thread + headers so the recipient's
    client recognises the reply.
    """
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
    new_history_id: int,
) -> int:
    """Fetch the upstream history slice and import inbound messages
    that land in a CRM-initiated thread. Returns the number of
    messages persisted.
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
        return 0

    crm_thread_ids = {
        t.gmail_thread_id
        for t in session.scalars(
            select(EmailThread).where(
                EmailThread.gmail_account_user_id == user_id
            )
        )
    }
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
            if not mid or not tid or tid not in crm_thread_ids:
                continue
            if mid in seen_messages:
                continue
            try:
                full = client.get_message(mid)
                _persist_inbound(
                    session,
                    user_id=user_id,
                    raw=full,
                    gmail_thread_id=tid,
                )
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

    # Always advance the watch — even when every message in the
    # range failed individually. Otherwise a single ghost message
    # would trap us reprocessing the same history forever.
    watch.history_id = new_history_id
    session.flush()
    return imported


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


def _persist_inbound(
    session: Session,
    *,
    user_id: str,
    raw: dict[str, Any],
    gmail_thread_id: str,
) -> EmailMessage | None:
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
    cc_emails = [addr for _, addr in getaddresses([cc_header])] if cc_header else None
    body_text, body_html = _extract_bodies(raw.get("payload", {}))

    # Sprint Email v2.3a — NDR detection. When this looks like a
    # bounce we attach the event to the ORIGINAL outbound message and
    # SKIP persisting the NDR itself: the operator doesn't want a
    # "Mail delivery failed" row cluttering their thread. The original
    # send still lives in the thread and now has a BOUNCE event next
    # to it, which is what the timeline UI surfaces.
    if _is_ndr(from_email, headers):
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

    contact = session.scalar(
        select(Contact).where(Contact.email == from_email)
    )

    thread = session.scalar(
        select(EmailThread).where(
            EmailThread.gmail_account_user_id == user_id,
            EmailThread.gmail_thread_id == gmail_thread_id,
        )
    )
    if thread is None:
        # Should not happen — process_history filters by known
        # threads — but stay defensive.
        thread = _get_or_create_thread(
            session,
            gmail_account_user_id=user_id,
            gmail_thread_id=gmail_thread_id,
            initiated_by_user_id=user_id,
            contact_id=contact.id if contact else None,
            subject=subject,
            first_message_at=sent_at,
            participants=[from_email, *to_emails],
        )

    message = EmailMessage(
        thread_id=thread.id,
        gmail_message_id=raw["id"],
        gmail_account_user_id=user_id,
        direction=EmailDirection.INBOUND,
        from_email=from_email,
        from_name=from_name,
        to_emails_json=json.dumps(to_emails),
        cc_emails_json=json.dumps(cc_emails) if cc_emails else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        snippet=raw.get("snippet"),
        sent_at=sent_at,
        contact_id=contact.id if contact else None,
    )
    session.add(message)
    thread.last_message_at = sent_at
    thread.message_count = (thread.message_count or 0) + 1
    thread.has_unread_replies = True
    session.flush()
    # Mirror the reply onto the contact's activity timeline so the
    # ficha de contacto picks it up alongside the outbound sends and
    # the rest of the activity. Skipped when the inbound came from
    # an unknown address (no contact_id).
    if contact is not None:
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
    response = client.watch_mailbox(settings.gmail_pubsub_topic)
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
