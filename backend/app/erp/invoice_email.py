"""ERP-F1 Parte 2 — enviar la factura por email al cliente en su idioma.

Conecta piezas que YA existen: el PDF multiidioma (E4), la cascada de
idioma (E4-fix2/fix3) y el envío por Gmail desde alias con adjuntos
(`gmail.service.send_email`). No reimplementa nada de eso.

Discovery (documentado en el PR): NO existe vínculo email↔pedido en el
modelo — `EmailThread` solo enlaza por `contact_id`, `Order` no tiene
thread, y el nº de pedido no aparece en asuntos/cuerpos. Así que: el correo
se envía SIEMPRE ligado por `contact_id` (como cualquier email del CRM), y
se responde a un hilo solo si se localiza uno del contacto que referencie
el pedido; si no, correo nuevo.

El cuerpo usa plantillas por idioma editables en `/erp/settings`
(`factusol_series_json.invoice_email_templates`), con defaults sobrios en
código. Placeholders: {cliente}, {numero}, {referencia}.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.language import SUPPORTED_LANGS
from app.integrations.factusol.client import FactusolClient

logger = logging.getLogger(__name__)

#: Plantillas por idioma. Defaults SOBRIOS y cortos (Bart los ajustará en
#: /erp/settings — no se incrusta redacción comercial elaborada). Cada una:
#: {subject, body}. Placeholders: {cliente}, {numero}, {referencia}.
INVOICE_EMAIL_DEFAULTS: dict[str, dict[str, str]] = {
    "es": {
        "subject": "Factura {numero}",
        "body": "Estimado/a {cliente}:\n\nAdjuntamos la factura {numero}"
                "{referencia}.\n\nUn saludo.",
    },
    "en": {
        "subject": "Invoice {numero}",
        "body": "Dear {cliente},\n\nPlease find attached invoice {numero}"
                "{referencia}.\n\nKind regards.",
    },
    "de": {
        "subject": "Rechnung {numero}",
        "body": "Sehr geehrte/r {cliente},\n\nanbei die Rechnung {numero}"
                "{referencia}.\n\nMit freundlichen Grüßen.",
    },
    "fr": {
        "subject": "Facture {numero}",
        "body": "Bonjour {cliente},\n\nVeuillez trouver ci-joint la facture "
                "{numero}{referencia}.\n\nCordialement.",
    },
    "nl": {
        "subject": "Factuur {numero}",
        "body": "Beste {cliente},\n\nBijgevoegd vindt u factuur {numero}"
                "{referencia}.\n\nMet vriendelijke groet.",
    },
}

#: Cómo se lee «(su ref. X)» en cada idioma para el placeholder {referencia}.
_REF_SUFFIX: dict[str, str] = {
    "es": " (su ref. {ref})",
    "en": " (your ref. {ref})",
    "de": " (Ihre Ref. {ref})",
    "fr": " (votre réf. {ref})",
    "nl": " (uw ref. {ref})",
}


def invoice_email_templates(session: Session) -> dict[str, dict[str, str]]:
    """Plantillas efectivas: defaults del código + overrides de settings
    (`factusol_series_json.invoice_email_templates`)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    stored = series_config(session).get("invoice_email_templates")
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGS:
        base = dict(INVOICE_EMAIL_DEFAULTS.get(lang, INVOICE_EMAIL_DEFAULTS["es"]))
        override = stored.get(lang)
        if isinstance(override, dict):
            if str(override.get("subject") or "").strip():
                base["subject"] = str(override["subject"])
            if str(override.get("body") or "").strip():
                base["body"] = str(override["body"])
        out[lang] = base
    return out


def render_invoice_email(
    session: Session, *, lang: str, cliente: str, numero: str,
    referencia: str,
) -> tuple[str, str]:
    """`(asunto, cuerpo_texto)` de la plantilla del idioma, con los
    placeholders sustituidos. Idioma no soportado → español."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = invoice_email_templates(session)[lang]
    ref_txt = ""
    if referencia:
        ref_txt = _REF_SUFFIX.get(lang, _REF_SUFFIX["es"]).format(ref=referencia)
    fields = {"cliente": cliente or "", "numero": numero, "referencia": ref_txt}

    def _fill(text: str) -> str:
        for key, val in fields.items():
            text = text.replace("{" + key + "}", val)
        return text

    return _fill(tpl["subject"]), _fill(tpl["body"])


def _text_to_html(text: str) -> str:
    from app.erp.factusol_pdf import _esc  # noqa: PLC0415

    return "<p>" + _esc(text).replace("\n\n", "</p><p>").replace(
        "\n", "<br/>",
    ) + "</p>"


def default_from_alias(session: Session, user: Any) -> str:
    """Alias por defecto del usuario para enviar (su primera preferencia
    permitida; si no hay ninguna, su propio email)."""
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    pref = session.scalar(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == user.id,
            UserEmailAliasPref.is_allowed.is_(True),
        ).order_by(UserEmailAliasPref.alias_email)
    )
    return pref.alias_email if pref is not None else user.email


def find_reply_target(session: Session, order: Any) -> str | None:
    """Id de nuestro `EmailMessage` al que responder para agrupar el correo
    de la factura en el hilo del pedido, o None si no hay uno fiable.

    Como NO existe vínculo email↔pedido (solo por contacto), se busca entre
    los hilos del contacto uno cuyo asunto referencie el pedido (nº de
    pedido); si ninguno lo hace, se devuelve None → correo nuevo. Nunca se
    responde a un hilo ajeno al pedido solo por compartir contacto."""
    if order is None or not order.contact_id:
        return None
    from app.models.crm import EmailMessage, EmailThread  # noqa: PLC0415

    number = str(order.order_number or "").split("-")[-1].lstrip("0")
    if not number:
        return None
    threads = session.scalars(
        select(EmailThread).where(EmailThread.contact_id == order.contact_id)
        .order_by(EmailThread.last_message_at.desc()).limit(50)
    ).all()
    for thread in threads:
        if number in str(thread.subject or ""):
            last = session.scalar(
                select(EmailMessage).where(EmailMessage.thread_id == thread.id)
                .order_by(EmailMessage.sent_at.desc()).limit(1)
            )
            if last is not None:
                return last.id
    return None


def build_invoice_email_preview(
    session: Session,
    client: FactusolClient,
    *,
    serie: int,
    codigo: int,
    ejercicio: str,
    current_user: Any,
    lang_override: str | None = None,
    fop_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Datos de la PREVISUALIZACIÓN (obligatoria antes de enviar): a quién,
    asunto, idioma + procedencia, cuerpo editable y el nombre del adjunto.
    NO envía nada ni genera el PDF todavía."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        extract_document_data,
        load_raw_document,
        pdf_filename,
        suggest_pdf_language,
    )

    raw = load_raw_document(
        client, "facturas", serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if raw is None:
        return {"error": "not_found"}
    data = extract_document_data(
        client, "facturas", raw[0], raw[1], ejercicio=ejercicio,
        fop_names=fop_names,
    )
    suggestion = suggest_pdf_language(session, "facturas", data)
    lang = lang_override if lang_override in SUPPORTED_LANGS else suggestion["lang"]
    source = "selector" if lang_override in SUPPORTED_LANGS else suggestion["source"]

    order = _find_order(session, data)
    to = ""
    if order is not None and order.contact_id:
        from app.models.crm import Contact  # noqa: PLC0415

        contact = session.get(Contact, order.contact_id)
        to = (contact.email or "") if contact is not None else ""

    subject, body_text = render_invoice_email(
        session, lang=lang, cliente=data["cliente"]["nombre"],
        numero=data["numero"], referencia=data["referencia"],
    )
    reply_to = find_reply_target(session, order)
    return {
        "serie": serie, "codigo": codigo,
        "numero": data["numero"],
        "to": to,
        "lang": lang, "lang_source": source,
        "subject": subject,
        "body_text": body_text,
        "from_alias": default_from_alias(session, current_user),
        "attachment_filename": pdf_filename("facturas", data, lang),
        "reply_to_message_id": reply_to,
        "replies_to_thread": reply_to is not None,
        "order_id": order.id if order is not None else None,
    }


def _find_order(session: Session, data: dict[str, Any]):
    from app.erp.factusol_pdf import _find_order_for_document  # noqa: PLC0415

    return _find_order_for_document(session, "facturas", data)


def send_invoice_email(
    session: Session,
    client: FactusolClient,
    *,
    serie: int,
    codigo: int,
    ejercicio: str,
    current_user: Any,
    to: list[str],
    subject: str,
    body_text: str,
    lang: str,
    from_alias: str,
    bank: dict[str, Any] | None = None,
    variant: str | None = None,
    reply_to_message_id: str | None = None,
    fop_names: dict[str, str] | None = None,
    company: dict[str, Any] | None = None,
    logo: Any = None,
) -> dict[str, Any]:
    """Genera el PDF de la factura y lo envía por Gmail, ligado al contacto
    del pedido y (si procede) al hilo. Registra el envío en el timeline del
    pedido. Si el envío falla, propaga la excepción y NO registra nada: la
    factura NO queda marcada como enviada y el PDF se puede regenerar.
    """
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
    )
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    raw = load_raw_document(
        client, "facturas", serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if raw is None:
        raise ValueError(f"No existe la factura {serie}-{codigo}")
    data = extract_document_data(
        client, "facturas", raw[0], raw[1], ejercicio=ejercicio,
        fop_names=fop_names,
    )
    company = company if company is not None else company_for_serie(session, serie)
    pdf = generate_document_pdf(
        data, company=company, lang=lang,
        logo=logo if logo is not None else logo_path_for_serie(serie),
        variant=variant, bank=bank,
    )
    filename = pdf_filename("facturas", data, lang, variant)
    body_html = _text_to_html(body_text)

    order = _find_order(session, data)

    # El envío es la parte irreversible: si falla, la excepción sube y no se
    # registra nada (el caller devuelve error y el PDF se puede reintentar).
    message = gmail_service.send_email(
        session,
        sender_user_id=current_user.id,
        from_alias=from_alias,
        from_name=None,
        to=to,
        cc=None, bcc=None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        contact_id=order.contact_id if order is not None else None,
        in_reply_to_message_id=reply_to_message_id,
        attachments=[{
            "filename": filename,
            "content_type": "application/pdf",
            "data": pdf,
        }],
    )

    # Registro en el timeline del pedido (aparece como evento `audit`).
    if order is not None:
        _LANG_NAMES = {"es": "español", "en": "inglés", "de": "alemán",
                       "fr": "francés", "nl": "neerlandés"}
        record_event(
            session,
            action="erp.invoice_emailed",
            target_type="order",
            target_id=order.id,
            actor=current_user,
            message=(
                f"Factura {data['numero']} enviada a {', '.join(to)} "
                f"en {_LANG_NAMES.get(lang, lang)}"
            ),
            metadata={
                "factura": data["numero"], "to": to, "lang": lang,
                "message_id": message.id, "thread_id": message.thread_id,
                "attachment": filename,
            },
        )
        session.commit()

    return {
        "sent": True,
        "message_id": message.id,
        "thread_id": message.thread_id,
        "to": to,
        "lang": lang,
        "numero": data["numero"],
        "attachment_filename": filename,
    }
