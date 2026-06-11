from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import (
    ActivityEvent,
    AuditLog,
    Company,
    Contact,
    ContactTag,
    ExternalReference,
    ExternalSystem,
    Note,
    Tag,
    Task,
    User,
)

# Valid contact list sort keys. Anything else falls back to the
# default `created_at desc` so a malicious or misspelt `sort_by` can't
# leak unindexed columns into the ORDER BY.
CONTACT_SORT_COLUMNS = {
    "name": Contact.first_name,
    "first_name": Contact.first_name,
    "last_name": Contact.last_name,
    "email": Contact.email,
    "created_at": Contact.created_at,
    "updated_at": Contact.updated_at,
    "created_at_external": Contact.created_at_external,
    "updated_at_external": Contact.updated_at_external,
    "lead_score": Contact.lead_score,
}


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email.lower()))


def list_users(session: Session, skip: int, limit: int) -> list[User]:
    statement = select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
    return list(session.scalars(statement))


def get_user_by_reset_token_hash(session: Session, token_hash: str) -> User | None:
    return session.scalar(select(User).where(User.password_reset_token_hash == token_hash))


def get_company(session: Session, company_id: str) -> Company | None:
    return session.get(Company, company_id)


def list_companies(
    session: Session, q: str | None, skip: int, limit: int, include_inactive: bool = False
) -> list[Company]:
    statement = select(Company).order_by(Company.name).offset(skip).limit(limit)
    if not include_inactive:
        statement = statement.where(Company.is_active.is_(True))
    if q:
        statement = statement.where(Company.name.ilike(f"%{q}%"))
    return list(session.scalars(statement))


def count_companies(
    session: Session, q: str | None = None, include_inactive: bool = False
) -> int:
    """Return the total number of companies matching the same filters
    the list endpoint applies — used by the dashboard stat cards."""
    statement = select(func.count()).select_from(Company)
    if not include_inactive:
        statement = statement.where(Company.is_active.is_(True))
    if q:
        statement = statement.where(Company.name.ilike(f"%{q}%"))
    return int(session.scalar(statement) or 0)


def get_contact(session: Session, contact_id: str) -> Contact | None:
    return session.scalar(
        select(Contact)
        .options(
            selectinload(Contact.notes),
            selectinload(Contact.tasks),
            selectinload(Contact.external_refs),
        )
        .where(Contact.id == contact_id)
    )


def get_contact_with_timeline(
    session: Session, contact_id: str, *, timeline_limit: int
) -> tuple[Contact, list[ActivityEvent], int] | None:
    """Detail endpoint helper. Returns the contact (with its
    notes/tasks/external_refs eager-loaded), the latest
    `timeline_limit` activity events, and the total event count so the
    UI can decide whether to surface "Ver todos" without a second
    round-trip."""
    contact = get_contact(session, contact_id)
    if contact is None:
        return None
    events = list_activity_events(session, contact_id, skip=0, limit=timeline_limit)
    total = count_activity_events(session, contact_id)
    return contact, events, total


def get_contact_by_email(session: Session, email: str) -> Contact | None:
    return session.scalar(select(Contact).where(Contact.email == email.lower()))


def _apply_contact_filters(
    statement: Select,
    *,
    q: str | None = None,
    tag: str | None = None,
    tag_ids: list[str] | None = None,
    tag_match_mode: str = "any",
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    origin_account_keys: list[str] | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
    lead_score_min: int | None = None,
    lead_score_max: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    include_inactive: bool = False,
) -> Select:
    """Single source of truth for contact-list filtering. Used by both
    `list_contacts` and `count_contacts` so the dashboard stat cards
    and the list endpoint always agree on which rows match."""
    if not include_inactive:
        statement = statement.where(Contact.is_active.is_(True))
    if q:
        like = f"%{q}%"
        statement = statement.where(
            Contact.first_name.ilike(like)
            | Contact.last_name.ilike(like)
            | Contact.email.ilike(like)
            | Contact.phone.ilike(like)
        )
    if tag:
        # Legacy `tag=` filter: match against the new `tags` table by
        # exact name. Replaces the old CSV-substring approach so
        # operators with bookmarked URLs keep working after the M:N
        # migration.
        statement = statement.where(
            Contact.id.in_(
                select(ContactTag.contact_id)
                .join(Tag, Tag.id == ContactTag.tag_id)
                .where(Tag.name_normalized == tag.strip().lower())
            )
        )
    if tag_ids:
        # New multi-tag filter. `any` matches contacts with at least one
        # of the requested tags (IN subquery); `all` requires every
        # tag (count subquery). The count form stays portable across
        # SQLite + MySQL because it avoids correlated GROUP BY HAVING
        # tricks.
        if tag_match_mode == "all":
            statement = statement.where(
                Contact.id.in_(
                    select(ContactTag.contact_id)
                    .where(ContactTag.tag_id.in_(tag_ids))
                    .group_by(ContactTag.contact_id)
                    .having(func.count(func.distinct(ContactTag.tag_id)) == len(tag_ids))
                )
            )
        else:
            statement = statement.where(
                Contact.id.in_(
                    select(ContactTag.contact_id).where(
                        ContactTag.tag_id.in_(tag_ids)
                    )
                )
            )
    if origin_account_keys:
        # New (preferred) path: pairs of `(system, account_id)` —
        # operators with 9 AgileCRM accounts need to pick concrete
        # ones, not the entire system. Format: "system:account_id".
        # Invalid entries are silently dropped — the caller already
        # validated via the available-origin-accounts endpoint.
        pairs = []
        for raw in origin_account_keys:
            if not raw or ":" not in raw:
                continue
            system_slug, _, account_id = raw.partition(":")
            if not system_slug or not account_id:
                continue
            try:
                pairs.append(
                    (ExternalSystem(system_slug.strip()), account_id.strip())
                )
            except ValueError:
                continue
        if pairs:
            from sqlalchemy import and_, or_, tuple_  # noqa: PLC0415

            # `IN (tuple, tuple)` is the cleanest write but MySQL 8
            # supports it natively while SQLite emulates row values
            # only since 3.15 — playing safe with an OR-of-AND chain
            # so the same SQL plan runs on both backends.
            clauses = [
                and_(
                    ExternalReference.system == system,
                    ExternalReference.account_id == account_id,
                )
                for system, account_id in pairs
            ]
            subq = select(ExternalReference.contact_id).where(or_(*clauses))
            statement = statement.where(Contact.id.in_(subq))
            # `tuple_` import kept available for future MySQL-only
            # optimisations.
            _ = tuple_
    elif origin_system is not None or origin_account_id:
        # Legacy path kept for bookmarked URLs + the migration window.
        # New code should send `origin_account_keys` instead.
        subq = select(ExternalReference.contact_id)
        if origin_system is not None:
            subq = subq.where(ExternalReference.system == origin_system)
        if origin_account_id:
            subq = subq.where(ExternalReference.account_id == origin_account_id)
        statement = statement.where(Contact.id.in_(subq))
    if commercial_status:
        statement = statement.where(Contact.commercial_status == commercial_status)
    if marketing_consent:
        statement = statement.where(Contact.marketing_consent == marketing_consent)
    if lead_score_min is not None:
        statement = statement.where(Contact.lead_score >= lead_score_min)
    if lead_score_max is not None:
        statement = statement.where(Contact.lead_score <= lead_score_max)
    if created_after:
        statement = statement.where(Contact.created_at >= created_after)
    if created_before:
        statement = statement.where(Contact.created_at <= created_before)
    return statement


def list_contacts(
    session: Session,
    *,
    q: str | None = None,
    tag: str | None = None,
    tag_ids: list[str] | None = None,
    tag_match_mode: str = "any",
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    origin_account_keys: list[str] | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
    lead_score_min: int | None = None,
    lead_score_max: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    skip: int = 0,
    limit: int = 25,
    include_inactive: bool = False,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> list[Contact]:
    statement = select(Contact).options(
        selectinload(Contact.tag_assignments).selectinload(ContactTag.tag),
        # Eager-load so `ContactRead.external_references_summary` (which
        # reads `contact.external_refs`) doesn't fire one SELECT per row.
        selectinload(Contact.external_refs),
    )
    statement = _apply_contact_filters(
        statement,
        q=q,
        tag=tag,
        tag_ids=tag_ids,
        tag_match_mode=tag_match_mode,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        origin_account_keys=origin_account_keys,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        lead_score_min=lead_score_min,
        lead_score_max=lead_score_max,
        created_after=created_after,
        created_before=created_before,
        include_inactive=include_inactive,
    )
    sort_column = CONTACT_SORT_COLUMNS.get(sort_by, Contact.created_at)
    order = sort_column.desc() if sort_dir.lower() == "desc" else sort_column.asc()
    statement = statement.order_by(order).offset(skip).limit(limit)
    return list(session.scalars(statement))


def count_contacts(
    session: Session,
    *,
    q: str | None = None,
    tag: str | None = None,
    tag_ids: list[str] | None = None,
    tag_match_mode: str = "any",
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    origin_account_keys: list[str] | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
    lead_score_min: int | None = None,
    lead_score_max: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    include_inactive: bool = False,
) -> int:
    """Return the total number of contacts matching the same filters the
    list endpoint applies — used by the dashboard stat cards so they
    don't reflect the paginated page size, and by `/contacts` itself so
    the wrapped response can include the running total."""
    statement = select(func.count()).select_from(Contact)
    statement = _apply_contact_filters(
        statement,
        q=q,
        tag=tag,
        tag_ids=tag_ids,
        tag_match_mode=tag_match_mode,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        origin_account_keys=origin_account_keys,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        lead_score_min=lead_score_min,
        lead_score_max=lead_score_max,
        created_after=created_after,
        created_before=created_before,
        include_inactive=include_inactive,
    )
    return int(session.scalar(statement) or 0)


def search_contacts(
    session: Session,
    *,
    filter_clause: Any | None = None,
    q: str | None = None,
    skip: int = 0,
    limit: int = 25,
    include_inactive: bool = False,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> tuple[list[Contact], int]:
    """List + count contacts under one prebuilt SQLAlchemy filter.

    Returns `(items, total)` so the route can answer both the paginated
    page and the total counter without re-building the WHERE. The
    free-text `q` rides on top so a saved query can be narrowed
    further from the search box without rewriting the rule tree."""
    base = (
        select(Contact)
        .options(
            selectinload(Contact.tag_assignments).selectinload(ContactTag.tag),
            selectinload(Contact.external_refs),
        )
    )
    if not include_inactive:
        base = base.where(Contact.is_active.is_(True))
    if filter_clause is not None:
        base = base.where(filter_clause)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(
            or_(
                func.lower(Contact.first_name).like(like),
                func.lower(Contact.last_name).like(like),
                func.lower(Contact.email).like(like),
                func.lower(Contact.phone).like(like),
            )
        )
    total_stmt = select(func.count()).select_from(base.subquery())
    total = int(session.scalar(total_stmt) or 0)

    sort_column = CONTACT_SORT_COLUMNS.get(sort_by, Contact.created_at)
    order = sort_column.desc() if sort_dir.lower() == "desc" else sort_column.asc()
    page_stmt = base.order_by(order).offset(skip).limit(limit)
    items = list(session.scalars(page_stmt))
    return items, total


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def normalize_tag_name(name: str) -> str:
    """Single source of truth for the case-insensitive tag key. Used by
    every layer (mapper, repository, route) so a tag created from the
    UI as "VIP" matches one imported from AgileCRM as "vip"."""
    return name.strip().lower()


def get_tag(session: Session, tag_id: str) -> Tag | None:
    return session.get(Tag, tag_id)


def get_tag_by_name(session: Session, name: str) -> Tag | None:
    return session.scalar(
        select(Tag).where(Tag.name_normalized == normalize_tag_name(name))
    )


def list_tags(
    session: Session,
    *,
    q: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[Tag], list[int], int]:
    """Return one page of tags + parallel list of contact counts + the
    grand total so the UI can paginate without a second round-trip.

    The count subquery is a single LEFT JOIN GROUP BY so we don't fire
    N+1 queries — important for tenants with thousands of tags."""
    base = select(Tag)
    if q:
        like = f"%{q.strip().lower()}%"
        base = base.where(Tag.name_normalized.like(like))
    total = int(
        session.scalar(select(func.count()).select_from(base.subquery())) or 0
    )
    statement = base.order_by(Tag.name).offset(skip).limit(limit)
    tags = list(session.scalars(statement))
    if not tags:
        return [], [], total
    counts_rows = session.execute(
        select(ContactTag.tag_id, func.count(ContactTag.contact_id))
        .where(ContactTag.tag_id.in_([t.id for t in tags]))
        .group_by(ContactTag.tag_id)
    ).all()
    counts_by_id = {tag_id: count for tag_id, count in counts_rows}
    return tags, [counts_by_id.get(t.id, 0) for t in tags], total


def upsert_tag(
    session: Session,
    *,
    name: str,
    color: str | None = None,
    description: str | None = None,
    created_by_user_id: str | None = None,
) -> tuple[Tag, bool]:
    """Get-or-create a tag by case-insensitive name. Returns
    `(tag, created)`. The unique constraint guarantees no race ever
    persists two rows for the same normalized name."""
    normalized = normalize_tag_name(name)
    existing = session.scalar(
        select(Tag).where(Tag.name_normalized == normalized)
    )
    if existing is not None:
        return existing, False
    tag = Tag(
        name=name.strip(),
        name_normalized=normalized,
        color=color,
        description=description,
        created_by_user_id=created_by_user_id,
    )
    session.add(tag)
    session.flush()
    return tag, True


def assign_tag_to_contact(
    session: Session,
    *,
    contact_id: str,
    tag_id: str,
    assigned_by_user_id: str | None,
    source: str | None,
) -> bool:
    """Idempotent: returns True when a new link was written, False when
    the contact already had the tag (so the route can audit only real
    additions)."""
    existing = session.get(ContactTag, {"contact_id": contact_id, "tag_id": tag_id})
    if existing is not None:
        return False
    session.add(
        ContactTag(
            contact_id=contact_id,
            tag_id=tag_id,
            assigned_by_user_id=assigned_by_user_id,
            source=source,
        )
    )
    session.flush()
    return True


def remove_tag_from_contact(
    session: Session, *, contact_id: str, tag_id: str
) -> bool:
    existing = session.get(ContactTag, {"contact_id": contact_id, "tag_id": tag_id})
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


def list_notes(session: Session, contact_id: str) -> list[Note]:
    statement = select(Note).where(Note.contact_id == contact_id).order_by(Note.created_at.desc())
    return list(session.scalars(statement))


def list_tasks(session: Session, contact_id: str) -> list[Task]:
    statement = select(Task).where(Task.contact_id == contact_id).order_by(Task.created_at.desc())
    return list(session.scalars(statement))


#: How many timeline events the detail endpoint embeds in the contact
#: response. Anything beyond that the operator paginates through the
#: dedicated `/contacts/{id}/activity-events` endpoint.
ACTIVITY_EVENTS_INLINE_LIMIT = 50


def list_activity_events(
    session: Session, contact_id: str, *, skip: int = 0, limit: int = 50
) -> list[ActivityEvent]:
    statement = (
        select(ActivityEvent)
        .where(ActivityEvent.contact_id == contact_id)
        .order_by(ActivityEvent.occurred_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(session.scalars(statement))


def count_activity_events(session: Session, contact_id: str) -> int:
    statement = (
        select(func.count())
        .select_from(ActivityEvent)
        .where(ActivityEvent.contact_id == contact_id)
    )
    return int(session.scalar(statement) or 0)


def _audit_query(
    *,
    action: str | None,
    action_prefix: str | None,
    actor_user_id: str | None,
    target_type: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
):
    statement = select(AuditLog)
    if action:
        statement = statement.where(AuditLog.action == action)
    if action_prefix:
        statement = statement.where(AuditLog.action.like(f"{action_prefix}%"))
    if actor_user_id:
        statement = statement.where(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        statement = statement.where(AuditLog.target_type == target_type)
    if from_date:
        statement = statement.where(AuditLog.created_at >= from_date)
    if to_date:
        statement = statement.where(AuditLog.created_at <= to_date)
    return statement


def list_audit_logs(
    session: Session,
    *,
    skip: int = 0,
    limit: int = 50,
    action: str | None = None,
    action_prefix: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[AuditLog]:
    statement = _audit_query(
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    statement = statement.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    return list(session.scalars(statement))


def count_audit_logs(
    session: Session,
    *,
    action: str | None = None,
    action_prefix: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> int:
    statement = _audit_query(
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def list_audit_logs_for_export(
    session: Session,
    *,
    max_rows: int,
    action: str | None = None,
    action_prefix: str | None = None,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[AuditLog]:
    """Like list_audit_logs but without offset and capped at max_rows + 1 so
    the caller can detect overflow without scanning the whole table."""
    statement = _audit_query(
        action=action,
        action_prefix=action_prefix,
        actor_user_id=actor_user_id,
        target_type=target_type,
        from_date=from_date,
        to_date=to_date,
    )
    statement = statement.order_by(AuditLog.created_at.desc()).limit(max_rows + 1)
    return list(session.scalars(statement))
