"""Sprint Web-Forms — generador de formularios web propios.

Tres tablas:
  - `web_forms`         config de cada formulario (post-submit, asignación,
                        anti-spam, marca/idioma).
  - `web_form_fields`   campos ordenados de cada form.
  - `form_submissions`  histórico de TODOS los submits (spam incluido) para
                        auditoría/debug.

Los "enums" se guardan como String + constantes validadas en la capa API
(no `ENUM` de MySQL) para que SQLite en tests y MySQL en prod se comporten
igual — mismo criterio que `contacts.commercial_status`. Los blobs JSON
(`options_json`, `raw_payload_json`) son Text con json.dumps, como el resto
del código (`metadata_json`, `custom_fields`).
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.crm import Base, TimestampMixin

# --- constantes de validación (capa API) -----------------------------------

SUBMIT_SUCCESS_MODES = {"modal", "redirect"}
ASSIGNMENT_MODES = {"rules", "fixed_owner", "none"}
# `tags` = multi-select de tags reales del CRM; sus opciones (options_json)
# guardan [{tag_id, label}] y al submit se aplican al contacto. No requiere
# migración: `field_type` es String(16), no un ENUM de MySQL.
FIELD_TYPES = {
    "text", "email", "tel", "textarea", "select", "checkbox", "hidden", "tags",
}
SPAM_REASONS = {
    "honeypot", "recaptcha_low_score", "rate_limit", "invalid_email",
    "missing_required",
}


class WebForm(TimestampMixin, Base):
    __tablename__ = "web_forms"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="es")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Post-submit.
    submit_success_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="modal"
    )
    submit_success_message: Mapped[str | None] = mapped_column(Text)
    submit_redirect_url: Mapped[str | None] = mapped_column(String(512))

    # Email de confirmación al lead.
    send_confirmation_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    confirmation_email_template_id: Mapped[str | None] = mapped_column(String(36))

    # Asignación del contacto capturado.
    assignment_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="rules"
    )
    fixed_owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    notify_owner_on_new: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    recaptcha_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )

    fields: Mapped[list[WebFormField]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="WebFormField.position",
    )


class WebFormField(Base):
    __tablename__ = "web_form_fields"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    form_id: Mapped[str] = mapped_column(
        ForeignKey("web_forms.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[str] = mapped_column(String(16), nullable=False)
    placeholder: Mapped[str | None] = mapped_column(String(255))
    help_text: Mapped[str | None] = mapped_column(String(512))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(String(512))
    options_json: Mapped[str | None] = mapped_column(Text)  # select: [{value,label}]
    validation_pattern: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    maps_to_contact_field: Mapped[str | None] = mapped_column(String(64))

    form: Mapped[WebForm] = relationship(back_populates="fields")

    __table_args__ = (
        Index("idx_form_fields_form", "form_id", "position"),
    )


class FormSubmission(Base):
    __tablename__ = "form_submissions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    form_id: Mapped[str] = mapped_column(
        ForeignKey("web_forms.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spam_reason: Mapped[str | None] = mapped_column(String(128))
    recaptcha_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    referrer: Mapped[str | None] = mapped_column(String(512))
    landing_page: Mapped[str | None] = mapped_column(String(512))
    utm_source: Mapped[str | None] = mapped_column(String(128))
    utm_medium: Mapped[str | None] = mapped_column(String(128))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_submissions_form_created", "form_id", "created_at"),
        Index("idx_submissions_contact", "contact_id"),
    )
