"""Pydantic schemas for the v2.2 email templates router."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FolderRead(BaseModel):
    id: str
    name: str
    parent_folder_id: str | None
    owner_user_id: str | None
    is_global: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FolderWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_folder_id: str | None = None
    is_global: bool = False
    sort_order: int = 0


class FolderTreeNode(BaseModel):
    """Recursive folder tree the page consumes on first paint."""

    id: str
    name: str
    is_global: bool
    sort_order: int
    children: list[FolderTreeNode] = Field(default_factory=list)
    template_count: int = 0


class TemplateRead(BaseModel):
    id: str
    name: str
    subject: str | None
    body_html: str
    body_text: str | None
    folder_id: str | None
    owner_user_id: str | None
    is_global: bool
    usage_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateListItem(BaseModel):
    """Lightweight shape for list views — drops the body to keep
    payloads small. The page hydrates the full body on demand."""

    id: str
    name: str
    subject: str | None
    folder_id: str | None
    owner_user_id: str | None
    is_global: bool
    usage_count: int
    last_used_at: datetime | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str | None = None
    body_html: str
    folder_id: str | None = None
    is_global: bool = False


class BrevoPickerItem(BaseModel):
    """Brevo template surfaced through the mixed picker. The CRM
    can't edit these — they belong to the Brevo account."""

    id: int
    name: str
    subject: str | None
    sender_name: str | None
    has_html: bool


class ComposerSourcePickerItem(BaseModel):
    """composer.bomedia.net template, surfaced via the read-only
    Supabase proxy. The CRM cannot edit these — clicking the row in
    the UI opens the live Composer in a new tab."""

    id: str
    name: str
    brand: str | None
    blocks_count: int
    open_url: str


class ComposerSourceResponse(BaseModel):
    items: list[ComposerSourcePickerItem]
    error: str | None = None


class ImageUploadResponse(BaseModel):
    url: str
    filename: str
    content_type: str
    size_bytes: int


class PickerResponse(BaseModel):
    crm: list[TemplateListItem]
    brevo: list[BrevoPickerItem]
    folders: list[FolderTreeNode]
    recent: list[TemplateListItem]
