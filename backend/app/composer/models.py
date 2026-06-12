"""SQLAlchemy models for the eleven composer_* tables.

All JSON columns land as `Text` so SQLite (CI) and MySQL (prod)
share the same shape. Serialisation/parsing happens at the
service / API layer using `json.dumps` / `json.loads`.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin


class ComposerBrand(TimestampMixin, Base):
    __tablename__ = "composer_brands"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="brand"
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    logo: Mapped[str | None] = mapped_column(Text)
    logo_text: Mapped[str | None] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="#000")
    divider: Mapped[str | None] = mapped_column(String(20))
    logo_height: Mapped[str | None] = mapped_column(String(8))
    logo_max_width: Mapped[str | None] = mapped_column(String(8))
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    i18n_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ComposerProduct(TimestampMixin, Base):
    __tablename__ = "composer_products"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(
        ForeignKey("composer_brands.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    badge: Mapped[str | None] = mapped_column(String(60))
    badge_bg: Mapped[str | None] = mapped_column(String(20))
    badge_color: Mapped[str | None] = mapped_column(String(20))
    img: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(60))
    alt: Mapped[str | None] = mapped_column(String(60))
    feat1: Mapped[str | None] = mapped_column(String(200))
    feat2: Mapped[str | None] = mapped_column(String(200))
    price: Mapped[str | None] = mapped_column(String(80))
    link: Mapped[str | None] = mapped_column(Text)
    accent: Mapped[str | None] = mapped_column(String(20))
    gradient: Mapped[str | None] = mapped_column(Text)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    i18n_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ComposerPrewrittenText(TimestampMixin, Base):
    __tablename__ = "composer_prewritten_texts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(10))
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("composer_brands.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    i18n_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ComposerComposedBlock(TimestampMixin, Base):
    __tablename__ = "composer_composed_blocks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_range: Mapped[str | None] = mapped_column(String(120))
    color_tag: Mapped[str | None] = mapped_column(String(40))
    intro_text: Mapped[str | None] = mapped_column(Text)
    brand_strip: Mapped[str | None] = mapped_column(String(64))
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    products: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    include_hero: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_steps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    i18n_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ComposerStandaloneBlock(TimestampMixin, Base):
    __tablename__ = "composer_standalone_blocks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(10))
    icon_bg: Mapped[str | None] = mapped_column(String(20))
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("composer_brands.id", ondelete="SET NULL")
    )
    section: Mapped[str | None] = mapped_column(String(60))
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    i18n_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ComposerTemplate(TimestampMixin, Base):
    __tablename__ = "composer_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_class: Mapped[str | None] = mapped_column(String(40))
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("composer_brands.id", ondelete="SET NULL")
    )
    blocks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    compositor_blocks_json: Mapped[str | None] = mapped_column(Text)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ComposerTemplateRevision(Base):
    __tablename__ = "composer_template_revisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("composer_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.utcnow(),
    )


class ComposerDraft(Base):
    """Per-user canvas autosave. One row per user — the upsert
    is keyed on `user_id` so there's no ambiguity about which
    draft is "current"."""

    __tablename__ = "composer_drafts"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ComposerAsset(Base):
    __tablename__ = "composer_assets"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_composer_assets_sha256"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    public_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="upload", index=True
    )
    metadata_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ComposerSettings(Base):
    """Singleton — only id=1 ever exists. The service layer
    enforces this (no DB-level check, SQLite has no support)."""

    __tablename__ = "composer_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    openai_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    ai_styles_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    agent_system_prompt: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ComposerUserHiddenItem(Base):
    __tablename__ = "composer_user_hidden_items"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    collection: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class ComposerUserAiStyle(Base):
    __tablename__ = "composer_user_ai_styles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    lang: Mapped[str] = mapped_column(String(5), primary_key=True)
    style_text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ComposerActivityLog(Base):
    __tablename__ = "composer_activity_log"

    # `BigInteger.with_variant(Integer, "sqlite")` so SQLite's
    # `INTEGER PRIMARY KEY` ROWID alias kicks in (SQLite has no
    # autoincrement support for plain BIGINT). MySQL still gets the
    # full 64-bit column the audit log was designed for.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
