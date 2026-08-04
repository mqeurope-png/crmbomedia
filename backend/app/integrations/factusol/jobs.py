"""Jobs RQ de FACTUSOL (Fase C PR C-2).

Toda escritura a FACTUSOL pasa por la cola `factusol:writes`, procesada por un
worker DEDICADO con concurrency=1 (`worker-factusol`). Serializar es
obligatorio: el CODFAC se calcula con `SELECT MAX+1` justo antes de escribir,
y dos emisiones en paralelo pisarían la numeración.

Sin retry automático: si un job falla a mitad de la escritura, la compensación
de `emit_invoice` intenta borrar lo escrito; si aun así queda basura, es
preferible que Bart lo vea en la bandeja y actúe a mano antes que reintentar a
ciegas sobre una factura potencialmente ya creada.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.service import emit_invoice

logger = logging.getLogger(__name__)

FACTUSOL_QUEUE_WRITES = "factusol:writes"
JOB_TIMEOUT_SECONDS = 120
RESULT_TTL_SECONDS = 86_400  # 1 día: el frontend consulta el resultado


def emit_invoice_job(order_id: str, actor_user_id: str | None = None) -> dict[str, Any]:
    """Emite la factura FACTUSOL del pedido. Corre en `factusol:writes`
    (worker serializado). Un fallo se propaga → RQ marca el job failed."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    with Session(get_engine()) as session:
        actor = session.get(User, actor_user_id) if actor_user_id else None
        client = FactusolClient.from_settings()
        result = emit_invoice(session, order_id, client, actor=actor)
    logger.info("factusol: factura emitida order=%s codfac=%s",
                order_id, result.get("codfac"))
    return result


def enqueue_emit_invoice(order_id: str, actor_user_id: str | None = None) -> str:
    """Encola `emit_invoice_job` en `factusol:writes` y devuelve el job_id."""
    from redis import Redis  # noqa: PLC0415
    from rq import Queue  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    conn = Redis.from_url(get_settings().redis_url)
    job = Queue(FACTUSOL_QUEUE_WRITES, connection=conn).enqueue(
        "app.integrations.factusol.jobs.emit_invoice_job",
        order_id, actor_user_id,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=RESULT_TTL_SECONDS,
    )
    return job.id
