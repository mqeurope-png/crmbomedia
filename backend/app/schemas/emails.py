"""Pydantic schemas for the email endpoints."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class EmailSendRequest(BaseModel):
    from_alias: EmailStr
    from_name: str | None = None
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] | None = None
    bcc: list[EmailStr] | None = None
    subject: str = Field(default="", max_length=500)
    body_html: str | None = None
    body_text: str | None = None
    contact_id: str | None = None
    in_reply_to_message_id: str | None = None
    # Sprint Email v2.3a. When omitted (`None`) the route falls back
    # to the sender's `users.email_include_unsubscribe_default` flag —
    # the per-operator preference for the modal toggle.
    include_unsubscribe: bool | None = None
    # Sprint Email v2.4e — when set to a future datetime the message
    # is persisted with `scheduled_status='pending'` and NOT handed
    # to Gmail; the periodic worker sweeps it out at the right time.
    # NULL keeps the legacy "send immediately" path.
    scheduled_for: datetime | None = None


class EmailDraftWrite(BaseModel):
    """Create / update payload for an email draft. Every field is
    optional — the auto-save endpoint accepts whatever the modal
    has at the moment of the tick. `to_emails` / `cc_emails` /
    `bcc_emails` ship as plain string arrays so the operator can
    save half-typed addresses without the EmailStr validator
    refusing the payload."""

    thread_id: str | None = None
    contact_id: str | None = None
    from_alias: str | None = Field(default=None, max_length=255)
    from_name: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = None
    body_text: str | None = None
    to_emails: list[str] | None = None
    cc_emails: list[str] | None = None
    bcc_emails: list[str] | None = None
    in_reply_to_message_id: str | None = None
    signature_id: str | None = None
    include_unsubscribe: bool = False
    scheduled_for: datetime | None = None


class EmailDraftRead(BaseModel):
    id: str
    user_id: str
    thread_id: str | None
    contact_id: str | None
    from_alias: str | None
    from_name: str | None
    subject: str | None
    body_html: str | None
    body_text: str | None
    to_emails: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("to_emails", "to_emails_json"),
    )
    cc_emails: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("cc_emails", "cc_emails_json"),
    )
    bcc_emails: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("bcc_emails", "bcc_emails_json"),
    )
    in_reply_to_message_id: str | None
    signature_id: str | None
    include_unsubscribe: bool
    scheduled_for: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("to_emails", mode="before")
    @classmethod
    def _decode_to(cls, value: Any) -> Any:
        # `to_emails` is non-optional on the model, so a NULL column
        # has to become an empty list rather than propagate None.
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return []
        return value

    @field_validator("cc_emails", "bcc_emails", mode="before")
    @classmethod
    def _decode_cc_bcc(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return []
        return value


class ScheduledMessageUpdate(BaseModel):
    """PUT payload for editing a pending scheduled message. Every
    field is optional — operator may rewrite just the time or just
    the body. The backend ignores fields not present."""

    scheduled_for: datetime | None = None
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = None
    body_text: str | None = None


class EmailMessageRead(BaseModel):
    id: str
    thread_id: str
    # Sprint Email v2.4e — nullable while a scheduled-send row is
    # waiting for Gmail to accept it; populated when the sweep
    # actually sends.
    gmail_message_id: str | None = None
    direction: str
    from_email: str
    from_name: str | None
    to_emails: list[str] = Field(
        validation_alias=AliasChoices("to_emails", "to_emails_json"),
    )
    cc_emails: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("cc_emails", "cc_emails_json"),
    )
    subject: str | None
    body_html: str | None
    body_text: str | None
    snippet: str | None
    sent_at: datetime | None
    contact_id: str | None
    created_by_user_id: str | None
    read_at: datetime | None
    # Sprint Email v2.4e — scheduled send. NULL pair means the
    # message went out immediately (every legacy row).
    scheduled_for: datetime | None = None
    scheduled_status: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("to_emails", "cc_emails", mode="before")
    @classmethod
    def _decode_json(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return []
        return value


class EmailThreadRead(BaseModel):
    id: str
    contact_id: str | None
    initiated_by_user_id: str
    gmail_thread_id: str
    gmail_account_user_id: str
    subject: str | None
    participants: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("participants", "participants_json"),
    )
    first_message_at: datetime
    last_message_at: datetime
    message_count: int
    has_unread_replies: bool
    is_archived: bool
    # Email v2.1: enriched fields the list view renders without
    # having to fetch every message in the thread. Computed by the
    # route handler from the latest message; absent from the
    # in-memory model.
    last_message_direction: str | None = None
    last_message_from: str | None = None
    last_message_snippet: str | None = None
    # Email v2.1.1: Gmail-style list shows a single "Contact" column
    # which prefers the linked Contact row's full name; falls back to
    # the From-header display name; falls back to the capitalised
    # local part of the email. Resolved server-side so the UI
    # doesn't have to.
    contact_name: str | None = None
    # Email v2.3b: per-thread tracking counts (open / click / bounce /
    # unsubscribe) aggregated across the thread's outbound messages, so
    # the inbox list can show event badges per row without N+1 fetches.
    # `sent` is intentionally excluded — every thread has it, so it's
    # noise in the list.
    tracking: dict[str, int] = Field(default_factory=dict)
    # Email v2.4a: mailbox state. `state` drives which "box" the
    # thread lives in (inbox/archived/trashed/spam); `folder_id` is
    # the custom-folder sub-classification; `labels` is the
    # many-to-many label list resolved server-side so the list view
    # can render colored chips without an N+1.
    state: str = "inbox"
    folder_id: str | None = None
    is_starred: bool = False
    snooze_until: datetime | None = None
    labels: list[EmailLabelRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("participants", mode="before")
    @classmethod
    def _decode_participants(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return []
        if value is None:
            return []
        return value


class EmailThreadDetail(EmailThreadRead):
    messages: list[EmailMessageRead]
    # Email v2.2 round 4: the address the "Responder" button should
    # pre-fill. Computed server-side as the last message NOT sent from
    # one of the operator's own aliases — `direction` can't be trusted
    # because a comercial replying straight from Gmail surfaces via the
    # account's history watch as `inbound` even though it's really their
    # own outbound. Null only when the thread has no resolvable other
    # party (shouldn't happen in practice).
    reply_to_suggestion: str | None = None


class EmailThreadList(BaseModel):
    items: list[EmailThreadRead]
    total: int


class GmailTemplate(BaseModel):
    """Plantilla nativa Gmail (canned response) en el shape que
    consume el `EmailComposerModal`. Los templates Gmail se guardan
    como drafts con el label sistema `^smartlabel_canned_response`."""

    id: str
    subject: str = ""
    body_html: str = ""
    snippet: str = ""
    updated_at: datetime | None = None


class EmailAlias(BaseModel):
    """Gmail alias enriched with the current user's preferences.

    `is_default` here is the per-user CRM preference (alias to send
    from by default), not Gmail's own default flag — the latter
    lives in `is_primary`. Pre-Fase 2 callers that don't know about
    prefs see `user_pref_*` mirror the legacy flags so they keep
    working unchanged.

    PR-DisplayName-Remitente:
    - `gmail_display_name`: lo que Gmail Send-As tiene configurado
      (sincronizado al GET).
    - `display_name_override`: override del user persistido en
      `user_email_alias_prefs`.
    - `resolved_display_name`: efectivo (`override or gmail or ""`).
      Es el que la UI muestra en chips y el composer pone en el
      header `From:` al enviar.
    """

    send_as_email: str
    display_name: str
    is_primary: bool
    is_default: bool
    verification_status: str | None = None
    user_pref_allowed: bool = False
    user_pref_default: bool = False
    gmail_display_name: str | None = None
    display_name_override: str | None = None
    resolved_display_name: str = ""


class MyAlias(BaseModel):
    """Trimmed alias shape used by the composer dropdown — only the
    fields the modal actually renders.

    PR-DisplayName-Remitente: `resolved_display_name` es el que el
    composer pinta en el `<select>` y manda como `from_name` al
    handler de send (ver `EmailComposerModal.handleSubmit`)."""

    send_as_email: str
    display_name: str
    is_default: bool
    resolved_display_name: str = ""


class AliasPreferenceItem(BaseModel):
    alias_email: str = Field(min_length=1, max_length=255)
    is_allowed: bool = True
    is_default: bool = False
    # PR-DisplayName-Remitente. None = no tocar el override actual,
    # "" = limpiar (vuelve a usar `gmail_display_name`), str = setear.
    # El handler hace strip + null-on-empty.
    display_name_override: str | None = Field(default=None, max_length=255)


class AliasPreferencesPayload(BaseModel):
    preferences: list[AliasPreferenceItem]

    @field_validator("preferences")
    @classmethod
    def _at_most_one_default(
        cls, items: list[AliasPreferenceItem]
    ) -> list[AliasPreferenceItem]:
        defaults = [it for it in items if it.is_default and it.is_allowed]
        if len(defaults) > 1:
            raise ValueError(
                "Solo un alias puede marcarse como predeterminado."
            )
        return items


# --- Sprint Email v2.4a — mailbox redesign (folders + labels +
# thread state/star/snooze). Backend-only foundation; the UI ships
# in v2.4b. ---


class EmailFolderWrite(BaseModel):
    """Create / update payload for a custom folder. `name` is the
    only required field; the rest are optional sidebar polish."""

    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=40)
    sort_order: int = 0


class EmailFolderRead(BaseModel):
    id: str
    name: str
    parent_id: str | None
    color: str | None
    icon: str | None
    sort_order: int
    is_system: bool
    # Populated by the tree endpoint so the sidebar can render
    # badges next to each row without a follow-up call.
    unread_count: int = 0
    total_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class EmailLabelWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=20)
    sort_order: int = 0


class EmailLabelRead(BaseModel):
    id: str
    name: str
    color: str | None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class EmailThreadBulkAction(BaseModel):
    """Generic bulk-action payload — the action verb is encoded in
    the URL path so the schema stays flat and the API stays
    explicit."""

    thread_ids: list[str] = Field(min_length=1, max_length=200)


class EmailThreadBulkMove(EmailThreadBulkAction):
    """Move to a folder (NULL = bandeja). Kept separate so OpenAPI
    documents the optional `folder_id`."""

    folder_id: str | None = None


class EmailThreadBulkLabel(EmailThreadBulkAction):
    """Add or remove a single label from a batch of threads. The
    verb (add/remove) lives on the URL."""

    label_id: str


class EmailThreadBulkSnooze(EmailThreadBulkAction):
    snooze_until: datetime


# EmailThreadRead refers to EmailLabelRead by string forward-ref
# (the `from __future__ import annotations` at the top makes every
# annotation a string). Resolve it now that EmailLabelRead exists.
EmailThreadRead.model_rebuild()
EmailThreadDetail.model_rebuild()


class EmailDraftAttachmentRead(BaseModel):
    """Sprint Email v2.5 — A. Metadata de un adjunto de draft
    (filename, content_type, size). El binario NO se serializa —
    sólo viaja del POST de upload al MIME del send."""

    id: str
    draft_id: str
    filename: str
    content_type: str | None
    size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
