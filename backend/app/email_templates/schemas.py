"""Pydantic schemas for the v2.2 email templates router."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Sprint Email v2.5 — C. Tres modos de visibilidad para una carpeta.
# `team` reemplaza al legacy `is_global=True` (el flag queda como
# sombra para retrocompat).
FolderVisibility = Literal["private", "team", "shared"]


class FolderRead(BaseModel):
    id: str
    name: str
    parent_folder_id: str | None
    owner_user_id: str | None
    is_global: bool
    visibility: FolderVisibility = "private"
    sort_order: int
    created_at: datetime
    updated_at: datetime
    # Sprint Email v2.5 — C. Lista de user_id con acceso a la carpeta
    # cuando visibility=='shared'. Vacía para private/team.
    shared_user_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class FolderWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_folder_id: str | None = None
    # Legacy. Si se manda `visibility` la API lo respeta; si no, se
    # deriva de `is_global` (True -> team, False -> private) para que
    # los clientes pre-v2.5 sigan funcionando sin cambios.
    is_global: bool = False
    visibility: FolderVisibility | None = None
    sort_order: int = 0
    # Solo se respeta cuando visibility == "shared". Lista de user_id
    # con acceso de lectura+escritura a la carpeta.
    shared_user_ids: list[str] = Field(default_factory=list)


class FolderShareWrite(BaseModel):
    """Atajo para añadir / quitar un único user a una carpeta shared.
    `POST /email-template-folders/{id}/shares` lo acepta junto al
    PUT general — útil para la UI de "compartir con" sin tener que
    re-enviar la lista completa."""

    user_id: str


class FolderTreeNode(BaseModel):
    """Recursive folder tree the page consumes on first paint."""

    id: str
    name: str
    is_global: bool
    visibility: FolderVisibility = "private"
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


class BrevoTemplateHtmlResponse(BaseModel):
    """Lazy-loaded body fetched on demand when the operator picks a
    Brevo template. The marketing cache stores html_content NULL
    until first detail open; this surface fills it in transparently."""

    id: int
    name: str
    subject: str | None
    body_html: str


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
    """Sprint Email v2.2 (post-fixes). `public_url` is what the editor
    inlines into the email — absolute when `email_assets_public_base`
    is configured, root-relative otherwise (dev / tests)."""

    public_url: str
    filename: str
    content_type: str
    size_bytes: int


class PickerResponse(BaseModel):
    crm: list[TemplateListItem]
    brevo: list[BrevoPickerItem]
    folders: list[FolderTreeNode]
    recent: list[TemplateListItem]
