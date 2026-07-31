"""Endpoints ADMIN de formularios web (auth admin/manager).

CRUD de forms + campos, histórico de submissions y generación del embed
code (script JS + iframe). Los comerciales (`user`) no acceden.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import require_manager
from app.core.config import get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import CustomFieldDefinition, User
from app.models.web_forms import (
    ASSIGNMENT_MODES,
    FIELD_TYPES,
    SUBMIT_SUCCESS_MODES,
    FormSubmission,
    WebForm,
    WebFormField,
)

router = APIRouter(prefix="/api/admin/forms", tags=["web-forms-admin"])
# Endpoint auxiliar fuera del prefijo /forms (lo consume el editor).
aux_router = APIRouter(prefix="/api/admin", tags=["web-forms-admin"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# --- schemas ----------------------------------------------------------------


class FormFieldIn(BaseModel):
    # v2 Bug 1: opcional — si viene vacío se autogenera del label
    # (slugify + sufijo si colisiona). Sigue editable manualmente.
    field_key: str = Field(default="", max_length=64)
    label: str = Field(min_length=1, max_length=255)
    field_type: str
    placeholder: str | None = None
    help_text: str | None = None
    is_required: bool = False
    is_hidden: bool = False
    default_value: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    validation_pattern: str | None = None
    position: int = 0
    maps_to_contact_field: str | None = None


class FormFieldRead(FormFieldIn):
    id: str


class FormBase(BaseModel):
    slug: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    brand: str | None = None
    language: str = "es"
    is_active: bool = True
    submit_success_mode: str = "modal"
    submit_success_message: str | None = None
    submit_redirect_url: str | None = None
    send_confirmation_email: bool = False
    confirmation_email_template_id: str | None = None
    assignment_mode: str = "rules"
    fixed_owner_user_id: str | None = None
    notify_owner_on_new: bool = True
    recaptcha_enabled: bool = True


class FormCreate(FormBase):
    fields: list[FormFieldIn] = Field(default_factory=list)


class FormUpdate(FormBase):
    fields: list[FormFieldIn] | None = None


class FormListItem(BaseModel):
    id: str
    slug: str
    name: str
    brand: str | None
    language: str
    is_active: bool
    submissions_total: int
    submissions_spam: int
    created_at: datetime


class FormDetail(FormBase):
    id: str
    created_by_user_id: str
    created_at: datetime
    fields: list[FormFieldRead]


# --- helpers ----------------------------------------------------------------


import unicodedata  # noqa: E402


def _slugify_key(label: str) -> str:
    """`"¿Cómo nos conociste?"` → `"como_nos_conociste"`."""
    norm = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode()
    norm = re.sub(r"[^a-z0-9]+", "_", norm.lower()).strip("_")
    return norm or "campo"


def _autofill_field_keys(fields: list[FormFieldIn] | None) -> None:
    """v2 Bug 1. Rellena `field_key` vacíos con slugify(label) y garantiza
    unicidad dentro del form (sufijo `_2`, `_3`… en colisión, sea el key
    manual o autogenerado). Muta la lista in-place."""
    if not fields:
        return
    seen: set[str] = set()
    for f in fields:
        key = (f.field_key or "").strip() or _slugify_key(f.label)
        base = key
        n = 2
        while key in seen:
            key = f"{base}_{n}"
            n += 1
        seen.add(key)
        f.field_key = key


def _validate_enums(payload: FormBase, fields: list[FormFieldIn] | None) -> None:
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="slug inválido: solo minúsculas, números y guiones.",
        )
    if payload.submit_success_mode not in SUBMIT_SUCCESS_MODES:
        raise HTTPException(400, f"submit_success_mode inválido: {payload.submit_success_mode!r}")
    if payload.assignment_mode not in ASSIGNMENT_MODES:
        raise HTTPException(400, f"assignment_mode inválido: {payload.assignment_mode!r}")
    if payload.assignment_mode == "fixed_owner" and not payload.fixed_owner_user_id:
        raise HTTPException(400, "assignment_mode=fixed_owner requiere fixed_owner_user_id.")
    for f in fields or []:
        if f.field_type not in FIELD_TYPES:
            raise HTTPException(400, f"field_type inválido: {f.field_type!r}")
        # Bug 3: un desplegable sin opciones es inutilizable.
        if f.field_type == "select" and not f.options:
            raise HTTPException(
                400,
                f"El campo «{f.label or f.field_key}» es un desplegable y "
                "necesita al menos una opción.",
            )


def _field_model(form_id: str, f: FormFieldIn) -> WebFormField:
    return WebFormField(
        form_id=form_id,
        field_key=f.field_key,
        label=f.label,
        field_type=f.field_type,
        placeholder=f.placeholder,
        help_text=f.help_text,
        is_required=f.is_required,
        is_hidden=f.is_hidden,
        default_value=f.default_value,
        options_json=json.dumps(f.options) if f.options else None,
        validation_pattern=f.validation_pattern,
        position=f.position,
        maps_to_contact_field=f.maps_to_contact_field,
    )


def _serialise_detail(form: WebForm) -> FormDetail:
    return FormDetail(
        id=form.id,
        slug=form.slug,
        name=form.name,
        brand=form.brand,
        language=form.language,
        is_active=form.is_active,
        submit_success_mode=form.submit_success_mode,
        submit_success_message=form.submit_success_message,
        submit_redirect_url=form.submit_redirect_url,
        send_confirmation_email=form.send_confirmation_email,
        confirmation_email_template_id=form.confirmation_email_template_id,
        assignment_mode=form.assignment_mode,
        fixed_owner_user_id=form.fixed_owner_user_id,
        notify_owner_on_new=form.notify_owner_on_new,
        recaptcha_enabled=form.recaptcha_enabled,
        created_by_user_id=form.created_by_user_id,
        created_at=form.created_at,
        fields=[
            FormFieldRead(
                id=f.id, field_key=f.field_key, label=f.label,
                field_type=f.field_type, placeholder=f.placeholder,
                help_text=f.help_text, is_required=f.is_required,
                is_hidden=f.is_hidden, default_value=f.default_value,
                options=_parse_options(f.options_json),
                validation_pattern=f.validation_pattern, position=f.position,
                maps_to_contact_field=f.maps_to_contact_field,
            )
            for f in sorted(form.fields, key=lambda x: x.position)
        ],
    )


def _parse_options(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _get_form(session: Session, form_id: str) -> WebForm:
    form = session.scalar(
        select(WebForm).where(WebForm.id == form_id)
        .options(selectinload(WebForm.fields))
    )
    if form is None:
        raise not_found("Form")
    return form


# Campos estándar del contacto a los que un form puede mapear. Espejo del
# whitelist `DIRECT_CONTACT_FIELDS` del motor de submit — solo estos se
# guardan de verdad en columnas del contacto (el resto va a raw_payload).
_STANDARD_MAPPABLE: list[tuple[str, str, str]] = [
    ("contact.first_name", "Nombre", "text"),
    ("contact.last_name", "Apellidos", "text"),
    ("contact.email", "Email", "email"),
    ("contact.phone", "Teléfono", "tel"),
    ("contact.job_title", "Cargo", "text"),
    ("contact.personal_website", "Sitio web", "text"),
    ("contact.linkedin_url", "LinkedIn", "text"),
    ("contact.address_country", "País", "text"),
    ("contact.address_city", "Ciudad", "text"),
    ("contact.address_line", "Dirección", "text"),
    ("contact.address_postal_code", "Código postal", "text"),
]


@aux_router.get("/contact-fields-mappable")
def contact_fields_mappable(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, list[dict[str, str]]]:
    """Campos del contacto a los que se puede mapear cada campo del form:
    estándar (columnas directas) + custom fields definidos en
    /admin/custom-fields. El editor los pinta en el dropdown «Mapear a»."""
    _ = current_user
    standard = [
        {"value": value, "label": label, "type": ftype, "group": "standard"}
        for value, label, ftype in _STANDARD_MAPPABLE
    ]
    custom = [
        {
            "value": f"contact.custom.{cf.key}",
            "label": f"{cf.label or cf.key} (personalizado)",
            "type": cf.field_type or "text",
            "group": "custom",
        }
        for cf in session.scalars(
            select(CustomFieldDefinition).order_by(CustomFieldDefinition.key)
        )
    ]
    return {"standard": standard, "custom": custom}


@aux_router.get("/tags-selectable")
def tags_selectable(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[dict[str, str | None]]:
    """v2 Bug 2. Tags del CRM disponibles como opciones de un campo tipo
    `tags`. El editor lo usa como autocomplete."""
    _ = current_user
    from app.models.crm import Tag  # noqa: PLC0415

    stmt = select(Tag)
    if search and search.strip():
        stmt = stmt.where(Tag.name_normalized.like(f"%{search.strip().lower()}%"))
    rows = list(session.scalars(stmt.order_by(Tag.name).limit(limit)))
    return [{"id": t.id, "name": t.name, "color": t.color} for t in rows]


# --- endpoints --------------------------------------------------------------


@router.get("", response_model=list[FormListItem])
def list_forms(
    brand: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    language: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> list[FormListItem]:
    _ = current_user
    stmt = select(WebForm)
    if brand is not None:
        stmt = stmt.where(WebForm.brand == brand)
    if is_active is not None:
        stmt = stmt.where(WebForm.is_active.is_(is_active))
    if language is not None:
        stmt = stmt.where(WebForm.language == language)
    forms = list(session.scalars(stmt.order_by(WebForm.created_at.desc())))

    # Conteos por form en una sola query (total + spam).
    counts = dict(
        session.execute(
            select(FormSubmission.form_id, func.count(FormSubmission.id))
            .group_by(FormSubmission.form_id)
        ).all()
    )
    spam_counts = dict(
        session.execute(
            select(FormSubmission.form_id, func.count(FormSubmission.id))
            .where(FormSubmission.is_spam.is_(True))
            .group_by(FormSubmission.form_id)
        ).all()
    )
    return [
        FormListItem(
            id=f.id, slug=f.slug, name=f.name, brand=f.brand,
            language=f.language, is_active=f.is_active,
            submissions_total=int(counts.get(f.id, 0)),
            submissions_spam=int(spam_counts.get(f.id, 0)),
            created_at=f.created_at,
        )
        for f in forms
    ]


@router.post("", response_model=FormDetail, status_code=201)
def create_form(
    payload: FormCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> FormDetail:
    _autofill_field_keys(payload.fields)
    _validate_enums(payload, payload.fields)
    if session.scalar(select(WebForm.id).where(WebForm.slug == payload.slug)):
        raise HTTPException(status_code=409, detail=f"slug ya existe: {payload.slug!r}")
    form = WebForm(
        slug=payload.slug, name=payload.name, brand=payload.brand,
        language=payload.language, is_active=payload.is_active,
        submit_success_mode=payload.submit_success_mode,
        submit_success_message=payload.submit_success_message,
        submit_redirect_url=payload.submit_redirect_url,
        send_confirmation_email=payload.send_confirmation_email,
        confirmation_email_template_id=payload.confirmation_email_template_id,
        assignment_mode=payload.assignment_mode,
        fixed_owner_user_id=payload.fixed_owner_user_id,
        notify_owner_on_new=payload.notify_owner_on_new,
        recaptcha_enabled=payload.recaptcha_enabled,
        created_by_user_id=current_user.id,
    )
    session.add(form)
    session.flush()
    for f in payload.fields:
        session.add(_field_model(form.id, f))
    session.commit()
    return _serialise_detail(_get_form(session, form.id))


@router.get("/{form_id}", response_model=FormDetail)
def get_form(
    form_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> FormDetail:
    _ = current_user
    return _serialise_detail(_get_form(session, form_id))


@router.patch("/{form_id}", response_model=FormDetail)
def update_form(
    form_id: str,
    payload: FormUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> FormDetail:
    _ = current_user
    form = _get_form(session, form_id)
    _autofill_field_keys(payload.fields)
    _validate_enums(payload, payload.fields)
    if payload.slug != form.slug and session.scalar(
        select(WebForm.id).where(WebForm.slug == payload.slug, WebForm.id != form_id)
    ):
        raise HTTPException(status_code=409, detail=f"slug ya existe: {payload.slug!r}")
    for attr in (
        "slug", "name", "brand", "language", "is_active",
        "submit_success_mode", "submit_success_message", "submit_redirect_url",
        "send_confirmation_email", "confirmation_email_template_id",
        "assignment_mode", "fixed_owner_user_id", "notify_owner_on_new",
        "recaptcha_enabled",
    ):
        setattr(form, attr, getattr(payload, attr))
    # Reemplazo total de campos (delete + insert) en la misma transacción.
    if payload.fields is not None:
        for existing in list(form.fields):
            session.delete(existing)
        session.flush()
        for f in payload.fields:
            session.add(_field_model(form.id, f))
    session.commit()
    return _serialise_detail(_get_form(session, form_id))


@router.delete("/{form_id}", response_model=dict)
def delete_form(
    form_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    _ = current_user
    form = _get_form(session, form_id)
    form.is_active = False  # soft delete
    session.commit()
    return {"message": "deactivated", "id": form_id}


@router.get("/{form_id}/submissions")
def list_submissions(
    form_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    is_spam: bool | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, Any]:
    _ = current_user
    _get_form(session, form_id)
    stmt = select(FormSubmission).where(FormSubmission.form_id == form_id)
    if is_spam is not None:
        stmt = stmt.where(FormSubmission.is_spam.is_(is_spam))
    rows = list(
        session.scalars(stmt.order_by(FormSubmission.created_at.desc()).limit(limit))
    )
    return {
        "items": [
            {
                "id": s.id,
                "contact_id": s.contact_id,
                "is_spam": s.is_spam,
                "spam_reason": s.spam_reason,
                "recaptcha_score": (
                    float(s.recaptcha_score) if s.recaptcha_score is not None else None
                ),
                "ip_address": s.ip_address,
                "utm_source": s.utm_source,
                "utm_medium": s.utm_medium,
                "utm_campaign": s.utm_campaign,
                "referrer": s.referrer,
                "landing_page": s.landing_page,
                "payload": _parse_payload(s.raw_payload_json),
                "created_at": s.created_at.isoformat(),
            }
            for s in rows
        ],
    }


@router.get("/{form_id}/embed-code")
def embed_code(
    form_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_manager),
) -> dict[str, str]:
    _ = current_user
    form = _get_form(session, form_id)
    settings = get_settings()
    base = (settings.web_forms_embed_base_url or settings.frontend_base_url).rstrip("/")
    script_snippet = (
        f'<script src="{base}/forms/embed/{form.id}.js" async></script>\n'
        f'<div data-bohub-form="{form.id}"></div>'
    )
    iframe_snippet = (
        f'<iframe src="{base}/forms/{form.id}" width="100%" height="600" '
        f'frameborder="0" style="border:0;max-width:100%"></iframe>'
    )
    return {"script_snippet": script_snippet, "iframe_snippet": iframe_snippet}


def _parse_payload(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}
