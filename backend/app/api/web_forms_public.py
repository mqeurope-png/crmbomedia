"""Endpoints PÚBLICOS de formularios web (sin auth, CORS abierto).

Consumidos por el widget JS / iframe embebido en cualquier web:
  - GET  /public/forms/{form_id}/config.json  → schema para renderizar.
  - POST /public/forms/{form_id}/submit       → captura el lead.

El CORS abierto (`*`) para este prefijo lo aplica un middleware dedicado
en `app.main` (el CORSMiddleware global sigue restringido a `/api/*`).

NOTA DE DESPLIEGUE: el reverse proxy de producción hoy solo enruta
`/api/*` al backend. Para servir estos endpoints hay que añadir una regla
de proxy para `/public/forms/*` (y `/forms/*` cuando llegue el widget en
PR-B) → backend. Documentado en el PR body.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.models.web_forms import WebForm
from app.services.web_forms import process_submission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/forms", tags=["web-forms-public"])

#: Campos del payload que NO son de negocio (anti-spam / tracking).
_META_KEYS = {
    "website", "recaptcha_token", "g-recaptcha-response",
    "utm_source", "utm_medium", "utm_campaign", "referrer", "landing_page",
}


def _get_active_form(session: Session, form_id: str) -> WebForm:
    form = session.get(WebForm, form_id)
    if form is None or not form.is_active:
        raise not_found("Form")
    return form


@router.get("/{form_id}/config.json")
def form_config(
    form_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Schema público del form para que el widget lo renderice. NO expone
    secretos (recaptcha_secret, asignación, owner) — solo lo necesario
    para pintar y validar client-side. El `recaptcha_site_key` es público
    por diseño."""
    form = _get_active_form(session, form_id)
    settings = get_settings()
    return {
        "id": form.id,
        "slug": form.slug,
        "name": form.name,
        "brand": form.brand,
        "language": form.language,
        "recaptcha_enabled": form.recaptcha_enabled,
        "recaptcha_site_key": (
            settings.recaptcha_site_key if form.recaptcha_enabled else None
        ),
        "submit": {
            "mode": form.submit_success_mode,
            "message": form.submit_success_message,
            "redirect_url": form.submit_redirect_url,
        },
        "fields": [
            {
                "key": f.field_key,
                "label": f.label,
                "type": f.field_type,
                "placeholder": f.placeholder,
                "help_text": f.help_text,
                "required": f.is_required,
                "hidden": f.is_hidden,
                "default_value": f.default_value,
                "options": _parse_options(f.options_json),
                "validation_pattern": f.validation_pattern,
                "position": f.position,
            }
            for f in form.fields
        ],
    }


@router.post("/{form_id}/submit")
async def form_submit(
    form_id: str, request: Request, session: Session = Depends(get_session)
):
    """Recibe el submit, ejecuta el anti-spam + captura del lead, y
    devuelve el JSON que el widget usa para mostrar modal o redirect."""
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    form = _get_active_form(session, form_id)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except (json.JSONDecodeError, ValueError):
        body = {}

    meta = {
        "ip": _client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:512],
        "recaptcha_token": (
            body.get("recaptcha_token") or body.get("g-recaptcha-response")
        ),
        "utm_source": _clip(body.get("utm_source")),
        "utm_medium": _clip(body.get("utm_medium")),
        "utm_campaign": _clip(body.get("utm_campaign")),
        "referrer": _clip(body.get("referrer")),
        "landing_page": _clip(body.get("landing_page")),
    }

    outcome = process_submission(session, form=form, payload=body, meta=meta)
    return JSONResponse(status_code=outcome.http_status, content=outcome.response)


def _client_ip(request: Request) -> str | None:
    # Respeta X-Forwarded-For (primer hop) tras el reverse proxy.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def _clip(value: Any, limit: int = 512) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _parse_options(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []
