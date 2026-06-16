"""Email endpoints — send, list threads, thread detail, admin list.

Sprint Email v1. Mounted at `/api/emails`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import Action, record_event
from app.core.auth import require_admin, require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.email_templates.services import replace_merge_vars
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.service import (
    GmailNotConnectedError,
    GmailScopeMissingError,
)
from app.models.crm import (
    Contact,
    EmailEventType,
    EmailMessage,
    EmailMessageEvent,
    EmailThread,
    EmailThreadLabel,
    EmailThreadState,
    User,
    UserEmailAliasPref,
    UserRole,
)
from app.schemas.emails import (
    AliasPreferencesPayload,
    EmailAlias,
    EmailMessageRead,
    EmailSendRequest,
    EmailThreadDetail,
    EmailThreadList,
    EmailThreadRead,
    GmailTemplate,
    MyAlias,
)

router = APIRouter(prefix="/api/emails", tags=["emails"])
logger = logging.getLogger(__name__)


def _emit_activity(
    session: Session,
    *,
    contact_id: str | None,
    event_type: str,
    subject: str | None,
    metadata: dict[str, str | int | None],
    occurred_at: datetime,
    body: str | None = None,
) -> None:
    """Mirror an email mutation into the contact's activity timeline
    when we know which contact it belongs to."""
    if not contact_id:
        return
    from app.models.crm import ActivityEvent  # noqa: PLC0415

    session.add(
        ActivityEvent(
            contact_id=contact_id,
            system="crm",
            account_id="emails",
            external_id=(
                f"email:{metadata.get('message_id') or metadata.get('thread_id')}"
                f":{event_type}"
            ),
            event_type=event_type,
            subject=(subject or "")[:200],
            body=(body or "")[:200] or None,
            metadata_json=json.dumps(metadata, default=str),
            occurred_at=occurred_at,
            synced_at=datetime.now(UTC),
        )
    )


@router.get("/gmail-templates")
def list_gmail_templates(
    q: str | None = Query(
        default=None,
        description=(
            "Override del query de búsqueda Gmail (`label:foo`, etc.). "
            "Por defecto pedimos todos los drafts y filtramos en "
            "código por `labelIds` que matchean canned-response/"
            "template."
        ),
    ),
    limit: int = Query(default=30, ge=1, le=100),
    debug: bool = Query(
        default=False,
        description=(
            "Modo diagnóstico: devuelve TODOS los drafts del user "
            "con `label_ids` + `thread_id` para identificar el patrón "
            "real de templates en cuentas con setup raro. Body queda "
            "vacío para no inflar el payload."
        ),
    ),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[dict]:
    """Plantillas Gmail nativas del user (canned responses / templates).

    El producto Gmail los expone como drafts con un label sistema
    `^smartlabel_*` (Google ha cambiado el nombre exacto varias veces).
    Listamos todos los drafts y filtramos en código por `labelIds` —
    el query `label:^smartlabel_canned_response` no matchea fiable.

    Sin cache — fetch on-demand cuando la UI abre el dropdown.
    """
    try:
        items = gmail_service.list_gmail_templates(
            session,
            current_user.id,
            query=q,
            max_results=limit,
            debug=debug,
        )
    except GmailNotConnectedError:
        return []
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    if debug:
        # Modo debug devuelve el shape ampliado tal cual.
        return [
            {
                "id": it.get("id"),
                "subject": it.get("subject"),
                "snippet": it.get("snippet"),
                "label_ids": it.get("label_ids"),
                "thread_id": it.get("thread_id"),
                "updated_at": it.get("updated_at").isoformat()
                if it.get("updated_at")
                else None,
            }
            for it in items
        ]
    return [GmailTemplate.model_validate(item).model_dump() for item in items]


@router.get("/aliases", response_model=list[EmailAlias])
def list_aliases(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailAlias]:
    """All verified "Send mail as" aliases from Gmail, enriched
    with the current user's CRM preferences."""
    try:
        items = gmail_service.list_aliases(session, current_user.id)
    except GmailNotConnectedError:
        return []
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    prefs = _prefs_index(session, current_user.id)
    return [
        EmailAlias(
            send_as_email=item["send_as_email"],
            display_name=item["display_name"],
            is_primary=item["is_primary"],
            is_default=item["is_default"],
            verification_status=item.get("verification_status"),
            user_pref_allowed=prefs.get(item["send_as_email"], (False, False))[0],
            user_pref_default=prefs.get(item["send_as_email"], (False, False))[1],
        )
        for item in items
    ]


@router.get("/my-aliases", response_model=list[MyAlias])
def my_aliases(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[MyAlias]:
    """Only the aliases the user marked as allowed, used by the
    composer dropdown. Cross-checked against the live Gmail list
    so an alias removed from Gmail doesn't keep showing up."""
    try:
        items = gmail_service.list_aliases(session, current_user.id)
    except (GmailNotConnectedError, GmailScopeMissingError):
        return []
    available = {it["send_as_email"]: it for it in items}
    prefs = _prefs_index(session, current_user.id)
    out: list[MyAlias] = []
    for alias_email, (allowed, is_default) in prefs.items():
        if not allowed:
            continue
        if alias_email not in available:
            continue
        out.append(
            MyAlias(
                send_as_email=alias_email,
                display_name=available[alias_email]["display_name"],
                is_default=is_default,
            )
        )
    out.sort(key=lambda a: (not a.is_default, a.send_as_email))
    return out


@router.put("/aliases/preferences", response_model=list[EmailAlias])
def upsert_alias_preferences(
    payload: AliasPreferencesPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailAlias]:
    """Upsert the user's alias preferences in one shot.

    Semantics:
    - `is_allowed=true` upserts the row.
    - `is_allowed=false` deletes the row (keep the table clean).
    - Setting `is_default=true` on one row demotes the other
      defaults to false inside the same transaction.
    """
    existing = {
        p.alias_email: p
        for p in session.scalars(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == current_user.id
            )
        )
    }
    incoming_default: str | None = None
    for item in payload.preferences:
        row = existing.get(item.alias_email)
        if not item.is_allowed:
            if row is not None:
                session.delete(row)
            continue
        if item.is_default:
            incoming_default = item.alias_email
        if row is None:
            session.add(
                UserEmailAliasPref(
                    user_id=current_user.id,
                    alias_email=item.alias_email,
                    is_allowed=True,
                    is_default=item.is_default,
                )
            )
        else:
            row.is_allowed = True
            row.is_default = item.is_default
    session.flush()
    if incoming_default is not None:
        session.execute(
            UserEmailAliasPref.__table__.update()  # type: ignore[attr-defined]
            .where(
                UserEmailAliasPref.user_id == current_user.id,
                UserEmailAliasPref.alias_email != incoming_default,
            )
            .values(is_default=False)
        )
    _ = request
    session.commit()
    # Re-render the enriched list so the UI doesn't need a second
    # round-trip.
    return list_aliases(session=session, current_user=current_user)


def _prefs_index(
    session: Session, user_id: str
) -> dict[str, tuple[bool, bool]]:
    rows = session.scalars(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == user_id
        )
    )
    return {r.alias_email: (r.is_allowed, r.is_default) for r in rows}


@router.post("/send", response_model=EmailMessageRead, status_code=201)
def send_email(
    payload: EmailSendRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailMessageRead:
    # Sprint Email v2.4e — `scheduled_for` in the future routes the
    # message through the pending queue instead of Gmail. We still
    # need the alias-preference check below; the Gmail-scope check
    # is only relevant for an immediate send (the sweep re-validates
    # later) so the scheduled path skips it on purpose, otherwise
    # an operator who hasn't completed the OAuth flow can't queue a
    # send for "in 10 minutes" while they go grant the scope.
    scheduled_future = (
        payload.scheduled_for is not None
        and (
            payload.scheduled_for
            if payload.scheduled_for.tzinfo
            else payload.scheduled_for.replace(tzinfo=UTC)
        )
        > datetime.now(UTC)
    )

    if not scheduled_future:
        # The user must have Gmail connected with the send scope before
        # we even bother to check preferences — `_client_for` inside
        # gmail_service.send_email raises GmailScopeMissingError, but
        # we'd rather catch the scope problem here than return the
        # less-helpful pref error.
        from app.integrations.gmail.service import _client_for  # noqa: PLC0415

        try:
            _client_for(session, current_user.id)
        except GmailNotConnectedError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except GmailScopeMissingError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc

    # Validate that the alias is in the user's allowed preferences.
    # Blocks an operator from spoofing an alias their colleague
    # configured but they didn't opt into.
    pref = session.scalar(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == current_user.id,
            UserEmailAliasPref.alias_email == payload.from_alias,
            UserEmailAliasPref.is_allowed.is_(True),
        )
    )
    if pref is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El alias seleccionado no está en tus preferencias. "
                "Márcalo desde /account."
            ),
        )

    # Sprint Email v2.3a — block the send when the contact already
    # opted out. We check for any unsubscribe row whose scope covers
    # marketing (the default scope of the One-Click footer) or `all`
    # (a manual flag set by the operator). Transactional-only opt-outs
    # don't block a 1-a-1 mail.
    if payload.contact_id is not None:
        from app.email_tracking.services import (  # noqa: PLC0415
            contact_is_unsubscribed,
        )

        opted_out = contact_is_unsubscribed(
            session, payload.contact_id
        )
        if opted_out is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Este contacto se ha dado de baja de tus envíos "
                    "comerciales. Edita su preferencia en la ficha del "
                    "contacto si crees que es un error."
                ),
            )

    # Sprint Email v2.2b — substitute `{nombre}` / `{empresa}` /
    # `{email}` placeholders against the contact this email is being
    # sent to. When no contact is attached the body ships unchanged
    # (placeholders stay literal so the operator can see what slipped
    # through).
    body_html = payload.body_html
    body_text = payload.body_text
    subject = payload.subject
    if payload.contact_id is not None:
        contact = session.get(Contact, payload.contact_id)
        if contact is not None:
            body_html = replace_merge_vars(body_html, contact)
            body_text = replace_merge_vars(body_text, contact)
            subject = replace_merge_vars(subject, contact) or ""

    # Sprint Email v2.3a — `include_unsubscribe` falls back to the
    # operator's stored default when the modal didn't ship a value.
    include_unsubscribe = (
        payload.include_unsubscribe
        if payload.include_unsubscribe is not None
        else current_user.email_include_unsubscribe_default
    )

    if scheduled_future:
        # Pending row + early return. We skip activity timeline
        # emission and the audit `EMAIL_SENT_FROM_CRM` record on
        # purpose — those represent the actual send, which the
        # sweep will perform later. include_unsubscribe is dropped
        # here too; the sweep re-derives it at send time so a
        # changed operator preference in the meantime takes effect.
        from app.api.emails_scheduled import (  # noqa: PLC0415
            persist_scheduled_message,
        )

        scheduled_msg = persist_scheduled_message(
            session,
            sender_user_id=current_user.id,
            scheduled_for=payload.scheduled_for,  # type: ignore[arg-type]
            from_alias=payload.from_alias,
            from_name=payload.from_name,
            to=list(payload.to),
            cc=list(payload.cc) if payload.cc else None,
            bcc=list(payload.bcc) if payload.bcc else None,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            contact_id=payload.contact_id,
            in_reply_to_message_id=payload.in_reply_to_message_id,
        )
        session.commit()
        session.refresh(scheduled_msg)
        return _message_read(scheduled_msg)

    try:
        message = gmail_service.send_email(
            session,
            sender_user_id=current_user.id,
            from_alias=payload.from_alias,
            from_name=payload.from_name,
            to=list(payload.to),
            cc=list(payload.cc) if payload.cc else None,
            bcc=list(payload.bcc) if payload.bcc else None,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            contact_id=payload.contact_id,
            in_reply_to_message_id=payload.in_reply_to_message_id,
            include_unsubscribe=include_unsubscribe,
        )
    except GmailNotConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except GmailScopeMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    _emit_activity(
        session,
        contact_id=payload.contact_id,
        event_type="email.sent_from_crm",
        subject=subject,
        body=message.snippet or (body_text or "")[:200] or None,
        metadata={
            "message_id": message.id,
            "thread_id": message.thread_id,
            "direction": "outbound",
            "from_email": message.from_email,
            "to": ", ".join(payload.to)[:200],
            "snippet": message.snippet or "",
        },
        occurred_at=message.sent_at,
    )
    record_event(
        session,
        action=Action.EMAIL_SENT_FROM_CRM,
        target_type="email_message",
        target_id=message.id,
        actor=current_user,
        metadata={
            "to": list(payload.to),
            "thread_id": message.thread_id,
            "contact_id": payload.contact_id,
        },
        request=request,
    )
    session.commit()
    session.refresh(message)
    return _message_read(message)


@router.get("/threads", response_model=EmailThreadList)
def list_threads(
    contact_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="LIKE on subject / from / snippet"),
    # v2.4a: mailbox filters. `state` defaults to inbox so the old
    # behaviour (no filter = active inbox) is preserved; pass
    # `state=archived` etc. to see the other boxes. `folder_id` ===
    # "inbox" means "no folder set" (top-level bandeja); a real UUID
    # restricts to that folder. `include_snoozed` flips the snooze
    # filter off so the dedicated "Pospuestos" view can see them.
    state: str | None = Query(
        default=None, pattern="^(inbox|archived|trashed|spam|sent)$"
    ),
    folder_id: str | None = Query(default=None),
    label_id: str | None = Query(default=None),
    starred: bool | None = Query(default=None),
    has_unread: bool | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    include_snoozed: bool = Query(default=False),
    # QoL sprint — toggle "Mías ↔ Todo el equipo" del listing /emails.
    # Pre-QoL: el manager veía TODOS los threads por defecto (overload).
    # Post-QoL: el manager por defecto ve solo los suyos (`mine`); con
    # `scope=team` ve los del equipo entero (o de un user concreto via
    # `team_user_id`). Manager puede LEER otros threads, pero el envío
    # sigue usando el Gmail integration del propio caller.
    scope: str = Query(default="mine", pattern="^(mine|team)$"),
    team_user_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailThreadList:
    stmt = select(EmailThread).options(selectinload(EmailThread.labels))
    if contact_id:
        stmt = stmt.where(EmailThread.contact_id == contact_id)
    is_privileged = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)
    if scope == "team":
        if not is_privileged:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo manager+ puede ver emails del equipo.",
            )
        if team_user_id:
            stmt = stmt.where(
                EmailThread.initiated_by_user_id == team_user_id
            )
        # else: no filter → todos los threads del equipo.
    else:  # scope == "mine" (default)
        stmt = stmt.where(
            EmailThread.initiated_by_user_id == current_user.id
        )
    # State default = INBOX so the legacy list view keeps its
    # narrow scope. The contact-detail panel (which passes
    # `contact_id`) explicitly drops the default so it still shows
    # every thread tied to the contact regardless of where the
    # operator filed it.
    #
    # v2.4d — `sent` is a virtual view: threads where the operator
    # initiated at least one outbound message that actually went
    # out (excluding pending scheduled rows), AND the thread isn't
    # currently in archived/trashed/spam. It's NOT a column on
    # EmailThread; we encode the filter as a subquery rather than
    # add a "sent" enum member so a thread can simultaneously
    # belong to "Enviados" and to a custom folder.
    if state == "sent":
        sent_subq = select(EmailMessage.thread_id).where(
            EmailMessage.direction == "outbound",
            EmailMessage.sent_at.is_not(None),
            EmailMessage.gmail_account_user_id == current_user.id,
        )
        stmt = stmt.where(
            EmailThread.id.in_(sent_subq),
            EmailThread.state == EmailThreadState.INBOX,
        )
    elif state is not None:
        stmt = stmt.where(EmailThread.state == EmailThreadState(state))
    elif contact_id is None:
        stmt = stmt.where(EmailThread.state == EmailThreadState.INBOX)
    if folder_id == "inbox":
        stmt = stmt.where(EmailThread.folder_id.is_(None))
    elif folder_id is not None:
        stmt = stmt.where(EmailThread.folder_id == folder_id)
    if label_id is not None:
        # Subquery instead of join so the outer count/pagination
        # stay unique-by-thread without DISTINCT gymnastics.
        label_match = select(EmailThreadLabel.thread_id).where(
            EmailThreadLabel.label_id == label_id
        )
        stmt = stmt.where(EmailThread.id.in_(label_match))
    if starred is not None:
        stmt = stmt.where(EmailThread.is_starred.is_(starred))
    if has_unread is not None:
        stmt = stmt.where(EmailThread.has_unread_replies.is_(has_unread))
    if since is not None:
        stmt = stmt.where(EmailThread.last_message_at >= since)
    if until is not None:
        stmt = stmt.where(EmailThread.last_message_at <= until)
    if not include_snoozed:
        from sqlalchemy import or_ as _or_sn  # noqa: PLC0415

        now = datetime.now(UTC)
        stmt = stmt.where(
            _or_sn(
                EmailThread.snooze_until.is_(None),
                EmailThread.snooze_until <= now,
            )
        )
    if q:
        # LIKE-match the term against the thread subject OR any
        # message's from_email / from_name / subject / snippet /
        # body_text, plus the linked Contact's name fields (so the
        # operator can find "Eduard Riera" even when the contact's
        # row carries the canonical name and the message header
        # ships the email alone).
        from sqlalchemy import or_ as _or  # noqa: PLC0415

        from app.models.crm import Contact as _Contact  # noqa: PLC0415

        like = f"%{q}%"
        msg_match = select(EmailMessage.thread_id).where(
            _or(
                EmailMessage.from_email.ilike(like),
                EmailMessage.from_name.ilike(like),
                EmailMessage.subject.ilike(like),
                EmailMessage.snippet.ilike(like),
                EmailMessage.body_text.ilike(like),
            )
        )
        contact_match = select(_Contact.id).where(
            _or(
                _Contact.first_name.ilike(like),
                _Contact.last_name.ilike(like),
                _Contact.email.ilike(like),
            )
        )
        stmt = stmt.where(
            _or(
                EmailThread.subject.ilike(like),
                EmailThread.id.in_(msg_match),
                EmailThread.contact_id.in_(contact_match),
            )
        )
    total = int(
        session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        or 0
    )
    items = list(
        session.scalars(
            stmt.order_by(EmailThread.last_message_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    # Last message per thread — driven the "Remitente último" +
    # "Vista previa" columns the v2.1 list view exposes. One small
    # extra query per page, batched via `id IN (...)`.
    thread_ids = [t.id for t in items]
    last_by_thread = _latest_messages(session, thread_ids)
    contacts_by_id = _contacts_for_threads(session, items)
    tracking_by_thread = _tracking_counts_for_threads(session, thread_ids)
    out: list[EmailThreadRead] = []
    for thread in items:
        last = last_by_thread.get(thread.id)
        read = EmailThreadRead.model_validate(thread)
        if last is not None:
            read.last_message_direction = (
                last.direction.value
                if hasattr(last.direction, "value")
                else str(last.direction)
            )
            read.last_message_from = last.from_email
            read.last_message_snippet = last.snippet or _snippet_from_body(
                last.body_text, last.body_html
            )
        read.contact_name = _resolve_contact_name(
            contact=contacts_by_id.get(thread.contact_id) if thread.contact_id else None,
            last_message=last,
        )
        read.tracking = tracking_by_thread.get(thread.id, {})
        out.append(read)
    return EmailThreadList(items=out, total=total)


@router.get("/threads/{thread_id}", response_model=EmailThreadDetail)
def thread_detail(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailThreadDetail:
    thread = session.scalar(
        select(EmailThread)
        .where(EmailThread.id == thread_id)
        .options(
            selectinload(EmailThread.messages),
            selectinload(EmailThread.labels),
        )
    )
    if thread is None:
        raise not_found("EmailThread")
    if (
        current_user.role not in (UserRole.ADMIN, UserRole.MANAGER)
        and thread.initiated_by_user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver este hilo.",
        )
    # v2.1.1: opening the detail page as the thread owner marks it
    # read automatically — saves the front-end an extra POST and
    # matches the operator's mental model ("if I opened it, I saw
    # it"). Other roles (admin viewing someone else's thread) do
    # NOT mark-read, since that would clobber the owner's badge.
    if thread.initiated_by_user_id == current_user.id and thread.has_unread_replies:
        thread.has_unread_replies = False
        session.execute(
            EmailMessage.__table__.update()  # type: ignore[attr-defined]
            .where(
                EmailMessage.thread_id == thread.id,
                EmailMessage.direction == "inbound",
                EmailMessage.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        session.commit()
    return EmailThreadDetail(
        **EmailThreadRead.model_validate(thread).model_dump(),
        messages=[_message_read(m) for m in thread.messages],
        reply_to_suggestion=_reply_to_suggestion(
            session, thread, current_user
        ),
    )


@router.post("/threads/{thread_id}/mark-unread")
def mark_unread(
    thread_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    """Inverse of mark-read — flips `has_unread_replies` back on
    so the operator can flag a thread for follow-up. Doesn't
    touch the `read_at` stamps on individual messages."""
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise not_found("EmailThread")
    thread.has_unread_replies = True
    record_event(
        session,
        action=Action.EMAIL_THREAD_MARKED_READ,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        metadata={"flipped_to": "unread"},
        request=request,
    )
    session.commit()
    return {"message": "marked_unread"}


@router.post("/threads/{thread_id}/mark-read")
def mark_read(
    thread_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise not_found("EmailThread")
    now = datetime.now(UTC)
    session.execute(
        EmailMessage.__table__.update()  # type: ignore[attr-defined]
        .where(
            EmailMessage.thread_id == thread_id,
            EmailMessage.direction == "inbound",
            EmailMessage.read_at.is_(None),
        )
        .values(read_at=now)
    )
    thread.has_unread_replies = False
    record_event(
        session,
        action=Action.EMAIL_THREAD_MARKED_READ,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        request=request,
    )
    session.commit()
    return {"message": "marked_read"}


@router.get("/activity")
def email_activity(
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    limit: int = Query(default=5, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[dict[str, Any]]:
    """Recent CRM email activity for the dashboard widget.

    Returns flat items `{type, thread_id, subject, contact_id,
    contact_name, occurred_at, snippet, direction}` sorted by
    `occurred_at` desc. `scope=mine` filters to threads the
    current user initiated OR whose Gmail account belongs to
    them. `scope=all` is admin-only — other roles get their own
    activity regardless of what they passed.
    """
    from app.models.crm import Contact  # noqa: PLC0415

    stmt = (
        select(EmailMessage, EmailThread, Contact)
        .join(EmailThread, EmailMessage.thread_id == EmailThread.id)
        .outerjoin(Contact, Contact.id == EmailMessage.contact_id)
        # Activity timeline = messages that actually went out. A
        # pending scheduled message hasn't reached anyone yet, so
        # it must NOT surface here (it would also crash the ORDER
        # BY since `sent_at` is NULL).
        .where(EmailMessage.sent_at.is_not(None))
    )
    # Spec: only the `admin` role can see other users' activity when
    # `scope=all`. Every other role (including manager) is forced to
    # the `mine` filter regardless of the scope they passed —
    # defence in depth for managers who landed on the dashboard.
    if scope == "mine" or current_user.role != UserRole.ADMIN:
        from sqlalchemy import or_ as _or  # noqa: PLC0415

        stmt = stmt.where(
            _or(
                EmailThread.initiated_by_user_id == current_user.id,
                EmailThread.gmail_account_user_id == current_user.id,
            )
        )
    stmt = stmt.order_by(EmailMessage.sent_at.desc()).limit(limit)
    rows = list(session.execute(stmt).all())
    out: list[dict[str, Any]] = []
    for msg, thread, contact in rows:
        snippet = msg.snippet or _snippet_from_body(msg.body_text, msg.body_html)
        contact_name = None
        if contact is not None:
            contact_name = (
                " ".join(
                    p for p in (contact.first_name, contact.last_name) if p
                )
                or contact.email
            )
        out.append(
            {
                "type": (
                    "email.sent_from_crm"
                    if msg.direction.value == "outbound"
                    else "email.reply_received"
                ),
                "direction": msg.direction.value,
                "thread_id": thread.id,
                "message_id": msg.id,
                "subject": thread.subject,
                "contact_id": contact.id if contact else None,
                "contact_name": contact_name,
                "from_email": msg.from_email,
                "occurred_at": msg.sent_at,
                "snippet": snippet,
            }
        )
    return out


@router.get("/admin/all-threads", response_model=EmailThreadList)
def admin_all_threads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> EmailThreadList:
    _ = current_user
    stmt = select(EmailThread).order_by(EmailThread.last_message_at.desc())
    total = int(
        session.scalar(select(func.count()).select_from(EmailThread))
        or 0
    )
    items = list(session.scalars(stmt.offset(offset).limit(limit)))
    return EmailThreadList(
        items=[EmailThreadRead.model_validate(t) for t in items],
        total=total,
    )


def _message_read(m: EmailMessage) -> EmailMessageRead:
    return EmailMessageRead.model_validate(m)


def _user_own_emails(session: Session, user: User) -> set[str]:
    """Every address that belongs to the operator: their login email
    plus every alias they've ever configured a preference for. Cheap
    (one indexed query) and crucially does NOT hit Gmail, so it's safe
    to call on every thread-detail open."""
    own = {user.email.lower()} if user.email else set()
    rows = session.scalars(
        select(UserEmailAliasPref.alias_email).where(
            UserEmailAliasPref.user_id == user.id
        )
    )
    for alias in rows:
        if alias:
            own.add(alias.lower())
    return own


def _reply_to_suggestion(
    session: Session, thread: EmailThread, user: User
) -> str | None:
    """The address "Responder" should target.

    We can't trust `direction`: a comercial replying to a lead
    straight from Gmail lands back in the watched account via
    history.list and gets materialised as `inbound` with `from_email`
    set to the comercial's own alias. Filtering by the operator's
    address set fixes it — pick the most recent message whose sender
    is NOT the operator. Fallback: the first outbound's first
    recipient, which is the lead by construction.
    """
    own = _user_own_emails(session, user)
    # Pending scheduled messages have `sent_at IS NULL`; they
    # haven't reached anyone yet so they don't influence the
    # reply-to suggestion. Drop them BEFORE sorting so the
    # `<` comparison doesn't blow up on None.
    actual = [m for m in thread.messages if m.sent_at is not None]
    msgs = sorted(actual, key=lambda m: m.sent_at)
    for m in reversed(msgs):
        sender = (m.from_email or "").lower()
        if sender and sender not in own:
            return m.from_email
    # No message from anyone but the operator — fall back to whoever
    # the first message was addressed to.
    if msgs:
        try:
            to_list = json.loads(msgs[0].to_emails_json or "[]")
        except (TypeError, ValueError):
            to_list = []
        if to_list:
            return str(to_list[0])
    return None


def _latest_messages(
    session: Session, thread_ids: list[str]
) -> dict[str, EmailMessage]:
    """Return the most recent EmailMessage per thread id. Avoids
    the per-row N+1 the list view used to have."""
    if not thread_ids:
        return {}
    rows = session.scalars(
        select(EmailMessage)
        .where(
            EmailMessage.thread_id.in_(thread_ids),
            # Skip pending scheduled rows — the inbox list's
            # "Último mensaje" + snippet columns should reflect
            # what's been received, not what the operator queued
            # to send later.
            EmailMessage.sent_at.is_not(None),
        )
        .order_by(EmailMessage.sent_at.desc())
    ).all()
    out: dict[str, EmailMessage] = {}
    for row in rows:
        if row.thread_id not in out:
            out[row.thread_id] = row
    return out


# Event types the inbox surfaces per row. `sent` / `delivered` are
# excluded — every thread has a send, so showing it is pure noise.
_INBOX_EVENT_TYPES = (
    EmailEventType.OPEN,
    EmailEventType.CLICK,
    EmailEventType.BOUNCE,
    EmailEventType.UNSUBSCRIBE,
)


def _tracking_counts_for_threads(
    session: Session, thread_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Aggregate open/click/bounce/unsubscribe counts per thread in a
    single grouped query (events → messages → thread). Threads with no
    events simply don't appear in the result; the caller treats a
    missing key as an empty dict."""
    if not thread_ids:
        return {}
    rows = session.execute(
        select(
            EmailMessage.thread_id,
            EmailMessageEvent.event_type,
            func.count(EmailMessageEvent.id),
        )
        .join(
            EmailMessage,
            EmailMessage.id == EmailMessageEvent.message_id,
        )
        .where(EmailMessage.thread_id.in_(thread_ids))
        .where(EmailMessageEvent.event_type.in_(_INBOX_EVENT_TYPES))
        .group_by(EmailMessage.thread_id, EmailMessageEvent.event_type)
    ).all()
    out: dict[str, dict[str, int]] = {}
    for thread_id, event_type, count in rows:
        key = (
            event_type.value
            if hasattr(event_type, "value")
            else str(event_type)
        )
        out.setdefault(thread_id, {})[key] = int(count)
    return out


def _contacts_for_threads(
    session: Session, threads: list[EmailThread]
) -> dict[str, Any]:
    """Batch-load the Contact rows referenced by `threads.contact_id`."""
    from app.models.crm import Contact as _Contact  # noqa: PLC0415

    ids = {t.contact_id for t in threads if t.contact_id}
    if not ids:
        return {}
    rows = session.scalars(
        select(_Contact).where(_Contact.id.in_(ids))
    ).all()
    return {c.id: c for c in rows}


def _resolve_contact_name(
    *,
    contact: Any | None,
    last_message: EmailMessage | None,
) -> str | None:
    """Pick the most operator-friendly name for a thread.

    Order of preference:
    1. Linked Contact's full name (or just first / last when one is
       missing).
    2. Last message's `from_name` header value.
    3. Capitalised local part of the last message's email.
    4. None — the UI will fall back to "(sin nombre)".
    """
    if contact is not None:
        parts = [contact.first_name, contact.last_name]
        joined = " ".join(p for p in parts if p)
        if joined.strip():
            return joined.strip()
    if last_message is not None:
        if last_message.from_name and last_message.from_name.strip():
            return last_message.from_name.strip()
        if last_message.from_email and "@" in last_message.from_email:
            local = last_message.from_email.split("@", 1)[0]
            local = local.replace(".", " ").replace("_", " ")
            return local.title() or None
    return None


def _snippet_from_body(
    body_text: str | None, body_html: str | None
) -> str | None:
    """Derive a ~200-char snippet for the inbox list preview.

    Prefers the multipart text body; when only HTML exists (every
    TinyMCE-authored send → `body_text=null`) it routes through
    `extract_text_from_html`, which strips `<style>` / `<script>` /
    `<head>` BLOCK contents — not just the tags — so the CSS reset
    boilerplate the editor injects doesn't bleed into the preview as
    raw CSS source. (Naive `re.sub("<[^>]+>", …)` left the block
    contents behind; that was the `<style>body,table,td{…` Bart saw.)
    """
    from app.email_templates.services import (  # noqa: PLC0415
        extract_text_from_html,
    )

    if body_text and body_text.strip():
        flat = " ".join(body_text.split()).strip()
        return flat[:200] or None
    if body_html:
        clean = extract_text_from_html(body_html)
        if clean:
            return clean[:200]
    return None
