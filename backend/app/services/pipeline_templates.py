"""Hardcoded library of pipeline templates.

The 7 default templates cover the most common CRM workflows in Spanish
SMBs (ventas B2B/B2C, onboarding, reactivación, soporte, renovaciones,
RRHH). They're hardcoded — NOT in the DB — because they're product
content, not user data; they ship with the release and can be
versioned alongside the code.

`build_pipeline_payload(template_id, name=None)` translates a template
into the dict shape `pipelines_repository.create_pipeline` expects so
the route layer doesn't have to know the template internals.
"""
from __future__ import annotations

from typing import Any

_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "sales_b2b",
        "name": "Ventas B2B",
        "description": "Pipeline clásico de ventas a empresas. Adaptable a cualquier sector.",
        "category": "ventas",
        "color": "#3b82f6",
        "stages": [
            {"name": "Nuevo lead", "target_days": 1, "color": "#6b7280"},
            {"name": "Contactado", "target_days": 3, "color": "#f59e0b"},
            {"name": "Cualificado", "target_days": 7, "color": "#10b981"},
            {"name": "Propuesta enviada", "target_days": 14},
            {"name": "Negociación", "target_days": 21},
            {"name": "Cerrado ganado", "is_won": True, "color": "#22c55e"},
            {"name": "Cerrado perdido", "is_lost": True, "color": "#ef4444"},
        ],
    },
    {
        "id": "sales_b2c",
        "name": "Ventas B2C / Ecommerce",
        "description": "Pipeline corto para venta directa a consumidor final.",
        "category": "ventas",
        "color": "#06b6d4",
        "stages": [
            {"name": "Lead", "target_days": 1},
            {"name": "Interesado"},
            {"name": "Pidió presupuesto", "target_days": 2},
            {"name": "Pago realizado", "is_won": True},
            {"name": "No cerrado", "is_lost": True},
        ],
    },
    {
        "id": "onboarding",
        "name": "Onboarding nuevos clientes",
        "description": (
            "Acompañamiento desde la firma del contrato hasta que el "
            "cliente está operativo."
        ),
        "category": "post-venta",
        "color": "#10b981",
        "stages": [
            {"name": "Bienvenida", "target_days": 1},
            {"name": "Setup técnico", "target_days": 7},
            {"name": "Formación", "target_days": 14},
            {"name": "Producción", "target_days": 30},
            {"name": "Operativo", "is_won": True},
            {"name": "Abandonó", "is_lost": True},
        ],
    },
    {
        "id": "reactivation",
        "name": "Reactivación clientes inactivos",
        "description": "Recuperar clientes que llevan tiempo sin comprar.",
        "category": "marketing",
        "color": "#a855f7",
        "stages": [
            {"name": "Inactivo identificado"},
            {"name": "Primer contacto", "target_days": 3},
            {"name": "Seguimiento", "target_days": 14},
            {"name": "Oferta enviada", "target_days": 7},
            {"name": "Recuperado", "is_won": True},
            {"name": "No recuperable", "is_lost": True},
        ],
    },
    {
        "id": "support",
        "name": "Soporte técnico / Tickets",
        "description": "Gestión de incidencias técnicas reportadas por clientes.",
        "category": "soporte",
        "color": "#f97316",
        "stages": [
            {"name": "Abierto", "target_days": 1},
            {"name": "Diagnóstico", "target_days": 2},
            {"name": "En reparación", "target_days": 5},
            {"name": "En espera de cliente", "target_days": 3},
            {"name": "Resuelto", "is_won": True},
            {"name": "Cerrado sin resolver", "is_lost": True},
        ],
    },
    {
        "id": "renewal",
        "name": "Renovaciones de contrato",
        "description": "Seguimiento de fechas de renovación de servicios.",
        "category": "post-venta",
        "color": "#14b8a6",
        "stages": [
            {"name": "30 días antes"},
            {"name": "Contacto inicial", "target_days": 7},
            {"name": "Negociando", "target_days": 14},
            {"name": "Renovado", "is_won": True},
            {"name": "Perdido", "is_lost": True},
        ],
    },
    {
        "id": "recruitment",
        "name": "Selección RRHH",
        "description": "Pipeline para procesos de selección de personal.",
        "category": "rrhh",
        "color": "#ec4899",
        "stages": [
            {"name": "CV recibido"},
            {"name": "Preseleccionado"},
            {"name": "Entrevista 1"},
            {"name": "Entrevista 2"},
            {"name": "Oferta enviada"},
            {"name": "Contratado", "is_won": True},
            {"name": "Descartado", "is_lost": True},
        ],
    },
]


def list_templates() -> list[dict[str, Any]]:
    """Return a shallow copy so a route mutation can't corrupt the
    module-level constant for the next request."""
    return [
        {
            **template,
            "stages": [dict(stage) for stage in template["stages"]],
        }
        for template in _TEMPLATES
    ]


def get_template(template_id: str) -> dict[str, Any] | None:
    for template in _TEMPLATES:
        if template["id"] == template_id:
            return {
                **template,
                "stages": [dict(stage) for stage in template["stages"]],
            }
    return None


def build_pipeline_payload(
    template_id: str, *, name: str | None = None
) -> dict[str, Any] | None:
    """Translate a template into the dict
    `pipelines_repository.create_pipeline` consumes (name, description,
    color, stages with `position` indexed)."""
    template = get_template(template_id)
    if template is None:
        return None
    return {
        "name": (name or template["name"]).strip() or template["name"],
        "description": template.get("description"),
        "color": template.get("color"),
        "stages": [
            {
                "name": stage["name"],
                "description": stage.get("description"),
                "color": stage.get("color"),
                "is_won": bool(stage.get("is_won", False)),
                "is_lost": bool(stage.get("is_lost", False)),
                "target_days": stage.get("target_days"),
                "position": index,
            }
            for index, stage in enumerate(template["stages"])
        ],
    }
