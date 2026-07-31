"""Procesamiento de un submit de formulario web → lead en BoHub.

Orden (defensa en profundidad + captura fiable):
  1. rate limit por IP        → 429
  2. honeypot                 → 400 spam
  3. reCAPTCHA v3             → 400 spam si score < umbral
  4. validación required+email→ 400
  5. contacto: update-or-create por email (NUNCA duplica, NUNCA pisa
     campos ya rellenos, NUNCA reasigna un owner existente)
  6. tag `form:<slug>` + activity `form.submitted`
  7. `form_submissions` SIEMPRE (spam incluido) para auditoría
  8. email de confirmación al lead + notificación al owner (best-effort)

Las verificaciones anti-spam se inyectan (`verify_recaptcha_fn`,
`rate_limit_fn`) para poder testear sin red/Redis y para el mutation
testing del flujo público.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import ActivityEvent, Contact, User
from app.models.web_forms import FormSubmission, WebForm
from app.repositories import crm as crm_repository
from app.services.web_forms.antispam import (
    check_and_increment_rate_limit,
    honeypot_triggered,
    recaptcha_min_score,
    verify_recaptcha,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Columnas de `contacts` que un formulario puede rellenar directamente.
DIRECT_CONTACT_FIELDS = {
    "email", "first_name", "last_name", "phone", "job_title",
    "personal_website", "linkedin_url", "address_country", "address_city",
    "address_line", "address_postal_code",
}


@dataclass
class SubmitOutcome:
    submission_id: str
    is_spam: bool
    spam_reason: str | None
    contact_id: str | None
    created_contact: bool
    http_status: int
    response: dict[str, Any] = field(default_factory=dict)


def process_submission(
    session: Session,
    *,
    form: WebForm,
    payload: dict[str, Any],
    meta: dict[str, Any],
    verify_recaptcha_fn: Callable[[str | None, str | None], float | None] = verify_recaptcha,
    rate_limit_fn: Callable[[str | None, str], bool] = check_and_increment_rate_limit,
) -> SubmitOutcome:
    ip = meta.get("ip")

    # 1. Rate limit.
    if not rate_limit_fn(ip, form.id):
        return _record_spam(session, form, payload, meta, "rate_limit", http=429)

    # 2. Honeypot.
    if honeypot_triggered(payload):
        return _record_spam(session, form, payload, meta, "honeypot", http=400)

    # 3. reCAPTCHA v3 (solo si el form lo pide).
    score: float | None = None
    if form.recaptcha_enabled:
        score = verify_recaptcha_fn(meta.get("recaptcha_token"), ip)
        if score is not None and score < recaptcha_min_score():
            return _record_spam(
                session, form, payload, meta, "recaptcha_low_score",
                http=400, score=score,
            )

    # Bug 6: prellenar los campos con default_value cuando el submit no
    # los trae (típico de los hidden UTM). Se aplica ANTES de validar y
    # de extraer el contacto, así el default cuenta como valor real.
    for f in form.fields:
        if f.default_value and not str(payload.get(f.field_key) or "").strip():
            payload[f.field_key] = f.default_value

    # 4. Validación: campos requeridos + email válido.
    data, custom, email = _extract_contact_data(form, payload)
    missing = [
        f.field_key for f in form.fields
        if f.is_required and not str(payload.get(f.field_key) or "").strip()
    ]
    if missing:
        return _record_spam(
            session, form, payload, meta, "missing_required", http=400, score=score
        )
    if not email or not _EMAIL_RE.match(email):
        return _record_spam(
            session, form, payload, meta, "invalid_email", http=400, score=score
        )

    # 5. Contacto: update-or-create por email.
    contact, created = _resolve_contact(session, form, email, data, custom)

    # Asignación (solo contacto nuevo o existente-sin-owner; nunca reasigna).
    _apply_assignment(session, form, contact)
    # Tag de origen + evento de historial.
    _apply_form_tag(session, form, contact)
    _record_activity(session, form, contact, payload, meta)

    # 6. Submission (no spam).
    submission = _store_submission(
        session, form, payload, meta,
        contact_id=contact.id, is_spam=False, spam_reason=None, score=score,
    )
    session.commit()

    contact_email = email
    owner_id = contact.owner_user_id

    # 7. Efectos best-effort post-commit (no deben tumbar la respuesta).
    if form.send_confirmation_email:
        _send_confirmation_email(session, form, contact_email, contact)
    if form.notify_owner_on_new and owner_id:
        _notify_owner(session, form, owner_id, contact)

    return SubmitOutcome(
        submission_id=submission.id, is_spam=False, spam_reason=None,
        contact_id=contact.id, created_contact=created, http_status=200,
        response=_success_response(form),
    )


# --- contacto ---------------------------------------------------------------


def _extract_contact_data(
    form: WebForm, payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Traduce el payload a campos de `contacts` según `maps_to_contact_field`
    de cada campo. Devuelve (columnas_directas, custom_fields, email)."""
    data: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    email: str | None = None
    for f in form.fields:
        raw = payload.get(f.field_key)
        value = str(raw).strip() if raw is not None else ""
        if not value:
            continue
        mapping = (f.maps_to_contact_field or "").strip()
        if mapping.startswith("contact.custom."):
            custom[mapping[len("contact.custom."):]] = value
            continue
        col = mapping[len("contact."):] if mapping.startswith("contact.") else ""
        if col == "email":
            email = value.lower()
        elif col in DIRECT_CONTACT_FIELDS:
            data[col] = value
    # Fallback de email: campo key 'email' o el primer campo type email.
    if email is None:
        for f in form.fields:
            if f.field_key == "email" or f.field_type == "email":
                raw = payload.get(f.field_key)
                if raw and str(raw).strip():
                    email = str(raw).strip().lower()
                    break
    if email:
        data["email"] = email
    return data, custom, email


def _resolve_contact(
    session: Session, form: WebForm, email: str,
    data: dict[str, Any], custom: dict[str, Any],
) -> tuple[Contact, bool]:
    existing = session.scalar(
        select(Contact).where(func.lower(Contact.email) == email)
    )
    if existing is not None:
        _update_empty_fields(existing, data, custom)
        session.flush()
        return existing, False

    # Nuevo contacto. first_name es NOT NULL → fallback al local-part.
    first_name = data.get("first_name") or email.split("@")[0] or "Lead web"
    contact = Contact(
        first_name=first_name,
        last_name=data.get("last_name"),
        email=email,
        phone=data.get("phone"),
        origin="web_form",
        origin_account_id=f"web_form:{form.slug}",
    )
    for col, value in data.items():
        if col in {"email", "first_name", "last_name", "phone"}:
            continue
        setattr(contact, col, value)
    if custom:
        contact.custom_fields = json.dumps(custom, default=str)
    session.add(contact)
    session.flush()
    return contact, True


def _update_empty_fields(
    contact: Contact, data: dict[str, Any], custom: dict[str, Any]
) -> None:
    """Rellena SOLO los campos vacíos del contacto — nunca pisa lo que el
    CRM ya tiene (regla de merge de duplicados)."""
    for col, value in data.items():
        if col == "email":
            continue  # el email es la clave de dedup, no se toca
        if not getattr(contact, col, None):
            setattr(contact, col, value)
    if custom:
        try:
            current = json.loads(contact.custom_fields or "{}")
            if not isinstance(current, dict):
                current = {}
        except (TypeError, ValueError):
            current = {}
        for k, v in custom.items():
            current.setdefault(k, v)
        contact.custom_fields = json.dumps(current, default=str)


def _apply_assignment(session: Session, form: WebForm, contact: Contact) -> None:
    """Asigna el contacto según el modo del form. NUNCA reasigna un owner
    existente (si `owner_user_id` ya está, se respeta)."""
    if contact.owner_user_id:
        return
    mode = form.assignment_mode
    if mode == "rules":
        from app.services import assignment_rules  # noqa: PLC0415

        assignment_rules.evaluate_for_contact(session, contact, trigger="web_form")
    elif mode == "fixed_owner" and form.fixed_owner_user_id:
        from app.repositories import assignments as assignments_repo  # noqa: PLC0415

        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=form.fixed_owner_user_id,
            is_primary=True,
            source="web_form",
        )
    # mode == "none" → owner_user_id queda NULL (asignación manual después).


def _apply_form_tag(session: Session, form: WebForm, contact: Contact) -> None:
    tag, _created = crm_repository.upsert_tag(
        session, name=f"form:{form.slug}",
        created_by_user_id=form.created_by_user_id,
    )
    crm_repository.assign_tag_to_contact(
        session, contact_id=contact.id, tag_id=tag.id,
        assigned_by_user_id=None, source="web_form",
    )


def _record_activity(
    session: Session, form: WebForm, contact: Contact,
    payload: dict[str, Any], meta: dict[str, Any],
) -> None:
    from uuid import uuid4  # noqa: PLC0415

    now = datetime.now(UTC)
    session.add(ActivityEvent(
        contact_id=contact.id,
        system="web_forms",
        account_id=form.id,
        external_id=str(uuid4()),
        event_type="form.submitted",
        subject=f"Formulario: {form.name}",
        metadata_json=json.dumps(
            {
                "form_id": form.id, "form_slug": form.slug,
                "payload": _public_payload(payload),
                "utm": {
                    "source": meta.get("utm_source"),
                    "medium": meta.get("utm_medium"),
                    "campaign": meta.get("utm_campaign"),
                },
                "referrer": meta.get("referrer"),
                "landing_page": meta.get("landing_page"),
            },
            default=str,
        ),
        occurred_at=now,
        synced_at=now,
    ))
    session.flush()


# --- submissions / spam -----------------------------------------------------


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Descarta el honeypot y el token reCAPTCHA del payload persistido."""
    drop = {"website", "recaptcha_token", "g-recaptcha-response"}
    return {k: v for k, v in payload.items() if k not in drop}


def _store_submission(
    session: Session, form: WebForm, payload: dict[str, Any],
    meta: dict[str, Any], *, contact_id: str | None, is_spam: bool,
    spam_reason: str | None, score: float | None,
) -> FormSubmission:
    submission = FormSubmission(
        form_id=form.id,
        contact_id=contact_id,
        raw_payload_json=json.dumps(_public_payload(payload), default=str),
        is_spam=is_spam,
        spam_reason=spam_reason,
        recaptcha_score=score,
        ip_address=meta.get("ip"),
        user_agent=(meta.get("user_agent") or None),
        referrer=meta.get("referrer"),
        landing_page=meta.get("landing_page"),
        utm_source=meta.get("utm_source"),
        utm_medium=meta.get("utm_medium"),
        utm_campaign=meta.get("utm_campaign"),
    )
    session.add(submission)
    session.flush()
    return submission


def _record_spam(
    session: Session, form: WebForm, payload: dict[str, Any],
    meta: dict[str, Any], reason: str, *, http: int, score: float | None = None,
) -> SubmitOutcome:
    """Persiste el submit como spam (sin crear contacto) y devuelve el
    outcome de rechazo. Se guarda SIEMPRE para auditoría/debug."""
    submission = _store_submission(
        session, form, payload, meta,
        contact_id=None, is_spam=True, spam_reason=reason, score=score,
    )
    session.commit()
    return SubmitOutcome(
        submission_id=submission.id, is_spam=True, spam_reason=reason,
        contact_id=None, created_contact=False, http_status=http,
        response={"success": False, "error": reason},
    )


def _success_response(form: WebForm) -> dict[str, Any]:
    if form.submit_success_mode == "redirect" and form.submit_redirect_url:
        return {
            "success": True, "action": "redirect",
            "redirect_url": form.submit_redirect_url,
        }
    return {
        "success": True, "action": "modal",
        "success_message": (
            form.submit_success_message
            or "¡Gracias! Hemos recibido tu solicitud."
        ),
    }


# --- emails (best-effort) ---------------------------------------------------


def _send_confirmation_email(
    session: Session, form: WebForm, to_email: str, contact: Contact
) -> None:
    try:
        from app.services.email import get_email_service  # noqa: PLC0415

        subject = f"Gracias por contactar con {form.brand or 'nosotros'}"
        text = (
            "¡Gracias! Hemos recibido tu solicitud y te contactaremos pronto."
        )
        html: str | None = None
        if form.confirmation_email_template_id:
            rendered = _render_template(
                session, form.confirmation_email_template_id, contact
            )
            if rendered is not None:
                subject, html, text = rendered
        get_email_service().send_notification(
            to_email=to_email,
            to_name=contact.first_name or to_email,
            subject=subject,
            text_body=text,
            html_body=html,
        )
    except Exception:  # noqa: BLE001 — un fallo de email no tumba la captura
        logger.warning("web_forms.confirmation_email failed", exc_info=True)


def _render_template(
    session: Session, template_id: str, contact: Contact
) -> tuple[str, str, str] | None:
    try:
        from app.email_templates.models import EmailTemplate  # noqa: PLC0415
        from app.email_templates.services import (  # noqa: PLC0415
            extract_text_from_html,
            replace_merge_vars,
        )

        tpl = session.get(EmailTemplate, template_id)
        if tpl is None:
            return None
        subject = replace_merge_vars(tpl.subject or "", contact)
        html = replace_merge_vars(tpl.body_html or "", contact)
        text = replace_merge_vars(
            tpl.body_text or extract_text_from_html(tpl.body_html or ""), contact
        )
        return subject, html, text
    except Exception:  # noqa: BLE001
        logger.warning("web_forms.template render failed", exc_info=True)
        return None


def _notify_owner(
    session: Session, form: WebForm, owner_id: str, contact: Contact
) -> None:
    try:
        from app.services.email import get_email_service  # noqa: PLC0415

        owner = session.get(User, owner_id)
        if owner is None or not owner.email:
            return
        name = " ".join(
            p for p in [contact.first_name, contact.last_name] if p
        ) or contact.email
        get_email_service().send_notification(
            to_email=owner.email,
            to_name=owner.full_name or owner.email,
            subject=f"Nuevo lead del formulario «{form.name}»",
            text_body=(
                f"Has recibido un nuevo lead: {name} ({contact.email}).\n"
                f"Formulario: {form.name} ({form.slug})."
            ),
        )
    except Exception:  # noqa: BLE001
        logger.warning("web_forms.notify_owner failed", exc_info=True)
