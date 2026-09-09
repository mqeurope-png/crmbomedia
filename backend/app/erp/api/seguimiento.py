"""ERP-F6 — API del seguimiento de pedidos (la vista que sustituye el Excel).

La vista y la exportación funcionan SIEMPRE, con o sin Drive configurado; el
sincronizado con la hoja es un extra que avisa si falta configuración.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
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
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    return core.filter_rows(
        _rows(session),
        serie=serie, vendedor=vendedor, transportista=transportista,
        origen=origen, desde=desde, hasta=hasta, estado=estado, q=q,
        en_curso=en_curso, sort=sort, direction=direction,
    )


@router.get("")
def list_seguimiento(
    serie: int | None = Query(default=None, ge=1, le=9),
    vendedor: str | None = Query(default=None),
    transportista: str | None = Query(default=None),
    origen: str | None = Query(default=None),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None, pattern="^(pendiente|enviado|facturado)$"),
    q: str | None = Query(default=None, max_length=120),
    en_curso: bool = Query(default=True),
    sort: str = Query(default="fecha"),
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),  # noqa: A002
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Vista de seguimiento. Por defecto: pedidos EN CURSO (la parte de
    arriba del Excel, lo que Bart mira a diario)."""
    _ = current_user
    rows = _filtered(
        session, serie=serie, vendedor=vendedor, transportista=transportista,
        origen=origen, desde=desde, hasta=hasta, estado=estado, q=q,
        en_curso=en_curso, sort=sort, direction=dir,
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
    estado: str | None = Query(default=None, pattern="^(pendiente|enviado|facturado)$"),
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
    """«Actualizar hoja de Drive»: sincronización MANUAL. Identifica cada
    pedido por su número desnudo (ERP-F6-fix2): actualiza el que ya está,
    añade el que no. Incremental, sin borrar filas ajenas ni pisar celdas
    manuales. `dry_run=true` PREVISUALIZA (cuántas añade/actualiza/conflictos)
    sin escribir nada — para que Bart lo vea antes de confirmar."""
    _ = current_user
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.drive_sheets import (  # noqa: PLC0415
        DriveConfigError,
        DriveSyncError,
        GoogleSheetsClient,
        drive_config,
        sync_to_sheet,
    )
    from app.erp.models import ErpDriveSyncRow  # noqa: PLC0415
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
    # Se sincronizan los pedidos EN CURSO + los ya presentes en la hoja
    # (cualquier pedido con foto previa se sigue actualizando).
    rows = core.filter_rows(_rows(session), en_curso=True, sort="fecha", direction="asc")
    known = {r["id"] for r in rows}
    tracked_ids = set(session.scalars(select(ErpDriveSyncRow.order_id)).all())
    if tracked_ids - known:
        extra = [
            r for r in core.filter_rows(
                _rows(session), en_curso=False, sort="fecha", direction="asc",
            )
            if r["id"] in tracked_ids - known
        ]
        rows = rows + extra
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
