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


def emit_invoice_job(
    order_id: str, actor_user_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emite la factura FACTUSOL del pedido. Corre en `factusol:writes`
    (worker serializado). Un fallo se propaga → RQ marca el job failed.

    `options` llega como dict serializable (desde el modal de emisión) y se
    reconstruye a `FacturaOptions`."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.mapper import FacturaOptions  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    opts = FacturaOptions(**options) if options else None
    with Session(get_engine()) as session:
        actor = session.get(User, actor_user_id) if actor_user_id else None
        client = FactusolClient.from_settings()
        result = emit_invoice(session, order_id, client, actor=actor, options=opts)
    logger.info("factusol: factura emitida order=%s codfac=%s",
                order_id, result.get("codfac"))
    return result


def enqueue_emit_invoice(
    order_id: str, actor_user_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    """Encola `emit_invoice_job` en `factusol:writes` y devuelve el job_id."""
    return _enqueue(
        "app.integrations.factusol.jobs.emit_invoice_job",
        order_id, actor_user_id, options,
    )


def _enqueue(func_path: str, *args: Any) -> str:
    """Encola en `factusol:writes` y devuelve el job_id. Un solo sitio donde
    se abre Redis para todas las escrituras FACTUSOL."""
    from redis import Redis  # noqa: PLC0415
    from rq import Queue  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    conn = Redis.from_url(get_settings().redis_url)
    job = Queue(FACTUSOL_QUEUE_WRITES, connection=conn).enqueue(
        func_path, *args,
        job_timeout=JOB_TIMEOUT_SECONDS,
        result_ttl=RESULT_TTL_SECONDS,
    )
    return job.id


# --- proformas (Fase C · C-4) ------------------------------------------------
#
# Las tres van a la MISMA cola serializada que la emisión de facturas. Crear y
# duplicar calculan el CODPRE con un `MAX+1` justo antes de escribir, así que
# necesitan el worker de concurrency=1 igual que el CODFAC. Convertir no
# escribe en FACTUSOL, pero comparte cola para que las tres acciones tengan el
# mismo contrato de polling (202 + job_id) en el frontend.


def create_quote_job(
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, fecha: str | None = None,
    fopfac: str | None = None,
) -> dict[str, Any]:
    """Crea la proforma en F_PRE y cachea su desglose."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.quotes import create_quote  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        result = create_quote(
            client, session, ejercicio=ejercicio_for(session),
            customer=customer, lines=lines, referencia=referencia,
            fecha=fecha, fopfac=fopfac,
        )
    logger.info("factusol: proforma creada codpre=%s", result.get("codpre"))
    return result


def update_quote_job(
    codpre: str, customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, force: bool = False,
) -> dict[str, Any]:
    """Reescribe cabecera + líneas de una proforma existente."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.quotes import update_quote  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        result = update_quote(
            client, codpre, ejercicio=ejercicio_for(session),
            customer=customer, lines=lines, referencia=referencia, force=force,
        )
    logger.info("factusol: proforma %s actualizada", codpre)
    return result


def duplicate_quote_job(codpre: str, fecha: str | None = None) -> dict[str, Any]:
    """Duplica una proforma existente con CODPRE nuevo y fecha de hoy."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.quotes import duplicate_quote  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        result = duplicate_quote(
            client, session, codpre, ejercicio=ejercicio_for(session), fecha=fecha,
        )
    logger.info("factusol: proforma %s duplicada → %s", codpre, result.get("codpre"))
    return result


def convert_quote_to_order_job(
    codpre: str, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Crea el pedido manual del CRM a partir de la proforma."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.quotes import convert_quote_to_order  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        result = convert_quote_to_order(
            client, session, codpre, ejercicio=ejercicio_for(session),
            actor_user_id=actor_user_id,
        )
    logger.info("factusol: proforma %s → pedido %s",
                codpre, result.get("order_number"))
    return result


def enqueue_create_quote(
    customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, fecha: str | None = None,
    fopfac: str | None = None,
) -> str:
    return _enqueue(
        "app.integrations.factusol.jobs.create_quote_job",
        customer, lines, referencia, fecha, fopfac,
    )


def enqueue_update_quote(
    codpre: str, customer: dict[str, Any], lines: list[dict[str, Any]],
    referencia: str | None = None, force: bool = False,
) -> str:
    return _enqueue(
        "app.integrations.factusol.jobs.update_quote_job",
        codpre, customer, lines, referencia, force,
    )


def enqueue_duplicate_quote(codpre: str, fecha: str | None = None) -> str:
    return _enqueue(
        "app.integrations.factusol.jobs.duplicate_quote_job", codpre, fecha,
    )


def enqueue_convert_quote_to_order(
    codpre: str, actor_user_id: str | None = None,
) -> str:
    return _enqueue(
        "app.integrations.factusol.jobs.convert_quote_to_order_job",
        codpre, actor_user_id,
    )
