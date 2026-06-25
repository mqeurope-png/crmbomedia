"""Repository helpers for `contact_assignments`.

Sprint Reglas-Assign — PR-A. The source of truth for "who is assigned
to a contact" is the `contact_assignments` M:N table. `contacts.
owner_user_id` is a denormalised CACHE of the primary's user_id,
recomputed here in code (no DB trigger) whenever the assignment set
changes.

Two invariants enforced in app-logic (not DB constraints, for
SQLite↔MySQL portability — same approach as `contact_phones`):

1. At most one `is_primary=True` row per contact — `set_primary`
   clears every sibling then sets the target in one transaction.
2. `contacts.owner_user_id` always equals the primary's user_id (or
   NULL when there's no primary) — `recompute_primary_cache`.

No commit happens here; callers own the transaction boundary.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.crm import Contact, ContactAssignment


def list_for_contact(
    session: Session, contact_id: str
) -> list[ContactAssignment]:
    return list(
        session.scalars(
            select(ContactAssignment)
            .where(ContactAssignment.contact_id == contact_id)
            .order_by(
                ContactAssignment.is_primary.desc(),
                ContactAssignment.assigned_at.asc(),
            )
        )
    )


def get_assignment(
    session: Session, assignment_id: str
) -> ContactAssignment | None:
    return session.get(ContactAssignment, assignment_id)


def find(
    session: Session, *, contact_id: str, user_id: str
) -> ContactAssignment | None:
    return session.scalar(
        select(ContactAssignment).where(
            ContactAssignment.contact_id == contact_id,
            ContactAssignment.user_id == user_id,
        )
    )


def recompute_primary_cache(session: Session, contact_id: str) -> None:
    """Sync `contacts.owner_user_id` to the current primary assignment
    (or NULL if none). Call inside the same transaction after any
    mutation of the contact's assignment set.

    Sprint-Push-CRM-Brevo. Esta función es el chokepoint único de
    cambios de owner — PATCH, reglas, workflows y bulk pasan por aquí.
    Si el primary cambia, registramos un evento en `session.info`
    para que después del commit el listener encole `brevo:push_contact`
    o `brevo:remove_from_brevo`. Inline (pre-commit) sería una race:
    el worker podría ver el contacto antes de que la mutación esté
    persistida."""
    contact = session.get(Contact, contact_id)
    if contact is None:
        return
    primary = session.scalar(
        select(ContactAssignment.user_id).where(
            ContactAssignment.contact_id == contact_id,
            ContactAssignment.is_primary.is_(True),
        )
    )
    old_owner = contact.owner_user_id
    contact.owner_user_id = primary  # str | None
    if old_owner != primary:
        # Import diferido — el servicio importa este módulo para los
        # tests, así que el ciclo se rompe con late binding.
        from app.services import brevo_push  # noqa: PLC0415

        brevo_push.record_owner_change(
            session, contact_id, old_owner, primary
        )


def add_assignment(
    session: Session,
    *,
    contact_id: str,
    user_id: str,
    is_primary: bool = False,
    assigned_by_user_id: str | None = None,
    source: str = "manual",
    rule_id: str | None = None,
    notes: str | None = None,
) -> ContactAssignment:
    """Add (or return existing) assignment. Idempotent on
    `(contact_id, user_id)` — a repeated add updates the existing row's
    flags rather than violating the UNIQUE. When `is_primary=True`,
    demotes any other primary first (one-primary invariant), then
    recomputes the cache."""
    existing = find(session, contact_id=contact_id, user_id=user_id)
    now = datetime.now(UTC)
    if existing is not None:
        if is_primary and not existing.is_primary:
            _demote_other_primaries(session, contact_id, except_id=existing.id)
            existing.is_primary = True
        if notes is not None:
            existing.notes = notes
        existing.source = source
        existing.rule_id = rule_id
        session.flush()
        recompute_primary_cache(session, contact_id)
        return existing

    if is_primary:
        _demote_other_primaries(session, contact_id)
    row = ContactAssignment(
        contact_id=contact_id,
        user_id=user_id,
        is_primary=is_primary,
        assigned_by_user_id=assigned_by_user_id,
        assigned_at=now,
        source=source,
        rule_id=rule_id,
        notes=notes,
    )
    row.created_at = now
    row.updated_at = now
    session.add(row)
    session.flush()
    recompute_primary_cache(session, contact_id)
    return row


def remove_assignment(session: Session, assignment: ContactAssignment) -> None:
    contact_id = assignment.contact_id
    session.delete(assignment)
    session.flush()
    recompute_primary_cache(session, contact_id)


def set_primary(
    session: Session, *, contact_id: str, assignment_id: str
) -> ContactAssignment | None:
    """Make `assignment_id` the primary: clear every sibling's flag,
    set this one, recompute cache. Returns the row, or None if it
    doesn't belong to the contact."""
    target = session.get(ContactAssignment, assignment_id)
    if target is None or target.contact_id != contact_id:
        return None
    _demote_other_primaries(session, contact_id, except_id=assignment_id)
    target.is_primary = True
    session.flush()
    recompute_primary_cache(session, contact_id)
    return target


def _demote_other_primaries(
    session: Session, contact_id: str, except_id: str | None = None
) -> None:
    stmt = (
        update(ContactAssignment)
        .where(
            ContactAssignment.contact_id == contact_id,
            ContactAssignment.is_primary.is_(True),
        )
        .values(is_primary=False)
    )
    if except_id:
        stmt = stmt.where(ContactAssignment.id != except_id)
    session.execute(stmt)
