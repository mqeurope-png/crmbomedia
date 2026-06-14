"""Helpers for the v2.2 email templates layer."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings

from .models import EmailTemplateFolder

# Max folder depth enforced at the API layer (BD is unconstrained so
# legacy imports don't fail on existing trees).
MAX_FOLDER_DEPTH = 3

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Strip `<style>`, `<script>`, and `<head>` blocks INCLUDING their
# contents. Without these, any TinyMCE / Outlook boilerplate that
# ships CSS reset blocks inline (very common when pasting templates)
# bleeds into the plain-text fallback as raw CSS source.
_BLOCK_TAGS_RE = re.compile(
    r"<(?P<tag>style|script|head)\b[^>]*>[\s\S]*?</(?P=tag)>",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")

_log = logging.getLogger(__name__)


def extract_text_from_html(html: str | None) -> str | None:
    """Strip tags + collapse whitespace. Used for the multipart
    `body_text` column on every template write AND the inbox snippet
    when a TinyMCE-authored body ships without a plaintext companion.
    Block-tag stripping has to land BEFORE the simple tag pass —
    otherwise the CSS / JS contents leak into the result as raw text."""
    if not html:
        return None
    body = _COMMENT_RE.sub(" ", html)
    body = _BLOCK_TAGS_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
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


# ───────────────────────────────────────────────────────────────────
# composer.bomedia.net (Supabase) read-only proxy
# ───────────────────────────────────────────────────────────────────

COMPOSER_OPEN_URL_BASE = "https://composer.bomedia.net/?template="
_COMPOSER_CACHE_TTL_SECONDS = 5 * 60


@dataclass(slots=True)
class ComposerSourceItem:
    id: str
    name: str
    brand: str | None
    blocks_count: int
    open_url: str


@dataclass(slots=True)
class _CacheEntry:
    items: list[ComposerSourceItem]
    error: str | None
    expires_at: float


_composer_cache: _CacheEntry | None = None
_composer_cache_lock = threading.Lock()


def _normalize_composer_template(raw: dict[str, Any]) -> ComposerSourceItem | None:
    tid = raw.get("id")
    if not tid:
        return None
    blocks = raw.get("blocks") or raw.get("compositorBlocks") or []
    return ComposerSourceItem(
        id=str(tid),
        name=str(raw.get("name") or "Sin nombre"),
        brand=raw.get("brand") if isinstance(raw.get("brand"), str) else None,
        blocks_count=len(blocks) if isinstance(blocks, list) else 0,
        open_url=f"{COMPOSER_OPEN_URL_BASE}{tid}",
    )


def fetch_composer_templates() -> tuple[list[ComposerSourceItem], str | None]:
    """Read-only mirror of composer.bomedia.net's Supabase row.

    Returns `(items, error)`. The error string is set only when Supabase
    refused us — the picker tab renders it as a notice and the rest of
    the picker keeps working. Cached for 5 minutes so a rapidly-opened
    picker doesn't hammer Supabase.

    Side effects: only a single outbound httpx.get() per cache miss.
    No DB writes, no audit log entry — the CRM treats Composer as a
    third-party read source.
    """
    global _composer_cache

    settings = get_settings()
    if not settings.supabase_composer_configured:
        return ([], "Composer no está configurado en este servidor.")

    now = time.monotonic()
    with _composer_cache_lock:
        cached = _composer_cache
        if cached is not None and cached.expires_at > now:
            return (list(cached.items), cached.error)

    url = (
        f"{settings.supabase_composer_url.rstrip('/')}"
        "/rest/v1/composer_data?id=eq.main&select=*"
    )
    headers = {
        "apikey": settings.supabase_composer_key,
        "Authorization": f"Bearer {settings.supabase_composer_key}",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        _log.warning("supabase composer fetch failed: %s", exc)
        error = "No se pudo conectar con composer.bomedia.net."
        with _composer_cache_lock:
            # Cache the failure too so we don't retry on every click,
            # but with a shorter TTL so the picker recovers within
            # 30 seconds once the source is back.
            _composer_cache = _CacheEntry(
                items=[], error=error, expires_at=now + 30.0
            )
        return ([], error)

    row = rows[0] if rows else {}
    # composer.bomedia.net stores the whole app state under a single
    # `data` JSON column on the `composer_data` row. Older snapshots
    # kept the state at the row root, so we fall back to that shape
    # if `data` is missing — keeps the proxy working across any
    # incremental Supabase migration on the Composer side.
    state = row.get("data") if isinstance(row.get("data"), dict) else row
    raw_templates = state.get("templates") or []
    _log.info(
        "composer-source proxy: fetched %d template(s)",
        len(raw_templates) if isinstance(raw_templates, list) else 0,
    )
    items: list[ComposerSourceItem] = []
    for raw in raw_templates:
        if not isinstance(raw, dict):
            continue
        normalised = _normalize_composer_template(raw)
        if normalised is not None:
            items.append(normalised)

    with _composer_cache_lock:
        _composer_cache = _CacheEntry(
            items=list(items),
            error=None,
            expires_at=now + _COMPOSER_CACHE_TTL_SECONDS,
        )
    return (items, None)


def reset_composer_cache() -> None:
    """Drop the proxy cache. Test helper; not exposed as an endpoint."""
    global _composer_cache
    with _composer_cache_lock:
        _composer_cache = None


# ───────────────────────────────────────────────────────────────────
# Merge variables — {nombre} / {empresa} / {email}
# ───────────────────────────────────────────────────────────────────

_MERGE_TOKENS = ("{nombre}", "{empresa}", "{email}")


def has_merge_tokens(text: str | None) -> bool:
    """True when the body still carries a placeholder. The send modal
    uses this for the "Se reemplazarán al enviar" footer badge."""
    if not text:
        return False
    return any(tok in text for tok in _MERGE_TOKENS)


def replace_merge_vars(text: str | None, contact: Any | None) -> str | None:
    """Substitute `{nombre}` / `{empresa}` / `{email}` with the values
    on `contact`. Returns the original text unchanged when there is no
    contact (e.g. composing from /emails). A missing field substitutes
    an empty string so an operator never ships an email with a literal
    `{nombre}` after the substitution pass."""
    if text is None or contact is None:
        return text
    first_name = getattr(contact, "first_name", "") or ""
    email = getattr(contact, "email", "") or ""
    company = getattr(contact, "company", None)
    company_name = getattr(company, "name", "") if company is not None else ""
    return (
        text.replace("{nombre}", first_name)
        .replace("{empresa}", company_name)
        .replace("{email}", email)
    )
