"""Service-layer helpers for the composer routers.

- `apply_hidden_items_filter` strips items the current user
  hid from the catalogue payload.
- `record_revision` writes a template snapshot and FIFO-trims
  to the most recent 20.
- `record_activity` appends one row to `composer_activity_log`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.composer.models import (
    ComposerActivityLog,
    ComposerTemplate,
    ComposerTemplateRevision,
    ComposerUserHiddenItem,
)

MAX_REVISIONS_PER_TEMPLATE = 20


def hidden_items_for_user(session: Session, user_id: str) -> dict[str, set[str]]:
    """Return `{collection: {item_id, ...}}` for the given user."""
    rows = session.scalars(
        select(ComposerUserHiddenItem).where(
            ComposerUserHiddenItem.user_id == user_id
        )
    )
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row.collection, set()).add(row.item_id)
    return out


def record_revision(
    session: Session,
    *,
    template: ComposerTemplate,
    actor_user_id: str | None,
) -> ComposerTemplateRevision:
    """Append a snapshot of `template` and FIFO-trim the oldest
    revisions if we go past `MAX_REVISIONS_PER_TEMPLATE`."""
    snapshot = {
        "name": template.name,
        "description": template.description,
        "color_class": template.color_class,
        "brand_id": template.brand_id,
        "blocks": _safe_json(template.blocks_json, []),
        "compositor_blocks": _safe_json(template.compositor_blocks_json, None),
        "visible": template.visible,
        "is_global": template.is_global,
    }
    revision = ComposerTemplateRevision(
        template_id=template.id,
        snapshot_json=json.dumps(snapshot, default=str, ensure_ascii=False),
        created_by_user_id=actor_user_id,
        created_at=datetime.now(UTC),
    )
    session.add(revision)
    session.flush()

    existing = list(
        session.scalars(
            select(ComposerTemplateRevision)
            .where(ComposerTemplateRevision.template_id == template.id)
            .order_by(ComposerTemplateRevision.created_at.desc())
        )
    )
    for stale in existing[MAX_REVISIONS_PER_TEMPLATE:]:
        session.delete(stale)
    session.flush()
    return revision


def record_activity(
    session: Session,
    *,
    user_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        ComposerActivityLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata_json=json.dumps(
                metadata or {}, default=str, ensure_ascii=False
            ),
            created_at=datetime.now(UTC),
        )
    )


def _safe_json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback
