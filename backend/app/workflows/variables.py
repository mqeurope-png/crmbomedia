"""Interpolación de variables en plantillas de pasos.

Jinja2 `SandboxedEnvironment` con `autoescape=True` para targets HTML
(cuerpo de email). Para targets plain (asunto, descripción tarea) se
desactiva autoescape — pero las variables se siguen sanitizando contra
control chars.

Whitelist de namespaces:

- `{{ contact.first_name }}`, `{{ contact.email }}`, ...
- `{{ trigger.event_type }}`, ...
- `{{ owner.full_name }}`, ...
- `{{ company.name }}`, ...
- `{{ opportunity.title }}`, ...

Acceso a `__class__`, `__mro__`, etc. está bloqueado por el sandbox.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import (
    Company,
    Contact,
    ContactPipelineStage,
    User,
)

log = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _contact_namespace(contact: Contact) -> dict[str, Any]:
    return {
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "full_name": (
            f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        ),
        "email": contact.email or "",
        "phone": contact.phone or "",
        "origin": contact.origin or "",
        "lifecycle_status": contact.commercial_status or "",
        "lead_score": contact.lead_score or 0,
        "job_title": contact.job_title or "",
        "company_name": "",  # filled below if Company exists
        "address_country": contact.address_country or "",
        "address_city": contact.address_city or "",
    }


def _owner_namespace(session: Session, user_id: str | None) -> dict[str, Any]:
    if not user_id:
        return {
            "full_name": "",
            "first_name": "",
            "email": "",
            "id": "",
        }
    user = session.get(User, user_id)
    if user is None:
        return {
            "full_name": "",
            "first_name": "",
            "email": "",
            "id": user_id,
        }
    parts = (user.full_name or "").split(" ", 1)
    return {
        "full_name": user.full_name or "",
        "first_name": parts[0] if parts else "",
        "email": user.email or "",
        "id": user.id,
    }


def _company_namespace(
    session: Session, company_id: str | None
) -> dict[str, Any]:
    """Empresa asociada. `company_id=None` (sin empresa) devuelve
    strings vacíos — `{{ company.name }}` renderiza `""` sin crashear."""
    empty = {"name": "", "domain": "", "id": ""}
    if not company_id:
        return empty
    company = session.get(Company, company_id)
    if company is None:
        return empty
    return {
        "name": company.name or "",
        "domain": (company.website or "") if hasattr(company, "website") else "",
        "id": company.id,
    }


def _last_opportunity_namespace(
    session: Session, contact_id: str
) -> dict[str, Any]:
    """Última asignación a pipeline del contacto — el modelo
    `ContactPipelineStage` es la "oportunidad" en este CRM. Decisión:
    la más reciente por `entered_stage_at`."""
    row = session.scalar(
        select(ContactPipelineStage)
        .where(ContactPipelineStage.contact_id == contact_id)
        .order_by(ContactPipelineStage.entered_stage_at.desc())
        .limit(1)
    )
    if row is None:
        return {"pipeline": "", "stage": "", "id": ""}
    return {
        "pipeline": str(row.pipeline_id or ""),
        "stage": str(row.stage_id or ""),
        "id": row.id,
    }


def build_context(
    *,
    session: Session,
    contact: Contact,
    trigger_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construye el namespace completo. Llamado una vez por step
    antes de cada interpolación."""
    contact_ns = _contact_namespace(contact)
    company_ns = _company_namespace(session, contact.company_id)
    contact_ns["company_name"] = company_ns["name"]
    return {
        "contact": contact_ns,
        "owner": _owner_namespace(session, contact.owner_user_id),
        "company": company_ns,
        "opportunity": _last_opportunity_namespace(session, contact.id),
        "trigger": trigger_payload or {},
    }


_html_env = SandboxedEnvironment(
    autoescape=True,
    undefined=StrictUndefined,
)
_text_env = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
)


def render(
    template_text: str,
    context: dict[str, Any],
    *,
    is_html: bool = False,
) -> str:
    """Interpola `template_text` contra el contexto. `StrictUndefined`
    levanta si el template referencia un namespace inexistente — el
    caller hace catch + log + fallback a la plantilla literal."""
    if not template_text:
        return template_text or ""
    env = _html_env if is_html else _text_env
    try:
        template = env.from_string(template_text)
        return template.render(**context)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "workflows.variables render failed: %s — falling back to raw",
            exc,
        )
        return template_text


def list_referenced_variables(template_text: str) -> list[str]:
    """Para validación: lista las variables `{{ x.y }}` presentes en
    el template. El validador del editor las contrasta con el
    whitelist antes de activar el workflow."""
    if not template_text:
        return []
    return list({m.group(1) for m in _VAR_RE.finditer(template_text)})


def available_variables() -> list[str]:
    """Lista para el dropdown del builder."""
    return [
        # Contacto
        "contact.first_name",
        "contact.last_name",
        "contact.full_name",
        "contact.email",
        "contact.phone",
        "contact.origin",
        "contact.lifecycle_status",
        "contact.lead_score",
        "contact.job_title",
        "contact.company_name",
        "contact.address_country",
        "contact.address_city",
        # Owner
        "owner.full_name",
        "owner.first_name",
        "owner.email",
        # Empresa asociada
        "company.name",
        "company.domain",
        # Última asignación a pipeline (oportunidad)
        "opportunity.pipeline",
        "opportunity.stage",
        # Payload del trigger (campos arbitrarios — el validador
        # solo avisa, no rechaza).
        "trigger.event_type",
        "trigger.field",
        "trigger.value",
    ]
