"""Mapper WooCommerce Order → BoHub ERP Order (Fase B PR B-2).

Reglas del spec (decisiones cerradas):
- external_source = 'woocommerce', store_id = integration_accounts.id.
- payment_status: paid si date_paid seteado; pending si status=processing
  sin date_paid (excepción visible en Cola PEDIDOS, no aprobable).
- preparation_status = pending_review (entra en Cola PEDIDOS).
- Auto-crea contact por email si no existe.
- Auto-crea company por (CIF+razón social) si el pedido los trae y no
  existe empresa con ese CIF.
- Cada línea busca product_sku_mapping por (woo_sku, store_id) con
  confirmed_at NOT NULL → si existe, setea codart; si no, la línea queda
  con codart NULL y NO se crea ninguna excepción. El ERP confía en la
  fuente: un pedido de WooCommerce es válido tal cual llega (B-2-fix5).

Idempotente: si ya existe Order con (external_source='woocommerce',
external_id=str(woo_id), store_id) hace UPDATE conservativo (no pisa
estados avanzados ni reescribe las líneas; en un pedido TRAÍDO A MANO deja
constancia de lo que la web haya cambiado desde que se trajo).

Sin duplicados: la búsqueda/alta va bajo un cerrojo por tienda (la fila de
`integration_accounts`, `SELECT … FOR UPDATE`), así que el webhook, el
backfill, «pagados que faltan» y «Traer pedido web» no crean dos a la vez.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.language import (
    language_for_country,
    normalize_country,
    normalize_language,
)
from app.erp.models import (
    Order,
    OrderLine,
    OrderSource,
    PaymentStatus,
    PreparationStatus,
    ProductSkuMapping,
)
from app.erp.sat_autoenqueue import enqueue_paid_order
from app.models.crm import Company, Contact
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Valores de `OrderLine.line_kind` que rellena este mapper (NULL = mercancía).
LINE_KIND_SHIPPING = "shipping"
LINE_KIND_FEE = "fee"


@dataclass
class ImportOutcome:
    order_id: str | None
    created: bool          # True si es alta, False si update
    contact_created: bool
    company_created: bool
    unmapped_skus: list[str]
    #: Estado Woo por el que NO se creó el pedido (regla «solo processing»);
    #: None si se creó o se actualizó.
    skipped_status: str | None = None


#: Regla de creación (pedida por Bart): un pedido de WooCommerce solo se CREA en
#: BoHub cuando ha ENTRADO EN EL FLUJO: `processing` (pagado / en preparación),
#: `completed` (servido) o `refunded` (pagado y devuelto). Los `pending`,
#: `on-hold` (aún sin pagar), `failed`, `draft`/`checkout-draft` y `cancelled`
#: de un pedido que BoHub no conoce se IGNORAN: son carritos, no pedidos. Solo
#: gobierna la creación: los pedidos ya importados se siguen actualizando
#: (`woo_status`) con cualquier cambio de estado, como hace #376, y el que
#: pasa a `processing` se crea en ese momento.
CREATE_ON_STATUSES: frozenset[str] = frozenset({"processing", "completed", "refunded"})


def should_create_order(woo_order: dict[str, Any]) -> bool:
    return (_woo_status(woo_order) or "") in CREATE_ON_STATUSES


#: «Traer pedido web» (decisión de Bart, 28/10): un pedido `on-hold` de la
#: tienda (transferencia aún sin cobrar) se puede CREAR en BoHub si alguien de
#: la oficina lo trae a mano. Es la única excepción a `CREATE_ON_STATUSES` y
#: solo con la autorización explícita (`TraidoAMano`); el webhook y el backfill
#: siguen ignorando los `on-hold` que nadie ha traído.
TRAER_A_MANO_STATUSES: frozenset[str] = frozenset({"on-hold"})


@dataclass(frozen=True)
class TraidoAMano:
    """Autorización de «Traer pedido web»: quién lo trae. Traerlo ES la
    autorización (el pedido es normal aunque aún no esté cobrado); se sella en
    `Order.manual_web_import_at/_by_user_id` solo si el pedido se CREA por ella
    (un `processing` traído entra por el camino normal: ya está pagado)."""

    user_id: str | None


def import_woo_order(
    session: Session, *, store: IntegrationAccount, woo_order: dict[str, Any],
    traido_a_mano: TraidoAMano | None = None,
) -> ImportOutcome:
    """Idempotente + dedup por (external_source, external_id, store_id).

    Punto ÚNICO de ingesta (lo usan el webhook, el sync/backfill, «pagados que
    faltan» y «Traer pedido web»): aquí vive la regla «solo se crea en
    `processing`». Un pedido desconocido en otro estado devuelve
    `skipped_status` sin tocar nada — ni siquiera crea el contacto/empresa (se
    resuelven DESPUÉS del filtro a propósito). La excepción es `traido_a_mano`:
    con ella un `on-hold` se crea por este MISMO camino (mismas líneas, cliente,
    portes, totales y pasos posteriores) y queda sellado como traído a mano.

    Antes de buscar el pedido se coge el cerrojo de la tienda (ver
    `_bloquear_tienda`): el cerrojo dura hasta el commit del llamante. Si otra
    importación ha creado este pedido mientras se esperaba el cerrojo, lanza
    `ImportacionSimultanea` (se reintenta; nunca se duplica)."""
    external_id = str(woo_order.get("id") or "")
    if not external_id:
        raise ValueError("payload sin id de pedido Woo")

    _bloquear_tienda(session, store)
    existing = _pedido_existente(session, store, external_id)
    status = _woo_status(woo_order) or ""
    por_traer = (
        existing is None and traido_a_mano is not None
        and not should_create_order(woo_order) and status in TRAER_A_MANO_STATUSES
    )
    if existing is None and not (should_create_order(woo_order) or por_traer):
        logger.info(
            "woo import: pedido %s (%s) ignorado: estado %r, solo se crea en %s",
            external_id, store.account_id, status or "?", sorted(CREATE_ON_STATUSES),
        )
        return ImportOutcome(
            order_id=None, created=False, contact_created=False,
            company_created=False, unmapped_skus=[], skipped_status=status or "?",
        )

    email = _pick_email(woo_order)
    contact, contact_created = _resolve_contact(session, email, woo_order)

    company, company_created = _resolve_company(session, woo_order)
    if company is not None and contact.company_id is None:
        contact.company_id = company.id
        session.flush()

    if existing is not None:
        _refresh_existing(session, existing, woo_order, contact, company, store)
        return ImportOutcome(
            order_id=existing.id, created=False,
            contact_created=contact_created, company_created=company_created,
            unmapped_skus=_unmapped_from(session, existing.id),
        )

    order = _create_order(session, woo_order, store, contact, company)
    if por_traer and traido_a_mano is not None:
        # Traído a mano: quién y cuándo. Es lo ÚNICO que lo distingue del
        # pedido que el importador habría creado al pasar a `processing`.
        order.manual_web_import_at = datetime.now(UTC)
        order.manual_web_import_by_user_id = traido_a_mano.user_id
    session.flush()
    # La empresa CRM por la vía fiable: el CLIENTE de FACTUSOL del pedido
    # (REFPCL → F_PCL.CLIPCL → F_CLI). El billing de Woo no siempre trae NIF y
    # entonces `_resolve_company` se quedaba en None. Best-effort: si FACTUSOL
    # no responde (o FesteWeb aún no ha creado el F_PCL) el pedido entra igual
    # y el backfill lo recupera.
    _link_company_from_factusol(session, order, woo_order)
    _apply_lines(session, order, woo_order, store)
    session.flush()
    # B-2-fix4: pedidos anteriores a la fecha de corte de la tienda se
    # auto-marcan como procesados externamente (no entran a Cola PEDIDOS).
    _auto_mark_external_if_before_cutoff(session, order, store)
    # Lote 4: un pedido web recién creado y YA pagado entra directo a la Cola
    # SAT («Por embalar»), sin esperar aprobación. Se hace DESPUÉS del corte
    # externo: un pedido auto-externalizado ya no está en `pending_review`, así
    # que el guard de `enqueue_paid_order` lo deja fuera. Actor = None (sistema).
    enqueue_paid_order(session, order, actor=None)
    return ImportOutcome(
        order_id=order.id, created=True,
        contact_created=contact_created, company_created=company_created,
        unmapped_skus=_unmapped_from(session, order.id),
    )


# --- cerrojo por tienda -----------------------------------------------------


def _bloquear_tienda(session: Session, store: IntegrationAccount) -> None:
    """Cerrojo por tienda: `SELECT … FOR UPDATE` de su fila de
    `integration_accounts`. No hay restricción única en `orders`, así que sin
    esto el webhook y «Traer pedido web» (o dos clics) podrían crear el mismo
    pedido dos veces. Dura hasta el commit/rollback del llamante. En SQLite
    (tests) no hace nada: no hay concurrencia."""
    session.execute(
        select(IntegrationAccount.id)
        .where(IntegrationAccount.id == store.id)
        .with_for_update()
    )


class ImportacionSimultanea(RuntimeError):
    """Otra transacción ha creado ESTE pedido mientras esta esperaba el cerrojo
    de la tienda. Lo que esta transacción lee es anterior a ese alta (MySQL,
    REPEATABLE READ), así que seguir podría duplicar contacto/empresa o leer
    sus líneas vacías: se para sin tocar nada y el llamante reintenta con una
    transacción nueva (el webhook, con el reintento de RQ; `import_order_from_event`
    —backfill, «Reimportar pedido»—, en el momento; «Traer pedido web» devuelve
    el que hay). `order_id` = el pedido que ya existe."""

    def __init__(self, order_id: str):
        super().__init__(f"pedido {order_id} creado a la vez por otra importación")
        self.order_id = order_id


def _pedido_existente(
    session: Session, store: IntegrationAccount, external_id: str,
) -> Order | None:
    """El pedido de BoHub de ese pedido Woo (tienda + id), visto DESPUÉS del
    cerrojo.

    En MySQL (REPEATABLE READ) una lectura normal usa la foto que la
    transacción tomó en su primera lectura —antes del cerrojo—, así que no
    vería el pedido que otra transacción acaba de crear y soltar. Si aquí no
    aparece, se vuelve a mirar por una conexión aparte (foto nueva): si ahora
    sí está, lanza `ImportacionSimultanea` en vez de crear un duplicado."""
    filtro = (
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id == external_id,
        Order.store_id == store.id,
    )
    existing = session.scalar(select(Order).where(*filtro))
    if existing is not None:
        return existing
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        return None
    with bind.engine.connect() as conn:
        order_id = conn.scalar(select(Order.id).where(*filtro).limit(1))
    if order_id is not None:
        logger.info(
            "woo import: pedido %s (%s) creado a la vez por otra importación; "
            "se reintenta con una transacción nueva", external_id, store.account_id,
        )
        raise ImportacionSimultanea(str(order_id))
    return None


# --- contact / company ------------------------------------------------------


def _pick_email(woo: dict[str, Any]) -> str:
    billing = woo.get("billing") or {}
    raw = (billing.get("email") or "").strip().lower()
    if not raw or not _EMAIL_RE.match(raw):
        raise ValueError(f"pedido Woo sin email válido: {raw!r}")
    return raw


def _resolve_contact(
    session: Session, email: str, woo: dict[str, Any],
) -> tuple[Contact, bool]:
    existing = session.scalar(
        select(Contact).where(func.lower(Contact.email) == email)
    )
    if existing is not None:
        return existing, False
    billing = woo.get("billing") or {}
    contact = Contact(
        first_name=(billing.get("first_name") or email.split("@")[0] or "Lead").strip(),
        last_name=(billing.get("last_name") or "").strip() or None,
        email=email,
        phone=(billing.get("phone") or "").strip() or None,
        origin="woocommerce",
        origin_account_id=f"woocommerce:{woo.get('_store_slug', '')}",
        address_city=(billing.get("city") or "").strip() or None,
        address_country=(billing.get("country") or "").strip() or None,
        address_postal_code=(billing.get("postcode") or "").strip() or None,
        address_line=", ".join(
            p for p in [
                (billing.get("address_1") or "").strip(),
                (billing.get("address_2") or "").strip(),
            ] if p
        ) or None,
    )
    session.add(contact)
    session.flush()
    return contact, True


def _resolve_company(
    session: Session, woo: dict[str, Any],
) -> tuple[Company | None, bool]:
    """Auto-crea empresa si el billing trae CIF + razón social. Sin CIF NO
    hay empresa (evitamos duplicar 'Particular' N veces)."""
    billing = woo.get("billing") or {}
    name = (billing.get("company") or "").strip()
    cif = _extract_cif(woo)
    if not (name and cif):
        return None, False
    normalised_cif = cif.upper().replace(" ", "").replace("-", "")
    existing = session.scalar(select(Company).where(
        func.upper(func.replace(func.replace(Company.tax_id, " ", ""), "-", ""))
        == normalised_cif
    ))
    if existing is not None:
        return existing, False
    # E4-fix3: guardar el país YA normalizado a ISO2 (el billing de Woo ya
    # viene en ISO2, pero se pasa por el normalizador por consistencia y
    # para alimentar la cascada de idioma). None si el billing no lo trae.
    country = normalize_country(billing.get("country"))
    company = Company(
        name=name, tax_id=normalised_cif, source="woocommerce",
        country=country,
    )
    session.add(company)
    session.flush()
    return company, True


def _link_company_from_factusol(
    session: Session, order: Order, woo: dict[str, Any],
) -> None:
    """Resuelve y vincula la empresa CRM del pedido web por su cliente de
    FACTUSOL. Idempotente y best-effort: si ya está resuelto no toca nada y
    cualquier fallo de FACTUSOL se registra sin romper la importación.

    Se llama con el cerrojo de la tienda cogido (`_bloquear_tienda`): la
    lectura va con el cliente FACTUSOL acotado (`importando=True`: espera
    corta, sin reintentos, con presupuesto), para que un DELSOL lento no deje
    parada la ingesta de toda la tienda."""
    from app.erp.web_order_company import ensure_web_order_company  # noqa: PLC0415

    try:
        result = ensure_web_order_company(
            session, order, fallback_nif=_extract_cif(woo), importando=True,
        )
    except Exception:  # noqa: BLE001 — la ingesta de Woo nunca cae por esto
        logger.warning(
            "woo import: empresa de %s no resuelta (error inesperado)",
            order.order_number, exc_info=True,
        )
        return
    if result.resolved:
        logger.info(
            "woo import: pedido %s → empresa %s (F_CLI %s)%s",
            order.order_number, result.company.id, result.codcli,
            " [creada]" if result.created else "",
        )
    elif result.reason not in (None, "ya_tenia_empresa"):
        logger.info(
            "woo import: pedido %s sin empresa (%s)", order.order_number, result.reason,
        )


def _extract_cif(woo: dict[str, Any]) -> str | None:
    """WooCommerce España guarda el CIF en `billing.vat` (Plugin YITH/
    similar) o en `meta_data` con clave `_billing_nif`/`billing_nif`."""
    billing = woo.get("billing") or {}
    if billing.get("vat"):
        return str(billing["vat"]).strip()
    for meta in woo.get("meta_data") or []:
        key = (meta.get("key") or "").lower().lstrip("_")
        if key in {"billing_nif", "billing_cif", "billing_vat_id", "vat_id"}:
            val = str(meta.get("value") or "").strip()
            if val:
                return val
    return None


#: Campos de dirección que hacen «útil» un bloque (calle/ciudad/CP/provincia).
_ADDR_FIELDS = ("address_1", "city", "postcode", "state")


def _has_address(block: dict[str, Any]) -> bool:
    return any((block.get(k) or "").strip() for k in _ADDR_FIELDS)


def shipping_address_block(woo: dict[str, Any]) -> dict[str, Any] | None:
    """Dirección de ENVÍO del pedido Woo para `packing_json.shipping_address`
    (la que usa el albarán manual y ahora Genei). Usa `shipping` si trae
    dirección; si no (Woo lo manda vacío cuando «enviar a otra dirección» está
    sin marcar), cae a `billing`. Teléfono y email salen del billing (Woo no los
    guarda en shipping); el NIF, de `_extract_cif`. El país va normalizado a
    ISO2 con el helper real. `None` si no hay ni dirección ni nombre.

    Claves compatibles con `order_shipping_address` (`address_line`, `city`,
    `postal_code`, `state`, `country`) + extras para Genei (`name`, `phone`,
    `email`, `nif`)."""
    shipping = woo.get("shipping") or {}
    billing = woo.get("billing") or {}
    src = shipping if _has_address(shipping) else billing

    address_line = " ".join(
        p for p in [(src.get("address_1") or "").strip(), (src.get("address_2") or "").strip()] if p
    ).strip()
    parts = [(src.get("first_name") or "").strip(), (src.get("last_name") or "").strip()]
    name = " ".join(p for p in parts if p).strip() \
        or (shipping.get("company") or billing.get("company") or "").strip()
    iso2 = normalize_country(src.get("country"))

    block = {
        "address_line": address_line,
        "city": (src.get("city") or "").strip(),
        "postal_code": (src.get("postcode") or "").strip(),
        "state": (src.get("state") or "").strip(),
        "country": iso2 or (src.get("country") or "").strip(),
        # Extras para el destino de Genei (el billing manda en contacto).
        "name": name,
        "phone": (billing.get("phone") or "").strip(),
        "email": (billing.get("email") or "").strip(),
        "nif": _extract_cif(woo) or "",
    }
    if not (block["address_line"] or block["city"] or block["postal_code"] or block["name"]):
        return None
    return block


def _apply_shipping_address(order: Order, woo: dict[str, Any], *, overwrite: bool) -> None:
    """Guarda la dirección de envío en `packing_json.shipping_address` sin pisar
    otros bloques del packing (factusol_payment, genei…). En el refresh solo se
    escribe si falta (backfill de pedidos ya importados); en el alta siempre."""
    block = shipping_address_block(woo)
    if block is None:
        return
    try:
        data = json.loads(order.packing_json) if order.packing_json else {}
        if not isinstance(data, dict):
            data = {}
    except (TypeError, ValueError):
        data = {}
    if not overwrite and isinstance(data.get("shipping_address"), dict):
        return
    data["shipping_address"] = block
    order.packing_json = json.dumps(data)


# --- order + líneas ---------------------------------------------------------


def apply_payment_method(order: Order, woo: dict[str, Any]) -> bool:
    """Guarda el método de pago de WooCommerce en el pedido: `payment_method`
    (id del gateway, p. ej. `mollie_wc_gateway_creditcard`) y
    `payment_method_title` (texto de la tienda, p. ej. «Carte»). Woo es la
    fuente: si trae un valor, manda; si no trae nada, no se borra lo que hay.
    Devuelve True si cambió algo (para la puesta al día / backfill)."""
    changed = False
    method = str(woo.get("payment_method") or "").strip()[:64]
    title = str(woo.get("payment_method_title") or "").strip()[:120]
    if method and order.payment_method != method:
        order.payment_method = method
        changed = True
    if title and order.payment_method_title != title:
        order.payment_method_title = title
        changed = True
    return changed


def _payment_status(woo: dict[str, Any]) -> PaymentStatus:
    if woo.get("date_paid"):
        return PaymentStatus.PAID
    return PaymentStatus.PENDING


def _woo_status(woo: dict[str, Any]) -> str | None:
    """Estado crudo de WooCommerce, normalizado a minúsculas. WC lo entrega
    sin el prefijo `wc-` en la REST API. None si el payload no lo trae."""
    raw = str(woo.get("status") or "").strip().lower()
    return raw or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


#: E4-fix1 — claves de meta_data donde los plugins multiidioma de WordPress
#: guardan el idioma del pedido. Se sondean TODAS porque cada tienda puede
#: llevar un plugin distinto (WPML, Polylang) o ninguno; el discovery real
#: por tienda se hace con `scripts/woo_language_discovery.py` sobre los
#: payloads ya almacenados en integration_events.
_LANGUAGE_META_KEYS = (
    "wpml_language", "_wpml_language", "wpml_language_code",
    "pll_language", "_pll_language", "_locale", "locale", "language",
)

def detect_order_language(woo: dict[str, Any]) -> tuple[str | None, str | None]:
    """`(idioma, fuente)` del pedido Woo, o `(None, None)` si no se puede
    deducir (pedido SIN país — la cascada seguirá por el cliente).

    Orden de sondeo: meta_data de WPML/Polylang/locale → campos de primer
    nivel (`customer_locale` y afines) → país de facturación. El discovery
    (E4-fix2) confirmó que las tres tiendas solo traen el país; se resuelve
    con el mapa compartido `language_for_country` — BE→fr, CH→en, resto→en,
    sin dejar vacíos por ambigüedad. Solo un pedido SIN país queda vacío."""
    for md in woo.get("meta_data") or []:
        key = str(md.get("key") or "").strip().lower()
        if key in _LANGUAGE_META_KEYS or key.endswith("wpml_language"):
            lang = normalize_language(md.get("value"))
            if lang:
                return lang, f"meta:{key}"
    for key in ("customer_locale", "locale", "lang", "language"):
        lang = normalize_language(woo.get(key))
        if lang:
            return lang, key
    country = str((woo.get("billing") or {}).get("country") or "").strip().upper()
    lang = language_for_country(country)
    if lang:
        return lang, f"billing.country:{country}"
    return None, None


def _order_number(woo: dict[str, Any]) -> str:
    """Prefijo con el nombre corto de la tienda para evitar colisiones con
    los manuales del CRM (MAN-N) — decisión operativa."""
    slug = woo.get("_store_slug") or "woo"
    number = str(woo.get("number") or woo.get("id"))
    return f"{slug.upper()[:6]}-{number}"


def _create_order(
    session: Session, woo: dict[str, Any], store: IntegrationAccount,
    contact: Contact, company: Company | None,
) -> Order:
    order = Order(
        external_source=OrderSource.WOOCOMMERCE,
        external_id=str(woo.get("id")),
        store_id=store.id,
        order_number=_order_number(woo),
        contact_id=contact.id,
        company_id=company.id if company else None,
        total_amount=float(woo.get("total") or 0),
        currency=(woo.get("currency") or "EUR")[:3],
        payment_status=_payment_status(woo),
        # Estado CRUDO de WooCommerce — base para la regla de seguimiento
        # (cancelado/fallido/reembolsado). Se refresca siempre desde la fuente.
        woo_status=_woo_status(woo),
        preparation_status=PreparationStatus.PENDING_REVIEW,
        placed_at=_parse_dt(woo.get("date_created")) or datetime.now(UTC),
        # E4-fix1: idioma detectado del payload (WPML/locale/país) — se
        # arrastra a los PDF de toda la cadena del documento. None si no se
        # puede deducir (editable en la ficha).
        language=detect_order_language(woo)[0],
    )
    # Dirección de envío de Woo (para el albarán manual y para Genei). Aditivo:
    # los pedidos sin dirección quedan como hasta ahora.
    _apply_shipping_address(order, woo, overwrite=True)
    # Método de pago (gateway + título): sugiere la contrapartida del cobro.
    apply_payment_method(order, woo)
    session.add(order)
    return order


def _refresh_existing(
    session: Session, order: Order, woo: dict[str, Any],
    contact: Contact, company: Company | None, store: IntegrationAccount,
) -> None:
    """UPDATE conservativo. NO pisa preparation_status ni estados
    avanzados (el ERP puede tener el pedido ya aprobado / embalado; solo
    refresca datos económicos y detección de pago."""
    from app.erp.order_cancel import (  # noqa: PLC0415
        WOO_AUTOCANCEL_STATUSES,
        autocancel_web_order,
    )

    _ = store  # firma incluye store para simetría con _create_order
    # Traído a mano (decisión 7): si la web ha cambiado el pedido desde que se
    # trajo (total o líneas, típico entre `on-hold` y `processing`), las líneas
    # NO se reescriben en silencio —el refresco nunca lo ha hecho—: se deja
    # constancia en el pedido para que Bart lo revise. Sin bloquear nada. Se
    # mira ANTES de refrescar el total, que es lo que se compara.
    if (
        order.manual_web_import_at is not None
        and (_woo_status(woo) or "") not in WOO_AUTOCANCEL_STATUSES
    ):
        _anotar_diferencias(session, order, woo)
    order.total_amount = float(woo.get("total") or order.total_amount)
    order.currency = (woo.get("currency") or order.currency)[:3]
    # El estado de WooCommerce SÍ se refresca siempre (a diferencia de los
    # estados propios de BoHub): es la fuente de verdad para cancelado /
    # reembolsado / fallido. Aquí es donde un `order.updated` con `cancelled`
    # (que antes se ignoraba) hace que el pedido salga del seguimiento.
    new_woo_status = _woo_status(woo)
    if new_woo_status is not None:
        order.woo_status = new_woo_status
    # Método de pago: se refresca desde Woo (y rellena los pedidos importados
    # antes de guardarlo).
    apply_payment_method(order, woo)
    # Parte A: un pedido web que pasa a `refunded` (reembolso TOTAL) o
    # `cancelled` en WooCommerce se AUTO-ANULA en BoHub (sale de «por facturar»
    # y demás colas). Idempotente; no toca un pedido ya anulado ni ya facturado.
    # El reembolso PARCIAL deja el estado en `processing`, así que no entra aquí.
    # Backfill de la dirección de envío en pedidos ya importados: solo si falta
    # (no pisa una dirección ya guardada ni editada a mano). Un re-sync o el
    # próximo `order.updated` la puebla en los pedidos abiertos.
    _apply_shipping_address(order, woo, overwrite=False)
    if (new_woo_status or "") in WOO_AUTOCANCEL_STATUSES:
        autocancel_web_order(session, order)
        return   # anulado: no promociona pagos ni cola SAT
    # Detección de pago: si Woo ya tiene date_paid y el ERP seguía pending
    # → promociona a paid (el estado avanzará en la máquina en su momento).
    new_payment = _payment_status(woo)
    if order.payment_status == PaymentStatus.PENDING and new_payment == PaymentStatus.PAID:
        order.payment_status = PaymentStatus.PAID
        # Lote 4: al confirmarse el pago (webhook `order.updated` con date_paid)
        # el pedido entra solo en la Cola SAT si sigue en pre-cola. Idempotente
        # (guard `pending_review`): no toca un pedido ya aprobado/embalado.
        enqueue_paid_order(session, order, actor=None)
    # Si no había contact/company vinculado (rare), engancha ahora.
    if order.contact_id is None:
        order.contact_id = contact.id
    if company is not None and order.company_id is None:
        order.company_id = company.id
    # Y si sigue sin empresa (el billing de Woo no traía NIF), se resuelve por
    # el cliente de FACTUSOL: un `order.updated` posterior recupera el pedido
    # que se importó antes de que FesteWeb escribiera el F_PCL.
    _link_company_from_factusol(session, order, woo)
    # E4-fix1: idioma — solo si aún no se conocía (no pisa una corrección
    # manual de Bart).
    if not order.language:
        order.language = detect_order_language(woo)[0]


def _woo_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _effective_tax_rate(base: Any, tax: Any) -> float:
    """Tipo de IVA EFECTIVO (%) a partir del impuesto REAL cobrado, nunca de un
    literal: `impuesto / base × 100`, redondeado. 0 si la línea no lleva
    impuesto (base o impuesto = 0) — no se inventa ningún 21 %.

    En la REST API de WooCommerce el `total` de una línea, del envío y de un
    fee es SIEMPRE sin impuesto (el impuesto va aparte en `total_tax`), tenga
    la tienda los precios con IVA incluido o no (`prices_include_tax` solo
    cambia cómo se teclean/enseñan, no estos totales de la API). Así que la
    base es `total` y el IVA es `total_tax`: no se suma nada encima. Un tipo
    resultante negativo (datos raros con signos cruzados) se descarta a 0."""
    base = _woo_num(base)
    tax = _woo_num(tax)
    if base == 0 or tax == 0:
        return 0.0
    rate = round(tax / base * 100, 2)
    return rate if rate > 0 else 0.0


def _shipping_line_specs(woo: dict[str, Any]) -> list[dict[str, Any]]:
    """Líneas de ENVÍO (portes) del pedido web con su IVA real. Prefiere
    `shipping_lines[]` (un método por línea, con su título e impuesto); si no
    hay ninguna pero el pedido trae `shipping_total`, sintetiza una sola. Salta
    el envío gratis (0 sin impuesto). Van sin CODART, marcadas `is_shipping=1`
    (para que la emisión las lleve a la banda de portes de la cabecera) y
    `line_kind='shipping'`."""
    out: list[dict[str, Any]] = []
    for j, sh in enumerate(woo.get("shipping_lines") or []):
        total = _woo_num(sh.get("total"))
        tax = _woo_num(sh.get("total_tax"))
        if total == 0 and tax == 0:
            continue
        title = str(sh.get("method_title") or "").strip()
        out.append({
            "product_sku": f"woo-shipping-{sh.get('id') or j}",
            "product_codart": None,
            "description": (f"Envío: {title}" if title else "Envío")[:255],
            "quantity": 1.0, "unit_price": total,
            "tax_rate": _effective_tax_rate(total, tax),
            "line_total": total, "is_shipping": True,
            "line_kind": LINE_KIND_SHIPPING,
        })
    if not out:
        total = _woo_num(woo.get("shipping_total"))
        tax = _woo_num(woo.get("shipping_tax"))
        if total != 0 or tax != 0:
            out.append({
                "product_sku": "woo-shipping", "product_codart": None,
                "description": "Envío", "quantity": 1.0, "unit_price": total,
                "tax_rate": _effective_tax_rate(total, tax),
                "line_total": total, "is_shipping": True,
                "line_kind": LINE_KIND_SHIPPING,
            })
    return out


def _fee_line_specs(woo: dict[str, Any]) -> list[dict[str, Any]]:
    """Líneas de COMISIONES / otros cargos (`fee_lines[]`, p. ej. «PayPal cost
    4%») con su IVA real. Van sin CODART y con `line_kind='fee'` (NO
    `is_shipping`: no son portes). Un fee puede ser negativo (un descuento
    metido como fee): se respeta el signo del importe."""
    out: list[dict[str, Any]] = []
    for k, fee in enumerate(woo.get("fee_lines") or []):
        total = _woo_num(fee.get("total"))
        tax = _woo_num(fee.get("total_tax"))
        if total == 0 and tax == 0:
            continue
        name = str(fee.get("name") or "").strip() or "Cargo"
        out.append({
            "product_sku": f"woo-fee-{fee.get('id') or k}",
            "product_codart": None, "description": name[:255],
            "quantity": 1.0, "unit_price": total,
            "tax_rate": _effective_tax_rate(total, tax),
            "line_total": total, "is_shipping": False,
            "line_kind": LINE_KIND_FEE,
        })
    return out


def _apply_lines(
    session: Session, order: Order, woo: dict[str, Any], store: IntegrationAccount,
) -> None:
    """Crea las líneas del pedido: mercancía (`line_items`), ENVÍO
    (`shipping_lines`/`shipping_total`) y COMISIONES (`fee_lines`), cada una
    con su IVA REAL leído de la fuente (`total_tax`) — nunca un 21 % fijo.

    Si el SKU de una línea de mercancía tiene un mapping CONFIRMADO se rellena
    el CODART; si no, queda NULL y no se hace nada más — el ERP confía en la
    fuente y no crea excepciones sintéticas (B-2-fix5). El envío y los fees no
    son artículos (CODART NULL) y no cuentan como «sin mapear».

    El `total_amount` del pedido lo fija SIEMPRE la cabecera de Woo (lo que
    pagó el cliente, con IVA); estas líneas solo reflejan el DESGLOSE real para
    que el resumen económico sume los componentes en vez de derivar los portes
    como resto."""
    position = 0
    for spec in _line_item_specs(woo):
        codart = _resolve_codart(session, spec["product_sku"], store.id)
        session.add(OrderLine(
            order_id=order.id, position=position, product_codart=codart, **spec,
        ))
        position += 1
    for spec in _shipping_line_specs(woo) + _fee_line_specs(woo):
        session.add(OrderLine(order_id=order.id, position=position, **spec))
        position += 1


def _line_item_specs(woo: dict[str, Any]) -> list[dict[str, Any]]:
    """Líneas de MERCANCÍA (`line_items[]`) con su IVA real. Un artículo sin
    `sku` (pasa: el pack de láminas del 5873) va como `woo-pid-<product_id>`.
    Sin CODART: lo pone `_apply_lines` si hay un mapping confirmado."""
    out: list[dict[str, Any]] = []
    for li in woo.get("line_items") or []:
        sku = str(li.get("sku") or "").strip()
        if not sku:
            sku = f"woo-pid-{li.get('product_id') or li.get('id')}"
        qty = _woo_num(li.get("quantity"))
        line_total = _woo_num(li.get("total"))
        out.append({
            "product_sku": sku,
            "description": (li.get("name") or sku)[:255],
            "quantity": qty,
            "unit_price": round(line_total / qty, 4) if qty else 0.0,
            "tax_rate": _effective_tax_rate(line_total, li.get("total_tax")),
            "line_total": line_total,
        })
    return out


# --- traído a mano: lo que la web cambió después --------------------------------

#: Tope de avisos guardados (la lista crece sin repetir; esto evita que un
#: pedido muy editado en la web la haga enorme).
MAX_DIFERENCIAS = 30


def _eur(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + " €"


def _cantidad(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _etiqueta_linea(sku: str, description: str) -> str:
    """Cómo se nombra una línea en el aviso: la descripción y, si es un
    artículo con referencia de la tienda, su SKU (los portes y comisiones
    llevan un SKU técnico que no dice nada)."""
    desc = (description or "").strip() or sku
    if sku and not sku.startswith("woo-"):
        return f"{desc} ({sku})"
    return desc


def diferencias_con_la_web(
    session: Session, order: Order, woo: dict[str, Any],
) -> list[str]:
    """Lo que el pedido de Woo tiene distinto de lo que BoHub guardó al traerlo:
    el total y las líneas (mercancía, portes y comisiones, por SKU: cantidad e
    importe, y las que sobran o faltan). Textos legibles; [] si coincide."""
    out: list[str] = []
    raw_total = woo.get("total")
    if raw_total not in (None, ""):
        web = round(_woo_num(raw_total), 2)
        bohub = round(float(order.total_amount or 0), 2)
        if abs(web - bohub) > 0.005:
            out.append(f"Total: {_eur(bohub)} en BoHub; la web dice {_eur(web)}.")

    web_lines: dict[str, list[dict[str, Any]]] = {}
    for spec in _line_item_specs(woo) + _shipping_line_specs(woo) + _fee_line_specs(woo):
        web_lines.setdefault(spec["product_sku"], []).append(spec)
    bohub_lines: dict[str, list[OrderLine]] = {}
    for ln in session.scalars(
        select(OrderLine).where(OrderLine.order_id == order.id)
        .order_by(OrderLine.position)
    ):
        bohub_lines.setdefault(ln.product_sku or "", []).append(ln)

    for sku in list(dict.fromkeys([*bohub_lines, *web_lines])):
        en_bohub = bohub_lines.get(sku, [])
        en_web = web_lines.get(sku, [])
        for ln, spec in zip(en_bohub, en_web, strict=False):
            nombre = _etiqueta_linea(sku, spec["description"])
            q_b, q_w = float(ln.quantity or 0), float(spec["quantity"])
            if abs(q_b - q_w) > 1e-6:
                out.append(f"{nombre}: cantidad {_cantidad(q_b)} en BoHub; "
                           f"la web dice {_cantidad(q_w)}.")
            t_b, t_w = round(float(ln.line_total or 0), 2), round(float(spec["line_total"]), 2)
            if abs(t_b - t_w) > 0.005:
                out.append(f"{nombre}: importe {_eur(t_b)} en BoHub; "
                           f"la web dice {_eur(t_w)}.")
        for spec in en_web[len(en_bohub):]:
            out.append(
                f"Línea nueva en la web: {_etiqueta_linea(sku, spec['description'])} "
                f"× {_cantidad(float(spec['quantity']))} = {_eur(float(spec['line_total']))}."
            )
        for ln in en_bohub[len(en_web):]:
            out.append(
                f"Línea que ya no está en la web: {_etiqueta_linea(sku, ln.description)} "
                f"× {_cantidad(float(ln.quantity or 0))}."
            )
    return out


def _anotar_diferencias(session: Session, order: Order, woo: dict[str, Any]) -> None:
    """Guarda en `packing_json["traido_a_mano"]["diferencias"]` lo que la web
    cambió (sin repetir lo ya anotado). No toca líneas ni estados."""
    nuevas = diferencias_con_la_web(session, order, woo)
    if not nuevas:
        return
    try:
        data = json.loads(order.packing_json) if order.packing_json else {}
        if not isinstance(data, dict):
            data = {}
    except (TypeError, ValueError):
        data = {}
    bloque = data.get("traido_a_mano")
    if not isinstance(bloque, dict):
        bloque = {}
    previas = [d for d in (bloque.get("diferencias") or []) if isinstance(d, str)]
    por_anotar = [d for d in nuevas if d not in previas]
    if not por_anotar:
        return
    bloque["diferencias"] = (previas + por_anotar)[-MAX_DIFERENCIAS:]
    data["traido_a_mano"] = bloque
    order.packing_json = json.dumps(data)
    logger.info(
        "woo import: pedido traído a mano %s cambiado en la web: %s",
        order.order_number, " | ".join(por_anotar),
    )


def remap_web_order_lines(
    session: Session, order: Order, woo: dict[str, Any], store: IntegrationAccount,
) -> None:
    """Recalcula el DESGLOSE económico de un pedido web YA importado: borra sus
    líneas y las recrea desde el payload de Woo con la lógica nueva (IVA real +
    envío + comisiones) y refresca `total_amount` con el total de la cabecera.

    Para el recálculo de los pedidos web aún NO facturados tras el fix (los que
    se importaron con el IVA fabricado y sin envío/comisiones). No hace commit:
    lo hace el caller. No toca estados ni colas — solo las líneas y el total."""
    order.lines.clear()
    session.flush()
    order.total_amount = _woo_num(woo.get("total"), float(order.total_amount or 0))
    _apply_lines(session, order, woo, store)
    session.flush()


def _resolve_codart(
    session: Session, woo_sku: str, store_id: str,
) -> str | None:
    """Solo mappings CONFIRMADOS mapean automáticamente. Los propuestos
    (fuzzy sin confirmar) requieren revisión en `/admin/erp/sku-mapping`
    (PR B-5)."""
    row = session.scalar(select(ProductSkuMapping).where(
        ProductSkuMapping.woo_sku == woo_sku,
        ProductSkuMapping.store_id == store_id,
        ProductSkuMapping.confirmed_at.is_not(None),
    ))
    return row.factusol_codart if row else None


def _store_cutoff(store: IntegrationAccount) -> datetime | None:
    if not store.metadata_json:
        return None
    try:
        meta = json.loads(store.metadata_json)
    except (TypeError, ValueError):
        return None
    return _parse_dt(meta.get("external_cutoff_date")) if isinstance(meta, dict) else None


def _auto_mark_external_if_before_cutoff(
    session: Session, order: Order, store: IntegrationAccount,
) -> None:
    cutoff = _store_cutoff(store)
    if cutoff is None or order.placed_at is None:
        return
    placed = order.placed_at
    if placed.tzinfo is None:
        placed = placed.replace(tzinfo=UTC)
    if placed < cutoff:
        from app.erp.external_processing import (  # noqa: PLC0415
            mark_order_externally_processed,
        )

        mark_order_externally_processed(
            session, order=order, actor=None,
            note="Pedido anterior a la fecha de corte de la tienda "
                 "(migrado del proceso anterior).",
        )
        session.flush()


def _unmapped_from(session: Session, order_id: str) -> list[str]:
    # El envío y las comisiones no son artículos: no llevan CODART a propósito
    # y NO cuentan como «sin mapear» (solo la mercancía sin CODART).
    lines = session.scalars(
        select(OrderLine).where(
            OrderLine.order_id == order_id,
            OrderLine.product_codart.is_(None),
            OrderLine.is_shipping.is_(False),
            OrderLine.line_kind.is_(None),
        )
    ).all()
    return [ln.product_sku for ln in lines]
