"""Composer REST surface — single router exposing every
`/api/composer/*` endpoint Fase 1 needs.

Kept in one module because the surface is wide but each
endpoint is short; splitting into per-resource files for v1
would add navigation cost without clarity. Future fases that
add the AI agent / backoffice CRUD can lift these out.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.composer.models import (
    ComposerAsset,
    ComposerBrand,
    ComposerComposedBlock,
    ComposerDraft,
    ComposerPrewrittenText,
    ComposerProduct,
    ComposerSettings,
    ComposerStandaloneBlock,
    ComposerTemplate,
    ComposerTemplateRevision,
)
from app.composer.permissions import (
    assert_can_write_catalog,
    assert_composer_access,
    assert_is_admin,
    can_edit_template,
)
from app.composer.schemas import (
    ComposerAssetRead,
    ComposerBrandRead,
    ComposerCatalog,
    ComposerComposedBlockRead,
    ComposerDraftRead,
    ComposerDraftWrite,
    ComposerPrewrittenTextRead,
    ComposerProductRead,
    ComposerSettingsRead,
    ComposerSettingsWrite,
    ComposerStandaloneBlockRead,
    ComposerTemplateRead,
    ComposerTemplateRevisionRead,
    ComposerTemplateWrite,
)
from app.composer.services import (
    hidden_items_for_user,
    record_activity,
    record_revision,
)
from app.core.auth import require_user
from app.core.crypto import decrypt, encrypt
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import User

router = APIRouter(prefix="/api/composer", tags=["composer"])
logger = logging.getLogger(__name__)

MAX_ASSET_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_ASSET_MIME = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
ASSET_ROOT = Path(
    os.environ.get("COMPOSER_ASSET_ROOT", "/opt/crmbo/uploads/composer")
)
ASSET_PUBLIC_BASE = os.environ.get(
    "COMPOSER_ASSET_PUBLIC_BASE", "/assets/composer"
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=ComposerCatalog)
def get_catalog(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerCatalog:
    assert_composer_access(current_user)
    hidden = hidden_items_for_user(session, current_user.id)
    brands = [
        ComposerBrandRead.model_validate(b)
        for b in session.scalars(
            select(ComposerBrand).order_by(
                ComposerBrand.sort_order, ComposerBrand.label
            )
        )
        if b.id not in hidden.get("brands", set())
    ]
    products = [
        ComposerProductRead.model_validate(p)
        for p in session.scalars(
            select(ComposerProduct).order_by(
                ComposerProduct.sort_order, ComposerProduct.name
            )
        )
        if p.id not in hidden.get("products", set())
    ]
    texts = [
        ComposerPrewrittenTextRead.model_validate(t)
        for t in session.scalars(
            select(ComposerPrewrittenText).order_by(
                ComposerPrewrittenText.sort_order, ComposerPrewrittenText.name
            )
        )
        if t.id not in hidden.get("prewrittenTexts", set())
    ]
    composed = [
        ComposerComposedBlockRead.model_validate(c)
        for c in session.scalars(
            select(ComposerComposedBlock).order_by(
                ComposerComposedBlock.sort_order, ComposerComposedBlock.title
            )
        )
        if c.id not in hidden.get("composedBlocks", set())
    ]
    standalone = [
        ComposerStandaloneBlockRead.model_validate(s)
        for s in session.scalars(
            select(ComposerStandaloneBlock).order_by(
                ComposerStandaloneBlock.sort_order, ComposerStandaloneBlock.title
            )
        )
        if s.id not in hidden.get("standaloneBlocks", set())
    ]
    return ComposerCatalog(
        brands=brands,
        products=products,
        prewritten_texts=texts,
        composed_blocks=composed,
        standalone_blocks=standalone,
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=list[ComposerTemplateRead])
def list_templates(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ComposerTemplateRead]:
    assert_composer_access(current_user)
    from sqlalchemy import or_ as _or  # noqa: PLC0415

    rows = list(
        session.scalars(
            select(ComposerTemplate)
            .where(
                _or(
                    ComposerTemplate.is_global.is_(True),
                    ComposerTemplate.owner_user_id == current_user.id,
                )
            )
            .order_by(ComposerTemplate.updated_at.desc())
        )
    )
    return [ComposerTemplateRead.model_validate(t) for t in rows]


@router.get("/templates/{template_id}", response_model=ComposerTemplateRead)
def get_template(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerTemplateRead:
    assert_composer_access(current_user)
    template = session.get(ComposerTemplate, template_id)
    if template is None:
        raise not_found("ComposerTemplate")
    if (
        not template.is_global
        and template.owner_user_id
        and template.owner_user_id != current_user.id
        and not can_edit_template(current_user, owner_user_id=template.owner_user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver esta plantilla.",
        )
    return ComposerTemplateRead.model_validate(template)


@router.post(
    "/templates",
    response_model=ComposerTemplateRead,
    status_code=201,
)
def create_template(
    payload: ComposerTemplateWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerTemplateRead:
    assert_composer_access(current_user)
    template_id = f"tpl-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{current_user.id[:8]}"
    now = datetime.now(UTC)
    template = ComposerTemplate(
        id=template_id,
        name=payload.name.strip(),
        description=payload.description,
        color_class=payload.color_class,
        brand_id=payload.brand_id,
        blocks_json=json.dumps(payload.blocks, default=str, ensure_ascii=False),
        compositor_blocks_json=(
            json.dumps(payload.compositor_blocks, default=str, ensure_ascii=False)
            if payload.compositor_blocks is not None
            else None
        ),
        is_global=payload.is_global,
        owner_user_id=current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(template)
    session.flush()
    record_revision(session, template=template, actor_user_id=current_user.id)
    record_activity(
        session,
        user_id=current_user.id,
        action="template.created",
        entity_type="composer_template",
        entity_id=template.id,
    )
    session.commit()
    session.refresh(template)
    return ComposerTemplateRead.model_validate(template)


@router.put("/templates/{template_id}", response_model=ComposerTemplateRead)
def update_template(
    template_id: str,
    payload: ComposerTemplateWrite,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerTemplateRead:
    assert_composer_access(current_user)
    template = session.get(ComposerTemplate, template_id)
    if template is None:
        raise not_found("ComposerTemplate")
    if not can_edit_template(current_user, owner_user_id=template.owner_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden editar esta plantilla.",
        )
    template.name = payload.name.strip()
    template.description = payload.description
    template.color_class = payload.color_class
    template.brand_id = payload.brand_id
    template.blocks_json = json.dumps(
        payload.blocks, default=str, ensure_ascii=False
    )
    template.compositor_blocks_json = (
        json.dumps(payload.compositor_blocks, default=str, ensure_ascii=False)
        if payload.compositor_blocks is not None
        else None
    )
    template.is_global = payload.is_global
    template.updated_at = datetime.now(UTC)
    session.flush()
    record_revision(session, template=template, actor_user_id=current_user.id)
    record_activity(
        session,
        user_id=current_user.id,
        action="template.updated",
        entity_type="composer_template",
        entity_id=template.id,
    )
    session.commit()
    session.refresh(template)
    return ComposerTemplateRead.model_validate(template)


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    assert_composer_access(current_user)
    template = session.get(ComposerTemplate, template_id)
    if template is None:
        raise not_found("ComposerTemplate")
    if not can_edit_template(current_user, owner_user_id=template.owner_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el creador o un admin pueden borrar esta plantilla.",
        )
    session.delete(template)
    record_activity(
        session,
        user_id=current_user.id,
        action="template.deleted",
        entity_type="composer_template",
        entity_id=template_id,
    )
    session.commit()
    return {"message": "deleted"}


@router.get(
    "/templates/{template_id}/revisions",
    response_model=list[ComposerTemplateRevisionRead],
)
def list_template_revisions(
    template_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ComposerTemplateRevisionRead]:
    assert_composer_access(current_user)
    template = session.get(ComposerTemplate, template_id)
    if template is None:
        raise not_found("ComposerTemplate")
    rows = list(
        session.scalars(
            select(ComposerTemplateRevision)
            .where(ComposerTemplateRevision.template_id == template_id)
            .order_by(ComposerTemplateRevision.created_at.desc())
        )
    )
    return [ComposerTemplateRevisionRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


@router.get("/drafts", response_model=ComposerDraftRead)
def get_draft(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerDraftRead:
    assert_composer_access(current_user)
    draft = session.get(ComposerDraft, current_user.id)
    if draft is None:
        return ComposerDraftRead(state={}, updated_at=None)
    try:
        state = json.loads(draft.state_json)
    except (TypeError, ValueError):
        state = {}
    return ComposerDraftRead(state=state, updated_at=draft.updated_at)


@router.put("/drafts", response_model=ComposerDraftRead)
def upsert_draft(
    payload: ComposerDraftWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerDraftRead:
    assert_composer_access(current_user)
    serialised = json.dumps(payload.state, default=str, ensure_ascii=False)
    now = datetime.now(UTC)
    draft = session.get(ComposerDraft, current_user.id)
    if draft is None:
        draft = ComposerDraft(
            user_id=current_user.id, state_json=serialised, updated_at=now
        )
        session.add(draft)
    else:
        draft.state_json = serialised
        draft.updated_at = now
    session.commit()
    return ComposerDraftRead(state=payload.state, updated_at=now)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@router.post("/assets", response_model=ComposerAssetRead, status_code=201)
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerAssetRead:
    """Save the uploaded file to disk dedupe-d by sha256."""
    assert_composer_access(current_user)
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    if content_type not in ALLOWED_ASSET_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de archivo no permitido: {content_type!r}",
        )
    body = await file.read()
    if len(body) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo supera el tamaño máximo (10 MB).",
        )
    sha = hashlib.sha256(body).hexdigest()
    existing = session.scalar(
        select(ComposerAsset).where(ComposerAsset.sha256 == sha)
    )
    if existing is not None:
        return ComposerAssetRead.model_validate(existing)
    now = datetime.now(UTC)
    ext = mimetypes.guess_extension(content_type) or ".bin"
    rel_dir = Path(f"{now.year:04d}/{now.month:02d}")
    rel_path = rel_dir / f"{sha}{ext}"
    target = ASSET_ROOT / rel_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError as exc:
        logger.warning("composer.asset.write_failed %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo guardar el archivo en disco.",
        ) from exc
    asset = ComposerAsset(
        user_id=current_user.id,
        filename=file.filename or f"{sha}{ext}",
        mime_type=content_type,
        size_bytes=len(body),
        sha256=sha,
        storage_path=str(target),
        public_url=f"{ASSET_PUBLIC_BASE.rstrip('/')}/{rel_path.as_posix()}",
        source="upload",
        metadata_json="{}",
        created_at=now,
    )
    session.add(asset)
    record_activity(
        session,
        user_id=current_user.id,
        action="asset.uploaded",
        entity_type="composer_asset",
        entity_id=asset.id,
        metadata={"filename": asset.filename, "size_bytes": asset.size_bytes},
    )
    session.commit()
    session.refresh(asset)
    return ComposerAssetRead.model_validate(asset)


@router.get("/assets", response_model=list[ComposerAssetRead])
def list_assets(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> list[ComposerAssetRead]:
    assert_composer_access(current_user)
    rows = list(
        session.scalars(
            select(ComposerAsset)
            .where(ComposerAsset.user_id == current_user.id)
            .order_by(ComposerAsset.created_at.desc())
            .limit(200)
        )
    )
    return [ComposerAssetRead.model_validate(a) for a in rows]


@router.delete("/assets/{asset_id}")
def delete_asset(
    asset_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    assert_composer_access(current_user)
    asset = session.get(ComposerAsset, asset_id)
    if asset is None:
        raise not_found("ComposerAsset")
    from app.models.crm import UserRole  # noqa: PLC0415

    if asset.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para borrar este asset.",
        )
    session.delete(asset)
    session.commit()
    return {"message": "deleted"}


# ---------------------------------------------------------------------------
# Settings (admin)
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=ComposerSettingsRead)
def get_settings(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerSettingsRead:
    assert_is_admin(current_user)
    row = session.get(ComposerSettings, 1)
    if row is None:
        return ComposerSettingsRead(
            openai_configured=False,
            ai_styles={},
            agent_system_prompt=None,
            updated_at=None,
        )
    try:
        styles = json.loads(row.ai_styles_json or "{}")
    except (TypeError, ValueError):
        styles = {}
    return ComposerSettingsRead(
        openai_configured=bool(row.openai_api_key_encrypted),
        ai_styles=styles,
        agent_system_prompt=row.agent_system_prompt,
        updated_at=row.updated_at,
    )


@router.put("/settings", response_model=ComposerSettingsRead)
def update_settings(
    payload: ComposerSettingsWrite,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> ComposerSettingsRead:
    assert_is_admin(current_user)
    row = session.get(ComposerSettings, 1)
    now = datetime.now(UTC)
    if row is None:
        row = ComposerSettings(
            id=1,
            openai_api_key_encrypted=None,
            ai_styles_json="{}",
            agent_system_prompt=None,
            updated_at=now,
        )
        session.add(row)
    if payload.openai_api_key is not None:
        row.openai_api_key_encrypted = (
            encrypt(payload.openai_api_key) if payload.openai_api_key else None
        )
    if payload.ai_styles is not None:
        row.ai_styles_json = json.dumps(
            payload.ai_styles, default=str, ensure_ascii=False
        )
    if payload.agent_system_prompt is not None:
        row.agent_system_prompt = payload.agent_system_prompt or None
    row.updated_at = now
    record_activity(
        session,
        user_id=current_user.id,
        action="settings.updated",
        entity_type="composer_settings",
        entity_id="1",
    )
    session.commit()
    try:
        styles = json.loads(row.ai_styles_json or "{}")
    except (TypeError, ValueError):
        styles = {}
    return ComposerSettingsRead(
        openai_configured=bool(row.openai_api_key_encrypted),
        ai_styles=styles,
        agent_system_prompt=row.agent_system_prompt,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# AI proxy stubs
# ---------------------------------------------------------------------------


def _require_openai_key(session: Session) -> str:
    """Return the decrypted OpenAI key, 503 when missing."""
    row = session.get(ComposerSettings, 1)
    if row is None or not row.openai_api_key_encrypted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI no está configurado. Pide al admin que añada la API key.",
        )
    return decrypt(row.openai_api_key_encrypted)


@router.post("/ai/agent/run")
def ai_agent_run(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Fase 1 stub — returns 503 unless the OpenAI key is
    configured. The Agent loop ports in Fase 3."""
    assert_composer_access(current_user)
    _require_openai_key(session)
    return {"status": "stub", "note": "Agent loop pending Fase 3"}


@router.post("/ai/rewrite")
def ai_rewrite(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    assert_composer_access(current_user)
    _require_openai_key(session)
    return {"status": "stub", "note": "Rewrite proxy pending Fase 2"}


@router.post("/ai/translate")
def ai_translate(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    assert_composer_access(current_user)
    _require_openai_key(session)
    return {"status": "stub", "note": "Translate proxy pending Fase 2"}
