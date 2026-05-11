from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class ConsentStatus(StrEnum):
    UNKNOWN = "unknown"
    GRANTED = "granted"
    DENIED = "denied"
    UNSUBSCRIBED = "unsubscribed"


class ExternalSystem(StrEnum):
    AGILECRM = "agilecrm"
    BREVO = "brevo"
    FRESHDESK = "freshdesk"
    FACTUSOL = "factusol"


class UserRole(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    VIEWER = "viewer"


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tax_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    website: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="company")


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    commercial_status: Mapped[str] = mapped_column(String(80), default="new", nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(36))
    marketing_consent: Mapped[ConsentStatus] = mapped_column(
        Enum(
            ConsentStatus,
            native_enum=False,
            values_callable=enum_values,
            length=32,
        ),
        default=ConsentStatus.UNKNOWN,
        nullable=False,
    )
    is_email_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))

    company: Mapped[Company | None] = relationship(back_populates="contacts")
    notes: Mapped[list["Note"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    external_refs: Mapped[list["ExternalReference"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class Note(TimestampMixin, Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(String(36))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="notes")


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, values_callable=enum_values, length=32),
        default=TaskStatus.OPEN,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee_user_id: Mapped[str | None] = mapped_column(String(36))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="tasks")


class ExternalReference(TimestampMixin, Base):
    __tablename__ = "external_references"
    __table_args__ = (UniqueConstraint("system", "external_id", name="uq_external_reference"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_label: Mapped[str | None] = mapped_column(String(255))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="external_refs")


class SyncLog(TimestampMixin, Base):
    __tablename__ = "sync_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=enum_values, length=32),
        default=UserRole.VIEWER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_reset_token_hash: Mapped[str | None] = mapped_column(String(255))
    password_reset_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # TOTP 2FA. Secret is encrypted at rest with the Fernet key reused from
    # the integration-credentials work. backup_codes_hash holds a JSON array
    # of one-time pbkdf2 hashes; consumed codes are removed from the list.
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backup_codes_hash: Mapped[str | None] = mapped_column(Text)


class GdprRequestType(StrEnum):
    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    PORTABILITY = "portability"
    OBJECTION = "objection"


class GdprRequestStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class GdprRequest(TimestampMixin, Base):
    """A data-subject rights request under GDPR (RGPD).

    Stored as a tracking record only: the actual processing (export file
    generation, contact erasure, audit-log anonymisation, consent flip) is
    performed by `app.services.gdpr.process_request` and recorded both on
    this row (status + completed_at + evidence_path) and in `audit_logs`
    via `gdpr.*` events.
    """

    __tablename__ = "gdpr_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject_contact_id: Mapped[str | None] = mapped_column(String(36))
    request_type: Mapped[GdprRequestType] = mapped_column(
        Enum(
            GdprRequestType,
            native_enum=False,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[GdprRequestStatus] = mapped_column(
        Enum(
            GdprRequestStatus,
            native_enum=False,
            values_callable=enum_values,
            length=32,
        ),
        default=GdprRequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requester_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    # Filesystem path to the generated export (access/portability). Stored
    # as relative path under the export root so the row survives a host
    # migration without rewriting absolute paths.
    evidence_path: Mapped[str | None] = mapped_column(String(512))


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(36))
    # `metadata` collides with SQLAlchemy's Base.metadata, so the Python
    # attribute is metadata_json while the underlying column keeps the
    # short name in SQL.
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
    message: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # fits IPv6
    user_agent: Mapped[str | None] = mapped_column(Text)
