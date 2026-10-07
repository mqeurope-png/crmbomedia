"""ERP · WooCommerce — pedidos PAGADOS en la tienda que no están en BoHub.

Caso (07/10/2026): el pedido 99976 de boprint se creó `on-hold` (sus webhooks
llegaron y se ignoraron: aún no estaba pagado) y el plugin de TSM lo marcó
pagado a las 09:02 SIN que la tienda disparara ningún webhook, así que nunca
entró en BoHub. Hubo otro igual en fluxlasers. La puesta al día de estados solo
miraba los pedidos que BoHub ya conocía.

Aquí se pide a cada tienda la LISTA de sus pedidos pagados —los estados con
los que BoHub CREA un pedido (`CREATE_ON_STATUSES`: processing, completed,
refunded)— creados en los últimos N días (90 por defecto; el repaso periódico,
además, solo los modificados desde su última pasada) y se cruza con BoHub. Los
que faltan:

- se IMPORTAN por el mismo camino que el webhook y «Reimportar pedido»
  (`upsert_backfill_event` + `import_order_from_event` → `import_woo_order`):
  empresa, líneas, método de pago y Cola SAT quedan como si hubiera llegado el
  webhook. Idempotente: un pedido que ya está en BoHub no se pasa al
  importador (ni se refresca ni se pisa) y una segunda pasada no encuentra
  nada que importar;
- o solo se LISTAN (vista previa de «Poner al día estados Woo…» y la
  comprobación del Cuadre «Pedido pagado en WooCommerce que no está en BoHub»).

Barato: una consulta por tienda (todos los estados pagados juntos, filtrados
por fecha), de 100 en 100. Los recién modificados (menos de
`GRACIA_MINUTOS`) se dejan para la siguiente pasada: su webhook puede estar
en camino y así no se importan dos veces a la vez.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.erp.models import Order, OrderSource
from app.integrations.woocommerce.client import WooError, WooHTTPClient
from app.integrations.woocommerce.mapper import CREATE_ON_STATUSES
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount

logger = logging.getLogger(__name__)

#: Estados pagados que se piden a la tienda: los mismos con los que el
#: importador CREA un pedido (un `on-hold` / `pending` no es un pedido aún).
PAID_STATUSES: tuple[str, ...] = tuple(sorted(CREATE_ON_STATUSES))

#: Configuración ERP (blob de ajustes): interruptor y cada cuánto corre el
#: repaso periódico, y cuántos días hacia atrás se mira.
ENABLED_KEY = "woo_missing_check_enabled"
INTERVAL_KEY = "woo_missing_check_interval_minutes"
DAYS_KEY = "woo_missing_days"
DEFAULT_ENABLED = True
DEFAULT_INTERVAL_MINUTES = 60
MIN_INTERVAL_MINUTES = 15
MAX_INTERVAL_MINUTES = 1440
DEFAULT_DAYS = 90
MAX_DAYS = 365

#: Operación de `sync_logs` de cada pasada que importa (una fila por tienda):
#: deja rastro en Integraciones y marca desde dónde mira el repaso siguiente.
OPERATION = "import_missing"
#: Un pedido modificado hace menos de esto no se juzga: su webhook puede estar
#: en camino (se mira en la pasada siguiente).
GRACIA_MINUTOS = 5
#: El repaso periódico mira lo modificado desde su última pasada buena menos
#: este margen (mayor que la gracia, para no perder los que se dejaron).
MARGEN_MINUTOS = 15
#: Primera pasada periódica de una tienda (sin pasadas anteriores): solo el
#: último día. Los N días enteros, con vista previa, son de «Poner al día…».
PRIMERA_PASADA_HORAS = 24

_PER_PAGE = 100
DEFAULT_MAX_PAGES = 20
#: Importaciones por pasada como mucho (cada una puede consultar FACTUSOL para
#: la empresa, como el webhook); el resto entra en la siguiente.
DEFAULT_MAX_IMPORTS = 50


# --- configuración ------------------------------------------------------------------


def _int_in(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(n, lo), hi)


def config_from_series(series: dict[str, Any] | None) -> dict[str, Any]:
    """`{enabled, interval_minutes, days}` con los defaults (encendido, 60 min,
    90 días)."""
    series = series if isinstance(series, dict) else {}
    enabled = series.get(ENABLED_KEY)
    return {
        "enabled": DEFAULT_ENABLED if enabled is None else bool(enabled),
        "interval_minutes": _int_in(series.get(INTERVAL_KEY), DEFAULT_INTERVAL_MINUTES,
                                    MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES),
        "days": _int_in(series.get(DAYS_KEY), DEFAULT_DAYS, 1, MAX_DAYS),
    }


def missing_config(session: Session) -> dict[str, Any]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return config_from_series(series_config(session))


# --- lo que falta -------------------------------------------------------------------


@dataclass
class PedidoAusente:
    """Un pedido pagado de la tienda que no está en BoHub."""

    tienda: str             # account_id
    tienda_nombre: str
    woo_id: int
    numero: str             # nº de pedido de la tienda
    estado: str
    cliente: str
    importe: str
    moneda: str
    pagado_el: str | None   # ISO (UTC)
    enlace: str             # el pedido en el admin de la tienda

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_gmt(value: Any) -> datetime | None:
    """Fecha de Woo (`date_*_gmt`, sin zona: es UTC) → datetime UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(dt)


def _iso_utc(value: datetime) -> str:
    return _aware(value).astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def _cliente(wo: dict[str, Any]) -> str:
    b = wo.get("billing") or {}
    nombre = " ".join(
        p for p in (str(b.get("first_name") or "").strip(), str(b.get("last_name") or "").strip())
        if p
    )
    empresa = str(b.get("company") or "").strip()
    if empresa and nombre:
        return f"{empresa} ({nombre})"
    return empresa or nombre or str(b.get("email") or "").strip() or "—"


def _pagado_el(wo: dict[str, Any]) -> str | None:
    for clave in ("date_paid_gmt", "date_completed_gmt", "date_modified_gmt", "date_created_gmt"):
        dt = _parse_gmt(wo.get(clave))
        if dt is not None:
            return dt.isoformat()
    return None


def enlace_tienda(store: IntegrationAccount, woo_id: int | str) -> str:
    """El pedido en el admin de WordPress (WooCommerce redirige solo a la
    pantalla nueva de pedidos si la tienda la usa)."""
    base = str(store.base_url or "").rstrip("/")
    return f"{base}/wp-admin/post.php?post={woo_id}&action=edit"


def _ausente(store: IntegrationAccount, wo: dict[str, Any]) -> PedidoAusente:
    woo_id = int(wo["id"])
    return PedidoAusente(
        tienda=store.account_id,
        tienda_nombre=store.display_name or store.account_id,
        woo_id=woo_id,
        numero=str(wo.get("number") or woo_id),
        estado=str(wo.get("status") or "").strip().lower(),
        cliente=_cliente(wo),
        importe=str(wo.get("total") or "0"),
        moneda=str(wo.get("currency") or "EUR"),
        pagado_el=_pagado_el(wo),
        enlace=enlace_tienda(store, woo_id),
    )


def _order_number(store: IntegrationAccount, wo: dict[str, Any]) -> str:
    """El nº que le pondría el importador (`mapper._order_number`)."""
    return f"{store.account_id.upper()[:6]}-{wo.get('number') or wo.get('id')}"


def _ya_en_bohub(session: Session, store: IntegrationAccount, pedidos: dict[str, dict]) -> set[str]:
    """Ids de Woo (de `pedidos`) que BoHub ya tiene: por id de la tienda (los de
    esta tienda o, por prudencia, los que no guardan tienda) o por el mismo nº
    de pedido (uno dado de alta a mano con ese número). Esos NO se importan."""
    ids = list(pedidos)
    numeros = {_order_number(store, wo): wid for wid, wo in pedidos.items()}
    hay: set[str] = set()
    for i in range(0, len(ids), 500):
        trozo = ids[i:i + 500]
        hay.update(str(x) for x in session.scalars(select(Order.external_id).where(
            Order.external_source == OrderSource.WOOCOMMERCE,
            Order.external_id.in_(trozo),
            or_(Order.store_id == store.id, Order.store_id.is_(None)),
        )))
    claves = list(numeros)
    for i in range(0, len(claves), 500):
        for num in session.scalars(select(Order.order_number).where(
            Order.order_number.in_(claves[i:i + 500]),
        )):
            if num in numeros:
                hay.add(numeros[num])
    return hay


def pagados_que_faltan(
    session: Session,
    store: IntegrationAccount,
    client: Any,
    *,
    days: int,
    now: datetime,
    modified_after: datetime | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Pedidos pagados de UNA tienda (creados en los últimos `days` días y, si
    se da, modificados desde `modified_after`) que no están en BoHub. Una
    consulta (paginada) a la tienda. Lanza `WooError` si la tienda falla.

    Devuelve `{"faltan": [(PedidoAusente, payload)], "listados", "llamadas",
    "con_tope"}`."""
    desde = now - timedelta(days=days)
    gracia = now - timedelta(minutes=GRACIA_MINUTOS)
    vistos: dict[str, dict[str, Any]] = {}
    llamadas = 0
    con_tope = False
    for page in range(1, max_pages + 1):
        llamadas += 1
        lote = client.list_orders(
            status=",".join(PAID_STATUSES), since=_iso_utc(desde),
            modified_after=_iso_utc(modified_after) if modified_after else None,
            dates_are_gmt=True, per_page=_PER_PAGE, page=page,
        )
        if not lote:
            break
        for wo in lote:
            if not isinstance(wo, dict) or not wo.get("id"):
                continue
            if str(wo.get("status") or "").strip().lower() not in CREATE_ON_STATUSES:
                continue                       # por si la tienda ignora el filtro
            modificado = _parse_gmt(wo.get("date_modified_gmt"))
            if modificado is not None and modificado > gracia:
                continue                       # su webhook puede estar en camino
            vistos[str(wo["id"])] = wo
        if len(lote) < _PER_PAGE:
            break
        if page == max_pages:
            con_tope = True
    hay = _ya_en_bohub(session, store, vistos) if vistos else set()
    faltan = [
        (_ausente(store, wo), wo)
        for wid, wo in sorted(vistos.items(), key=lambda kv: int(kv[0]))
        if wid not in hay
    ]
    return {"faltan": faltan, "listados": len(vistos), "llamadas": llamadas,
            "con_tope": con_tope}


def woo_stores(session: Session, store_account_id: str | None = None) -> list[IntegrationAccount]:
    """Tiendas Woo habilitadas (las mismas a las que se aceptan webhooks)."""
    stmt = select(IntegrationAccount).where(
        IntegrationAccount.system == ExternalSystem.WOOCOMMERCE,
        IntegrationAccount.enabled.is_(True),
    ).order_by(IntegrationAccount.account_id)
    if store_account_id:
        stmt = stmt.where(IntegrationAccount.account_id == store_account_id)
    return list(session.scalars(stmt))


# --- importar ------------------------------------------------------------------------


def _importar_como_webhook(session: Session, store: IntegrationAccount, wo: dict[str, Any]) -> dict:
    """El camino de «Reimportar pedido»: evento `backfill:{id}` + import inline
    (`import_woo_order`, el mismo del webhook)."""
    from app.integrations.woocommerce.jobs import (  # noqa: PLC0415
        import_order_from_event,
        upsert_backfill_event,
    )

    event_id = upsert_backfill_event(session, store, int(wo["id"]), wo)
    session.commit()
    return import_order_from_event(event_id)


def _ultima_pasada(session: Session, account_id: str) -> datetime | None:
    """Inicio de la última pasada de la tienda que listó TODO lo que tocaba
    (la tienda respondió y sin tope). Una importación fallida no la invalida:
    ese pedido lo sigue avisando el Cuadre y lo reintenta «Poner al día…»."""
    from app.models.crm import SyncLog, SyncStatus  # noqa: PLC0415

    filas = session.scalars(select(SyncLog).where(
        SyncLog.system == ExternalSystem.WOOCOMMERCE,
        SyncLog.account_id == account_id,
        SyncLog.operation == OPERATION,
        SyncLog.status.in_((SyncStatus.SUCCESS.value, SyncStatus.PARTIAL_SUCCESS.value)),
    ).order_by(SyncLog.started_at.desc()).limit(20))
    for fila in filas:
        try:
            meta = json.loads(fila.metadata_json or "{}")
        except (TypeError, ValueError):
            meta = {}
        if fila.started_at is not None and not (isinstance(meta, dict) and meta.get("con_tope")):
            return _aware(fila.started_at)
    return None


def desde_para_repaso(session: Session, store: IntegrationAccount, *, now: datetime,
                      days: int) -> datetime:
    """Desde cuándo mira el repaso periódico (fecha de modificación): su última
    pasada buena menos el margen; sin pasadas, el último día. Nunca antes de
    los `days` días."""
    ultima = _ultima_pasada(session, store.account_id)
    desde = (ultima - timedelta(minutes=MARGEN_MINUTOS)) if ultima \
        else now - timedelta(hours=PRIMERA_PASADA_HORAS)
    return max(desde, now - timedelta(days=days))


def _registrar(session: Session, store: IntegrationAccount, *, inicio: datetime,
               trigger: str, resultado: dict[str, Any]) -> None:
    from app.models.crm import SyncLog, SyncStatus  # noqa: PLC0415

    fallo = resultado.get("error")
    errores = resultado["fallidos"] + (1 if fallo else 0)
    if fallo:
        estado = SyncStatus.FAILED.value
    elif resultado["fallidos"] or resultado["con_tope"]:
        estado = SyncStatus.PARTIAL_SUCCESS.value
    else:
        estado = SyncStatus.SUCCESS.value
    session.add(SyncLog(
        system=ExternalSystem.WOOCOMMERCE, account_id=store.account_id,
        operation=OPERATION, status=estado, started_at=inicio,
        finished_at=datetime.now(UTC), records_processed=resultado["importados"],
        records_failed=errores, triggered_by=trigger,
        error_summary=str(fallo)[:2000] if fallo else None,
        message=(f"pagados que faltan: {resultado['faltan']} · importados "
                 f"{resultado['importados']}" + (f" · {resultado['fallidos']} con error"
                                                  if resultado["fallidos"] else "")),
        metadata_json=json.dumps({
            "faltan": resultado["faltan"], "importados": resultado["importados"],
            "numeros": resultado["numeros"][:50], "con_tope": resultado["con_tope"],
        }, ensure_ascii=False),
    ))
    session.commit()


def import_missing_paid_orders(
    session: Session,
    *,
    dry_run: bool = True,
    store_account_id: str | None = None,
    days: int | None = None,
    incremental: bool = False,
    trigger: str = "manual",
    client_factory: Callable[[IntegrationAccount], Any] = WooHTTPClient,
    importer: Callable[[Session, IntegrationAccount, dict[str, Any]], dict] | None = None,
    now: datetime | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_imports: int = DEFAULT_MAX_IMPORTS,
) -> dict[str, Any]:
    """Busca en cada tienda los pedidos pagados que no están en BoHub y, si no
    es `dry_run`, los importa por el camino del webhook.

    - `incremental` (repaso periódico): solo lo modificado desde la última
      pasada buena de cada tienda; si no, los `days` días enteros.
    - Al importar deja una fila en `sync_logs` por tienda (operación
      `import_missing`); en vista previa no escribe nada.

    Devuelve `{dias, faltan, importados, items, errores, con_tope, woo_calls}`;
    cada item es un `PedidoAusente` con `resultado` (`a_importar` en vista
    previa; `importado` / `error` / `sin_importar` al aplicar) y, si se
    importó, `order_id` y `order_number`."""
    now = _aware(now or datetime.now(UTC))
    days = days or missing_config(session)["days"]
    importer = importer or _importar_como_webhook
    items: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    woo_calls = 0
    con_tope = False
    importados = 0
    presupuesto = max_imports

    for store in woo_stores(session, store_account_id):
        account_id = store.account_id
        inicio = now      # conservador: el repaso siguiente mira desde aquí
        resultado: dict[str, Any] = {"faltan": 0, "importados": 0, "fallidos": 0,
                                     "numeros": [], "con_tope": False}
        try:
            client = client_factory(store)
            modified_after = (
                desde_para_repaso(session, store, now=now, days=days) if incremental else None
            )
            vista = pagados_que_faltan(
                session, store, client, days=days, now=now,
                modified_after=modified_after, max_pages=max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — una tienda no corta a las demás
            session.rollback()
            texto = str(exc) if isinstance(exc, WooError) else f"{type(exc).__name__}: {exc}"
            errores.append({"store": account_id, "error": texto[:200]})
            logger.warning("woo.missing %s: no se pudo listar la tienda: %s", account_id, texto)
            if not dry_run:
                resultado["error"] = texto
                _registrar(session, store, inicio=inicio, trigger=trigger, resultado=resultado)
            continue
        woo_calls += vista["llamadas"]
        con_tope = con_tope or vista["con_tope"]
        resultado["con_tope"] = vista["con_tope"]
        resultado["faltan"] = len(vista["faltan"])

        for ausente, wo in vista["faltan"]:
            item = ausente.as_dict()
            items.append(item)
            if dry_run:
                item["resultado"] = "a_importar"
                continue
            if presupuesto <= 0:
                item["resultado"] = "sin_importar"   # tope de la pasada
                con_tope = resultado["con_tope"] = True
                continue
            presupuesto -= 1
            try:
                outcome = importer(session, store, wo) or {}
            except Exception as exc:  # noqa: BLE001 — uno no corta los demás
                session.rollback()
                outcome = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            order_id = outcome.get("order_id")
            if order_id and not outcome.get("error"):
                session.expire_all()
                order = session.get(Order, order_id)
                item.update(resultado="importado", order_id=order_id,
                            order_number=order.order_number if order else None)
                importados += 1
                resultado["importados"] += 1
                resultado["numeros"].append(item["order_number"] or ausente.numero)
            else:
                motivo = (outcome.get("error") or outcome.get("reason")
                          or (f"estado {outcome['skipped_status']}"
                              if outcome.get("skipped_status") else "no se creó"))
                item.update(resultado="error", error=str(motivo)[:200])
                resultado["fallidos"] += 1
                errores.append({"store": account_id, "order_number": ausente.numero,
                                "error": str(motivo)[:200]})
        if not dry_run:
            _registrar(session, store, inicio=inicio, trigger=trigger, resultado=resultado)
            if resultado["importados"]:
                logger.info("woo.missing %s: importados %d pedidos pagados que faltaban (%s)",
                            account_id, resultado["importados"],
                            ", ".join(resultado["numeros"][:20]))

    return {
        "dias": days,
        "faltan": len(items),
        "importados": importados,
        "items": items,
        "errores": errores,
        "con_tope": con_tope,
        "woo_calls": woo_calls,
    }


__all__ = [
    "DEFAULT_DAYS",
    "DEFAULT_INTERVAL_MINUTES",
    "PAID_STATUSES",
    "PedidoAusente",
    "config_from_series",
    "desde_para_repaso",
    "enlace_tienda",
    "import_missing_paid_orders",
    "missing_config",
    "pagados_que_faltan",
    "woo_stores",
]
