from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    # Nullable on purpose: ingesters routinely surface malformed
    # addresses ("emete@emete@emete.cat") that can't be repaired
    # downstream. The mapper writes NULL in that case so the read
    # schema can tell the difference between "we have no email" and
    # "we have an unusable string" — `is_email_valid` flags the latter
    # when something IS stored but failed the validator. Migration
    # 20260606_0019 relaxed the column from NOT NULL.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
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
    # Free-form per-system extras (AgileCRM custom properties today,
    # other systems' equivalents tomorrow). Stored as JSON text so the
    # operator can query it cheaply without a side table; the API
    # exposes it decoded.
    custom_fields: Mapped[str | None] = mapped_column(Text)
    # Address components captured by AgileCRM's address property and
    # other CRMs alike. Kept as separate columns (instead of nested
    # JSON) so filters / sort are straightforward.
    address_country: Mapped[str | None] = mapped_column(String(120))
    address_country_name: Mapped[str | None] = mapped_column(String(255))
    address_state: Mapped[str | None] = mapped_column(String(120))
    address_city: Mapped[str | None] = mapped_column(String(120))
    # AgileCRM lead score. Other systems push their own scoring under
    # the same column for consistency.
    lead_score: Mapped[int | None] = mapped_column(Integer)
    # When the operator last clicked "Actualizar desde AgileCRM" on
    # this contact's detail page. Drives the `external_data_freshness`
    # flag in the API response; null means "never refreshed
    # on-demand" (so the UI surfaces an "Outdated" banner). Kept on
    # `contacts` instead of MAX(synced_at) over the child tables so a
    # contact with zero notes/tasks/events still records its last
    # refresh attempt.
    external_data_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Real creation / last-modification timestamps in the source
    # system(s), NOT the CRM row's `created_at`/`updated_at`. The
    # operator wants to see "this contact entered Brevo in March 2025"
    # instead of "it synced into the CRM in May 2026". Populated by the
    # connector mappers from each payload; when a contact lives in more
    # than one system the merge policy keeps the OLDEST creation (the
    # earliest system is the real origin) and the NEWEST modification.
    # NULL until a sync carries a parseable date — we never invent one.
    created_at_external: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at_external: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

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
    activity_events: Mapped[list["ActivityEvent"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    tag_assignments: Mapped[list["ContactTag"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )

    @property
    def tag_objects(self) -> list["Tag"]:
        """Flatten the M:N relationship for API serialisation. The
        Pydantic `ContactRead.tag_objects` field reads this via
        `from_attributes`; the manual flatten keeps the schema layer
        ignorant of the through-table."""
        return [assignment.tag for assignment in self.tag_assignments]

    @property
    def external_references_summary(self) -> list[dict[str, str]]:
        """Compact `(system, account_id)` list for the contacts list
        endpoint — enough to render the origin chips per row without
        shipping the full `external_refs` payload. `ContactRead`
        reads it via `from_attributes`. Callers that hit this on a
        list MUST eager-load `external_refs` (the repository does) or
        each row triggers a lazy SELECT."""
        out: list[dict[str, str]] = []
        for ref in self.external_refs:
            system = ref.system.value if hasattr(ref.system, "value") else str(ref.system)
            out.append({"system": system, "account_id": ref.account_id})
        return out


class Tag(TimestampMixin, Base):
    """Reusable contact tag. The case-insensitive uniqueness is enforced
    by a normalized companion column (`name_normalized`) so SQLite and
    MySQL behave identically without relying on functional indexes."""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("name_normalized", name="uq_tag_name_normalized"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Lowercased + stripped form of `name`. We dedup on this column so
    # "VIP", "vip" and " VIP " all collapse to one tag.
    name_normalized: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String(7))
    description: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36))

    assignments: Mapped[list["ContactTag"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class ContactTag(Base):
    """M:N row between `contacts` and `tags`. `source` records where the
    assignment came from ("agilecrm:default", "manual", "import",
    "migrated_from_csv") so a tag set in AgileCRM doesn't survive a
    manual unassign and vice versa."""

    __tablename__ = "contact_tags"

    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    assigned_by_user_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str | None] = mapped_column(String(80))

    contact: Mapped[Contact] = relationship(back_populates="tag_assignments")
    tag: Mapped[Tag] = relationship(back_populates="assignments")


class ContactView(TimestampMixin, Base):
    """Saved contacts-list configuration (filters + columns + sort)
    that an operator can name, share with others read-only, and mark as
    their default landing view.

    `is_shared` opens the row to read-by-anyone — the front-end greys
    out edit affordances when `owner_user_id != current_user.id`.
    `is_default` is enforced at most once per `owner_user_id` from the
    route layer (no DB-level partial unique because portable SQLite
    doesn't have them; the route always demotes the previous default
    inside the same transaction)."""

    __tablename__ = "contact_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Three opaque JSON blobs (text). The route layer decodes them
    # before returning — same trick AuditLog / ExternalReference use
    # for their `metadata` column, except `columns` would clash with
    # the SQLAlchemy Table.columns descriptor so all three use
    # `_json` suffixes consistently.
    filters_json: Mapped[str | None] = mapped_column(Text)
    columns_json: Mapped[str | None] = mapped_column(Text)
    sort_json: Mapped[str | None] = mapped_column(Text)


class Pipeline(TimestampMixin, Base):
    """A named sequence of stages a contact moves through. A tenant
    can run several pipelines side by side (Ventas, Reactivación,
    Onboarding) and the same contact can sit in more than one.

    `is_shared` defaults to True — pipelines are an
    organisation-level concept; a private pipeline is the unusual
    case. `is_active` is the soft-delete; the row stays for history.
    """

    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(7))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    is_shared: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.position",
    )
    contact_assignments: Mapped[list["ContactPipelineStage"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan"
    )


class PipelineStage(TimestampMixin, Base):
    """One ordered step inside a pipeline. Positions are kept
    contiguous (0..N-1) by the reorder endpoint — there's no DB-level
    constraint enforcing it because portable SQLite + MySQL don't
    have a clean way to express "no gaps", but every mutation route
    rewrites the positions through the repository helper."""

    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id", "position", name="uq_pipeline_stage_position"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    pipeline_id: Mapped[str] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(7))
    is_won: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_lost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    target_days: Mapped[int | None] = mapped_column(Integer)

    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")
    contact_assignments: Mapped[list["ContactPipelineStage"]] = relationship(
        back_populates="stage"
    )


class ContactPipelineStage(TimestampMixin, Base):
    """The row that says "contact C is in stage S of pipeline P". A
    contact has at most one such row per pipeline (the unique key
    enforces it) and moves between stages by updating
    `stage_id` + `entered_stage_at`, while the repository writes a
    `ContactStageHistory` row in the same transaction."""

    __tablename__ = "contact_pipeline_stages"
    __table_args__ = (
        UniqueConstraint(
            "contact_id",
            "pipeline_id",
            name="uq_contact_pipeline_single_stage",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_id: Mapped[str] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False, index=True
    )
    entered_stage_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    added_to_pipeline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    notes: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pipeline: Mapped[Pipeline] = relationship(back_populates="contact_assignments")
    stage: Mapped[PipelineStage] = relationship(back_populates="contact_assignments")
    history: Mapped[list["ContactStageHistory"]] = relationship(
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="ContactStageHistory.moved_at",
    )


class ContactStageHistory(Base):
    """Audit trail of stage transitions for one
    `contact_pipeline_stages` row. The initial add writes one row with
    `from_stage_id=NULL`; every subsequent move writes another with
    the prior stage and the duration the contact spent in it.
    Reports (avg time per stage, conversion rate) aggregate over this
    table."""

    __tablename__ = "contact_stage_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    contact_pipeline_stage_id: Mapped[str] = mapped_column(
        ForeignKey("contact_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_stage_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_stages.id")
    )
    to_stage_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_stages.id"), nullable=False
    )
    moved_by_user_id: Mapped[str | None] = mapped_column(String(36))
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    duration_seconds_in_previous_stage: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    assignment: Mapped[ContactPipelineStage] = relationship(back_populates="history")


class Segment(TimestampMixin, Base):
    """Dynamic group of contacts defined by a boolean rule tree.

    Re-evaluated on demand via the rules engine in
    `app/services/segments/engine.py`. `cached_count` +
    `last_evaluated_at` are populated by the route layer on
    create/update/manual-refresh so the list page renders without
    re-running the SQL on every render.

    `is_dynamic=False` switches the segment to a frozen list of
    `static_contact_ids` — used for one-off "send to these 50 people"
    workflows that should NOT pick up new matches over time.
    """

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rules_json: Mapped[str | None] = mapped_column(Text)
    is_dynamic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    static_contact_ids: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    color: Mapped[str | None] = mapped_column(String(7))
    cached_count: Mapped[int | None] = mapped_column(Integer)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Marker for externally-managed segments (Brevo segments mirror).
    # Format: `<system>:<account_id>:<external_id>` — when populated
    # the segment behaves as `is_dynamic=False` with `static_contact_ids`
    # refreshed periodically by the connector job; the UI hides the
    # rule editor and shows "Espejo Brevo" + "Refrescar / Abrir en Brevo".
    # NULL = ordinary CRM-native segment.
    external_source: Mapped[str | None] = mapped_column(String(150))
    external_last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    external_refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer)


class Note(TimestampMixin, Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(String(36))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    # Provenance for notes imported from an external CRM (AgileCRM today,
    # Brevo/Freshdesk tomorrow). All four are NULL for manual notes so
    # the UI form keeps working unchanged. The sync job dedups by the
    # (external_system, external_account_id, external_id) triplet via
    # the helper in app/repositories/crm.py — no DB-level unique key
    # because manual notes share the (NULL, NULL, NULL) slot.
    external_system: Mapped[str | None] = mapped_column(String(32))
    external_account_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    # Snapshot of the remote author's email + name. We never resolve to a
    # real `User` row because AgileCRM users aren't us; the operator
    # just needs the name on screen.
    external_author_email: Mapped[str | None] = mapped_column(String(255))
    external_author_name: Mapped[str | None] = mapped_column(String(255))
    external_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contact: Mapped[Contact] = relationship(back_populates="notes")


class TaskStatus(StrEnum):
    # New productivity-layer states (Mini-PR C). PENDING and DONE are
    # the working set; IN_PROGRESS is exposed in the UI for in-flight
    # work; CANCELLED keeps task history without polluting "done".
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    # Legacy alias from Sprint A. Maps to PENDING by the migration; the
    # value lives on so historical audit rows still resolve.
    OPEN = "open"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Task(TimestampMixin, Base):
    """Productivity tasks owned by a CRM user.

    Started in Sprint A as a contact sub-resource (just title + status
    + due + assignee + AgileCRM provenance) and expanded by Mini-PR C
    into a full productivity layer with priority, optional company /
    pipeline-stage links, a separate creator, and Google Calendar
    mirror columns. The migration `20260612_0027` adds the new
    columns in place, renames `assignee_user_id` → `assigned_user_id`
    (and makes it NOT NULL), and relaxes `contact_id` to NULL so the
    operator can keep personal todos.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, values_callable=enum_values, length=32),
        default=TaskStatus.PENDING,
        nullable=False,
        index=True,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, native_enum=False, values_callable=enum_values, length=32),
        default=TaskPriority.MEDIUM,
        nullable=False,
    )
    assigned_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    pipeline_stage_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_stages.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    google_event_id: Mapped[str | None] = mapped_column(String(255))
    google_calendar_id: Mapped[str | None] = mapped_column(String(255))
    reminder_minutes_before: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # AgileCRM provenance trio kept from Sprint A so imports continue
    # to dedupe by (external_system, external_account_id, external_id).
    external_system: Mapped[str | None] = mapped_column(String(32))
    external_account_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contact: Mapped["Contact | None"] = relationship(back_populates="tasks")
    assigned_user: Mapped["User"] = relationship(foreign_keys=[assigned_user_id])

    contact: Mapped[Contact] = relationship(back_populates="tasks")


class ExternalReference(TimestampMixin, Base):
    __tablename__ = "external_references"
    __table_args__ = (
        UniqueConstraint(
            "system",
            "account_id",
            "external_id",
            name="uq_external_reference_system_account_external_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Used by the AgileCRM quota-purge job (and analogous per-system
    # cleanup tasks) to flag references whose remote record was deleted
    # in origin. The row is preserved so the historical link survives.
    external_status: Mapped[str | None] = mapped_column(String(40))
    account_label: Mapped[str | None] = mapped_column(String(255))
    # Mirror of the remote system's own created/updated timestamps —
    # NOT the row's created_at/updated_at in our DB. Lets the dashboard
    # show "last sync touched this on <date> in AgileCRM" without
    # losing the audit trail of when we inserted/updated the row here.
    external_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AgileCRM's `source` field today; other systems' provenance hint
    # tomorrow. Kept opaque on purpose (provider-defined strings).
    origin_detail: Mapped[str | None] = mapped_column(String(255))
    # Free-form per-reference extras (owner snapshot, raw tag array,
    # whatever the connector wants to preserve without polluting the
    # canonical Contact). JSON text under the SQL name `metadata` —
    # the Python attribute is `metadata_json` to avoid the clash with
    # `Base.metadata` (same trick `AuditLog`/`SyncLog` already use).
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="external_refs")


class ActivityEvent(TimestampMixin, Base):
    """Timeline event imported from an external CRM (AgileCRM `activities`
    today). One row per event, dedup'd by `(system, account_id,
    external_id)` so a re-sync doesn't double-write the operator's
    timeline.

    Kept as a thin generic table — `event_type` is the remote system's
    raw type string ("EMAIL_SENT", "NOTE", "FORM_FILL", …) — so the
    Brevo + Freshdesk connectors can reuse the same shape later without
    a schema migration."""

    __tablename__ = "activity_events"
    __table_args__ = (
        UniqueConstraint(
            "system",
            "account_id",
            "external_id",
            name="uq_activity_event_system_account_external_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    contact_id: Mapped[str] = mapped_column(
        ForeignKey("contacts.id"), nullable=False, index=True
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Brevo campaign id (`brevo_campaigns_cache.brevo_campaign_id`),
    # NULL for events outside Brevo's marketing flow. Indexed because
    # `/campaigns/{id}/recipients/{event_type}` filters by this column —
    # without it the endpoint had to fall back to a
    # `external_id LIKE 'backfill:{id}:%'` substring scan that misses
    # webhook events entirely. The webhook + backfill mappers both
    # populate it on insert (see
    # `app/integrations/brevo/{webhooks,historical_backfill}.py`).
    campaign_brevo_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    # Raw remote payload (or the relevant subset) preserved verbatim so
    # the operator can drill in when the summary is ambiguous and the
    # mapper can be improved without a re-sync. JSON text — Python attr
    # is `metadata_json` to avoid the SQLAlchemy `Base.metadata` clash.
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    contact: Mapped[Contact] = relationship(back_populates="activity_events")


class SyncStatus(StrEnum):
    """Lifecycle of a sync_logs row. Created as `PENDING` when an
    operator enqueues a job; flipped to `RUNNING` when the worker picks
    it up; ends in `SUCCESS`, `PARTIAL_SUCCESS` (some records processed,
    some skipped/failed) or `FAILED` (the whole operation aborted)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class SyncTrigger(StrEnum):
    MANUAL = "manual"
    CRON = "cron"
    WEBHOOK = "webhook"


class SyncLog(TimestampMixin, Base):
    """Trace row for every integration operation (manual sync, cron job,
    webhook delivery). The composite `(system, account_id)` matches the
    natural key of `integration_accounts`; the FK is informal (no
    ON DELETE cascade) because we want the audit trail to outlive the
    account if it ever gets removed."""

    __tablename__ = "sync_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    system: Mapped[ExternalSystem] = mapped_column(
        Enum(ExternalSystem, native_enum=False, values_callable=enum_values, length=32),
        nullable=False,
        index=True,
    )
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)
    operation: Mapped[str | None] = mapped_column(String(120), index=True)
    # `direction` is the pre-refactor field; kept nullable so the column
    # remains backwards compatible while the new `operation` carries the
    # canonical name (`sync_contacts`, `webhook_received`, ...).
    direction: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=SyncStatus.PENDING.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(String(32))
    triggered_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
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


class UserGoogleIntegration(TimestampMixin, Base):
    """Per-user Google Calendar connection.

    Mini-PR C Fase 2. One row per user once they complete the OAuth
    flow. `access_token` + `refresh_token` are encrypted with the same
    Fernet key the integrations layer uses for API keys
    (`INTEGRATION_SECRETS_KEY`). `selected_calendar_id` stays NULL
    until the user picks one in the post-OAuth setup screen — the
    sync layer is a no-op until then.
    """

    __tablename__ = "user_google_integrations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    google_email: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    selected_calendar_id: Mapped[str | None] = mapped_column(String(255))
    selected_calendar_summary: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
