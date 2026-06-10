"""Hardcoded library of starter segments.

Same versioned-with-code pattern the pipeline templates use. The
wizard's gallery instantiates one of these via `POST /api/segments`
after the operator picks it from the modal.
"""
from __future__ import annotations

from typing import Any

_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "hot_leads",
        "name": "Hot leads",
        "description": "Contactos con lead score alto y consentimiento marketing.",
        "category": "ventas",
        "color": "#ef4444",
        "rules": {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "lead_score",
                    "comparator": "gte",
                    "value": 70,
                },
                {
                    "type": "rule",
                    "field": "marketing_consent",
                    "comparator": "eq",
                    "value": "granted",
                },
                {
                    "type": "rule",
                    "field": "is_active",
                    "comparator": "eq",
                    "value": True,
                },
            ],
        },
    },
    {
        "id": "inactive_90_days",
        "name": "Inactivos 90 días",
        "description": "Sin actividad reciente en los últimos 90 días.",
        "category": "marketing",
        "color": "#a855f7",
        "rules": {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "updated_at",
                    "comparator": "not_in_last_n_days",
                    "value": 90,
                },
                {
                    "type": "rule",
                    "field": "is_active",
                    "comparator": "eq",
                    "value": True,
                },
            ],
        },
    },
    {
        "id": "new_this_week",
        "name": "Nuevos esta semana",
        "description": "Contactos creados en los últimos 7 días.",
        "category": "ventas",
        "color": "#22c55e",
        "rules": {
            "type": "rule",
            "field": "created_at",
            "comparator": "in_last_n_days",
            "value": 7,
        },
    },
    {
        "id": "no_marketing_consent",
        "name": "Sin consentimiento marketing",
        "description": "Contactos con consentimiento explícito denegado o desconocido.",
        "category": "compliance",
        "color": "#f59e0b",
        "rules": {
            "type": "rule",
            "field": "marketing_consent",
            "comparator": "in",
            "value": ["denied", "unknown", "unsubscribed"],
        },
    },
    {
        "id": "agilecrm_only",
        "name": "Sólo AgileCRM",
        "description": "Contactos cuya única fuente externa es AgileCRM.",
        "category": "datos",
        "color": "#3b82f6",
        "rules": {
            "type": "rule",
            "field": "origin_system",
            "comparator": "eq",
            "value": "agilecrm",
        },
    },
    {
        "id": "spain_active",
        "name": "España activos",
        "description": "Contactos activos con dirección en España.",
        "category": "ventas",
        "color": "#10b981",
        "rules": {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "address_country",
                    "comparator": "in",
                    "value": ["ES", "España", "Spain"],
                },
                {
                    "type": "rule",
                    "field": "is_active",
                    "comparator": "eq",
                    "value": True,
                },
            ],
        },
    },
    {
        "id": "vip_email_only",
        "name": "VIP con email válido",
        "description": "Contactos con tag VIP y dirección de email registrada.",
        "category": "ventas",
        "color": "#d946ef",
        "rules": {
            "operator": "AND",
            "children": [
                {
                    "type": "rule",
                    "field": "tags",
                    "comparator": "contains_any",
                    "value": ["__vip__"],  # placeholder; UI replaces with real tag id
                },
                {
                    "type": "rule",
                    "field": "email",
                    "comparator": "is_not_null",
                },
            ],
        },
    },
]


def list_templates() -> list[dict[str, Any]]:
    return [{**tmpl, "rules": dict(tmpl["rules"])} for tmpl in _TEMPLATES]


def get_template(template_id: str) -> dict[str, Any] | None:
    for tmpl in _TEMPLATES:
        if tmpl["id"] == template_id:
            return {**tmpl, "rules": dict(tmpl["rules"])}
    return None
