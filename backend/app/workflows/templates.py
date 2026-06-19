"""Galería de 3 plantillas predefinidas (Bloque 1 — Sprint UX).

Cada plantilla es un dict con la forma del JSON que el editor envía
al backend al guardar (`steps` + `edges`). El endpoint `POST
/api/workflows/from-template/{template_id}` clona la plantilla a una
nueva fila draft con nombre "{plantilla} (copia)".

Tres casos básicos pedidos por Bart:

1. `onboarding-lead-nuevo` — Bienvenida + recordatorio si no abre.
2. `cumpleanos` — Felicitación cumpleaños.
3. `followup-presupuesto` — Recordatorio + escalado al manager.

Cuando Bart pida más plantillas las añadimos aquí — son data, no
código.
"""
from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "onboarding-lead-nuevo": {
        "name": "Onboarding lead nuevo",
        "description": (
            "Cuando entra un lead nuevo, mándale email de bienvenida. "
            "Si no lo abre en 3 días, crea tarea de seguimiento."
        ),
        "trigger_type": "contact.created",
        "trigger_config": {},
        "steps": [
            {
                "client_id": "step-1",
                "type": "trigger",
                "config": {},
                "position_x": 120,
                "position_y": 80,
                "is_entry": True,
            },
            {
                "client_id": "step-2",
                "type": "action_send_email",
                "config": {
                    "subject": "Bienvenido/a a Bomedia, {{ contact.first_name }}",
                    "body_html": (
                        "<p>Hola {{ contact.first_name }},</p>"
                        "<p>Gracias por ponerte en contacto. Aquí va una "
                        "presentación rápida de lo que hacemos.</p>"
                    ),
                    "from_alias": "",
                },
                "position_x": 120,
                "position_y": 220,
                "is_entry": False,
            },
            {
                "client_id": "step-3",
                "type": "wait_for_event",
                "config": {
                    "event_type": "email.crm.opened",
                    "timeout_minutes": 4320,
                },
                "position_x": 120,
                "position_y": 360,
                "is_entry": False,
            },
            {
                "client_id": "step-4",
                "type": "action_create_task",
                "config": {
                    "title": "Llamar a {{ contact.first_name }} — no abrió bienvenida",
                    "description": "El lead no abrió el email en 3 días.",
                    "priority": "medium",
                    "due_in_days": 1,
                },
                "position_x": 120,
                "position_y": 500,
                "is_entry": False,
            },
            {
                "client_id": "step-5",
                "type": "exit_natural",
                "config": {},
                "position_x": 120,
                "position_y": 640,
                "is_entry": False,
            },
            {
                "client_id": "step-6",
                "type": "exit_won",
                "config": {},
                "position_x": 380,
                "position_y": 500,
                "is_entry": False,
            },
        ],
        "edges": [
            {
                "from_client_id": "step-1",
                "to_client_id": "step-2",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-2",
                "to_client_id": "step-3",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-3",
                "to_client_id": "step-6",
                "branch_label": "matched",
            },
            {
                "from_client_id": "step-3",
                "to_client_id": "step-4",
                "branch_label": "timeout",
            },
            {
                "from_client_id": "step-4",
                "to_client_id": "step-5",
                "branch_label": "default",
            },
        ],
    },
    "cumpleanos": {
        "name": "Felicitar cumpleaños",
        "description": (
            "Cada año, el día del cumpleaños del contacto, le mandas un "
            "email de felicitación."
        ),
        "trigger_type": "contact.date_field",
        "trigger_config": {"field": "birthday"},
        "steps": [
            {
                "client_id": "step-1",
                "type": "trigger",
                "config": {},
                "position_x": 120,
                "position_y": 80,
                "is_entry": True,
            },
            {
                "client_id": "step-2",
                "type": "action_send_email",
                "config": {
                    "subject": "¡Feliz cumpleaños, {{ contact.first_name }}!",
                    "body_html": (
                        "<p>Hola {{ contact.first_name }},</p>"
                        "<p>Que pases un gran día. Un abrazo del equipo "
                        "Bomedia.</p>"
                    ),
                    "from_alias": "",
                },
                "position_x": 120,
                "position_y": 220,
                "is_entry": False,
            },
            {
                "client_id": "step-3",
                "type": "exit_natural",
                "config": {},
                "position_x": 120,
                "position_y": 360,
                "is_entry": False,
            },
        ],
        "edges": [
            {
                "from_client_id": "step-1",
                "to_client_id": "step-2",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-2",
                "to_client_id": "step-3",
                "branch_label": "default",
            },
        ],
    },
    "followup-presupuesto": {
        "name": "Follow-up presupuesto",
        "description": (
            "Cuando una oportunidad pasa a stage 'Presupuesto enviado', "
            "espera 5 días, recuérdale, y a los 14 días escala al manager."
        ),
        "trigger_type": "opportunity.stage_changed",
        "trigger_config": {},
        "steps": [
            {
                "client_id": "step-1",
                "type": "trigger",
                "config": {},
                "position_x": 120,
                "position_y": 80,
                "is_entry": True,
            },
            {
                "client_id": "step-2",
                "type": "wait_time",
                "config": {"duration_minutes": 7200},
                "position_x": 120,
                "position_y": 220,
                "is_entry": False,
            },
            {
                "client_id": "step-3",
                "type": "action_send_email",
                "config": {
                    "subject": "Recordatorio: presupuesto",
                    "body_html": (
                        "<p>Hola {{ contact.first_name }},</p>"
                        "<p>Te recuerdo el presupuesto que te envié hace "
                        "unos días. ¿Qué tal lo ves?</p>"
                    ),
                    "from_alias": "",
                },
                "position_x": 120,
                "position_y": 360,
                "is_entry": False,
            },
            {
                "client_id": "step-4",
                "type": "wait_time",
                "config": {"duration_minutes": 12960},
                "position_x": 120,
                "position_y": 500,
                "is_entry": False,
            },
            {
                "client_id": "step-5",
                "type": "action_notify_manager",
                "config": {
                    "message": (
                        "Presupuesto a {{ contact.first_name }} "
                        "({{ contact.email }}) lleva 14 días sin respuesta. "
                        "Revisar."
                    )
                },
                "position_x": 120,
                "position_y": 640,
                "is_entry": False,
            },
            {
                "client_id": "step-6",
                "type": "exit_natural",
                "config": {},
                "position_x": 120,
                "position_y": 780,
                "is_entry": False,
            },
        ],
        "edges": [
            {
                "from_client_id": "step-1",
                "to_client_id": "step-2",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-2",
                "to_client_id": "step-3",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-3",
                "to_client_id": "step-4",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-4",
                "to_client_id": "step-5",
                "branch_label": "default",
            },
            {
                "from_client_id": "step-5",
                "to_client_id": "step-6",
                "branch_label": "default",
            },
        ],
    },
}


def list_templates() -> list[dict[str, Any]]:
    """Devuelve metadata ligera para la galería (sin steps/edges)."""
    return [
        {
            "id": tid,
            "name": tpl["name"],
            "description": tpl["description"],
            "trigger_type": tpl["trigger_type"],
            "steps_count": len(tpl["steps"]),
        }
        for tid, tpl in TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict[str, Any] | None:
    return TEMPLATES.get(template_id)
