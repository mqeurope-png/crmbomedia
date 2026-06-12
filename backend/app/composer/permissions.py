"""Role → composer-capability mapping.

The CRM has four roles; this module centralises the rules for
each `/api/composer/*` capability so the routers stay terse.

Layout:
- Every signed-in user **except viewer** can read the catalogue,
  list templates and operate on their own drafts.
- Manager + Admin can write the catalogue (products / brands /
  texts / blocks).
- Admin alone can touch global settings, write the activity
  log surface, manage other users' hidden items.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.models.crm import User, UserRole


def assert_composer_access(user: User) -> None:
    """Block viewers outright — they get a 403 the moment they
    poke the module."""
    if user.role == UserRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El rol viewer no puede usar Composer.",
        )


def assert_can_write_catalog(user: User) -> None:
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin o manager pueden editar el catálogo.",
        )


def assert_is_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin puede acceder a esta acción.",
        )


def can_edit_template(user: User, *, owner_user_id: str | None) -> bool:
    """Owner OR admin can edit/delete the template. Manager and
    user role can only edit templates they created themselves."""
    if user.role == UserRole.ADMIN:
        return True
    if owner_user_id and owner_user_id == user.id:
        return True
    return False
