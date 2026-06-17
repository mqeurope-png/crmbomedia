"""SQLAlchemy models for the v2.2 email templates surface."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    # Sprint Email v2.5 — C. `private`/`team`/`shared`. `team`
    # convive con `is_global=True` (la columna legacy queda como
    # sombra para retrocompat). `shared` consulta
    # `email_template_folder_shares`.
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private", server_default="private"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EmailTemplateFolderShare(Base):
    """Lista de users con acceso a una carpeta `visibility='shared'`.
    No diferencia read/write — quien está dentro puede leer y editar
    las plantillas de la carpeta. (Si Bart quiere granularidad por
    permiso lo añadimos en una columna `role` enum más adelante.)"""

    __tablename__ = "email_template_folder_shares"
    __table_args__ = (
        UniqueConstraint(
            "folder_id",
            "user_id",
            name="uq_email_template_folder_share_folder_user",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    folder_id: Mapped[str] = mapped_column(
        ForeignKey("email_template_folders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class EmailTemplate(TimestampMixin, Base):
    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
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


class EmailTemplateAttachment(Base):
    """Inline binary attachments (imágenes) referenciadas por
    `<img src="cid:..">` en el draft Gmail original. Tras el import
    (Migración 0051) el `body_html` no las lleva en base64 sino que
    apunta a `GET /api/email-templates/{template_id}/attachments/by-
    cid/{cid}`. Al enviar, la send-path las re-inyecta como inline
    parts del MIME con `Content-ID: <cid>`."""

    __tablename__ = "email_template_attachments"
    __table_args__ = (
        Index(
            "ix_email_template_attachments_template_cid",
            "template_id",
            "original_cid",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("email_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_cid: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # MEDIUMBLOB (16 MB) en MySQL, BLOB en SQLite. Una imagen sola
    # casi nunca llega al cap; las que sí lo harían (raras) habría que
    # comprimir antes de subirlas al draft.
    data: Mapped[bytes] = mapped_column(
        LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
