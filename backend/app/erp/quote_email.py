"""Punto A (lote Proformas) — enviar un presupuesto / proforma por email.

Reutiliza el flujo de la factura (`invoice_email`): plantillas por idioma en
Ajustes ERP, remitente por serie (empresa emisora) con alias verificados en
Gmail, selector de contactos de la empresa, PDF adjunto generado al vuelo con
las mismas opciones que «Descargar PDF» (tipo presupuesto / proforma, moneda,
banco) y envío por `gmail.service.send_email`. NO escribe en FACTUSOL.

Diferencias con la factura:
- La proforma rara vez tiene pedido: los destinatarios salen de la EMPRESA CRM
  vinculada al cliente (CLIPRE); se premarca el contacto cuyo email coincide
  con el de la cabecera (CEMPRE) o, si no, el primero con email.
- El remitente es el de la SERIE (1 Bomedia, 2 MQ Europe, 5 Streamtec), con
  la misma configuración que la factura; si la serie no tiene, el alias del
  usuario. Responder-a = el mismo remitente (es el From del correo).
- El idioma sigue la cascada del PDF (`suggest_pdf_language`): pedido →
  idioma explícito de la empresa → país del documento → país de la empresa →
  idioma de la emisora → ES. El modal lo puede cambiar.
- La traza es un evento `erp.proforma_emailed` sobre la PROPIA proforma
  (`target_type="factusol_quote"`, `target_id="2-000075"`), exista o no
  pedido; con pedido se añade el evento al timeline del pedido. Las listas
  leen la marca «Enviada dd/mm» de ahí (`latest_quote_emailed_map`).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.language import SUPPORTED_LANGS
from app.integrations.factusol.client import FactusolClient

logger = logging.getLogger(__name__)

#: Evento de AuditLog que deja cada envío (y cada reenvío).
QUOTE_EMAILED_EVENT = "erp.proforma_emailed"
#: `target_type` del evento: la proforma, identificada por su nº visible.
QUOTE_EMAILED_TARGET = "factusol_quote"
#: Clave del blob `factusol_series_json` con las plantillas guardadas.
TEMPLATES_KEY = "quote_email_templates"

#: Plantillas por idioma, SOBRIAS (Bart las ajusta en /erp/settings).
#: Marcadores: {numero}, {empresa}, {contacto}, {total}, {fecha}, {validez}
#: (la frase de validez que imprime el PDF) y {firma} (nombre de la empresa
#: emisora de la serie).
QUOTE_EMAIL_DEFAULTS: dict[str, dict[str, str]] = {
    "es": {
        "subject": "Presupuesto {numero}",
        "body": "Estimado/a {contacto}:\n\nAdjuntamos el presupuesto {numero} "
                "de fecha {fecha} por un importe de {total}.\n{validez}\n\n"
                "Quedamos a su disposición para cualquier aclaración.\n\n"
                "Un saludo,\n{firma}",
    },
    "en": {
        "subject": "Quotation {numero}",
        "body": "Dear {contacto},\n\nPlease find attached quotation {numero} "
                "dated {fecha} for a total of {total}.\n{validez}\n\n"
                "We remain at your disposal for any questions.\n\n"
                "Kind regards,\n{firma}",
    },
    "de": {
        "subject": "Angebot {numero}",
        "body": "Sehr geehrte/r {contacto},\n\nanbei das Angebot {numero} vom "
                "{fecha} über {total}.\n{validez}\n\n"
                "Bei Fragen stehen wir Ihnen gerne zur Verfügung.\n\n"
                "Mit freundlichen Grüßen,\n{firma}",
    },
    "fr": {
        "subject": "Devis {numero}",
        "body": "Bonjour {contacto},\n\nVeuillez trouver ci-joint le devis "
                "{numero} du {fecha} d'un montant de {total}.\n{validez}\n\n"
                "Nous restons à votre disposition pour toute question.\n\n"
                "Cordialement,\n{firma}",
    },
    "nl": {
        "subject": "Offerte {numero}",
        "body": "Beste {contacto},\n\nBijgevoegd vindt u offerte {numero} van "
                "{fecha} voor een bedrag van {total}.\n{validez}\n\n"
                "Wij staan tot uw beschikking voor eventuele vragen.\n\n"
                "Met vriendelijke groet,\n{firma}",
    },
}

#: Datos de MUESTRA para «Ver ejemplo» / «Enviarme una prueba» en Ajustes.
SAMPLE_QUOTE_EMAIL: dict[str, str] = {
    "numero": "2-000075",
    "empresa": "Laboratorios Duaner S.L.",
    "contacto": "Marta Coll",
    "total": "1.234,56 €",
    # Como lo imprime el PDF y como lo escribe el envío real (dd-mm-aaaa).
    "fecha": "02-10-2026",
    "validez": "Presupuesto válido durante 30 días, a partir de la fecha de emisión.",
    "firma": "MQ Europe",
}

_LANG_NAMES = {"es": "español", "en": "inglés", "de": "alemán",
               "fr": "francés", "nl": "neerlandés"}


# --- plantillas -------------------------------------------------------------


def merge_templates(stored: Any) -> dict[str, dict[str, str]]:
    """Defaults del código + overrides guardados (solo subject/body no vacíos)."""
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGS:
        base = dict(QUOTE_EMAIL_DEFAULTS.get(lang, QUOTE_EMAIL_DEFAULTS["es"]))
        override = stored.get(lang)
        if isinstance(override, dict):
            for key in ("subject", "body"):
                if str(override.get(key) or "").strip():
                    base[key] = str(override[key])
        out[lang] = base
    return out


def quote_email_templates(session: Session) -> dict[str, dict[str, str]]:
    """Plantillas efectivas (`factusol_series_json.quote_email_templates`)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return merge_templates(series_config(session).get(TEMPLATES_KEY))


def fill_template(subject: str, body: str, fields: dict[str, str]) -> tuple[str, str]:
    """Sustituye `{marcador}` por su valor en asunto y cuerpo. Un marcador sin
    dato queda vacío (y la línea de validez desaparece si no hay frase)."""
    def _fill(text: str) -> str:
        for key, val in fields.items():
            text = text.replace("{" + key + "}", val or "")
        return text

    body_out = _fill(body)
    # Sin validez, que no quede una línea en blanco de más.
    if not fields.get("validez"):
        body_out = body_out.replace("\n\n\n", "\n\n")
    return _fill(subject), body_out


def render_quote_email(
    session: Session, *, lang: str, fields: dict[str, str],
) -> tuple[str, str]:
    """`(asunto, cuerpo)` de la plantilla del idioma con los marcadores
    sustituidos. Idioma no soportado → español."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = quote_email_templates(session)[lang]
    return fill_template(tpl["subject"], tpl["body"], fields)


def render_sample_quote_email(
    session: Session, *, lang: str, subject: str | None = None,
    body: str | None = None,
) -> dict[str, str]:
    """Plantilla rellena con `SAMPLE_QUOTE_EMAIL` (lo que se está escribiendo
    en Ajustes manda sobre lo guardado). `{lang, subject, body_text, body_html}`."""
    from app.erp.invoice_email import _text_to_html  # noqa: PLC0415

    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = quote_email_templates(session)[lang]
    subject_tpl = subject if subject is not None and subject.strip() else tpl["subject"]
    body_tpl = body if body is not None and body.strip() else tpl["body"]
    rendered_subject, rendered_body = fill_template(subject_tpl, body_tpl, SAMPLE_QUOTE_EMAIL)
    return {
        "lang": lang, "subject": rendered_subject,
        "body_text": rendered_body, "body_html": _text_to_html(rendered_body),
    }


# --- destinatarios ----------------------------------------------------------


def quote_company(session: Session, codcli: Any):
    """Empresa CRM vinculada al cliente FACTUSOL de la proforma, o None. Misma
    regla que el listado: `'0055'` y `'55'` son el mismo CODCLI."""
    from app.erp.quotes_bandeja import _companies_by_codcli  # noqa: PLC0415

    code = str(codcli or "").strip()
    if not code:
        return None
    found = _companies_by_codcli(session, {code})
    return found.get(code) or (found.get(str(int(code))) if code.isdigit() else None)


def quote_recipients(
    session: Session, company: Any, header_email: str | None,
) -> list[dict[str, Any]]:
    """Contactos de la empresa vinculada como candidatos a destinatario:
    `{id, name, email, has_email, is_primary}`. `is_primary` (premarcado en
    «Para»): el contacto cuyo email es el de la cabecera de la proforma
    (CEMPRE); si ninguno coincide, el primero con email. Sin empresa → []."""
    if company is None:
        return []
    from app.models.crm import Contact  # noqa: PLC0415

    rows = list(session.scalars(
        select(Contact).where(
            Contact.company_id == company.id, Contact.is_active.is_(True),
        ).order_by(Contact.last_name.asc(), Contact.first_name.asc())
    ))
    wanted = str(header_email or "").strip().lower()
    out: list[dict[str, Any]] = []
    for c in rows:
        email = (c.email or "").strip() if c.is_email_valid else ""
        name = " ".join(p for p in (c.first_name, c.last_name) if p).strip()
        out.append({
            "id": c.id, "name": name or email or "—",
            "email": email or None, "has_email": bool(email),
            "is_primary": bool(email) and wanted != "" and email.lower() == wanted,
        })
    if not any(c["is_primary"] for c in out):
        for c in out:
            if c["has_email"]:
                c["is_primary"] = True
                break
    return out


def _contact_id_for(session: Session, company: Any, recipients: list[str]) -> str | None:
    """Contacto al que se ENLAZA el correo (timeline del CRM): el primer
    destinatario que sea contacto de la empresa; si ninguno lo es (cliente sin
    empresa vinculada, o contactos añadidos con el buscador del CRM), el primer
    destinatario que sea un contacto activo de cualquier empresa. None si no."""
    from sqlalchemy import func  # noqa: PLC0415

    from app.models.crm import Contact  # noqa: PLC0415

    emails = [e.strip().lower() for e in recipients if e and e.strip()]
    if not emails:
        return None
    if company is not None:
        by_email: dict[str, str] = {}
        for c in session.scalars(
            select(Contact).where(Contact.company_id == company.id, Contact.email.isnot(None))
        ):
            key = (c.email or "").strip().lower()
            if key:
                by_email.setdefault(key, c.id)
        for e in emails:
            if e in by_email:
                return by_email[e]
    for e in emails:
        found = session.scalar(
            select(Contact.id).where(
                Contact.is_active.is_(True), func.lower(Contact.email) == e,
            ).order_by(Contact.created_at.asc()).limit(1)
        )
        if found:
            return found
    return None


def _crm_contact_greeting(session: Session, contact_id: str | None) -> dict[str, Any] | None:
    """`{id, name}` de un contacto ACTIVO del CRM que no es de la empresa
    vinculada (lo añadió el operador con el buscador): `{contacto}` le saluda
    por su nombre. Sin nombre, `name` va vacío y se saluda a la empresa (nunca
    al contacto principal, que quizá ni recibe el correo). None si no existe."""
    if not contact_id or contact_id == "none":
        return None
    from app.models.crm import Contact  # noqa: PLC0415

    contact = session.get(Contact, contact_id)
    if contact is None or not contact.is_active:
        return None
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p).strip()
    return {"id": contact.id, "name": name}


# --- datos de la proforma ---------------------------------------------------


def _load_quote_data(
    client: FactusolClient, *, serie: int, codpre: int, ejercicio: str,
    fop_names: dict[str, str] | None,
) -> dict[str, Any] | None:
    """Cabecera + líneas de F_PRE/F_LPS en la forma neutra del maquetador de
    PDF (`extract_document_data`), o None si no existe."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        extract_document_data,
        load_raw_document,
    )

    raw = load_raw_document(
        client, "presupuestos", serie=serie, codigo=codpre, ejercicio=ejercicio,
    )
    if raw is None:
        return None
    data = extract_document_data(
        client, "presupuestos", raw[0], raw[1], ejercicio=ejercicio, fop_names=fop_names,
    )
    # `suggest_pdf_language` mira `cliente_codigo` (forma del lector de
    # documentos) además de `cliente.pais` (forma del PDF): se dan las dos.
    data["cliente_codigo"] = (data.get("cliente") or {}).get("codigo")
    # Email de la cabecera (CEMPRE): el maquetador no lo extrae.
    data["email"] = str(raw[0].get("CEMPRE") or "").strip()
    return data


def quote_attachment_filename(
    data: dict[str, Any], lang: str, variant: str | None,
) -> str:
    """Nombre del PDF adjunto: el mismo que el de las descargas y que los
    adjuntos de pedido y de factura (`pdf_filename`, #531) — tipo traducido,
    cliente y número: `Angebot MANUFAKTUR FUR GESTALTUNG UND DRUCK 2-000080.pdf`,
    `Proforma invoice HUGIN GMBH 2-004365.pdf`. Antes iba siempre en
    castellano y sin cliente (`Proforma-2-000075.pdf`), también cuando el PDF
    y el correo salían en alemán."""
    from app.erp.factusol_pdf import pdf_filename  # noqa: PLC0415

    return pdf_filename("presupuestos", data, lang, variant)


def _validez_text(lang: str) -> str:
    from app.erp.factusol_pdf import labels_for  # noqa: PLC0415

    return str(labels_for(lang).get("validez_presupuesto") or "").strip()


def _firma_for_serie(session: Session, serie: int) -> str:
    from app.erp.factusol_pdf import company_for_serie  # noqa: PLC0415

    return str(company_for_serie(session, serie).get("nombre") or "").strip()


def quote_email_fields(
    session: Session, *, data: dict[str, Any], lang: str, company: Any,
    contacto: str, currency: str = "EUR",
) -> dict[str, str]:
    """Valores de los marcadores para esta proforma e idioma."""
    from app.erp.factusol_pdf import CURRENCIES, _fmt_money  # noqa: PLC0415

    empresa = (
        str(company.name) if company is not None and getattr(company, "name", None)
        else str((data.get("cliente") or {}).get("nombre") or "")
    )
    importe = _fmt_money(float(data.get("total") or 0.0), lang, currency)
    # Mismo símbolo que el PDF adjunto; fuera del euro se añade el código ISO
    # (un «kr» a secas no dice si son coronas suecas, danesas o noruegas).
    symbol = str((CURRENCIES.get(currency) or {}).get("symbol") or "").strip()
    if currency == "EUR":
        total = f"{importe} €"
    elif symbol and symbol != currency:
        total = f"{importe} {symbol} ({currency})"
    else:
        total = f"{importe} {currency}"
    return {
        "numero": str(data.get("numero") or ""),
        "empresa": empresa,
        "contacto": contacto or empresa,
        "total": total,
        "fecha": str(data.get("fecha") or ""),
        "validez": _validez_text(lang),
        "firma": _firma_for_serie(session, int(data.get("serie") or 0) or 1),
    }


def _linked_order(session: Session, serie: int, codpre: int):
    from app.erp.orders_from_factusol import find_quote_order  # noqa: PLC0415

    return find_quote_order(session, int(serie), int(codpre))


# --- previsualización y envío ------------------------------------------------


def build_quote_email_preview(
    session: Session,
    client: FactusolClient,
    *,
    serie: int,
    codpre: int,
    ejercicio: str,
    current_user: Any,
    lang_override: str | None = None,
    variant: str | None = None,
    currency: str = "EUR",
    fop_names: dict[str, str] | None = None,
    contact_id: str | None = None,
) -> dict[str, Any]:
    """Datos de la PREVISUALIZACIÓN (obligatoria): contactos candidatos (con
    el premarcado), idioma + procedencia, asunto y cuerpo editables, remitente
    por serie y nombre del adjunto. No envía nada ni genera el PDF.

    `contact_id`: a quién saluda `{contacto}`. Sin él, el premarcado; con el
    id de un contacto de la lista —o de cualquier contacto activo del CRM
    añadido con el buscador—, ese (el modal lo manda cuando el operador
    cambia el primer destinatario); `"none"` = ningún contacto (direcciones
    libres) → se saluda a la empresa."""
    from app.erp.factusol_pdf import suggest_pdf_language  # noqa: PLC0415
    from app.erp.invoice_email import (  # noqa: PLC0415
        check_sender_alias,
        default_from_alias,
        series_from_alias,
    )

    data = _load_quote_data(client, serie=serie, codpre=codpre, ejercicio=ejercicio,
                            fop_names=fop_names)
    if data is None:
        return {"error": "not_found"}
    company = quote_company(session, data.get("cliente_codigo"))
    order = _linked_order(session, serie, codpre)
    suggestion = suggest_pdf_language(session, "presupuestos", data, order=order)
    lang = lang_override if lang_override in SUPPORTED_LANGS else suggestion["lang"]
    source = "selector" if lang_override in SUPPORTED_LANGS else suggestion["source"]

    contacts = quote_recipients(session, company, data.get("email"))
    if contact_id == "none":
        saludo = None
    else:
        saludo = next((c for c in contacts if contact_id and c["id"] == contact_id), None)
        if saludo is None:
            # Contacto de OTRA empresa añadido con el buscador del CRM.
            saludo = _crm_contact_greeting(session, contact_id)
        if saludo is None:
            saludo = next((c for c in contacts if c["is_primary"]), None)
    fields = quote_email_fields(
        session, data=data, lang=lang, company=company,
        contacto=saludo["name"] if saludo else "", currency=currency,
    )
    subject, body_text = render_quote_email(session, lang=lang, fields=fields)

    serie_alias = series_from_alias(session, serie)
    from_alias = serie_alias or default_from_alias(session, current_user)
    alias_check = (
        check_sender_alias(session, current_user, from_alias)
        if from_alias else {"ok": False, "reason": "sin_alias"}
    )
    return {
        "serie": serie, "codpre": codpre,
        "numero": data["numero"],
        # Email de la cabecera, por si no hay empresa CRM ni contactos.
        "to": data.get("email") or "",
        "lang": lang, "lang_source": source,
        "subject": subject, "body_text": body_text,
        "from_alias": from_alias,
        "from_alias_source": "serie" if serie_alias else "usuario",
        "from_alias_ok": alias_check.get("ok"),
        "from_alias_problem": alias_check.get("reason"),
        "attachment_filename": quote_attachment_filename(data, lang, variant),
        "variant": variant, "currency": currency,
        "company_id": company.id if company is not None else None,
        "company_name": company.name if company is not None else None,
        "customer_name": (data.get("cliente") or {}).get("nombre") or None,
        "order_id": order.id if order is not None else None,
        "order_number": order.order_number if order is not None else None,
        "company_contacts": contacts,
        # A quién saluda el cuerpo (null = a la empresa): el modal recarga si
        # el primer destinatario elegido es otro.
        "contacto_id": saludo["id"] if saludo else None,
        "markers": fields,
    }


def send_quote_email(
    session: Session,
    client: FactusolClient,
    *,
    serie: int,
    codpre: int,
    ejercicio: str,
    current_user: Any,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str,
    body_text: str,
    lang: str,
    from_alias: str,
    variant: str | None = None,
    currency: str = "EUR",
    bank: dict[str, Any] | None = None,
    fop_names: dict[str, str] | None = None,
    company: dict[str, Any] | None = None,
    logo: Any = None,
) -> dict[str, Any]:
    """Genera el PDF del presupuesto (tipo / moneda / banco elegidos) y lo
    envía por Gmail desde `from_alias`, ligado al primer contacto destinatario
    de la empresa. Registra `erp.proforma_emailed` sobre la proforma (y en el
    timeline del pedido si lo hay). Si el envío falla, propaga y NO registra
    nada: la proforma no queda marcada como enviada."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        generate_document_pdf,
        logo_path_for_serie,
    )
    from app.erp.invoice_email import _text_to_html  # noqa: PLC0415
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    data = _load_quote_data(client, serie=serie, codpre=codpre, ejercicio=ejercicio,
                            fop_names=fop_names)
    if data is None:
        raise ValueError(f"No existe el presupuesto {serie}-{codpre}")
    crm_company = quote_company(session, data.get("cliente_codigo"))
    emisora = company if company is not None else company_for_serie(session, serie)
    pdf = generate_document_pdf(
        data, company=emisora, lang=lang,
        logo=logo if logo is not None else logo_path_for_serie(serie),
        variant=variant, bank=bank, currency=currency,
    )
    filename = quote_attachment_filename(data, lang, variant)
    recipients = list(to) + list(cc or [])
    message = gmail_service.send_email(
        session,
        sender_user_id=current_user.id,
        from_alias=from_alias,
        from_name=None,
        to=list(to), cc=cc or None, bcc=bcc or None,
        subject=subject,
        body_html=_text_to_html(body_text),
        body_text=body_text,
        contact_id=_contact_id_for(session, crm_company, recipients),
        attachments=[{"filename": filename, "content_type": "application/pdf", "data": pdf}],
    )

    # Traza: sobre la proforma (siempre) y en el timeline del pedido (si hay).
    numero = str(data["numero"])
    metadata = {
        "numero": numero, "serie": int(serie), "codpre": int(codpre),
        # El nº visible se repite de un ejercicio a otro: la marca de la
        # lista solo cuenta los envíos de SU ejercicio.
        "ejercicio": str(ejercicio),
        "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []),
        "lang": lang, "from_alias": from_alias, "variant": variant,
        "currency": currency, "message_id": message.id,
        "thread_id": message.thread_id, "attachment": filename,
        "user": getattr(current_user, "email", None),
    }
    texto = (f"Presupuesto {numero} enviado a {', '.join(recipients)} "
             f"en {_LANG_NAMES.get(lang, lang)}")
    record_event(
        session, action=QUOTE_EMAILED_EVENT, target_type=QUOTE_EMAILED_TARGET,
        target_id=numero, actor=current_user, message=texto, metadata=metadata,
    )
    order = _linked_order(session, serie, codpre)
    if order is not None:
        record_event(
            session, action=QUOTE_EMAILED_EVENT, target_type="order",
            target_id=order.id, actor=current_user, message=texto, metadata=metadata,
        )
    session.commit()
    return {
        "sent": True, "message_id": message.id, "thread_id": message.thread_id,
        "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []),
        "lang": lang, "numero": numero, "attachment_filename": filename,
    }


# --- marcas para las listas ----------------------------------------------------


def latest_quote_emailed_map(
    session: Session, numeros: list[str], ejercicio: str | None = None,
) -> dict[str, dict[str, Any]]:
    """`{numero: {"at": ISO, "to": [...]}}` del ÚLTIMO envío de cada proforma
    (evento `erp.proforma_emailed` sobre la propia proforma), en UNA query.
    Alimenta la marca «Enviada dd/mm» (con los destinatarios en el tooltip) y
    el botón «Reenviar» de las listas. Con `ejercicio`, se ignoran los envíos
    de otro ejercicio (el nº visible «2-000075» se repite cada año)."""
    keys = sorted({str(n) for n in numeros if n})
    if not keys:
        return {}
    from app.models.crm import AuditLog  # noqa: PLC0415

    rows = session.execute(
        select(AuditLog.target_id, AuditLog.created_at, AuditLog.metadata_json)
        .where(
            AuditLog.action == QUOTE_EMAILED_EVENT,
            AuditLog.target_type == QUOTE_EMAILED_TARGET,
            AuditLog.target_id.in_(keys),
        ).order_by(AuditLog.created_at.desc())
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for target_id, created_at, raw in rows:
        if target_id in out or created_at is None:
            continue
        try:
            meta = json.loads(raw) if raw else {}
        except ValueError:
            meta = {}
        if ejercicio and meta.get("ejercicio") and str(meta["ejercicio"]) != str(ejercicio):
            continue
        out[target_id] = {
            "at": created_at.isoformat(),
            "to": list(meta.get("to") or []) + list(meta.get("cc") or []),
        }
    return out
