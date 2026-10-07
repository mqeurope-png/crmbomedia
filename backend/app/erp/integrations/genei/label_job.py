"""Genei — la ETIQUETA se trae sola al tramitarse el envío (y el arreglo de los
pedidos que se quedaron «sin enviar»).

Caso (07/10/2026): FLUXLA-5849, envío Genei tramitado a las 11:37 con tracking y
aviso al cliente enviado, seguía `transport_status = not_shipped` porque la
etiqueta solo se traía (y el transporte solo se movía) cuando alguien pulsaba
«🖨 Imprimir etiqueta». Ahora:

- El transporte pasa a «etiqueta creada» al tramitarse, sin etiqueta
  (`tracking.transport_target`).
- La etiqueta se pide sola en cuanto BoHub ve el envío tramitado (webhook,
  «Actualizar estado», pagar o el sondeo) y queda adjunta en «Documentos de
  envío», igual que con el botón. Genei tarda unos segundos en generar el PDF:
  si aún no la tiene (400/404 → «label_not_ready») se reintenta a los 30 s,
  2 min, 10 min y 1 h, y después se rinde, dejando constancia.
- No duplica: si ya hay etiqueta adjunta (o `label_fetched_at`, la trajo una
  persona), no hace nada. Un webhook repetido no encola otra cadena.
- Todo queda en `packing_json.genei.label_auto` (estado, intentos, último
  intento, siguiente, último error, cuándo terminó) para auditarlo y para que
  la ficha y la Cola SAT lo enseñen.

Corre en `worker-sync` (cola `genei:shipments`, `--with-scheduler`), como el
sondeo del tracking. NUNCA paga ni crea envíos: solo descarga la etiqueta.

Arreglo de una pasada (`fix_stuck_transport`): los pedidos con envío Genei
tramitado y el transporte aún «sin enviar» pasan a «etiqueta creada». Corre
una vez al arrancar el API (marca en Ajustes ERP) y a mano:
`python -m app.erp.integrations.genei.label_job [--apply]` (sin `--apply`,
solo informa).
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.integrations.genei.service import (
    genei_state_of,
    now_iso,
    set_genei_state,
    shipment_code_of_order,
)
from app.erp.integrations.genei.status import LABEL_CREATED_BUCKETS, READY
from app.workers.queues import queue_name

logger = logging.getLogger(__name__)

LABEL_QUEUE = queue_name("genei", "shipments")
JOB_TIMEOUT_SECONDS = 300
#: Espera antes de cada reintento (tras el 1.er intento, que va al momento).
RETRY_DELAYS: tuple[int, ...] = (30, 120, 600, 3600)
MAX_ATTEMPTS = 1 + len(RETRY_DELAYS)
#: Una cadena «esperando» sin noticias en este tiempo se da por perdida (job
#: borrado de Redis, worker caído): se puede volver a encolar.
STALE_AFTER = timedelta(hours=2)

#: Estados de `label_auto.status`.
ESPERANDO = "esperando"     # cadena de intentos en marcha
ADJUNTA = "adjunta"         # etiqueta adjunta (por el automático o a mano)
AGOTADA = "agotada"         # se agotaron los reintentos: traerla a mano
SIN_COLA = "sin_cola"       # no se pudo encolar (Redis): se reintenta en el próximo aviso

#: Arreglo de una pasada: marca en el blob de Ajustes ERP.
FIX_DONE_KEY = "genei_transport_fix"
FIX_ARM_KEY = "genei:transport_fix:armed"
FIX_ARM_TTL_SECONDS = 6 * 3600


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def label_auto_of(order: Any) -> dict[str, Any]:
    block = genei_state_of(order).get("label_auto")
    return dict(block) if isinstance(block, dict) else {}


def current_label(session: Session, order: Any) -> Any | None:
    """La etiqueta vigente adjunta al pedido PARA ESTE ENVÍO (la más reciente
    subida o traída desde que se creó el envío Genei), o None. Una de antes (de
    un envío borrado, o de otra agencia) no cuenta."""
    from app.erp.models.shipping import KIND_ETIQUETA, ShipmentFile  # noqa: PLC0415

    creado = _parse(genei_state_of(order).get("created_at"))
    for row in session.scalars(select(ShipmentFile).where(
        ShipmentFile.order_id == order.id, ShipmentFile.kind == KIND_ETIQUETA,
        ShipmentFile.replaced_at.is_(None),
    ).order_by(ShipmentFile.uploaded_at.desc())):
        subida = _aware(row.uploaded_at) if row.uploaded_at else None
        if creado is None or (subida is not None and subida >= creado):
            return row
    return None


def label_info(session: Session | None, order: Any) -> dict[str, Any]:
    """Para la ficha y la Cola SAT: ¿hay etiqueta adjunta (y desde cuándo)? y
    el estado de la descarga automática."""
    row = current_label(session, order) if session is not None else None
    return {
        "label_attached": row is not None,
        "label_attached_at": row.uploaded_at.isoformat() if row is not None and row.uploaded_at
        else None,
        "label_auto": label_auto_of(order) or None,
    }


# --- ¿toca pedirla? ------------------------------------------------------------------


def needs_auto_label(session: Session, order: Any, *, now: datetime | None = None) -> bool:
    """¿Hay que lanzar la descarga automática? Envío tramitado esperando al
    transportista (bucket READY), sin etiqueta adjunta, que nadie trajo a mano y
    sin otra cadena en marcha (o una perdida) ni ya terminada."""
    now = _aware(now or datetime.now(UTC))
    state = genei_state_of(order)
    if not state.get("shipment_code") or state.get("state_bucket") != READY:
        return False
    if state.get("label_fetched_at") or current_label(session, order) is not None:
        return False
    auto = label_auto_of(order)
    status = auto.get("status")
    if status in (ADJUNTA, AGOTADA):
        return False
    if status == ESPERANDO:
        visto = _parse(auto.get("next_attempt_at")) or _parse(auto.get("scheduled_at"))
        return visto is None or now - visto > STALE_AFTER
    return True


def _enqueue(order_id: str, attempt: int, delay_seconds: int) -> None:
    from rq import Queue  # noqa: PLC0415

    from app.workers.queues import redis_connection  # noqa: PLC0415

    queue = Queue(LABEL_QUEUE, connection=redis_connection(),
                  default_timeout=JOB_TIMEOUT_SECONDS)
    if delay_seconds <= 0:
        queue.enqueue(fetch_label_job, order_id, attempt)
    else:
        queue.enqueue_in(timedelta(seconds=delay_seconds), fetch_label_job, order_id, attempt)


def maybe_schedule_auto_label(
    session: Session, order: Any, *,
    enqueue: Callable[[str, int, int], None] | None = None,
    now: datetime | None = None,
) -> bool:
    """Si toca, apunta la cadena en el pedido (commit) y encola el 1.er intento
    al momento. Devuelve si se encoló. NUNCA rompe a quien lo llama (webhook,
    «Actualizar estado», pagar, sondeo)."""
    try:
        if not needs_auto_label(session, order, now=now):
            return False
        ahora = now_iso() if now is None else _aware(now).isoformat()
        set_genei_state(order, {"label_auto": {
            "status": ESPERANDO, "attempts": 0, "scheduled_at": ahora,
            "next_attempt_at": ahora, "max_attempts": MAX_ATTEMPTS,
        }})
        session.commit()
        try:
            (enqueue or _enqueue)(order.id, 1, 0)
        except Exception as exc:  # noqa: BLE001 — sin Redis: el próximo aviso lo reintenta
            logger.warning("genei etiqueta: no se pudo encolar (pedido %s): %s", order.id, exc)
            auto = label_auto_of(order)
            auto.update(status=SIN_COLA, last_error="No se pudo encolar la descarga.")
            set_genei_state(order, {"label_auto": auto})
            session.commit()
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.warning("genei etiqueta: no se pudo programar (pedido %s)",
                       getattr(order, "id", "?"), exc_info=True)
        session.rollback()
        return False


# --- un intento ----------------------------------------------------------------------


def _client_for(session: Session) -> Any | None:
    from app.erp.api.genei import build_client, get_genei_carrier  # noqa: PLC0415

    carrier = get_genei_carrier(session)
    if carrier is None or not carrier.api_credentials_encrypted:
        return None
    return build_client(carrier)


def _releer(session: Session, order: Any) -> Any:
    """Relee el pedido con cerrojo de fila justo antes de guardar: mientras se
    esperaba a Genei, un webhook o el sondeo pudieron cambiar el bloque genei
    (p. ej. el aviso al cliente): no se pisa con la copia de antes."""
    session.refresh(order, with_for_update=True)
    return order


def _ya_adjunta(session: Session, order: Any) -> bool:
    return bool(genei_state_of(order).get("label_fetched_at")) or \
        current_label(session, order) is not None


def auto_fetch_label(
    session: Session, order_id: str, *, attempt: int = 1, client: Any = None,
    schedule: Callable[[str, int, int], None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Un intento de traer la etiqueta de Genei y adjuntarla. Si Genei aún no la
    tiene (o falla), programa el siguiente intento; al último, se rinde."""
    from app.erp.api.genei import genei_user_message  # noqa: PLC0415
    from app.erp.api.shipping import _store_new_file, _transition_on_etiqueta  # noqa: PLC0415
    from app.erp.integrations.genei.client import GeneiError  # noqa: PLC0415
    from app.erp.models import Order  # noqa: PLC0415
    from app.erp.models.shipping import KIND_ETIQUETA, SOURCE_GENEI_API  # noqa: PLC0415
    from app.models.crm import AuditLog  # noqa: PLC0415

    ahora = _aware(now or datetime.now(UTC))
    order = session.get(Order, order_id)
    if order is None:
        return {"result": "sin_pedido"}
    code = shipment_code_of_order(order)
    if not code:
        return {"result": "sin_envio"}
    if _ya_adjunta(session, order):
        return _marcar_ya_estaba(session, order, ahora)

    error: str | None = None
    label = None
    try:
        client = client or _client_for(session)
        if client is None:
            error = "Genei no está configurado."
        else:
            label = client.get_label(code)
    except GeneiError as exc:
        error = ("Genei aún no tiene la etiqueta." if exc.status in (400, 404)
                 else genei_user_message(exc))
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:200]}"

    # Se guarda sobre el pedido RELEÍDO (con cerrojo): lo que cambió mientras se
    # esperaba a Genei se conserva. Y si alguien trajo la etiqueta entretanto,
    # no se adjunta otra.
    order = _releer(session, order)
    if _ya_adjunta(session, order):
        return _marcar_ya_estaba(session, order, ahora)
    auto = label_auto_of(order)
    auto.update(status=ESPERANDO, attempts=attempt, last_attempt_at=ahora.isoformat(),
                max_attempts=MAX_ATTEMPTS)
    if label is not None:
        try:
            row = _store_new_file(
                session, order, kind=KIND_ETIQUETA, source=SOURCE_GENEI_API,
                filename=label.filename, mime_type=label.mime_type,
                data=label.content, actor_id=None,
            )
            session.flush()
            # Idempotente: si el transporte ya pasó a «etiqueta creada» (al
            # tramitarse), no se mueve otra vez.
            applied, _reason = _transition_on_etiqueta(session, order, row, None)
            auto.update(status=ADJUNTA, done_at=ahora.isoformat(), via="automatica",
                        file_id=row.id, last_error=None, next_attempt_at=None)
            set_genei_state(order, {"label_fetched_at": ahora.isoformat(), "label_auto": auto})
            session.add(AuditLog(
                actor_user_id=None, actor_email=None, action="erp.genei.label_fetched_auto",
                target_type="order", target_id=order.id,
                metadata_json=json.dumps({"shipment_code": code, "filename": label.filename,
                                          "attempt": attempt}),
            ))
            session.commit()
            logger.info("genei etiqueta: %s adjuntada sola (intento %d)", order.order_number,
                        attempt)
            return {"result": "adjunta", "file_id": row.id, "transition_applied": applied}
        except Exception as exc:  # noqa: BLE001 — p. ej. el almacén de ficheros
            session.rollback()
            order = _releer(session, session.get(Order, order_id))
            auto = label_auto_of(order) | {"status": ESPERANDO, "attempts": attempt,
                                           "last_attempt_at": ahora.isoformat(),
                                           "max_attempts": MAX_ATTEMPTS}
            error = f"No se pudo guardar la etiqueta: {str(exc)[:200]}"

    auto["last_error"] = error
    if attempt < MAX_ATTEMPTS:
        delay = RETRY_DELAYS[attempt - 1]
        auto.update(status=ESPERANDO,
                    next_attempt_at=(ahora + timedelta(seconds=delay)).isoformat())
        set_genei_state(order, {"label_auto": auto})
        session.commit()
        try:
            (schedule or _enqueue)(order.id, attempt + 1, delay)
        except Exception as exc:  # noqa: BLE001
            logger.warning("genei etiqueta: no se pudo encolar el reintento: %s", exc)
            auto.update(status=SIN_COLA)
            set_genei_state(order, {"label_auto": auto})
            session.commit()
            return {"result": "sin_cola", "error": error}
        return {"result": "reintento", "next_in": delay, "error": error}
    auto.update(status=AGOTADA, gave_up_at=ahora.isoformat(), next_attempt_at=None)
    set_genei_state(order, {"label_auto": auto})
    session.commit()
    logger.warning("genei etiqueta: %s sin etiqueta tras %d intentos (%s): hay que traerla "
                   "a mano", order.order_number, attempt, error)
    return {"result": "agotada", "error": error}


def _marcar_ya_estaba(session: Session, order: Any, ahora: datetime) -> dict[str, Any]:
    """Alguien la trajo (o la subió) antes: no se pide otra; se apunta."""
    order = _releer(session, order)
    auto = label_auto_of(order)
    auto.update(status=ADJUNTA, done_at=auto.get("done_at") or ahora.isoformat(),
                via=auto.get("via") or "manual", next_attempt_at=None)
    set_genei_state(order, {"label_auto": auto})
    session.commit()
    return {"result": "ya_estaba"}


def fetch_label_job(order_id: str, attempt: int = 1) -> dict[str, Any]:
    """Entrada RQ de un intento."""
    from app.db.session import get_engine  # noqa: PLC0415

    with Session(get_engine()) as session:
        return auto_fetch_label(session, order_id, attempt=attempt)


# --- arreglo de una pasada -------------------------------------------------------------


def fix_stuck_transport(session: Session, *, dry_run: bool = True) -> dict[str, Any]:
    """Pedidos con envío Genei tramitado (sin incidencia) y el transporte aún
    «sin enviar» → «etiqueta creada», como hará ya el webhook. Los que esperan
    al transportista y no tienen etiqueta, además, la piden sola. Devuelve lo
    que cambia (o cambiaría, con `dry_run`)."""
    from app.erp.integrations.genei.webhook import advance_transport, tramitado_at  # noqa: PLC0415
    from app.erp.models import Order  # noqa: PLC0415
    from app.erp.models.orders import TransportStatus  # noqa: PLC0415

    candidatos = session.scalars(select(Order).where(
        Order.packing_json.like('%"shipment_code"%'),
        Order.transport_status == TransportStatus.NOT_SHIPPED,
        Order.cancelled_at.is_(None),
    )).all()
    movidos: list[str] = []
    sin_mover: list[dict[str, str]] = []
    for order in candidatos:
        state = genei_state_of(order)
        if not state.get("shipment_code") or state.get("state_bucket") not in LABEL_CREATED_BUCKETS:
            continue
        prep = getattr(order.preparation_status, "value", order.preparation_status)
        if prep != "packed":
            # La máquina de estados exige embalado: no se fuerza (ni se cuenta).
            sin_mover.append({"order_number": order.order_number,
                              "motivo": f"no está embalado (preparación {prep})"})
            continue
        if dry_run:
            movidos.append(order.order_number)
            continue
        # Fechado cuando se tramitó (no hoy): la hoja usa esa fecha como
        # «Fecha recogido» mientras nadie marque recogido.
        if advance_transport(session, order, "label_created", evidence={
            "tracking_number": state.get("tracking") or order.tracking_number or "",
            "description": "Arreglo: envío Genei tramitado que seguía «sin enviar»",
        }, label_at=tramitado_at(state)):
            movidos.append(order.order_number)
        else:
            sin_mover.append({"order_number": order.order_number,
                              "motivo": "la transición no se pudo aplicar"})
    if not dry_run:
        session.commit()
        for order in candidatos:
            maybe_schedule_auto_label(session, order)
    return {"preview": dry_run, "movidos": movidos, "sin_mover": sin_mover}


def run_fix_once() -> dict[str, Any]:
    """Entrada RQ del arreglo al desplegar: una sola vez (marca en Ajustes ERP)."""
    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.models import ErpSettings  # noqa: PLC0415
    from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    with Session(get_engine()) as session:
        hecho = series_config(session).get(FIX_DONE_KEY)
        if isinstance(hecho, dict) and hecho.get("done_at"):
            return {"skipped": True, "done_at": hecho["done_at"]}
        result = fix_stuck_transport(session, dry_run=False)
        logger.info("genei arreglo: %d pedidos pasan a «etiqueta creada» (%s)%s",
                    len(result["movidos"]), ", ".join(result["movidos"][:30]) or "ninguno",
                    f"; {len(result['sin_mover'])} no se pudieron mover" if result["sin_mover"]
                    else "")
        cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
        if cfg is None:
            cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
            session.add(cfg)
        try:
            blob = json.loads(cfg.factusol_series_json or "{}")
        except (TypeError, ValueError):
            blob = {}
        blob = blob if isinstance(blob, dict) else {}
        blob[FIX_DONE_KEY] = {"done_at": now_iso(), "movidos": result["movidos"][:100],
                              "sin_mover": result["sin_mover"][:100]}
        cfg.factusol_series_json = json.dumps(blob, ensure_ascii=False)
        session.commit()
        return result


def arm() -> None:
    """Al arrancar el API: encola UNA vez el arreglo (SETNX; el job mira la
    marca de «hecho» y no repite)."""
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import redis_connection  # noqa: PLC0415

        conn = redis_connection()
        if not conn.set(FIX_ARM_KEY, "1", nx=True, ex=FIX_ARM_TTL_SECONDS):
            return
        Queue(LABEL_QUEUE, connection=conn).enqueue(
            run_fix_once, job_timeout=JOB_TIMEOUT_SECONDS, result_ttl=24 * 3600,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("genei arreglo arm failed: %s", exc)


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import get_engine

    aplicar = "--apply" in sys.argv[1:]
    with Session(get_engine()) as _s:
        print(json.dumps(fix_stuck_transport(_s, dry_run=not aplicar), ensure_ascii=False,
                         indent=2))
