"""Embed-facing endpoints — `/api/composer/state` + `/api/composer/backups`.

The CRM now embeds the standalone Bomedia Composer (literal JSX under
`frontend/public/composer/`). Its `app-supabase.jsx` replacement
reads a single monolithic state blob from `GET /state` and pushes the
modified blob back via `PUT /state`. Backups (`/backups`) are a
FIFO-trimmed snapshot ring the embed uses for "Restaurar versión
anterior" UI affordances.

This module deliberately lives next to (not inside) `router.py`:

- `router.py` ships the granular, typed Pydantic surface (catalog,
  templates, drafts, assets, settings, ai stubs) used by Fase 1
  integrations like Sprint Email v2.2.
- `state_router.py` ships the monolithic embed surface that mirrors
  the Composer's original Supabase layout.

Both routers read/write the same SQL tables.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.composer.models import (
    ComposerActivityLog,
    ComposerAsset,
    ComposerBrand,
    ComposerComposedBlock,
    ComposerPrewrittenText,
    ComposerProduct,
    ComposerSettings,
    ComposerStandaloneBlock,
    ComposerTemplate,
)
from app.composer.permissions import assert_composer_access
from app.core.auth import require_user
from app.core.crypto import decrypt, encrypt
from app.db.session import get_session
from app.models.crm import User, UserRole

router = APIRouter(prefix="/api/composer", tags=["composer-embed"])
logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────
# SQL row → embed JSON helpers (snake_case → camelCase, json text →
# parsed dict / list).
# ───────────────────────────────────────────────────────────────────


def _decode_json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _brand_to_jsx(b: ComposerBrand) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": b.id,
        "type": b.type,
        "label": b.label,
        "logo": b.logo,
        "logoText": b.logo_text,
        "color": b.color,
        "divider": b.divider,
        "logoHeight": b.logo_height,
        "logoMaxWidth": b.logo_max_width,
        "visible": b.visible,
        "sortOrder": b.sort_order,
    }
    i18n_data = _decode_json(b.i18n_json, {})
    if isinstance(i18n_data, dict):
        # `url` and `urlLabel` live inside i18n_json so the embed
        # picks them up via b.url[lang] / b.urlLabel[lang]. The Fase
        # 1 seed parked them there too.
        if "url" in i18n_data:
            base["url"] = i18n_data["url"]
        if "urlLabel" in i18n_data:
            base["urlLabel"] = i18n_data["urlLabel"]
        # Everything else gets re-exposed as `i18n` for getLocalizedText.
        leftover = {k: v for k, v in i18n_data.items() if k not in ("url", "urlLabel")}
        if leftover:
            base["i18n"] = leftover
    return base


def _product_to_jsx(p: ComposerProduct) -> dict[str, Any]:
    return {
        "id": p.id,
        "brand": p.brand_id,
        "name": p.name,
        "badge": p.badge,
        "badgeBg": p.badge_bg,
        "badgeColor": p.badge_color,
        "img": p.img,
        "desc": p.description,
        "area": p.area,
        "alt": p.alt,
        "feat1": p.feat1,
        "feat2": p.feat2,
        "price": p.price,
        "link": p.link,
        "accent": p.accent,
        "gradient": p.gradient,
        "visible": p.visible,
        "sortOrder": p.sort_order,
        "tags": _decode_json(p.tags, []),
        "i18n": _decode_json(p.i18n_json, {}),
    }


def _text_to_jsx(t: ComposerPrewrittenText) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "icon": t.icon,
        "brand": t.brand_id,
        "text": t.text,
        "visible": t.visible,
        "sortOrder": t.sort_order,
        "i18n": _decode_json(t.i18n_json, {}),
    }


def _composed_to_jsx(c: ComposerComposedBlock) -> dict[str, Any]:
    config = _decode_json(c.config_json, {})
    base: dict[str, Any] = {
        "id": c.id,
        "title": c.title,
        "desc": c.description,
        "priceRange": c.price_range,
        "colorTag": c.color_tag,
        "introText": c.intro_text,
        "brandStrip": c.brand_strip,
        "blockType": c.block_type,
        "products": _decode_json(c.products, []),
        "includeHero": c.include_hero,
        "includeSteps": c.include_steps,
        "visible": c.visible,
        "sortOrder": c.sort_order,
        "i18n": _decode_json(c.i18n_json, {}),
    }
    # innerBlocks + compositorBlocks ship inside config_json so the
    # SQL shape stays stable while the embed reads them as
    # first-class fields.
    if isinstance(config, dict):
        if "innerBlocks" in config:
            base["innerBlocks"] = config["innerBlocks"]
        if "compositorBlocks" in config:
            base["compositorBlocks"] = config["compositorBlocks"]
    return base


def _standalone_to_jsx(s: ComposerStandaloneBlock) -> dict[str, Any]:
    return {
        "id": s.id,
        "type": s.block_type,
        "blockType": s.block_type,
        "title": s.title,
        "desc": s.description,
        "icon": s.icon,
        "iconBg": s.icon_bg,
        "brand": s.brand_id,
        "section": s.section,
        "config": _decode_json(s.config_json, {}),
        "visible": s.visible,
        "sortOrder": s.sort_order,
        "i18n": _decode_json(s.i18n_json, {}),
    }


def _template_to_jsx(t: ComposerTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "desc": t.description,
        "colorClass": t.color_class,
        "brand": t.brand_id,
        "blocks": _decode_json(t.blocks_json, []),
        "compositorBlocks": _decode_json(t.compositor_blocks_json, None),
        "visible": t.visible,
        "isGlobal": t.is_global,
        "ownerUserId": t.owner_user_id,
    }


def _asset_to_jsx(a: ComposerAsset) -> dict[str, Any]:
    return {
        "id": a.id,
        "filename": a.filename,
        "mimeType": a.mime_type,
        "size": a.size_bytes,
        "sha256": a.sha256,
        "url": a.public_url,
        "source": a.source,
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


# ───────────────────────────────────────────────────────────────────
# State endpoints
# ───────────────────────────────────────────────────────────────────


@router.get("/state")
def get_composer_state(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Return the full state blob the embed expects on boot.

    Collections come from the CRM DB. Where the DB rows are absent
    or sparse, the embed's `app-data.jsx` provides defaults via its
    own `mergeI18nFromDefaults` step (so a fresh CRM install still
    shows the full catalog the embed ships with).
    """
    assert_composer_access(current_user)

    brands = list(
        session.scalars(
            select(ComposerBrand).order_by(
                ComposerBrand.sort_order, ComposerBrand.label
            )
        )
    )
    products = list(
        session.scalars(
            select(ComposerProduct).order_by(
                ComposerProduct.sort_order, ComposerProduct.name
            )
        )
    )
    texts = list(
        session.scalars(
            select(ComposerPrewrittenText).order_by(
                ComposerPrewrittenText.sort_order,
                ComposerPrewrittenText.name,
            )
        )
    )
    composed = list(
        session.scalars(
            select(ComposerComposedBlock).order_by(
                ComposerComposedBlock.sort_order,
                ComposerComposedBlock.title,
            )
        )
    )
    standalone = list(
        session.scalars(
            select(ComposerStandaloneBlock).order_by(
                ComposerStandaloneBlock.sort_order,
                ComposerStandaloneBlock.title,
            )
        )
    )
    # Templates: global + own.
    from sqlalchemy import or_ as _or  # noqa: PLC0415

    templates = list(
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
    assets = list(
        session.scalars(
            select(ComposerAsset)
            .where(ComposerAsset.user_id == current_user.id)
            .order_by(ComposerAsset.created_at.desc())
            .limit(200)
        )
    )
    settings = session.get(ComposerSettings, 1)

    # OpenAI key — admin only, decrypted in-process and returned for
    # the embed to use client-side (same posture as the original
    # Composer; the CRM session is already trusted at this point).
    openai_key = ""
    if (
        current_user.role == UserRole.ADMIN
        and settings is not None
        and settings.openai_api_key_encrypted
    ):
        try:
            openai_key = decrypt(settings.openai_api_key_encrypted)
        except Exception:  # noqa: BLE001
            logger.warning("composer.state openai key decrypt failed", exc_info=True)
            openai_key = ""

    # Single synthetic user — the embed's multi-user logic is dormant
    # under the CRM (auth lives at the CRM layer).
    synthetic_user = {
        "id": current_user.id,
        "name": current_user.full_name or current_user.email,
        "role": "admin" if current_user.role == UserRole.ADMIN else (
            "manager" if current_user.role == UserRole.MANAGER else "user"
        ),
        "hiddenItems": {},
        "aiStyles": {},
    }

    return {
        "brands": [_brand_to_jsx(b) for b in brands],
        "products": [_product_to_jsx(p) for p in products],
        "prewrittenTexts": [_text_to_jsx(t) for t in texts],
        "composedBlocks": [_composed_to_jsx(c) for c in composed],
        "standaloneBlocks": [_standalone_to_jsx(s) for s in standalone],
        "templates": [_template_to_jsx(t) for t in templates],
        "uploadedImages": [_asset_to_jsx(a) for a in assets],
        "users": [synthetic_user],
        "openaiKey": openai_key,
        "activityLog": [],
        "_meta": {
            "source": "crm",
            "serverTime": datetime.now(UTC).isoformat(),
            "currentUserId": current_user.id,
        },
    }


# ───────────────────────────────────────────────────────────────────
# State PUT — accepts the embed's monolithic blob and persists the
# parts each role is allowed to touch.
# ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _upsert_brand(session: Session, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    existing = session.get(ComposerBrand, raw["id"])
    i18n_blob: dict[str, Any] = {}
    if isinstance(raw.get("i18n"), dict):
        i18n_blob.update(raw["i18n"])
    # url / urlLabel live in i18n_json so the schema doesn't need to
    # know about per-lang URLs.
    if "url" in raw:
        i18n_blob["url"] = raw["url"]
    if "urlLabel" in raw:
        i18n_blob["urlLabel"] = raw["urlLabel"]
    fields = {
        "type": raw.get("type") or "brand",
        "label": raw.get("label") or raw["id"],
        "logo": raw.get("logo"),
        "logo_text": raw.get("logoText"),
        "color": raw.get("color") or "#000",
        "divider": raw.get("divider"),
        "logo_height": str(raw["logoHeight"]) if raw.get("logoHeight") is not None else None,
        "logo_max_width": str(raw["logoMaxWidth"]) if raw.get("logoMaxWidth") is not None else None,
        "visible": bool(raw.get("visible", True)),
        "sort_order": int(raw.get("sortOrder", 0) or 0),
        "i18n_json": json.dumps(i18n_blob, default=str, ensure_ascii=False),
    }
    now = _now()
    if existing is None:
        session.add(
            ComposerBrand(id=raw["id"], created_at=now, updated_at=now, **fields)
        )
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = now


def _upsert_product(session: Session, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("brand"):
        return
    existing = session.get(ComposerProduct, raw["id"])
    fields = {
        "brand_id": raw["brand"],
        "name": raw.get("name") or raw["id"],
        "badge": raw.get("badge"),
        "badge_bg": raw.get("badgeBg"),
        "badge_color": raw.get("badgeColor"),
        "img": raw.get("img") or "",
        "description": raw.get("desc"),
        "area": raw.get("area"),
        "alt": raw.get("alt"),
        "feat1": raw.get("feat1"),
        "feat2": raw.get("feat2"),
        "price": raw.get("price"),
        "link": raw.get("link"),
        "accent": raw.get("accent"),
        "gradient": raw.get("gradient"),
        "visible": bool(raw.get("visible", True)),
        "sort_order": int(raw.get("sortOrder", 0) or 0),
        "tags": json.dumps(raw.get("tags", []), default=str, ensure_ascii=False),
        "i18n_json": json.dumps(raw.get("i18n", {}), default=str, ensure_ascii=False),
    }
    now = _now()
    if existing is None:
        session.add(
            ComposerProduct(id=raw["id"], created_at=now, updated_at=now, **fields)
        )
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = now


def _upsert_text(session: Session, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    existing = session.get(ComposerPrewrittenText, raw["id"])
    fields = {
        "name": raw.get("name") or raw["id"],
        "icon": raw.get("icon"),
        "brand_id": raw.get("brand"),
        "text": raw.get("text") or "",
        "visible": bool(raw.get("visible", True)),
        "sort_order": int(raw.get("sortOrder", 0) or 0),
        "i18n_json": json.dumps(raw.get("i18n", {}), default=str, ensure_ascii=False),
    }
    now = _now()
    if existing is None:
        session.add(
            ComposerPrewrittenText(
                id=raw["id"], created_at=now, updated_at=now, **fields
            )
        )
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = now


def _upsert_composed(session: Session, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    existing = session.get(ComposerComposedBlock, raw["id"])
    config_blob: dict[str, Any] = {}
    if isinstance(raw.get("innerBlocks"), list):
        config_blob["innerBlocks"] = raw["innerBlocks"]
    if isinstance(raw.get("compositorBlocks"), list):
        config_blob["compositorBlocks"] = raw["compositorBlocks"]
    fields = {
        "title": raw.get("title") or raw["id"],
        "description": raw.get("desc"),
        "price_range": raw.get("priceRange"),
        "color_tag": raw.get("colorTag"),
        "intro_text": raw.get("introText"),
        "brand_strip": raw.get("brandStrip"),
        "block_type": raw.get("blockType") or "product_single",
        "products": json.dumps(raw.get("products", []), default=str, ensure_ascii=False),
        "include_hero": bool(raw.get("includeHero", False)),
        "include_steps": bool(raw.get("includeSteps", False)),
        "visible": bool(raw.get("visible", True)),
        "sort_order": int(raw.get("sortOrder", 0) or 0),
        "i18n_json": json.dumps(raw.get("i18n", {}), default=str, ensure_ascii=False),
        "config_json": json.dumps(config_blob, default=str, ensure_ascii=False),
    }
    now = _now()
    if existing is None:
        session.add(
            ComposerComposedBlock(
                id=raw["id"], created_at=now, updated_at=now, **fields
            )
        )
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = now


def _upsert_standalone(session: Session, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    existing = session.get(ComposerStandaloneBlock, raw["id"])
    fields = {
        "title": raw.get("title") or raw["id"],
        "description": raw.get("desc"),
        "icon": raw.get("icon"),
        "icon_bg": raw.get("iconBg"),
        "brand_id": raw.get("brand"),
        "section": raw.get("section"),
        "block_type": raw.get("blockType") or raw.get("type") or "cta",
        "config_json": json.dumps(raw.get("config", {}), default=str, ensure_ascii=False),
        "visible": bool(raw.get("visible", True)),
        "sort_order": int(raw.get("sortOrder", 0) or 0),
        "i18n_json": json.dumps(raw.get("i18n", {}), default=str, ensure_ascii=False),
    }
    now = _now()
    if existing is None:
        session.add(
            ComposerStandaloneBlock(
                id=raw["id"], created_at=now, updated_at=now, **fields
            )
        )
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = now


def _upsert_template(
    session: Session, raw: dict[str, Any], current_user: User
) -> None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    existing = session.get(ComposerTemplate, raw["id"])
    is_admin = current_user.role == UserRole.ADMIN
    incoming_is_global = bool(raw.get("isGlobal", False))
    incoming_owner = raw.get("ownerUserId")

    if existing is not None:
        # Owner-or-admin to edit existing.
        if (
            not is_admin
            and existing.owner_user_id != current_user.id
            and existing.is_global
        ):
            # Non-admin cannot edit a global template they don't own.
            return
        existing.name = raw.get("name") or existing.name
        existing.description = raw.get("desc", existing.description)
        existing.color_class = raw.get("colorClass", existing.color_class)
        existing.brand_id = raw.get("brand", existing.brand_id)
        if "blocks" in raw:
            existing.blocks_json = json.dumps(
                raw.get("blocks") or [], default=str, ensure_ascii=False
            )
        if "compositorBlocks" in raw:
            existing.compositor_blocks_json = (
                json.dumps(
                    raw.get("compositorBlocks"),
                    default=str,
                    ensure_ascii=False,
                )
                if raw.get("compositorBlocks") is not None
                else None
            )
        if is_admin:
            existing.is_global = incoming_is_global
        existing.visible = bool(raw.get("visible", existing.visible))
        existing.updated_at = _now()
        return

    now = _now()
    new_tpl = ComposerTemplate(
        id=raw["id"],
        name=raw.get("name") or raw["id"],
        description=raw.get("desc"),
        color_class=raw.get("colorClass"),
        brand_id=raw.get("brand"),
        blocks_json=json.dumps(raw.get("blocks", []), default=str, ensure_ascii=False),
        compositor_blocks_json=(
            json.dumps(raw["compositorBlocks"], default=str, ensure_ascii=False)
            if raw.get("compositorBlocks") is not None
            else None
        ),
        visible=bool(raw.get("visible", True)),
        is_global=incoming_is_global if is_admin else False,
        owner_user_id=incoming_owner if is_admin else current_user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(new_tpl)


@router.put("/state")
def put_composer_state(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Persist a state mutation from the embed.

    Role gates:
      - admin / manager: catalog (brands, products, texts, composed,
        standalone).
      - any (non-viewer): own templates. Admin gates global templates.
      - admin: openaiKey (encrypted).
    """
    assert_composer_access(current_user)
    can_write_catalog = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)

    if can_write_catalog:
        for b in payload.get("brands", []) or []:
            _upsert_brand(session, b)
        for p in payload.get("products", []) or []:
            _upsert_product(session, p)
        for t in payload.get("prewrittenTexts", []) or []:
            _upsert_text(session, t)
        for c in payload.get("composedBlocks", []) or []:
            _upsert_composed(session, c)
        for s in payload.get("standaloneBlocks", []) or []:
            _upsert_standalone(session, s)

    for tpl in payload.get("templates", []) or []:
        _upsert_template(session, tpl, current_user)

    if current_user.role == UserRole.ADMIN:
        key = (payload.get("openaiKey") or "").strip()
        if key:
            row = session.get(ComposerSettings, 1)
            now = _now()
            if row is None:
                row = ComposerSettings(
                    id=1,
                    openai_api_key_encrypted=encrypt(key),
                    ai_styles_json="{}",
                    agent_system_prompt=None,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.openai_api_key_encrypted = encrypt(key)
                row.updated_at = now

    session.add(
        ComposerActivityLog(
            user_id=current_user.id,
            action="state.put",
            entity_type="composer_state",
            entity_id=None,
            metadata_json=json.dumps(
                {
                    "templates": len(payload.get("templates", []) or []),
                    "brands": len(payload.get("brands", []) or []),
                    "products": len(payload.get("products", []) or []),
                },
                default=str,
                ensure_ascii=False,
            ),
            created_at=_now(),
        )
    )
    session.commit()
    return {"status": "ok"}


# ───────────────────────────────────────────────────────────────────
# Backups — minimal stubs for now.
# ───────────────────────────────────────────────────────────────────
# The embed calls these to offer "Restaurar versión anterior". A real
# implementation would snapshot the full state blob; persisting it
# requires either a new table or an asset-blob convention. Punted to
# the next PR. The stubs keep the embed from showing errors when a
# user opens the backup menu.


@router.post("/backups", status_code=201)
def create_backup(
    payload: dict[str, Any],
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    assert_composer_access(current_user)
    # TODO(composer): persist a snapshot once a backups table or
    # filesystem layout lands. Accept + acknowledge so the embed's
    # UI continues to flow.
    reason = (payload or {}).get("reason", "")
    logger.info("composer.backup.requested user=%s reason=%s", current_user.id, reason)
    return {"id": f"stub-{int(datetime.now(UTC).timestamp())}", "status": "stub"}


@router.get("/backups")
def list_backups(
    current_user: User = Depends(require_user),
) -> list[dict[str, Any]]:
    assert_composer_access(current_user)
    return []


@router.get("/backups/{backup_id}")
def get_backup(
    backup_id: str,  # noqa: ARG001
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    assert_composer_access(current_user)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Backups todavía no se persisten — Fase 4.",
    )


@router.delete("/backups")
def prune_backups(
    keep: int = 50,  # noqa: ARG001
    current_user: User = Depends(require_user),
) -> dict[str, str]:
    assert_composer_access(current_user)
    return {"status": "ok"}
