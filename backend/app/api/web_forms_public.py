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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session
from app.models.web_forms import WebForm
from app.services.web_forms import apariencia as aparien
from app.services.web_forms import process_submission
from app.services.web_forms.enlaces import texto_con_enlaces
from app.services.web_forms.textos import texto_submit, textos

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/forms", tags=["web-forms-public"])

#: Campos del payload que NO son de negocio (anti-spam / tracking).
_META_KEYS = {
    "website", "recaptcha_token", "g-recaptcha-response",
    "utm_source", "utm_medium", "utm_campaign", "referrer", "landing_page",
}


def _get_active_form(session: Session, form_id: str) -> WebForm:
    """404 si no existe; 403 `form_inactive` si está desactivado (el
    mismo criterio que las vías de render)."""
    from app.api.web_forms_embed import _get_active_form as obtener  # noqa: PLC0415

    return obtener(session, form_id)


@router.get("/by-site/{sitio}/config.json")
def form_config_por_sitio(
    sitio: str,
    lang: str = Query(default="", max_length=16),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """El formulario de una WEB en el idioma de la página: así cada web
    lleva un solo código de inserción en todas sus traducciones. Sin ese
    idioma se sirve el de respaldo de la web (ver `seleccion`)."""
    from app.services.web_forms.seleccion import (  # noqa: PLC0415
        formulario_de_sitio,
    )

    form = formulario_de_sitio(session, sitio, lang)
    if form is None:
        raise HTTPException(status_code=404, detail={
            "code": "site_without_forms",
            "message": f"No hay ningún formulario activo de «{sitio}».",
            "loading_error": textos(lang)["no_cargado"],
        })
    return _config_payload(form)


@router.get("/{form_id}/config.json")
def form_config(
    form_id: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Schema público del form para que el widget lo renderice. NO expone
    secretos (recaptcha_secret, asignación, owner) — solo lo necesario
    para pintar y validar client-side. El `recaptcha_site_key` es público
    por diseño."""
    return _config_payload(_get_active_form(session, form_id))


def _config_payload(form: WebForm) -> dict[str, Any]:
    settings = get_settings()
    ap = aparien.cargar(form.appearance_json)
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
        # Apariencia: texto del botón y CSS ya compuesto por el servidor
        # (solo valores validados). Vacío = el aspecto de siempre.
        "submit_text": texto_submit(form.language, ap.submit_text),
        # Todo lo que ve quien rellena, en el idioma del formulario: el
        # widget ya no lleva textos fijos en castellano.
        "texts": textos(form.language),
        "style_css": aparien.css(ap, via="widget", form_id=form.id),
        "fields": [
            {
                "key": f.field_key,
                "label": f.label,
                "label_html": texto_con_enlaces(f.label),
                "type": f.field_type,
                "placeholder": f.placeholder,
                "help_text": f.help_text,
                "help_html": texto_con_enlaces(f.help_text or ""),
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


@router.get("/{form_id}/html")
def form_pure_html(
    form_id: str, session: Session = Depends(get_session)
):
    """v3 Bug 3. Fragmento HTML puro copiable del formulario (sin
    <html>/<head>/<body>), con clases semánticas SIN estilar, honeypot y
    snippet reCAPTCHA v3 inline. Para pegar en cualquier web y maquetar con
    el CSS propio del sitio."""
    from fastapi.responses import HTMLResponse  # noqa: PLC0415

    from app.api.web_forms_embed import build_pure_html_fragment  # noqa: PLC0415

    form = _get_active_form(session, form_id)
    settings = get_settings()
    base = (
        settings.web_forms_embed_base_url or settings.frontend_base_url
    ).rstrip("/")
    site_key = settings.recaptcha_site_key if form.recaptcha_enabled else None
    fragment = build_pure_html_fragment(form, api_base=base, site_key=site_key)
    return HTMLResponse(content=fragment)


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
