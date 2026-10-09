"""La fila de `lead_classifications` y la copia en el contacto.

- `entrada_desde_payload`: la `EntradaLead` a partir del evento
  `lead.received` que disparó el workflow.
- `entrada_desde_contacto`: la misma a partir de lo que hay en la ficha
  (último envío de formulario no spam, o última nota «form note» de Agile),
  para cuando el workflow se lanzó a mano.
- `registrar`: guarda la clasificación UNA vez por lead y copia lo esencial
  al contacto.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import Contact, Note
from app.models.leads import (
    ESTADO_CLASIFICADO,
    ESTADO_SPAM,
    FUENTE_AGILE,
    FUENTE_FORMULARIO,
    LeadClassification,
)
from app.models.web_forms import FormSubmission, WebForm
from app.services.leads.clasificador import Clasificacion, EntradaLead

logger = logging.getLogger(__name__)


# --- lo que entra ----------------------------------------------------------


def _fecha(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    if not valor:
        return None
    try:
        parsed = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def entrada_desde_payload(payload: dict[str, Any], contact: Contact) -> EntradaLead | None:
    """La entrada a partir del payload de `lead.received`. `None` si el run
    no lo disparó ese evento."""
    if not payload or payload.get("source") not in (FUENTE_FORMULARIO, FUENTE_AGILE):
        return None
    referencia = str(payload.get("source_ref") or "").strip()
    if not referencia:
        return None
    productos = payload.get("products") or []
    if not isinstance(productos, list):
        productos = []
    return EntradaLead(
        texto=str(payload.get("text") or ""),
        fuente=str(payload["source"]),
        referencia=referencia,
        lead_at=_fecha(payload.get("lead_at")),
        sitio=(payload.get("site") or None),
        formulario=(payload.get("form_slug") or None),
        idioma_formulario=(payload.get("form_language") or None),
        productos=[str(p) for p in productos if str(p).strip()],
        pais=(payload.get("country") or contact.address_country or None),
        cuenta_agile=(payload.get("agile_account_id") or None),
        email=contact.email,
    )


def texto_de_envio(form: WebForm, payload: dict[str, Any]) -> str:
    """La consulta de un envío: los campos de texto largo y los que van a
    notas; si no hay, el resto de campos que no son datos del contacto."""
    consulta: list[str] = []
    resto: list[str] = []
    de_contacto = {"contact.first_name", "contact.last_name", "contact.email",
                   "contact.phone", "contact.marketing_consent", "contact.company_id"}
    for f in sorted(form.fields, key=lambda x: x.position):
        raw = payload.get(f.field_key)
        if raw is None:
            raw = payload.get(f"{f.field_key}[]")
        valor = (", ".join(str(v).strip() for v in raw if str(v).strip())
                 if isinstance(raw, (list, tuple)) else str(raw or "").strip())
        if not valor or f.is_hidden or f.field_type in {"hidden", "tags"}:
            continue
        destino = (f.maps_to_contact_field or "").strip()
        if f.field_type == "textarea" or destino == "contact.notes":
            consulta.append(valor)
        elif destino not in de_contacto and f.field_type not in {"email", "tel"}:
            resto.append(f"{f.label or f.field_key}: {valor}")
    return "\n\n".join(consulta) if consulta else "\n".join(resto)


def productos_de_envio(session: Session, form: WebForm, payload: dict[str, Any]) -> list[str]:
    """Nombres de las etiquetas marcadas en los campos `tags` del envío."""
    from app.services.web_forms.submit import _coerce_tag_ids, _resolver_tag  # noqa: PLC0415

    nombres: list[str] = []
    for f in form.fields:
        if f.field_type != "tags":
            continue
        for valor in _coerce_tag_ids(payload.get(f.field_key) or payload.get(f"{f.field_key}[]")):
            tag = _resolver_tag(session, f, valor)
            if tag is not None and tag.name not in nombres:
                nombres.append(tag.name)
    return nombres


def entrada_desde_contacto(session: Session, contact: Contact) -> EntradaLead | None:
    """Lo último que entró de este contacto: el envío de formulario no spam
    más reciente, o la nota «form note» de Agile más reciente."""
    from app.services.leads.eventos import es_nota_de_formulario, texto_de_nota  # noqa: PLC0415
    from app.services.web_forms.sitios import clave_de_sitio  # noqa: PLC0415

    envio = session.scalar(
        select(FormSubmission)
        .where(FormSubmission.contact_id == contact.id, FormSubmission.is_spam.is_(False))
        .order_by(FormSubmission.created_at.desc())
        .limit(1)
    )
    nota = next((
        n for n in session.scalars(
            select(Note)
            .where(Note.contact_id == contact.id, Note.external_system == FUENTE_AGILE)
            .order_by(Note.external_created_at.desc(), Note.created_at.desc())
            .limit(20)
        ) if es_nota_de_formulario(n.body)
    ), None)
    fecha_envio = _fecha(envio.created_at) if envio is not None else None
    fecha_nota = (_fecha(nota.external_created_at or nota.created_at)
                  if nota is not None else None)
    if envio is not None and (nota is None or (fecha_envio or datetime.min.replace(tzinfo=UTC))
                              >= (fecha_nota or datetime.min.replace(tzinfo=UTC))):
        form = session.get(WebForm, envio.form_id)
        try:
            payload = json.loads(envio.raw_payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        if form is None or not isinstance(payload, dict):
            return None
        return EntradaLead(
            texto=texto_de_envio(form, payload), fuente=FUENTE_FORMULARIO,
            referencia=envio.id, lead_at=fecha_envio, sitio=clave_de_sitio(form.slug),
            formulario=form.slug, idioma_formulario=form.language,
            productos=productos_de_envio(session, form, payload),
            pais=contact.address_country, email=contact.email,
        )
    if nota is not None:
        return EntradaLead(
            texto=texto_de_nota(nota.body), fuente=FUENTE_AGILE, referencia=nota.id,
            lead_at=fecha_nota, cuenta_agile=nota.external_account_id,
            pais=contact.address_country, email=contact.email,
        )
    return None


# --- lo que se guarda -------------------------------------------------------


def clasificacion_existente(session: Session, entrada: EntradaLead) -> LeadClassification | None:
    return session.scalar(
        select(LeadClassification).where(
            LeadClassification.source == entrada.fuente,
            LeadClassification.source_ref == entrada.referencia,
        )
    )


def ultima_clasificacion(session: Session, contact_id: str) -> LeadClassification | None:
    return session.scalar(
        select(LeadClassification)
        .where(LeadClassification.contact_id == contact_id)
        .order_by(LeadClassification.created_at.desc())
        .limit(1)
    )


def copiar_al_contacto(contact: Contact, fila: LeadClassification) -> None:
    contact.lead_interest = fila.interes_efectivo
    contact.lead_is_spam = fila.es_spam_efectivo
    contact.lead_confidence = fila.confidence
    contact.lead_classified_at = fila.corrected_at or fila.created_at or datetime.now(UTC)


def registrar(
    session: Session, contact: Contact, entrada: EntradaLead, clasificacion: Clasificacion,
    *, run_id: str | None = None,
) -> LeadClassification:
    """Guarda la clasificación de ESTE lead. Si ya existía (mismo envío o
    nota), devuelve la existente sin tocarla: un lead se clasifica una vez."""
    existente = clasificacion_existente(session, entrada)
    if existente is not None:
        return existente
    ahora = datetime.now(UTC)
    fila = LeadClassification(
        contact_id=contact.id, source=entrada.fuente, source_ref=entrada.referencia,
        lead_at=entrada.lead_at, input_text=(entrada.texto or "")[:20000],
        input_json=json.dumps(entrada.contexto(), default=str),
        language=clasificacion.idioma, language_source=clasificacion.idioma_fuente,
        language_mismatch=clasificacion.discrepancia_idioma,
        form_language=entrada.idioma_formulario, interest=clasificacion.interes,
        interest_source=clasificacion.interes_fuente, is_spam=clasificacion.es_spam,
        confidence=clasificacion.confianza, reason=(clasificacion.motivo or "")[:500],
        provider=clasificacion.proveedor, model=clasificacion.modelo,
        status=ESTADO_SPAM if clasificacion.es_spam else ESTADO_CLASIFICADO,
        workflow_run_id=run_id,
    )
    fila.created_at = ahora
    fila.updated_at = ahora
    session.add(fila)
    session.flush()
    copiar_al_contacto(contact, fila)
    try:
        from app.core.audit import Action, record_event  # noqa: PLC0415

        record_event(
            session, action=Action.LEAD_CLASSIFIED, target_type="contact",
            target_id=contact.id,
            metadata={
                "lead_classification_id": fila.id, "source": fila.source,
                "source_ref": fila.source_ref, **clasificacion.como_dict(),
                "run_id": run_id,
            },
        )
    except Exception:  # noqa: BLE001 — la auditoría nunca bloquea
        logger.warning("leads.audit clasificación no registrada", exc_info=True)
    session.flush()
    return fila


def vincular_tarea(session: Session, run_id: str | None, task_id: str) -> bool:
    """La tarea que creó el workflow queda enlazada a la clasificación de
    ese mismo run (la primera sin tarea)."""
    if not run_id:
        return False
    fila = session.scalar(
        select(LeadClassification).where(
            LeadClassification.workflow_run_id == run_id,
            LeadClassification.task_id.is_(None),
        ).limit(1)
    )
    if fila is None:
        return False
    fila.task_id = task_id
    return True


def url_borrador(draft_id: str | None) -> str:
    """Enlace al borrador en la Bandeja (Borradores)."""
    from app.core.config import get_settings  # noqa: PLC0415

    if not draft_id:
        return ""
    base = (get_settings().frontend_base_url or "").rstrip("/")
    return f"{base}/emails/drafts?draft={draft_id}"
