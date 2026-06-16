"""SQLAlchemy models for the v2.2 email templates surface."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin


class EmailTemplateFolder(TimestampMixin, Base):
    __tablename__ = "email_template_folders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_template_folders.id", ondelete="SET NULL"),
        index=True,
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    is_global: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EmailTemplate(TimestampMixin, Base):
    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    # MEDIUMTEXT (16 MB) en MySQL para alojar imágenes inline base64
    # de Gmail Templates importadas. Migración 0050. En SQLite (tests)
    # el variant se ignora y queda como TEXT.
    body_html: Mapped[str] = mapped_column(
        Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
        nullable=False,
    )
    body_text: Mapped[str | None] = mapped_column(Text)
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_template_folders.id", ondelete="SET NULL"),
        index=True,
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    is_global: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
