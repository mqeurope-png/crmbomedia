"""BoHub ERP Fase D · PR D-1 — expedición manual (bultos + albarán + etiqueta).

Endpoints táctiles de la Cola SAT para operar el envío sin depender aún de la
API de Genei/DSV:

- Bultos (`shipment_packages`): multi-bulto obligatorio; el pedido solo pasa a
  `packed` cuando tiene ≥1 bulto con peso + 3 dimensiones.
- Albarán: descarga automática del plugin PDF de WooCommerce (o subida manual
  como fallback) — `shipment_files(kind=albaran)`.
- Etiqueta: siempre subida manual en Fase D — `shipment_files(kind=etiqueta)`.

Los bytes viven en el storage abstracto (`app/storage`); la BD solo guarda la
ruta relativa.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import require_erp_view
from app.erp.models import (
    KIND_ALBARAN,
    SHIPMENT_FILE_KINDS,
    SOURCE_CRM_GENERATED_PDF,
    SOURCE_MANUAL_UPLOAD,
    SOURCE_WOO_PDF_PLUGIN,
    Order,
    OrderSource,
    ShipmentFile,
    ShipmentPackage,
)
from app.erp.state_machine.engine import TransitionError, apply_transition
from app.models.crm import User
from app.storage import get_shipping_storage
from app.storage.base import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/orders", tags=["erp-shipping"])

#: Tope de tamaño por fichero de expedición (PDF de albarán / etiqueta).
MAX_SHIPPING_FILE_BYTES = 20 * 1024 * 1024
#: MIME permitidos para albarán/etiqueta.
ALLOWED_SHIPPING_MIME = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/jpg",
})


def _get_order(session: Session, order_id: str) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise not_found("Order")
    return order


def _status_value(v: Any) -> str:
    return getattr(v, "value", v)


# --- bultos ------------------------------------------------------------------


class PackageIn(BaseModel):
    # Opcionales para poder devolver 400 (no 422) ante bultos incompletos.
    weight_kg: float | None = None
    height_cm: int | None = None
    width_cm: int | None = None
    depth_cm: int | None = None


def _validate_package(i: int, p: PackageIn) -> None:
    fields = {"weight_kg": p.weight_kg, "height_cm": p.height_cm,
              "width_cm": p.width_cm, "depth_cm": p.depth_cm}
    missing = [k for k, v in fields.items() if v is None]
    if missing:
        raise HTTPException(400, {
            "code": "package_incomplete",
            "detail": f"Bulto {i + 1}: faltan {', '.join(missing)}.",
        })
    if any((v or 0) <= 0 for v in fields.values()):
        raise HTTPException(400, {
            "code": "package_invalid",
            "detail": f"Bulto {i + 1}: peso y medidas deben ser > 0.",
        })


def _serialise_package(p: ShipmentPackage) -> dict[str, Any]:
    return {
        "id": p.id, "position": p.position,
        "weight_kg": float(p.weight_kg), "height_cm": p.height_cm,
        "width_cm": p.width_cm, "depth_cm": p.depth_cm,
    }


@router.post("/{order_id}/packages")
def set_packages(
    order_id: str,
    packages: list[PackageIn],
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Reemplaza la lista de bultos del pedido (idempotente). Rechaza (400) los
    bultos incompletos o con peso/medidas ≤ 0."""
    _ = current_user
    order = _get_order(session, order_id)
    for i, p in enumerate(packages):
        _validate_package(i, p)
    # Borra los previos y crea los nuevos con position 1..N.
    for old in session.scalars(
        select(ShipmentPackage).where(ShipmentPackage.order_id == order.id)
    ):
        session.delete(old)
    session.flush()
    created = []
    for i, p in enumerate(packages):
        row = ShipmentPackage(
            order_id=order.id, position=i + 1, weight_kg=p.weight_kg,
            height_cm=p.height_cm, width_cm=p.width_cm, depth_cm=p.depth_cm,
        )
        session.add(row)
        created.append(row)
    session.commit()
    return {"order_id": order.id,
            "packages": [_serialise_package(p) for p in created]}


@router.get("/{order_id}/packages")
def list_packages(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    _get_order(session, order_id)
    rows = session.scalars(
        select(ShipmentPackage).where(ShipmentPackage.order_id == order_id)
        .order_by(ShipmentPackage.position.asc())
    )
    return {"items": [_serialise_package(p) for p in rows]}


@router.post("/{order_id}/transition/preparation/packed")
def transition_packed(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Marca el pedido `packed`. Exige ≥1 bulto (400 si no hay). La matriz de
    roles/estado la aplica el engine (403/409)."""
    order = _get_order(session, order_id)
    n = session.scalar(
        select(ShipmentPackage.id).where(ShipmentPackage.order_id == order.id).limit(1)
    )
    if n is None:
        raise HTTPException(400, {
            "code": "no_packages",
            "detail": "Añade al menos un bulto con peso y medidas antes de embalar.",
        })
    try:
        apply_transition(
            session, order=order, domain=_preparation_domain(),
            to_status="packed", actor=current_user,
        )
    except TransitionError as exc:
        http = {
            "invalid_transition": 409, "role_forbidden": 403,
            "guard_failed": 409, "evidence_missing": 422,
        }.get(exc.code, 400)
        raise HTTPException(http, {"code": exc.code, "detail": exc.detail}) from exc
    session.commit()
    return {"order_id": order.id,
            "preparation_status": _status_value(order.preparation_status)}


def _preparation_domain():
    from app.erp.models import StatusDomain  # noqa: PLC0415

    return StatusDomain.PREPARATION


# --- ficheros de expedición (albarán / etiqueta) -----------------------------


def _serialise_file(f: ShipmentFile) -> dict[str, Any]:
    return {
        "id": f.id, "kind": f.kind, "source": f.source,
        "filename": f.filename, "mime_type": f.mime_type,
        "size_bytes": f.size_bytes,
        "uploaded_by_user_id": f.uploaded_by_user_id,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "download_url": (
            f"/api/erp/orders/{f.order_id}/shipping-files/{f.id}/download"
        ),
    }


def _current_files(session: Session, order_id: str, kind: str | None = None):
    stmt = select(ShipmentFile).where(
        ShipmentFile.order_id == order_id, ShipmentFile.replaced_at.is_(None),
    )
    if kind:
        stmt = stmt.where(ShipmentFile.kind == kind)
    return list(session.scalars(stmt.order_by(ShipmentFile.uploaded_at.desc())))


def _mark_replaced(session: Session, order_id: str, kind: str) -> None:
    now = datetime.now(UTC)
    for prev in _current_files(session, order_id, kind):
        prev.replaced_at = now


def _store_new_file(
    session: Session, order: Order, *, kind: str, source: str,
    filename: str, mime_type: str, data: bytes, actor_id: str | None,
) -> ShipmentFile:
    try:
        path = get_shipping_storage().save(order.id, kind, filename, data)
    except (StorageError, NotImplementedError) as exc:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, {
            "code": "storage_error", "detail": str(exc)[:300],
        }) from exc
    _mark_replaced(session, order.id, kind)
    row = ShipmentFile(
        order_id=order.id, kind=kind, source=source, filename=filename,
        mime_type=mime_type, size_bytes=len(data), storage_path=path,
        uploaded_by_user_id=actor_id, uploaded_at=datetime.now(UTC),
    )
    session.add(row)
    return row


@router.get("/{order_id}/shipping-files")
def list_shipping_files(
    order_id: str,
    kind: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Ficheros vigentes (no reemplazados), opcionalmente filtrados por kind."""
    _ = current_user
    _get_order(session, order_id)
    if kind is not None and kind not in SHIPMENT_FILE_KINDS:
        raise HTTPException(400, f"kind inválido: {kind!r}")
    return {"items": [_serialise_file(f)
                      for f in _current_files(session, order_id, kind)]}


@router.get("/{order_id}/shipping-files/{file_id}/download")
def download_shipping_file(
    order_id: str,
    file_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    """Devuelve el fichero para abrir INLINE en el navegador (imprimir desde
    el diálogo del navegador — no hay impresora térmica en el taller)."""
    _ = current_user
    f = session.get(ShipmentFile, file_id)
    if f is None or f.order_id != order_id:
        raise not_found("ShipmentFile")
    try:
        data = get_shipping_storage().read(f.storage_path)
    except (StorageError, NotImplementedError) as exc:
        raise HTTPException(404, {
            "code": "file_unavailable", "detail": str(exc)[:300],
        }) from exc
    return Response(
        content=data, media_type=f.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{f.filename}"'},
    )


@router.post("/{order_id}/shipping-files", status_code=201)
async def upload_shipping_file(
    order_id: str,
    kind: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Subida manual de albarán o etiqueta. Marca el fichero previo del mismo
    kind como reemplazado (se conserva) y guarda el nuevo."""
    order = _get_order(session, order_id)
    if kind not in SHIPMENT_FILE_KINDS:
        raise HTTPException(400, f"kind inválido: {kind!r} (usa albaran|etiqueta)")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_SHIPPING_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Tipo no permitido: {mime!r}. Sube PDF o imagen (PNG/JPG).",
        )
    data = await file.read()
    if not data:
        raise HTTPException(400, "Archivo vacío.")
    if len(data) > MAX_SHIPPING_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "El fichero supera el máximo de 20 MB.",
        )
    row = _store_new_file(
        session, order, kind=kind, source=SOURCE_MANUAL_UPLOAD,
        filename=file.filename or f"{kind}.pdf", mime_type=mime, data=data,
        actor_id=current_user.id,
    )
    session.commit()
    return {"order_id": order.id, "file": _serialise_file(row)}


@router.post("/{order_id}/albaran/fetch-from-woo", status_code=201)
def fetch_albaran_from_woo(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Descarga el albarán del plugin PDF de WooCommerce y lo guarda como
    ShipmentFile. Idempotente: si ya hay un albarán de Woo vigente, lo devuelve
    sin re-descargar. 502 si la descarga falla (el operativo sube a mano)."""
    _ = current_user
    order = _get_order(session, order_id)
    if _status_value(order.external_source) != OrderSource.WOOCOMMERCE.value:
        raise HTTPException(400, {
            "code": "not_woo_order",
            "detail": "El pedido no viene de WooCommerce; sube el albarán a mano.",
        })
    if not order.store_id or not order.external_id:
        raise HTTPException(400, {
            "code": "missing_woo_link",
            "detail": "Falta la tienda o el id de Woo del pedido.",
        })
    # Idempotente: ¿ya hay un albarán auto-generado vigente?
    for existing in _current_files(session, order.id, KIND_ALBARAN):
        if existing.source in (SOURCE_WOO_PDF_PLUGIN, SOURCE_CRM_GENERATED_PDF):
            return {"order_id": order.id, "file": _serialise_file(existing),
                    "already_present": True, "source": existing.source}

    from app.integrations.woocommerce.client import (  # noqa: PLC0415
        WooError,
        WooHTTPClient,
    )
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415

    account = session.get(IntegrationAccount, order.store_id)
    if account is None:
        raise HTTPException(400, {
            "code": "store_missing", "detail": "La tienda del pedido no existe.",
        })
    try:
        woo_id = int(str(order.external_id).strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, {
            "code": "bad_woo_id",
            "detail": f"id de Woo no numérico: {order.external_id!r}",
        }) from exc

    client = WooHTTPClient(account)
    # Un solo fetch del pedido: da el order_key (para el plugin público) y los
    # datos (para generar el albarán propio si el plugin no responde).
    try:
        order_json = client.get_order(woo_id)
    except WooError as exc:
        logger.warning("albarán Woo: pedido %s no accesible: %s", woo_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "woo_unreachable",
            "detail": "WooCommerce no responde; sube el albarán a mano.",
        }) from exc
    order_key = order_json.get("order_key") if isinstance(order_json, dict) else None
    try:
        pdf, filename = client.get_packing_slip_pdf(woo_id, order_key=order_key)
        source = SOURCE_WOO_PDF_PLUGIN
    except WooError:
        # Fallback garantizado: generamos un albarán propio con los datos del
        # pedido (nunca 502 por el plugin).
        from app.erp.albaran_pdf import generate_albaran_pdf  # noqa: PLC0415

        pdf = generate_albaran_pdf(order_json)
        filename = f"albaran-{woo_id}.pdf"
        source = SOURCE_CRM_GENERATED_PDF
        logger.info("albarán Woo %s generado por el CRM (plugin no disponible)", woo_id)
    row = _store_new_file(
        session, order, kind=KIND_ALBARAN, source=source,
        filename=filename, mime_type="application/pdf", data=pdf, actor_id=None,
    )
    session.commit()
    return {"order_id": order.id, "file": _serialise_file(row),
            "already_present": False, "source": source}


class MarkPickedUpPayload(BaseModel):
    #: Tracking del transportista si lo hay; opcional en recogida manual.
    tracking_number: str | None = None


@router.post("/{order_id}/mark-picked-up")
def mark_picked_up(
    order_id: str,
    payload: MarkPickedUpPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """«Marcar recogido» desde el taller: el paquete salió. Lleva transporte a
    `in_transit` (auto-creando el registro de envío si hacía falta). Exige el
    pedido `packed`. La recogida es manual (sin tracking del sistema), así que
    se marca `manual_pickup` en la evidencia (respeta el guard de tracking)."""
    order = _get_order(session, order_id)
    if _status_value(order.preparation_status) != "packed":
        raise HTTPException(400, {
            "code": "not_packed",
            "detail": "Solo se puede marcar recogido un pedido embalado.",
        })
    transport = _status_value(order.transport_status)
    if transport in ("in_transit", "delivered", "already_shipped_externally"):
        return {"order_id": order.id, "transport_status": transport,
                "already_picked_up": True}

    from app.erp.models import StatusDomain  # noqa: PLC0415

    tracking = (payload.tracking_number if payload else None) or None
    try:
        if transport == "not_shipped":
            apply_transition(
                session, order=order, domain=StatusDomain.TRANSPORT,
                to_status="label_created", actor=current_user,
            )
        apply_transition(
            session, order=order, domain=StatusDomain.TRANSPORT,
            to_status="in_transit", actor=current_user,
            evidence={"manual_pickup": True, "tracking_number": tracking},
        )
    except TransitionError as exc:
        http = {
            "invalid_transition": 409, "role_forbidden": 403,
            "guard_failed": 409, "evidence_missing": 422,
        }.get(exc.code, 400)
        raise HTTPException(http, {"code": exc.code, "detail": exc.detail}) from exc
    if tracking:
        order.tracking_number = tracking
    session.commit()
    return {"order_id": order.id,
            "transport_status": _status_value(order.transport_status),
            "already_picked_up": False}
