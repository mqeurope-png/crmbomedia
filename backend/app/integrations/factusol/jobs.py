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

from app.integrations.factusol.client import FactusolClient, FactusolError
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
    reconstruye a `FacturaOptions`.

    ERP-E2-fix2: antes de propagar el error se **registra el fallo en la BD**
    (`record_emit_failure`). Sin eso el pedido se quedaba en «Generando…» para
    siempre: `emit_invoice` no llega a marcar nada y nadie deshace el estado,
    así que la ficha seguía esperando un job que ya había muerto."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.mapper import FacturaOptions  # noqa: PLC0415
    from app.integrations.factusol.service import record_emit_failure  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    # `from_payload` ignora claves obsoletas: un job encolado antes de ERP-E2
    # trae `serfac` (la serie como string), que ya no existe.
    opts = FacturaOptions.from_payload(options) if options else None
    try:
        with Session(get_engine()) as session:
            actor = session.get(User, actor_user_id) if actor_user_id else None
            client = FactusolClient.from_settings()
            result = emit_invoice(
                session, order_id, client, actor=actor, options=opts
            )
    except Exception as exc:  # noqa: BLE001 — se re-lanza tras registrarlo
        logger.warning(
            "factusol: emisión fallida order=%s", order_id, exc_info=True
        )
        try:
            with Session(get_engine()) as fail_session:
                record_emit_failure(
                    fail_session, order_id, str(exc), actor_user_id=actor_user_id
                )
        except Exception:  # noqa: BLE001 — el fallo original manda
            logger.warning(
                "factusol: no se pudo registrar el fallo de emisión order=%s",
                order_id, exc_info=True,
            )
        raise
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


# --- ERP-F3: marcar la factura como cobrada/pendiente (ESTFAC) ---------------


def mark_invoice_paid_job(
    serie: int, codigo: int, paid: bool,
    actor_user_id: str | None = None,
    current_estado: Any = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escribe `ESTFAC` de la factura (cobrada/pendiente) por clave COMPUESTA
    `(TIPFAC, CODFAC)`. Corre en `factusol:writes` (serial). Idempotente (no
    reescribe si ya estaba). Registra el cambio en el timeline del pedido/
    documento. Si la escritura falla, el resultado del job lo refleja
    (`marked: False`) y el frontend NO cambia su estado."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        ejercicio_for,
        mark_invoice_payment,
    )

    info = meta or {}
    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        marked, motivo = mark_invoice_payment(
            client, session, serie=serie, codigo=codigo, paid=paid,
            ejercicio=ejercicio, current_estado=current_estado,
        )
        if marked:
            _record_invoice_payment_event(
                session, serie=serie, codigo=codigo, paid=paid,
                actor_user_id=actor_user_id, meta=info,
            )
            session.commit()
    return {
        "marked": marked, "motivo": motivo,
        "serie": serie, "codigo": codigo, "paid": paid,
        "numero": info.get("numero") or f"{serie}-{int(codigo):06d}",
    }


def _record_invoice_payment_event(
    session: Any, *, serie: int, codigo: int, paid: bool,
    actor_user_id: str | None, meta: dict[str, Any],
) -> None:
    """Timeline «Factura X marcada como cobrada por …». Se cuelga del pedido si
    se localiza; si no, queda como evento de documento (siempre auditable)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factusol_pdf import _find_order_for_document  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    actor = session.get(User, actor_user_id) if actor_user_id else None
    numero = meta.get("numero") or f"{serie}-{int(codigo):06d}"
    estado_txt = "cobrada" if paid else "pendiente"
    quien = f" por {actor.full_name}" if actor is not None else ""
    order = _find_order_for_document(
        session, "facturas",
        {"codigo": codigo, "referencia": meta.get("referencia") or ""},
    )
    record_event(
        session,
        action="erp.invoice_payment_marked",
        target_type="order" if order is not None else "document",
        target_id=order.id if order is not None else numero,
        actor=actor,
        metadata={
            "numero": numero, "paid": paid,
            "cliente": meta.get("cliente"), "importe": meta.get("importe"),
        },
        message=f"Factura {numero} marcada como {estado_txt}{quien}",
    )


def enqueue_mark_invoice_paid(
    serie: int, codigo: int, paid: bool,
    actor_user_id: str | None = None,
    current_estado: Any = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Encola `mark_invoice_paid_job` en `factusol:writes`; devuelve el job_id."""
    return _enqueue(
        "app.integrations.factusol.jobs.mark_invoice_paid_job",
        serie, codigo, paid, actor_user_id, current_estado, meta,
    )


# --- ERP-F4-B: registrar un COBRO (F_LCO + ESTFAC) ----------------------------


def register_invoice_collection_job(
    serie: int, codigo: int, contrapartida: str, fecha: str,
    importe: float | None = None, forma: str | None = None,
    observaciones: str | None = None,
    actor_user_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inserta la línea de cobro en `F_LCO` y marca `ESTFAC=2`. Corre en
    `factusol:writes` (serial: el LINLCO = max+1 necesita concurrency=1).
    Idempotente (ya cobrada → `already`, sin escribir). El resultado refleja
    lo que pasó de verdad (`registered`, `estfac_marked`, `motivo`)."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.collections_write import (  # noqa: PLC0415
        register_invoice_collection,
    )
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    info = meta or {}
    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        result = register_invoice_collection(
            client, session, serie=serie, codigo=codigo,
            contrapartida=contrapartida, fecha=fecha, importe=importe,
            forma=forma, observaciones=observaciones, ejercicio=ejercicio,
        )
        if result.get("registered"):
            _record_invoice_collection_event(
                session, serie=serie, codigo=codigo, result=result,
                actor_user_id=actor_user_id, meta=info,
            )
            session.commit()
    result.setdefault("numero", info.get("numero") or f"{serie}-{int(codigo):06d}")
    return result


def _record_invoice_collection_event(
    session: Any, *, serie: int, codigo: int, result: dict[str, Any],
    actor_user_id: str | None, meta: dict[str, Any],
) -> None:
    """Timeline «Cobro de X € registrado en FACTUSOL para la factura N»."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factusol_pdf import _find_order_for_document  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    actor = session.get(User, actor_user_id) if actor_user_id else None
    numero = result.get("numero") or f"{serie}-{int(codigo):06d}"
    quien = f" por {actor.full_name}" if actor is not None else ""
    order = _find_order_for_document(
        session, "facturas",
        {"codigo": codigo, "referencia": meta.get("referencia") or ""},
    )
    record_event(
        session,
        action="erp.invoice_collection_registered",
        target_type="order" if order is not None else "document",
        target_id=order.id if order is not None else numero,
        actor=actor,
        metadata={
            "numero": numero, "importe": result.get("importe"),
            "contrapartida": result.get("contrapartida"),
            "fecha": result.get("fecha"), "linlco": result.get("linlco"),
            "estfac_marked": result.get("estfac_marked"),
        },
        message=(
            f"Cobro de {result.get('importe')} € registrado en FACTUSOL para "
            f"la factura {numero}{quien}"
        ),
    )


def enqueue_register_invoice_collection(
    serie: int, codigo: int, contrapartida: str, fecha: str,
    importe: float | None = None, forma: str | None = None,
    observaciones: str | None = None,
    actor_user_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Encola `register_invoice_collection_job` en `factusol:writes`."""
    return _enqueue(
        "app.integrations.factusol.jobs.register_invoice_collection_job",
        serie, codigo, contrapartida, fecha, importe, forma, observaciones,
        actor_user_id, meta,
    )


# --- cadena de documentos (ERP-E3-B) -----------------------------------------


def create_document_job(
    source_type: str, target_type: str, tip: int, cod: int,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crea el documento DESTINO (albarán/factura) desde el ORIGEN, enlazado
    por `DOC/DTP/DCO` en las líneas. Corre en `factusol:writes` (serial): el
    contador MAX+1 y el re-chequeo anti-duplicado necesitan concurrency=1.

    `options`: `{"ejercicio", "serie", "fecha", "force"}` — el ejercicio lo
    fija el endpoint (el mismo contra el que pre-chequeó duplicados);
    serie/fecha son los overrides del modal; `force` salta el anti-duplicado
    (el operador ya vio el aviso).

    Como en la emisión (E2-fix2), el fallo se registra en SyncLog ANTES de
    propagarse: el frontend ve el job failed por polling, y Bart ve el motivo
    en la bandeja de sincronización aunque nadie estuviera mirando."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.chain import (  # noqa: PLC0415
        convert_document,
        log_chain_sync,
    )
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    opts = options or {}
    try:
        with Session(get_engine()) as session:
            ejercicio = opts.get("ejercicio") or ejercicio_for(session)
            client = FactusolClient.from_settings()
            result = convert_document(
                session, client,
                source_type=source_type, target_type=target_type,
                tip=int(tip), cod=int(cod), ejercicio=ejercicio,
                serie_override=opts.get("serie"),
                fecha=opts.get("fecha"),
                force=bool(opts.get("force")),
            )
    except Exception as exc:  # noqa: BLE001 — se re-lanza tras registrarlo
        logger.warning(
            "factusol: conversión fallida %s %s-%s → %s",
            source_type, tip, cod, target_type, exc_info=True,
        )
        try:
            with Session(get_engine()) as fail_session:
                log_chain_sync(
                    fail_session, success=False,
                    message=(
                        f"{source_type} {tip}-{cod} → {target_type}: {exc}"
                    ),
                )
                fail_session.commit()
        except Exception:  # noqa: BLE001 — el fallo original manda
            logger.warning(
                "factusol: no se pudo registrar el fallo de conversión "
                "%s %s-%s", source_type, tip, cod, exc_info=True,
            )
        raise
    logger.info(
        "factusol: %s %s creado desde %s %s-%s",
        target_type, result.get("numero"), source_type, tip, cod,
    )
    return result


def enqueue_create_document(
    source_type: str, target_type: str, tip: int, cod: int,
    options: dict[str, Any] | None = None,
) -> str:
    """Encola `create_document_job` en `factusol:writes`; devuelve el job_id."""
    return _enqueue(
        "app.integrations.factusol.jobs.create_document_job",
        source_type, target_type, tip, cod, options,
    )


# --- Fase 2: albarán FACTUSOL del pedido creado desde un documento ----------


def create_order_albaran_job(
    order_id: str, actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Crea (o vincula) el albarán FACTUSOL del pedido de BoHub. Corre en
    `factusol:writes` (serial: contador MAX+1 + guard de esquema). Idempotente
    (`already` / `linked` / `created`). Un fallo (guard de esquema, FACTUSOL
    caído, pedido web) queda en el historial del pedido ANTES de propagarse:
    la ficha lo enseña y ofrece reintentar."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.factusol_albaran import (  # noqa: PLC0415
        create_albaran_for_order,
        record_albaran_failure,
    )
    from app.erp.models import Order  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    try:
        with Session(get_engine()) as session:
            order = session.get(Order, order_id)
            if order is None:
                raise FactusolError(f"Order {order_id!r} no existe")
            client = FactusolClient.from_settings()
            result = create_albaran_for_order(
                session, client, order, ejercicio=ejercicio_for(session),
                actor_user_id=actor_user_id,
            )
    except Exception as exc:  # noqa: BLE001 — se re-lanza tras registrarlo
        logger.warning("factusol: albarán fallido order=%s", order_id, exc_info=True)
        try:
            with Session(get_engine()) as fail_session:
                failed = fail_session.get(Order, order_id)
                if failed is not None:
                    record_albaran_failure(
                        fail_session, failed, str(exc), actor_user_id=actor_user_id,
                    )
                    fail_session.commit()
        except Exception:  # noqa: BLE001 — el fallo original manda
            logger.warning("factusol: no se pudo registrar el fallo del albarán "
                           "order=%s", order_id, exc_info=True)
        raise
    logger.info("factusol: albarán %s order=%s (%s)",
                result.get("numero"), order_id, result.get("status"))
    return result


def enqueue_create_order_albaran(
    order_id: str, actor_user_id: str | None = None,
) -> str:
    """Encola `create_order_albaran_job` en `factusol:writes`; devuelve el job_id."""
    return _enqueue(
        "app.integrations.factusol.jobs.create_order_albaran_job",
        order_id, actor_user_id,
    )


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
    payment: dict[str, Any] | None = None, create_albaran: bool = True,
) -> dict[str, Any]:
    """Crea el pedido de BoHub a partir de la proforma y, Fase 2, en el MISMO
    job del worker serial: apunta el pago (opción B, ya resuelto por el
    endpoint) y crea el albarán en FACTUSOL (idempotente; un fallo del
    albarán viaja en el resultado, no tumba el job — el pedido existe)."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.factusol_albaran import apply_conversion_extras  # noqa: PLC0415
    from app.integrations.factusol.quotes import convert_quote_to_order  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        result = convert_quote_to_order(
            client, session, codpre, ejercicio=ejercicio,
            actor_user_id=actor_user_id,
        )
        result.update(apply_conversion_extras(
            session, client, order_id=result["order_id"], payment=payment,
            create_albaran=create_albaran, ejercicio=ejercicio,
            actor_user_id=actor_user_id,
        ))
    logger.info("factusol: proforma %s → pedido %s (albarán: %s)",
                codpre, result.get("order_number"),
                (result.get("albaran") or {}).get("numero")
                or result.get("albaran_error") or result.get("albaran_skipped"))
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
    payment: dict[str, Any] | None = None, create_albaran: bool = True,
) -> str:
    return _enqueue(
        "app.integrations.factusol.jobs.convert_quote_to_order_job",
        codpre, actor_user_id, payment, create_albaran,
    )


# --- Reconciliación FACTUSOL → pedidos (vincular facturas manuales) ----------
#: Cola INTERACTIVA (la consume `worker-factusol` con prioridad, igual que la
#: reconciliación de estados Woo). NO va por `worker-sync` (saturado de batch).
ERP_INTERACTIVE_QUEUE = "erp:interactive"
INVOICE_RECONCILE_TIMEOUT = 300
INVOICE_RECONCILE_RESULT_TTL = 3600


def run_factusol_invoice_reconcile(dry_run: bool = True) -> dict[str, Any]:
    """Job: enlaza a los pedidos las facturas que ya existen en FACTUSOL (las
    creadas a mano incluidas), por REFFAC. Abre su propia sesión y delega en el
    núcleo. Devuelve el resumen (lo recoge RQ como `job.result`)."""
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.invoice_reconcile import (  # noqa: PLC0415
        reconcile_factusol_invoices,
    )
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        return reconcile_factusol_invoices(session, client, ejercicio, dry_run=dry_run)


def enqueue_factusol_invoice_reconcile(dry_run: bool = True) -> str:
    """Encola `run_factusol_invoice_reconcile` en `erp:interactive`
    (worker-factusol) y devuelve el job_id. La API responde al instante; el
    frontend hace polling del estado."""
    from redis import Redis  # noqa: PLC0415
    from rq import Queue  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    conn = Redis.from_url(get_settings().redis_url)
    job = Queue(ERP_INTERACTIVE_QUEUE, connection=conn).enqueue(
        run_factusol_invoice_reconcile, dry_run,
        job_timeout=INVOICE_RECONCILE_TIMEOUT, result_ttl=INVOICE_RECONCILE_RESULT_TTL,
    )
    return job.id
