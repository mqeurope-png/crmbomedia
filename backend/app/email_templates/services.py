"""Helpers for the v2.2 email templates layer."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EmailTemplateFolder

# Max folder depth enforced at the API layer (BD is unconstrained so
# legacy imports don't fail on existing trees).
MAX_FOLDER_DEPTH = 3

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_text_from_html(html: str | None) -> str | None:
    """Strip tags + collapse whitespace. Used for the multipart
    `body_text` column on every template write. Falls back to the
    same input when the parser surface gets weird; the multipart
    text is informational and a noisy fallback beats a silent
    nullable."""
    if not html:
        return None
    body = _TAG_RE.sub(" ", html)
    body = (
        body.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    body = _WS_RE.sub(" ", body).strip()
    return body or None


def folder_depth(
    session: Session, folder_id: str | None, depth_seen: int = 0
) -> int:
    """Walk up the parent chain to find the depth of a folder."""
    if folder_id is None:
        return depth_seen
    if depth_seen > MAX_FOLDER_DEPTH * 2:
        # Guard against accidental cycles — anything past 2x the
        # configured max is broken regardless.
        return depth_seen
    folder = session.get(EmailTemplateFolder, folder_id)
    if folder is None:
        return depth_seen
    return folder_depth(session, folder.parent_folder_id, depth_seen + 1)


def descendants(
    session: Session, parent_id: str | None
) -> list[EmailTemplateFolder]:
    """Direct children of `parent_id` (NOT recursive)."""
    return list(
        session.scalars(
            select(EmailTemplateFolder)
            .where(EmailTemplateFolder.parent_folder_id == parent_id)
            .order_by(
                EmailTemplateFolder.sort_order, EmailTemplateFolder.name
            )
        )
    )
