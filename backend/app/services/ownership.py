"""PR-Workflows-Pipelines-Per-User. Helpers compartidos para
permisos owner/admin sobre recursos que tienen el patrón
`owner_user_id NULL = global del equipo`.

Diseñado para `workflows` y `pipelines`, pero aplicable a cualquier
recurso con la misma columna. Mantén estos helpers como las dos
únicas reglas de autorización del feature — endpoints SOLO llaman
a estos.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select

from app.models.crm import Contact, User, UserRole

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class _OwnedResource(Protocol):
    """Cualquier modelo con `owner_user_id: str | None`."""

    owner_user_id: str | None


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def can_user_edit_resource(user: User, resource: _OwnedResource) -> bool:
    """Edit-rights: admin OR owner. Si el recurso es global
    (`owner_user_id IS NULL`), solo admin puede editar."""
    if is_admin(user):
        return True
    if resource.owner_user_id is None:
        return False
    return resource.owner_user_id == user.id


def can_user_see_resource(user: User, resource: _OwnedResource) -> bool:
    """View-rights: admin todo + cualquier user ve los suyos +
    cualquier user ve los globales del equipo."""
    if is_admin(user):
        return True
    if resource.owner_user_id is None:
        return True
    return resource.owner_user_id == user.id


def can_user_toggle_global(user: User) -> bool:
    """Solo admin puede cambiar el flag `is_global` (en cualquier
    dirección)."""
    return is_admin(user)


def resource_is_global(resource: _OwnedResource) -> bool:
    return resource.owner_user_id is None


def resource_is_mine(resource: _OwnedResource, user: User) -> bool:
    return (
        resource.owner_user_id is not None
        and resource.owner_user_id == user.id
    )


def user_processes_all_contacts(user: User) -> bool:
    """PR-Bulk-Comerciales. admin y manager operan sobre CUALQUIER
    contacto en las acciones masivas; el resto (comercial = `user`) solo
    sobre los suyos."""
    return user.role in (UserRole.ADMIN, UserRole.MANAGER)


def partition_contacts_by_ownership(
    session: Session, contact_ids: list[str], user: User
) -> tuple[list[str], list[str]]:
    """PR-Bulk-Comerciales. Particiona `contact_ids` en
    `(owned_ids, foreign_ids)` según la propiedad del contacto.

    - admin/manager: (todos los ids recibidos, []) — procesan todo, sin
      filtrar. No se toca la BD (mantiene el orden y no descarta ids
      inexistentes, que las acciones ya ignoran aguas abajo).
    - comercial: `owned_ids` son los contactos cuyo `owner_user_id` es el
      user; `foreign_ids` es el resto (ajenos o inexistentes). Se preserva
      el orden de entrada.

    El helper es la ÚNICA regla de propiedad de las acciones masivas —
    los endpoints solo llaman aquí.
    """
    if user_processes_all_contacts(user):
        return list(contact_ids), []
    owned = set(
        session.scalars(
            select(Contact.id).where(
                Contact.id.in_(contact_ids),
                Contact.owner_user_id == user.id,
            )
        )
    )
    owned_ids = [cid for cid in contact_ids if cid in owned]
    foreign_ids = [cid for cid in contact_ids if cid not in owned]
    return owned_ids, foreign_ids
