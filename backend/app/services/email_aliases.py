"""CRM-GMAIL — helpers sobre `user_email_aliases` (alias entrante por usuario).

Fuente única para las tres necesidades del sprint Gmail:
  - captura universal del sync: ¿este mail va dirigido a ALGÚN alias activo?
    (`active_alias_map` + `resolve_delivered_to`).
  - filtro de visibilidad por comercial (Parte E): ¿qué alias posee este user?
    (`user_active_aliases`).
  - CRUD de admin (Parte A).

`user_email_aliases.alias_email` es único global (una dirección pertenece a
un solo usuario), así que el mapeo alias→dueño es 1:1. Distinto de
`user_email_alias_prefs` (Send-As, outbound, no único).
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.models.crm import (
    EmailMessage,
    EmailThread,
    User,
    UserEmailAlias,
    UserRole,
)


def active_alias_map(session: Session) -> dict[str, str]:
    """Todos los alias activos, `{alias_lower: alias_original}`. Lo consume la
    captura universal para decidir si un mail entrante «es nuestro»."""
    rows = session.scalars(
        select(UserEmailAlias.alias_email).where(UserEmailAlias.active.is_(True))
    )
    return {alias.lower(): alias for alias in rows}


def user_active_aliases(session: Session, user_id: str) -> list[str]:
    """Alias activos que posee `user_id` (para el filtro de visibilidad)."""
    return list(
        session.scalars(
            select(UserEmailAlias.alias_email).where(
                UserEmailAlias.user_id == user_id,
                UserEmailAlias.active.is_(True),
            )
        )
    )


def resolve_delivered_to(
    recipients: Iterable[str | None],
    alias_map: dict[str, str],
) -> str | None:
    """Primer destinatario (To/Cc/Bcc/Delivered-To) que casa un alias activo.
    Case-insensitive; devuelve el alias en su forma canónica o None."""
    for addr in recipients:
        if addr and addr.lower() in alias_map:
            return alias_map[addr.lower()]
    return None


# ---------------------------------------------------------------------------
# Parte E — filtro de visibilidad por alias
#
# Regla (spec CRM-GMAIL): admin ve TODO; el resto ve un thread si (a) lo
# inició él (para no ocultar su propio correo enviado, que no tiene
# `delivered_to`) o (b) el thread tiene algún mensaje entregado a uno de sus
# alias activos. Aplica a bandeja, ficha de contacto y timeline.


def personal_mailbox_filter(
    session: Session, user: User
) -> ColumnElement[bool]:
    """Predicado «mi bandeja»: threads que el usuario inició (para no ocultar
    su propio correo enviado, sin `delivered_to`) o que tienen un mensaje
    entregado a uno de sus alias activos. SIEMPRE se aplica — la bandeja
    personal de un admin también es la SUYA (para ver todo usa scope=team)."""
    aliases = user_active_aliases(session, user.id)
    conditions: list[ColumnElement[bool]] = [
        EmailThread.initiated_by_user_id == user.id
    ]
    if aliases:
        conditions.append(
            EmailThread.id.in_(
                select(EmailMessage.thread_id).where(
                    EmailMessage.delivered_to.in_(aliases)
                )
            )
        )
    return or_(*conditions)


def thread_visibility_filter(
    session: Session, user: User
) -> ColumnElement[bool] | None:
    """Predicado de visibilidad para la ficha de contacto / timeline / feeds
    agregados: admin ve TODO (devuelve None); el resto, la misma regla que
    `personal_mailbox_filter`."""
    if user.role == UserRole.ADMIN:
        return None
    return personal_mailbox_filter(session, user)


def thread_is_visible(
    session: Session, user: User, thread: EmailThread
) -> bool:
    """Chequeo a nivel de fila (para thread_detail). Misma regla."""
    if user.role == UserRole.ADMIN:
        return True
    if thread.initiated_by_user_id == user.id:
        return True
    aliases = user_active_aliases(session, user.id)
    if not aliases:
        return False
    hit = session.scalar(
        select(EmailMessage.id)
        .where(
            EmailMessage.thread_id == thread.id,
            EmailMessage.delivered_to.in_(aliases),
        )
        .limit(1)
    )
    return hit is not None
