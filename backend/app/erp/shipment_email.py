"""ERP · aviso de ENVÍO al cliente (nº de seguimiento + enlace), en su idioma.

Hasta ahora lo mandaba Genei (Perfil → Notificaciones → «Destinatario» → «Al
crear un envío»), siempre en el mismo idioma. Ahora lo manda BoHub, en el
idioma del cliente, desde el remitente de la marca; Bart desmarca ese aviso en
Genei cuando esto esté en producción (las incidencias y la entrega los sigue
mandando Genei). Genei no tiene un parámetro por envío para apagarlo: es un
ajuste de la cuenta (verificado en su Swagger v2: no hay flag de notificación
en `POST /shipments`).

Conecta piezas que YA existen, sin reimplementarlas:
- el envío por Gmail desde alias (`gmail.service.send_email`, integración de
  Google de la organización), igual que el email de factura;
- los remitentes por TIENDA de Ajustes ERP (`store_email_from`: artisjet →
  info@artisjet-printers.eu; boprint / fluxlasers → pedidos@streamtec.es);
- la cascada de idioma del ERP (`language_for_country`);
- el tracking y la URL de seguimiento que ya guarda el bloque Genei.

**Remitente** (decisión de Bart):
- pedido WEB → el de su TIENDA (BOPRIN-/FLUXLA- → pedidos@streamtec.es,
  ARTISJ- → info@artisjet-printers.eu), configurable en «Remitentes»;
- pedido MANUAL (factura, proforma, albarán, manual o muestra) — o de una tienda
  sin remitente — → por IDIOMA: español → pedidos@streamtec.es; cualquier
  otro → info@artisjet-printers.eu (configurable: `shipment_email_from`).

**Idioma** (`shipment_email_language`), por orden: el del PEDIDO → el
explícito del CLIENTE (empresa) → el del PAÍS DE DESTINO del envío → el del
país de la empresa → español.

**Disparo** (`maybe_send_shipment_email`): automático UNA sola vez por envío,
en cuanto el envío tiene nº de seguimiento (al pagar/tramitar, al llegar el
webhook, al «Actualizar estado» o en el sondeo). Solo para envíos creados con
el aviso activado (marca `customer_email.status = "pending"` al crearlos): los
anteriores no se avisan solos (ya los avisó Genei). Idempotente y a prueba de
carreras (cerrojo de fila + estado «sending»). Reenvío manual desde la ficha.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.language import SUPPORTED_LANGS, language_for_country

logger = logging.getLogger(__name__)

#: Clave de las plantillas en el blob de Ajustes ERP (`factusol_series_json`).
TEMPLATES_KEY = "shipment_email_templates"
#: Remitentes de los pedidos MANUALES por idioma (`{"es": …, "otros": …}`).
MANUAL_FROM_KEY = "shipment_email_from"
DEFAULT_MANUAL_FROM: dict[str, str] = {
    "es": "pedidos@streamtec.es",
    "otros": "info@artisjet-printers.eu",
}
#: Tienda por prefijo del nº de pedido web (por si el pedido no trae cuenta).
STORE_BY_PREFIX: dict[str, str] = {
    "BOPRIN": "boprint",
    "FLUXLA": "fluxlasers",
    "ARTISJ": "artisjet",
}
#: Reintentos automáticos si Gmail falla (luego, reenvío manual).
MAX_AUTO_ATTEMPTS = 3
AUDIT_ACTION = "erp.shipment_emailed"

#: Plantillas por idioma. Placeholders: {cliente}, {pedido} (el nº que conoce
#: el cliente: el de la tienda en un pedido web), {tracking}, {enlace} (URL de
#: seguimiento; si no la hay, «en la web de la agencia») y {agencia}.
SHIPMENT_EMAIL_DEFAULTS: dict[str, dict[str, str]] = {
    "es": {
        "subject": "Tu pedido {pedido} ya tiene envío — seguimiento {tracking}",
        "body": "Hola {cliente}:\n\nTu pedido {pedido} se envía con {agencia}.\n\n"
                "Nº de seguimiento: {tracking}\nSigue el envío aquí: {enlace}\n\n"
                "El seguimiento puede tardar unas horas en mostrar información.\n\n"
                "Un saludo.",
    },
    "en": {
        "subject": "Your order {pedido} has been shipped — tracking {tracking}",
        "body": "Hello {cliente},\n\nYour order {pedido} is being shipped with {agencia}.\n\n"
                "Tracking number: {tracking}\nTrack your shipment here: {enlace}\n\n"
                "Tracking information may take a few hours to appear.\n\n"
                "Kind regards.",
    },
    "de": {
        "subject": "Ihre Bestellung {pedido} wurde versandt — Sendungsnummer {tracking}",
        "body": "Hallo {cliente},\n\nIhre Bestellung {pedido} wird mit {agencia} versandt.\n\n"
                "Sendungsnummer: {tracking}\nSendungsverfolgung: {enlace}\n\n"
                "Es kann einige Stunden dauern, bis die Sendungsverfolgung "
                "Informationen anzeigt.\n\nMit freundlichen Grüßen.",
    },
    "fr": {
        "subject": "Votre commande {pedido} a été expédiée — suivi {tracking}",
        "body": "Bonjour {cliente},\n\nVotre commande {pedido} est expédiée avec {agencia}.\n\n"
                "Numéro de suivi : {tracking}\nSuivez votre envoi ici : {enlace}\n\n"
                "Le suivi peut mettre quelques heures à afficher des informations.\n\n"
                "Cordialement.",
    },
    "nl": {
        "subject": "Uw bestelling {pedido} is verzonden — track & trace {tracking}",
        "body": "Beste {cliente},\n\nUw bestelling {pedido} wordt verzonden met {agencia}.\n\n"
                "Track & trace-nummer: {tracking}\nVolg uw zending hier: {enlace}\n\n"
                "Het kan enkele uren duren voordat de tracking informatie toont.\n\n"
                "Met vriendelijke groet.",
    },
}

#: {enlace} cuando no hay URL de seguimiento pero SÍ se sabe el courier
#: («Sigue el envío aquí: en la web de MBE»). Sin courier tampoco, la línea del
#: enlace desaparece y el aviso va solo con el número.
_NO_LINK: dict[str, str] = {
    "es": "en la web de {agencia}",
    "en": "on the {agencia} website",
    "de": "auf der Website von {agencia}",
    "fr": "sur le site de {agencia}",
    "nl": "op de website van {agencia}",
}
#: {agencia} cuando no consta la agencia.
_NO_AGENCY: dict[str, str] = {
    "es": "la agencia de transporte", "en": "our carrier",
    "de": "unserem Versanddienstleister", "fr": "notre transporteur",
    "nl": "onze vervoerder",
}
_GREETING_FALLBACK: dict[str, str] = {
    "es": "cliente", "en": "customer", "de": "Kunde", "fr": "client", "nl": "klant",
}
LANG_NAMES: dict[str, str] = {
    "es": "español", "en": "inglés", "de": "alemán", "fr": "francés", "nl": "neerlandés",
}

#: Datos de MUESTRA para «Ver ejemplo» / «Enviarme una prueba» en Ajustes.
SAMPLE_SHIPMENT_EMAIL: dict[str, str] = {
    "cliente": "Rotulación Levante S.L.",
    "pedido": "9553",
    "tracking": "0033260080539700026674",
    "enlace": ("https://app.cttexpress.com/AreaClientes/Views/Destinatarios.aspx"
               "?s=0033260080539700026674"),
    "agencia": "CTT Express",
}


# --- plantillas ----------------------------------------------------------------


def shipment_email_templates(session: Session) -> dict[str, dict[str, str]]:
    """Plantillas efectivas: defaults del código + overrides de Ajustes ERP."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    stored = series_config(session).get(TEMPLATES_KEY)
    return merge_templates(stored)


def merge_templates(stored: Any) -> dict[str, dict[str, str]]:
    """Defaults + overrides (asunto/cuerpo no vacíos) por idioma."""
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGS:
        base = dict(SHIPMENT_EMAIL_DEFAULTS.get(lang, SHIPMENT_EMAIL_DEFAULTS["es"]))
        over = stored.get(lang)
        if isinstance(over, dict):
            for key in ("subject", "body"):
                if str(over.get(key) or "").strip():
                    base[key] = str(over[key])
        out[lang] = base
    return out


def fill_template(lang: str, subject: str, body: str, fields: dict[str, str]) -> tuple[str, str]:
    """Sustituye los placeholders (idioma ya resuelto). Sin enlace: con courier
    conocido → «en la web de UPS»; sin courier → se quita la línea del enlace
    (queda solo el número)."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    agencia = (fields.get("agencia") or "").strip()
    enlace = (fields.get("enlace") or "").strip()
    if not enlace and agencia:
        enlace = _NO_LINK[lang].replace("{agencia}", agencia)
    values = {
        "cliente": fields.get("cliente") or _GREETING_FALLBACK[lang],
        "pedido": fields.get("pedido") or "",
        "tracking": fields.get("tracking") or "",
        "enlace": enlace,
        "agencia": agencia or _NO_AGENCY[lang],
    }

    def _fill(text: str) -> str:
        if not values["enlace"]:
            # Sin enlace ni courier: fuera la(s) línea(s) que lo llevan.
            text = "\n".join(line for line in text.split("\n") if "{enlace}" not in line)
        for key, val in values.items():
            text = text.replace("{" + key + "}", val)
        return text

    return _fill(subject), _fill(body)


def text_to_html(text: str) -> str:
    """Cuerpo de texto → HTML sencillo, con la URL de seguimiento clicable."""
    import html  # noqa: PLC0415
    import re  # noqa: PLC0415

    esc = html.escape(text)
    esc = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', esc)
    return "<p>" + esc.replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>"


def render_sample(session: Session, *, lang: str, subject: str | None = None,
                  body: str | None = None) -> dict[str, str]:
    """Plantilla del idioma rellena con datos de muestra (Ajustes ERP)."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = shipment_email_templates(session)[lang]
    s_tpl = subject if subject is not None and subject.strip() else tpl["subject"]
    b_tpl = body if body is not None and body.strip() else tpl["body"]
    s, b = fill_template(lang, s_tpl, b_tpl, SAMPLE_SHIPMENT_EMAIL)
    return {"lang": lang, "subject": s, "body_text": b, "body_html": text_to_html(b)}


# --- datos del pedido -------------------------------------------------------------


def _state(order: Any) -> dict[str, Any]:
    """Bloque del ENVÍO del pedido: el de Genei si el envío es de Genei; si no,
    el del envío con otro courier (`packing_json.envio`)."""
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.shipping_courier import external_state  # noqa: PLC0415

    genei = genei_state_of(order)
    if genei.get("shipment_code"):
        return genei
    return external_state(order)


def _set_state(order: Any, patch: dict[str, Any]) -> None:
    """Guarda en el bloque del envío (Genei u otro courier)."""
    from app.erp.integrations.genei.service import genei_state_of, set_genei_state  # noqa: PLC0415
    from app.erp.shipping_courier import set_external_state  # noqa: PLC0415

    if genei_state_of(order).get("shipment_code"):
        set_genei_state(order, patch)
    else:
        set_external_state(order, patch)


def _is_genei(order: Any) -> bool:
    from app.erp.shipping_courier import is_genei_shipment  # noqa: PLC0415

    return is_genei_shipment(order)


def brand_store(session: Session, order: Any) -> str | None:
    """Tienda (marca) del pedido: la cuenta Woo o, si no la trae, el prefijo
    del nº de pedido (BOPRIN-/FLUXLA-/ARTISJ-). None = pedido manual."""
    from app.erp.invoice_email import order_store_slug  # noqa: PLC0415

    slug = order_store_slug(session, order)
    if slug:
        return slug.strip().lower()
    prefix = str(getattr(order, "order_number", "") or "").split("-")[0].upper()
    return STORE_BY_PREFIX.get(prefix)


def manual_from_config(raw: Any) -> dict[str, str]:
    """`{es, otros}` → remitente de los pedidos manuales (con los defaults)."""
    out = dict(DEFAULT_MANUAL_FROM)
    if isinstance(raw, dict):
        for key in ("es", "otros"):
            text = str(raw.get(key) or "").strip()
            if text:
                out[key] = text
    return out


def shipment_from_alias(session: Session, order: Any, lang: str) -> tuple[str, str]:
    """`(alias, origen)` del remitente: la TIENDA del pedido web; si no (pedido
    manual o tienda sin remitente), por IDIOMA."""
    from app.erp.invoice_email import store_from_alias  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    slug = brand_store(session, order)
    if slug:
        alias = store_from_alias(session, slug)
        if alias:
            return alias, "tienda"
    manual = manual_from_config(series_config(session).get(MANUAL_FROM_KEY))
    return (manual["es"] if lang == "es" else manual["otros"]), "idioma"


def shipment_email_language(session: Session, order: Any) -> tuple[str, str]:
    """`(idioma, origen)`: pedido → cliente → país de destino del envío →
    país de la empresa → español."""
    if (getattr(order, "language", None) or "") in SUPPORTED_LANGS:
        return order.language, "pedido"
    company = None
    if getattr(order, "company_id", None):
        from app.models.crm import Company  # noqa: PLC0415

        company = session.get(Company, order.company_id)
    if company is not None and (company.language or "") in SUPPORTED_LANGS:
        return company.language, "cliente"
    derived = language_for_country(_state(order).get("dest_country") or _shipping_country(order))
    if derived in SUPPORTED_LANGS:
        return derived, "pais_destino"
    if company is not None:
        derived = language_for_country(company.country)
        if derived in SUPPORTED_LANGS:
            return derived, "pais_cliente"
    return "es", "defecto"


def _shipping_country(order: Any) -> str | None:
    from app.erp.factusol_albaran import packing_of  # noqa: PLC0415

    addr = packing_of(order).get("shipping_address")
    if isinstance(addr, dict):
        return addr.get("country") or addr.get("iso_country")
    return None


def recipient_of(session: Session, order: Any) -> str:
    """El email del DESTINATARIO del envío (el que se puso al crear el envío
    Genei, el mismo que recibía el aviso de Genei); si no consta, el del
    contacto del pedido."""
    email = str(_state(order).get("dest_email") or "").strip()
    if email:
        return email
    # El email de ENVÍO del pedido: el mismo que carga el modal de crear envío
    # (dirección de envío de la web, o factura/albarán/proforma/manual/muestra).
    try:
        from app.erp.shipping_destination import resolve_shipping_destination  # noqa: PLC0415

        campos, _origen = resolve_shipping_destination(session, order)
        email = str(campos.get("email") or "").strip()
    except Exception:  # noqa: BLE001 — sin destino resoluble, se sigue
        email = ""
    if "@" in email:
        return email
    if getattr(order, "contact_id", None):
        from app.models.crm import Contact  # noqa: PLC0415

        contact = session.get(Contact, order.contact_id)
        if contact is not None and contact.email:
            return contact.email.strip()
    return ""


def customer_order_ref(order: Any) -> str:
    """El nº de pedido que conoce el cliente: el de la TIENDA en un pedido web
    (p. ej. 9553 de ARTISJ-9553); el de BoHub en uno manual."""
    from app.erp.factusol_albaran import is_web_order  # noqa: PLC0415

    number = str(getattr(order, "order_number", "") or "")
    if is_web_order(order):
        ext = str(getattr(order, "external_id", "") or "").strip()
        if ext:
            return ext
        if "-" in number:
            return number.split("-", 1)[1]
    return number


def customer_name(session: Session, order: Any) -> str:
    """A quién se saluda: el contacto del envío; si no, el del pedido."""
    name = str(_state(order).get("dest_name") or "").strip()
    if name:
        return name
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    who = customer_names(session, [order]).get(order.id) or {}
    return str(who.get("contact_name") or who.get("company_name")
               or getattr(order, "shipping_name", "") or "").strip()


# --- composición ----------------------------------------------------------------


def build_shipment_email(
    session: Session, order: Any, *, lang: str | None = None, to: str | None = None,
) -> dict[str, Any]:
    """El aviso tal como saldría (NO envía nada): a quién, desde dónde, idioma
    (y de dónde sale), asunto, cuerpo, tracking y enlace, y qué falta."""
    state = _state(order)
    auto_lang, lang_source = shipment_email_language(session, order)
    if lang in SUPPORTED_LANGS:
        use_lang, lang_source = lang, "selector"
    else:
        use_lang = auto_lang
    from_alias, from_source = shipment_from_alias(session, order, use_lang)
    tracking = str(state.get("tracking") or getattr(order, "tracking_number", "") or "").strip()
    if _is_genei(order):
        url = str(state.get("tracking_url") or "").strip() or None
    else:
        # Otro courier: el enlace sale de la tabla por courier (si se conoce).
        from app.erp.shipping_courier import tracking_url_for  # noqa: PLC0415

        url = tracking_url_for(state.get("courier"), tracking)
    fields = {
        "cliente": customer_name(session, order),
        "pedido": customer_order_ref(order),
        "tracking": tracking,
        "enlace": url or "",
        "agencia": str(state.get("courier") or "").strip(),
    }
    tpl = shipment_email_templates(session)[use_lang]
    subject, body = fill_template(use_lang, tpl["subject"], tpl["body"], fields)
    recipient = (to or "").strip() or recipient_of(session, order)
    missing = []
    if not tracking:
        missing.append("tracking")
    if "@" not in recipient:
        missing.append("destinatario")
    return {
        "to": recipient,
        "from_alias": from_alias,
        "from_alias_source": from_source,
        "store": brand_store(session, order),
        "lang": use_lang,
        "lang_source": lang_source,
        "subject": subject,
        "body_text": body,
        "tracking": tracking or None,
        "tracking_url": url,
        "courier": fields["agencia"] or None,
        "order_ref": fields["pedido"],
        "missing": missing,
        "status": dict(state.get("customer_email") or {}),
    }


# --- envío ----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sender_user(session: Session, order: Any, actor: Any | None) -> Any | None:
    """Usuario al que se apunta el envío (la cuenta de Gmail es la de la
    organización): quien actúa; si es automático, quien creó el envío Genei;
    si no consta, un administrador."""
    from app.models.crm import AuditLog, User, UserRole  # noqa: PLC0415

    if actor is not None:
        return actor
    uid = _state(order).get("created_by_user_id")
    if not uid:
        uid = session.scalar(
            select(AuditLog.actor_user_id).where(
                AuditLog.action == "erp.genei.shipment_created",
                AuditLog.target_id == order.id,
            ).order_by(AuditLog.created_at.desc()).limit(1)
        )
    user = session.get(User, uid) if uid else None
    if user is not None and getattr(user, "is_active", True):
        return user
    return session.scalar(
        select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .order_by(User.created_at).limit(1)
    )


def send_shipment_email(
    session: Session, order: Any, *, actor: Any | None, to: str | None = None,
    lang: str | None = None, automatic: bool = False,
) -> dict[str, Any]:
    """Envía el aviso por Gmail y lo deja registrado (bloque Genei del pedido +
    evento `erp.shipment_emailed` en su timeline). Si Gmail falla, la
    excepción sube y NO se marca como enviado."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    email = build_shipment_email(session, order, lang=lang, to=to)
    if email["missing"]:
        raise ValueError("Falta " + " y ".join(email["missing"])
                         + " para enviar el aviso de envío.")
    sender = _sender_user(session, order, actor)
    if sender is None:
        raise ValueError("No hay ningún usuario con el que registrar el envío del aviso.")
    message = gmail_service.send_email(
        session,
        sender_user_id=sender.id,
        from_alias=email["from_alias"],
        from_name=None,
        to=[email["to"]],
        cc=None, bcc=None,
        subject=email["subject"],
        body_html=text_to_html(email["body_text"]),
        body_text=email["body_text"],
        contact_id=getattr(order, "contact_id", None),
    )
    previous = dict(_state(order).get("customer_email") or {})
    _set_state(order, {"customer_email": {
        **previous,
        "status": "sent",
        "sent_at": _now(),
        "to": email["to"],
        "lang": email["lang"],
        "from": email["from_alias"],
        "tracking": email["tracking"],
        "message_id": message.id,
        "automatic": automatic,
        "sends": int(previous.get("sends") or 0) + 1,
        "error": None,
    }})
    record_event(
        session, action=AUDIT_ACTION, target_type="order", target_id=order.id,
        actor=actor,
        actor_email=None if actor is not None else "BoHub (automático)",
        message=(
            f"Aviso de envío (seguimiento {email['tracking']}) enviado a {email['to']} "
            f"en {LANG_NAMES.get(email['lang'], email['lang'])} desde {email['from_alias']}"
            + (" · automático" if automatic else "")
        ),
        metadata={
            "to": email["to"], "lang": email["lang"], "lang_source": email["lang_source"],
            "from_alias": email["from_alias"], "from_alias_source": email["from_alias_source"],
            "tracking": email["tracking"], "tracking_url": email["tracking_url"],
            "message_id": message.id, "automatic": automatic,
        },
    )
    return email | {"sent": True, "message_id": message.id}


def mark_pending_on_create(order: Any, *, enabled: bool, user_id: str | None,
                           destination: dict[str, Any]) -> dict[str, Any]:
    """Datos del aviso al CREAR el envío Genei: a quién va (el destinatario del
    envío), su país (idioma) y si se enviará solo (`pending`) o no
    (`disabled`, interruptor apagado)."""
    return {
        "dest_email": str(destination.get("email") or "").strip() or None,
        "dest_name": str(destination.get("contact") or destination.get("name") or "").strip()
        or None,
        "dest_country": str(destination.get("isoCountry") or "").strip().upper() or None,
        "created_by_user_id": user_id,
        "customer_email": {"status": "pending" if enabled else "disabled",
                           "attempts": 0, "sends": 0},
    }


def mark_external_pending(order: Any, *, enabled: bool, user_id: str | None) -> None:
    """Al «📤 Marcar recogido» un envío con OTRO courier: el aviso al cliente
    queda pendiente (se manda en cuanto haya tracking), una sola vez. Si ya
    tenía estado (reintento de marcar recogido, reenvío…), no se toca."""
    from app.erp.shipping_courier import external_state, set_external_state  # noqa: PLC0415

    if external_state(order).get("customer_email"):
        return
    set_external_state(order, {
        "created_by_user_id": user_id,
        "customer_email": {"status": "pending" if enabled else "disabled",
                           "attempts": 0, "sends": 0},
    })


def maybe_send_shipment_email(
    session: Session, order: Any, *, actor: Any | None = None,
    tracking_url_fetcher: Any | None = None,
) -> dict[str, Any] | None:
    """Envío AUTOMÁTICO del aviso, una sola vez por envío. Llamar DESPUÉS de
    confirmar (commit) el estado del envío. No lanza nunca: un fallo queda
    apuntado (`customer_email.status = "error"`) y se reintenta en el siguiente
    disparo, como mucho `MAX_AUTO_ATTEMPTS` veces; luego, reenvío manual.

    `tracking_url_fetcher(código)` = URL de seguimiento de Genei si aún no se
    tiene (opcional)."""
    try:
        ce = _state(order).get("customer_email") or {}
        if ce.get("status") not in ("pending", "error"):
            return None
        if ce.get("status") == "error" and int(ce.get("attempts") or 0) >= MAX_AUTO_ATTEMPTS:
            return None
        if not _enabled(session):
            return None
        state = _state(order)
        if not (state.get("tracking") or getattr(order, "tracking_number", None)):
            return None                              # aún sin nº: se espera
        if getattr(order, "shipping_not_required", False):
            return None
        # Cerrojo de fila: dos disparos a la vez (webhook + sondeo) no mandan dos.
        session.refresh(order, with_for_update=True)
        ce = _state(order).get("customer_email") or {}
        if ce.get("status") not in ("pending", "error"):
            session.rollback()
            return None
        if (_is_genei(order) and not _state(order).get("tracking_url")
                and tracking_url_fetcher is not None):
            url = None
            try:
                url = tracking_url_fetcher(str(_state(order).get("shipment_code") or ""))
            except Exception:  # noqa: BLE001 — sin enlace se envía igual
                url = None
            if url:
                _set_state(order, {"tracking_url": url})
        _set_state(order, {"customer_email": {**ce, "status": "sending",
                                                    "attempts": int(ce.get("attempts") or 0) + 1}})
        session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("aviso de envío: no se pudo preparar (pedido %s)",
                       getattr(order, "id", "?"), exc_info=True)
        session.rollback()
        return None

    try:
        result = send_shipment_email(session, order, actor=actor, automatic=True)
        session.commit()
        logger.info("aviso de envío enviado: pedido %s → %s (%s)", order.id,
                    result["to"], result["lang"])
        return result
    except Exception as exc:  # noqa: BLE001 — nunca rompe el flujo que lo dispara
        session.rollback()
        logger.warning("aviso de envío: falló el envío (pedido %s): %s", order.id,
                       type(exc).__name__)
        try:
            session.refresh(order)
            ce = _state(order).get("customer_email") or {}
            _set_state(order, {"customer_email": {
                **ce, "status": "error", "error": str(exc)[:300], "error_at": _now(),
            }})
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
        return None


def _enabled(session: Session) -> bool:
    """Interruptor del aviso automático (config de Genei, encendido por defecto)."""
    from app.erp.api.genei import get_genei_carrier  # noqa: PLC0415
    from app.erp.integrations.genei.config import GeneiConfig  # noqa: PLC0415

    carrier = get_genei_carrier(session)
    return GeneiConfig.of(carrier).customer_email_enabled if carrier else False
