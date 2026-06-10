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


def _rate_limit_check(
    user_id: str, *, max_calls: int = _RATE_LIMIT_MAX_CALLS
) -> None:
    """Sliding window: drop any timestamp older than the window, then
    accept or reject the new call. `max_calls` is per-namespace —
    pipelines and segments live in different buckets via the
    `namespace:user_id` key the caller passes."""
    now = time.monotonic()
    bucket = _rate_buckets[user_id]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= max_calls:
        raise LLMRateLimitError(
            f"Rate limit reached: {max_calls} calls / "
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


# ---------------------------------------------------------------------------
# Segment AI helpers (Sprint P.3)
# ---------------------------------------------------------------------------


SEGMENT_GENERATE_SYSTEM_PROMPT = (
    "Eres un asistente experto en segmentación de contactos en un CRM. "
    "Tu trabajo es traducir descripciones en lenguaje natural a árboles "
    "de reglas booleanas estrictas en formato JSON.\n\n"
    "Solo puedes usar los campos y comparadores autorizados. "
    "La whitelist completa se incluye al final.\n\n"
    "Estructura del árbol:\n"
    "{\n"
    "  \"operator\": \"AND\" | \"OR\" | \"NOT\",\n"
    "  \"children\": [\n"
    "    { \"type\": \"rule\", \"field\": \"...\", \"comparator\": \"...\", \"value\": ... },\n"
    "    { \"operator\": \"OR\", \"children\": [ ... ] }\n"
    "  ]\n"
    "}\n\n"
    "Ejemplo válido:\n"
    "{\"operator\":\"AND\",\"children\":[\n"
    "  {\"type\":\"rule\",\"field\":\"lead_score\","
    "\"comparator\":\"gte\",\"value\":50},\n"
    "  {\"type\":\"rule\",\"field\":\"marketing_consent\","
    "\"comparator\":\"eq\",\"value\":\"granted\"}\n"
    "]}\n\n"
    "Reglas:\n"
    "- SIEMPRE devuelves JSON válido, sin texto adicional, sin markdown.\n"
    "- Si la descripción es ambigua o requiere campos fuera de la "
    "whitelist, devuelve `{\"error\": \"explicación breve\"}`.\n"
    "- Idioma: español. Lenguaje natural en `error`, no jerga técnica.\n\n"
    "Campos disponibles:\n{fields_table}"
)


SEGMENT_EXPLAIN_SYSTEM_PROMPT = (
    "Eres un asistente que traduce árboles de reglas booleanas de un "
    "CRM a explicaciones legibles para usuarios no técnicos.\n\n"
    "Recibirás un árbol JSON con reglas combinadas. Devuelves un "
    "párrafo breve (2-4 frases) en español describiendo a quién "
    "incluye el segmento.\n\n"
    "Usa lenguaje natural, NO nombres técnicos de campos. Ejemplo: "
    "en vez de \"marketing_consent = granted\" di \"que han dado su "
    "consentimiento de marketing\". No menciones JSON ni comparadores."
)


def _build_fields_table() -> str:
    """Render the whitelist as a markdown-ish table that the system
    prompt embeds. Centralising it here means a new field added to
    the engine is automatically surfaced to the LLM."""
    from app.services.segments.fields import FIELD_SPECS  # noqa: PLC0415

    lines: list[str] = []
    for spec in FIELD_SPECS.values():
        comp = ", ".join(spec.comparators)
        enums = (
            f" | enum: {', '.join(spec.enum_values)}"
            if spec.enum_values
            else ""
        )
        lines.append(
            f"- {spec.key} ({spec.type}, etiqueta UI: {spec.label}; "
            f"comparadores: {comp}){enums}"
        )
    return "\n".join(lines)


def generate_segment_rules(
    description: str, *, user_id: str
) -> dict[str, Any]:
    """Translate a natural-language description into a rule tree.
    Returns either `{"rules": {...}}` for a valid proposal or
    `{"error": "..."}` for an ambiguous / unsupported case."""
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
    _rate_limit_check(f"segment-generate:{user_id}", max_calls=10)
    # Use `replace` instead of `.format()` — the prompt's JSON
    # examples contain literal `{}` braces that `.format()` would
    # try to interpolate.
    system = SEGMENT_GENERATE_SYSTEM_PROMPT.replace(
        "{fields_table}", _build_fields_table()
    )
    raw = _invoke_claude(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        system_prompt=system,
        user_prompt=f"Genera un segmento para: {cleaned}",
    )
    parsed = _parse_segment_json(raw)
    if "error" in parsed:
        return {"error": str(parsed["error"])}
    return {"rules": parsed}


def explain_segment_rules(
    rules: dict[str, Any], *, user_id: str
) -> str:
    """Translate a rule tree back into a short Spanish paragraph the
    operator can read without learning the field whitelist."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise LLMNotConfiguredError(
            "AI features are disabled. Set ANTHROPIC_API_KEY to enable."
        )
    _rate_limit_check(f"segment-explain:{user_id}", max_calls=30)
    import json as _json  # noqa: PLC0415

    raw = _invoke_claude(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        system_prompt=SEGMENT_EXPLAIN_SYSTEM_PROMPT,
        user_prompt=_json.dumps(rules, ensure_ascii=False),
    )
    return raw.strip().strip("`").strip()


def _parse_segment_json(raw: str) -> dict[str, Any]:
    """JSON-load the raw provider output, stripping markdown fences.

    On parse failure we log a short preview of the raw text so an
    operator hunting down a "non-JSON content" issue can confirm
    whether Claude returned prose, a refusal, or a malformed snippet
    without having to enable the upstream's debug logs. The preview is
    capped to 200 chars so a runaway model can't flood the log."""
    import json as _json  # noqa: PLC0415

    cleaned = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = _json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        preview = cleaned[:200].replace("\n", " ")
        logger.warning(
            "llm.parse_failure chars=%d preview=%r",
            len(raw),
            preview,
        )
        raise LLMUpstreamError("Provider returned non-JSON content") from exc
    if not isinstance(data, dict):
        logger.warning(
            "llm.parse_non_object type=%s preview=%r",
            type(data).__name__,
            str(data)[:200],
        )
        raise LLMUpstreamError("Provider returned a non-object payload")
    return data


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
    the high-level `generate_pipeline_proposal` flow.

    Diagnostic logging is metadata-only: model + prompt sizes + response
    length. The API key never leaves this scope; raw user prompts and
    raw responses are NOT logged so an enabled DEBUG level doesn't leak
    PII to the application log.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - guarded by config
        raise LLMNotConfiguredError(
            "anthropic package not installed"
        ) from exc

    logger.info(
        "llm.request model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS_OUTPUT,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except getattr(anthropic, "RateLimitError", Exception) as exc:
        logger.warning("llm.rate_limit model=%s", model)
        raise LLMRateLimitError("Upstream rate limit") from exc
    except getattr(anthropic, "APIError", Exception) as exc:
        logger.warning("llm.api_error model=%s err=%s", model, exc)
        raise LLMUpstreamError("Provider error") from exc
    except Exception as exc:  # noqa: BLE001 - last-line catch-all
        logger.exception("llm.unexpected_failure model=%s", model)
        raise LLMUpstreamError("Unexpected provider failure") from exc

    if not message.content:
        logger.warning("llm.empty_response model=%s", model)
        raise LLMUpstreamError("Empty response from provider")
    chunk = message.content[0]
    text = getattr(chunk, "text", None)
    if not text:
        logger.warning("llm.non_text_response model=%s", model)
        raise LLMUpstreamError("Provider returned non-text content")
    logger.info("llm.response model=%s chars=%d", model, len(text))
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
