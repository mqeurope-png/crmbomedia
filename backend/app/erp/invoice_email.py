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
código. Placeholders: {cliente}, {numero} (nº de factura), {pedido} (nº de
pedido de BoHub, con separador localizado; vacío si la factura no tiene
pedido) y {referencia} («su ref.», la referencia web del pedido).

«Enviar factura al cliente» desde la ficha de pedido reutiliza TODO esto por
pedido (resuelve la factura del pedido y llama a la misma previsualización y
envío). El remitente se elige por TIENDA del pedido (alias por tienda,
configurable) → si no, por SERIE (empresa emisora) → si no, el alias del
usuario.
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
        "subject": "Factura {numero}{pedido}",
        "body": "Estimado/a {cliente}:\n\nAdjuntamos la factura {numero}"
                "{pedido}{referencia}.\n\nUn saludo.",
    },
    "en": {
        "subject": "Invoice {numero}{pedido}",
        "body": "Dear {cliente},\n\nPlease find attached invoice {numero}"
                "{pedido}{referencia}.\n\nKind regards.",
    },
    "de": {
        "subject": "Rechnung {numero}{pedido}",
        "body": "Sehr geehrte/r {cliente},\n\nanbei die Rechnung {numero}"
                "{pedido}{referencia}.\n\nMit freundlichen Grüßen.",
    },
    "fr": {
        "subject": "Facture {numero}{pedido}",
        "body": "Bonjour {cliente},\n\nVeuillez trouver ci-joint la facture "
                "{numero}{pedido}{referencia}.\n\nCordialement.",
    },
    "nl": {
        "subject": "Factuur {numero}{pedido}",
        "body": "Beste {cliente},\n\nBijgevoegd vindt u factuur {numero}"
                "{pedido}{referencia}.\n\nMet vriendelijke groet.",
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

#: Cómo se lee «· pedido X» en cada idioma para el placeholder {pedido} (nº de
#: pedido de BoHub). Va con separador para que quede bien pegado al nº de
#: factura en el asunto y en el cuerpo; vacío si la factura no tiene pedido.
_ORDER_SUFFIX: dict[str, str] = {
    "es": " · pedido {n}",
    "en": " · order {n}",
    "de": " · Bestellung {n}",
    "fr": " · commande {n}",
    "nl": " · bestelling {n}",
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


def _fill_template(
    lang: str, subject: str, body: str, *, cliente: str, numero: str,
    referencia: str, pedido: str,
) -> tuple[str, str]:
    """Sustituye los placeholders en `subject`/`body` (idioma ya resuelto).
    {referencia} y {pedido} llevan su separador localizado y desaparecen si
    no hay dato."""
    ref_txt = ""
    if referencia:
        ref_txt = _REF_SUFFIX.get(lang, _REF_SUFFIX["es"]).format(ref=referencia)
    pedido_txt = ""
    if pedido:
        pedido_txt = _ORDER_SUFFIX.get(lang, _ORDER_SUFFIX["es"]).format(n=pedido)
    fields = {
        "cliente": cliente or "", "numero": numero,
        "referencia": ref_txt, "pedido": pedido_txt,
    }

    def _fill(text: str) -> str:
        for key, val in fields.items():
            text = text.replace("{" + key + "}", val)
        return text

    return _fill(subject), _fill(body)


def render_invoice_email(
    session: Session, *, lang: str, cliente: str, numero: str,
    referencia: str, pedido: str = "",
) -> tuple[str, str]:
    """`(asunto, cuerpo_texto)` de la plantilla del idioma, con los
    placeholders sustituidos. Idioma no soportado → español. `pedido` es el nº
    de pedido de BoHub (vacío → el placeholder {pedido} desaparece)."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = invoice_email_templates(session)[lang]
    return _fill_template(
        lang, tpl["subject"], tpl["body"], cliente=cliente, numero=numero,
        referencia=referencia, pedido=pedido,
    )


#: Lote 2 · PR-2 — datos de MUESTRA con los que Ajustes ERP enseña cómo queda
#: una plantilla («Ver ejemplo» / «Enviarme una prueba»). Rellenan los cuatro
#: placeholders para que se vea el efecto de cada uno.
SAMPLE_INVOICE_EMAIL: dict[str, str] = {
    "cliente": "Rotulación Levante S.L.",
    "numero": "5-000118",
    "pedido": "BP-2479",
    "referencia": "BOP-002479",
}


def render_sample_invoice_email(
    session: Session, *, lang: str, subject: str | None = None,
    body: str | None = None,
) -> dict[str, str]:
    """Plantilla del idioma rellena con los datos de muestra
    (`SAMPLE_INVOICE_EMAIL`), con la misma sustitución que el envío real.
    `subject` / `body` no vacíos sustituyen a la plantilla guardada (es lo que
    se está escribiendo en Ajustes, aún sin guardar); vacíos → la guardada o,
    si no hay, la por defecto del idioma. Idioma no soportado → español.
    Devuelve `{lang, subject, body_text, body_html}`."""
    lang = lang if lang in SUPPORTED_LANGS else "es"
    tpl = invoice_email_templates(session)[lang]
    subject_tpl = subject if subject is not None and subject.strip() else tpl["subject"]
    body_tpl = body if body is not None and body.strip() else tpl["body"]
    rendered_subject, rendered_body = _fill_template(
        lang, subject_tpl, body_tpl, **SAMPLE_INVOICE_EMAIL,
    )
    return {
        "lang": lang,
        "subject": rendered_subject,
        "body_text": rendered_body,
        "body_html": _text_to_html(rendered_body),
    }


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


#: Remitente (alias de envío) por SERIE = empresa emisora. Precarga: serie 2
#: (MQ Europe / artisJet) → info@artisjet-printers.eu; serie 5 (Streamtec:
#: boprint, flux) → pedidos@streamtec.es. Serie 1 (Bomedia) sin definir. Es
#: CONFIGURABLE en /erp/settings (blob `factusol_series_json.series_email_from`,
#: sin migración); un valor vacío en la config BORRA el default de esa serie.
DEFAULT_SERIES_EMAIL_FROM: dict[int, str] = {
    2: "info@artisjet-printers.eu",
    5: "pedidos@streamtec.es",
}


def series_email_from_config(raw: Any) -> dict[int, str]:
    """`{serie → alias remitente}` configurado, partiendo de los precargados."""
    out = dict(DEFAULT_SERIES_EMAIL_FROM)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                serie = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            text = str(value or "").strip()
            if text:
                out[serie] = text
            else:
                out.pop(serie, None)  # vacío = sin remitente propio para esa serie
    return out


def series_from_alias(session: Session, serie: int | None) -> str | None:
    """Alias de envío de la EMPRESA EMISORA de esa serie (o None si la serie no
    tiene remitente configurado → el caller cae al alias del usuario)."""
    if serie is None:
        return None
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    mapping = series_email_from_config(series_config(session).get("series_email_from"))
    return mapping.get(int(serie))


#: Remitente por TIENDA (slug de la cuenta Woo: artisjet / boprint /
#: fluxlasers…). Más fino que la serie: dos tiendas de la misma empresa
#: emisora (boprint y flux, ambas serie 5) pueden enviar desde alias
#: distintos. Precarga = el alias de su serie; CONFIGURABLE en /erp/settings
#: (blob `factusol_series_json.store_email_from`, sin migración). Un valor
#: vacío en la config BORRA el default de esa tienda (cae a la serie).
DEFAULT_STORE_EMAIL_FROM: dict[str, str] = {
    "artisjet": "info@artisjet-printers.eu",
    "boprint": "pedidos@streamtec.es",
    "fluxlasers": "pedidos@streamtec.es",
}


def store_email_from_config(raw: Any) -> dict[str, str]:
    """`{tienda → alias remitente}` configurado, partiendo de los precargados.
    Las claves se normalizan a minúsculas (slug de la cuenta Woo)."""
    out = dict(DEFAULT_STORE_EMAIL_FROM)
    if isinstance(raw, dict):
        for key, value in raw.items():
            slug = str(key or "").strip().lower()
            if not slug:
                continue
            text = str(value or "").strip()
            if text:
                out[slug] = text
            else:
                out.pop(slug, None)  # vacío = sin remitente propio para esa tienda
    return out


def store_from_alias(session: Session, store_slug: str | None) -> str | None:
    """Alias de envío de esa TIENDA (o None si no tiene remitente propio → el
    caller cae a la serie / al usuario)."""
    if not store_slug:
        return None
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    mapping = store_email_from_config(series_config(session).get("store_email_from"))
    return mapping.get(str(store_slug).strip().lower())


def order_store_slug(session: Session, order: Any) -> str | None:
    """Slug de la tienda Woo del pedido (`IntegrationAccount.account_id`), o
    None si el pedido no es de tienda / no tiene cuenta."""
    if order is None or not getattr(order, "store_id", None):
        return None
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    store = session.get(IntegrationAccount, order.store_id)
    return store.account_id if store is not None else None


def company_contacts_for_order(session: Session, order: Any) -> list[dict[str, Any]]:
    """Contactos candidatos a destinatarios de los envíos del pedido:
    `{id, name, email, has_email, is_order_contact}`. Incluye los contactos de
    la EMPRESA del pedido y, además, el propio contacto del pedido aunque no
    esté asociado a la empresa (frecuente en pedidos web). Ordenados por nombre;
    un contacto con email inválido/ausente sale `has_email=False` (se enseña
    deshabilitado). Sin pedido → lista vacía."""
    if order is None:
        return []
    from app.models.crm import Contact  # noqa: PLC0415

    rows: list[Any] = []
    seen: set[str] = set()
    if getattr(order, "company_id", None):
        rows = list(session.scalars(
            select(Contact).where(
                Contact.company_id == order.company_id,
                Contact.is_active.is_(True),
            ).order_by(Contact.last_name.asc(), Contact.first_name.asc())
        ))
        seen = {c.id for c in rows}
    # El contacto del pedido siempre debe poder elegirse, aunque no esté
    # vinculado a la empresa (el «Para» que se precargaba hasta ahora).
    order_contact_id = getattr(order, "contact_id", None)
    if order_contact_id and order_contact_id not in seen:
        oc = session.get(Contact, order_contact_id)
        if oc is not None:
            rows = [oc, *rows]
    out: list[dict[str, Any]] = []
    for c in rows:
        email = (c.email or "").strip() if c.is_email_valid else ""
        name = " ".join(p for p in (c.first_name, c.last_name) if p).strip()
        out.append({
            "id": c.id,
            "name": name or email or "—",
            "email": email or None,
            "has_email": bool(email),
            "is_order_contact": c.id == order_contact_id,
        })
    return out


def resolve_primary_contact(
    session: Session, order: Any, recipients: list[str],
) -> str | None:
    """Contacto al que se ENLAZA el correo (para el timeline), elegido entre los
    destinatarios: el contacto del pedido si su email está entre ellos; si no,
    el primer destinatario (en orden) que sea un contacto de la empresa; si
    ninguno lo es, el contacto del pedido (comportamiento de siempre). La
    auditoría registra aparte TODOS los destinatarios."""
    if order is None:
        return None
    emails = [e.strip().lower() for e in recipients if e and e.strip()]
    email_set = set(emails)
    from app.models.crm import Contact  # noqa: PLC0415

    order_contact_id = getattr(order, "contact_id", None)
    if order_contact_id:
        oc = session.get(Contact, order_contact_id)
        if oc is not None and (oc.email or "").strip().lower() in email_set:
            return oc.id
    if getattr(order, "company_id", None) and emails:
        by_email: dict[str, str] = {}
        for c in session.scalars(
            select(Contact).where(
                Contact.company_id == order.company_id,
                Contact.email.isnot(None),
            )
        ):
            key = (c.email or "").strip().lower()
            if key:
                by_email.setdefault(key, c.id)
        for e in emails:
            if e in by_email:
                return by_email[e]
    return order_contact_id


class OrderInvoiceMismatch(ValueError):
    """La factura pedida NO es la del pedido indicado (o el pedido no tiene
    factura): nunca se envía la factura de un pedido desde otro."""


def resolve_order_for_invoice(
    session: Session, order_id: str, *, serie: int, codigo: int,
    referencia: Any = None, cliente_codigo: Any = None,
):
    """Pedido `order_id` VERIFICADO como dueño de la factura (serie, código).
    Es el camino de la ficha de pedido: el pedido se conoce, no se adivina a
    partir de la factura. Vale si:
      - su referencia común es la REFFAC de la factura; o
      - guarda ese nº de factura con la misma serie; o
      - guarda ese nº de factura SIN serie (CODFAC desnudo) y nada lo
        contradice: ni la referencia (si ambas constan y difieren) ni el
        cliente (CLIFAC ≠ empresa del pedido), salvo que el cliente coincida.
    Si no, `OrderInvoiceMismatch` (nunca se envía la factura de otro pedido)."""
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        order_composed_ref,
        order_customer_code,
        order_invoice_serie,
    )
    from app.erp.models import Order  # noqa: PLC0415

    order = session.get(Order, order_id)
    if order is None:
        raise OrderInvoiceMismatch("El pedido no existe.")
    _, o_codigo = parse_invoice_number(order.factusol_invoice_number)
    o_serie = order_invoice_serie(order)
    ref = str(referencia or "").strip().upper()
    o_ref = order_composed_ref(session, order)
    inv_cli = str(cliente_codigo or "").strip()
    o_cli = order_customer_code(session, order) or ""
    ref_match = bool(ref) and o_ref == ref
    cli_match = bool(inv_cli) and o_cli == inv_cli
    ref_conflict = bool(ref) and bool(o_ref) and o_ref != ref
    cli_conflict = bool(inv_cli) and bool(o_cli) and o_cli != inv_cli
    same_number = o_codigo == int(codigo)
    if o_serie is not None:
        ok = ref_match or (same_number and o_serie == int(serie))
    else:
        ok = ref_match or (same_number and (cli_match or not (ref_conflict or cli_conflict)))
    if not ok:
        raise OrderInvoiceMismatch(
            f"La factura {serie}-{int(codigo):06d} no es la del pedido "
            f"{order.order_number}."
        )
    return order


def erp_configured_senders(session: Session) -> set[str]:
    """Alias remitentes configurados en Ajustes ERP (por tienda y por serie),
    en minúsculas. Son los que el ERP propone como «De» de la factura."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = series_config(session)
    out = {
        v.strip().lower()
        for v in store_email_from_config(cfg.get("store_email_from")).values()
        if v and v.strip()
    }
    out |= {
        v.strip().lower()
        for v in series_email_from_config(cfg.get("series_email_from")).values()
        if v and v.strip()
    }
    return out


def check_sender_alias(session: Session, user: Any, alias: str) -> dict[str, Any]:
    """¿Puede `user` enviar desde `alias`? `{ok, source, reason}`.

    Bug 3 (#426): `gmail:sync_aliases` refleja TODOS los send-as de la cuenta
    Google compartida en `user_email_alias_prefs`, pero marca `is_allowed=0`
    a todo alias que no sea el email propio del usuario (para que cada
    comercial solo vea los suyos en el compositor). `pedidos@streamtec.es`
    es un alias de la organización, no de nadie → quedaba oculto y el ERP
    lo rechazaba aunque en Gmail esté verificado.

    Criterio ahora:
      1. Alias en las preferencias PERMITIDAS del usuario → ok (`preferencia`).
      2. Alias configurado como remitente en Ajustes ERP (tienda o serie) y
         send-as VERIFICADO en Gmail (lista en vivo; si Gmail no responde, el
         espejo local aunque esté oculto) → ok (`gmail` / `gmail_cache`). Se
         refresca el espejo local con lo que dice Gmail (sin cambiar la
         visibilidad que eligió el usuario).
      3. Cualquier otro alias → no (nadie suplanta un alias ajeno que el ERP
         no tenga configurado)."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    wanted = (alias or "").strip()
    key = wanted.lower()
    if not key:
        return {"ok": False, "source": None, "reason": "sin_alias"}
    rows = session.scalars(
        select(UserEmailAliasPref).where(UserEmailAliasPref.user_id == user.id)
    ).all()
    mine = {r.alias_email.strip().lower(): r for r in rows}
    row = mine.get(key)
    if row is not None and row.is_allowed:
        return {"ok": True, "source": "preferencia", "reason": None}
    if key not in erp_configured_senders(session):
        return {"ok": False, "source": None, "reason": "alias_not_allowed"}
    try:
        gmail = gmail_service.list_aliases(session, user.id)
    except Exception:  # noqa: BLE001 — Gmail desconectado / sin scope / caído
        if row is not None:
            return {"ok": True, "source": "gmail_cache", "reason": None}
        return {"ok": False, "source": None, "reason": "gmail_unavailable"}
    verified = {
        (a.get("send_as_email") or "").strip().lower(): a for a in gmail
    }
    found = verified.get(key)
    if found is None:
        return {"ok": False, "source": None, "reason": "not_in_gmail"}
    # Espejo local al día (fila oculta si no existía: la visibilidad en el
    # compositor sigue siendo decisión del usuario / del sync).
    display = found.get("display_name") or None
    if row is None:
        session.add(UserEmailAliasPref(
            user_id=user.id, alias_email=found.get("send_as_email") or wanted,
            is_allowed=False, is_default=False, gmail_display_name=display,
        ))
        session.flush()
    elif display and row.gmail_display_name != display:
        row.gmail_display_name = display
        session.flush()
    return {"ok": True, "source": "gmail", "reason": None}


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
    order_id: str | None = None,
) -> dict[str, Any]:
    """Datos de la PREVISUALIZACIÓN (obligatoria antes de enviar): a quién,
    asunto, idioma + procedencia, cuerpo editable y el nombre del adjunto.
    NO envía nada ni genera el PDF todavía.

    `order_id`: el pedido desde cuya ficha se envía; se VERIFICA que la
    factura es suya (`resolve_order_for_invoice`, si no
    `OrderInvoiceMismatch`). Sin él, el pedido se localiza a partir de la
    factura (serie + número + referencia + cliente; nunca por número desnudo,
    que es homónimo entre series)."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        extract_document_data,
        load_raw_document,
        order_customer_code,
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
    order = _order_for(session, order_id, serie=serie, codigo=codigo, data=data)
    suggestion = suggest_pdf_language(session, "facturas", data, order=order)
    lang = lang_override if lang_override in SUPPORTED_LANGS else suggestion["lang"]
    source = "selector" if lang_override in SUPPORTED_LANGS else suggestion["source"]

    # Destinatario: el contacto del pedido de ESTA factura (nunca el de un
    # pedido homónimo). Además se contrasta el cliente de la factura (CLIFAC)
    # con la empresa del pedido: si no coinciden, se avisa en la
    # previsualización (posible vínculo erróneo) en vez de callar.
    to = ""
    if order is not None and order.contact_id:
        from app.models.crm import Contact  # noqa: PLC0415

        contact = session.get(Contact, order.contact_id)
        to = (contact.email or "") if contact is not None else ""
    invoice_cli = str((data.get("cliente") or {}).get("codigo") or "").strip()
    order_cli = order_customer_code(session, order) if order is not None else None
    customer_mismatch = bool(invoice_cli and order_cli and invoice_cli != order_cli)

    subject, body_text = render_invoice_email(
        session, lang=lang, cliente=data["cliente"]["nombre"],
        numero=data["numero"], referencia=data["referencia"],
        pedido=order.order_number if order is not None else "",
    )
    reply_to = find_reply_target(session, order)
    # Remitente, por orden: el alias de la TIENDA del pedido (flux / artis /
    # boprint…, configurable en /erp/settings); si no, el de la EMPRESA
    # EMISORA de la serie; si no, el alias por defecto del usuario. El envío
    # valida luego que sea un send-as del usuario.
    store_slug = order_store_slug(session, order)
    store_alias = store_from_alias(session, store_slug)
    serie_alias = None if store_alias else series_from_alias(session, serie)
    from_alias = store_alias or serie_alias or default_from_alias(session, current_user)
    from_alias_source = (
        "tienda" if store_alias else ("serie" if serie_alias else "usuario")
    )
    # ¿Se podrá enviar desde ese alias? Mismo criterio que el envío (send-as
    # verificado de Gmail); se avisa ya en la previsualización, no al pulsar.
    alias_check = (
        check_sender_alias(session, current_user, from_alias)
        if from_alias else {"ok": False, "reason": "sin_alias"}
    )
    return {
        "serie": serie, "codigo": codigo,
        "numero": data["numero"],
        "to": to,
        "lang": lang, "lang_source": source,
        "subject": subject,
        "body_text": body_text,
        "from_alias": from_alias,
        # De dónde sale el remitente: "tienda", "serie" (empresa emisora) o
        # "usuario".
        "from_alias_source": from_alias_source,
        # None = no se pudo comprobar contra Gmail (desconectado / sin scope).
        "from_alias_ok": alias_check.get("ok"),
        "from_alias_problem": alias_check.get("reason"),
        "store": store_slug,
        "attachment_filename": pdf_filename("facturas", data, lang),
        "reply_to_message_id": reply_to,
        "replies_to_thread": reply_to is not None,
        "order_id": order.id if order is not None else None,
        "order_number": order.order_number if order is not None else None,
        # Cliente de la factura en FACTUSOL y si NO coincide con la empresa
        # del pedido (posible vínculo erróneo: el operador debe mirar el «Para»).
        "invoice_customer": (data.get("cliente") or {}).get("nombre") or None,
        "customer_mismatch": customer_mismatch,
        # Contactos de la empresa del pedido para el selector de destinatarios
        # (además del «Para» libre). Los sin email salen deshabilitados.
        "company_contacts": company_contacts_for_order(session, order),
    }


def _find_order(session: Session, data: dict[str, Any]):
    from app.erp.factusol_pdf import _find_order_for_document  # noqa: PLC0415

    return _find_order_for_document(session, "facturas", data)


def _order_for(
    session: Session, order_id: str | None, *, serie: int, codigo: int,
    data: dict[str, Any],
):
    """Pedido de la factura: el indicado (verificado) o el localizado."""
    if order_id:
        return resolve_order_for_invoice(
            session, order_id, serie=serie, codigo=codigo,
            referencia=data.get("referencia"),
            cliente_codigo=(data.get("cliente") or {}).get("codigo"),
        )
    return _find_order(session, data)


def send_invoice_email(
    session: Session,
    client: FactusolClient,
    *,
    serie: int,
    codigo: int,
    ejercicio: str,
    current_user: Any,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
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
    order_id: str | None = None,
) -> dict[str, Any]:
    """Genera el PDF de la factura y lo envía por Gmail, ligado al contacto
    del pedido y (si procede) al hilo. Registra el envío en el timeline del
    pedido. Si el envío falla, propaga la excepción y NO registra nada: la
    factura NO queda marcada como enviada y el PDF se puede regenerar.

    `order_id`: pedido desde cuya ficha se envía, VERIFICADO como dueño de la
    factura (si no, `OrderInvoiceMismatch` antes de enviar nada); sin él se
    localiza por serie + número + referencia + cliente.
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
    # Antes de generar nada: el pedido (verificado si viene de la ficha).
    order = _order_for(session, order_id, serie=serie, codigo=codigo, data=data)
    company = company if company is not None else company_for_serie(session, serie)
    pdf = generate_document_pdf(
        data, company=company, lang=lang,
        logo=logo if logo is not None else logo_path_for_serie(serie),
        variant=variant, bank=bank,
    )
    filename = pdf_filename("facturas", data, lang, variant)
    body_html = _text_to_html(body_text)
    # El correo se enlaza a UN contacto para el timeline (el principal entre los
    # destinatarios); la auditoría registra aparte a TODOS.
    primary_contact = resolve_primary_contact(
        session, order, list(to) + list(cc or []),
    )

    # El envío es la parte irreversible: si falla, la excepción sube y no se
    # registra nada (el caller devuelve error y el PDF se puede reintentar).
    message = gmail_service.send_email(
        session,
        sender_user_id=current_user.id,
        from_alias=from_alias,
        from_name=None,
        to=to,
        cc=cc or None, bcc=bcc or None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        contact_id=primary_contact,
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
        destinatarios = list(to) + list(cc or [])
        record_event(
            session,
            action="erp.invoice_emailed",
            target_type="order",
            target_id=order.id,
            actor=current_user,
            message=(
                f"Factura {data['numero']} enviada a {', '.join(destinatarios)} "
                f"en {_LANG_NAMES.get(lang, lang)}"
            ),
            metadata={
                "factura": data["numero"], "to": list(to),
                "cc": list(cc or []), "bcc": list(bcc or []), "lang": lang,
                "message_id": message.id, "thread_id": message.thread_id,
                "attachment": filename,
            },
        )
        session.commit()

    return {
        "sent": True,
        "message_id": message.id,
        "thread_id": message.thread_id,
        "to": list(to),
        "cc": list(cc or []),
        "bcc": list(bcc or []),
        "lang": lang,
        "numero": data["numero"],
        "attachment_filename": filename,
    }
