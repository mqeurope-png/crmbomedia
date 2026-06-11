#!/usr/bin/env python3
"""Seed the composer catalog tables from `composer_seed_data.json`.

Idempotent: upserts by id. Existing rows are NOT overwritten
unless `--force` is passed. Drafts, revisions, assets and
activity stay empty — those are runtime artefacts.

Usage:
    docker compose exec api python scripts/seed_composer_catalog.py
    docker compose exec api python scripts/seed_composer_catalog.py --force
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.composer.models import (  # noqa: E402
    ComposerBrand,
    ComposerComposedBlock,
    ComposerPrewrittenText,
    ComposerProduct,
    ComposerStandaloneBlock,
    ComposerTemplate,
)
from app.db.session import get_engine  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("seed_composer_catalog")


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_json(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _upsert(
    session: Session,
    model: type,
    pk_value: str,
    fields: dict,
    *,
    force: bool,
) -> str:
    """Insert or update one row by primary key. Returns the action
    taken: `created`, `updated` or `skipped`."""
    existing = session.get(model, pk_value)
    now = _now()
    if existing is None:
        if "created_at" in model.__table__.columns:
            fields.setdefault("created_at", now)
        if "updated_at" in model.__table__.columns:
            fields.setdefault("updated_at", now)
        session.add(model(**fields))
        return "created"
    if not force:
        return "skipped"
    for key, value in fields.items():
        setattr(existing, key, value)
    if "updated_at" in model.__table__.columns:
        existing.updated_at = now
    return "updated"


def seed_brands(session: Session, items: list[dict], *, force: bool) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "type": raw.get("type", "brand"),
            "label": raw["label"],
            "logo": raw.get("logo"),
            "logo_text": raw.get("logo_text"),
            "color": raw.get("color", "#000"),
            "divider": raw.get("divider"),
            "logo_height": raw.get("logo_height"),
            "logo_max_width": raw.get("logo_max_width"),
            "visible": raw.get("visible", True),
            "sort_order": raw.get("sort_order", 0),
            "i18n_json": _dump_json(raw.get("i18n_json", {})),
        }
        action = _upsert(session, ComposerBrand, raw["id"], fields, force=force)
        counter[action] += 1
    return counter


def seed_products(session: Session, items: list[dict], *, force: bool) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "brand_id": raw["brand_id"],
            "name": raw["name"],
            "badge": raw.get("badge"),
            "badge_bg": raw.get("badge_bg"),
            "badge_color": raw.get("badge_color"),
            "img": raw["img"],
            "description": raw.get("description"),
            "area": raw.get("area"),
            "alt": raw.get("alt"),
            "feat1": raw.get("feat1"),
            "feat2": raw.get("feat2"),
            "price": raw.get("price"),
            "link": raw.get("link"),
            "accent": raw.get("accent"),
            "gradient": raw.get("gradient"),
            "visible": raw.get("visible", True),
            "sort_order": raw.get("sort_order", 0),
            "tags": _dump_json(raw.get("tags", [])),
            "i18n_json": _dump_json(raw.get("i18n_json", {})),
        }
        action = _upsert(session, ComposerProduct, raw["id"], fields, force=force)
        counter[action] += 1
    return counter


def seed_prewritten_texts(
    session: Session, items: list[dict], *, force: bool
) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "name": raw["name"],
            "icon": raw.get("icon"),
            "brand_id": raw.get("brand_id"),
            "text": raw["text"],
            "visible": raw.get("visible", True),
            "sort_order": raw.get("sort_order", 0),
            "i18n_json": _dump_json(raw.get("i18n_json", {})),
        }
        action = _upsert(
            session, ComposerPrewrittenText, raw["id"], fields, force=force
        )
        counter[action] += 1
    return counter


def seed_composed_blocks(
    session: Session, items: list[dict], *, force: bool
) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "title": raw["title"],
            "description": raw.get("description"),
            "price_range": raw.get("price_range"),
            "color_tag": raw.get("color_tag"),
            "intro_text": raw.get("intro_text"),
            "brand_strip": raw.get("brand_strip"),
            "block_type": raw["block_type"],
            "products": _dump_json(raw.get("products", [])),
            "include_hero": raw.get("include_hero", False),
            "include_steps": raw.get("include_steps", False),
            "visible": raw.get("visible", True),
            "sort_order": raw.get("sort_order", 0),
            "i18n_json": _dump_json(raw.get("i18n_json", {})),
            "config_json": _dump_json(raw.get("config_json", {})),
        }
        action = _upsert(
            session, ComposerComposedBlock, raw["id"], fields, force=force
        )
        counter[action] += 1
    return counter


def seed_standalone_blocks(
    session: Session, items: list[dict], *, force: bool
) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "title": raw["title"],
            "description": raw.get("description"),
            "icon": raw.get("icon"),
            "icon_bg": raw.get("icon_bg"),
            "brand_id": raw.get("brand_id"),
            "section": raw.get("section"),
            "block_type": raw["block_type"],
            "config_json": _dump_json(raw.get("config_json", {})),
            "visible": raw.get("visible", True),
            "sort_order": raw.get("sort_order", 0),
            "i18n_json": _dump_json(raw.get("i18n_json", {})),
        }
        action = _upsert(
            session, ComposerStandaloneBlock, raw["id"], fields, force=force
        )
        counter[action] += 1
    return counter


def seed_templates(
    session: Session, items: list[dict], *, force: bool
) -> dict[str, int]:
    counter = {"created": 0, "updated": 0, "skipped": 0}
    for raw in items:
        fields = {
            "id": raw["id"],
            "name": raw["name"],
            "description": raw.get("description"),
            "color_class": raw.get("color_class"),
            "brand_id": raw.get("brand_id"),
            "blocks_json": _dump_json(raw.get("blocks_json", [])),
            "compositor_blocks_json": (
                _dump_json(raw["compositor_blocks_json"])
                if raw.get("compositor_blocks_json") is not None
                else None
            ),
            "visible": raw.get("visible", True),
            "is_global": raw.get("is_global", False),
            "owner_user_id": raw.get("owner_user_id"),
        }
        action = _upsert(session, ComposerTemplate, raw["id"], fields, force=force)
        counter[action] += 1
    return counter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing rows instead of skipping them.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=HERE / "composer_seed_data.json",
        help="Path to the seed JSON.",
    )
    args = parser.parse_args()

    data = json.loads(args.source.read_text(encoding="utf-8"))
    with Session(get_engine()) as session:
        brand_stats = seed_brands(session, data.get("brands", []), force=args.force)
        product_stats = seed_products(
            session, data.get("products", []), force=args.force
        )
        text_stats = seed_prewritten_texts(
            session, data.get("prewritten_texts", []), force=args.force
        )
        composed_stats = seed_composed_blocks(
            session, data.get("composed_blocks", []), force=args.force
        )
        standalone_stats = seed_standalone_blocks(
            session, data.get("standalone_blocks", []), force=args.force
        )
        template_stats = seed_templates(
            session, data.get("templates", []), force=args.force
        )
        session.commit()
    logger.info(
        "composer.seed.done brands=%s products=%s texts=%s composed=%s "
        "standalone=%s templates=%s force=%s",
        brand_stats,
        product_stats,
        text_stats,
        composed_stats,
        standalone_stats,
        template_stats,
        args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
