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

from .models import EmailTemplate, EmailTemplateFolder
from .schemas import (
    BrevoPickerItem,
    BrevoTemplateHtmlResponse,
    ComposerSourcePickerItem,
    ComposerSourceResponse,
    FolderRead,
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


def _can_edit_template(template: EmailTemplate, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if template.owner_user_id and template.owner_user_id == user.id:
        return True
    return False


def _can_edit_folder(folder: EmailTemplateFolder, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if folder.owner_user_id and folder.owner_user_id == user.id:
        return True
    return False


def _visible_templates_query(user: User):
    """Build the WHERE clause that filters to templates the user can
    see (global + their own)."""
    return select(EmailTemplate).where(
        or_(
            EmailTemplate.is_global.is_(True),
            EmailTemplate.owner_user_id == user.id,
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
    stmt = _visible_templates_query(current_user)
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
            detail="Solo un admin puede marcar una plantilla como global.",
        )
    if payload.folder_id is not None:
        folder = session.get(EmailTemplateFolder, payload.folder_id)
        if folder is None:
            raise not_found("EmailTemplateFolder")
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
    if not template.is_global and template.owner_user_id != current_user.id:
        if current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para ver esta plantilla.",
            )
    return TemplateRead.model_validate(template)


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
    if not _can_edit_template(template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden editar esta plantilla.",
        )
    if payload.is_global and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un admin puede cambiar el flag is_global.",
        )
    if payload.folder_id is not None:
        folder = session.get(EmailTemplateFolder, payload.folder_id)
        if folder is None:
            raise not_found("EmailTemplateFolder")
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
    if not _can_edit_template(template, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden borrar esta plantilla.",
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
    if not template.is_global and template.owner_user_id != current_user.id:
        if current_user.role != UserRole.ADMIN:
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


def _build_tree_nodes(
    session: Session, parent_id: str | None, depth: int
) -> list[FolderTreeNode]:
    """Recursively build the folder tree from a starting parent."""
    nodes: list[FolderTreeNode] = []
    for folder in descendants(session, parent_id):
        children = (
            _build_tree_nodes(session, folder.id, depth + 1)
            if depth + 1 < MAX_FOLDER_DEPTH
            else []
        )
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
                sort_order=folder.sort_order,
                children=children,
                template_count=template_count,
            )
        )
    return nodes


@router.get("/email-template-folders", response_model=list[FolderTreeNode])
def list_folder_tree(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),  # noqa: ARG001
) -> list[FolderTreeNode]:
    """Recursive tree from the root. The frontend filters by ownership
    + `is_global` at render time; we ship every folder because the
    tree is small (<100 rows in expected workloads) and the picker
    needs cross-user visibility for global folders anyway."""
    return _build_tree_nodes(session, None, 0)


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
    if payload.is_global and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un admin puede crear carpetas globales.",
        )
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
    now = _now()
    folder = EmailTemplateFolder(
        name=payload.name.strip(),
        parent_folder_id=payload.parent_folder_id,
        owner_user_id=current_user.id,
        is_global=payload.is_global,
        sort_order=payload.sort_order,
        created_at=now,
        updated_at=now,
    )
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return FolderRead.model_validate(folder)


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
    if not _can_edit_folder(folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden editar esta carpeta.",
        )
    if payload.is_global and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un admin puede cambiar el flag is_global.",
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
    if current_user.role == UserRole.ADMIN:
        folder.is_global = payload.is_global
    folder.sort_order = payload.sort_order
    folder.updated_at = _now()
    session.commit()
    session.refresh(folder)
    return FolderRead.model_validate(folder)


@router.delete("/email-template-folders/{folder_id}")
def delete_folder(
    folder_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        raise not_found("EmailTemplateFolder")
    if not _can_edit_folder(folder, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden borrar esta carpeta.",
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
            _visible_templates_query(current_user).order_by(EmailTemplate.name)
        )
    )
    brevo_rows = list(
        session.scalars(
            select(BrevoTemplateCache)
            .where(BrevoTemplateCache.is_active.is_(True))
            .order_by(BrevoTemplateCache.name)
        )
    )
    folders = _build_tree_nodes(session, None, 0)
    recent_rows = list(
        session.scalars(
            _visible_templates_query(current_user)
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
