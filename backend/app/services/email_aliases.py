"""CRM-GMAIL — helpers sobre los alias de correo y la visibilidad por usuario.

Tres conceptos distintos que aquí NO se mezclan:

  - **El usuario está activo** (`users.is_active`): puede entrar en BoHub.
  - **La dirección es nuestra** (`user_email_aliases`, alias entrante, único
    global): su correo se captura y se enlaza al contacto. Dar de baja a una
    persona no da de baja sus direcciones: los clientes siguen escribiendo a
    ellas y el correo se sigue capturando (`active_alias_map` no mira
    `users.is_active`).
  - **El usuario puede enviar como ella** (`user_email_alias_prefs`,
    `is_allowed`): aparece en su desplegable de remitentes. Puede tener una
    o varias.

Fuente única para:
  - captura universal del sync: ¿este mail va dirigido a ALGÚN alias activo?
    (`active_alias_map` + `resolve_delivered_to`).
  - filtro «míos» de la Bandeja y visibilidad en la ficha
    (`personal_mailbox_filter`, `thread_visibility_filter`, `thread_is_visible`).
  - CRUD de admin (Parte A).

`user_email_aliases.alias_email` es único global (una dirección pertenece a
un solo usuario), así que el mapeo alias→dueño es 1:1. Distinto de
`user_email_alias_prefs` (Send-As, outbound, no único).
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app.models.crm import (
    Contact,
    EmailMessage,
    EmailThread,
    User,
    UserEmailAlias,
    UserEmailAliasPref,
    UserRole,
)


def active_alias_map(session: Session) -> dict[str, str]:
    """Todos los alias activos, `{alias_lower: alias_original}`. Lo consume la
    captura universal para decidir si un mail entrante «es nuestro». No mira
    si el dueño del alias sigue activo: la dirección sigue siendo nuestra."""
    rows = session.scalars(
        select(UserEmailAlias.alias_email).where(UserEmailAlias.active.is_(True))
    )
    return {alias.lower(): alias for alias in rows}


def user_active_aliases(session: Session, user_id: str) -> list[str]:
    """Alias entrantes activos que posee `user_id` (para el filtro de visibilidad)."""
    return list(
        session.scalars(
            select(UserEmailAlias.alias_email).where(
                UserEmailAlias.user_id == user_id,
                UserEmailAlias.active.is_(True),
            )
        )
    )


def user_sender_addresses(session: Session, user_id: str) -> list[str]:
    """Direcciones que el usuario tiene marcadas como remitente suyo
    (`user_email_alias_prefs.is_allowed`), en minúsculas y sin repetir."""
    rows = session.scalars(
        select(UserEmailAliasPref.alias_email).where(
            UserEmailAliasPref.user_id == user_id,
            UserEmailAliasPref.is_allowed.is_(True),
        )
    )
    return sorted({alias.strip().lower() for alias in rows if alias and alias.strip()})


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
# «Míos» — filtro de visibilidad por usuario
#
# «Míos» es la unión de cuatro cosas (definido el 10/10/2026, no queda al
# criterio de nadie):
#   1. Los correos que ha escrito el usuario (y los hilos que inició).
#   2. Los hilos en los que ha participado, aunque los abriera otro (#538).
#   3. Los correos enviados desde o dirigidos a una dirección que el usuario
#      tiene marcada como remitente suyo (`user_email_alias_prefs.is_allowed`),
#      más la entrada a sus alias registrados (`user_email_aliases`).
#   4. Cualquier correo de un contacto cuyo propietario sea el usuario, sea
#      cual sea el remitente y el destinatario: el comercial que lleva un lead
#      ve toda su correspondencia aunque la contestara un compañero desde otra
#      dirección. Es lo que da sentido al reparto de leads de los formularios.
# Admin ve TODO en la ficha y en «todos»; su bandeja personal también es la
# SUYA (para ver todo usa scope=team).


def _contactos_propios(user_id: str) -> Select[tuple[str]]:
    return select(Contact.id).where(Contact.owner_user_id == user_id)


def my_messages_filter(session: Session, user: User) -> ColumnElement[bool]:
    """Predicado a nivel de MENSAJE de las reglas 1, 3 y 4 (lo que el usuario
    escribió, lo que entró o salió por sus direcciones, lo de sus contactos)."""
    aliases = user_active_aliases(session, user.id)
    remitentes = user_sender_addresses(session, user.id)
    conditions: list[ColumnElement[bool]] = [
        EmailMessage.created_by_user_id == user.id,
        EmailMessage.contact_id.in_(_contactos_propios(user.id)),
    ]
    if aliases:
        conditions.append(EmailMessage.delivered_to.in_(aliases))
    if remitentes:
        conditions.append(func.lower(EmailMessage.from_email).in_(remitentes))
        conditions.append(func.lower(EmailMessage.delivered_to).in_(remitentes))
    return or_(*conditions)


def personal_mailbox_filter(
    session: Session, user: User
) -> ColumnElement[bool]:
    """Predicado «mi bandeja» a nivel de HILO: los hilos que el usuario
    inició, los de contactos de los que es propietario y los que tienen algún
    mensaje suyo (`my_messages_filter`). SIEMPRE se aplica — la bandeja
    personal de un admin también es la SUYA (para ver todo usa scope=team)."""
    return or_(
        EmailThread.initiated_by_user_id == user.id,
        EmailThread.contact_id.in_(_contactos_propios(user.id)),
        EmailThread.id.in_(
            select(EmailMessage.thread_id).where(my_messages_filter(session, user))
        ),
    )


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
    if thread.contact_id and session.scalar(
        select(Contact.id).where(
            Contact.id == thread.contact_id, Contact.owner_user_id == user.id
        )
    ):
        return True
    hit = session.scalar(
        select(EmailMessage.id)
        .where(EmailMessage.thread_id == thread.id, my_messages_filter(session, user))
        .limit(1)
    )
    return hit is not None
