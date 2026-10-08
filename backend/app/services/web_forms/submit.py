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

from app.models.crm import ActivityEvent, Contact
from app.models.web_forms import ASSIGNMENT_MODES, FormSubmission, WebForm
from app.repositories import crm as crm_repository
from app.services.web_forms.antispam import (
    check_and_increment_rate_limit,
    honeypot_triggered,
    recaptcha_min_score,
    verify_recaptcha,
)
from app.services.web_forms.sitios import (
    origen_de,
    origen_legible,
)
from app.services.web_forms.textos import normalizar_idioma

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Columnas de `contacts` que un formulario puede rellenar directamente.
DIRECT_CONTACT_FIELDS = {
    "email", "first_name", "last_name", "phone", "job_title",
    "personal_website", "linkedin_url", "address_country", "address_city",
    "address_line", "address_postal_code",
}

#: Estados comerciales válidos (misma lista que la UI del contacto).
#: `lifecycle_status` es un alias de `commercial_status` en este modelo.
ALLOWED_COMMERCIAL_STATUS = {"new", "qualified", "working", "won", "lost"}

#: Targets mapeables que NO son columnas string directas — tienen lógica
#: propia (notas=append, empresa=lookup/create, etc.) en
#: `_apply_mapped_special_fields`. `_extract_contact_data` los ignora.
SPECIAL_MAPPINGS = {
    "contact.notes", "contact.lead_score", "contact.stars",
    "contact.commercial_status", "contact.lifecycle_status",
    "contact.company_id", "contact.marketing_consent",
}

#: Valores de una casilla marcada (el navegador manda «on» sin `value`).
CHECKBOX_TRUE = {"on", "true", "1", "si", "sí", "yes", "y", "x"}

#: Envíos bloqueados sin payload (bots que golpean la URL pública) que se
#: borran pasados estos días.
PURGA_BLOQUEADOS_DIAS = 90


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
        # El consentimiento comercial solo vale si la persona marca la
        # casilla: un valor por defecto lo daría aunque no la marcara.
        if (f.maps_to_contact_field or "").strip() == "contact.marketing_consent":
            continue
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
    # Tag de origen + tags seleccionados en campos tipo `tags` + historial.
    _apply_form_tag(session, form, contact)
    etiquetas = _apply_tag_fields(session, form, contact, payload)
    # v3 Bugs 1+2: notas (append), empresa (lookup/create), lead_score,
    # estrellas y estado comercial mapeados. Muta `payload` si registra un
    # intento de cambio de empresa descartado (queda en raw_payload).
    _apply_mapped_special_fields(session, form, contact, payload)
    # Después de la empresa (la crea `_apply_mapped_special_fields`).
    _idioma_del_formulario(contact, form)
    _record_activity(session, form, contact, payload, meta)

    # 6. Submission (no spam). v3 Bug 4: marca created/updated para el badge.
    submission = _store_submission(
        session, form, payload, meta,
        contact_id=contact.id, is_spam=False, spam_reason=None, score=score,
        contact_action="created" if created else "updated",
    )
    session.commit()

    contact_email = email

    # 7. Efectos best-effort post-commit (no deben tumbar la respuesta).
    if form.send_confirmation_email:
        _send_confirmation_email(session, form, contact_email, contact,
                                 payload, etiquetas)
    # Aviso del lead: al comercial asignado si lo tiene y, si no, a la
    # dirección fija. Lo gobierna el mismo interruptor de siempre.
    if form.notify_owner_on_new:
        from app.services.web_forms.aviso import enviar_aviso_lead  # noqa: PLC0415

        enviar_aviso_lead(session, form, contact, payload,
                          etiquetas=etiquetas, nuevo=created)

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
        if f.field_type == "tags":
            continue  # los tags se aplican aparte (_apply_tag_fields)
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
        # El origen identifica el formulario: la web (la clave del slug, no
        # la marca) y el idioma. Legible en la ficha, y filtrable y
        # segmentable por `origin_account_id` (`web_form:<sitio>:<idioma>`).
        origin=origen_legible(form.slug, form.language),
        origin_account_id=origen_de(form.slug, form.language),
        language=normalizar_idioma(form.language),
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


def _idioma_del_formulario(contact: Contact, form: WebForm) -> None:
    """El idioma del formulario, en el contacto y —si está vacío— en su
    empresa: quien escribió en neerlandés no debe recibir después un correo
    en castellano (la cascada de idioma del ERP lee la empresa). Nunca pisa
    un idioma ya puesto."""
    idioma = normalizar_idioma(form.language)
    if not contact.language:
        contact.language = idioma
    empresa = getattr(contact, "company", None)
    if empresa is not None and not empresa.language:
        empresa.language = idioma


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
    elif mode == "fixed_owner":
        if not form.fixed_owner_user_id:
            logger.warning(
                "web_forms.asignacion: el formulario %s (%s) es de propietario "
                "fijo pero no tiene ninguno puesto; el lead %s queda sin "
                "comercial", form.slug, form.id, contact.id,
            )
            return
        from app.repositories import assignments as assignments_repo  # noqa: PLC0415

        assignments_repo.add_assignment(
            session,
            contact_id=contact.id,
            user_id=form.fixed_owner_user_id,
            is_primary=True,
            source="web_form",
        )
    elif mode != "none":
        # «none» sí es un modo: el lead se asigna a mano después. Cualquier
        # otra cosa es un valor que el motor no entiende, y antes no hacía
        # nada ni lo decía: los 25 formularios estuvieron días con «fixed»
        # en vez de «fixed_owner» y ningún lead se asignó a nadie.
        logger.warning(
            "web_forms.asignacion: assignment_mode desconocido %r en el "
            "formulario %s (%s); el lead %s queda sin comercial. Válidos: %s",
            mode, form.slug, form.id, contact.id, ", ".join(sorted(ASSIGNMENT_MODES)),
        )


def _apply_form_tag(session: Session, form: WebForm, contact: Contact) -> None:
    tag, _created = crm_repository.upsert_tag(
        session, name=f"form:{form.slug}",
        created_by_user_id=form.created_by_user_id,
    )
    crm_repository.assign_tag_to_contact(
        session, contact_id=contact.id, tag_id=tag.id,
        assigned_by_user_id=None, source="web_form",
    )


def _coerce_tag_ids(raw: Any) -> list[str]:
    """El payload de un campo `tags` llega como lista de tag_ids; toleramos
    también un string separado por comas (form-encoding)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _apply_tag_fields(
    session: Session, form: WebForm, contact: Contact, payload: dict[str, Any]
) -> list[str]:
    """v2 Bug 2. Campos tipo `tags`: aplica al contacto los tags reales del
    CRM seleccionados (por tag_id). Idempotente (assign_tag_to_contact) +
    audit `contact_tag.added` con via=form. No reasigna nada más."""
    aplicados: list[str] = []
    for f in form.fields:
        if f.field_type != "tags":
            continue
        valores = _coerce_tag_ids(
            payload.get(f.field_key) or payload.get(f"{f.field_key}[]")
        )
        for valor in valores:
            tag = _resolver_tag(session, f, valor)
            if tag is None:
                logger.warning("web_forms: etiqueta %r del campo %s (%s) no corresponde a "
                               "ninguna opción", valor, f.field_key, form.slug)
                continue
            linked = crm_repository.assign_tag_to_contact(
                session, contact_id=contact.id, tag_id=tag.id,
                assigned_by_user_id=None, source="form",
            )
            if linked:
                _audit_form_tag(session, form, contact, tag)
            aplicados.append(tag.name)
    if aplicados:
        _sumar_tags_csv(contact, aplicados)
    return aplicados


def _opciones_tags(f: Any) -> list[dict[str, Any]]:
    try:
        opciones = json.loads(f.options_json or "[]")
    except (TypeError, ValueError):
        return []
    return [o for o in opciones if isinstance(o, dict)] if isinstance(opciones, list) else []


def _resolver_tag(session: Session, f: Any, valor: str) -> Any | None:
    """La etiqueta del CRM que corresponde a un valor enviado en un campo
    `tags`. Solo vale una de las opciones del campo (nadie puede colar otra
    etiqueta desde fuera). La opción se reconoce por su `tag_id`, su `value`
    o su texto; la etiqueta, por id o, si ese id ya no existe (se borró y se
    volvió a crear), por nombre."""
    from app.models.crm import Tag  # noqa: PLC0415

    buscado = valor.strip().lower()
    for o in _opciones_tags(f):
        claves = {str(o.get(k) or "").strip().lower() for k in ("tag_id", "value", "label")}
        if buscado not in claves - {""}:
            continue
        tag = session.get(Tag, str(o.get("tag_id") or "")) if o.get("tag_id") else None
        if tag is None:
            nombre = str(o.get("label") or o.get("value") or "").strip()
            if nombre:
                tag = session.scalar(select(Tag).where(
                    Tag.name_normalized == crm_repository.normalize_tag_name(nombre)))
        return tag
    return None


def _sumar_tags_csv(contact: Contact, nombres: list[str]) -> None:
    """La columna antigua `contacts.tags` (CSV) se mantiene en paralelo,
    como hace el paso de workflow: segmentos, condiciones y GDPR aún la leen."""
    actuales = [t.strip() for t in (contact.tags or "").split(",") if t.strip()]
    vistos = {t.lower() for t in actuales}
    for nombre in nombres:
        if nombre.lower() not in vistos:
            actuales.append(nombre)
            vistos.add(nombre.lower())
    contact.tags = ",".join(actuales)[:500]


def _audit_form_tag(session: Session, form: WebForm, contact: Contact, tag: Any) -> None:
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session,
            action="contact_tag.added",
            target_type="contact",
            target_id=contact.id,
            metadata={
                "tag_id": tag.id, "tag_name": tag.name,
                "via": "form", "form_id": form.id, "form_slug": form.slug,
            },
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea la captura
        logger.warning("web_forms.audit form tag failed", exc_info=True)


# --- campos mapeados con lógica especial (v3 Bugs 1+2) ----------------------


def _apply_mapped_special_fields(
    session: Session, form: WebForm, contact: Contact, payload: dict[str, Any]
) -> None:
    """Aplica los campos mapeados a targets que NO son columnas string
    directas: notas (append con timestamp), lead_score, estrellas, estado
    comercial y empresa (lookup/create). Respeta la regla de no pisar lo ya
    poblado — EXCEPTO notas, que siempre añade una entrada nueva."""
    for f in form.fields:
        target = (f.maps_to_contact_field or "").strip()
        if target not in SPECIAL_MAPPINGS:
            continue
        raw = payload.get(f.field_key)
        value = str(raw).strip() if raw is not None else ""
        if not value:
            continue
        if target == "contact.notes":
            _append_contact_note(session, form, contact, value)
        elif target == "contact.lead_score":
            _apply_lead_score(contact, value)
        elif target == "contact.stars":
            _apply_stars(contact, value)
        elif target in ("contact.commercial_status", "contact.lifecycle_status"):
            _apply_commercial_status(contact, value)
        elif target == "contact.company_id":
            _apply_company(session, contact, value, payload)
        elif target == "contact.marketing_consent":
            _apply_marketing_consent(session, form, contact, value)
    session.flush()


def _apply_marketing_consent(session: Session, form: WebForm, contact: Contact,
                             value: str) -> None:
    """Casilla «Acepto recibir comunicaciones comerciales»: marcada, el
    contacto da su consentimiento (aunque antes lo hubiera retirado: es un
    consentimiento nuevo y explícito). Sin marcar no llega el campo y no se
    toca nada."""
    from app.models.crm import ConsentStatus  # noqa: PLC0415

    if value.strip().lower() not in CHECKBOX_TRUE:
        return
    antes = contact.marketing_consent
    if antes == ConsentStatus.GRANTED:
        return
    contact.marketing_consent = ConsentStatus.GRANTED
    try:
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action="contact.marketing_consent", target_type="contact",
            target_id=contact.id,
            metadata={"antes": getattr(antes, "value", antes), "despues": "granted",
                      "via": "form", "form_id": form.id, "form_slug": form.slug},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea la captura
        logger.warning("web_forms.audit marketing consent failed", exc_info=True)


def _append_contact_note(
    session: Session, form: WebForm, contact: Contact, text: str
) -> None:
    """`contact.notes` es una tabla timeline (no un campo escalar): cada
    submit añade una NOTA nueva con timestamp + nombre del form. Nunca pisa
    notas anteriores. Es el append semántico que pide el spec e idiomático
    para el timeline del contacto."""
    from app.models.crm import Note  # noqa: PLC0415

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    body = f"--- {stamp} · Formulario: {form.name} ---\n{text}"
    session.add(Note(contact_id=contact.id, body=body, source="web_form"))


def _apply_lead_score(contact: Contact, value: str) -> None:
    if contact.lead_score is not None:
        return  # no pisa un score ya asignado
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return  # entrada no numérica → se ignora
    if 0 <= n <= 100:
        contact.lead_score = n


def _apply_stars(contact: Contact, value: str) -> None:
    """`contact.stars` → columna `star_rating` (Integer 1-5). Convierte el
    string del form a int; ignora (con warning) lo no numérico o fuera de
    rango sin romper el submit. No pisa una valoración ya asignada."""
    if contact.star_rating:
        return  # no pisa una valoración ya asignada
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        logger.warning("web_forms.stars: valor no numérico ignorado: %r", value)
        return
    if 1 <= n <= 5:
        contact.star_rating = n
    else:
        logger.warning("web_forms.stars: valor fuera de rango 1-5 ignorado: %r", value)


def _apply_commercial_status(contact: Contact, value: str) -> None:
    """Solo aplica si el contacto está sin tocar (default `new`) — nunca
    degrada un estado ya avanzado (qualified/won/…)."""
    current = (contact.commercial_status or "").strip()
    if current and current != "new":
        return
    if value in ALLOWED_COMMERCIAL_STATUS:
        contact.commercial_status = value


def _apply_company(
    session: Session, contact: Contact, name: str, payload: dict[str, Any]
) -> None:
    """Empresa por nombre (case-insensitive): la encuentra o la crea con
    `source='web_form'`. Si el contacto YA tiene empresa, no la pisa y deja
    constancia del intento en el raw_payload de la submission."""
    from app.models.crm import Company  # noqa: PLC0415

    if contact.company_id:
        payload["_company_change_skipped"] = name
        return
    existing = session.scalar(
        select(Company).where(func.lower(Company.name) == name.lower())
    )
    if existing is None:
        existing = Company(name=name, source="web_form")
        session.add(existing)
        session.flush()
    contact.company_id = existing.id


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
    contact_action: str | None = None,
) -> FormSubmission:
    submission = FormSubmission(
        form_id=form.id,
        contact_id=contact_id,
        raw_payload_json=json.dumps(_public_payload(payload), default=str),
        is_spam=is_spam,
        spam_reason=spam_reason,
        contact_action=contact_action,
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
        contact_action="spam",
    )
    purgar_bloqueados(session, form_id=form.id)
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
    session: Session, form: WebForm, to_email: str, contact: Contact,
    payload: dict[str, Any], etiquetas: list[str] | None = None,
) -> None:
    """El acuse va por el camino de la Bandeja (`web_forms/acuse.py`): desde el
    remitente de su web, con `Reply-To` del comercial, y queda en la ficha del
    contacto, en Enviados y con seguimiento de apertura.

    Un fallo NO tumba la captura: el lead ya está guardado y comiteado. Queda
    registrado con su motivo, que es lo que permite enterarse de que un alias
    no está sincronizado o de que Gmail se ha caído."""
    _ = to_email      # el destinatario lo resuelve el acuse desde el contacto
    try:
        from app.services.web_forms.acuse import enviar_acuse  # noqa: PLC0415

        enviar_acuse(session, form, contact, payload, etiquetas=etiquetas)
        session.commit()
    except Exception:  # noqa: BLE001 — un fallo de email no tumba la captura
        session.rollback()
        logger.warning(
            "web_forms.acuse: no se pudo enviar el acuse del formulario %s al "
            "lead %s", form.slug, contact.id, exc_info=True,
        )


def purgar_bloqueados(session: Session, *, form_id: str | None = None,
                      ahora: datetime | None = None) -> int:
    """Borra los envíos bloqueados SIN payload (bots que golpean la URL
    pública sin pasar por ninguna página) de hace más de 90 días. Los que
    traen datos se quedan, para poder revisarlos. Se llama al registrar un
    bloqueo (barato: índice por formulario y fecha)."""
    from datetime import timedelta  # noqa: PLC0415

    from sqlalchemy import delete  # noqa: PLC0415

    limite = (ahora or datetime.now(UTC)) - timedelta(days=PURGA_BLOQUEADOS_DIAS)
    stmt = delete(FormSubmission).where(
        FormSubmission.is_spam.is_(True),
        FormSubmission.raw_payload_json.in_(("{}", "")),
        FormSubmission.created_at < limite.replace(tzinfo=None),
    )
    if form_id is not None:
        stmt = stmt.where(FormSubmission.form_id == form_id)
    return session.execute(stmt).rowcount or 0
