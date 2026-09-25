"""BoHub ERP — endpoints de la Cola SAT (Fase A PR 5).

  - GET  /api/erp/sat/queue           cola priorizada (in_queue/preparing/blocked)
                                      + filtros (fechas, tienda, estado, texto)
  - GET  /api/erp/sat/history         historial de «enviados al taller»
                                      (emails al SAT + aprobaciones)
  - GET  /api/erp/sat/find-order      nº de pedido → id (para «añadir a mano»)
  - POST /api/erp/orders/{id}/sat-enqueue        añade un pedido a la cola a mano
  - POST /api/erp/orders/{id}/report-exception   crea excepción + bloquea
  - POST /api/erp/orders/{id}/packing-info        peso/dimensiones/bultos
  - POST /api/erp/orders/{id}/attach-document     foto/PDF → DocumentStorage

La cola SAT lee directo de `orders` filtrando por estado de preparación
(decisión cerrada nº10: NO se reusa `tasks`). Reportar excepción cambia
preparation → blocked vía el engine (misma auditoría que cualquier
transición).

Regla del taller (Lote B6): «enviado al taller» = el pedido se mandó por
email al SAT O se aprobó. Por eso el historial une las dos fuentes y por eso
enviar por email aprueba (en `order_email.py`) si seguía pendiente.

Albarán en la cola (Lote 2 A3): cada item dice de DÓNDE sale el albarán que el
taller imprime (`albaran_source`): el de FACTUSOL para pedidos manuales / de
FACTUSOL, el fichero vigente si ya lo hay, y para los pedidos WEB el de
WooCommerce (lo genera la tienda; BoHub nunca lo crea en FACTUSOL —
`WebOrderNoAlbaran`). Un pedido web solo queda «sin albarán» cuando la descarga
de Woo no es posible, y entonces el item trae el motivo.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import (
    require_erp_view,
    require_orders_approve,
    require_sat_no_shipping,
    require_sat_prepare,
    require_sat_shipping,
)
from app.erp.factusol_albaran import is_web_order
from app.erp.models import (
    EXCEPTION_SUBTYPES,
    KIND_ALBARAN,
    KIND_ETIQUETA,
    ErpException,
    ExceptionStatus,
    ExceptionType,
    Order,
    OrderStatusHistory,
    PreparationStatus,
    ShipmentFile,
    StatusDomain,
    TransportStatus,
)
from app.erp.state_machine import TransitionError, apply_transition
from app.erp.storage import get_document_storage
from app.models.crm import AuditLog, User
from app.models.integration_settings import IntegrationAccount

router = APIRouter(prefix="/api/erp", tags=["erp-sat"])

#: Fuente del albarán que el taller imprime/descarga desde la cola (Lote 2 A3).
ALBARAN_FACTUSOL = "factusol"   # PDF del albarán que BoHub creó en FACTUSOL
ALBARAN_FILE = "file"           # fichero vigente (subido a mano o ya bajado de Woo)
ALBARAN_WOO = "woo"             # pedido web: descargar de WooCommerce y abrir

#: Máximo por documento (foto de móvil ~ pocos MB; PDF de etiqueta pequeño).
MAX_DOC_BYTES = 15 * 1024 * 1024

#: Orden de prioridad de la cola SAT (bloqueados arriba para resolverlos ya,
#: luego los que están preparándose, luego los recién aprobados).
_QUEUE_ORDER = {
    PreparationStatus.BLOCKED.value: 0,
    PreparationStatus.PREPARING.value: 1,
    PreparationStatus.IN_QUEUE.value: 2,
}

#: Filtro `estado` de la cola: qué estados de preparación entran en «Por
#: embalar». Los de «Listos» (`ready` / `packed`) se resuelven aparte.
_ESTADO_PREPARING: dict[str, tuple[str, ...]] = {
    "por_embalar": tuple(_QUEUE_ORDER),
    PreparationStatus.BLOCKED.value: (PreparationStatus.BLOCKED.value,),
    PreparationStatus.IN_QUEUE.value: (PreparationStatus.IN_QUEUE.value,),
    PreparationStatus.PREPARING.value: (PreparationStatus.PREPARING.value,),
}
_ESTADO_READY = ("ready", PreparationStatus.PACKED.value)
_ESTADO_PATTERN = "^(por_embalar|blocked|in_queue|preparing|ready|packed)$"

#: Motivo con el que queda en el historial la entrada manual a la cola.
_ENQUEUE_REASON = "añadido a mano a la Cola SAT"


class ReportExceptionIn(BaseModel):
    type: str
    subtype: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PackingInfoIn(BaseModel):
    weight_kg: float | None = Field(default=None, ge=0)
    dimensions_cm: str | None = Field(default=None, max_length=64)
    packages: int | None = Field(default=None, ge=1)


def _get_order(session: Session, order_id: str, user: User | None = None) -> Order:
    order = session.scalar(
        select(Order).where(Order.id == order_id).options(selectinload(Order.lines))
    )
    if order is None:
        raise not_found("Order")
    # Roles y permisos: un pedido WEB es invisible para quien no puede verlos
    # (Comercial) — acceso directo o acción sobre él → 403.
    if user is not None:
        from app.erp.api.orders import order_web_or_403  # noqa: PLC0415

        order_web_or_403(order, user)
    return order


def _packing(order: Order) -> dict[str, Any]:
    if not order.packing_json:
        return {}
    try:
        data = json.loads(order.packing_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _prep(o: Order) -> str:
    return getattr(o.preparation_status, "value", o.preparation_status)


#: Estados de transporte que sacan un pedido de «Listos para envío» (ya salió).
_SHIPPED_TRANSPORT = ("in_transit", "delivered", "already_shipped_externally")

# --- pestañas de la Cola SAT ---------------------------------------------------
#
#   Por embalar ─▶ En preparación ─▶ Embalados ─▶ Pendiente de recogida ─▶ Enviados
#   (en cola /     («Empezar          (packed)     (envío Genei tramitado,   (recogido /
#    bloqueado)     preparación»)                   o etiqueta ya puesta)     en tránsito /
#                                                                             entregado)
#   «Todos pendientes» = las cuatro primeras. «Sin envío» = los que NO se
#   envían (recogida en tienda, licencia, servicio…): la marca
#   `shipping_not_required` («No requiere envío»). NO cuentan como enviados:
#   tienen su propia pestaña y nunca salen en «Enviados». El backend decide la
#   pestaña de cada pedido (`sat_tab_of`) para que los contadores cuadren.

TAB_POR_EMBALAR = "por_embalar"
TAB_EN_PREPARACION = "en_preparacion"
TAB_EMBALADOS = "embalados"
TAB_PENDIENTE_RECOGIDA = "pendiente_recogida"
TAB_SIN_ENVIO = "sin_envio"
TAB_ENVIADOS = "enviados"

#: «Por embalar»: en cola sin empezar, y los bloqueados (arriba, para resolverlos).
_POR_EMBALAR = (PreparationStatus.BLOCKED.value, PreparationStatus.IN_QUEUE.value)


def _genei_summary(order: Order) -> dict[str, Any] | None:
    """El envío Genei del pedido para la card (None si no hay): con él se sabe
    si ofrecer «Crear» o «Ver envío Genei» y si la etiqueta ya está disponible."""
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.integrations.genei.status import is_tramitado  # noqa: PLC0415

    state = genei_state_of(order)
    if not state.get("shipment_code"):
        return None
    return {
        "shipment_code": state.get("shipment_code"),
        "state_code": state.get("state_code"),
        "state_bucket": state.get("state_bucket"),
        "state_label": state.get("state_label"),
        "courier": state.get("courier"),
        "tracking": state.get("tracking"),
        "label_available": is_tramitado(state.get("state_bucket")),
    }


def pendiente_de_recogida(order: Order) -> bool:
    """Embalado y con la etiqueta lista, esperando al transportista: envío Genei
    TRAMITADO (estado 1+) o etiqueta ya puesta (`label_created`, también la
    subida a mano de otra agencia). Aún no ha salido."""
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.integrations.genei.status import READY  # noqa: PLC0415

    transport = getattr(order.transport_status, "value", order.transport_status)
    if transport == TransportStatus.LABEL_CREATED.value:
        return True
    # Tramitado y aún sin recoger (Genei 1). Una incidencia/devolución ya salió
    # y tiene un problema: va a «Incidencias», no a esperar al transportista.
    if transport in (TransportStatus.INCIDENT.value, TransportStatus.RETURNED.value):
        return False
    return genei_state_of(order).get("state_bucket") == READY


def sat_tab_of(order: Order) -> str | None:
    """Pestaña de la Cola SAT del pedido (None si no está en ninguna)."""
    if order.shipping_not_required:
        return TAB_SIN_ENVIO
    prep = _prep(order)
    if prep in _POR_EMBALAR:
        return TAB_POR_EMBALAR
    if prep == PreparationStatus.PREPARING.value:
        return TAB_EN_PREPARACION
    if prep == PreparationStatus.PACKED.value:
        transport = getattr(order.transport_status, "value", order.transport_status)
        if transport in _SHIPPED_TRANSPORT:
            return TAB_ENVIADOS
        return TAB_PENDIENTE_RECOGIDA if pendiente_de_recogida(order) else TAB_EMBALADOS
    return None


def _enviados_clause() -> Any:
    """«Enviados»: embalados que ya salieron (recogido / en tránsito /
    entregado / externalizado). Los «No requiere envío» NO (van a «Sin
    envío»), ni los que nunca pasaron por el taller (histórico importado)."""
    return and_(
        Order.preparation_status == PreparationStatus.PACKED.value,
        Order.transport_status.in_(_SHIPPED_TRANSPORT),
        Order.shipping_not_required.is_(False),
    )


# --- filtros comunes (cola + historial) --------------------------------------


def _apply_filters(
    stmt: Any, *, desde: date | None, hasta: date | None,
    store_slug: str | None, q: str | None,
) -> Any:
    """Filtros compartidos por la cola y el historial: rango de fecha del
    pedido (`placed_at`, inclusivo), tienda por slug (artisjet / boprint /
    fluxlasers…) y texto (nº de pedido o cliente: empresa, contacto, email).
    Mismo criterio de fechas/tienda que la bandeja (`list_orders`)."""
    if desde:
        stmt = stmt.where(Order.placed_at >= datetime.combine(desde, time.min, tzinfo=UTC))
    if hasta:
        stmt = stmt.where(Order.placed_at < datetime.combine(
            hasta + timedelta(days=1), time.min, tzinfo=UTC,
        ))
    if store_slug and store_slug.strip():
        stmt = stmt.where(Order.store_id.in_(
            select(IntegrationAccount.id).where(
                func.lower(IntegrationAccount.account_id) == store_slug.strip().lower()
            )
        ))
    if q and q.strip():
        from app.models.crm import Company, Contact  # noqa: PLC0415

        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Order.order_number.ilike(like),
            Order.company_id.in_(select(Company.id).where(Company.name.ilike(like))),
            Order.contact_id.in_(select(Contact.id).where(or_(
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.email.ilike(like),
            ))),
        ))
    return stmt


def _files_by_order(session: Session, order_ids: list[str]) -> dict[str, dict[str, str]]:
    """`{order_id: {kind: source}}` de los albaranes/etiquetas vigentes (Fase
    D) — una sola query. Con varios vigentes del mismo kind (no debería:
    subir/descargar reemplaza el anterior) se queda el más reciente."""
    files: dict[str, dict[str, str]] = {}
    if order_ids:
        for oid, kind, source in session.execute(
            select(ShipmentFile.order_id, ShipmentFile.kind, ShipmentFile.source).where(
                ShipmentFile.order_id.in_(order_ids),
                ShipmentFile.replaced_at.is_(None),
            ).order_by(ShipmentFile.uploaded_at.desc())
        ):
            files.setdefault(oid, {}).setdefault(kind, source)
    return files


def _stores_by_id(session: Session, orders: list[Order]) -> dict[str, IntegrationAccount]:
    """`{store_id: cuenta}` de las tiendas de los pedidos dados (una query):
    el slug para la vista lista y la configuración de la conexión con Woo
    para saber si el albarán web se puede descargar."""
    ids = {o.store_id for o in orders if o.store_id}
    if not ids:
        return {}
    return {
        acc.id: acc for acc in session.scalars(
            select(IntegrationAccount).where(IntegrationAccount.id.in_(ids))
        )
    }


def _store_slug(stores: dict[str, IntegrationAccount], o: Order) -> str | None:
    acc = stores.get(o.store_id or "")
    return acc.account_id if acc else None


def woo_albaran_state(
    order: Order, account: IntegrationAccount | None,
) -> tuple[bool, str | None]:
    """`(disponible, motivo)`: ¿se puede descargar el albarán de WooCommerce
    de este pedido (`POST /orders/{id}/albaran/fetch-from-woo`)?

    Solo aplica a pedidos WEB: su albarán lo genera la tienda (mu-plugin
    `bohub-albaran`, con el albarán propio de BoHub como respaldo) y BoHub
    nunca lo crea en FACTUSOL (`WebOrderNoAlbaran`). Son las mismas
    condiciones que exige el endpoint de descarga (tienda, id de Woo numérico
    y cuenta con conexión configurada), evaluadas sin salir a la red. Para un
    pedido manual / de FACTUSOL devuelve `(False, None)`: no es un fallo, es
    que el albarán sale de FACTUSOL."""
    if not is_web_order(order):
        return False, None
    if not order.store_id:
        return False, "El pedido no tiene tienda vinculada en BoHub."
    if account is None:
        return False, "La tienda del pedido ya no existe en BoHub."
    try:
        int(str(order.external_id or "").strip())
    except ValueError:
        return False, "Falta el id del pedido en WooCommerce."
    if not (
        account.base_url
        and account.consumer_key_encrypted
        and account.consumer_secret_encrypted
    ):
        return False, (
            f"La tienda «{account.account_id}» no tiene configurada la conexión "
            "con WooCommerce."
        )
    return True, None


def albaran_source_for(
    order: Order, files: dict[str, str], woo_available: bool,
) -> str | None:
    """De dónde sale el albarán que imprime el taller, por prioridad:

    1. `factusol`: el albarán que BoHub creó en FACTUSOL (el documento real
       del pedido, el mismo PDF que la ficha y que el email al SAT);
    2. `file`: fichero vigente en `shipment_files` (subido a mano o ya
       descargado de Woo);
    3. `woo`: pedido web sin fichero aún — se descarga de WooCommerce;
    4. `None`: sin albarán (manual sin crear/subir, o web sin descarga
       posible — entonces `woo_albaran_unavailable_reason` dice por qué)."""
    if order.factusol_albaran_number:
        return ALBARAN_FACTUSOL
    if KIND_ALBARAN in files:
        return ALBARAN_FILE
    if woo_available:
        return ALBARAN_WOO
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _clean(value: str | None) -> str | None:
    """Texto libre de la ficha para la cola: recortado, y None si queda vacío
    (así la card no pinta un bloque de observaciones en blanco)."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def _as_utc(dt: datetime) -> datetime:
    """SQLite devuelve naive; MySQL también puede. Para ordenar y serializar
    todo igual se asume UTC (es lo que se escribe)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- cola ----------------------------------------------------------------------


def sat_items(session: Session, rows: list[Order]) -> list[dict[str, Any]]:
    """Los pedidos como items de la Cola SAT (misma forma en todas las
    pestañas y en el refresco de una sola card)."""
    files_by_order = _files_by_order(session, [o.id for o in rows])
    stores = _stores_by_id(session, rows)

    # D-2: nombre del cliente en las cards del taller (el número solo no basta).
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, rows)

    def _item(o: Order) -> dict[str, Any]:
        who = names.get(o.id) or {}
        files = files_by_order.get(o.id, {})
        woo_ok, woo_reason = woo_albaran_state(o, stores.get(o.store_id or ""))
        source = albaran_source_for(o, files, woo_ok)
        return {
            "id": o.id,
            "order_number": o.order_number,
            "contact_name": who.get("contact_name"),
            "company_name": who.get("company_name"),
            "preparation_status": _prep(o),
            "transport_status": getattr(o.transport_status, "value", o.transport_status),
            "payment_status": getattr(o.payment_status, "value", o.payment_status),
            "total_amount": float(o.total_amount or 0),
            "currency": o.currency,
            "lines": [
                {"sku": line.product_sku, "description": line.description,
                 "quantity": float(line.quantity)}
                for line in o.lines
            ],
            # Lote 2 A3 — albarán de la cola. `albaran_source` dice de dónde
            # sale el PDF (factusol › file › woo, ver `albaran_source_for`) y
            # `has_albaran` es «hay albarán que imprimir o descargar»: True
            # también para un pedido web que aún no lo ha bajado de Woo (lo
            # genera la tienda; BoHub nunca lo crea en FACTUSOL). El fichero
            # vigente queda aparte en `has_albaran_file` / `albaran_file_source`.
            "has_albaran": source is not None,
            "albaran_source": source,
            "has_albaran_file": KIND_ALBARAN in files,
            "albaran_file_source": files.get(KIND_ALBARAN),
            "is_web_order": is_web_order(o),
            "woo_albaran_available": woo_ok,
            "woo_albaran_unavailable_reason": woo_reason,
            "has_etiqueta": KIND_ETIQUETA in files,
            # Albarán que BoHub creó en FACTUSOL (Fase 2). Es la fuente
            # PREFERENTE del PDF en el taller: el mismo documento que el botón
            # de la ficha (#396) y el que adjunta el email al SAT (#407).
            "factusol_albaran_number": o.factusol_albaran_number or None,
            # Lote B6: la vista lista enseña tienda y fecha del pedido.
            "store_slug": _store_slug(stores, o),
            "placed_at": _iso(o.placed_at),
            # Lote 2 · PR-2: lo que el taller necesita leer de pie, a un brazo
            # de distancia — observaciones del comercial (lo primero), nº de
            # serie y licencia WhiteRIP (grandes, en mono, con «copiar») y
            # origen del envío (OFI-TER-SAT). Son los campos de seguimiento
            # del pedido (ERP-F6), que se editan en la ficha; aquí solo se
            # leen. Vacío o solo espacios → None (la card pinta «—»).
            "serial_number": _clean(o.serial_number),
            "whiterip_license": _clean(o.whiterip_license),
            "shipping_origin": _clean(o.shipping_origin),
            # Lote 5 · #3 — nº de seguimiento guardado del pedido, para que la
            # casilla de tracking de «Listos» aparezca precargada si ya existe.
            "tracking_number": _clean(o.tracking_number),
            "notes": _clean(o.notes),
            # Pestaña a la que pertenece (el backend decide; la UI no adivina)
            # y el envío Genei, si lo hay («Crear» vs «Ver envío Genei»,
            # etiqueta disponible o no).
            "sat_tab": sat_tab_of(o),
            # «No requiere envío» (pestaña «Sin envío»).
            "sin_envio": bool(o.shipping_not_required),
            "genei": _genei_summary(o),
        }

    return [_item(o) for o in rows]


def _count(session: Session, stmt: Any) -> int:
    return int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


@router.get("/sat/queue")
def sat_queue(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    estado: str | None = Query(default=None, pattern=_ESTADO_PATTERN),
    q: str | None = Query(default=None, max_length=120),
    # «No requiere envío» (pestaña «Sin envío»): por defecto (False)
    # se EXCLUYEN de las pestañas de pendientes; con True se enseñan SOLO ellos
    # (compatibilidad; la pestaña usa `/sat/shipped?sin_envio=true`).
    no_shipping: bool = Query(default=False),
    # C4: orden por FECHA del pedido. Por defecto los más recientes primero
    # (`fecha_desc`); `fecha_asc` = los más antiguos primero (FIFO de siempre).
    # La prioridad por estado (bloqueado → preparando → en cola) manda igual:
    # la fecha ordena DENTRO de cada grupo.
    sort: str = Query(default="fecha_desc", pattern="^fecha_(desc|asc)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Cola del taller, por pestañas (el backend decide la pestaña de cada
    pedido y los contadores, para que siempre cuadren):

    - `por_embalar`: en cola sin empezar + bloqueados (arriba);
    - `en_preparacion`: preparación empezada, aún sin embalar;
    - `embalados`: embalados sin etiqueta tramitada;
    - `pendiente_recogida`: embalados con envío Genei tramitado (estado 1+) o
      etiqueta ya puesta, esperando al transportista;
    - `counts`: los de esas cuatro, `pendientes` (su suma), y los de
      «Sin envío» y «Enviados» (su lista, en `/sat/shipped`).

    `preparing` / `ready_for_pickup` son las dos secciones de antes (por
    embalar + en preparación; embalados + pendiente de recogida), que siguen
    para quien las lea.

    Filtros (Lote B6), todos opcionales y aplicados a todas las pestañas:
    `desde`/`hasta` (fecha del pedido), `store_slug`, `estado`
    (`por_embalar` = en cola + preparando + bloqueados · `blocked` ·
    `in_queue` · `preparing` · `ready`/`packed` = solo embalados) y `q` (nº
    de pedido o cliente).
    """
    from app.erp.api.orders import worklist_visible  # noqa: PLC0415

    filters = {"desde": desde, "hasta": hasta, "store_slug": store_slug, "q": q}
    # Sin `estado` entran todas las secciones; con él, solo la que toca.
    prep_statuses = _ESTADO_PREPARING.get(estado or "por_embalar", ())
    want_ready = not estado or estado in _ESTADO_READY

    # Control manual (#388 + bandeja): los quitados a mano tampoco entran en el
    # taller (mismo flag que la bandeja y el seguimiento).
    # «No requiere envío» («Sin envío»): FUERA de los pendientes; con
    # `no_shipping=True`, SOLO ellos (compatibilidad).
    ship_flag = Order.shipping_not_required.is_(no_shipping)
    # C4: dirección de la fecha (por defecto, los más recientes primero).
    newest_first = sort != "fecha_asc"

    def _fecha_key(o: Order) -> float:
        d = o.placed_at or o.created_at
        return d.timestamp() if d is not None else 0.0

    prep_rows: list[Order] = []
    if prep_statuses:
        prep_rows = list(session.scalars(
            _apply_filters(worklist_visible(
                select(Order).where(
                    Order.preparation_status.in_(list(prep_statuses)), ship_flag,
                ), current_user
            ), **filters).options(selectinload(Order.lines))
        ))
        # La prioridad por estado manda; la fecha ordena dentro de cada grupo
        # (signo según la dirección elegida).
        signo = -1.0 if newest_first else 1.0
        prep_rows.sort(key=lambda o: (
            _QUEUE_ORDER.get(_prep(o), 9),
            signo * _fecha_key(o),
        ))
    ready_rows: list[Order] = []
    if want_ready:
        orden_fecha = Order.placed_at.desc() if newest_first else Order.placed_at.asc()
        ready_rows = list(session.scalars(
            _apply_filters(worklist_visible(select(Order).where(
                Order.preparation_status == PreparationStatus.PACKED.value,
                Order.transport_status.notin_(_SHIPPED_TRANSPORT),
                ship_flag,
            ), current_user), **filters).options(selectinload(Order.lines))
            .order_by(orden_fecha)
        ))

    prep_items = sat_items(session, prep_rows)
    ready_items = sat_items(session, ready_rows)
    tabs: dict[str, list[dict[str, Any]]] = {
        TAB_POR_EMBALAR: [], TAB_EN_PREPARACION: [],
        TAB_EMBALADOS: [], TAB_PENDIENTE_RECOGIDA: [],
    }
    for item in [*prep_items, *ready_items]:
        destino = item["sat_tab"]
        if no_shipping:
            # Vista de compatibilidad (todos marcados): por su preparación.
            destino = (TAB_POR_EMBALAR if item["preparation_status"] in _POR_EMBALAR
                       else TAB_EN_PREPARACION
                       if item["preparation_status"] == PreparationStatus.PREPARING.value
                       else TAB_EMBALADOS)
        tabs.setdefault(destino, []).append(item)

    def _base(clause: Any) -> Any:
        return _apply_filters(
            worklist_visible(select(Order.id).where(clause), current_user), **filters,
        )

    counts = {key: len(tabs[key]) for key in (
        TAB_POR_EMBALAR, TAB_EN_PREPARACION, TAB_EMBALADOS, TAB_PENDIENTE_RECOGIDA)}
    counts["pendientes"] = sum(counts.values())
    counts[TAB_SIN_ENVIO] = _count(
        session, _base(Order.shipping_not_required.is_(True)))
    counts[TAB_ENVIADOS] = _count(session, _base(_enviados_clause()))

    return {
        TAB_POR_EMBALAR: tabs[TAB_POR_EMBALAR],
        TAB_EN_PREPARACION: tabs[TAB_EN_PREPARACION],
        TAB_EMBALADOS: tabs[TAB_EMBALADOS],
        TAB_PENDIENTE_RECOGIDA: tabs[TAB_PENDIENTE_RECOGIDA],
        "counts": counts,
        # Las dos secciones de antes (compatibilidad).
        "preparing": prep_items,
        "ready_for_pickup": ready_items,
    }


@router.get("/sat/shipped")
def sat_shipped(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=120),
    sin_envio: bool = Query(default=False),
    sort: str = Query(default="fecha_desc", pattern="^fecha_(desc|asc)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """«Enviados» (recogido / en tránsito / entregado) o, con
    `sin_envio=true`, los «No requiere envío» (pestaña «Sin envío», que NO
    son enviados). Con los mismos filtros que la cola. `total` es el recuento
    completo; la lista trae los `limit` primeros por fecha del pedido."""
    from app.erp.api.orders import worklist_visible  # noqa: PLC0415

    clause = (Order.shipping_not_required.is_(True) if sin_envio
              else _enviados_clause())
    stmt = _apply_filters(
        worklist_visible(select(Order).where(clause), current_user),
        desde=desde, hasta=hasta, store_slug=store_slug, q=q,
    )
    total = _count(session, stmt.with_only_columns(Order.id))
    orden = Order.placed_at.desc() if sort != "fecha_asc" else Order.placed_at.asc()
    rows = list(session.scalars(
        stmt.options(selectinload(Order.lines)).order_by(orden).limit(limit)
    ))
    return {"items": sat_items(session, rows), "total": total, "limit": limit}


@router.get("/sat/orders/{order_id}")
def sat_order_item(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Un pedido como item de la Cola SAT: tras avanzarlo de estado, la card se
    refresca EN SU SITIO (sin recargar la cola ni sacar a nadie del pedido)."""
    order = _get_order(session, order_id, current_user)
    return sat_items(session, [order])[0]


# --- «No requiere envío» en lote (pestaña «Sin envío») -------------------------


class BulkNoShippingIn(BaseModel):
    """Marcar/desmarcar «No requiere envío» en lote: `value=True` = el pedido
    NO se envía (recogida en tienda, licencia, servicio…) y pasa a «Sin
    envío»; `value=False` lo devuelve a los pendientes del taller."""

    order_ids: list[str] = Field(min_length=1, max_length=500)
    value: bool = True


@router.post("/sat/bulk-no-shipping")
def bulk_no_shipping(
    payload: BulkNoShippingIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_no_shipping),
) -> dict[str, Any]:
    """Marca (o desmarca) «No requiere envío» en varios pedidos a la vez.

    Marcar = el pedido NO se envía: sale de los pendientes del taller y queda
    en la pestaña «Sin envío» (NUNCA en «Enviados»); en la hoja «Seguimiento
    (app)» su Envío sale «No aplica». (#487 lo había convertido en «enviado
    sin seguimiento»; se revierte a este significado, con la misma marca
    `shipping_not_required`, así que los ya marcados pasan a «Sin envío» sin
    migrar datos.) NO toca pago, factura, cobro ni el «completado».
    Idempotente y reversible; queda en el audit log."""
    from app.core.audit import record_event  # noqa: PLC0415

    changed: list[str] = []
    already = 0
    for order in session.scalars(
        select(Order).where(Order.id.in_(payload.order_ids))
    ):
        if bool(order.shipping_not_required) == payload.value:
            already += 1
            continue
        order.shipping_not_required = payload.value
        changed.append(order.order_number)
    if changed:
        record_event(
            session,
            action="erp.sat_no_shipping" if payload.value else "erp.sat_requires_shipping",
            target_type="order", target_id=None, actor=current_user,
            metadata={"order_ids": payload.order_ids, "value": payload.value,
                      "order_numbers": changed, "meaning": "sin_envio"},
            message=(
                f"«No requiere envío» {'marcado' if payload.value else 'desmarcado'} "
                f"en {len(changed)} pedido(s)"
            ),
        )
    session.commit()
    return {"ok": True, "changed": len(changed), "already": already, "value": payload.value}


# --- historial de enviados al taller -----------------------------------------


@router.get("/sat/history")
def sat_history(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Historial de «enviados al taller», más recientes primero: unión de

    - `email_sat`: envíos del pedido por email (auditoría `erp.order_emailed`:
      destinatarios, asunto, quién y cuándo);
    - `aprobado`: paso de preparación a `in_queue` (aprobación en Cola
      PEDIDOS, aprobación implícita al enviar al SAT, reapertura o entrada
      manual — el `reason` lo distingue).

    Mismos filtros que la cola (fecha del pedido, tienda, texto). No esconde
    anulados/quitados: es un registro de lo que pasó, y enseña el estado
    actual de cada pedido.
    """
    _ = current_user
    filters = {"desde": desde, "hasta": hasta, "store_slug": store_slug, "q": q}

    email_rows = session.execute(
        _apply_filters(
            select(AuditLog, Order).join(Order, Order.id == AuditLog.target_id).where(
                AuditLog.action == "erp.order_emailed",
                AuditLog.target_type == "order",
            ), **filters,
        ).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    approval_rows = session.execute(
        _apply_filters(
            select(OrderStatusHistory, Order).join(
                Order, Order.id == OrderStatusHistory.order_id,
            ).where(
                OrderStatusHistory.domain == StatusDomain.PREPARATION,
                OrderStatusHistory.to_status == PreparationStatus.IN_QUEUE.value,
            ), **filters,
        ).order_by(OrderStatusHistory.changed_at.desc()).limit(limit)
    ).all()

    orders: dict[str, Order] = {}
    for _row, o in (*email_rows, *approval_rows):
        orders[o.id] = o
    order_list = list(orders.values())

    from app.erp.api.orders import customer_names  # noqa: PLC0415

    names = customer_names(session, order_list)
    files_by_order = _files_by_order(session, list(orders))
    stores = _stores_by_id(session, order_list)
    user_ids = {
        uid for uid in (
            *(a.actor_user_id for a, _o in email_rows),
            *(h.changed_by_user_id for h, _o in approval_rows),
        ) if uid
    }
    user_names: dict[str, str] = {}
    if user_ids:
        user_names = {
            u.id: u.full_name for u in session.scalars(
                select(User).where(User.id.in_(user_ids))
            )
        }

    def _base(o: Order, at: datetime) -> dict[str, Any]:
        who = names.get(o.id) or {}
        return {
            "order_id": o.id,
            "order_number": o.order_number,
            "contact_name": who.get("contact_name"),
            "company_name": who.get("company_name"),
            "at": _as_utc(at).isoformat(),
            "preparation_status": _prep(o),
            "transport_status": getattr(o.transport_status, "value", o.transport_status),
            "factusol_albaran_number": o.factusol_albaran_number or None,
            # En el historial `has_albaran` sigue siendo «hay fichero subido /
            # descargado» (columna «Subido»): es un registro, no una acción.
            "has_albaran": KIND_ALBARAN in files_by_order.get(o.id, {}),
            "store_slug": _store_slug(stores, o),
            "placed_at": _iso(o.placed_at),
            "cancelled": o.cancelled_at is not None,
            "excluded": o.seguimiento_excluded_at is not None,
        }

    items: list[dict[str, Any]] = []
    for a, o in email_rows:
        try:
            meta = json.loads(a.metadata_json) if a.metadata_json else {}
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        items.append({
            **_base(o, a.created_at),
            "kind": "email_sat",
            "actor_user_id": a.actor_user_id,
            "actor_name": user_names.get(a.actor_user_id or "") or a.actor_email,
            "to": [str(x) for x in (meta.get("to") or [])],
            "cc": [str(x) for x in (meta.get("cc") or [])],
            "subject": meta.get("subject"),
            "attachment_kinds": list(meta.get("attachment_kinds") or []),
            "reason": None,
            "from_status": None,
        })
    for h, o in approval_rows:
        items.append({
            **_base(o, h.changed_at),
            "kind": "aprobado",
            "actor_user_id": h.changed_by_user_id,
            "actor_name": user_names.get(h.changed_by_user_id or ""),
            "to": [],
            "cc": [],
            "subject": None,
            "attachment_kinds": [],
            "reason": h.reason,
            "from_status": h.from_status,
        })
    # Los pedidos con INCIDENCIA (de envío o de pedido) NO salen en «Enviados»:
    # se ven en la pestaña «Incidencias». Cuando se resuelven, vuelven aquí.
    incidencia_ids = _incidencia_order_ids(session)
    items = [it for it in items if it["order_id"] not in incidencia_ids]
    items.sort(key=lambda it: it["at"], reverse=True)
    return {"items": items[:limit], "limit": limit}


# --- incidencias (Cola SAT): de ENVÍO (webhook) o de PEDIDO (excepción) -------


def _incidencia_order_ids(session: Session) -> set[str]:
    """Ids de pedidos con incidencia: de ENVÍO (`transport_status = incident`,
    lo pone el webhook de Genei / «Actualizar estado») o de PEDIDO (una
    `ErpException` abierta: taller, falta de stock, VIES…)."""
    envio = set(session.scalars(
        select(Order.id).where(Order.transport_status == TransportStatus.INCIDENT)
    ))
    pedido = set(session.scalars(
        select(ErpException.order_id).where(
            ErpException.status.in_([ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS]),
        )
    ))
    return envio | pedido


@router.get("/sat/incidencias")
def sat_incidencias(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    store_slug: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=120),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Pedidos con INCIDENCIA para la Cola SAT (los que salen de «Enviados»):

    - **envío**: `transport_status = incident` (incidencia de TRANSPORTE, del
      webhook de Genei o de «Actualizar estado»); el motivo es la descripción
      de la incidencia de transporte.
    - **pedido**: una `ErpException` abierta (taller, falta de stock, VIES…).

    Distingue el `tipo` para que el operario sepa qué resolver. «Resolver» de
    envío devuelve a «en tránsito»; el de pedido cierra la excepción (endpoint
    de excepciones)."""
    _ = current_user
    from app.erp.api.orders import customer_names  # noqa: PLC0415
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.seguimiento import EXCEPTION_TYPE_LABELS, _exception_motivo  # noqa: PLC0415

    filters = {"desde": desde, "hasta": hasta, "store_slug": store_slug, "q": q}
    envio_orders = list(session.scalars(_apply_filters(
        select(Order).where(Order.transport_status == TransportStatus.INCIDENT), **filters,
    )))
    excs = list(session.scalars(
        select(ErpException).where(
            ErpException.status.in_([ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS]),
        ).order_by(ErpException.created_at.desc())
    ))
    exc_by_order: dict[str, ErpException] = {}
    for e in excs:
        exc_by_order.setdefault(e.order_id, e)      # la más reciente por pedido
    pedido_orders = list(session.scalars(_apply_filters(
        select(Order).where(Order.id.in_(set(exc_by_order))), **filters,
    ))) if exc_by_order else []

    todos = list({o.id: o for o in (*envio_orders, *pedido_orders)}.values())
    names = customer_names(session, todos)
    stores = _stores_by_id(session, todos)

    def _row(o: Order, tipo: str, motivo: str, *, exc: ErpException | None) -> dict[str, Any]:
        who = names.get(o.id) or {}
        tipo_v = getattr(exc.type, "value", exc.type) if exc else None
        return {
            "order_id": o.id, "order_number": o.order_number,
            "contact_name": who.get("contact_name"), "company_name": who.get("company_name"),
            "tipo": tipo,                          # "envio" | "pedido"
            "motivo": motivo,
            # Solo en las de PEDIDO: id + etiqueta de la excepción (para resolver).
            "exception_id": exc.id if exc else None,
            "exception_type": (
                EXCEPTION_TYPE_LABELS.get(str(tipo_v or ""), str(tipo_v or "")) or None
            ),
            "transport_status": getattr(o.transport_status, "value", o.transport_status),
            "tracking_number": _clean(o.tracking_number),
            "store_slug": _store_slug(stores, o),
            "placed_at": _iso(o.placed_at),
        }

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for o in envio_orders:                          # envío primero (transporte urgente)
        motivo = str(genei_state_of(o).get("desc_incidencia") or "Incidencia de transporte")
        items.append(_row(o, "envio", motivo, exc=None))
        seen.add(o.id)
    for o in pedido_orders:
        if o.id in seen:
            continue                                # ya está como incidencia de envío
        e = exc_by_order.get(o.id)
        tipo_v = getattr(e.type, "value", e.type) if e else ""
        motivo = ((_exception_motivo(e) if e else "")
                  or EXCEPTION_TYPE_LABELS.get(str(tipo_v), "Incidencia de pedido"))
        items.append(_row(o, "pedido", str(motivo), exc=e))
    items.sort(key=lambda it: it["placed_at"] or "", reverse=True)
    return {"items": items}


@router.post("/sat/orders/{order_id}/shipping-incidencia/resolve")
def sat_resolve_shipping_incidencia(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Marca resuelta una incidencia de ENVÍO: el transporte vuelve a «en
    tránsito» y el pedido sale de «Incidencias» → «Enviados». El SAT puede
    hacerlo desde la Cola SAT (el arco `incident → in_transit` se abrió a SAT)."""
    order = session.get(Order, order_id)
    if order is None:
        raise not_found("Pedido")
    current = getattr(order.transport_status, "value", order.transport_status)
    if current != TransportStatus.INCIDENT.value:
        raise HTTPException(409, {
            "code": "not_incident", "detail": "El pedido no está en incidencia de envío.",
        })
    try:
        apply_transition(
            session, order=order, domain=StatusDomain.TRANSPORT,
            to_status=TransportStatus.IN_TRANSIT.value, actor=current_user,
            reason="Incidencia de envío resuelta (Cola SAT)",
        )
    except TransitionError as exc:
        raise HTTPException(409, {"code": "invalid_transition", "detail": str(exc)}) from exc
    session.commit()
    return {"order_id": order.id,
            "transport_status": getattr(order.transport_status, "value", order.transport_status)}


# --- añadir a mano ------------------------------------------------------------


@router.get("/sat/find-order")
def sat_find_order(
    number: str = Query(min_length=1, max_length=64),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Resuelve un nº de pedido (exacto, sin distinguir mayúsculas) a su id,
    para «Añadir pedido a la cola». Devuelve también en qué situación está
    (ya en cola / anulado / quitado) para que la UI avise antes de intentar."""
    _ = current_user
    wanted = number.strip().lower()
    order = session.scalar(
        select(Order).where(func.lower(Order.order_number) == wanted)
        .order_by(Order.created_at.desc()).limit(1)
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "order_not_found",
            "detail": f"No hay ningún pedido con el número «{number.strip()}».",
        })
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    who = customer_names(session, [order]).get(order.id) or {}
    return {
        "id": order.id,
        "order_number": order.order_number,
        "contact_name": who.get("contact_name"),
        "company_name": who.get("company_name"),
        "preparation_status": _prep(order),
        "transport_status": getattr(order.transport_status, "value", order.transport_status),
        "already_queued": _prep(order) in _QUEUE_ORDER,
        "cancelled": order.cancelled_at is not None,
        "excluded": order.seguimiento_excluded_at is not None,
    }


def _force_in_queue(session: Session, order: Order, actor: User, reason: str) -> None:
    """Sin arco en la máquina de estados (p.ej. externalizado) o arco reservado
    a otro rol: se fuerza `in_queue` dejando la MISMA huella que una transición
    normal (fila de historial + auditoría), para que el timeline y el
    historial del taller lo cuenten igual."""
    from app.core.audit import record_event  # noqa: PLC0415

    current = _prep(order)
    session.add(OrderStatusHistory(
        order_id=order.id, domain=StatusDomain.PREPARATION,
        from_status=current, to_status=PreparationStatus.IN_QUEUE.value,
        changed_at=datetime.now(UTC), changed_by_user_id=actor.id,
        reason=reason, metadata_json=json.dumps({"reason": reason, "forced": True}),
    ))
    order.preparation_status = PreparationStatus.IN_QUEUE.value
    session.flush()
    record_event(
        session, action="erp.order_status_changed", target_type="order",
        target_id=order.id, actor=actor,
        metadata={
            "domain": StatusDomain.PREPARATION.value, "from": current,
            "to": PreparationStatus.IN_QUEUE.value,
            "label": "Añadir a mano a la Cola SAT", "reason": reason,
            "order_number": order.order_number, "forced": True,
        },
    )


@router.post("/orders/{order_id}/sat-enqueue")
def sat_enqueue(
    order_id: str,
    session: Session = Depends(get_session),
    # «Añadir a mano a la Cola SAT» es una acción de gestión de pedido
    # (aprobar/meter en cola), no de taller: la hace la oficina (admin/pedidos/
    # comercial). El SAT trabaja lo que ya está en la cola, no lo añade.
    current_user: User = Depends(require_orders_approve),
) -> dict[str, Any]:
    """Añade un pedido a la Cola SAT a mano (Lote B6).

    - pendiente de revisión → misma lógica que aprobar (bloqueos → 409,
      transición a in_queue, approved_at/by);
    - embalado / externalizado / otros → in_queue por la máquina de estados
      si hay arco (packed → in_queue «Reabrir»), y si no, forzado con fila de
      historial + auditoría;
    - ya en cola (in_queue / preparing / blocked) → idempotente;
    - anulado → 409 `cancelled`; quitado de las listas → 409 `excluded`.
    """
    from app.erp.api.orders import _blockers, approve_inline  # noqa: PLC0415

    order = _get_order(session, order_id, current_user)
    if order.cancelled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "cancelled",
            "detail": (
                f"El pedido {order.order_number} está anulado: "
                "no se puede añadir a la Cola SAT."
            ),
        })
    if order.seguimiento_excluded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "excluded",
            "detail": (
                f"El pedido {order.order_number} está quitado de las listas de trabajo: "
                "reinclúyelo desde la bandeja antes de añadirlo a la Cola SAT."
            ),
        })

    current = _prep(order)
    result: dict[str, Any] = {
        "order_id": order.id, "order_number": order.order_number,
        "already_queued": False, "approved": False, "via": None,
    }
    if current in _QUEUE_ORDER:
        return {**result, "preparation_status": current, "already_queued": True}

    if current == PreparationStatus.PENDING_REVIEW.value:
        blockers = _blockers(session, order)
        if blockers:
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "code": "blocked", "blockers": blockers,
                "detail": (
                    f"El pedido {order.order_number} tiene excepciones sin resolver: "
                    "resuélvelas antes de añadirlo a la Cola SAT."
                ),
            })
        try:
            approve_inline(session, order, current_user, reason=_ENQUEUE_REASON)
        except TransitionError as exc:
            raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
        result.update(approved=True, via="approve")
    else:
        try:
            apply_transition(
                session, order=order, domain=StatusDomain.PREPARATION,
                to_status=PreparationStatus.IN_QUEUE.value, actor=current_user,
                reason=_ENQUEUE_REASON, evidence={"reason": _ENQUEUE_REASON},
            )
            result["via"] = "transition"
        except TransitionError as exc:
            if exc.code not in ("invalid_transition", "role_forbidden"):
                raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
            _force_in_queue(session, order, current_user, _ENQUEUE_REASON)
            result["via"] = "direct"
    session.commit()
    return {**result, "preparation_status": _prep(order)}


# --- reportar excepción ------------------------------------------------------


@router.post("/orders/{order_id}/report-exception", status_code=201)
def report_exception(
    order_id: str,
    payload: ReportExceptionIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_prepare),
) -> dict[str, Any]:
    """SAT reporta un problema: crea la excepción y bloquea la preparación.
    VER es suficiente para reportar (SAT puede) — el bloqueo lo aplica el
    engine, que permite blocked a admin/pedidos/sat."""
    order = _get_order(session, order_id, current_user)
    try:
        etype = ExceptionType(payload.type)
    except ValueError as exc:
        raise HTTPException(400, f"type de excepción inválido: {payload.type!r}") from exc
    valid_subtypes = EXCEPTION_SUBTYPES.get(etype, set())
    if payload.subtype and valid_subtypes and payload.subtype not in valid_subtypes:
        raise HTTPException(
            400, f"subtype inválido para {etype.value}: {payload.subtype!r}"
        )

    metadata = dict(payload.metadata)
    if payload.description:
        metadata["description"] = payload.description

    exc_row = ErpException(
        type=etype, subtype=payload.subtype,
        metadata_json=json.dumps(metadata, default=str) if metadata else None,
        order_id=order.id, reported_by_user_id=current_user.id,
    )
    session.add(exc_row)

    # Bloquea la preparación si el pedido está en un estado bloqueable
    # (in_queue/preparing). Si ya está packed/blocked, se registra la
    # excepción sin forzar transición inválida.
    current = getattr(order.preparation_status, "value", order.preparation_status)
    if current in (PreparationStatus.IN_QUEUE.value, PreparationStatus.PREPARING.value):
        try:
            apply_transition(
                session, order=order, domain=StatusDomain.PREPARATION,
                to_status=PreparationStatus.BLOCKED.value, actor=current_user,
                reason=payload.description or f"Excepción: {etype.value}",
                evidence={"reason": payload.description or etype.value},
            )
        except TransitionError as exc:
            raise HTTPException(
                409, {"code": exc.code, "detail": exc.detail}
            ) from exc
    session.commit()
    session.refresh(exc_row)
    return {
        "id": exc_row.id,
        "type": etype.value,
        "subtype": exc_row.subtype,
        "order_id": order.id,
        "preparation_status": getattr(order.preparation_status, "value", order.preparation_status),
    }


@router.post("/orders/{order_id}/packing-info")
def packing_info(
    order_id: str,
    payload: PackingInfoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """SAT introduce peso/dimensiones/bultos. Se guarda en packing_json
    (junto a los documentos adjuntos)."""
    _ = current_user
    order = _get_order(session, order_id, current_user)
    packing = _packing(order)
    if payload.weight_kg is not None:
        packing["weight_kg"] = payload.weight_kg
    if payload.dimensions_cm is not None:
        packing["dimensions_cm"] = payload.dimensions_cm
    if payload.packages is not None:
        packing["packages"] = payload.packages
    order.packing_json = json.dumps(packing, default=str)
    session.commit()
    return {"order_id": order.id, "packing": packing}


@router.post("/orders/{order_id}/attach-document", status_code=201)
async def attach_document(
    order_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_sat_shipping),
) -> dict[str, Any]:
    """Sube una foto/PDF a HiDrive (o disco local si no hay creds) vía la
    interfaz DocumentStorage y guarda su referencia en packing_json."""
    _ = current_user
    order = _get_order(session, order_id, current_user)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Archivo vacío.")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El documento supera el máximo de 15 MB.",
        )
    stored = get_document_storage().save(
        order_id=order.id, filename=file.filename or "documento",
        content_type=file.content_type, data=data,
    )
    packing = _packing(order)
    docs = packing.get("documents")
    if not isinstance(docs, list):
        docs = []
    doc = {**stored.as_dict(), "uploaded_by_user_id": current_user.id}
    docs.append(doc)
    packing["documents"] = docs
    order.packing_json = json.dumps(packing, default=str)
    session.commit()
    return {"order_id": order.id, "document": doc}
