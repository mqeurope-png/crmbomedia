from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import AuditLog, Company, Contact, Note, Task, User


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


def list_contacts(
    session: Session, q: str | None, skip: int, limit: int, include_inactive: bool = False
) -> list[Contact]:
    statement = select(Contact).order_by(Contact.created_at.desc()).offset(skip).limit(limit)
    if not include_inactive:
        statement = statement.where(Contact.is_active.is_(True))
    if q:
        like = f"%{q}%"
        statement = statement.where(
            Contact.first_name.ilike(like)
            | Contact.last_name.ilike(like)
            | Contact.email.ilike(like)
        )
    return list(session.scalars(statement))


def list_notes(session: Session, contact_id: str) -> list[Note]:
    statement = select(Note).where(Note.contact_id == contact_id).order_by(Note.created_at.desc())
    return list(session.scalars(statement))


def list_tasks(session: Session, contact_id: str) -> list[Task]:
    statement = select(Task).where(Task.contact_id == contact_id).order_by(Task.created_at.desc())
    return list(session.scalars(statement))


def create_audit_log(
    session: Session,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    message: str | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
    )
    session.add(audit_log)
    return audit_log


def list_audit_logs(session: Session, skip: int, limit: int) -> list[AuditLog]:
    statement = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    return list(session.scalars(statement))


def list_all_audit_logs(session: Session) -> list[AuditLog]:
    statement = select(AuditLog).order_by(AuditLog.created_at.desc())
    return list(session.scalars(statement))
