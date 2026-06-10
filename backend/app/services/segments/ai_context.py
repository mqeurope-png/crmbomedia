"""CRM-context builder for the segment AI prompt.

The default system prompt only tells Claude what fields + comparators
exist, not what real data lives in the operator's DB. As a result a
prompt like "contactos con tag MBO" generates rules that match zero
contacts because the model didn't know the tag is actually called
`formMBO` or `MBO 3050 UV LED`.

This module builds a per-tenant context block that lists the tags,
integration accounts, address countries, pipelines + stages and the
current `lead_score` range. The block is injected into the system
prompt right before the field whitelist so the model has the exact
ids it needs.

Cache: 5 minutes per process. Long enough to absorb most segment-AI
bursts without re-querying, short enough that an operator adding a
new tag sees it within the next coffee break.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import (
    Contact,
    Pipeline,
    PipelineStage,
    Tag,
)
from app.models.integration_settings import IntegrationAccount

CACHE_TTL_SECONDS = 300
MAX_TAGS = 100
MAX_COUNTRIES = 60
MAX_PIPELINES = 20
MAX_CONTEXT_CHARS = 16_000  # ~4000 tokens cap — leaves room for the
# field whitelist + the operator's description.

_cache: dict[int, tuple[float, str]] = {}


def reset_cache() -> None:
    """Test-only knob; production code never calls this explicitly."""
    _cache.clear()


def build_crm_context(session: Session) -> str:
    """Return a formatted CRM-context block to splice into the AI
    system prompt. Cached per process for `CACHE_TTL_SECONDS`."""
    # Cache keyed on the engine identity so a test using an in-memory
    # SQLite + an unrelated process talking to MySQL don't pollute each
    # other's blocks.
    cache_key = id(session.get_bind())
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    tags = _load_top_tags(session)
    accounts = _load_integration_accounts(session)
    countries = _load_countries(session)
    pipelines = _load_pipelines(session)
    lead_score_range = _load_lead_score_range(session)

    parts: list[str] = ["Contexto del CRM al que el operador pertenece:"]
    parts.append(_format_tags(tags))
    parts.append(_format_accounts(accounts))
    parts.append(_format_countries(countries))
    parts.append(_format_pipelines(pipelines))
    parts.append(_format_lead_score(lead_score_range))
    parts.append(
        "Cuando el operador mencione una tag por nombre, busca el id "
        "correspondiente en la lista. Si menciona algo que pueda "
        "matchear varias tags (ej: \"MBO\"), incluye todas las "
        "relevantes en el array de value. Misma lógica para las "
        "cuentas de integración y los pipelines."
    )

    text = "\n\n".join(part for part in parts if part)

    # Hard cap — if a tenant has 5000 tags and 200 accounts the prompt
    # would balloon past the model's input. Truncate from the end so
    # tags (the most useful slice) are preserved.
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[...contexto truncado por tamaño...]"

    _cache[cache_key] = (now, text)
    return text


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def _load_top_tags(session: Session) -> list[dict[str, Any]]:
    """Pull the most-used tags first so a truncated context still
    contains the ones the operator is most likely to reference."""
    from app.models.crm import ContactTag  # noqa: PLC0415

    usage = (
        select(
            Tag.id,
            Tag.name,
            func.count(ContactTag.contact_id).label("usage"),
        )
        .outerjoin(ContactTag, ContactTag.tag_id == Tag.id)
        .group_by(Tag.id, Tag.name)
        .order_by(func.count(ContactTag.contact_id).desc(), Tag.name)
        .limit(MAX_TAGS)
    )
    return [
        {"id": row.id, "name": row.name, "usage": int(row.usage)}
        for row in session.execute(usage)
    ]


def _load_integration_accounts(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            IntegrationAccount.system,
            IntegrationAccount.account_id,
            IntegrationAccount.display_name,
        )
        .where(IntegrationAccount.enabled.is_(True))
        .order_by(IntegrationAccount.system, IntegrationAccount.account_id)
    ).all()
    return [
        {
            "system": row.system.value if hasattr(row.system, "value") else str(row.system),
            "account_id": row.account_id,
            "display_name": row.display_name,
        }
        for row in rows
    ]


def _load_countries(session: Session) -> list[str]:
    rows = session.execute(
        select(Contact.address_country)
        .where(Contact.address_country.is_not(None))
        .where(Contact.address_country != "")
        .distinct()
        .order_by(Contact.address_country)
        .limit(MAX_COUNTRIES)
    ).all()
    return [row[0] for row in rows if row[0]]


def _load_pipelines(session: Session) -> list[dict[str, Any]]:
    pipelines = session.execute(
        select(Pipeline)
        .where(Pipeline.is_active.is_(True))
        .order_by(Pipeline.name)
        .limit(MAX_PIPELINES)
    ).scalars().all()
    result: list[dict[str, Any]] = []
    for pipeline in pipelines:
        stages = session.execute(
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline.id)
            .order_by(PipelineStage.position)
        ).scalars().all()
        result.append(
            {
                "id": pipeline.id,
                "name": pipeline.name,
                "stages": [{"id": s.id, "name": s.name} for s in stages],
            }
        )
    return result


def _load_lead_score_range(session: Session) -> tuple[int | None, int | None]:
    row = session.execute(
        select(func.min(Contact.lead_score), func.max(Contact.lead_score))
    ).first()
    if not row:
        return (None, None)
    lo, hi = row
    return (int(lo) if lo is not None else None, int(hi) if hi is not None else None)


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


def _format_tags(tags: list[dict[str, Any]]) -> str:
    if not tags:
        return "TAGS DISPONIBLES: ninguna todavía."
    lines = [
        "TAGS DISPONIBLES (usa estos ids exactos en value cuando el "
        "field sea \"tags\"):"
    ]
    for tag in tags:
        lines.append(f"- \"{tag['id']}\": \"{tag['name']}\"")
    return "\n".join(lines)


def _format_accounts(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return "CUENTAS DE INTEGRACIÓN: ninguna configurada."
    lines = [
        "CUENTAS DE INTEGRACIÓN (usa estos account_id para "
        "origin_account_id; el system del enum se elige aparte):"
    ]
    for acc in accounts:
        lines.append(
            f"- system={acc['system']}, account_id=\"{acc['account_id']}\""
            f": {acc['display_name']}"
        )
    return "\n".join(lines)


def _format_countries(countries: list[str]) -> str:
    if not countries:
        return "PAÍSES PRESENTES en contactos: ninguno."
    return "PAÍSES PRESENTES en contactos: " + ", ".join(countries) + "."


def _format_pipelines(pipelines: list[dict[str, Any]]) -> str:
    if not pipelines:
        return "PIPELINES Y ETAPAS: no hay pipelines activos."
    lines = ["PIPELINES Y ETAPAS:"]
    for pipe in pipelines:
        lines.append(f"- Pipeline \"{pipe['name']}\" (id={pipe['id']}):")
        for stage in pipe["stages"]:
            lines.append(f"   - Etapa \"{stage['name']}\" (id={stage['id']})")
    return "\n".join(lines)


def _format_lead_score(rng: tuple[int | None, int | None]) -> str:
    lo, hi = rng
    if lo is None or hi is None:
        return "RANGO LEAD SCORE: sin datos todavía."
    return f"RANGO LEAD SCORE: actual de {lo} a {hi}."
