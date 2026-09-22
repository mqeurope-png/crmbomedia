"""ERP · enviar el PEDIDO por email (al SAT / taller y a quien haga falta).

Conecta piezas que YA existen, no reimplementa ninguna: los PDF del motor E4
(albarán #396, pedido / presupuesto #397, factura), el envío por Gmail desde
alias con adjuntos (`gmail.service.send_email`, cuenta org-wide ya conectada)
y el timeline del pedido (`audit.record_event`), igual que el envío de la
factura al cliente (`invoice_email.py`, ERP-F1 Parte 2).

Diferencias con el email de factura, que son el motivo de este módulo:

- el destinatario por defecto NO es el cliente sino el **SAT / taller**, una
  dirección configurable en Ajustes ERP (`factusol_series_json.sat_email`,
  sin migración — mismo blob que las plantillas de la factura);
- el adjunto por defecto es el **albarán**, que es lo que el taller necesita;
  el PDF del pedido y el de la factura son opcionales;
- se admiten varios destinatarios + CC / CCO.

Nada de esto escribe en FACTUSOL: los PDF se componen leyendo los documentos.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.erp.language import SUPPORTED_LANGS

logger = logging.getLogger(__name__)

#: Clave del blob de ajustes (`ErpSettings.factusol_series_json`) donde vive
#: la dirección del SAT / taller. Sin migración, como el resto de ajustes ERP.
SAT_EMAIL_KEY = "sat_email"

#: Plantillas por idioma del email del pedido. Sobrias y cortas: son para el
#: taller, no comerciales. Editables en el propio modal antes de enviar.
#: Placeholders: {numero}, {cliente}, {referencia}.
ORDER_EMAIL_DEFAULTS: dict[str, dict[str, str]] = {
    "es": {
        "subject": "Pedido {numero} — {cliente}",
        "body": "Hola:\n\nAdjuntamos el pedido {numero} de {cliente}"
                "{referencia}.\n\nUn saludo.",
    },
    "en": {
        "subject": "Order {numero} — {cliente}",
        "body": "Hello,\n\nPlease find attached order {numero} for {cliente}"
                "{referencia}.\n\nKind regards.",
    },
    "de": {
        "subject": "Auftrag {numero} — {cliente}",
        "body": "Hallo,\n\nanbei der Auftrag {numero} von {cliente}"
                "{referencia}.\n\nMit freundlichen Grüßen.",
    },
    "fr": {
        "subject": "Commande {numero} — {cliente}",
        "body": "Bonjour,\n\nVeuillez trouver ci-joint la commande {numero} de "
                "{cliente}{referencia}.\n\nCordialement.",
    },
    "nl": {
        "subject": "Bestelling {numero} — {cliente}",
        "body": "Hallo,\n\nBijgevoegd de bestelling {numero} van {cliente}"
                "{referencia}.\n\nMet vriendelijke groet.",
    },
}

#: «(ref. X)» por idioma para el placeholder {referencia}.
_REF_SUFFIX: dict[str, str] = {
    "es": " (ref. {ref})", "en": " (ref. {ref})", "de": " (Ref. {ref})",
    "fr": " (réf. {ref})", "nl": " (ref. {ref})",
}

#: Tipos de adjunto que admite el envío, en el orden en que se adjuntan.
ATTACHMENT_KINDS: tuple[str, ...] = ("albaran", "pedido", "factura")


class OrderEmailError(ValueError):
    """Fallo con código para que la API responda algo accionable."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def sat_email_config(raw: Any) -> str:
    """Dirección del SAT configurada (o «» si no hay ninguna)."""
    return str(raw or "").strip()


def order_sat_email(session: Session) -> str:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return sat_email_config(series_config(session).get(SAT_EMAIL_KEY))


def render_order_email(
    *, lang: str, numero: str, cliente: str, referencia: str,
) -> tuple[str, str]:
    """`(asunto, cuerpo)` de la plantilla del idioma con los placeholders
    sustituidos. Idioma no soportado → español."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = ORDER_EMAIL_DEFAULTS[lang]
    ref_txt = ""
    if referencia:
        ref_txt = _REF_SUFFIX.get(lang, _REF_SUFFIX["es"]).format(ref=referencia)
    fields = {"numero": numero or "", "cliente": cliente or "",
              "referencia": ref_txt}

    def _fill(text: str) -> str:
        for key, val in fields.items():
            text = text.replace("{" + key + "}", val)
        return text

    return _fill(tpl["subject"]), _fill(tpl["body"])


# --- resolución de los documentos del pedido --------------------------------


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def albaran_ref(order: Any) -> tuple[int, int] | None:
    """`(serie, código)` del albarán que BoHub creó para el pedido, o None."""
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    numero = str(getattr(order, "factusol_albaran_number", "") or "").strip()
    if not numero:
        return None
    head, _sep, tail = numero.partition("-")
    serie, codigo = coerce_serie(head), _int_or_none(tail)
    if serie is None or codigo is None:
        return None
    return serie, codigo


def factura_ref(order: Any) -> tuple[int, int] | None:
    """`(serie, código)` de la factura del pedido, o None si no está emitida
    (o no se sabe su serie: el número de factura solo es único por serie)."""
    codigo = _int_or_none(getattr(order, "factusol_invoice_number", None))
    serie = _int_or_none(getattr(order, "factusol_invoice_serie", None))
    if codigo is None or serie is None:
        return None
    return serie, codigo


def pedido_ref(
    session: Session, client: Any, order: Any, ejercicio: str,
) -> tuple[str, int, int] | None:
    """`(doc_type, serie, código)` del documento de ORIGEN del pedido en
    FACTUSOL (proforma / pedido de cliente guardados en la Fase 1, o el
    F_PCL del pedido web localizado por su referencia), o None si el pedido
    no procede de ninguno (alta manual). Misma resolución que el botón «PDF
    del pedido (FACTUSOL)»."""
    from app.erp.api.orders import _factusol_document  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        find_pcl_by_order,
        serie_of_row,
    )

    document = _factusol_document(session, order)
    if document is None:
        return None
    if not document["by_ref"]:
        return document["doc_type"], int(document["serie"]), int(document["codigo"])
    pcl = find_pcl_by_order(
        client, order, ejercicio, ref_prefix=_store_ref_prefix(session, order),
    )
    if pcl is None:
        return None
    serie = serie_of_row(pcl, "TIPPCL")
    codigo = _int_or_none(pcl.get("CODPCL"))
    if serie is None or codigo is None:
        return None
    return document["doc_type"], serie, codigo


def _document_pdf(
    session: Session, client: Any, *, doc_type: str, serie: int, codigo: int,
    ejercicio: str, lang: str,
) -> tuple[str, bytes] | None:
    """`(nombre, PDF)` de un documento de FACTUSOL con el motor E4 (el mismo
    que los botones de descarga). None si el documento ya no existe."""
    from app.erp.api.factusol import _fop_names  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
    )

    raw = load_raw_document(
        client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if raw is None:
        return None
    data = extract_document_data(
        client, doc_type, raw[0], raw[1], ejercicio=ejercicio,
        fop_names=_fop_names(client, ejercicio),
    )
    pdf = generate_document_pdf(
        data, company=company_for_serie(session, serie), lang=lang,
        logo=logo_path_for_serie(serie),
    )
    return pdf_filename(doc_type, data, lang), pdf


def available_attachments(
    session: Session, client: Any, order: Any, ejercicio: str,
) -> dict[str, dict[str, Any]]:
    """Qué PDF se pueden adjuntar a este pedido y por qué no los que no.

    No genera ningún PDF: solo resuelve las referencias (lectura). El albarán
    manda — si falta, el aviso es el que la ficha enseña para ofrecer crearlo.
    """
    out: dict[str, dict[str, Any]] = {}

    alb = albaran_ref(order)
    out["albaran"] = {
        "available": alb is not None,
        "numero": (
            str(order.factusol_albaran_number) if alb is not None else None
        ),
        "reason": None if alb is not None else (
            "Este pedido aún no tiene albarán en FACTUSOL. Créalo con «Crear "
            "albarán en FACTUSOL» o envía el correo sin él."
        ),
        "code": None if alb is not None else "albaran_missing",
    }

    fac = factura_ref(order)
    out["factura"] = {
        "available": fac is not None,
        "numero": (
            f"{fac[0]}-{fac[1]}" if fac is not None
            else (str(order.factusol_invoice_number)
                  if order.factusol_invoice_number else None)
        ),
        "reason": None if fac is not None else (
            "El pedido aún no tiene factura emitida en FACTUSOL."
        ),
        "code": None if fac is not None else "factura_missing",
    }

    try:
        ped = pedido_ref(session, client, order, ejercicio)
    except Exception:  # noqa: BLE001 — el preview no puede romperse por esto
        logger.warning("order_email: no se pudo resolver el documento de "
                       "origen del pedido %s", order.id, exc_info=True)
        ped = None
    out["pedido"] = {
        "available": ped is not None,
        "numero": (
            f"{ped[1]}-{ped[2]}" if ped is not None else None
        ),
        "doc_type": ped[0] if ped is not None else None,
        "reason": None if ped is not None else (
            "Este pedido no procede de ningún documento de FACTUSOL "
            "(alta manual): no hay PDF del pedido que adjuntar."
        ),
        "code": None if ped is not None else "pedido_not_in_factusol",
    }
    return out


def build_attachments(
    session: Session, client: Any, order: Any, *, ejercicio: str, lang: str,
    include_albaran: bool, include_pedido: bool, include_factura: bool,
) -> list[dict[str, Any]]:
    """Los PDF pedidos, listos para `gmail.send_email`. Un adjunto marcado
    que no se puede generar es un error (`OrderEmailError`): antes que enviar
    un correo al taller SIN el albarán que el operador creía adjuntar, se
    para y se dice por qué."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    wanted = {
        "albaran": include_albaran,
        "pedido": include_pedido,
        "factura": include_factura,
    }
    if not any(wanted.values()):
        raise OrderEmailError(
            "no_attachments",
            "Marca al menos un documento para adjuntar (el albarán, el pedido "
            "o la factura).",
        )
    refs: dict[str, tuple[str, int, int] | None] = {
        "albaran": None, "pedido": None, "factura": None,
    }
    alb = albaran_ref(order)
    if alb is not None:
        refs["albaran"] = ("albaranes", alb[0], alb[1])
    fac = factura_ref(order)
    if fac is not None:
        refs["factura"] = ("facturas", fac[0], fac[1])
    try:
        if include_pedido:
            refs["pedido"] = pedido_ref(session, client, order, ejercicio)
    except FactusolError as exc:
        raise OrderEmailError("factusol_unavailable", str(exc)[:200]) from exc

    missing = [kind for kind in ATTACHMENT_KINDS
               if wanted[kind] and refs[kind] is None]
    if missing:
        info = available_attachments(session, client, order, ejercicio)
        raise OrderEmailError(
            f"{missing[0]}_missing",
            " ".join(info[kind]["reason"] or "" for kind in missing).strip(),
        )

    out: list[dict[str, Any]] = []
    for kind in ATTACHMENT_KINDS:
        if not wanted[kind]:
            continue
        doc_type, serie, codigo = refs[kind]  # type: ignore[misc]
        try:
            built = _document_pdf(
                session, client, doc_type=doc_type, serie=serie, codigo=codigo,
                ejercicio=ejercicio, lang=lang,
            )
        except FactusolError as exc:
            raise OrderEmailError("factusol_unavailable", str(exc)[:200]) from exc
        if built is None:
            raise OrderEmailError(
                f"{kind}_missing",
                f"El documento {serie}-{codigo} ya no existe en FACTUSOL "
                f"(ejercicio {ejercicio}): no se puede adjuntar.",
            )
        filename, pdf = built
        out.append({
            "kind": kind, "filename": filename,
            "content_type": "application/pdf", "data": pdf,
        })
    return out


# --- preview + envío --------------------------------------------------------


def _customer_name(session: Session, order: Any) -> str:
    """Nombre del cliente del pedido: la empresa manda (es a quien se factura
    y a quien va el albarán) y el contacto es el respaldo."""
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, [order]).get(order.id) or {}
    return str(names.get("company_name") or names.get("contact_name") or "").strip()


def build_order_email_preview(
    session: Session, client: Any, order: Any, *, ejercicio: str,
    current_user: Any, lang_override: str | None = None,
) -> dict[str, Any]:
    """Datos del modal de envío: destinatarios precargados (el SAT), asunto y
    cuerpo editables, remitente y qué se puede adjuntar. No envía nada ni
    genera PDF."""
    from app.erp.invoice_email import (  # noqa: PLC0415
        company_contacts_for_order,
        default_from_alias,
    )

    lang = lang_override if lang_override in SUPPORTED_LANGS else (
        order.language if (order.language or "") in SUPPORTED_LANGS else "es"
    )
    cliente = _customer_name(session, order)
    subject, body_text = render_order_email(
        lang=lang, numero=order.order_number or "", cliente=cliente,
        referencia=_order_reference(order),
    )
    sat = order_sat_email(session)
    attachments = available_attachments(session, client, order, ejercicio)
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "cliente": cliente,
        "lang": lang,
        "to": [sat] if sat else [],
        "sat_email": sat,
        "sat_configured": bool(sat),
        "subject": subject,
        "body_text": body_text,
        "from_alias": default_from_alias(session, current_user),
        "attachments": attachments,
        # Contactos de la empresa del pedido para el selector de destinatarios
        # (enviar el pedido/proforma/factura a los contactos del cliente, no
        # solo al taller). Los sin email salen deshabilitados.
        "company_contacts": company_contacts_for_order(session, order),
        # El albarán va SIEMPRE por defecto (es el envío típico al taller);
        # los otros dos, solo si el operador los marca.
        "defaults": {
            "albaran": attachments["albaran"]["available"],
            "pedido": False,
            "factura": False,
        },
    }


def _order_reference(order: Any) -> str:
    """Referencia del documento de origen («su ref.»), si el pedido la trae."""
    import json  # noqa: PLC0415

    if not order.packing_json:
        return ""
    try:
        packing = json.loads(order.packing_json)
    except (TypeError, ValueError):
        return ""
    source = packing.get("factusol_source") if isinstance(packing, dict) else None
    if isinstance(source, dict):
        return str(source.get("referencia") or "").strip()
    return ""


def _ensure_in_sat_queue(session: Session, order: Any, current_user: Any) -> bool:
    """«Enviar al SAT» ENCOLA el pedido en la Cola SAT (si no estaba ya),
    idempotente («verify/add»):

    - pendiente de revisión y SIN bloqueos → se aprueba (→ in_queue), igual que
      aprobar en la Cola PEDIDOS (approved_at/by + transición);
    - pendiente de revisión CON bloqueos (excepciones abiertas) → NO se encola:
      un pedido bloqueado no entra en la cola hasta resolver la excepción, la
      MISMA regla que aplica el endpoint de «Añadir a mano a la Cola SAT»
      (que devuelve 409 `blocked`). El correo sale igual;
    - ya en la cola (in_queue/preparando/embalado/bloqueado) o en un estado
      terminal → no se toca (idempotente).

    Devuelve True si el envío también APROBÓ el pedido. Nunca hace fallar el
    envío: el correo ya está fuera pase lo que pase."""
    return _approve_after_sat_email(session, order, current_user)


def _approve_after_sat_email(session: Session, order: Any, current_user: Any) -> bool:
    """Regla del taller: «enviado al SAT» = aprobado. Si el pedido seguía
    pendiente de revisión y no tiene bloqueos, pasa a in_queue como si se
    aprobase en la Cola PEDIDOS (misma transición + approved_at/by). Con
    bloqueos (excepciones abiertas) el correo sale igual pero NO se aprueba.
    Nunca hace fallar el envío: el correo ya está fuera."""
    from app.erp.api.orders import _blockers, approve_inline  # noqa: PLC0415
    from app.erp.models import PreparationStatus  # noqa: PLC0415
    from app.erp.state_machine import TransitionError  # noqa: PLC0415

    current = getattr(order.preparation_status, "value", order.preparation_status)
    if current != PreparationStatus.PENDING_REVIEW.value:
        return False
    if _blockers(session, order):
        logger.info(
            "order_email: pedido %s enviado al SAT pero NO aprobado (bloqueos)",
            order.order_number,
        )
        return False
    try:
        approve_inline(session, order, current_user, reason="enviado al SAT por email")
    except TransitionError as exc:
        logger.warning(
            "order_email: pedido %s enviado pero no se pudo aprobar: %s",
            order.order_number, exc,
        )
        return False
    return True


def send_order_email(
    session: Session, client: Any, order: Any, *, ejercicio: str,
    current_user: Any, to: list[str], cc: list[str] | None = None,
    bcc: list[str] | None = None, subject: str, body_text: str, lang: str,
    from_alias: str, include_albaran: bool = True, include_pedido: bool = False,
    include_factura: bool = False,
) -> dict[str, Any]:
    """Genera los PDF marcados y manda el correo por Gmail; registra el envío
    en el timeline del pedido (a quién, cuándo y qué se adjuntó).

    Los adjuntos se construyen ANTES de enviar: si alguno no se puede generar
    no sale ningún correo. Si el envío falla, la excepción sube y no se
    registra nada (no queda constancia de un envío que no ocurrió)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.invoice_email import _text_to_html, resolve_primary_contact  # noqa: PLC0415
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    attachments = build_attachments(
        session, client, order, ejercicio=ejercicio, lang=lang,
        include_albaran=include_albaran, include_pedido=include_pedido,
        include_factura=include_factura,
    )
    message = gmail_service.send_email(
        session,
        sender_user_id=current_user.id,
        from_alias=from_alias,
        from_name=None,
        to=to,
        cc=cc or None,
        bcc=bcc or None,
        subject=subject,
        body_html=_text_to_html(body_text),
        body_text=body_text,
        # Se liga a UN contacto para el timeline: el principal entre los
        # destinatarios (el del pedido si está entre ellos, o el primer contacto
        # de la empresa elegido); la auditoría registra a todos.
        contact_id=resolve_primary_contact(session, order, list(to) + list(cc or [])),
        attachments=[
            {k: a[k] for k in ("filename", "content_type", "data")}
            for a in attachments
        ],
    )
    filenames = [a["filename"] for a in attachments]
    kinds = [a["kind"] for a in attachments]
    destinatarios = list(to) + list(cc or []) + list(bcc or [])
    record_event(
        session,
        action="erp.order_emailed",
        target_type="order",
        target_id=order.id,
        actor=current_user,
        message=(
            f"Pedido {order.order_number} enviado por email a "
            f"{', '.join(destinatarios)} con {', '.join(kinds)}"
        ),
        metadata={
            "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []),
            "subject": subject, "lang": lang, "from_alias": from_alias,
            "attachments": filenames, "attachment_kinds": kinds,
            "message_id": message.id, "thread_id": message.thread_id,
        },
    )
    # «Enviado al taller» = está en la Cola SAT: si seguía pendiente de revisión
    # se encola (aprobándolo si no hay bloqueos, o forzándolo a la cola si los
    # hay). Idempotente: si ya estaba en la cola (o en un estado terminal) no se
    # toca. El correo ya está fuera pase lo que pase.
    approved = _ensure_in_sat_queue(session, order, current_user)
    session.commit()
    logger.info(
        "order_email: pedido %s enviado a %s (%s)",
        order.order_number, ", ".join(destinatarios), ", ".join(kinds),
    )
    return {
        "sent": True,
        "order_id": order.id,
        "message_id": message.id,
        "thread_id": message.thread_id,
        "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []),
        "lang": lang,
        "attachments": filenames,
        "attachment_kinds": kinds,
        "approved": approved,
        "preparation_status": getattr(
            order.preparation_status, "value", order.preparation_status,
        ),
    }
