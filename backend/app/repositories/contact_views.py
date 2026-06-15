"""Repository helpers for `contact_views` (saved list configurations).

Sprint Filtros & Listas (PR-B) hizo la tabla multi-entidad: la columna
`entity_type` discrimina contact / company / email_thread /
brevo_template / brevo_campaign sin renombrar la tabla. Las firmas
existentes mantienen `entity_type='contact'` por defecto para que
todo el código legacy del Sprint P.1 (contact-views) siga funcionando
sin tocar.

Tres concerns viven aquí:

1. CRUD + duplicate + default-toggling sobre la tabla, scoped por
   `entity_type`.
2. JSON encode/decode de `filters_json` / `columns_json` / `sort_json`
   para que la capa de rutas use dicts.
3. Merge de filtros de vista con overrides de URL en
   `GET /api/contacts?view_id=...`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import ContactView

DEFAULT_ENTITY_TYPE = "contact"


def _encode(payload: Any) -> str | None:
    """JSON-encode the value, returning NULL for empty dicts/lists so
    the column doesn't bloat with `{}` / `[]` for every default."""
    if payload is None:
        return None
    if isinstance(payload, dict | list) and not payload:
        return None
    return json.dumps(payload, default=str, ensure_ascii=False)


def _decode_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_views_for_user(
    session: Session,
    *,
    user_id: str,
    entity_type: str = DEFAULT_ENTITY_TYPE,
) -> list[ContactView]:
    """Return every view the user can SEE for this entity: own rows + every
    shared row from other owners. Sorted owner-first, then by name."""
    statement = (
        select(ContactView)
        .where(ContactView.entity_type == entity_type)
        .where(
            (ContactView.owner_user_id == user_id)
            | (ContactView.is_shared.is_(True))
        )
        .order_by(
            (ContactView.owner_user_id != user_id),  # own first
            ContactView.name,
        )
    )
    return list(session.scalars(statement))


def get_view(session: Session, view_id: str) -> ContactView | None:
    return session.get(ContactView, view_id)


def create_view(
    session: Session,
    *,
    owner_user_id: str,
    name: str,
    description: str | None,
    is_shared: bool,
    is_default: bool,
    filters: dict[str, Any],
    columns: dict[str, Any],
    sort: dict[str, Any],
    entity_type: str = DEFAULT_ENTITY_TYPE,
) -> ContactView:
    if is_default:
        _demote_other_defaults(
            session, owner_user_id=owner_user_id, entity_type=entity_type
        )
    view = ContactView(
        name=name,
        description=description,
        owner_user_id=owner_user_id,
        entity_type=entity_type,
        is_shared=is_shared,
        is_default=is_default,
        filters_json=_encode(filters),
        columns_json=_encode(columns),
        sort_json=_encode(sort),
    )
    session.add(view)
    session.flush()
    return view


def update_view(
    session: Session,
    *,
    view: ContactView,
    name: str | None = None,
    description: str | None = None,
    is_shared: bool | None = None,
    is_default: bool | None = None,
    filters: dict[str, Any] | None = None,
    columns: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
) -> ContactView:
    if name is not None:
        view.name = name
    if description is not None:
        view.description = description
    if is_shared is not None:
        view.is_shared = is_shared
    if is_default is True:
        _demote_other_defaults(
            session,
            owner_user_id=view.owner_user_id,
            entity_type=view.entity_type,
            except_id=view.id,
        )
        view.is_default = True
    elif is_default is False:
        view.is_default = False
    if filters is not None:
        view.filters_json = _encode(filters)
    if columns is not None:
        view.columns_json = _encode(columns)
    if sort is not None:
        view.sort_json = _encode(sort)
    session.flush()
    return view


def _demote_other_defaults(
    session: Session,
    *,
    owner_user_id: str,
    entity_type: str = DEFAULT_ENTITY_TYPE,
    except_id: str | None = None,
) -> None:
    """At most one default per `(owner, entity_type)`. Clear any sibling
    row of the same entity that still claims `default=True` before the
    caller sets the new one."""
    statement = select(ContactView).where(
        ContactView.owner_user_id == owner_user_id,
        ContactView.entity_type == entity_type,
        ContactView.is_default.is_(True),
    )
    if except_id:
        statement = statement.where(ContactView.id != except_id)
    for sibling in session.scalars(statement):
        sibling.is_default = False
    session.flush()


def duplicate_view(
    session: Session,
    *,
    source: ContactView,
    owner_user_id: str,
    name: str | None = None,
) -> ContactView:
    """Clone the source view into a new row owned by `owner_user_id`.
    The duplicate inherits `entity_type` (you can't duplicate a contact
    view into the company space) but never inherits `is_shared` or
    `is_default` so the operator opts in deliberately after editing."""
    new_view = ContactView(
        name=name or f"{source.name} (copia)",
        description=source.description,
        owner_user_id=owner_user_id,
        entity_type=source.entity_type,
        is_shared=False,
        is_default=False,
        filters_json=source.filters_json,
        columns_json=source.columns_json,
        sort_json=source.sort_json,
    )
    session.add(new_view)
    session.flush()
    return new_view


def delete_view(session: Session, *, view: ContactView) -> None:
    session.delete(view)


def view_to_dicts(view: ContactView) -> tuple[dict, dict, dict]:
    """Return `(filters, columns, sort)` as decoded dicts. Used by the
    route serialiser and by the `view_id` merge in `GET /contacts`."""
    return (
        _decode_dict(view.filters_json),
        _decode_dict(view.columns_json),
        _decode_dict(view.sort_json),
    )


def merge_filters_from_view(
    view_filters: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Combine a view's saved filters with URL-level overrides. A param
    with value `None` in `overrides` means "the caller didn't pass it
    on the URL" — we keep the view's value. A param with a real value
    (including the empty string) means "I want to override"."""
    out: dict[str, Any] = dict(view_filters)
    for key, value in overrides.items():
        if value is None:
            continue
        out[key] = value
    return out
