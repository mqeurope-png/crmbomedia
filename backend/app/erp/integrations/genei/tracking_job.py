"""Genei — sondeo periódico del tracking DETALLADO de los envíos vivos.

El webhook de Genei (`notificationUrl`) solo avisa de SU estado grueso (el mismo
objeto que `GET /shipments/{code}`); los escaneos del transportista («Pendiente
de entrada en red», «En reparto»…) solo se leen con `GET
/shipments/{code}/tracking`. Además, los envíos creados antes de que se
arreglara el secreto del webhook ya no reciben avisos. Este sondeo mantiene al
día los envíos vivos sin que nadie pulse «Actualizar estado»:

- Job RQ self-rescheduling en la cola `genei:shipments` (ya la escucha el
  `worker-sync`, `--with-scheduler`), mismo patrón que `seguimiento_sync_job`:
  latido con SETNX + `enqueue_in`, re-armado en `finally`.
- Cada `tracking_poll_minutes` (30 por defecto, mínimo 10) revisa los envíos
  Genei vivos (tramitados y no entregados/cerrados) cuya última consulta tiene
  más de ese tiempo: `GET /shipments/{code}` + `/tracking` → la misma
  `apply_shipment_state` que el webhook y «Actualizar estado». Idempotente.
- Como mucho `BATCH` envíos por pasada (el resto, en la siguiente), confirmando
  en BD tras cada uno. Si Genei rechaza las credenciales, la pasada se corta
  (el cliente ya no insiste: ver `GeneiAuthError`).
- Interruptor `tracking_poll_enabled` en la config de Genei (encendido por
  defecto: solo LEE de Genei). NUNCA paga ni crea nada.

Una pasada a mano: `python -m app.erp.integrations.genei.tracking_job`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

TRACKING_QUEUE = queue_name("genei", "shipments")
HEARTBEAT_KEY = "genei:tracking:heartbeat"
JOB_TIMEOUT_SECONDS = 600
#: Envíos por pasada, como mucho (el resto en la siguiente).
BATCH = 40
#: Un envío que lleva más de esto sin entregarse deja de sondearse (se queda
#: con «Actualizar estado» manual).
MAX_AGE_DAYS = 60

#: Estados de transporte en los que el envío sigue vivo.
_LIVE_TRANSPORT = ("not_shipped", "label_created", "in_transit", "incident")


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_live(order: Any) -> bool:
    """¿Envío Genei vivo (tramitado, aún sin entregar ni cerrar)?"""
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.integrations.genei.status import (  # noqa: PLC0415
        IN_TRANSIT,
        INCIDENT,
        READY,
    )

    state = genei_state_of(order)
    if not state.get("shipment_code"):
        return False
    if order.shipping_not_required:
        return False
    return state.get("state_bucket") in (READY, IN_TRANSIT, INCIDENT)


def due_orders(session: Session, *, minutes: int, now: datetime | None = None,
               limit: int = BATCH) -> list[Any]:
    """Pedidos con envío Genei vivo cuya última consulta de tracking tiene más
    de `minutes` (o nunca se consultó), los más atrasados primero."""
    from app.erp.integrations.genei.service import genei_state_of  # noqa: PLC0415
    from app.erp.models import Order  # noqa: PLC0415

    now = now or datetime.now(UTC)
    corte = now - timedelta(minutes=minutes)
    antiguedad = now - timedelta(days=MAX_AGE_DAYS)
    candidatos = session.scalars(
        select(Order).where(
            Order.packing_json.like('%"shipment_code"%'),
            Order.transport_status.in_(_LIVE_TRANSPORT),
        )
    )
    vencidos: list[tuple[float, Any]] = []
    for order in candidatos:
        if not _is_live(order):
            continue
        creado = order.created_at
        if creado is not None:
            creado = creado if creado.tzinfo else creado.replace(tzinfo=UTC)
            if creado < antiguedad:
                continue
        visto = _parse(genei_state_of(order).get("tracking_checked_at"))
        if visto is not None and visto > corte:
            continue
        vencidos.append(((visto.timestamp() if visto else 0.0), order))
    vencidos.sort(key=lambda t: t[0])
    return [o for _t, o in vencidos[:limit]]


def run_tracking_poll(
    session: Session, *, client_factory: Callable[[Any], Any] | None = None,
    force: bool = False, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Una pasada del sondeo. Devuelve el resumen, o None si no corre
    (interruptor apagado o Genei sin configurar)."""
    from app.erp.api.genei import build_client, get_genei_carrier  # noqa: PLC0415
    from app.erp.integrations.genei.client import (  # noqa: PLC0415
        GeneiAuthError,
        GeneiError,
    )
    from app.erp.integrations.genei.config import GeneiConfig  # noqa: PLC0415
    from app.erp.integrations.genei.service import (  # noqa: PLC0415
        genei_state_of,
        set_genei_state,
        shipment_code_of_order,
    )
    from app.erp.integrations.genei.webhook import (  # noqa: PLC0415
        apply_shipment_state,
        safe_tracking,
    )

    carrier = get_genei_carrier(session)
    if carrier is None or not carrier.api_credentials_encrypted:
        return None
    cfg = GeneiConfig.of(carrier)
    if not (cfg.tracking_poll_enabled or force):
        return None
    try:
        client = (client_factory or build_client)(carrier)
    except GeneiError as exc:
        logger.warning("genei.tracking: Genei sin configurar: %s", exc)
        return None

    pedidos = due_orders(session, minutes=cfg.tracking_poll_minutes, now=now)
    resumen = {"revisados": 0, "movidos": 0, "con_escaneo": 0, "errores": 0}
    for order in pedidos:
        code = shipment_code_of_order(order)
        if not code:
            continue
        try:
            shipment = client.get_shipment(str(code))
        except GeneiAuthError as exc:
            logger.warning("genei.tracking: credenciales rechazadas, se corta la pasada: %s",
                           exc)
            session.rollback()
            break
        except GeneiError as exc:
            resumen["errores"] += 1
            logger.info("genei.tracking %s: no se pudo leer el envío: %s", order.id, exc)
            # Se apunta la consulta para no reintentarlo en cada tic.
            set_genei_state(order, {"tracking_checked_at": datetime.now(UTC).isoformat()})
            session.commit()
            continue
        tracking = safe_tracking(client, str(code))
        _summary, movido = apply_shipment_state(session, order, shipment, tracking=tracking)
        if tracking is None:
            # Sin detalle esta vez: igual se apunta la consulta (no martillear).
            set_genei_state(order, {"tracking_checked_at": datetime.now(UTC).isoformat()})
        session.commit()
        # Aviso de envío al cliente si ya hay nº de seguimiento (una sola vez).
        from app.erp.shipment_email import maybe_send_shipment_email  # noqa: PLC0415

        def _url(c: str, _client: Any = client) -> str | None:
            try:
                return _client.get_tracking_url(c) if c else None
            except GeneiError:
                return None

        maybe_send_shipment_email(session, order, actor=None, tracking_url_fetcher=_url)
        resumen["revisados"] += 1
        resumen["movidos"] += int(bool(movido))
        resumen["con_escaneo"] += int(bool(genei_state_of(order).get("carrier_status")))
    logger.info("genei.tracking: %d envíos revisados (%d con escaneo del transportista), "
                "%d movidos, %d errores", resumen["revisados"], resumen["con_escaneo"],
                resumen["movidos"], resumen["errores"])
    return resumen


# --- armado (job RQ self-rescheduling) ----------------------------------------


def _interval() -> timedelta:
    from app.erp.integrations.genei.config import (  # noqa: PLC0415
        TRACKING_POLL_MIN_MINUTES,
        GeneiConfig,
    )

    minutes = 30
    try:
        from app.db.session import get_engine  # noqa: PLC0415
        from app.erp.api.genei import get_genei_carrier  # noqa: PLC0415

        with Session(get_engine()) as session:
            carrier = get_genei_carrier(session)
            if carrier is not None:
                minutes = GeneiConfig.of(carrier).tracking_poll_minutes
    except Exception:  # noqa: BLE001
        minutes = 30
    # El latido va a la MITAD del intervalo por envío: así ningún envío espera
    # mucho más de su intervalo aunque le toque justo después de un tic.
    return timedelta(minutes=max(minutes // 2, TRACKING_POLL_MIN_MINUTES // 2))


def schedule_tracking_poll() -> None:
    """Arma el siguiente tic. Idempotente vía SETNX (api y worker a la vez)."""
    interval = _interval()
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        ttl = max(int(interval.total_seconds()) - 30, 10)
        if not conn.set(HEARTBEAT_KEY, "1", nx=True, ex=ttl):
            return
        try:
            Queue(TRACKING_QUEUE, connection=conn,
                  default_timeout=JOB_TIMEOUT_SECONDS).enqueue_in(interval, _tracking_runner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("genei.tracking arm failed: %s", exc)
            conn.delete(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("genei.tracking redis unreachable: %s", exc)


def _tracking_runner() -> None:
    """Entrada RQ. El re-armado va en `finally`: un fallo no corta la cadena."""
    try:
        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            run_tracking_poll(session)
    except Exception:  # noqa: BLE001
        logger.exception("genei.tracking failed")
    finally:
        schedule_tracking_poll()


def arm() -> None:
    """Llamado una vez en el arranque del API."""
    try:
        schedule_tracking_poll()
    except Exception as exc:  # noqa: BLE001
        logger.warning("genei.tracking arm failed: %s", exc)


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import get_engine

    with Session(get_engine()) as _s:
        print(run_tracking_poll(_s, force=True))
