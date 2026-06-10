"""Anthropic Claude client for AI-assisted pipeline generation.

The route layer calls `generate_pipeline_proposal(description)`; the
function returns a parsed JSON dict shaped like
`pipeline_templates.build_pipeline_payload`'s output so the same
downstream code path persists template-derived and AI-derived
pipelines.

Failure modes are surfaced through `LLMError` subclasses so the route
can map them to HTTP responses without leaking the API key, raw
provider exceptions, or model identifiers to the client.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Eres un asistente experto en CRMs y procesos comerciales. "
    "Generas estructuras de pipelines (workflows) para gestionar contactos "
    "en un CRM. Tu salida es SIEMPRE un JSON válido sin texto adicional.\n\n"
    "Formato esperado:\n"
    "{\n"
    "  \"name\": \"string, nombre breve del pipeline\",\n"
    "  \"description\": \"string, 1-2 frases explicando para qué sirve\",\n"
    "  \"color\": \"string hex (#RRGGBB) de paleta Tailwind 500\",\n"
    "  \"stages\": [\n"
    "    {\n"
    "      \"name\": \"string corto, máx 30 caracteres\",\n"
    "      \"description\": \"string opcional, 1 frase explicando la etapa\",\n"
    "      \"target_days\": int o null,\n"
    "      \"is_won\": bool (true SOLO en la etapa final de éxito),\n"
    "      \"is_lost\": bool (true SOLO en la etapa final de fracaso)\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Reglas:\n"
    "- Entre 4 y 8 etapas (no menos, no más).\n"
    "- Las etapas siguen un orden lógico de avance.\n"
    "- La penúltima etapa debe ser is_won=true (éxito).\n"
    "- La última debe ser is_lost=true (fracaso/abandono).\n"
    "- target_days realistas para el negocio descrito.\n"
    "- No devuelvas explicaciones ni markdown, solo JSON.\n"
    "- Lenguaje español."
)

MAX_DESCRIPTION_CHARS = 2000
MAX_TOKENS_OUTPUT = 2000
MIN_STAGES = 4
MAX_STAGES = 8

#: In-memory token bucket per (user_id) — one tuple of timestamps.
#: Resets at the configured window. Fine for a single-process dev
#: setup; a multi-worker production needs Redis-backed limiting which
#: is straightforward to swap in via the same `_rate_limit_check`
#: signature.
_RATE_LIMIT_WINDOW_SECONDS = 3600
_RATE_LIMIT_MAX_CALLS = 5
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


class LLMError(Exception):
    """Base for every failure mode the route surfaces back to the UI."""


class LLMNotConfiguredError(LLMError):
    """`ANTHROPIC_API_KEY` is unset — the endpoint must 503."""


class LLMRateLimitError(LLMError):
    """Either the local per-user limit OR Anthropic's own 429."""


class LLMUpstreamError(LLMError):
    """Network, 5xx, malformed JSON. The client sees a generic message."""


def _rate_limit_check(user_id: str) -> None:
    """Sliding window: drop any timestamp older than the window, then
    accept or reject the new call. Raises `LLMRateLimitError` so the
    caller can map it to HTTP 429 with the right Retry-After hint."""
    now = time.monotonic()
    bucket = _rate_buckets[user_id]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX_CALLS:
        raise LLMRateLimitError(
            f"Rate limit reached: {_RATE_LIMIT_MAX_CALLS} generations / "
            f"{_RATE_LIMIT_WINDOW_SECONDS // 60} min per user."
        )
    bucket.append(now)


def reset_rate_limit(user_id: str | None = None) -> None:
    """Test-only helper. Pytest re-creates the in-memory buckets between
    tests by importing fresh modules; this gives integration tests an
    explicit knob for the AI rate-limit case."""
    if user_id is None:
        _rate_buckets.clear()
    else:
        _rate_buckets.pop(user_id, None)


def generate_pipeline_proposal(
    description: str, *, user_id: str
) -> dict[str, Any]:
    """Synchronous call into Anthropic Claude. Returns a dict with
    keys `name`, `description`, `color`, `stages: list[dict]` —
    same shape as `pipeline_templates.build_pipeline_payload`.

    `user_id` is required because rate-limiting is per-user; the
    audit log entry uses it too.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise LLMNotConfiguredError(
            "AI features are disabled. Set ANTHROPIC_API_KEY to enable."
        )

    cleaned = description.strip()
    if not cleaned:
        raise LLMError("Description is empty")
    if len(cleaned) > MAX_DESCRIPTION_CHARS:
        raise LLMError(
            f"Description too long; max {MAX_DESCRIPTION_CHARS} characters."
        )

    _rate_limit_check(user_id)

    raw = _invoke_claude(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=f"Genera un pipeline para: {cleaned}",
    )
    return _normalize_proposal(raw)


def _invoke_claude(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Wrapper isolated so tests can monkeypatch it without touching
    the high-level `generate_pipeline_proposal` flow."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - guarded by config
        raise LLMNotConfiguredError(
            "anthropic package not installed"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS_OUTPUT,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except getattr(anthropic, "RateLimitError", Exception) as exc:
        raise LLMRateLimitError("Upstream rate limit") from exc
    except getattr(anthropic, "APIError", Exception) as exc:
        logger.warning("Anthropic API failure: %s", exc)
        raise LLMUpstreamError("Provider error") from exc
    except Exception as exc:  # noqa: BLE001 - last-line catch-all
        logger.exception("Unexpected LLM failure")
        raise LLMUpstreamError("Unexpected provider failure") from exc

    if not message.content:
        raise LLMUpstreamError("Empty response from provider")
    chunk = message.content[0]
    text = getattr(chunk, "text", None)
    if not text:
        raise LLMUpstreamError("Provider returned non-text content")
    return text


def _normalize_proposal(raw_text: str) -> dict[str, Any]:
    """Parse + validate the JSON Claude returned. Models occasionally
    wrap the JSON in a markdown ```json``` fence even when the system
    prompt forbids it — strip those before parsing."""
    cleaned = raw_text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        raise LLMUpstreamError("Provider returned non-JSON content") from exc
    if not isinstance(data, dict):
        raise LLMUpstreamError("Provider returned a non-object payload")

    name = (data.get("name") or "").strip()
    if not name:
        raise LLMUpstreamError("Proposal missing `name`")
    stages_raw = data.get("stages")
    if not isinstance(stages_raw, list) or not (
        MIN_STAGES <= len(stages_raw) <= MAX_STAGES
    ):
        raise LLMUpstreamError(
            f"Proposal needs between {MIN_STAGES} and {MAX_STAGES} stages"
        )

    stages_norm: list[dict[str, Any]] = []
    for index, stage in enumerate(stages_raw):
        if not isinstance(stage, dict):
            raise LLMUpstreamError(f"Stage {index} is not an object")
        stage_name = (stage.get("name") or "").strip()
        if not stage_name:
            raise LLMUpstreamError(f"Stage {index} missing name")
        target = stage.get("target_days")
        if target is not None:
            try:
                target = int(target)
                if target < 0:
                    target = None
            except (TypeError, ValueError):
                target = None
        color = stage.get("color")
        if isinstance(color, str):
            color = color.strip().lower() or None
        stages_norm.append(
            {
                "name": stage_name[:30],
                "description": (stage.get("description") or "").strip() or None,
                "color": color,
                "is_won": bool(stage.get("is_won", False)),
                "is_lost": bool(stage.get("is_lost", False)),
                "target_days": target,
                "position": index,
            }
        )

    return {
        "name": name[:100],
        "description": (data.get("description") or "").strip() or None,
        "color": (data.get("color") or "").strip().lower() or None,
        "stages": stages_norm,
    }
