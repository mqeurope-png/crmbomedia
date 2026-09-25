"""BoHub ERP — bandeja de excepciones + settings (Fase A PR 6).

Cierra la Fase A. Endpoints:
  - GET   /api/erp/exceptions?type=&status=&assigned=me|<uid>
  - POST  /api/erp/exceptions/{id}/assign     {assigned_to_user_id}
  - POST  /api/erp/exceptions/{id}/status     {status}   (open/in_progress/dismissed)
  - POST  /api/erp/exceptions/{id}/resolve    {resolution_note}
  - GET   /api/erp/settings                    (lleva `can_edit`)
  - PATCH /api/erp/settings                    (solo ADMIN)
  - POST  /api/erp/settings/invoice-email/preview    (plantilla con datos de muestra)
  - POST  /api/erp/settings/invoice-email/test-send  (solo ADMIN; me la envía por Gmail)
  - GET   /api/erp/settings/next-references          (siguiente nº manual y ref. por tienda)

El chip de alerta ETA vencida es de presentación: el backend expone
`eta_overdue` (bool) por excepción comparando `metadata.eta_date <= hoy`;
NO cambia el estado (decisión del catálogo).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_event
from app.core.crypto import encrypt
from app.core.errors import not_found
from app.db.session import get_session
from app.erp.api.deps import (
    ERP_ADMIN_ROLES,
    require_config,
    require_erp_edit,
    require_erp_view,
)
from app.erp.contrapartidas import (
    contrapartidas_config,
    normalize_store,
    paypal_by_store_config,
    validate_contrapartidas,
)
from app.erp.drive_sheets import (
    DriveConfigError,
    parse_service_account_json,
    service_account_email,
)
from app.erp.models import (
    ERP_SETTINGS_SINGLETON_ID,
    ErpException,
    ErpSettings,
    ExceptionStatus,
    InvoiceMode,
)
from app.erp.seguimiento import (
    abbr_variants_config,
    series_abbreviations_config,
    shipping_origins_config,
)
from app.integrations.factusol.catalogs import normalize_code
from app.integrations.factusol.service import REF_PREFIX_RE, configured_ref_prefixes
from app.models.crm import User

router = APIRouter(prefix="/api/erp", tags=["erp-exceptions"])


@router.get("/email-senders")
def email_senders(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Remitentes disponibles para los envíos del ERP: los «enviar como»
    VERIFICADOS de la cuenta de Gmail conectada del CRM (cualquiera, no solo los
    mapeados a una tienda/serie). Alimenta el selector «Enviar desde». Si Gmail
    no está conectado / accesible, devuelve la lista vacía y el motivo, y la UI
    cae al remitente propuesto por tienda/serie."""
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    try:
        aliases = gmail_service.list_aliases(session, current_user.id)
    except Exception as exc:  # noqa: BLE001 — Gmail desconectado / sin scope / caído
        return {
            "senders": [], "available": False,
            "problem": "gmail_unavailable", "detail": str(exc)[:200],
        }
    senders = [
        {
            "email": a["send_as_email"],
            "name": (a.get("display_name") or "").strip(),
            "is_primary": bool(a.get("is_primary")),
        }
        for a in aliases if a.get("send_as_email")
    ]
    # La cuenta base (primaria) primero; el resto por dirección.
    senders.sort(key=lambda s: (not s["is_primary"], s["email"].lower()))
    return {"senders": senders, "available": True, "problem": None}


# --- schemas -----------------------------------------------------------------


class AssignIn(BaseModel):
    assigned_to_user_id: str | None = None


class StatusIn(BaseModel):
    status: str


class ResolveIn(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=2000)


class SettingsIn(BaseModel):
    default_invoice_mode: str | None = None
    auto_invoice_max_amount_eur: float | None = None
    default_carrier_id: str | None = None
    factusol_default_ejercicio: str | None = None
    factusol_live: bool | None = None
    # C-2: serie de facturación (global + override por origen/tienda).
    factusol_series_default: str | None = None
    factusol_series_by_source: dict[str, str] | None = None
    #: Prefijo de la referencia común (`REFPCL`/`REFALB`/`REFFAC`) que la app
    #: Woo→FACTUSOL pone a los documentos de cada tienda ({"fluxlasers":
    #: "FLE"}). Sin él se deriva de las 3 primeras letras del nº de pedido
    #: (`FLUXLA-5789` → `FLU`), que puede no coincidir, y el F_PCL del pedido
    #: web no se localiza («aún no existe en FACTUSOL»). Vacío = derivado.
    factusol_ref_prefix_by_store: dict[str, str] | None = None
    #: ERP-E2 — nombre de la empresa emisora de cada serie: {"5": "Streamtec"}.
    factusol_series_names: dict[str, str] | None = None
    #: ERP-E2-fix2 — valor de F_PCL.ESTPCL que FACTUSOL usa para «Enviado»
    #: (= pedido facturado). Confirmado en vivo: "2".
    factusol_estpcl_invoiced: str | None = None
    #: E3-B-fix3 — estados con los que se marca el documento de ORIGEN al
    #: convertir (confirmados en el escritorio): ESTPRE «Aceptado» y ESTALB
    #: «Facturado». Vacío explícito = no marcar.
    factusol_estpre_accepted: str | None = None
    factusol_estalb_invoiced: str | None = None
    #: ERP-F3 — estado de COBRO de las facturas (F_FAC.ESTFAC). Confirmado por
    #: Bart: 2 = cobrada, 0 = pendiente. Vacío explícito = no marcar.
    factusol_estfac_cobrada: str | None = None
    factusol_estfac_pendiente: str | None = None
    #: ERP-F3 — marcar la factura como cobrada al emitirla SI el pedido ya
    #: constaba pagado (web con pago al comprar). Desactivado por defecto: es
    #: una afirmación contable automática y Bart debe activarla a conciencia.
    factusol_auto_mark_paid_when_order_paid: bool | None = None
    #: ERP-E4 — identidad fiscal de las empresas emisoras, por serie
    #: ({"1": {...}, "5": {...}}). Alimenta los PDF; editable para que Bart
    #: corrija un IBAN sin despliegue. Ver `factusol_pdf.COMPANY_DEFAULTS`.
    factusol_companies: dict[str, dict[str, Any]] | None = None
    #: E4-fix1 — almacenes de recogida del albarán de devolución
    #: ([{nombre, direccion}]).
    factusol_pickup_warehouses: list[dict[str, str]] | None = None
    #: F1 — plantillas del email de factura por idioma
    #: ({"es": {"subject","body"}, ...}). Placeholders {cliente}/{numero}/
    #: {referencia}. Vacío = defaults del código.
    factusol_invoice_email_templates: dict[str, dict[str, str]] | None = None
    #: Aviso de ENVÍO al cliente (nº de seguimiento + enlace) por idioma.
    #: Placeholders {cliente}/{pedido}/{tracking}/{enlace}/{agencia}.
    shipment_email_templates: dict[str, dict[str, str]] | None = None
    #: Remitente del aviso de envío de los pedidos MANUALES por idioma:
    #: {"es": "pedidos@streamtec.es", "otros": "info@artisjet-printers.eu"}.
    shipment_email_from: dict[str, str] | None = None
    #: ERP-F5 — contrapartidas de cobro ([{codigo, nombre}]; la tabla de
    #: FACTUSOL no se ha localizado, así que el catálogo vive aquí) y la
    #: contrapartida PayPal por tienda ({"artisjet": "12", …}).
    contrapartidas: list[dict[str, Any]] | None = None
    paypal_contrapartidas_by_store: dict[str, str] | None = None
    #: ERP-F6 — orígenes del envío (el «OFI-TER-SAT» del Excel), configurables.
    shipping_origins: list[str] | None = None
    #: ERP-F6 — sincronizado del seguimiento con la hoja de Drive: JSON de la
    #: cuenta de SERVICIO (write-only: se cifra y JAMÁS se devuelve ni se
    #: loguea; "" lo borra) e ID de la hoja destino.
    drive_service_account_json: str | None = None
    drive_spreadsheet_id: str | None = None
    #: ERP-F6-fix2 — en «Albarán / Núm Pedido WEb» escribir el nº de albarán
    #: cuando exista (coherente con las filas antiguas de Bart) o el de pedido
    #: web si no. Configurable; por defecto True.
    drive_reference_prefer_albaran: bool | None = None
    #: Espejo (Fase 2) — reconcile automático BoHub ↔ hoja en worker-sync.
    #: Apagado por defecto: se enciende tras revisar una pasada manual.
    seguimiento_reconcile_enabled: bool | None = None
    #: Cada cuántos minutos corre el reconcile automático (mín. 5).
    seguimiento_reconcile_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    #: ERP-F6-fix3 — abreviaturas de empresa por serie ({"1": "BO", "2": "MQ",
    #: "5": "ST"}) que se escriben en la columna Empresa del seguimiento.
    factusol_series_abbreviations: dict[str, str] | None = None
    #: ERP-F6-fix4 — variantes históricas aceptadas por serie
    #: ({"5": ["STR","STREAMTEC"]}); al comparar valen, al escribir se usa la
    #: canónica. Los defaults (STR/BOM…) van cableados; esto añade más.
    factusol_series_abbr_variants: dict[str, list[str]] | None = None
    #: ERP · remitente (alias de envío) del email de factura por serie = empresa
    #: emisora ({"2": "info@artisjet-printers.eu", "5": "pedidos@streamtec.es"}).
    #: Un valor vacío borra el default de esa serie.
    factusol_series_email_from: dict[str, str] | None = None
    #: ERP · remitente del email de factura por TIENDA ({"boprint":
    #: "pedidos@streamtec.es"}). Más fino que la serie (dos tiendas de la misma
    #: empresa emisora pueden enviar desde alias distintos); vacío borra el
    #: default de esa tienda y cae al remitente de la serie.
    factusol_store_email_from: dict[str, str] | None = None
    #: ERP · email del SAT / taller: destinatario por defecto de «Enviar por
    #: email» desde un pedido. "" = sin destinatario precargado.
    sat_email: str | None = Field(default=None, max_length=255)


# --- helpers -----------------------------------------------------------------


def _parse_eta(metadata: dict[str, Any]) -> date | None:
    raw = metadata.get("eta_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            return None


def _serialise(
    exc: ErpException, order_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = json.loads(exc.metadata_json) if exc.metadata_json else {}
    eta = _parse_eta(metadata)
    order_info = order_info or {}
    return {
        "id": exc.id,
        "type": getattr(exc.type, "value", exc.type),
        "subtype": exc.subtype,
        "status": getattr(exc.status, "value", exc.status),
        "order_id": exc.order_id,
        # D-2: identificar el pedido/cliente sin abrir la ficha.
        "order_number": order_info.get("order_number"),
        "contact_name": order_info.get("contact_name"),
        "company_name": order_info.get("company_name"),
        "metadata": metadata,
        "eta_date": eta.isoformat() if eta else None,
        # Chip de alerta: ETA vencida y la excepción sigue abierta.
        "eta_overdue": bool(
            eta and eta <= datetime.now(UTC).date()
            and exc.status in (ExceptionStatus.OPEN, ExceptionStatus.IN_PROGRESS)
        ),
        "assigned_to_user_id": exc.assigned_to_user_id,
        "reported_by_user_id": exc.reported_by_user_id,
        "resolution_note": exc.resolution_note,
        "resolved_at": exc.resolved_at.isoformat() if exc.resolved_at else None,
        "created_at": exc.created_at.isoformat(),
    }


def _orders_info(
    session: Session, rows: list[ErpException],
) -> dict[str, dict[str, Any]]:
    """D-2: `{order_id: {order_number, contact_name, company_name}}` para las
    excepciones listadas — batch, sin N+1."""
    from app.erp.api.orders import customer_names  # noqa: PLC0415
    from app.erp.models import Order  # noqa: PLC0415

    order_ids = {e.order_id for e in rows if e.order_id}
    if not order_ids:
        return {}
    orders = list(session.scalars(select(Order).where(Order.id.in_(order_ids))))
    names = customer_names(session, orders)
    return {
        o.id: {"order_number": o.order_number, **(names.get(o.id) or {})}
        for o in orders
    }


def _get(session: Session, exc_id: str) -> ErpException:
    exc = session.get(ErpException, exc_id)
    if exc is None:
        raise not_found("Exception")
    return exc


# --- excepciones -------------------------------------------------------------


@router.get("/exceptions")
def list_exceptions(
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    assigned: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    stmt = select(ErpException)
    if type:
        stmt = stmt.where(ErpException.type == type)
    if status:
        stmt = stmt.where(ErpException.status == status)
    if assigned == "me":
        stmt = stmt.where(ErpException.assigned_to_user_id == current_user.id)
    elif assigned:
        stmt = stmt.where(ErpException.assigned_to_user_id == assigned)
    rows = list(session.scalars(
        stmt.order_by(ErpException.created_at.desc()).limit(limit)
    ))
    info = _orders_info(session, rows)
    return {"items": [_serialise(e, info.get(e.order_id)) for e in rows]}


@router.post("/exceptions/{exc_id}/assign")
def assign_exception(
    exc_id: str,
    payload: AssignIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    exc = _get(session, exc_id)
    if payload.assigned_to_user_id and not session.get(User, payload.assigned_to_user_id):
        raise HTTPException(400, "assigned_to_user_id no existe")
    exc.assigned_to_user_id = payload.assigned_to_user_id
    _audit(session, exc, "assigned", current_user,
           {"assigned_to": payload.assigned_to_user_id})
    session.commit()
    return _serialise(exc)


@router.post("/exceptions/{exc_id}/status")
def update_status(
    exc_id: str,
    payload: StatusIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Marcar como vista» (in_progress) / descartar (dismissed) / reabrir."""
    exc = _get(session, exc_id)
    try:
        new_status = ExceptionStatus(payload.status)
    except ValueError as e:
        raise HTTPException(400, f"status inválido: {payload.status!r}") from e
    if new_status == ExceptionStatus.RESOLVED:
        raise HTTPException(400, "Usa /resolve para cerrar con nota.")
    exc.status = new_status
    _audit(session, exc, "status_changed", current_user, {"to": new_status.value})
    session.commit()
    return _serialise(exc)


@router.post("/exceptions/{exc_id}/resolve")
def resolve_exception(
    exc_id: str,
    payload: ResolveIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    exc = _get(session, exc_id)
    exc.status = ExceptionStatus.RESOLVED
    exc.resolution_note = payload.resolution_note
    exc.resolved_at = datetime.now(UTC)
    exc.resolved_by_user_id = current_user.id
    _audit(session, exc, "resolved", current_user, {"note": payload.resolution_note})
    session.commit()
    return _serialise(exc)


def _audit(
    session: Session, exc: ErpException, verb: str, actor: User, extra: dict[str, Any]
) -> None:
    try:
        record_event(
            session, action=f"erp.exception_{verb}", target_type="exception",
            target_id=exc.id, actor=actor,
            metadata={"order_id": exc.order_id,
                      "type": getattr(exc.type, "value", exc.type), **extra},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass


# --- settings ----------------------------------------------------------------


def _get_or_create_settings(session: Session) -> ErpSettings:
    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        session.add(cfg)
        session.flush()
    return cfg


def _woocommerce_stores(session: Session) -> list[dict[str, str | None]]:
    """ERP-F6-fix3 — tiendas Woo dadas de alta ({slug, label}), para poder
    configurar la serie de cada una (no un único «WooCommerce» para las tres).
    Lleva además el prefijo de referencia FACTUSOL que ya tenga la cuenta en
    su `metadata_json` (fijado a mano; manda sobre el de Ajustes) y el que se
    DERIVA del nº de pedido si no se configura ninguno (`FLU` para
    `FLUXLA-…`), para que la UI enseñe el que se usará de verdad."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        derived_ref_prefix,
        store_metadata_ref_prefix,
    )
    from app.models.integration_settings import (  # noqa: PLC0415
        ExternalSystem,
        IntegrationAccount,
    )

    rows = session.scalars(
        select(IntegrationAccount)
        .where(IntegrationAccount.system == ExternalSystem.WOOCOMMERCE)
        .order_by(IntegrationAccount.account_id)
    ).all()
    return [
        {
            "slug": a.account_id, "label": a.display_name or a.account_id,
            "ref_prefix_metadata": store_metadata_ref_prefix(a),
            # Mismo cálculo que el nº de pedido Woo (`slug.upper()[:6]-nº`).
            "derived_ref_prefix": derived_ref_prefix(f"{(a.account_id or '').upper()[:6]}-0"),
        }
        for a in rows
    ]


def _serialise_settings(cfg: ErpSettings, session: Session) -> dict[str, Any]:
    from app.erp.invoice_email import (  # noqa: PLC0415
        series_email_from_config,
        store_email_from_config,
    )
    from app.erp.order_email import sat_email_config  # noqa: PLC0415

    mode = getattr(cfg.default_invoice_mode, "value", cfg.default_invoice_mode)
    return {
        "default_invoice_mode": mode,
        "auto_invoice_max_amount_eur": (
            float(cfg.auto_invoice_max_amount_eur)
            if cfg.auto_invoice_max_amount_eur is not None else None
        ),
        "default_carrier_id": cfg.default_carrier_id,
        "factusol_default_ejercicio": cfg.factusol_default_ejercicio,
        "factusol_live": bool(cfg.factusol_live),
        # C-2 + ERP-E2: serie = empresa emisora (default global + override por
        # origen + nombres). Ya no es una letra que se escriba en una columna:
        # es el número que decide el rango de numeración del CODFAC.
        "factusol_series_default": _series(cfg).get("default") or "",
        "factusol_series_by_source": _series(cfg).get("by_source") or {},
        # Prefijo de la referencia común por tienda Woo ({"fluxlasers": "FLE"}).
        "factusol_ref_prefix_by_store": configured_ref_prefixes(session),
        "factusol_series_names": _series(cfg).get("names") or {},
        "factusol_estpcl_invoiced": _series(cfg).get("estpcl_invoiced") or "",
        # E3-B-fix3: sin configurar → el default efectivo ("1"), para que la
        # UI enseñe lo que realmente se escribirá; "" = marcado desactivado.
        "factusol_estpre_accepted": _series(cfg).get("estpre_accepted", "1"),
        "factusol_estalb_invoiced": _series(cfg).get("estalb_invoiced", "1"),
        # ERP-F3: estado de cobro (defaults confirmados 2/0) + auto-marcado.
        "factusol_estfac_cobrada": _series(cfg).get("estfac_cobrada", "2"),
        "factusol_estfac_pendiente": _series(cfg).get("estfac_pendiente", "0"),
        "factusol_auto_mark_paid_when_order_paid": bool(
            _series(cfg).get("auto_mark_paid_when_order_paid", False)
        ),
        # ERP-E4: identidad fiscal por serie, ya fusionada con los defaults
        # extraídos de los modelos reales, + si esa serie tiene logo subido.
        "factusol_companies": _companies_with_logos(cfg),
        "factusol_pickup_warehouses": _pickup_warehouses(cfg),
        "factusol_invoice_email_templates": _invoice_email_templates(cfg),
        # Aviso de envío al cliente: plantillas (defaults + las guardadas) y
        # remitentes de los pedidos manuales (español / otros idiomas).
        "shipment_email_templates": _shipment_templates(cfg),
        "shipment_email_from": _shipment_from(cfg),
        # ERP-F5: contrapartidas de cobro (defaults = las 14 de Bart) y PayPal
        # por tienda, ya completados con los valores iniciales.
        "contrapartidas": contrapartidas_config(_series(cfg).get("contrapartidas")),
        "paypal_contrapartidas_by_store": paypal_by_store_config(
            _series(cfg).get("paypal_contrapartidas_by_store")
        ),
        # ERP-F6: orígenes del envío + estado del Drive. De las credenciales
        # SOLO sale el client_email (para que Bart comparta la hoja con él);
        # el JSON cifrado no se devuelve nunca.
        "shipping_origins": shipping_origins_config(_series(cfg).get("shipping_origins")),
        "drive_spreadsheet_id": cfg.drive_spreadsheet_id,
        "drive_configured": bool(
            cfg.drive_service_account_json_encrypted and cfg.drive_spreadsheet_id
        ),
        "drive_service_account_email": service_account_email(cfg),
        "drive_reference_prefer_albaran": bool(
            _series(cfg).get("drive_reference_prefer_albaran", True)
        ),
        # Espejo (Fase 2): reconcile automático y su intervalo.
        "seguimiento_reconcile_enabled": bool(
            _series(cfg).get("seguimiento_reconcile_enabled", False)
        ),
        "seguimiento_reconcile_interval_minutes": int(
            _series(cfg).get("seguimiento_reconcile_interval_minutes") or 10
        ),
        # ERP-F6-fix3: abreviaturas de empresa (serie→abrev) y las tiendas Woo
        # para poder configurar la serie de cada una (no un único WooCommerce).
        "factusol_series_abbreviations": {
            str(k): v
            for k, v in series_abbreviations_config(
                _series(cfg).get("series_abbreviations")
            ).items()
        },
        # ERP · remitente del email de factura por serie (empresa emisora).
        "factusol_series_email_from": {
            str(k): v
            for k, v in series_email_from_config(
                _series(cfg).get("series_email_from")
            ).items()
        },
        # ERP · remitente del email de factura por TIENDA (más fino que la serie).
        "factusol_store_email_from": store_email_from_config(
            _series(cfg).get("store_email_from")
        ),
        # ERP · destinatario por defecto de «Enviar pedido por email» (taller).
        "sat_email": sat_email_config(_series(cfg).get("sat_email")),
        "woocommerce_stores": _woocommerce_stores(session),
        "factusol_series_abbr_variants": {
            str(k): v
            for k, v in abbr_variants_config(
                _series(cfg).get("series_abbr_variants")
            ).items()
        },
    }


def _invoice_email_templates(cfg: ErpSettings) -> dict[str, Any]:
    from app.erp.invoice_email import INVOICE_EMAIL_DEFAULTS  # noqa: PLC0415

    stored = _series(cfg).get("invoice_email_templates")
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, dict[str, str]] = {}
    for lang, base in INVOICE_EMAIL_DEFAULTS.items():
        merged = dict(base)
        over = stored.get(lang)
        if isinstance(over, dict):
            for k in ("subject", "body"):
                if str(over.get(k) or "").strip():
                    merged[k] = str(over[k])
        out[lang] = merged
    return out


def _shipment_templates(cfg: ErpSettings) -> dict[str, Any]:
    from app.erp.shipment_email import merge_templates  # noqa: PLC0415

    return merge_templates(_series(cfg).get("shipment_email_templates"))


def _shipment_from(cfg: ErpSettings) -> dict[str, str]:
    from app.erp.shipment_email import manual_from_config  # noqa: PLC0415

    return manual_from_config(_series(cfg).get("shipment_email_from"))


def _pickup_warehouses(cfg: ErpSettings) -> list[dict[str, str]]:
    from app.erp.factusol_pdf import pickup_warehouses_config  # noqa: PLC0415

    return pickup_warehouses_config(_series(cfg).get("pickup_warehouses"))


def _companies_with_logos(cfg: ErpSettings) -> dict[str, Any]:
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        logo_path_for_serie,
        merge_companies,
    )

    companies = merge_companies(_series(cfg).get("companies"))
    for serie, comp in companies.items():
        try:
            path = logo_path_for_serie(int(serie))
        except (TypeError, ValueError):
            path = None
        comp["logo"] = path is not None
        # ERP-F3: el nombre de fichero, para enseñarlo junto a la miniatura
        # (el input de archivo aparece vacío al recargar — comportamiento del
        # navegador — y parecía que no se hubiera guardado).
        comp["logo_filename"] = path.name if path is not None else None
    return companies


def _series(cfg: ErpSettings) -> dict[str, Any]:
    """Blob `factusol_series_json` decodificado (vacío si falta o es ilegible)."""
    if not cfg.factusol_series_json:
        return {}
    try:
        data = json.loads(cfg.factusol_series_json)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/settings")
def get_settings_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    cfg = _get_or_create_settings(session)
    session.commit()
    # Lote 2 · PR-2: la UI desactiva «Guardar cambios» (con el motivo) a quien
    # no puede guardar, en vez de dejar que el PATCH falle con 403.
    return {**_serialise_settings(cfg, session), "can_edit": _can_edit(current_user)}


@router.patch("/settings")
def update_settings(
    payload: SettingsIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    cfg = _get_or_create_settings(session)
    if payload.default_invoice_mode is not None:
        try:
            cfg.default_invoice_mode = InvoiceMode(payload.default_invoice_mode)
        except ValueError as e:
            raise HTTPException(
                400, f"default_invoice_mode inválido: {payload.default_invoice_mode!r}"
            ) from e
    if payload.auto_invoice_max_amount_eur is not None:
        cfg.auto_invoice_max_amount_eur = payload.auto_invoice_max_amount_eur
    if payload.default_carrier_id is not None:
        cfg.default_carrier_id = payload.default_carrier_id or None
    if payload.factusol_default_ejercicio is not None:
        cfg.factusol_default_ejercicio = payload.factusol_default_ejercicio or None
    if payload.factusol_live is not None:
        cfg.factusol_live = payload.factusol_live
    # C-2: serie de facturación — se reescribe el blob completo con lo que
    # llegue, conservando la parte que el PATCH no toque.
    if (payload.factusol_series_default is not None
            or payload.factusol_series_by_source is not None
            or payload.factusol_ref_prefix_by_store is not None
            or payload.factusol_series_names is not None
            or payload.factusol_estpcl_invoiced is not None
            or payload.factusol_estpre_accepted is not None
            or payload.factusol_estalb_invoiced is not None
            or payload.factusol_estfac_cobrada is not None
            or payload.factusol_estfac_pendiente is not None
            or payload.factusol_auto_mark_paid_when_order_paid is not None
            or payload.factusol_companies is not None
            or payload.factusol_pickup_warehouses is not None
            or payload.factusol_invoice_email_templates is not None
            or payload.shipment_email_templates is not None
            or payload.shipment_email_from is not None
            or payload.contrapartidas is not None
            or payload.paypal_contrapartidas_by_store is not None
            or payload.shipping_origins is not None
            or payload.drive_reference_prefer_albaran is not None
            or payload.seguimiento_reconcile_enabled is not None
            or payload.seguimiento_reconcile_interval_minutes is not None
            or payload.factusol_series_abbreviations is not None
            or payload.factusol_series_abbr_variants is not None
            or payload.sat_email is not None
            or payload.factusol_series_email_from is not None
            or payload.factusol_store_email_from is not None):
        series = _series(cfg)
        # ERP · email del SAT / taller (destinatario por defecto del envío del
        # pedido). "" lo borra: sin destinatario precargado.
        if payload.sat_email is not None:
            series["sat_email"] = payload.sat_email.strip()
        # ERP-F6: lista configurable de orígenes del envío (OFI-TER-SAT).
        if payload.shipping_origins is not None:
            series["shipping_origins"] = [
                str(v).strip() for v in payload.shipping_origins if str(v).strip()
            ]
        # ERP-F6-fix2: formato de la referencia en la hoja (albarán vs web).
        if payload.drive_reference_prefer_albaran is not None:
            series["drive_reference_prefer_albaran"] = bool(
                payload.drive_reference_prefer_albaran
            )
        # Espejo (Fase 2): interruptor y cada cuánto corre el reconcile.
        if payload.seguimiento_reconcile_enabled is not None:
            series["seguimiento_reconcile_enabled"] = bool(
                payload.seguimiento_reconcile_enabled
            )
        if payload.seguimiento_reconcile_interval_minutes is not None:
            series["seguimiento_reconcile_interval_minutes"] = int(
                payload.seguimiento_reconcile_interval_minutes
            )
        # ERP-F6-fix3: abreviaturas de empresa por serie ({"2": "MQ", …}). Solo
        # se guardan las claves numéricas con valor no vacío.
        if payload.factusol_series_abbreviations is not None:
            series["series_abbreviations"] = {
                str(int(str(k).strip())): str(v).strip()
                for k, v in payload.factusol_series_abbreviations.items()
                if str(k).strip().lstrip("-").isdigit() and str(v).strip()
            }
        # ERP-F6-fix4: variantes históricas por serie ({"5": ["STR", …]}).
        if payload.factusol_series_abbr_variants is not None:
            series["series_abbr_variants"] = {
                str(int(str(k).strip())): [str(x).strip() for x in v if str(x).strip()]
                for k, v in payload.factusol_series_abbr_variants.items()
                if str(k).strip().lstrip("-").isdigit() and isinstance(v, list)
            }
        # ERP · remitente del email de factura por serie = empresa emisora
        # ({"2": "info@artisjet-printers.eu"}). Se guardan las claves numéricas
        # CON el valor vacío incluido: "" borra el default precargado de esa
        # serie (a diferencia de las abreviaturas, donde vacío se descarta).
        if payload.factusol_series_email_from is not None:
            series["series_email_from"] = {
                str(int(str(k).strip())): str(v or "").strip()
                for k, v in payload.factusol_series_email_from.items()
                if str(k).strip().lstrip("-").isdigit()
            }
        # ERP · remitente del email de factura por TIENDA ({"boprint": "…"}).
        # Como por serie: se guarda el vacío para borrar el default precargado.
        if payload.factusol_store_email_from is not None:
            series["store_email_from"] = {
                str(k).strip().lower(): str(v or "").strip()
                for k, v in payload.factusol_store_email_from.items()
                if str(k).strip()
            }
        # ERP-F5: contrapartidas de cobro (código numérico único + descripción)
        # y contrapartida PayPal por tienda. Se guardan explícitas.
        if payload.contrapartidas is not None:
            try:
                series["contrapartidas"] = validate_contrapartidas(payload.contrapartidas)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        if payload.paypal_contrapartidas_by_store is not None:
            mapping: dict[str, str] = {}
            for store, code in payload.paypal_contrapartidas_by_store.items():
                key = normalize_store(store)
                value = str(code or "").strip()
                if not key or not value:
                    continue
                if not value.isdigit():
                    raise HTTPException(400, f"contrapartida PayPal inválida para {key}: {value!r}")
                mapping[key] = normalize_code(value)
            series["paypal_contrapartidas_by_store"] = mapping
        if payload.factusol_series_default is not None:
            series["default"] = payload.factusol_series_default.strip()
        if payload.factusol_series_by_source is not None:
            # Las series vacías se descartan: «vacío = usa la por defecto».
            series["by_source"] = {
                k: v.strip()
                for k, v in payload.factusol_series_by_source.items()
                if v and v.strip()
            }
        # Prefijo de referencia FACTUSOL por tienda ({"fluxlasers": "FLE"}).
        # Vacío = se deriva del nº de pedido; se guarda en MAYÚSCULAS.
        if payload.factusol_ref_prefix_by_store is not None:
            prefixes: dict[str, str] = {}
            for store, raw_prefix in payload.factusol_ref_prefix_by_store.items():
                key = str(store or "").strip().lower()
                value = str(raw_prefix or "").strip().upper()
                if not key or not value:
                    continue
                if not REF_PREFIX_RE.match(value):
                    raise HTTPException(
                        400,
                        f"prefijo de referencia inválido para {key}: {raw_prefix!r} "
                        "(1-6 letras o dígitos, p. ej. FLE)",
                    )
                prefixes[key] = value
            series["ref_prefix_by_store"] = prefixes
        if payload.factusol_estpcl_invoiced is not None:
            series["estpcl_invoiced"] = payload.factusol_estpcl_invoiced.strip()
        # E3-B-fix3: estados de marcado del origen al convertir. Guardar ""
        # es una elección VÁLIDA (desactiva el marcado de ese tipo).
        if payload.factusol_estpre_accepted is not None:
            series["estpre_accepted"] = payload.factusol_estpre_accepted.strip()
        if payload.factusol_estalb_invoiced is not None:
            series["estalb_invoiced"] = payload.factusol_estalb_invoiced.strip()
        # ERP-F3: estado de cobro de facturas (guardar "" desactiva el marcado)
        # + auto-marcado al emitir un pedido ya pagado.
        if payload.factusol_estfac_cobrada is not None:
            series["estfac_cobrada"] = payload.factusol_estfac_cobrada.strip()
        if payload.factusol_estfac_pendiente is not None:
            series["estfac_pendiente"] = payload.factusol_estfac_pendiente.strip()
        if payload.factusol_auto_mark_paid_when_order_paid is not None:
            series["auto_mark_paid_when_order_paid"] = bool(
                payload.factusol_auto_mark_paid_when_order_paid
            )
        # ERP-E4: identidad fiscal de las empresas. El PATCH llega con el
        # dict COMPLETO tal como lo sirvió el GET (ya fusionado con los
        # defaults) — se guarda explícito, así los valores no cambian si un
        # día cambiaran los defaults del código. `logo` es de solo lectura.
        if payload.factusol_companies is not None:
            series["companies"] = {
                str(serie): {
                    k: v for k, v in (comp or {}).items() if k != "logo"
                }
                for serie, comp in payload.factusol_companies.items()
            }
        # E4-fix1: almacenes de recogida (albarán de devolución).
        if payload.factusol_pickup_warehouses is not None:
            series["pickup_warehouses"] = [
                {"nombre": str(w.get("nombre") or "").strip(),
                 "direccion": str(w.get("direccion") or "").strip()}
                for w in payload.factusol_pickup_warehouses
                if str(w.get("direccion") or "").strip()
            ]
        # F1: plantillas del email de factura por idioma (solo subject/body).
        if payload.factusol_invoice_email_templates is not None:
            series["invoice_email_templates"] = {
                str(lang): {
                    "subject": str((tpl or {}).get("subject") or "").strip(),
                    "body": str((tpl or {}).get("body") or "").strip(),
                }
                for lang, tpl in payload.factusol_invoice_email_templates.items()
            }
        if payload.shipment_email_templates is not None:
            series["shipment_email_templates"] = {
                str(lang): {
                    "subject": str((tpl or {}).get("subject") or "").strip(),
                    "body": str((tpl or {}).get("body") or "").strip(),
                }
                for lang, tpl in payload.shipment_email_templates.items()
            }
        if payload.shipment_email_from is not None:
            series["shipment_email_from"] = {
                key: str(payload.shipment_email_from.get(key) or "").strip()
                for key in ("es", "otros")
            }
        if payload.factusol_series_names is not None:
            # ERP-E2: {"5": "Streamtec", …}. Claves como string por JSON.
            series["names"] = {
                str(k).strip(): v.strip()
                for k, v in payload.factusol_series_names.items()
                if v and v.strip()
            }
        cfg.factusol_series_json = json.dumps(series)
    # ERP-F6: credenciales de la cuenta de servicio de Drive. Se validan y se
    # CIFRAN; el contenido no aparece en logs, errores ni respuestas. "" borra.
    if payload.drive_service_account_json is not None:
        raw = payload.drive_service_account_json.strip()
        if not raw:
            cfg.drive_service_account_json_encrypted = None
        else:
            try:
                parse_service_account_json(raw)
            except DriveConfigError as e:
                raise HTTPException(400, str(e)) from e
            cfg.drive_service_account_json_encrypted = encrypt(raw)
    if payload.drive_spreadsheet_id is not None:
        cfg.drive_spreadsheet_id = payload.drive_spreadsheet_id.strip() or None
    _audit_settings(session, current_user)
    session.commit()
    return {**_serialise_settings(cfg, session), "can_edit": _can_edit(current_user)}


def _can_edit(user: User) -> bool:
    return user.role in ERP_ADMIN_ROLES


def _audit_settings(session: Session, actor: User) -> None:
    try:
        record_event(
            session, action="erp.settings_updated", target_type="erp_settings",
            target_id=ERP_SETTINGS_SINGLETON_ID, actor=actor,
        )
    except Exception:  # noqa: BLE001
        pass


# --- Lote 2 · PR-2 · Ajustes ERP: ejemplos en vivo ----------------------------
#
# Cada ajuste enseña al lado lo que va a pasar. Las plantillas del email de
# factura se ven rellenas con datos de muestra («Ver ejemplo») y se pueden
# enviar a uno mismo («Enviarme una prueba»); los prefijos de referencia
# enseñan la siguiente referencia que se compondrá con ellos.


class TemplatePreviewIn(BaseModel):
    lang: str = "es"
    #: Asunto / cuerpo tal como se están escribiendo (aún sin guardar). Vacío
    #: o ausente = la plantilla guardada (o la por defecto del idioma).
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=10000)


class TemplateTestIn(TemplatePreviewIn):
    #: Destinatario de la prueba. Ausente = el email del propio usuario.
    to: str | None = Field(default=None, max_length=255)


def _default_serie(session: Session) -> int | None:
    """Serie por defecto configurada (`factusol_series_json.default`), o None."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    raw = str(series_config(session).get("default") or "").strip()
    return int(raw) if raw.isdigit() else None


def _sample_sender(session: Session, user: User) -> tuple[str, str, str | None]:
    """Remitente con el que saldría la prueba: `(alias, origen, tienda|serie)`.
    Mismo orden que el envío real pero sin pedido: el remitente de la SERIE
    por defecto; si no tiene, el de la primera tienda Woo con remitente
    propio; si no, el alias por defecto del usuario. Alias vacío = no hay
    ninguno."""
    from app.erp.invoice_email import (  # noqa: PLC0415
        default_from_alias,
        series_from_alias,
        store_from_alias,
    )

    serie = _default_serie(session)
    serie_alias = series_from_alias(session, serie)
    if serie_alias:
        return serie_alias, "serie", str(serie)
    for store in _woocommerce_stores(session):
        alias = store_from_alias(session, store["slug"])
        if alias:
            return alias, "tienda", store["slug"]
    return default_from_alias(session, user) or "", "usuario", None


@router.post("/settings/invoice-email/preview")
def preview_invoice_email_template(
    payload: TemplatePreviewIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Plantilla del idioma rellena con datos de MUESTRA (cliente, nº de
    factura, nº de pedido y referencia ficticios), con la misma sustitución
    que el envío real. Si llegan `subject`/`body`, se usan ellos (lo que se
    está escribiendo); si no, la plantilla guardada. No envía nada."""
    from app.erp.invoice_email import (  # noqa: PLC0415
        SAMPLE_INVOICE_EMAIL,
        render_sample_invoice_email,
    )

    sample = render_sample_invoice_email(
        session, lang=payload.lang, subject=payload.subject, body=payload.body,
    )
    alias, source, scope = _sample_sender(session, current_user)
    return {
        **sample,
        # Desde dónde saldría (para enseñarlo en el ejemplo): tienda / serie /
        # usuario, como en el envío real.
        "from_alias_example": alias,
        "from_alias_source": source,
        "from_alias_scope": scope,
        "sample": dict(SAMPLE_INVOICE_EMAIL),
    }


_SENDER_PROBLEMS: dict[str, str] = {
    "sin_alias": "No hay ningún remitente configurado ni alias de envío propio.",
    "alias_not_allowed": (
        "El remitente no está en tus preferencias de envío ni es un remitente "
        "configurado en Ajustes ERP."
    ),
    "not_in_gmail": (
        "El remitente no es un «enviar como» verificado de la cuenta de Gmail."
    ),
    "gmail_unavailable": (
        "No se pudo comprobar el remitente en Gmail (desconectado o sin permiso)."
    ),
}


@router.post("/settings/invoice-email/test-send", status_code=201)
def send_invoice_email_template_test(
    payload: TemplateTestIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Envía por Gmail la plantilla rellena con los datos de muestra al
    propio usuario (o a `to`), desde el remitente configurado para la serie
    por defecto / primera tienda con remitente (validado como send-as; si no
    se puede usar, 403 con el motivo). Sin PDF: es una prueba del texto. El
    asunto va precedido de «[Prueba]» para que no se confunda en la bandeja."""
    from app.erp.invoice_email import (  # noqa: PLC0415
        check_sender_alias,
        render_sample_invoice_email,
    )
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    to = (payload.to or "").strip() or (current_user.email or "").strip()
    if "@" not in to or " " in to:
        raise HTTPException(400, f"Destinatario inválido: {to!r}")
    sample = render_sample_invoice_email(
        session, lang=payload.lang, subject=payload.subject, body=payload.body,
    )
    alias, source, scope = _sample_sender(session, current_user)
    check = (
        check_sender_alias(session, current_user, alias)
        if alias else {"ok": False, "reason": "sin_alias"}
    )
    if not check.get("ok"):
        reason = str(check.get("reason") or "alias_not_allowed")
        raise HTTPException(403, {
            "code": "alias_not_allowed",
            "reason": reason,
            "from_alias": alias,
            "detail": (
                f"No se puede enviar desde {alias or '(sin remitente)'}: "
                f"{_SENDER_PROBLEMS.get(reason, reason)}"
            ),
        })
    subject = f"[Prueba] {sample['subject']}"
    message = gmail_service.send_email(
        session,
        sender_user_id=current_user.id,
        from_alias=alias,
        from_name=None,
        to=[to],
        cc=None, bcc=None,
        subject=subject,
        body_html=sample["body_html"],
        body_text=sample["body_text"],
        contact_id=None,
    )
    try:
        record_event(
            session, action="erp.settings_template_test_sent",
            target_type="erp_settings", target_id=ERP_SETTINGS_SINGLETON_ID,
            actor=current_user,
            message=(
                f"Prueba de la plantilla del email de factura ({sample['lang']}) "
                f"enviada a {to} desde {alias}"
            ),
            metadata={
                "lang": sample["lang"], "to": to, "from_alias": alias,
                "from_alias_source": source, "message_id": message.id,
            },
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass
    session.commit()
    return {
        "sent": True,
        "to": to,
        "lang": sample["lang"],
        "subject": subject,
        "from_alias": alias,
        "from_alias_source": source,
        "from_alias_scope": scope,
        "message_id": message.id,
    }


def _store_next_number(session: Session, store_id: str) -> int | None:
    """Siguiente nº de pedido Woo de esa tienda según lo que YA conocemos:
    el mayor número de los últimos pedidos + 1. None si no hay ninguno (el
    nº real lo pone WooCommerce; esto es solo para el ejemplo)."""
    from app.erp.models import Order  # noqa: PLC0415

    rows = session.scalars(
        select(Order.order_number).where(Order.store_id == store_id)
        .order_by(Order.created_at.desc()).limit(50)
    ).all()
    top: int | None = None
    for number in rows:
        suffix = str(number or "").split("-")[-1]
        if suffix.isdigit():
            top = max(top or 0, int(suffix))
    return top + 1 if top is not None else None


@router.get("/settings/next-references")
def next_references(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Lo que se compondrá con los ajustes actuales: el siguiente nº de
    pedido manual y, por tienda Woo, el prefijo efectivo (metadata de la
    cuenta → configurado → derivado) con la siguiente referencia de ejemplo.
    El siguiente nº de pedido web lo decide WooCommerce, así que el ejemplo
    usa el último conocido de esa tienda + 1 (o 1 si no hay ninguno); la UI
    recompone la referencia en vivo con el prefijo que se esté escribiendo."""
    from app.erp.api.orders import _next_manual_number  # noqa: PLC0415
    from app.integrations.factusol.service import _compose_ref  # noqa: PLC0415
    from app.models.integration_settings import (  # noqa: PLC0415
        ExternalSystem,
        IntegrationAccount,
    )

    _ = current_user
    configured = configured_ref_prefixes(session)
    accounts = {
        a.account_id: a
        for a in session.scalars(
            select(IntegrationAccount)
            .where(IntegrationAccount.system == ExternalSystem.WOOCOMMERCE)
        ).all()
    }
    stores: list[dict[str, Any]] = []
    for store in _woocommerce_stores(session):
        slug = str(store["slug"] or "")
        metadata = store.get("ref_prefix_metadata")
        if metadata:
            prefix, source = str(metadata), "cuenta"
        elif configured.get(slug.lower()):
            prefix, source = configured[slug.lower()], "ajustes"
        else:
            prefix, source = str(store.get("derived_ref_prefix") or ""), "derivado"
        account = accounts.get(slug)
        known_next = _store_next_number(session, account.id) if account is not None else None
        next_number = known_next or 1
        stores.append({
            "slug": slug,
            "label": store["label"],
            "prefix": prefix,
            "prefix_source": source,
            "next_number": next_number,
            # True si el nº sale de pedidos ya conocidos; False = nunca hubo
            # ninguno y se enseña 000001.
            "next_number_known": known_next is not None,
            "example_ref": _compose_ref(f"{slug.upper()[:6]}-{next_number}", prefix),
        })
    return {"manual_next": _next_manual_number(session), "stores": stores}


# --- aviso de envío al cliente: ejemplo y prueba de la plantilla -----------------


@router.post("/settings/shipment-email/preview")
def preview_shipment_email_template(
    payload: TemplatePreviewIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Plantilla del aviso de envío rellena con datos de MUESTRA (cliente, nº
    de pedido, tracking, enlace y agencia ficticios). No envía nada. El
    remitente de ejemplo es el de un pedido manual en ese idioma."""
    from app.erp.shipment_email import (  # noqa: PLC0415
        MANUAL_FROM_KEY,
        SAMPLE_SHIPMENT_EMAIL,
        manual_from_config,
        render_sample,
    )
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    _ = current_user
    sample = render_sample(session, lang=payload.lang, subject=payload.subject,
                           body=payload.body)
    manual = manual_from_config(series_config(session).get(MANUAL_FROM_KEY))
    return {
        **sample,
        "from_alias_example": manual["es"] if sample["lang"] == "es" else manual["otros"],
        "from_alias_source": "idioma",
        "from_alias_scope": None,
        "sample": dict(SAMPLE_SHIPMENT_EMAIL),
    }


@router.post("/settings/shipment-email/test-send", status_code=201)
def send_shipment_email_template_test(
    payload: TemplateTestIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Envía al propio usuario (o a `to`) la plantilla del aviso de envío con
    datos de muestra, desde el remitente de un pedido manual en ese idioma
    (validado como «enviar como» de Gmail). Asunto con «[Prueba]»."""
    from app.erp.invoice_email import check_sender_alias  # noqa: PLC0415
    from app.erp.shipment_email import (  # noqa: PLC0415
        MANUAL_FROM_KEY,
        manual_from_config,
        render_sample,
    )
    from app.integrations.factusol.service import series_config  # noqa: PLC0415
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    to = (payload.to or "").strip() or (current_user.email or "").strip()
    if "@" not in to or " " in to:
        raise HTTPException(400, f"Destinatario inválido: {to!r}")
    sample = render_sample(session, lang=payload.lang, subject=payload.subject,
                           body=payload.body)
    manual = manual_from_config(series_config(session).get(MANUAL_FROM_KEY))
    alias = manual["es"] if sample["lang"] == "es" else manual["otros"]
    check = check_sender_alias(session, current_user, alias)
    if not check.get("ok"):
        reason = str(check.get("reason") or "alias_not_allowed")
        raise HTTPException(403, {
            "code": "alias_not_allowed", "reason": reason, "from_alias": alias,
            "detail": f"No se puede enviar desde {alias}: {_SENDER_PROBLEMS.get(reason, reason)}",
        })
    subject = f"[Prueba] {sample['subject']}"
    message = gmail_service.send_email(
        session, sender_user_id=current_user.id, from_alias=alias, from_name=None,
        to=[to], cc=None, bcc=None, subject=subject,
        body_html=sample["body_html"], body_text=sample["body_text"], contact_id=None,
    )
    try:
        record_event(
            session, action="erp.settings_template_test_sent",
            target_type="erp_settings", target_id=ERP_SETTINGS_SINGLETON_ID,
            actor=current_user,
            message=(f"Prueba de la plantilla del aviso de envío ({sample['lang']}) "
                     f"enviada a {to} desde {alias}"),
            metadata={"lang": sample["lang"], "to": to, "from_alias": alias,
                      "kind": "shipment", "message_id": message.id},
        )
    except Exception:  # noqa: BLE001 — audit nunca bloquea
        pass
    session.commit()
    return {"sent": True, "to": to, "lang": sample["lang"], "subject": subject,
            "from_alias": alias, "from_alias_source": "idioma", "from_alias_scope": None,
            "message_id": message.id}

