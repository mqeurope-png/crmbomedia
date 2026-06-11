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


class EmailThreadList(BaseModel):
    items: list[EmailThreadRead]
    total: int


class EmailAlias(BaseModel):
    send_as_email: str
    display_name: str
    is_primary: bool
    is_default: bool
