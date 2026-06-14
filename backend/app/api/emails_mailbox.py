"""Mailbox mutations — folders, labels, thread state/star/snooze.

Sprint Email v2.4a. Mounted at `/api/emails` alongside the legacy
`emails.py` router. The split is purely for review clarity: every
helper still lives in the same module the route uses it from.

Authorization model: every folder + label is owned by the user
who created it; thread mutations require the caller to be the
thread's `initiated_by_user_id` (admins + managers can act on
anything). Bulk endpoints silently skip threads the caller can't
touch — they don't 403, so a bulk action against a mixed
selection still does as much as the caller is allowed to do.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import (
    EmailFolder,
    EmailLabel,
    EmailMessage,
    EmailThread,
    EmailThreadLabel,
    EmailThreadState,
    User,
    UserRole,
)
from app.schemas.emails import (
    EmailFolderRead,
    EmailFolderWrite,
    EmailLabelRead,
    EmailLabelWrite,
    EmailThreadBulkAction,
    EmailThreadBulkLabel,
    EmailThreadBulkMove,
    EmailThreadBulkSnooze,
)

router = APIRouter(prefix="/api/emails", tags=["emails-mailbox"])


# -- helpers --------------------------------------------------------


def _is_privileged(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.MANAGER)


def _get_thread(session: Session, thread_id: str, user: User) -> EmailThread:
    """Fetch a thread the caller is allowed to mutate, else 404/403.
    404-not-found vs. 403-forbidden distinction matters here: a
    comercial probing other operators' inboxes by id should NOT
    learn whether the thread exists."""
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise not_found("EmailThread")
    if not _is_privileged(user) and thread.initiated_by_user_id != user.id:
        raise not_found("EmailThread")
    return thread


def _threads_for_bulk(
    session: Session, ids: list[str], user: User
) -> list[EmailThread]:
    """Resolve the subset of the requested ids the caller can act
    on. Unknown ids and other-operator threads are dropped without
    erroring so a partial selection still applies cleanly."""
    if not ids:
        return []
    stmt = select(EmailThread).where(EmailThread.id.in_(ids))
    if not _is_privileged(user):
        stmt = stmt.where(EmailThread.initiated_by_user_id == user.id)
    return list(session.scalars(stmt))


def _get_folder(session: Session, folder_id: str, user: User) -> EmailFolder:
    folder = session.get(EmailFolder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise not_found("EmailFolder")
    return folder


def _get_label(session: Session, label_id: str, user: User) -> EmailLabel:
    label = session.get(EmailLabel, label_id)
    if label is None or label.user_id != user.id:
        raise not_found("EmailLabel")
    return label


def _set_state(
    threads: list[EmailThread], state: EmailThreadState
) -> None:
    """Apply a state transition. `is_archived` is kept in sync so
    legacy queries still see the old flag flip until we drop the
    column in a follow-up migration."""
    for thread in threads:
        thread.state = state
        thread.is_archived = state == EmailThreadState.ARCHIVED


# -- folders --------------------------------------------------------


@router.get("/folders", response_model=list[EmailFolderRead])
def list_folders(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailFolderRead]:
    """All folders owned by the current user, flat list. Hierarchy
    is encoded via `parent_id` so the frontend can fold the tree
    however it wants; the API stays simple."""
    folders = list(
        session.scalars(
            select(EmailFolder)
            .where(EmailFolder.user_id == current_user.id)
            .order_by(EmailFolder.sort_order, EmailFolder.name)
        )
    )
    return [EmailFolderRead.model_validate(f) for f in folders]


@router.post(
    "/folders", response_model=EmailFolderRead, status_code=201
)
def create_folder(
    payload: EmailFolderWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailFolderRead:
    if payload.parent_id:
        # Confirms parent exists and belongs to the same user — a
        # comercial can't graft their tree onto someone else's.
        _get_folder(session, payload.parent_id, current_user)
    folder = EmailFolder(
        user_id=current_user.id,
        name=payload.name,
        parent_id=payload.parent_id,
        color=payload.color,
        icon=payload.icon,
        sort_order=payload.sort_order,
    )
    session.add(folder)
    record_event(
        session,
        action=Action.EMAIL_FOLDER_CREATED,
        target_type="email_folder",
        target_id=folder.id,
        actor=current_user,
        metadata={"name": payload.name},
    )
    session.commit()
    session.refresh(folder)
    return EmailFolderRead.model_validate(folder)


@router.put("/folders/{folder_id}", response_model=EmailFolderRead)
def update_folder(
    folder_id: str,
    payload: EmailFolderWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailFolderRead:
    folder = _get_folder(session, folder_id, current_user)
    if folder.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede modificar una carpeta del sistema.",
        )
    if payload.parent_id == folder.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Una carpeta no puede ser su propia padre.",
        )
    if payload.parent_id:
        _get_folder(session, payload.parent_id, current_user)
    folder.name = payload.name
    folder.parent_id = payload.parent_id
    folder.color = payload.color
    folder.icon = payload.icon
    folder.sort_order = payload.sort_order
    record_event(
        session,
        action=Action.EMAIL_FOLDER_UPDATED,
        target_type="email_folder",
        target_id=folder.id,
        actor=current_user,
    )
    session.commit()
    session.refresh(folder)
    return EmailFolderRead.model_validate(folder)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(
    folder_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    folder = _get_folder(session, folder_id, current_user)
    if folder.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede borrar una carpeta del sistema.",
        )
    record_event(
        session,
        action=Action.EMAIL_FOLDER_DELETED,
        target_type="email_folder",
        target_id=folder.id,
        actor=current_user,
        metadata={"name": folder.name},
    )
    session.delete(folder)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- labels ---------------------------------------------------------


@router.get("/labels", response_model=list[EmailLabelRead])
def list_labels(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[EmailLabelRead]:
    labels = list(
        session.scalars(
            select(EmailLabel)
            .where(EmailLabel.user_id == current_user.id)
            .order_by(EmailLabel.sort_order, EmailLabel.name)
        )
    )
    return [EmailLabelRead.model_validate(label) for label in labels]


@router.post("/labels", response_model=EmailLabelRead, status_code=201)
def create_label(
    payload: EmailLabelWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailLabelRead:
    existing = session.scalar(
        select(EmailLabel).where(
            EmailLabel.user_id == current_user.id,
            EmailLabel.name == payload.name,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tienes una etiqueta con ese nombre.",
        )
    label = EmailLabel(
        user_id=current_user.id,
        name=payload.name,
        color=payload.color,
        sort_order=payload.sort_order,
    )
    session.add(label)
    record_event(
        session,
        action=Action.EMAIL_LABEL_CREATED,
        target_type="email_label",
        target_id=label.id,
        actor=current_user,
        metadata={"name": payload.name},
    )
    session.commit()
    session.refresh(label)
    return EmailLabelRead.model_validate(label)


@router.put("/labels/{label_id}", response_model=EmailLabelRead)
def update_label(
    label_id: str,
    payload: EmailLabelWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailLabelRead:
    label = _get_label(session, label_id, current_user)
    if payload.name != label.name:
        # Re-check the unique-by-name constraint on rename.
        clash = session.scalar(
            select(EmailLabel).where(
                EmailLabel.user_id == current_user.id,
                EmailLabel.name == payload.name,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya tienes una etiqueta con ese nombre.",
            )
    label.name = payload.name
    label.color = payload.color
    label.sort_order = payload.sort_order
    record_event(
        session,
        action=Action.EMAIL_LABEL_UPDATED,
        target_type="email_label",
        target_id=label.id,
        actor=current_user,
    )
    session.commit()
    session.refresh(label)
    return EmailLabelRead.model_validate(label)


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(
    label_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    label = _get_label(session, label_id, current_user)
    record_event(
        session,
        action=Action.EMAIL_LABEL_DELETED,
        target_type="email_label",
        target_id=label.id,
        actor=current_user,
        metadata={"name": label.name},
    )
    session.delete(label)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- single-thread mutations ----------------------------------------


def _set_starred(
    session: Session,
    thread: EmailThread,
    user: User,
    *,
    value: bool,
) -> dict[str, bool]:
    thread.is_starred = value
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=user,
        metadata={"field": "is_starred", "value": value},
    )
    session.commit()
    return {"is_starred": value}


@router.post("/threads/{thread_id}/star")
def star_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, bool]:
    thread = _get_thread(session, thread_id, current_user)
    return _set_starred(session, thread, current_user, value=True)


@router.post("/threads/{thread_id}/unstar")
def unstar_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, bool]:
    thread = _get_thread(session, thread_id, current_user)
    return _set_starred(session, thread, current_user, value=False)


def _move_thread(
    session: Session,
    thread: EmailThread,
    folder_id: str | None,
    user: User,
) -> dict[str, str | None]:
    if folder_id is not None:
        _get_folder(session, folder_id, user)
    thread.folder_id = folder_id
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=user,
        metadata={"field": "folder_id", "value": folder_id},
    )
    session.commit()
    return {"folder_id": folder_id}


@router.post("/threads/{thread_id}/move")
def move_thread(
    thread_id: str,
    folder_id: str | None = Body(default=None, embed=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str | None]:
    thread = _get_thread(session, thread_id, current_user)
    return _move_thread(session, thread, folder_id, current_user)


def _transition_one(
    session: Session,
    thread: EmailThread,
    state: EmailThreadState,
    user: User,
) -> dict[str, str]:
    _set_state([thread], state)
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=user,
        metadata={"field": "state", "value": state.value},
    )
    session.commit()
    return {"state": state.value}


@router.post("/threads/{thread_id}/archive")
def archive_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = _get_thread(session, thread_id, current_user)
    return _transition_one(
        session, thread, EmailThreadState.ARCHIVED, current_user
    )


@router.post("/threads/{thread_id}/trash")
def trash_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = _get_thread(session, thread_id, current_user)
    return _transition_one(
        session, thread, EmailThreadState.TRASHED, current_user
    )


@router.post("/threads/{thread_id}/spam")
def spam_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = _get_thread(session, thread_id, current_user)
    return _transition_one(
        session, thread, EmailThreadState.SPAM, current_user
    )


@router.post("/threads/{thread_id}/restore")
def restore_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    """Move a thread back to inbox from archived / trashed / spam."""
    thread = _get_thread(session, thread_id, current_user)
    return _transition_one(
        session, thread, EmailThreadState.INBOX, current_user
    )


@router.post("/threads/{thread_id}/snooze")
def snooze_thread(
    thread_id: str,
    snooze_until: datetime = Body(..., embed=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    thread = _get_thread(session, thread_id, current_user)
    now = datetime.now(UTC)
    target = snooze_until if snooze_until.tzinfo else snooze_until.replace(
        tzinfo=UTC
    )
    if target <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La fecha de snooze debe ser en el futuro.",
        )
    thread.snooze_until = target
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        metadata={"field": "snooze_until", "value": target.isoformat()},
    )
    session.commit()
    return {"snooze_until": target.isoformat()}


@router.post("/threads/{thread_id}/unsnooze")
def unsnooze_thread(
    thread_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, None]:
    thread = _get_thread(session, thread_id, current_user)
    thread.snooze_until = None
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        metadata={"field": "snooze_until", "value": None},
    )
    session.commit()
    return {"snooze_until": None}


@router.post(
    "/threads/{thread_id}/labels/{label_id}", response_model=EmailLabelRead
)
def add_thread_label(
    thread_id: str,
    label_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> EmailLabelRead:
    thread = _get_thread(session, thread_id, current_user)
    label = _get_label(session, label_id, current_user)
    existing = session.get(EmailThreadLabel, (thread.id, label.id))
    if existing is None:
        session.add(
            EmailThreadLabel(
                thread_id=thread.id,
                label_id=label.id,
                applied_at=datetime.now(UTC),
            )
        )
        record_event(
            session,
            action=Action.EMAIL_THREADS_UPDATED,
            target_type="email_thread",
            target_id=thread.id,
            actor=current_user,
            metadata={"field": "label_added", "value": label.id},
        )
        session.commit()
    return EmailLabelRead.model_validate(label)


@router.delete(
    "/threads/{thread_id}/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_thread_label(
    thread_id: str,
    label_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    thread = _get_thread(session, thread_id, current_user)
    label = _get_label(session, label_id, current_user)
    session.execute(
        delete(EmailThreadLabel).where(
            EmailThreadLabel.thread_id == thread.id,
            EmailThreadLabel.label_id == label.id,
        )
    )
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=thread.id,
        actor=current_user,
        metadata={"field": "label_removed", "value": label.id},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- bulk operations ------------------------------------------------


def _bulk_state(
    session: Session,
    payload: EmailThreadBulkAction,
    user: User,
    state: EmailThreadState,
) -> dict[str, int]:
    threads = _threads_for_bulk(session, payload.thread_ids, user)
    _set_state(threads, state)
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=user,
        metadata={
            "bulk": "state",
            "value": state.value,
            "affected": len(threads),
        },
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/archive")
def bulk_archive(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_state(
        session, payload, current_user, EmailThreadState.ARCHIVED
    )


@router.post("/threads-bulk/trash")
def bulk_trash(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_state(
        session, payload, current_user, EmailThreadState.TRASHED
    )


@router.post("/threads-bulk/spam")
def bulk_spam(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_state(
        session, payload, current_user, EmailThreadState.SPAM
    )


@router.post("/threads-bulk/restore")
def bulk_restore(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_state(
        session, payload, current_user, EmailThreadState.INBOX
    )


def _bulk_starred(
    session: Session,
    payload: EmailThreadBulkAction,
    user: User,
    *,
    value: bool,
) -> dict[str, int]:
    threads = _threads_for_bulk(session, payload.thread_ids, user)
    for thread in threads:
        thread.is_starred = value
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=user,
        metadata={
            "bulk": "is_starred",
            "value": value,
            "affected": len(threads),
        },
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/star")
def bulk_star(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_starred(session, payload, current_user, value=True)


@router.post("/threads-bulk/unstar")
def bulk_unstar(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    return _bulk_starred(session, payload, current_user, value=False)


@router.post("/threads-bulk/move")
def bulk_move(
    payload: EmailThreadBulkMove,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    if payload.folder_id is not None:
        _get_folder(session, payload.folder_id, current_user)
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    for thread in threads:
        thread.folder_id = payload.folder_id
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={
            "bulk": "folder_id",
            "value": payload.folder_id,
            "affected": len(threads),
        },
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/snooze")
def bulk_snooze(
    payload: EmailThreadBulkSnooze,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    target = (
        payload.snooze_until
        if payload.snooze_until.tzinfo
        else payload.snooze_until.replace(tzinfo=UTC)
    )
    if target <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La fecha de snooze debe ser en el futuro.",
        )
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    for thread in threads:
        thread.snooze_until = target
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={
            "bulk": "snooze_until",
            "value": target.isoformat(),
            "affected": len(threads),
        },
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/mark-read")
def bulk_mark_read(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    now = datetime.now(UTC)
    for thread in threads:
        thread.has_unread_replies = False
    if threads:
        session.execute(
            EmailMessage.__table__.update()  # type: ignore[attr-defined]
            .where(
                EmailMessage.thread_id.in_([t.id for t in threads]),
                EmailMessage.direction == "inbound",
                EmailMessage.read_at.is_(None),
            )
            .values(read_at=now)
        )
    record_event(
        session,
        action=Action.EMAIL_THREAD_MARKED_READ,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={"bulk": "mark-read", "affected": len(threads)},
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/mark-unread")
def bulk_mark_unread(
    payload: EmailThreadBulkAction,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    for thread in threads:
        thread.has_unread_replies = True
    record_event(
        session,
        action=Action.EMAIL_THREAD_MARKED_READ,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={"bulk": "mark-unread", "affected": len(threads)},
    )
    session.commit()
    return {"affected": len(threads)}


@router.post("/threads-bulk/labels/add")
def bulk_label_add(
    payload: EmailThreadBulkLabel,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    label = _get_label(session, payload.label_id, current_user)
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    # Dedupe against existing rows so the second call is a no-op.
    existing = set(
        session.scalars(
            select(EmailThreadLabel.thread_id).where(
                EmailThreadLabel.label_id == label.id,
                EmailThreadLabel.thread_id.in_([t.id for t in threads]),
            )
        )
    )
    now = datetime.now(UTC)
    added = 0
    for thread in threads:
        if thread.id in existing:
            continue
        session.add(
            EmailThreadLabel(
                thread_id=thread.id, label_id=label.id, applied_at=now
            )
        )
        added += 1
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={
            "bulk": "label_added",
            "label_id": label.id,
            "affected": added,
        },
    )
    session.commit()
    return {"affected": added}


@router.post("/threads-bulk/labels/remove")
def bulk_label_remove(
    payload: EmailThreadBulkLabel,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    label = _get_label(session, payload.label_id, current_user)
    threads = _threads_for_bulk(session, payload.thread_ids, current_user)
    if not threads:
        return {"affected": 0}
    result = session.execute(
        delete(EmailThreadLabel).where(
            EmailThreadLabel.label_id == label.id,
            EmailThreadLabel.thread_id.in_([t.id for t in threads]),
        )
    )
    removed = result.rowcount or 0
    record_event(
        session,
        action=Action.EMAIL_THREADS_UPDATED,
        target_type="email_thread",
        target_id=None,
        actor=current_user,
        metadata={
            "bulk": "label_removed",
            "label_id": label.id,
            "affected": removed,
        },
    )
    session.commit()
    return {"affected": removed}
