"""Generic saved-view CRUD for any registered entity.

Sprint Filtros & Listas (PR-B). Mounts under
`/api/entity-views/{entity}` and shares the `contact_views` table with
the legacy `/api/contact-views` endpoint via the new `entity_type`
discriminator. Default uniqueness is per `(owner_user_id, entity_type)`
so a user can have one default contact view AND one default company
view simultaneously.

This router covers the four "list view" entities (company, email_thread,
brevo_template, brevo_campaign). Contact also routes through here for
completeness, but the legacy `/api/contact-views` keeps working for the
current `/contacts` UI until PR-E migrates it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_viewer
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import ContactView, User
from app.repositories import contact_views as views_repo
from app.services.entities import get_entity

router = APIRouter(prefix="/api/entity-views", tags=["entity-views"])


# Pydantic schemas — kept here so the entity-views surface stays
# self-contained. They're intentionally less constrained than the legacy
# `ContactViewFilters`: the saved filter blob is a free-form dict (it
# carries the engine's IR tree under `rules_json` plus optional flat
# helpers like `q`), and the column/sort blobs are likewise generic.


class EntityViewWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_shared: bool = False
    is_default: bool = False
    filters: dict[str, Any] = Field(default_factory=dict)
    columns: dict[str, Any] = Field(default_factory=dict)
    sort: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class EntityViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_shared: bool | None = None
    is_default: bool | None = None
    filters: dict[str, Any] | None = None
    columns: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class EntityViewDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class EntityViewRead(BaseModel):
    id: str
    entity_type: str
    name: str
    description: str | None = None
    owner_user_id: str
    is_owner: bool = False
    is_shared: bool
    is_default: bool
    filters: dict[str, Any]
    columns: dict[str, Any]
    sort: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


def _require_entity(entity: str) -> None:
    if get_entity(entity) is None:
        raise not_found("Entity")


def _view_to_read(view: ContactView, *, current_user: User) -> EntityViewRead:
    filters, columns, sort = views_repo.view_to_dicts(view)
    return EntityViewRead(
        id=view.id,
        entity_type=view.entity_type,
        name=view.name,
        description=view.description,
        owner_user_id=view.owner_user_id,
        is_owner=view.owner_user_id == current_user.id,
        is_shared=view.is_shared,
        is_default=view.is_default,
        filters=filters,
        columns=columns,
        sort=sort,
    )


def _view_in_entity(view: ContactView, entity: str) -> bool:
    return view.entity_type == entity


@router.get("/{entity}")
def list_entity_views(
    entity: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> list[EntityViewRead]:
    _require_entity(entity)
    rows = views_repo.list_views_for_user(
        session, user_id=current_user.id, entity_type=entity
    )
    return [_view_to_read(row, current_user=current_user) for row in rows]


@router.get("/{entity}/{view_id}")
def read_entity_view(
    entity: str,
    view_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntityViewRead:
    _require_entity(entity)
    view = views_repo.get_view(session, view_id)
    if not view or not _view_in_entity(view, entity):
        raise not_found("Entity view")
    if view.owner_user_id != current_user.id and not view.is_shared:
        raise not_found("Entity view")
    return _view_to_read(view, current_user=current_user)


@router.post("/{entity}", status_code=status.HTTP_201_CREATED)
def create_entity_view(
    entity: str,
    payload: EntityViewWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntityViewRead:
    _require_entity(entity)
    view = views_repo.create_view(
        session,
        owner_user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        is_shared=payload.is_shared,
        is_default=payload.is_default,
        filters=payload.filters,
        columns=payload.columns,
        sort=payload.sort,
        entity_type=entity,
    )
    record_event(
        session,
        action=Action.ENTITY_VIEW_CREATED,
        target_type="entity_view",
        target_id=view.id,
        actor=current_user,
        metadata={
            "entity_type": entity,
            "name": view.name,
            "is_shared": view.is_shared,
        },
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)


@router.patch("/{entity}/{view_id}")
def update_entity_view(
    entity: str,
    view_id: str,
    payload: EntityViewUpdate,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntityViewRead:
    _require_entity(entity)
    view = views_repo.get_view(session, view_id)
    if not view or not _view_in_entity(view, entity):
        raise not_found("Entity view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not owner"
        )
    changes = payload.model_dump(exclude_unset=True)
    views_repo.update_view(
        session,
        view=view,
        name=changes.get("name"),
        description=changes.get("description"),
        is_shared=changes.get("is_shared"),
        is_default=changes.get("is_default"),
        filters=payload.filters if payload.filters is not None else None,
        columns=payload.columns if payload.columns is not None else None,
        sort=payload.sort if payload.sort is not None else None,
    )
    record_event(
        session,
        action=Action.ENTITY_VIEW_UPDATED,
        target_type="entity_view",
        target_id=view.id,
        actor=current_user,
        metadata={
            "entity_type": entity,
            "name": view.name,
            "changed_fields": sorted(changes.keys()),
        },
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)


@router.delete(
    "/{entity}/{view_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_entity_view(
    entity: str,
    view_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> Response:
    _require_entity(entity)
    view = views_repo.get_view(session, view_id)
    if not view or not _view_in_entity(view, entity):
        raise not_found("Entity view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not owner"
        )
    record_event(
        session,
        action=Action.ENTITY_VIEW_DELETED,
        target_type="entity_view",
        target_id=view.id,
        actor=current_user,
        metadata={"entity_type": entity, "name": view.name},
        request=request,
    )
    views_repo.delete_view(session, view=view)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{entity}/{view_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_entity_view(
    entity: str,
    view_id: str,
    payload: EntityViewDuplicateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntityViewRead:
    _require_entity(entity)
    source = views_repo.get_view(session, view_id)
    if not source or not _view_in_entity(source, entity):
        raise not_found("Entity view")
    if source.owner_user_id != current_user.id and not source.is_shared:
        raise not_found("Entity view")
    duplicate = views_repo.duplicate_view(
        session,
        source=source,
        owner_user_id=current_user.id,
        name=payload.name,
    )
    record_event(
        session,
        action=Action.ENTITY_VIEW_DUPLICATED,
        target_type="entity_view",
        target_id=duplicate.id,
        actor=current_user,
        metadata={
            "entity_type": entity,
            "source_view_id": source.id,
            "name": duplicate.name,
        },
        request=request,
    )
    session.commit()
    session.refresh(duplicate)
    return _view_to_read(duplicate, current_user=current_user)


@router.post("/{entity}/{view_id}/set-default")
def set_default_entity_view(
    entity: str,
    view_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> EntityViewRead:
    _require_entity(entity)
    view = views_repo.get_view(session, view_id)
    if not view or not _view_in_entity(view, entity):
        raise not_found("Entity view")
    if view.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not owner"
        )
    views_repo.update_view(session, view=view, is_default=True)
    record_event(
        session,
        action=Action.ENTITY_VIEW_DEFAULT_SET,
        target_type="entity_view",
        target_id=view.id,
        actor=current_user,
        metadata={"entity_type": entity, "name": view.name},
        request=request,
    )
    session.commit()
    session.refresh(view)
    return _view_to_read(view, current_user=current_user)
