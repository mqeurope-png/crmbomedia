from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import (
    AuditLog,
    Company,
    Contact,
    ExternalReference,
    ExternalSystem,
    Note,
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


def get_contact_by_email(session: Session, email: str) -> Contact | None:
    return session.scalar(select(Contact).where(Contact.email == email.lower()))


def _apply_contact_filters(
    statement: Select,
    *,
    q: str | None = None,
    tag: str | None = None,
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
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
        # Contact.tags is a CSV string. Match `tag` as a whole token
        # without false positives ("VIP" must not match "VIPS"). The
        # four-way OR is portable across SQLite/MySQL and lets the
        # query plan use the column directly.
        statement = statement.where(
            or_(
                Contact.tags == tag,
                Contact.tags.like(f"{tag},%"),
                Contact.tags.like(f"%,{tag},%"),
                Contact.tags.like(f"%,{tag}"),
            )
        )
    if origin_system is not None or origin_account_id:
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
    return statement


def list_contacts(
    session: Session,
    *,
    q: str | None = None,
    tag: str | None = None,
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
    skip: int = 0,
    limit: int = 25,
    include_inactive: bool = False,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> list[Contact]:
    statement = select(Contact)
    statement = _apply_contact_filters(
        statement,
        q=q,
        tag=tag,
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
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
    origin_system: ExternalSystem | None = None,
    origin_account_id: str | None = None,
    commercial_status: str | None = None,
    marketing_consent: str | None = None,
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
        origin_system=origin_system,
        origin_account_id=origin_account_id,
        commercial_status=commercial_status,
        marketing_consent=marketing_consent,
        include_inactive=include_inactive,
    )
    return int(session.scalar(statement) or 0)


def list_notes(session: Session, contact_id: str) -> list[Note]:
    statement = select(Note).where(Note.contact_id == contact_id).order_by(Note.created_at.desc())
    return list(session.scalars(statement))


def list_tasks(session: Session, contact_id: str) -> list[Task]:
    statement = select(Task).where(Task.contact_id == contact_id).order_by(Task.created_at.desc())
    return list(session.scalars(statement))


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
