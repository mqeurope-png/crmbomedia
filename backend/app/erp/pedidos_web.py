"""ERP · Traer a mano un pedido web que aún no está pagado (`on-hold`).

Caso (Bart, 28/10): los pedidos de WooCommerce `on-hold` (transferencia
bancaria aún sin cobrar) no entran solos en BoHub —el importador solo crea
`processing` / `completed` / `refunded`—, pero son clientes reales con empresa
que Bart quiere preparar y facturar ya. Desde ERP · Pedidos se listan los
`on-hold` de las tiendas, leídos en vivo, y se trae uno a uno:

- **Mismo pedido que el importador.** Traer = `import_woo_order` (el MISMO
  camino del webhook) con la autorización `TraidoAMano`: mismas líneas,
  cliente, direcciones, portes, impuestos, totales y nº (`FLUXLA-5873`). Solo
  se sella quién y cuándo (`Order.manual_web_import_*`). Cuando la tienda lo
  cobre, el webhook de siempre lo ACTUALIZA (pago, Cola SAT) en vez de crear
  otro; si se cancela en la web, el mismo webhook lo anula en BoHub.
- **Sin duplicados.** El importador busca/crea bajo un cerrojo por tienda;
  traer dos veces devuelve el que hay (`creado: false`); y si ya hay en BoHub
  otro pedido con el mismo nº o uno creado «desde FACTUSOL» con su REFPCL, no
  se trae (409 `ya_hay_pedido_en_bohub`: Bart decide).
- **FACTUSOL, solo lectura.** Los pedidos web llegan solos a FACTUSOL como
  pedido de cliente (lo crea la app de la web, no BoHub). Al traer se busca y
  se dice si se reconoce (`enlazado`), si hay uno parecido (`posible`), si no
  está aún (`no_encontrado`) o si no se puede asegurar (`dudoso`). NUNCA se
  escribe nada en FACTUSOL desde aquí; el enlace sigue siendo el de siempre,
  en vivo por la referencia común (REFPCL).
- **Estados.** `on-hold` se trae con autorización; `processing`/`completed`
  que no estén en BoHub entran por el camino normal (ya están pagados);
  `cancelled` → «cancelado en la web»; `pending`/`failed` no son pedidos
  (carritos); el resto, no se trae.

Las tiendas se consultan en paralelo y con tiempo acotado: una que no responde
sale marcada en `tiendas` sin tumbar a las demás.
"""
from __future__ import annotations

import json
import logging
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderSource
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.mapper import (
    ImportacionSimultanea,
    TraidoAMano,
    _extract_cif,
    _order_number,
    import_woo_order,
)
from app.integrations.woocommerce.missing import enlace_tienda, woo_stores
from app.models.crm import ExternalSystem, User
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Estado de WooCommerce que se trae a mano (transferencia aún sin cobrar).
ESTADO_EN_ESPERA = "on-hold"
#: Estados que, si el pedido aún no está en BoHub, se importan por el camino
#: normal al traerlo (ya están pagados: no necesitan autorización).
ESTADOS_PAGADOS = frozenset({"processing", "completed"})
#: Carritos, no pedidos: ni se listan ni se traen.
ESTADOS_NO_PEDIDO = frozenset({"pending", "failed"})

#: Tiempo por petición a una tienda (sin reintentos) y tope de la pantalla:
#: el listado espera como mucho `TIEMPO_LISTADO`, y la consulta de un número
#: lo que quede hasta `TIEMPO_TOTAL` (con un máximo de `TIEMPO_CONSULTA`).
TIEMPO_POR_PETICION = 10.0
TIEMPO_LISTADO = 20.0
TIEMPO_CONSULTA = 5.0
TIEMPO_TOTAL = 25.0
#: Al traer, el pedido se relee en vivo: un poco más de margen y un reintento.
TIEMPO_TRAER = 20.0

_POR_PAGINA = 100
#: Hasta 500 pedidos en espera por tienda (en la práctica son unos pocos).
_MAX_PAGINAS = 5
#: Campos del listado (`_fields`): lo que enseña la lista, nada más.
CAMPOS_LISTADO = ",".join((
    "id", "number", "status", "total", "currency", "date_created", "date_created_gmt",
    "billing", "payment_method", "payment_method_title",
))
#: Tolerancia al comparar importes con FACTUSOL (céntimo de redondeo).
TOLERANCIA_IMPORTE = 0.01

#: Acción de auditoría al traer un pedido web.
TRAIDO_EVENT = "erp.pedido_web_traido"


def es_traido_a_mano(order: Any) -> bool:
    """¿Este pedido lo trajo alguien a mano desde ERP · Pedidos (un `on-hold`
    autorizado)? Es la marca que hace pasar las puertas de pago/visibilidad."""
    return getattr(order, "manual_web_import_at", None) is not None


class PedidoWebError(Exception):
    """Error con su código para la API: `{"code", "detail", "order_id"?}`."""

    def __init__(self, status: int, code: str, detail: str, *, order_id: str | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.order_id = order_id

    def as_detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.order_id:
            out["order_id"] = self.order_id
        return out


# --- clientes de las tiendas --------------------------------------------------------


def _cliente_listado(store: IntegrationAccount) -> Any:
    """Cliente para la lista: poco tiempo y sin reintentos (hay alguien
    mirando la pantalla)."""
    return WooHTTPClient(store, timeout=TIEMPO_POR_PETICION, max_retries=0)


def _cliente_traer(store: IntegrationAccount) -> Any:
    return WooHTTPClient(store, timeout=TIEMPO_TRAER, max_retries=1)


# --- utilidades -------------------------------------------------------------------


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sin_tildes(texto: Any) -> str:
    """Minúsculas y sin tildes, para buscar «Lopez» y encontrar «López»."""
    norm = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).casefold().strip()


def _fecha(wo: dict[str, Any]) -> str | None:
    """Fecha de creación en ISO con zona (UTC). `date_created_gmt` es UTC sin
    zona; si solo viene `date_created` se trata como UTC (como el mapper)."""
    for clave in ("date_created_gmt", "date_created"):
        raw = wo.get(clave)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return None


def _estado(wo: dict[str, Any]) -> str:
    return str(wo.get("status") or "").strip().lower()


def _numero_bohub(store: IntegrationAccount | _Tienda, wo: dict[str, Any]) -> str:
    """El nº que le pone (o le pondría) el importador: `FLUXLA-5873`."""
    return _order_number({**wo, "_store_slug": store.account_id})


def _packing(order: Order) -> dict[str, Any]:
    try:
        data = json.loads(order.packing_json) if order.packing_json else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class _Tienda:
    """Foto de la tienda para los hilos (nada del ORM cruza a otro hilo)."""

    store_id: str
    account_id: str
    nombre: str
    base_url: str
    client: Any = None
    error: str | None = None


def _item(t: _Tienda, wo: dict[str, Any]) -> dict[str, Any]:
    """Una fila de la lista (forma del contrato de la API)."""
    b = wo.get("billing") or {}
    woo_id = int(wo["id"])
    nombre = " ".join(
        p for p in (str(b.get("first_name") or "").strip(), str(b.get("last_name") or "").strip())
        if p
    )
    return {
        "store_id": t.store_id,
        "tienda": t.account_id,
        "tienda_nombre": t.nombre,
        "woo_id": woo_id,
        "numero": str(wo.get("number") or woo_id),
        "fecha": _fecha(wo),
        "cliente": nombre or None,
        "empresa": str(b.get("company") or "").strip() or None,
        "email": str(b.get("email") or "").strip() or None,
        "total": round(_num(wo.get("total")), 2),
        "moneda": str(wo.get("currency") or "EUR"),
        "forma_pago": (str(wo.get("payment_method_title") or "").strip()
                       or str(wo.get("payment_method") or "").strip() or None),
        "enlace_tienda": enlace_tienda(t, woo_id),
        "en_bohub": None,
    }


def _coincide(item: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    return any(
        q in _sin_tildes(item.get(campo))
        for campo in ("numero", "cliente", "empresa", "email")
    )


def _en_bohub(
    session: Session, t: _Tienda, pedidos: list[dict[str, Any]],
) -> dict[int, dict[str, str]]:
    """`{woo_id: {order_id, order_number}}` de los pedidos que BoHub ya tiene:
    por el id de la tienda (los de esta tienda o, por prudencia, los que no
    guardan tienda) o por el mismo nº de pedido de cualquier origen (como
    `missing._ya_en_bohub`)."""
    out: dict[int, dict[str, str]] = {}
    ids = [str(wo["id"]) for wo in pedidos]
    numeros = {_numero_bohub(t, wo): int(wo["id"]) for wo in pedidos}
    for i in range(0, len(ids), 500):
        for oid, num, ext in session.execute(select(
            Order.id, Order.order_number, Order.external_id,
        ).where(
            Order.external_source == OrderSource.WOOCOMMERCE,
            Order.external_id.in_(ids[i:i + 500]),
            or_(Order.store_id == t.store_id, Order.store_id.is_(None)),
        )):
            out.setdefault(int(ext), {"order_id": oid, "order_number": num})
    claves = list(numeros)
    for i in range(0, len(claves), 500):
        for oid, num in session.execute(select(Order.id, Order.order_number).where(
            Order.order_number.in_(claves[i:i + 500]),
        )):
            out.setdefault(numeros[num], {"order_id": oid, "order_number": num})
    return out


def motivo_estado(estado: str, en_bohub: bool) -> tuple[str, bool]:
    """(motivo legible, ¿se puede traer?) de un pedido de la tienda por su
    estado de WooCommerce."""
    if en_bohub:
        return "ya está en BoHub", False
    if estado == ESTADO_EN_ESPERA:
        return "en espera de pago: se puede traer", True
    if estado == "processing":
        return "procesando: entra solo (si no ha llegado, se puede traer)", True
    if estado == "completed":
        return "completado: entra solo (si no ha llegado, se puede traer)", True
    if estado == "cancelled":
        return "cancelado en la web: no se puede traer", False
    if estado == "pending":
        return "pendiente de pago (carrito sin pagar): no se trae", False
    if estado == "failed":
        return "pago fallido en la web: no se trae", False
    if estado == "refunded":
        return "reembolsado en la web: no se trae", False
    if estado == "trash":
        return "en la papelera de la tienda: no se trae", False
    return f"estado «{estado or '?'}» en la web: no se trae", False


# --- lista de los pedidos en espera ---------------------------------------------------


def _listar_en_espera(client: Any, limite: float) -> list[dict[str, Any]]:
    """Los `on-hold` de UNA tienda (en un hilo: solo red, nada de BD)."""
    pedidos: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGINAS + 1):
        if page > 1 and time.monotonic() > limite:
            break
        lote = client.list_orders(
            status=ESTADO_EN_ESPERA, per_page=_POR_PAGINA, page=page, fields=CAMPOS_LISTADO,
        )
        if not lote:
            break
        pedidos.extend(
            wo for wo in lote
            if isinstance(wo, dict) and wo.get("id") and _estado(wo) == ESTADO_EN_ESPERA
        )
        if len(lote) < _POR_PAGINA:
            break
    return pedidos


def _buscar_numero(client: Any, numero: int) -> dict[str, Any] | None:
    """El pedido con ese id en UNA tienda, o None si no existe (en un hilo)."""
    try:
        wo = client.get_order(numero)
    except WooError as exc:
        if exc.status == 404:
            return None
        raise
    return wo if isinstance(wo, dict) and wo.get("id") else None


def _en_paralelo(
    trabajos: dict[str, Callable[[], Any]], plazo: float,
) -> dict[str, tuple[bool, Any]]:
    """Ejecuta cada trabajo en su hilo y espera como mucho `plazo` segundos.
    `{clave: (ok, resultado | mensaje de error)}`; lo que no termina a tiempo
    sale como error (su hilo acaba solo, con el tiempo por petición)."""
    if not trabajos:
        return {}
    pool = ThreadPoolExecutor(max_workers=len(trabajos), thread_name_prefix="pedidos-web")
    futuros: dict[str, Future] = {k: pool.submit(fn) for k, fn in trabajos.items()}
    wait(list(futuros.values()), timeout=max(plazo, 0.0))
    pool.shutdown(wait=False, cancel_futures=True)
    out: dict[str, tuple[bool, Any]] = {}
    for clave, fut in futuros.items():
        if not fut.done():
            out[clave] = (False, f"la tienda no ha respondido a tiempo ({int(plazo)} s)")
            continue
        exc = fut.exception()
        if exc is not None:
            texto = str(exc) if isinstance(exc, WooError) else f"{type(exc).__name__}: {exc}"
            out[clave] = (False, texto[:200])
        else:
            out[clave] = (True, fut.result())
    return out


def pedidos_en_espera(
    session: Session,
    *,
    q: str | None = None,
    client_factory: Callable[[IntegrationAccount], Any] | None = None,
) -> dict[str, Any]:
    """Los `on-hold` de las tiendas Woo activas, leídos en vivo y en paralelo.

    Devuelve `{items, tiendas, consulta}` (contrato de la API). `q` filtra por
    número, cliente, empresa o email (sin tildes ni mayúsculas); si es un
    número que no está entre los `on-hold`, `consulta` dice qué es ese número
    en cada tienda donde exista. Solo lectura: no toca BoHub ni las tiendas."""
    inicio = time.monotonic()
    factory = client_factory or _cliente_listado
    tiendas: list[_Tienda] = []
    for store in woo_stores(session):
        t = _Tienda(store_id=store.id, account_id=store.account_id,
                    nombre=store.display_name or store.account_id,
                    base_url=str(store.base_url or ""))
        try:
            t.client = factory(store)
        except Exception as exc:  # noqa: BLE001 — credenciales rotas: una tienda menos
            t.error = (str(exc) if isinstance(exc, WooError) else type(exc).__name__)[:200]
        tiendas.append(t)

    limite = inicio + TIEMPO_LISTADO
    resultados = _en_paralelo(
        {t.store_id: (lambda c=t.client: _listar_en_espera(c, limite))
         for t in tiendas if t.client is not None},
        TIEMPO_LISTADO,
    )

    texto = _sin_tildes((q or "").strip().lstrip("#"))
    items: list[dict[str, Any]] = []
    resumen: list[dict[str, Any]] = []
    for t in tiendas:
        ok, res = resultados.get(t.store_id, (False, t.error or "sin cliente"))
        resumen.append({
            "store_id": t.store_id, "tienda": t.account_id, "tienda_nombre": t.nombre,
            "ok": ok, "error": None if ok else res, "en_espera": len(res) if ok else 0,
        })
        if not ok:
            logger.warning("pedidos web en espera: %s no responde: %s", t.account_id, res)
            continue
        hay = _en_bohub(session, t, res) if res else {}
        for wo in res:
            item = _item(t, wo)
            item["en_bohub"] = hay.get(item["woo_id"])
            if _coincide(item, texto):
                items.append(item)
    items.sort(key=lambda it: (it["fecha"] or "", it["woo_id"]), reverse=True)

    consulta: list[dict[str, Any]] = []
    es_numero = texto.isascii() and texto.isdigit() and len(texto) <= 12
    if es_numero and not any(it["numero"] == texto for it in items):
        restante = min(TIEMPO_CONSULTA, TIEMPO_TOTAL - (time.monotonic() - inicio))
        consulta = _consulta_numero(session, tiendas, resumen, int(texto), restante)
    return {"items": items, "tiendas": resumen, "consulta": consulta}


def _consulta_numero(
    session: Session, tiendas: list[_Tienda], resumen: list[dict[str, Any]],
    numero: int, plazo: float,
) -> list[dict[str, Any]]:
    """Qué es el pedido `numero` en cada tienda que respondió al listado."""
    vivas = {r["store_id"] for r in resumen if r["ok"]}
    candidatas = [t for t in tiendas if t.store_id in vivas and t.client is not None]
    if plazo <= 0 or not candidatas:
        return []
    resultados = _en_paralelo(
        {t.store_id: (lambda c=t.client: _buscar_numero(c, numero)) for t in candidatas},
        plazo,
    )
    out: list[dict[str, Any]] = []
    for t in candidatas:
        ok, wo = resultados.get(t.store_id, (False, None))
        if not ok or not wo:
            continue
        item = _item(t, wo)
        if item["numero"] != str(numero):
            continue                 # otro pedido con ese id interno: no es el que se busca
        item["en_bohub"] = _en_bohub(session, t, [wo]).get(item["woo_id"])
        estado = _estado(wo)
        motivo, se_puede = motivo_estado(estado, item["en_bohub"] is not None)
        item.update(estado=estado, motivo=motivo, se_puede_traer=se_puede)
        out.append(item)
    return out


# --- traer ----------------------------------------------------------------------------


def _tienda_por_id(session: Session, store_id: str) -> IntegrationAccount:
    store = session.get(IntegrationAccount, str(store_id))
    if store is None or store.system != ExternalSystem.WOOCOMMERCE or not store.enabled:
        raise PedidoWebError(404, "tienda_desconocida",
                             "La tienda no existe o no está activa.")
    return store


def _pedido_de_la_tienda(
    session: Session, store: IntegrationAccount, woo_id: int,
) -> Order | None:
    """El pedido de BoHub de ese pedido Woo (la llave del importador)."""
    return session.scalar(select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id == str(woo_id),
        Order.store_id == store.id,
    ).limit(1))


def _es_este(order: Order, store: IntegrationAccount, woo_id: int) -> bool:
    return (
        str(getattr(order.external_source, "value", order.external_source) or "")
        == OrderSource.WOOCOMMERCE.value
        and str(order.external_id or "") == str(woo_id)
        and order.store_id == store.id
    )


def referencia_factusol(session: Session, store: IntegrationAccount, order_number: str) -> str:
    """REFPCL con la que la app de la web deja el pedido de cliente en
    FACTUSOL: prefijo de la tienda (Configuración ERP) + nº a 6 cifras."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _compose_ref,
        _store_ref_prefix,
    )

    prefix = _store_ref_prefix(session, SimpleNamespace(store_id=store.id))
    return _compose_ref(order_number, prefix)


def _otro_pedido(
    session: Session, store: IntegrationAccount, woo_id: int, order_number: str, ref: str,
) -> Order | None:
    """Otro pedido de BoHub que ya es este pedido web: el mismo nº de pedido
    (de cualquier origen, p. ej. dado de alta a mano), el mismo id de Woo sin
    tienda, o uno creado «desde FACTUSOL» a partir de su pedido de cliente
    (`packing_json.factusol_source.referencia` = su REFPCL)."""
    for o in session.scalars(select(Order).where(Order.order_number == order_number)):
        if not _es_este(o, store, woo_id):
            return o
    sin_tienda = session.scalar(select(Order).where(
        Order.external_source == OrderSource.WOOCOMMERCE,
        Order.external_id == str(woo_id),
        Order.store_id.is_(None),
    ).limit(1))
    if sin_tienda is not None:
        return sin_tienda
    for o in session.scalars(select(Order).where(Order.packing_json.like(f"%{ref}%"))):
        src = _packing(o).get("factusol_source")
        if isinstance(src, dict) and str(src.get("referencia") or "").strip().upper() == ref:
            return o
    return None


def _veredicto(codigo: str, mensaje: str, referencia: str) -> dict[str, Any]:
    return {"codigo": codigo, "mensaje": mensaje, "referencia": referencia}


def _importe_casa(valor: Any, total: float) -> bool:
    try:
        return abs(float(valor) - float(total)) <= TOLERANCIA_IMPORTE + 1e-9
    except (TypeError, ValueError):
        return False


def _ejercicio_del_pedido(activo: str, fecha: str | None) -> str:
    """Ejercicio en el que buscar: el del año del pedido si es anterior al
    activo (un pedido de diciembre traído en enero está en el ejercicio
    viejo); si no, el activo (`ejercicio_for`)."""
    anio = (fecha or "")[:4]
    if anio.isdigit() and str(activo).isdigit() and int(anio) < int(activo):
        return anio
    return activo


def veredicto_factusol(
    session: Session, store: IntegrationAccount, *,
    order_number: str, cif: str | None, total: float, fecha: str | None = None,
) -> dict[str, Any]:
    """¿Está ya en FACTUSOL el pedido de cliente de este pedido web? SOLO LEE.

    - `enlazado`: exactamente un F_PCL con su REFPCL, de un cliente cuyo NIF
      es el CIF de la web y con el mismo total (±0,01).
    - `posible`: ninguno con esa REFPCL pero uno de ese CIF con ese importe
      (la referencia no casa: revisar el prefijo de la tienda).
    - `no_encontrado`: ni por referencia ni por CIF + importe.
    - `dudoso`: varios candidatos, o el de la referencia no casa en cliente o
      importe.
    - `sin_factusol`: FACTUSOL no configurado o no responde.

    En ningún caso se crea ni se cambia nada en FACTUSOL."""
    from app.erp import web_order_company  # noqa: PLC0415

    ref = referencia_factusol(session, store, order_number)
    pair = web_order_company.factusol_client_and_ejercicio(session)
    if pair is None:
        return _veredicto(
            "sin_factusol",
            "FACTUSOL no está configurado: no se ha podido comprobar el pedido de "
            "cliente. No se crea nada en FACTUSOL.",
            ref,
        )
    client, activo = pair
    try:
        return _veredicto_factusol(
            client, _ejercicio_del_pedido(activo, fecha), ref, cif, total,
        )
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído no impide traer
        logger.warning("pedidos web: FACTUSOL no responde para %s: %s", ref, exc)
        return _veredicto(
            "sin_factusol",
            "FACTUSOL no responde: no se ha podido comprobar el pedido de cliente. "
            "No se crea nada en FACTUSOL.",
            ref,
        )


def _veredicto_factusol(
    client: Any, ejercicio: str, ref: str, cif: str | None, total: float,
) -> dict[str, Any]:
    from app.erp.company_discovery import nif_key  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        get_customer,
        search_customers,
    )
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415

    nada = "No se crea nada en FACTUSOL."
    clave_web = nif_key(cif)
    # La misma consulta que `find_pcl_by_order`, pero con TODAS las filas: el
    # veredicto exige que haya exactamente una.
    filas = client.load_table("F_PCL", filtro=f"REFPCL='{ref}'", ejercicio=ejercicio)
    if len(filas) > 1:
        return _veredicto(
            "dudoso",
            f"Hay {len(filas)} pedidos de cliente con la referencia {ref} en FACTUSOL: "
            f"no se puede saber cuál es este. Revísalo en FACTUSOL. {nada}",
            ref,
        )
    if len(filas) == 1:
        pcl = filas[0]
        numero = visible_number(pcl.get("TIPPCL"), pcl.get("CODPCL"))
        codcli = str(pcl.get("CLIPCL") or "").strip()
        cliente = get_customer(client, codcli, ejercicio=ejercicio) if codcli else None
        nif_fs = (cliente or {}).get("nif")
        cliente_ok = bool(clave_web) and clave_web == nif_key(nif_fs)
        total_ok = _importe_casa(pcl.get("TOTPCL"), total)
        if cliente_ok and total_ok:
            return _veredicto(
                "enlazado",
                f"El pedido de cliente {numero} de FACTUSOL ({ref}) es este pedido: "
                f"mismo cliente ({cif}) e importe. BoHub lo enlaza por la referencia. {nada}",
                ref,
            )
        motivos: list[str] = []
        if not clave_web:
            motivos.append("la web no trae CIF para confirmar el cliente")
        elif not cliente_ok:
            motivos.append(
                f"su cliente ({codcli or '—'}, NIF {nif_fs or '—'}) no es el CIF de la web ({cif})"
            )
        if not total_ok:
            motivos.append(
                f"su importe ({pcl.get('TOTPCL')}) no es el de la web ({total:.2f})"
            )
        return _veredicto(
            "dudoso",
            f"Hay un pedido de cliente con la referencia {ref} ({numero}), pero "
            f"{' y '.join(motivos)}. Revísalo en FACTUSOL. {nada}",
            ref,
        )

    if not clave_web:
        return _veredicto(
            "no_encontrado",
            f"FACTUSOL aún no tiene un pedido de cliente con la referencia {ref} (y la "
            f"web no trae CIF para buscarlo por cliente). {nada}",
            ref,
        )
    codclis = [
        str(c["codcli"]) for c in search_customers(client, cif or "", by="nif",
                                                  ejercicio=ejercicio)
        if c.get("codcli") and nif_key(c.get("nif")) == clave_web
    ]
    candidatos: list[dict[str, Any]] = []
    for codcli in dict.fromkeys(codclis):
        if not codcli.isdigit():
            continue
        candidatos.extend(
            f for f in client.load_table(
                "F_PCL", filtro=f"CLIPCL={int(codcli)}", ejercicio=ejercicio,
            )
            if _importe_casa(f.get("TOTPCL"), total)
        )
    if len(candidatos) == 1:
        pcl = candidatos[0]
        otra_ref = str(pcl.get("REFPCL") or "").strip() or "sin referencia"
        return _veredicto(
            "posible",
            f"No hay pedido de cliente con la referencia {ref}, pero sí uno de ese CIF "
            f"con ese importe ({visible_number(pcl.get('TIPPCL'), pcl.get('CODPCL'))}, "
            f"referencia {otra_ref}): la referencia no casa. Revisa el prefijo de la "
            f"tienda en Configuración ERP. {nada}",
            ref,
        )
    if len(candidatos) > 1:
        return _veredicto(
            "dudoso",
            f"No hay pedido de cliente con la referencia {ref} y hay {len(candidatos)} de "
            f"ese CIF con ese importe: no se puede saber cuál es. {nada}",
            ref,
        )
    return _veredicto(
        "no_encontrado",
        f"FACTUSOL aún no tiene el pedido de cliente de este pedido ({ref}), ni por "
        f"referencia ni por CIF e importe. Cuando la web lo pase a FACTUSOL, BoHub lo "
        f"enlazará por la referencia. {nada}",
        ref,
    )


def _guardar_veredicto(order: Order, veredicto: dict[str, Any]) -> None:
    """`packing_json["traido_a_mano"] = {factusol, diferencias}` (sin pisar
    los demás bloques del packing)."""
    data = _packing(order)
    previo = data.get("traido_a_mano")
    diferencias = previo.get("diferencias") if isinstance(previo, dict) else None
    data["traido_a_mano"] = {"factusol": veredicto, "diferencias": diferencias}
    order.packing_json = json.dumps(data)


def _veredicto_guardado(order: Order) -> dict[str, Any] | None:
    bloque = _packing(order).get("traido_a_mano")
    fs = bloque.get("factusol") if isinstance(bloque, dict) else None
    return fs if isinstance(fs, dict) else None


def _veredicto_de_pedido(session: Session, store: IntegrationAccount, order: Order) -> dict:
    """Veredicto de un pedido que ya está en BoHub: el guardado al traerlo o,
    si entró solo, el que da FACTUSOL ahora (solo lectura, no se guarda)."""
    guardado = _veredicto_guardado(order)
    if guardado is not None:
        return guardado
    destino = _packing(order).get("shipping_address")
    cif = destino.get("nif") if isinstance(destino, dict) else None
    fecha = order.placed_at.isoformat() if order.placed_at else None
    return veredicto_factusol(
        session, store, order_number=order.order_number, cif=cif or None,
        total=float(order.total_amount or 0), fecha=fecha,
    )


def traer_pedido_web(
    session: Session,
    *,
    store_id: str,
    woo_id: int,
    user: User,
    client_factory: Callable[[IntegrationAccount], Any] | None = None,
) -> dict[str, Any]:
    """Trae a BoHub el pedido `woo_id` de la tienda `store_id`.

    Devuelve `{order_id, order_number, creado, factusol}`; lanza
    `PedidoWebError` con su código (ver el contrato de la API). Hace commit."""
    store = _tienda_por_id(session, store_id)
    ya = _pedido_de_la_tienda(session, store, woo_id)
    if ya is not None:
        return {"order_id": ya.id, "order_number": ya.order_number, "creado": False,
                "factusol": _veredicto_de_pedido(session, store, ya)}

    # El pedido FRESCO de la tienda (la lista puede estar desfasada).
    try:
        wo = (client_factory or _cliente_traer)(store).get_order(int(woo_id))
    except WooError as exc:
        if exc.status == 404:
            raise PedidoWebError(404, "pedido_no_encontrado",
                                 f"El pedido {woo_id} no existe en {store.account_id}.") from exc
        raise PedidoWebError(502, "tienda_no_responde",
                             f"La tienda {store.account_id} no responde: {str(exc)[:200]}") from exc
    if not isinstance(wo, dict) or not wo.get("id"):
        raise PedidoWebError(404, "pedido_no_encontrado",
                             f"El pedido {woo_id} no existe en {store.account_id}.")

    estado = _estado(wo)
    numero = str(wo.get("number") or wo.get("id"))
    if estado == "cancelled":
        raise PedidoWebError(409, "cancelado_en_la_web",
                             f"El pedido {numero} está cancelado en la web: no se puede traer.")
    if estado in ESTADOS_NO_PEDIDO:
        raise PedidoWebError(
            409, "no_es_un_pedido",
            f"El pedido {numero} está «{estado}» en la web (carrito sin pagar o pago "
            "fallido): no es un pedido, no se trae.",
        )
    if estado != ESTADO_EN_ESPERA and estado not in ESTADOS_PAGADOS:
        raise PedidoWebError(409, "estado_no_admitido",
                             f"El pedido {numero} está «{estado or '?'}» en la web: no se trae.")

    wo["_store_slug"] = store.account_id
    order_number = _order_number(wo)
    ref = referencia_factusol(session, store, order_number)
    otro = _otro_pedido(session, store, int(woo_id), order_number, ref)
    if otro is not None:
        raise PedidoWebError(
            409, "ya_hay_pedido_en_bohub",
            f"Ya hay en BoHub un pedido para este pedido web ({otro.order_number}): "
            "no se trae otro.",
            order_id=otro.id,
        )

    # FACTUSOL ANTES del cerrojo de la tienda (es red): solo lectura.
    veredicto = veredicto_factusol(
        session, store, order_number=order_number, cif=_extract_cif(wo),
        total=_num(wo.get("total")), fecha=_fecha(wo),
    )
    autorizacion = TraidoAMano(user_id=user.id) if estado == ESTADO_EN_ESPERA else None
    try:
        outcome = import_woo_order(session, store=store, woo_order=wo,
                                   traido_a_mano=autorizacion)
    except ImportacionSimultanea as exc:
        # Otro clic (o el webhook) lo ha creado mientras se esperaba el
        # cerrojo: se devuelve ese, leído en una transacción nueva.
        session.rollback()
        ya = session.get(Order, exc.order_id)
        if ya is None:
            raise PedidoWebError(409, "ya_hay_pedido_en_bohub",
                                 "El pedido se está trayendo a la vez: vuelve a probar.") from exc
        return {"order_id": ya.id, "order_number": ya.order_number, "creado": False,
                "factusol": _veredicto_guardado(ya) or veredicto}
    except ValueError as exc:
        session.rollback()
        raise PedidoWebError(422, "pedido_no_importable",
                             f"El pedido {numero} no se puede importar: {exc}") from exc
    if outcome.order_id is None:
        session.rollback()
        raise PedidoWebError(409, "estado_no_admitido",
                             f"El pedido {numero} está «{estado}» en la web: no se trae.")
    order = session.get(Order, outcome.order_id)
    if outcome.created:
        if es_traido_a_mano(order):
            _guardar_veredicto(order, veredicto)
        from app.core.audit import record_event  # noqa: PLC0415

        record_event(
            session, action=TRAIDO_EVENT, target_type="order", target_id=order.id,
            actor=user,
            message=f"Pedido web {order.order_number} traído a mano ({estado})",
            metadata={"store": store.account_id, "woo_id": int(woo_id),
                      "woo_status": estado, "autorizado": autorizacion is not None,
                      "factusol": veredicto["codigo"]},
        )
    else:
        veredicto = _veredicto_guardado(order) or veredicto
    session.commit()
    logger.info(
        "pedidos web: %s %s por %s (FACTUSOL: %s)", order.order_number,
        "traído" if outcome.created else "ya estaba", user.id, veredicto["codigo"],
    )
    return {"order_id": order.id, "order_number": order.order_number,
            "creado": outcome.created, "factusol": veredicto}


# --- ficha del pedido -------------------------------------------------------------------


def traido_a_mano_detalle(session: Session, order: Order) -> dict[str, Any] | None:
    """Bloque `traido_a_mano` del detalle del pedido: `{at, por, factusol,
    diferencias}` o None si entró solo."""
    if not es_traido_a_mano(order):
        return None
    por = None
    if order.manual_web_import_by_user_id:
        u = session.get(User, order.manual_web_import_by_user_id)
        por = u.full_name if u is not None else None
    bloque = _packing(order).get("traido_a_mano")
    bloque = bloque if isinstance(bloque, dict) else {}
    fs = bloque.get("factusol")
    diferencias = [d for d in (bloque.get("diferencias") or []) if isinstance(d, str)]
    return {
        "at": order.manual_web_import_at.isoformat(),
        "por": por,
        "factusol": fs if isinstance(fs, dict) else None,
        "diferencias": diferencias or None,
    }


__all__ = [
    "ESTADO_EN_ESPERA",
    "PedidoWebError",
    "es_traido_a_mano",
    "motivo_estado",
    "pedidos_en_espera",
    "traer_pedido_web",
    "traido_a_mano_detalle",
    "veredicto_factusol",
]
