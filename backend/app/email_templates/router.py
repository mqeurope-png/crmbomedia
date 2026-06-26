"""REST surface for v2.2 — templates CRUD, folders CRUD, picker."""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.config import get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.integrations.brevo import templates as _brevo_templates_service
from app.integrations.errors import IntegrationError
from app.models.brevo import BrevoTemplateCache
from app.models.crm import User, UserRole

from .models import (
    EmailTemplate,
    EmailTemplateAttachment,
    EmailTemplateFolder,
    EmailTemplateFolderShare,
)
from .schemas import (
    BrevoPickerItem,
    BrevoTemplateHtmlResponse,
    ComposerSourcePickerItem,
    ComposerSourceResponse,
    DefaultTemplateFolderRequest,
    DefaultTemplateFolderResponse,
    FolderRead,
    FolderShareWrite,
    FolderTreeNode,
    FolderWrite,
    ImageUploadResponse,
    PickerResponse,
    TemplateListItem,
    TemplateRead,
    TemplateWrite,
)
from .services import (
    MAX_FOLDER_DEPTH,
    descendants,
    extract_text_from_html,
    fetch_composer_templates,
    folder_depth,
)

# Tiptap accepts whatever image MIME types the browser produces. We
# allowlist the common ones so an operator can't sneak SVG (XSS via
# embedded <script>) or large RAW files through.
ALLOWED_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

router = APIRouter(prefix="/api", tags=["email-templates"])


def _now() -> datetime:
    return datetime.now(UTC)


def _folder_share_user_ids(
    session: Session, folder_id: str
) -> set[str]:
    """Set de user_id con acceso explícito a una carpeta `shared`."""
    rows = session.scalars(
        select(EmailTemplateFolderShare.user_id).where(
            EmailTemplateFolderShare.folder_id == folder_id
        )
    )
    return {r for r in rows}


def _can_view_folder(
    session: Session, folder: EmailTemplateFolder, user: User
) -> bool:
    """Sprint Email v2.5 — C. Visibilidad: private (solo owner),
    team (todo el CRM), shared (owner + lista de email_template_folder_
    shares). Admin todo."""
    if user.role == UserRole.ADMIN:
        return True
    visibility = folder.visibility or "private"
    if visibility == "team":
        return True
    if folder.owner_user_id == user.id:
        return True
    if visibility == "shared":
        return user.id in _folder_share_user_ids(session, folder.id)
    return False


def _can_edit_folder(
    session: Session, folder: EmailTemplateFolder, user: User
) -> bool:
    """Sprint Email v2.5 — C. Edit-rights == view-rights para team /
    shared (Bart's spec: cualquiera dentro puede editar). Private sigue
    siendo owner-only."""
    return _can_view_folder(session, folder, user)


def _can_view_template(
    session: Session, template: EmailTemplate, user: User
) -> bool:
    """View == edit con la única diferencia de los globals legacy:
    cualquier user los ve aunque no los pueda editar."""
    if user.role == UserRole.ADMIN:
        return True
    if template.is_global:
        return True
    if template.owner_user_id and template.owner_user_id == user.id:
        return True
    if template.folder_id:
        folder = session.get(EmailTemplateFolder, template.folder_id)
        if folder is not None and _can_view_folder(session, folder, user):
            return True
    return False


def _can_edit_template(
    session: Session, template: EmailTemplate, user: User
) -> bool:
    """Sprint Email v2.5 — C. Hereda permisos de la carpeta cuando
    existe. Sin carpeta vuelve al chequeo legacy (owner-only) para que
    los Gmail Templates importados (folder is_global) sigan siendo
    visibles a través del mecanismo team."""
    if user.role == UserRole.ADMIN:
        return True
    if template.owner_user_id and template.owner_user_id == user.id:
        return True
    if template.folder_id:
        folder = session.get(EmailTemplateFolder, template.folder_id)
        if folder is not None and _can_edit_folder(session, folder, user):
            return True
    return False


def _visible_templates_query(session: Session, user: User):
    """Sprint Email v2.5 — C. Filtra a templates visibles:

    - Admin: todo.
    - Cualquier user: templates propias.
    - Templates con folder `team`: visibles para todos.
    - Templates con folder `shared`: visibles si user está en
      `email_template_folder_shares` o es owner de la carpeta.
    - Templates con `is_global=True` y sin folder (legacy): visibles
      para todos (cubre el caso "Gmail (importadas)" pre v2.5).
    """
    if user.role == UserRole.ADMIN:
        return select(EmailTemplate)

    team_folder_ids = select(EmailTemplateFolder.id).where(
        EmailTemplateFolder.visibility == "team"
    )
    shared_folder_ids = select(EmailTemplateFolder.id).where(
        EmailTemplateFolder.visibility == "shared",
        EmailTemplateFolder.id.in_(
            select(EmailTemplateFolderShare.folder_id).where(
                EmailTemplateFolderShare.user_id == user.id
            )
        ),
    )
    owned_shared_folder_ids = select(EmailTemplateFolder.id).where(
        EmailTemplateFolder.visibility == "shared",
        EmailTemplateFolder.owner_user_id == user.id,
    )
    return select(EmailTemplate).where(
        or_(
            EmailTemplate.owner_user_id == user.id,
            EmailTemplate.is_global.is_(True),
            EmailTemplate.folder_id.in_(team_folder_ids),
            EmailTemplate.folder_id.in_(shared_folder_ids),
            EmailTemplate.folder_id.in_(owned_shared_folder_ids),
        )
    )


def _normalise_visibility(payload: FolderWrite) -> str:
    """Maps the legacy `is_global` flag onto `visibility` when the
    client doesn't ship the new field. Keeps pre-v2.5 frontends
    working without changes."""
    if payload.visibility is not None:
        return payload.visibility
    return "team" if payload.is_global else "private"


def _folder_read(
    session: Session, folder: EmailTemplateFolder
) -> FolderRead:
    """Hydrate a FolderRead with the shared_user_ids list. Pulled into
    a helper so create/update/delete share-handlers reuse it without
    re-querying."""
    shared_ids: list[str] = []
    if (folder.visibility or "private") == "shared":
        shared_ids = sorted(_folder_share_user_ids(session, folder.id))
    base = FolderRead.model_validate(folder).model_dump()
    base["shared_user_ids"] = shared_ids
    return FolderRead.model_validate(base)


def _sync_folder_shares(
    session: Session,
    folder: EmailTemplateFolder,
    user_ids: list[str],
) -> None:
    """Reconcilia la tabla `email_template_folder_shares` con la lista
    deseada. Idempotente: existing rows que ya estén en `user_ids` se
    mantienen, las que sobran se borran, las nuevas se insertan."""
    from uuid import uuid4  # noqa: PLC0415

    target = {uid for uid in user_ids if uid}
    existing = list(
        session.scalars(
            select(EmailTemplateFolderShare).where(
                EmailTemplateFolderShare.folder_id == folder.id
            )
        )
    )
    keep: set[str] = set()
    for row in existing:
        if row.user_id in target:
            keep.add(row.user_id)
        else:
            session.delete(row)
    now = _now()
    for uid in target - keep:
        session.add(
            EmailTemplateFolderShare(
                id=str(uuid4()),
                folder_id=folder.id,
                user_id=uid,
                created_at=now,
            )
        )


# ───────────────────────────────────────────────────────────────────
# Templates CRUD
# ───────────────────────────────────────────────────────────────────


@router.get("/email-templates", response_model=list[TemplateListItem])
def list_templates(
    folder_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    my_only: bool = Query(default=False, alias="my-only"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[TemplateListItem]:
    stmt = _visible_templates_query(session, current_user)
    if my_only:
        stmt = stmt.where(EmailTemplate.owner_user_id == current_user.id)
    if folder_id is not None:
        if folder_id == "":
            stmt = stmt.where(EmailTemplate.folder_id.is_(None))
        else:
            stmt = stmt.where(EmailTemplate.folder_id == folder_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(EmailTemplate.name.ilike(like))
    rows = list(session.scalars(stmt.order_by(EmailTemplate.name)))
    return [TemplateListItem.model_validate(r) for r in rows]


@router.post(
    "/email-templates",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    payload: TemplateWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TemplateRead:
    if payload.is_global and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo admin puede compartir plantillas con el equipo. "
                "Crea la plantilla sin marcarla como global; un admin "
                "podrá compartirla después."
            ),
        )
    if payload.folder_id is not None:
        folder = session.get(EmailTemplateFolder, payload.folder_id)
        if folder is None:
            raise not_found("EmailTemplateFolder")
        # Sprint Email v2.5 — C. El operador necesita derechos de
        # edición en la carpeta destino. team/shared son no-op para
        # cualquier user; private fuera de la propia rechaza.
        if not _can_edit_folder(session, folder, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permiso para crear plantillas en esta carpeta."
                ),
            )
    now = _now()
    template = EmailTemplate(
        name=payload.name.strip(),
        subject=payload.subject,
        body_html=payload.body_html,
        body_text=extract_text_from_html(payload.body_html),
        folder_id=payload.folder_id,
        owner_user_id=current_user.id,
        is_global=payload.is_global,
        usage_count=0,
        last_used_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return TemplateRead.model_validate(template)


@router.get("/email-templates/{template_id}", response_model=TemplateRead)
def get_template(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TemplateRead:
    template = session.get(EmailTemplate, template_id)
    if template is None:
        raise not_found("EmailTemplate")
    if not _can_view_template(session, template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver esta plantilla.",
        )
    return TemplateRead.model_validate(template)


@router.get(
    "/email-templates/{template_id}/attachments/by-cid/{cid}",
    response_class=Response,
)
def get_template_attachment_by_cid(
    template_id: str,
    cid: str,
    session: Session = Depends(get_session),
) -> Response:
    """Sirve el binario inline de un attachment (Gmail Templates
    import). El editor TinyMCE incrusta `<img src="…/by-cid/X">`
    apuntando aquí; el endpoint devuelve los bytes con `Cache-Control:
    immutable` para que el navegador no los re-pida.

    Endpoint público (sin `require_user`) — los <img> tags del HTML
    se cargan desde el browser sin headers Authorization, así que
    forzar Bearer lo rompería todo. La protección efectiva es la
    indirection: hace falta conocer `template_id` (UUID, 122 bits) y
    `cid` para encontrar la fila. Mismo patrón que sigue
    `/assets/email-templates/` para imágenes subidas vía editor.
    """
    row = session.scalar(
        select(EmailTemplateAttachment).where(
            EmailTemplateAttachment.template_id == template_id,
            EmailTemplateAttachment.original_cid == cid,
        )
    )
    if row is None:
        raise not_found("EmailTemplateAttachment")
    return Response(
        content=bytes(row.data),
        media_type=row.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.put("/email-templates/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: str,
    payload: TemplateWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> TemplateRead:
    template = session.get(EmailTemplate, template_id)
    if template is None:
        raise not_found("EmailTemplate")
    # PR-Backlog-3-5-7. Distinguimos "editar plantilla global del
    # equipo" (siempre 403 para non-admin) vs "editar plantilla
    # propia que casualmente está marcada como global" (OK si el
    # flag no cambia).
    is_team_global_other = (
        template.is_global
        and template.owner_user_id != current_user.id
    )
    if not _can_edit_template(session, template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No puedes editar plantillas globales del equipo. "
                "Pide a un admin que la edite o duplícala en una "
                "carpeta propia para personalizarla."
                if is_team_global_other
                else "No tienes permiso para editar esta plantilla."
            ),
        )
    # Solo bloqueamos el CAMBIO del flag is_global. Reenviar el
    # estado actual sin tocarlo (caso típico: el frontend manda el
    # objeto entero al guardar) NO debe disparar 403. Solo el
    # admin puede FLIPEAR el flag.
    is_global_changed = bool(payload.is_global) != bool(template.is_global)
    if is_global_changed and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo admin puede compartir plantillas con el equipo "
                "o quitar el flag global."
            ),
        )
    if payload.folder_id is not None:
        folder = session.get(EmailTemplateFolder, payload.folder_id)
        if folder is None:
            raise not_found("EmailTemplateFolder")
        if not _can_edit_folder(session, folder, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permiso para mover plantillas a esta carpeta."
                ),
            )
    template.name = payload.name.strip()
    template.subject = payload.subject
    template.body_html = payload.body_html
    template.body_text = extract_text_from_html(payload.body_html)
    template.folder_id = payload.folder_id
    if current_user.role == UserRole.ADMIN:
        template.is_global = payload.is_global
    template.updated_at = _now()
    session.commit()
    session.refresh(template)
    return TemplateRead.model_validate(template)


@router.delete("/email-templates/{template_id}")
def delete_template(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    template = session.get(EmailTemplate, template_id)
    if template is None:
        raise not_found("EmailTemplate")
    if not _can_edit_template(session, template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No tienes permiso para borrar esta plantilla."
            ),
        )
    session.delete(template)
    session.commit()
    return {"message": "deleted"}


@router.post("/email-templates/{template_id}/use")
def record_template_use(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, int]:
    template = session.get(EmailTemplate, template_id)
    if template is None:
        raise not_found("EmailTemplate")
    if not _can_view_template(session, template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para esta plantilla.",
        )
    template.usage_count = (template.usage_count or 0) + 1
    template.last_used_at = _now()
    session.commit()
    return {"usage_count": template.usage_count}


# ───────────────────────────────────────────────────────────────────
# Folders CRUD
# ───────────────────────────────────────────────────────────────────


def _default_template_folder_id(session: Session, user_id: str) -> str | None:
    """PR-Workflows-Pipelines-Per-User mini-fix. Devuelve el folder_id
    marcado como predeterminado del user para el modal Nuevo email."""
    from app.models.crm import UserTemplateFolderPref  # noqa: PLC0415

    return session.scalar(
        select(UserTemplateFolderPref.folder_id).where(
            UserTemplateFolderPref.user_id == user_id,
        )
    )


def _build_tree_nodes(
    session: Session,
    parent_id: str | None,
    depth: int,
    user: User,
    *,
    default_folder_id: str | None = None,
) -> list[FolderTreeNode]:
    """Recursively build the folder tree from a starting parent.

    Sprint Email v2.5 — C. Filtra por visibilidad del user: admin lo ve
    todo; el resto solo ve private propios, team, y shared en los que
    estén invitados. La jerarquía se mantiene aunque un nodo
    intermedio sea private de otro user — en ese caso ese nodo se
    salta pero sus hijos visibles suben un nivel (poco común, pero
    evita árboles colgados)."""
    nodes: list[FolderTreeNode] = []
    for folder in descendants(session, parent_id):
        children = (
            _build_tree_nodes(
                session,
                folder.id,
                depth + 1,
                user,
                default_folder_id=default_folder_id,
            )
            if depth + 1 < MAX_FOLDER_DEPTH
            else []
        )
        if not _can_view_folder(session, folder, user):
            # Propagamos los hijos visibles al padre para no perder
            # subcarpetas team/shared dentro de un private ajeno.
            nodes.extend(children)
            continue
        template_count = (
            session.scalar(
                select(EmailTemplate)
                .where(EmailTemplate.folder_id == folder.id)
                .with_only_columns(EmailTemplate.id)
            )
            and len(
                list(
                    session.scalars(
                        select(EmailTemplate).where(
                            EmailTemplate.folder_id == folder.id
                        )
                    )
                )
            )
        ) or 0
        nodes.append(
            FolderTreeNode(
                id=folder.id,
                name=folder.name,
                is_global=folder.is_global,
                visibility=folder.visibility or "private",
                sort_order=folder.sort_order,
                children=children,
                template_count=template_count,
                is_default_for_me=(
                    default_folder_id is not None
                    and folder.id == default_folder_id
                ),
            )
        )
    return nodes


@router.get("/email-template-folders", response_model=list[FolderTreeNode])
def list_folder_tree(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[FolderTreeNode]:
    """Recursive tree from the root.

    Sprint Email v2.5 — C. La visibilidad se aplica en backend: el
    user solo recibe nodos que puede ver (private suyo, team del
    equipo, shared invitado). El frontend agrupa por icono según
    `visibility`."""
    default_folder_id = _default_template_folder_id(session, current_user.id)
    return _build_tree_nodes(
        session,
        None,
        0,
        current_user,
        default_folder_id=default_folder_id,
    )


# PR-Workflows-Pipelines-Per-User mini-fix. Endpoints para marcar la
# carpeta predeterminada del current_user al cargar plantillas en el
# modal Nuevo email.
@router.put(
    "/users/me/default-template-folder",
    status_code=status.HTTP_204_NO_CONTENT,
)
def set_default_template_folder(
    payload: DefaultTemplateFolderRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    from app.models.crm import UserTemplateFolderPref  # noqa: PLC0415

    if payload.folder_id is not None:
        folder = session.get(EmailTemplateFolder, payload.folder_id)
        if folder is None or not _can_view_folder(
            session, folder, current_user
        ):
            raise not_found("EmailTemplateFolder")
        existing = session.scalar(
            select(UserTemplateFolderPref).where(
                UserTemplateFolderPref.user_id == current_user.id
            )
        )
        if existing is None:
            session.add(
                UserTemplateFolderPref(
                    user_id=current_user.id,
                    folder_id=payload.folder_id,
                )
            )
        else:
            existing.folder_id = payload.folder_id
    else:
        # Clear — borra la fila si existe (idempotente).
        existing = session.scalar(
            select(UserTemplateFolderPref).where(
                UserTemplateFolderPref.user_id == current_user.id
            )
        )
        if existing is not None:
            session.delete(existing)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/users/me/default-template-folder",
    response_model=DefaultTemplateFolderResponse,
)
def get_default_template_folder(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> DefaultTemplateFolderResponse:
    folder_id = _default_template_folder_id(session, current_user.id)
    return DefaultTemplateFolderResponse(folder_id=folder_id)


@router.post(
    "/email-template-folders",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_folder(
    payload: FolderWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> FolderRead:
    visibility = _normalise_visibility(payload)
    if payload.parent_folder_id is not None:
        parent = session.get(EmailTemplateFolder, payload.parent_folder_id)
        if parent is None:
            raise not_found("EmailTemplateFolder")
        if folder_depth(session, parent.id) + 1 > MAX_FOLDER_DEPTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Profundidad máxima alcanzada ({MAX_FOLDER_DEPTH} niveles)."
                ),
            )
        if not _can_edit_folder(session, parent, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permiso para crear subcarpetas aquí."
                ),
            )
    now = _now()
    folder = EmailTemplateFolder(
        name=payload.name.strip(),
        parent_folder_id=payload.parent_folder_id,
        owner_user_id=current_user.id,
        is_global=(visibility == "team"),
        visibility=visibility,
        sort_order=payload.sort_order,
        created_at=now,
        updated_at=now,
    )
    session.add(folder)
    session.flush()
    if visibility == "shared":
        _sync_folder_shares(session, folder, payload.shared_user_ids)
    session.commit()
    session.refresh(folder)
    return _folder_read(session, folder)


@router.put("/email-template-folders/{folder_id}", response_model=FolderRead)
def update_folder(
    folder_id: str,
    payload: FolderWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> FolderRead:
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        raise not_found("EmailTemplateFolder")
    if not _can_edit_folder(session, folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para editar esta carpeta.",
        )
    if payload.parent_folder_id is not None and payload.parent_folder_id != folder_id:
        parent = session.get(EmailTemplateFolder, payload.parent_folder_id)
        if parent is None:
            raise not_found("EmailTemplateFolder")
        # Guard against introducing a cycle: walk up from `parent` and
        # bail if we run into `folder_id`.
        seen = parent.parent_folder_id
        while seen is not None:
            if seen == folder_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No puedes mover una carpeta dentro de sí misma.",
                )
            ancestor = session.get(EmailTemplateFolder, seen)
            seen = ancestor.parent_folder_id if ancestor else None
    elif payload.parent_folder_id == folder_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Una carpeta no puede ser su propio padre.",
        )
    folder.name = payload.name.strip()
    folder.parent_folder_id = payload.parent_folder_id
    visibility = _normalise_visibility(payload)
    folder.visibility = visibility
    folder.is_global = (visibility == "team")
    folder.sort_order = payload.sort_order
    folder.updated_at = _now()
    if visibility == "shared":
        _sync_folder_shares(session, folder, payload.shared_user_ids)
    else:
        # Cambio a private/team → vaciar la lista de shares para que el
        # estado sea consistente.
        _sync_folder_shares(session, folder, [])
    session.commit()
    session.refresh(folder)
    return _folder_read(session, folder)


@router.post(
    "/email-template-folders/{folder_id}/shares",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
)
def add_folder_share(
    folder_id: str,
    payload: FolderShareWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> FolderRead:
    """Sprint Email v2.5 — C. Añade un user a la lista de acceso de una
    carpeta `shared`. Idempotente: si el user ya está, devuelve 201
    igual (la UNIQUE garantiza una sola fila)."""
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        raise not_found("EmailTemplateFolder")
    if not _can_edit_folder(session, folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para gestionar accesos de esta carpeta.",
        )
    if (folder.visibility or "private") != "shared":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo las carpetas 'shared' admiten lista de acceso.",
        )
    user = session.get(User, payload.user_id)
    if user is None:
        raise not_found("User")
    existing_ids = _folder_share_user_ids(session, folder_id)
    if payload.user_id not in existing_ids:
        from uuid import uuid4  # noqa: PLC0415

        session.add(
            EmailTemplateFolderShare(
                id=str(uuid4()),
                folder_id=folder_id,
                user_id=payload.user_id,
                created_at=_now(),
            )
        )
        session.commit()
        session.refresh(folder)
    return _folder_read(session, folder)


@router.delete(
    "/email-template-folders/{folder_id}/shares/{user_id}",
    response_model=FolderRead,
)
def remove_folder_share(
    folder_id: str,
    user_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> FolderRead:
    """Quita un user de la lista de acceso de una carpeta shared.
    Idempotente: 200 también si el user no estaba."""
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        raise not_found("EmailTemplateFolder")
    if not _can_edit_folder(session, folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para gestionar accesos de esta carpeta.",
        )
    row = session.scalar(
        select(EmailTemplateFolderShare).where(
            EmailTemplateFolderShare.folder_id == folder_id,
            EmailTemplateFolderShare.user_id == user_id,
        )
    )
    if row is not None:
        session.delete(row)
        session.commit()
        session.refresh(folder)
    return _folder_read(session, folder)


@router.delete("/email-template-folders/{folder_id}")
def delete_folder(
    folder_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        raise not_found("EmailTemplateFolder")
    if not _can_edit_folder(session, folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para borrar esta carpeta.",
        )
    # Explicit nullification: in production the FK ON DELETE SET NULL
    # handles this, but SQLite (tests) doesn't enforce FK actions by
    # default and we'd rather not depend on PRAGMA at runtime.
    session.query(EmailTemplate).filter(
        EmailTemplate.folder_id == folder_id
    ).update({EmailTemplate.folder_id: None}, synchronize_session=False)
    session.query(EmailTemplateFolder).filter(
        EmailTemplateFolder.parent_folder_id == folder_id
    ).update(
        {EmailTemplateFolder.parent_folder_id: None},
        synchronize_session=False,
    )
    session.delete(folder)
    session.commit()
    return {"message": "deleted"}


# ───────────────────────────────────────────────────────────────────
# Picker — mixed CRM + Brevo + Recent
# ───────────────────────────────────────────────────────────────────


@router.get("/emails/templates-picker", response_model=PickerResponse)
def templates_picker(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> PickerResponse:
    crm_rows = list(
        session.scalars(
            _visible_templates_query(session, current_user).order_by(
                EmailTemplate.name
            )
        )
    )
    brevo_rows = list(
        session.scalars(
            select(BrevoTemplateCache)
            .where(BrevoTemplateCache.is_active.is_(True))
            .order_by(BrevoTemplateCache.name)
        )
    )
    folders = _build_tree_nodes(session, None, 0, current_user)
    recent_rows = list(
        session.scalars(
            _visible_templates_query(session, current_user)
            .where(EmailTemplate.last_used_at.isnot(None))
            .order_by(
                EmailTemplate.last_used_at.desc(),
                EmailTemplate.usage_count.desc(),
            )
            .limit(10)
        )
    )

    return PickerResponse(
        crm=[TemplateListItem.model_validate(r) for r in crm_rows],
        brevo=[
            BrevoPickerItem(
                id=r.brevo_template_id,
                name=r.name,
                subject=r.subject,
                sender_name=r.sender_name,
                has_html=bool(r.html_content),
            )
            for r in brevo_rows
        ],
        folders=folders,
        recent=[TemplateListItem.model_validate(r) for r in recent_rows],
    )


@router.get(
    "/emails/brevo-templates/{brevo_template_id}/html",
    response_model=BrevoTemplateHtmlResponse,
)
def get_brevo_template_html(
    brevo_template_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),  # noqa: ARG001
) -> BrevoTemplateHtmlResponse:
    """Lazy-loaded body for the send-modal picker's Brevo tab.

    The marketing cache row stores html_content NULL until first
    detail open; the picker was therefore handing the editor an
    empty string. Here we fetch + persist on demand the same way
    the /marketing detail screen does — first hit pays the Brevo
    round-trip, every later open is served straight from the cache.

    The `picker` field on the response is what the editor pastes
    in; `subject` lets the front pre-fill the asunto field too.
    """
    row = session.scalar(
        select(BrevoTemplateCache)
        .where(BrevoTemplateCache.brevo_template_id == brevo_template_id)
        .where(BrevoTemplateCache.is_active.is_(True))
        .order_by(BrevoTemplateCache.cached_at.desc())
    )
    if row is None:
        raise not_found("BrevoTemplate")
    if row.html_content is None:
        try:
            # The Brevo client is async; the rest of this router is
            # sync. Same trampoline pattern as /api/brevo/templates/{id}.
            asyncio.run(
                _brevo_templates_service.ensure_template_html(session, row)
            )
            session.commit()
            session.refresh(row)
        except IntegrationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=exc.message,
            ) from exc
    return BrevoTemplateHtmlResponse(
        id=row.brevo_template_id,
        name=row.name,
        subject=row.subject,
        body_html=row.html_content or "",
    )


# ───────────────────────────────────────────────────────────────────
# Composer-source proxy + image upload — Sprint Email v2.2b
# ───────────────────────────────────────────────────────────────────


@router.get(
    "/emails/composer-source", response_model=ComposerSourceResponse
)
def list_composer_source(
    current_user: User = Depends(require_user),  # noqa: ARG001
) -> ComposerSourceResponse:
    """Read-only mirror of composer.bomedia.net templates. Always
    returns 200 — when Supabase is unreachable the items list is empty
    and `error` carries a user-facing string. That way the picker tab
    can render a notice and the rest of the picker keeps working."""
    items, error = fetch_composer_templates()
    return ComposerSourceResponse(
        items=[
            ComposerSourcePickerItem(
                id=item.id,
                name=item.name,
                brand=item.brand,
                blocks_count=item.blocks_count,
                open_url=item.open_url,
            )
            for item in items
        ],
        error=error,
    )


@router.post(
    "/email-templates/assets", response_model=ImageUploadResponse
)
async def upload_email_asset(
    file: UploadFile = File(...),
    current_user: User = Depends(require_user),  # noqa: ARG001
) -> ImageUploadResponse:
    """Inline image upload for the Tiptap editor.

    Content-addressed: same bytes → same SHA256 → same path. That way
    re-uploading a logo doesn't multiply the dedup cost and the public
    URL is stable across edits. Files land under
    `{email_assets_dir}/{YYYY}/{MM}/{sha256}.{ext}` so a single
    directory never grows past a few hundred entries.

    The URL we return is absolute when `EMAIL_ASSETS_PUBLIC_BASE` is
    set — recipients' inboxes need a full `https://` URL to render
    inline images. In dev / tests we fall back to a root-relative
    path served by the StaticFiles mount in `main.py`.
    """
    settings = get_settings()
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Formato no admitido. Sube PNG, JPG, GIF o WebP."
            ),
        )

    # Read into memory once — caps the read at max_bytes + 1 so an
    # oversized upload doesn't fill the disk before we reject it.
    max_bytes = settings.email_assets_max_bytes
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"La imagen supera el máximo permitido "
                f"({max_bytes // (1024 * 1024)} MB)."
            ),
        )

    digest = hashlib.sha256(data).hexdigest()
    suffix = mimetypes.guess_extension(content_type) or ""
    today = datetime.now(UTC)
    year, month = f"{today.year:04d}", f"{today.month:02d}"
    relative = f"{year}/{month}/{digest}{suffix}"
    target_dir = Path(settings.email_assets_dir) / year / month
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(data)

    public_path = f"/assets/email-templates/{relative}"
    public_url = (
        f"{settings.email_assets_public_base.rstrip('/')}{public_path}"
        if settings.email_assets_public_base
        else public_path
    )
    return ImageUploadResponse(
        public_url=public_url,
        filename=f"{digest}{suffix}",
        content_type=content_type,
        size_bytes=len(data),
    )


@router.post(
    "/email-templates/import-gmail",
    status_code=status.HTTP_202_ACCEPTED,
)
def import_gmail_templates(
    delete_after: bool = False,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
):
    """Encola el import a la cola `email_templates:import_gmail`.

    El import síncrono tardaba 1-3 min para 48 plantillas (cuello de
    botella en `drafts.get` × cada attachment) y Nginx cerraba a los
    60 s con 504. Lo movemos al worker y devolvemos 202 + el
    `sync_log_id` para que la UI redirija al panel de progreso.

    Idempotente desde el handler — re-encolar el job en paralelo no
    causa duplicados (skip por `(name, folder_id)` en el handler).

    Admin-only — el job usa la cuenta Gmail del admin que dispara la
    importación; los users normales no tocan el buzón compartido.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo admin puede importar plantillas desde Gmail.",
        )
    from app.models.crm import ExternalSystem, SyncTrigger  # noqa: PLC0415
    from app.workers.jobs import enqueue_sync_job  # noqa: PLC0415

    sync_log_id, job_id = enqueue_sync_job(
        session,
        system=ExternalSystem.EMAIL_TEMPLATES,
        # account_id no aplica — no hay integration_accounts para este
        # canal. El handler sigue funcionando porque el column es
        # nullable a nivel de DB; pasamos un sentinel para que el
        # listado UI filtre limpio.
        account_id="gmail_import",
        operation="import_gmail",
        triggered_by=SyncTrigger.MANUAL,
        triggered_by_user_id=current_user.id,
        payload={
            "user_id": current_user.id,
            "delete_after": delete_after,
        },
    )
    return {
        "sync_log_id": sync_log_id,
        "job_id": job_id,
        "status": "pending",
    }
