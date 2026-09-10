"""ERP-F6 — API del seguimiento de pedidos (la vista que sustituye el Excel).

La vista y la exportación funcionan SIEMPRE, con o sin Drive configurado; el
sincronizado con la hoja es un extra que avisa si falta configuración.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp import seguimiento as core
from app.erp.api.deps import require_erp_edit, require_erp_view
from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings, Order
from app.models.crm import User

router = APIRouter(prefix="/api/erp/seguimiento", tags=["erp-seguimiento"])


def _rows(session: Session) -> list[dict[str, Any]]:
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.api.orders import customer_names  # noqa: PLC0415

    orders = list(session.scalars(select(Order)))
    return core.build_rows(session, customer_names=customer_names(session, orders))


def _filtered(
    session: Session,
    *,
    serie: int | None,
    vendedor: str | None,
    transportista: str | None,
    origen: str | None,
    desde: date | None,
    hasta: date | None,
    estado: str | None,
    q: str | None,
    en_curso: bool,
    ver_excluidos: bool = False,
    ver_ocultos_estado: bool = False,
    pendiente_escribir: bool | None = None,
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    return core.filter_rows(
        _rows(session),
        serie=serie, vendedor=vendedor, transportista=transportista,
        origen=origen, desde=desde, hasta=hasta, estado=estado, q=q,
        en_curso=en_curso, ver_excluidos=ver_excluidos,
        ver_ocultos_estado=ver_ocultos_estado,
        pendiente_escribir=pendiente_escribir, sort=sort, direction=direction,
    )


@router.get("")
def list_seguimiento(
    serie: int | None = Query(default=None, ge=1, le=9),
    vendedor: str | None = Query(default=None),
    transportista: str | None = Query(default=None),
    origen: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None, pattern="^(pendiente|enviado|facturado|completado)$"),
    q: str | None = Query(default=None, max_length=120),
    en_curso: bool = Query(default=True),
    # ERP-F6-fix7 — ver SOLO los excluidos (para revisarlos/reincluirlos), o
    # solo los que faltan por escribir en la hoja de Drive.
    ver_excluidos: bool = Query(default=False),
    # ERP-Woo — ver SOLO los ocultados por estado (cancelado/reembolsado/fallido).
    ver_ocultos_estado: bool = Query(default=False),
    pendiente_escribir: bool = Query(default=False),
    sort: str = Query(default="fecha"),
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),  # noqa: A002
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Vista de seguimiento. Por defecto: pedidos EN CURSO (la parte de
    arriba del Excel, lo que Bart mira a diario). Los excluidos quedan fuera
    salvo con `ver_excluidos`."""
    _ = current_user
    rows = _filtered(
        session, serie=serie, vendedor=vendedor, transportista=transportista,
        origen=origen, desde=desde, hasta=hasta, estado=estado, q=q,
        en_curso=en_curso, ver_excluidos=ver_excluidos,
        ver_ocultos_estado=ver_ocultos_estado,
        pendiente_escribir=pendiente_escribir or None, sort=sort, direction=dir,
    )
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    from app.erp.drive_sheets import service_account_email  # noqa: PLC0415

    return {
        "items": rows[offset:offset + limit],
        "total": len(rows),
        "columns": core.SEGUIMIENTO_COLUMNS,
        "drive": {
            "configured": bool(
                cfg and cfg.drive_service_account_json_encrypted
                and cfg.drive_spreadsheet_id
            ),
            "service_account_email": service_account_email(cfg),
            "spreadsheet_id": cfg.drive_spreadsheet_id if cfg else None,
        },
    }


@router.get("/export")
def export_seguimiento(
    serie: int | None = Query(default=None, ge=1, le=9),
    vendedor: str | None = Query(default=None),
    transportista: str | None = Query(default=None),
    origen: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None, pattern="^(pendiente|enviado|facturado|completado)$"),
    q: str | None = Query(default=None, max_length=120),
    en_curso: bool = Query(default=True),
    sort: str = Query(default="fecha"),
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),  # noqa: A002
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    """Descarga .xlsx con las MISMAS columnas del Excel de Bart, respetando
    los filtros aplicados. No necesita Drive."""
    _ = current_user
    rows = _filtered(
        session, serie=serie, vendedor=vendedor, transportista=transportista,
        origen=origen, desde=desde, hasta=hasta, estado=estado, q=q,
        en_curso=en_curso, sort=sort, direction=dir,
    )
    content = core.export_xlsx(rows)
    filename = f"seguimiento_pedidos_{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _factusol_invoice_serie_resolver(session: Session):
    """ERP-F6-fix5 — resolutor perezoso `CODFAC → serie` contra FACTUSOL para
    las facturas escritas en la hoja como número desnudo. Carga F_FAC UNA sola
    vez (con `1=1`, gotcha nº1) y solo si de verdad se necesita; si el mismo
    CODFAC vive en varias series, devuelve None (ambiguo, no tocar)."""
    import logging  # noqa: PLC0415

    index: dict[str, set[int]] = {}
    state = {"loaded": False}
    log = logging.getLogger(__name__)

    def resolve(codfac: str) -> int | None:
        if not state["loaded"]:
            state["loaded"] = True
            try:
                from app.erp.api.factusol import _client_and_ejercicio  # noqa: PLC0415
                from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

                client, ejercicio = _client_and_ejercicio(session)
                for r in client.load_table("F_FAC", filtro="1=1", ejercicio=ejercicio):
                    try:
                        cod = str(int(float(str(r.get("CODFAC")).strip())))
                    except (TypeError, ValueError):
                        continue
                    serie = coerce_serie(r.get("TIPFAC"))
                    if serie is not None:
                        index.setdefault(cod, set()).add(serie)
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warning("F6-fix5: no se pudo cargar F_FAC para resolver series: %s", exc)
        try:
            key = str(int(float(str(codfac).strip())))
        except (TypeError, ValueError):
            return None
        series = index.get(key)
        return next(iter(series)) if series and len(series) == 1 else None

    return resolve


@router.post("/drive-sync")
def drive_sync(
    dry_run: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Actualizar hoja de Drive»: sincronización MANUAL. ERP-F6-fix7 — SOLO
    AÑADE: identifica cada pedido por su número desnudo (ERP-F6-fix2) para no
    duplicar; el que ya está en la hoja no se toca. Sin borrar filas ajenas ni
    pisar celdas manuales. `dry_run=true` PREVISUALIZA (cuántas añade / a
    revisar / info) sin escribir nada — para que Bart lo vea antes de confirmar."""
    _ = current_user
    from app.erp.drive_sheets import (  # noqa: PLC0415
        DriveConfigError,
        DriveSyncError,
        GoogleSheetsClient,
        drive_config,
        sync_to_sheet,
    )
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    try:
        conf = drive_config(cfg)
    except DriveConfigError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "drive_not_configured", "detail": str(exc)},
        ) from exc
    if conf is None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "drive_not_configured",
            "detail": (
                "Falta configurar la cuenta de servicio de Google y el ID de la "
                "hoja en Configuración ERP. La vista funciona igualmente."
            ),
        })
    info, spreadsheet_id = conf
    prefer_albaran = bool(series_config(session).get("drive_reference_prefer_albaran", True))
    rows = build_drive_sync_rows(session)
    try:
        return sync_to_sheet(
            session, GoogleSheetsClient(info, spreadsheet_id), rows,
            prefer_albaran=prefer_albaran, dry_run=dry_run,
            invoice_serie_resolver=_factusol_invoice_serie_resolver(session),
        )
    except DriveSyncError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"code": "drive_sync_failed", "detail": str(exc)[:300]},
        ) from exc


def build_drive_sync_rows(session: Session) -> list[dict[str, Any]]:
    """Pedidos candidatos a escribirse en la hoja: los EN CURSO, excluidos
    fuera (ERP-F6-fix7). El emparejamiento evita duplicar los que ya están; los
    excluidos no se listan ni se insertan. Reutilizado por el endpoint y por el
    script de medición `scripts.erp_f6_verify_sheet_format`."""
    return core.filter_rows(
        _rows(session), en_curso=True, sort="fecha", direction="asc",
    )


#: Motivos rápidos del botón «Quitar del seguimiento» (control manual). El
#: motivo se guarda en el campo de texto de F6-fix7 (`seguimiento_excluded_reason`)
#: como «etiqueta: texto libre»; no hace falta migración.
EXCLUSION_REASON_CODES: dict[str, str] = {
    "cancelado": "cancelado",
    "duplicado": "duplicado",
    "prueba": "prueba",
    "reembolsado": "reembolsado",
    "otro": "otro",
}

ExclusionReasonCode = Literal["cancelado", "duplicado", "prueba", "reembolsado", "otro"]


def compose_exclusion_reason(code: str | None, text: str | None) -> str | None:
    """Motivo que se guarda: `cancelado`, `duplicado: era el 5782`, o el texto
    libre tal cual. Con «otro» + texto se guarda solo el texto."""
    label = EXCLUSION_REASON_CODES.get((code or "").strip().lower())
    free = " ".join((text or "").split())
    if label and free:
        return free if label == "otro" else f"{label}: {free}"
    return label or free or None


def _exclusion_items(
    session: Session, order_ids: list[str],
) -> list[dict[str, Any]]:
    """Por cada pedido pedido: número, cliente, si ya está excluido y sus
    AVISOS aguas abajo (factura, cobro, albarán, SAT, Drive…). Solo avisa: no
    bloquea nada."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.api.orders import customer_names  # noqa: PLC0415
    from app.erp.downstream import downstream_reasons  # noqa: PLC0415

    orders = list(session.scalars(select(Order).where(Order.id.in_(order_ids))))
    names = customer_names(session, orders)
    items: list[dict[str, Any]] = []
    for o in orders:
        n = names.get(o.id) or {}
        items.append({
            "order_id": o.id,
            "order_number": o.order_number,
            "cliente": core.compose_client(n.get("company_name"), n.get("contact_name")),
            "woo_status": o.woo_status,
            "excluido": o.seguimiento_excluded_at is not None,
            "excluido_motivo": o.seguimiento_excluded_reason,
            "avisos": downstream_reasons(session, o),
        })
    items.sort(key=lambda it: it["order_number"] or "")
    return items


class ExcludePreviewIn(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/exclude-preview")
def exclude_preview(
    payload: ExcludePreviewIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Control manual — antes de «Quitar del seguimiento»: qué tiene cada pedido
    aguas abajo (factura, cobro, albarán, SAT, Drive…) para AVISAR. No escribe
    nada y no bloquea: Bart decide."""
    _ = current_user
    items = _exclusion_items(session, payload.order_ids)
    return {
        "ok": True,
        "items": items,
        "con_avisos": sum(1 for it in items if it["avisos"]),
        "ya_excluidos": sum(1 for it in items if it["excluido"]),
    }


class ExcludeIn(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=2000)
    #: Motivo rápido (opcional): cancelado / duplicado / prueba / reembolsado / otro.
    reason_code: ExclusionReasonCode | None = None


@router.post("/exclude")
def exclude_orders(
    payload: ExcludeIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """ERP-F6-fix7 + control manual — QUITAR pedidos del seguimiento (uno o
    varios), en CUALQUIER estado, aunque tengan factura, cobro o albarán (se
    devuelven como `avisos`, nunca se bloquea). Un pedido excluido no se lista,
    no se inserta en la hoja, no se actualiza ni se cuenta. NO borra ni modifica
    el pedido en BoHub ni en FACTUSOL; su fila en la hoja (si la hay) se queda
    intacta. Registra quién, cuándo y el motivo (rápido + texto). Idempotente:
    el ya excluido no se vuelve a sellar (conserva su fecha y motivo). Es
    reversible (`/include`)."""
    from sqlalchemy import select  # noqa: PLC0415

    items = _exclusion_items(session, payload.order_ids)
    now = datetime.now(UTC).replace(tzinfo=None)
    reason = compose_exclusion_reason(payload.reason_code, payload.reason)
    excluded = 0
    already = 0
    for order in session.scalars(
        select(Order).where(Order.id.in_(payload.order_ids))
    ):
        if order.seguimiento_excluded_at is not None:
            already += 1
            continue
        order.seguimiento_excluded_at = now
        order.seguimiento_excluded_by_user_id = current_user.id
        order.seguimiento_excluded_reason = reason
        excluded += 1
    session.commit()
    return {
        "ok": True,
        "excluded": excluded,
        "already_excluded": already,
        "reason": reason,
        "avisos": {it["order_number"]: it["avisos"] for it in items if it["avisos"]},
        "con_avisos": sum(1 for it in items if it["avisos"]),
    }


class IncludeIn(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/include")
def include_orders(
    payload: IncludeIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """ERP-F6-fix7 — REINCLUIR en el seguimiento pedidos antes excluidos
    (revierte `/exclude`). Limpia quién/cuándo/motivo de exclusión. Idempotente:
    reincluir lo que ya estaba incluido no hace nada (se cuenta aparte)."""
    _ = current_user
    from sqlalchemy import select  # noqa: PLC0415

    included = 0
    already = 0
    for order in session.scalars(
        select(Order).where(Order.id.in_(payload.order_ids))
    ):
        if order.seguimiento_excluded_at is None:
            already += 1
            continue
        order.seguimiento_excluded_at = None
        order.seguimiento_excluded_by_user_id = None
        order.seguimiento_excluded_reason = None
        included += 1
    session.commit()
    return {"ok": True, "included": included, "already_included": already}


@router.post("/reconcile-woo", status_code=status.HTTP_202_ACCEPTED)
def reconcile_woo(
    dry_run: bool = Query(default=True),
    store: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """ERP-Woo — «poner al día» los estados de WooCommerce de los pedidos que
    BoHub tiene como activos: lista por estado en cada tienda y cruza con los
    activos, aplicando la regla (cancelado/fallido/reembolso-no-cumplido → fuera
    del seguimiento; reembolso-cumplido → marcado). `dry_run=true` (por defecto)
    PREVISUALIZA. Corre en SEGUNDO PLANO (worker-sync): responde al instante con
    un `job_id`; el estado se consulta en `reconcile-woo-status/{job_id}`. Así
    no se bloquea la petición (era lo que daba 504). Nunca toca Drive ni el
    histórico; solo `woo_status`."""
    _ = session, current_user
    from app.integrations.woocommerce.jobs import enqueue_woo_reconcile  # noqa: PLC0415

    job_id = enqueue_woo_reconcile(dry_run=dry_run, store_account_id=store)
    return {"job_id": job_id, "status": "queued", "preview": dry_run}


@router.get("/reconcile-woo-status/{job_id}")
def reconcile_woo_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Polling del job de reconciliación: `pending` / `finished` (+`result` con
    el recuento por categoría) / `error` (+`error` legible)."""
    _ = session, current_user
    return _rq_reconcile_status(
        job_id,
        error_msg="La puesta al día falló (una tienda no respondió). "
                  "Revisa la conexión con WooCommerce y vuelve a intentarlo.",
    )


@router.post("/reconcile-factusol", status_code=status.HTTP_202_ACCEPTED)
def reconcile_factusol(
    dry_run: bool = Query(default=True),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """ERP — enlaza a los pedidos las facturas que YA existen en FACTUSOL (las
    creadas a mano incluidas), por REFFAC. Pasa el pedido a «facturado» y lo
    deja emailable. Escribe SOLO en BoHub (nunca en FACTUSOL). `dry_run=true`
    (por defecto) PREVISUALIZA (qué se enlazaría, y conflictos de pedidos con
    más de una factura) sin escribir. Corre en segundo plano (worker-factusol);
    responde con `job_id`, el estado en `reconcile-factusol-status/{job_id}`."""
    _ = session, current_user
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_factusol_invoice_reconcile,
    )

    job_id = enqueue_factusol_invoice_reconcile(dry_run=dry_run)
    return {"job_id": job_id, "status": "queued", "preview": dry_run}


@router.get("/reconcile-factusol-status/{job_id}")
def reconcile_factusol_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Polling del job de vinculación de facturas: `pending` / `finished`
    (+`result` con lo enlazado y los conflictos) / `error`."""
    _ = session, current_user
    return _rq_reconcile_status(
        job_id,
        error_msg="La vinculación de facturas falló. Revisa la conexión con "
                  "FACTUSOL y vuelve a intentarlo.",
    )


def _rq_reconcile_status(job_id: str, *, error_msg: str) -> dict[str, Any]:
    """Estado del job RQ (best-effort). Sin Redis (local/tests) → `pending`."""
    import logging  # noqa: PLC0415

    try:
        from redis import Redis  # noqa: PLC0415
        from rq.job import Job  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        conn = Redis.from_url(get_settings().redis_url)
        job = Job.fetch(job_id, connection=conn)
        rq_status = job.get_status(refresh=True)
        if rq_status == "failed":
            return {"status": "error", "error": error_msg}
        if rq_status == "finished":
            return {"status": "finished", "result": job.result}
        return {"status": "pending"}
    except Exception as exc:  # noqa: BLE001 — sin Redis o job caducado
        logging.getLogger(__name__).debug("reconcile job %s no consultable: %s", job_id, exc)
        return {"status": "pending"}
