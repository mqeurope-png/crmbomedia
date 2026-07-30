"""Sprint Ficha 360 — Bloque 2: timeline unificado del contacto.

GET /api/contacts/{id}/timeline?limit&offset&types&sort

Estrategia: los volúmenes son per-contacto (decenas/cientos), así que se
cargan las fuentes filtradas por contacto, se normalizan a un shape
común, se ordenan en memoria y se pagina el resultado. Brevo se agrupa
por campaña (un evento por campaña con contadores de opens/clicks).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import (
    ActivityEvent,
    AuditLog,
    CallLog,
    Contact,
    EmailMessage,
    EmailThread,
    Note,
    Task,
    User,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["contact-timeline"])

ALL_TYPES = {
    "call", "email", "note", "task", "tag", "pipeline", "brevo", "lifecycle",
}

_RESULT_LABELS = {
    "contacted": "Contactado", "no_answer": "No contesta",
    "voicemail": "Buzón de voz", "call_back": "Volver a llamar",
    "interested": "Interesado", "not_interested": "No interesado",
    "info_requested": "Pidió información", "other": "Otro",
}


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _event(
    kind: str, native_id: Any, occurred_at: datetime | None,
    actor_user_id: str | None, icon: str, title: str,
    detail: str | None, metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{native_id}",
        "type": kind if kind in ALL_TYPES else kind,
        "occurred_at": _aware(occurred_at),
        "_actor_user_id": actor_user_id,
        "icon": icon,
        "title": title,
        "detail": detail,
        "metadata": metadata,
    }


@router.get("/{contact_id}/timeline")
def contact_timeline(
    contact_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    types: str | None = Query(default=None),
    sort: str = Query(default="desc"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    _ = current_user
    if session.get(Contact, contact_id) is None:
        raise not_found("Contact")
    wanted = (
        {t.strip() for t in types.split(",") if t.strip()} & ALL_TYPES
        if types
        else set(ALL_TYPES)
    )
    events: list[dict[str, Any]] = []

    if "call" in wanted:
        for c in session.scalars(
            select(CallLog).where(CallLog.contact_id == contact_id)
        ):
            label = _RESULT_LABELS.get(c.result_code, c.result_code)
            if c.result_code == "other" and c.result_custom:
                label = c.result_custom
            detail = " · ".join(x for x in [c.subject, c.notes] if x) or None
            events.append(_event(
                "call", c.id, c.called_at, c.user_id, "📞",
                f"Llamada: {label}", detail,
                {"result_code": c.result_code,
                 "duration_bucket": c.duration_bucket,
                 "follow_up_task_id": c.follow_up_task_id},
            ))

    if "note" in wanted:
        for n in session.scalars(
            select(Note).where(Note.contact_id == contact_id)
        ):
            events.append(_event(
                "note", n.id, n.external_created_at or n.created_at,
                n.author_user_id, "📝", "Nota",
                (n.body or "")[:200],
                {"source": n.source,
                 "external_author_name": n.external_author_name},
            ))

    if "task" in wanted:
        for t in session.scalars(
            select(Task).where(Task.contact_id == contact_id)
        ):
            events.append(_event(
                "task", t.id, t.created_at, t.created_by_user_id, "✅",
                "Tarea creada", t.title, {"task_id": t.id},
            ))
            if t.completed_at is not None:
                events.append(_event(
                    "task", f"{t.id}:done", t.completed_at,
                    t.assigned_user_id, "☑️", "Tarea completada", t.title,
                    {"task_id": t.id},
                ))

    if "email" in wanted:
        rows = session.execute(
            select(EmailMessage, EmailThread.contact_id)
            .join(EmailThread, EmailThread.id == EmailMessage.thread_id)
            .where(EmailThread.contact_id == contact_id)
        ).all()
        for msg, _cid in rows:
            outbound = str(getattr(msg.direction, "value", msg.direction)) == "outbound"
            events.append(_event(
                "email", msg.id, msg.sent_at or msg.created_at,
                msg.gmail_account_user_id if outbound else None,
                "📧", "Email enviado" if outbound else "Email recibido",
                f"Asunto: {msg.subject}" if msg.subject else None,
                {"direction": "outbound" if outbound else "inbound",
                 "email_message_id": msg.id},
            ))

    if "brevo" in wanted:
        campaigns: dict[int, dict[str, Any]] = {}
        for ev in session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.contact_id == contact_id,
                ActivityEvent.campaign_brevo_id.is_not(None),
                ActivityEvent.event_type.in_(["email.opened", "email.clicked"]),
            )
        ):
            bucket = campaigns.setdefault(
                ev.campaign_brevo_id,
                {"opens": 0, "clicks": 0, "first": ev.occurred_at},
            )
            if ev.event_type == "email.opened":
                bucket["opens"] += 1
            else:
                bucket["clicks"] += 1
            if _aware(ev.occurred_at) < _aware(bucket["first"]):
                bucket["first"] = ev.occurred_at
        names: dict[int, str] = {}
        if campaigns:
            try:
                from app.models.brevo import BrevoCampaignCache  # noqa: PLC0415

                for cache in session.scalars(
                    select(BrevoCampaignCache).where(
                        BrevoCampaignCache.brevo_campaign_id.in_(
                            list(campaigns.keys())
                        )
                    )
                ):
                    names[cache.brevo_campaign_id] = cache.name
            except Exception:  # noqa: BLE001 — nombre opcional
                pass
        for camp_id, agg in campaigns.items():
            parts = []
            if agg["opens"]:
                parts.append(
                    f"Abierta {agg['opens']} vez"
                    if agg["opens"] == 1 else f"Abierta {agg['opens']} veces"
                )
            if agg["clicks"]:
                parts.append(
                    f"{agg['clicks']} click"
                    if agg["clicks"] == 1 else f"{agg['clicks']} clicks"
                )
            events.append(_event(
                "brevo", f"campaign:{camp_id}", agg["first"], None, "📨",
                f"Campaña {names.get(camp_id, camp_id)}",
                " · ".join(parts) or None,
                {"campaign_brevo_id": camp_id, "opens": agg["opens"],
                 "clicks": agg["clicks"]},
            ))

    audit_map = {
        "tag": (["contact_tag.added", "contact_tag.removed"], "🏷️"),
        "pipeline": ([
            "contact_pipeline_stage.added",
            "contact_pipeline_stage.stage_changed",
            "contact_pipeline_stage.archived",
        ], "🎯"),
    }
    for kind, (actions, icon) in audit_map.items():
        if kind not in wanted:
            continue
        for row in session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(actions),
                AuditLog.target_id == contact_id,
            )
        ):
            meta = _safe_meta(row.metadata_json)
            events.append(_event(
                kind, row.id, row.created_at, row.actor_user_id, icon,
                _AUDIT_TITLES.get(row.action, row.action),
                _audit_detail(row.action, meta), meta,
            ))

    if "lifecycle" in wanted:
        for row in session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(["contact.updated", "contact.bulk_updated"]),
                AuditLog.target_id == contact_id,
            )
        ):
            meta = _safe_meta(row.metadata_json)
            changed = meta.get("changed_fields") or []
            if "commercial_status" not in changed and "lead_score" not in changed:
                continue
            events.append(_event(
                "lifecycle", row.id, row.created_at, row.actor_user_id,
                "🔄", "Contacto actualizado",
                ", ".join(changed) or None, meta,
            ))

    reverse = sort != "asc"
    events.sort(key=lambda e: e["occurred_at"], reverse=reverse)
    total = len(events)
    page = events[offset : offset + limit]

    # Resolver actores en bloque.
    user_ids = {e["_actor_user_id"] for e in page if e["_actor_user_id"]}
    users = {
        u.id: u.full_name or u.email
        for u in session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    items = []
    for e in page:
        actor_id = e.pop("_actor_user_id")
        e["actor"] = (
            {"user_id": actor_id, "name": users.get(actor_id, "—")}
            if actor_id else None
        )
        e["occurred_at"] = e["occurred_at"].isoformat()
        items.append(e)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


_AUDIT_TITLES = {
    "contact_tag.added": "Tag añadida",
    "contact_tag.removed": "Tag quitada",
    "contact_pipeline_stage.added": "Añadido a pipeline",
    "contact_pipeline_stage.stage_changed": "Pipeline cambiado",
    "contact_pipeline_stage.archived": "Pipeline archivado",
}


def _safe_meta(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _audit_detail(action: str, meta: dict[str, Any]) -> str | None:
    if action.startswith("contact_tag"):
        return meta.get("tag_name") or meta.get("tag_id")
    if action.startswith("contact_pipeline_stage"):
        parts = [
            str(meta.get(k))
            for k in ("pipeline_name", "from_stage_name", "to_stage_name")
            if meta.get(k)
        ]
        return " → ".join(parts) or None
    return None
