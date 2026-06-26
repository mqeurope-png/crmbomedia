"""PR-Workflows-Pipelines-Per-User. Helpers compartidos para
permisos owner/admin sobre recursos que tienen el patrón
`owner_user_id NULL = global del equipo`.

Diseñado para `workflows` y `pipelines`, pero aplicable a cualquier
recurso con la misma columna. Mantén estos helpers como las dos
únicas reglas de autorización del feature — endpoints SOLO llaman
a estos.
"""
from __future__ import annotations

from typing import Protocol

from app.models.crm import User, UserRole


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
