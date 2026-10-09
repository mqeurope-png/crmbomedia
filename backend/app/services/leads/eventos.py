"""El evento `lead.received`: lo que dispara el workflow de respuesta a leads.

Dos productores:

- El envío de un formulario web que NO es spam (`web_forms/submit.py`), con la
  web, el idioma del formulario, los productos marcados y la consulta.
- Una nota de AgileCRM que empieza por «form note» (`agilecrm/refresh.py`),
  con la cuenta de origen y el texto. La fecha del lead es
  `notes.external_created_at`, la real, no la de sincronización: hay notas de
  julio sincronizadas en septiembre.

El evento se despacha DESPUÉS de comitear el lead y es best-effort: un fallo
del despacho no tumba la captura.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm import Contact, Note
from app.models.leads import FUENTE_AGILE, FUENTE_FORMULARIO
from app.models.web_forms import FormSubmission, WebForm

logger = logging.getLogger(__name__)

EVENTO_LEAD = "lead.received"
_NOTA_FORMULARIO_RE = re.compile(r"^\s*form\s*note\b[\s:.\-–—]*", re.IGNORECASE)


def es_nota_de_formulario(body: str | None) -> bool:
    return bool(_NOTA_FORMULARIO_RE.match(body or ""))


def texto_de_nota(body: str | None) -> str:
    """El texto de la consulta sin el prefijo «form note»."""
    return _NOTA_FORMULARIO_RE.sub("", body or "", count=1).strip()


def _iso(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.isoformat()


def payload_de_formulario(
    session: Session, *, form: WebForm, contact: Contact, submission: FormSubmission,
    payload: dict[str, Any], etiquetas: list[str] | None,
) -> dict[str, Any]:
    from app.services.leads.registro import productos_de_envio, texto_de_envio  # noqa: PLC0415
    from app.services.web_forms.sitios import clave_de_sitio  # noqa: PLC0415

    productos = list(etiquetas or []) or productos_de_envio(session, form, payload)
    return {
        "source": FUENTE_FORMULARIO,
        "source_ref": submission.id,
        "submission_id": submission.id,
        "lead_at": _iso(submission.created_at) or _iso(datetime.now(UTC)),
        "form_slug": form.slug,
        "site": clave_de_sitio(form.slug),
        "form_language": form.language,
        "products": productos,
        "text": texto_de_envio(form, payload),
        "country": contact.address_country,
        "email": contact.email,
    }


def payload_de_nota(note: Note) -> dict[str, Any]:
    return {
        "source": FUENTE_AGILE,
        "source_ref": note.id,
        "note_id": note.id,
        "lead_at": _iso(note.external_created_at or note.created_at),
        "agile_account_id": note.external_account_id,
        "text": texto_de_nota(note.body),
    }


def _despachar(session: Session, contact_id: str, payload: dict[str, Any]) -> None:
    from app.workflows.dispatcher import dispatch_event  # noqa: PLC0415

    dispatch_event(session, EVENTO_LEAD, contact_id, payload)
    # Sin Redis el despacho corre en línea y deja el run en la sesión: se
    # comitea aquí para que no dependa de lo que haga el llamador después.
    session.commit()


def despachar_lead_de_formulario(
    session: Session, *, form: WebForm, contact: Contact, submission: FormSubmission,
    payload: dict[str, Any], etiquetas: list[str] | None = None,
) -> bool:
    """Best-effort: el lead ya está guardado y comiteado."""
    try:
        datos = payload_de_formulario(
            session, form=form, contact=contact, submission=submission, payload=payload,
            etiquetas=etiquetas,
        )
        _despachar(session, contact.id, datos)
        return True
    except Exception:  # noqa: BLE001 — un fallo del despacho no tumba la captura
        session.rollback()
        logger.warning(
            "leads.evento: no se pudo despachar lead.received del formulario %s "
            "(contacto %s)", form.slug, contact.id, exc_info=True,
        )
        return False


def despachar_leads_de_notas(session: Session, *, contact_id: str, notas: list[Note]) -> int:
    """Un evento por nota «form note» nueva. Best-effort."""
    despachadas = 0
    for nota in notas:
        if not es_nota_de_formulario(nota.body):
            continue
        try:
            _despachar(session, contact_id, payload_de_nota(nota))
            despachadas += 1
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning(
                "leads.evento: no se pudo despachar lead.received de la nota %s "
                "(contacto %s)", nota.id, contact_id, exc_info=True,
            )
    return despachadas
