"""Pydantic in/out shapes for the composer endpoints.

JSON columns land as `Text` on the SQL side; the schemas decode
them into structured fields so the API hands the front-end ready
dicts/lists.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


def _decode_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    if value is None:
        return fallback
    return value


class ComposerBrandRead(BaseModel):
    id: str
    type: str
    label: str
    logo: str | None
    logo_text: str | None
    color: str
    divider: str | None
    logo_height: str | None
    logo_max_width: str | None
    visible: bool
    sort_order: int
    i18n: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("i18n", "i18n_json"),
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("i18n", mode="before")
    @classmethod
    def _decode(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerProductRead(BaseModel):
    id: str
    brand_id: str
    name: str
    badge: str | None
    badge_bg: str | None
    badge_color: str | None
    img: str
    description: str | None
    area: str | None
    alt: str | None
    feat1: str | None
    feat2: str | None
    price: str | None
    link: str | None
    accent: str | None
    gradient: str | None
    visible: bool
    sort_order: int
    tags: list[str] = Field(default_factory=list)
    i18n: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("i18n", "i18n_json"),
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("tags", mode="before")
    @classmethod
    def _decode_tags(cls, value: Any) -> Any:
        return _decode_json(value, [])

    @field_validator("i18n", mode="before")
    @classmethod
    def _decode_i18n(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerPrewrittenTextRead(BaseModel):
    id: str
    name: str
    icon: str | None
    brand_id: str | None
    text: str
    visible: bool
    sort_order: int
    i18n: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("i18n", "i18n_json"),
    )

    model_config = ConfigDict(from_attributes=True)

    @field_validator("i18n", mode="before")
    @classmethod
    def _decode(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerComposedBlockRead(BaseModel):
    id: str
    title: str
    description: str | None
    price_range: str | None
    color_tag: str | None
    intro_text: str | None
    brand_strip: str | None
    block_type: str
    products: list[str] = Field(default_factory=list)
    include_hero: bool
    include_steps: bool
    visible: bool
    sort_order: int
    i18n: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("i18n", "i18n_json"),
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "config_json"),
    )

    model_config = ConfigDict(from_attributes=True)

    @field_validator("products", mode="before")
    @classmethod
    def _decode_products(cls, value: Any) -> Any:
        return _decode_json(value, [])

    @field_validator("i18n", mode="before")
    @classmethod
    def _decode_i18n(cls, value: Any) -> Any:
        return _decode_json(value, {})

    @field_validator("config", mode="before")
    @classmethod
    def _decode_config(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerStandaloneBlockRead(BaseModel):
    id: str
    title: str
    description: str | None
    icon: str | None
    icon_bg: str | None
    brand_id: str | None
    section: str | None
    block_type: str
    config: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "config_json"),
    )
    visible: bool
    sort_order: int
    i18n: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("i18n", "i18n_json"),
    )

    model_config = ConfigDict(from_attributes=True)

    @field_validator("config", mode="before")
    @classmethod
    def _decode_config(cls, value: Any) -> Any:
        return _decode_json(value, {})

    @field_validator("i18n", mode="before")
    @classmethod
    def _decode_i18n(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerCatalog(BaseModel):
    """Returned by `GET /api/composer/catalog`. The front-end
    consumes this on every Composer page load."""

    brands: list[ComposerBrandRead]
    products: list[ComposerProductRead]
    prewritten_texts: list[ComposerPrewrittenTextRead]
    composed_blocks: list[ComposerComposedBlockRead]
    standalone_blocks: list[ComposerStandaloneBlockRead]


class ComposerTemplateRead(BaseModel):
    id: str
    name: str
    description: str | None
    color_class: str | None
    brand_id: str | None
    blocks: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("blocks", "blocks_json"),
    )
    compositor_blocks: list[Any] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "compositor_blocks", "compositor_blocks_json"
        ),
    )
    visible: bool
    is_global: bool
    owner_user_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("blocks", mode="before")
    @classmethod
    def _decode_blocks(cls, value: Any) -> Any:
        return _decode_json(value, [])

    @field_validator("compositor_blocks", mode="before")
    @classmethod
    def _decode_compositor(cls, value: Any) -> Any:
        if value is None:
            return None
        return _decode_json(value, None)


class ComposerTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    color_class: str | None = None
    brand_id: str | None = None
    blocks: list[str] = Field(default_factory=list)
    compositor_blocks: list[Any] | None = None
    is_global: bool = False


class ComposerTemplateRevisionRead(BaseModel):
    id: str
    template_id: str
    snapshot: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("snapshot", "snapshot_json"),
    )
    created_by_user_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("snapshot", mode="before")
    @classmethod
    def _decode_snapshot(cls, value: Any) -> Any:
        return _decode_json(value, {})


class ComposerDraftRead(BaseModel):
    state: dict[str, Any]
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ComposerDraftWrite(BaseModel):
    state: dict[str, Any]


class ComposerAssetRead(BaseModel):
    id: str
    user_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    public_url: str
    source: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ComposerSettingsRead(BaseModel):
    """Admin sees whether the OpenAI key is configured but never
    the plaintext value."""

    openai_configured: bool
    ai_styles: dict[str, Any] = Field(default_factory=dict)
    agent_system_prompt: str | None
    updated_at: datetime | None


class ComposerSettingsWrite(BaseModel):
    """Optional fields: send only the bits you want to change."""

    openai_api_key: str | None = None
    ai_styles: dict[str, Any] | None = None
    agent_system_prompt: str | None = None
