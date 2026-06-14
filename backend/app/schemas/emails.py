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


class EmailMessageRead(BaseModel):
    id: str
    thread_id: str
    gmail_message_id: str
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
    sent_at: datetime
    contact_id: str | None
    created_by_user_id: str | None
    read_at: datetime | None

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


class EmailAlias(BaseModel):
    """Gmail alias enriched with the current user's preferences.

    `is_default` here is the per-user CRM preference (alias to send
    from by default), not Gmail's own default flag — the latter
    lives in `is_primary`. Pre-Fase 2 callers that don't know about
    prefs see `user_pref_*` mirror the legacy flags so they keep
    working unchanged.
    """

    send_as_email: str
    display_name: str
    is_primary: bool
    is_default: bool
    verification_status: str | None = None
    user_pref_allowed: bool = False
    user_pref_default: bool = False


class MyAlias(BaseModel):
    """Trimmed alias shape used by the composer dropdown — only the
    fields the modal actually renders."""

    send_as_email: str
    display_name: str
    is_default: bool


class AliasPreferenceItem(BaseModel):
    alias_email: str = Field(min_length=1, max_length=255)
    is_allowed: bool = True
    is_default: bool = False


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
