"""Ofertas: presupuestar a un lead sin tocar FACTUSOL — la API.

`/api/erp/ofertas`: plantillas (crear desde un presupuesto, editar,
desactivar), ofertas (listar, crear, editar, PDF, previsualizar y enviar por
correo) y «Convertir en presupuesto». Las crea, edita y envía cualquier
comercial (`erp.proformas` y `erp.email.client`, que ya tiene); no hay
permiso nuevo.

Ningún endpoint escribe en FACTUSOL salvo `POST /{id}/convertir`, que encola
el job del worker serializado (`factusol:writes`) y solo lo pulsa una
persona. Leer (guardar una plantilla desde un presupuesto) sí lee de
FACTUSOL.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp import ofertas as svc
from app.erp.api.deps import require_email_client, require_erp_view, require_proformas
from app.erp.models import Oferta, OfertaPlantilla
from app.models.crm import Contact, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/ofertas", tags=["erp-ofertas"])


def _http(exc: svc.OfertaError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "detail": str(exc), **exc.extra})


def _oferta_o_404(session: Session, oferta_id: str) -> Oferta:
    oferta = session.get(Oferta, oferta_id)
    if oferta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "oferta_not_found", "detail": "La oferta no existe.",
        })
    return oferta


def _plantilla_o_404(session: Session, plantilla_id: str) -> OfertaPlantilla:
    plantilla = session.get(OfertaPlantilla, plantilla_id)
    if plantilla is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "plantilla_not_found", "detail": "La plantilla no existe.",
        })
    return plantilla


class LineaIn(BaseModel):
    codart: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, max_length=64)
    descripcion: str = Field(default="", max_length=svc.DESCRIPCION_MAX)
    cantidad: float = Field(default=1, ge=0)
    precio: float = Field(default=0, ge=0)
    descuento_pct: float = Field(default=0, ge=0, le=100)
    iva_pct: float = Field(default=21, ge=0, le=100)


def _lineas(items: list[LineaIn] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    try:
        return svc.normalizar_lineas([ln.model_dump() for ln in items])
    except svc.OfertaError as exc:
        raise _http(exc) from exc


# --- meta ---------------------------------------------------------------------


@router.get("/meta")
def meta_endpoint(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Lo que el editor necesita: prefijo y validez por defecto, series
    emisoras con su nombre, idiomas y estados."""
    _ = current_user
    cfg = svc.configuracion(session)
    return {
        **cfg,
        "series": [{"serie": s, "nombre": svc.nombre_emisora(session, s)}
                   for s in svc.SERIES_OFERTA],
        "idiomas": list(svc.SUPPORTED_LANGS),
        "estados": list(svc.ESTADOS),
    }


# --- plantillas -----------------------------------------------------------------


class PlantillaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=160)
    descripcion: str | None = Field(default=None, max_length=2000)
    #: Desde un presupuesto de FACTUSOL (serie + número) — solo lectura…
    serie: int | None = Field(default=None, ge=1, le=9)
    codpre: int | None = Field(default=None, ge=1)
    #: …o con las líneas escritas a mano.
    lineas: list[LineaIn] | None = None
    activa: bool = True


class PlantillaPatch(BaseModel):
    nombre: str | None = Field(default=None, max_length=160)
    descripcion: str | None = Field(default=None, max_length=2000)
    activa: bool | None = None
    lineas: list[LineaIn] | None = None


@router.get("/plantillas")
def listar_plantillas_endpoint(
    activas: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    filas = svc.listar_plantillas(session, solo_activas=activas)
    return {"items": [svc.plantilla_como_dict(p, con_lineas=False) for p in filas]}


@router.post("/plantillas", status_code=status.HTTP_201_CREATED)
def crear_plantilla_endpoint(
    payload: PlantillaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """«Guardar como plantilla de oferta»: desde un presupuesto existente
    (copia sus líneas con sus artículos, leyendo F_PRE/F_LPS) o con líneas a
    mano. No escribe en FACTUSOL."""
    try:
        if payload.serie is not None and payload.codpre is not None:
            from app.erp.api.factusol import (  # noqa: PLC0415
                _client_and_ejercicio,
                _factusol_gateway_error,
            )
            from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

            client, ejercicio = _client_and_ejercicio(session)
            try:
                plantilla = svc.plantilla_desde_presupuesto(
                    session, client, serie=payload.serie, codpre=payload.codpre,
                    ejercicio=ejercicio, nombre=payload.nombre, usuario=current_user,
                    descripcion=payload.descripcion,
                )
            except FactusolError as exc:
                raise _factusol_gateway_error(exc, "quote_read_failed") from exc
            plantilla.activa = payload.activa
        else:
            plantilla = svc.crear_plantilla(
                session, nombre=payload.nombre, lineas=_lineas(payload.lineas) or [],
                usuario=current_user, descripcion=payload.descripcion, activa=payload.activa,
            )
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.plantilla_como_dict(plantilla)


@router.get("/plantillas/{plantilla_id}")
def get_plantilla_endpoint(
    plantilla_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    return svc.plantilla_como_dict(_plantilla_o_404(session, plantilla_id))


@router.patch("/plantillas/{plantilla_id}")
def patch_plantilla_endpoint(
    plantilla_id: str,
    payload: PlantillaPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    _ = current_user
    plantilla = _plantilla_o_404(session, plantilla_id)
    try:
        svc.actualizar_plantilla(
            session, plantilla, nombre=payload.nombre, descripcion=payload.descripcion,
            activa=payload.activa, lineas=_lineas(payload.lineas),
        )
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.plantilla_como_dict(plantilla)


# --- ofertas ----------------------------------------------------------------------


class OfertaIn(BaseModel):
    contact_id: str = Field(min_length=1, max_length=36)
    serie: int = Field(ge=1, le=9)
    plantilla_id: str | None = Field(default=None, max_length=36)
    #: Las líneas ya retocadas en pantalla; sin ellas, las de la plantilla.
    lineas: list[LineaIn] | None = None
    idioma: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")
    con_iva: bool = True
    validez_dias: int | None = Field(default=None, ge=svc.VALIDEZ_MIN, le=svc.VALIDEZ_MAX)
    fecha: date | None = None
    referencia: str | None = Field(default=None, max_length=255)
    notas: str | None = Field(default=None, max_length=2000)


class OfertaPatch(BaseModel):
    serie: int | None = Field(default=None, ge=1, le=9)
    lineas: list[LineaIn] | None = None
    idioma: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")
    con_iva: bool | None = None
    validez_dias: int | None = Field(default=None, ge=svc.VALIDEZ_MIN, le=svc.VALIDEZ_MAX)
    #: Solo si se cambia a mano; si no viene, la fecha pasa a hoy.
    fecha: date | None = None
    referencia: str | None = Field(default=None, max_length=255)
    notas: str | None = Field(default=None, max_length=2000)


@router.get("")
def listar_ofertas_endpoint(
    contact_id: str | None = Query(default=None, max_length=36),
    company_id: str | None = Query(default=None, max_length=36),
    estado: str | None = Query(default=None, pattern="^(borrador|enviada|caducada|convertida)$"),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    filas = svc.listar_ofertas(session, contact_id=contact_id, company_id=company_id,
                               estado=estado, q=q, limit=limit)
    emisoras = svc.nombres_emisoras(session)
    return {"items": [svc.oferta_como_dict(session, o, con_lineas=False, emisoras=emisoras)
                      for o in filas]}


@router.post("", status_code=status.HTTP_201_CREATED)
def crear_oferta_endpoint(
    payload: OfertaIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """La oferta nueva para un contacto: sin cliente de FACTUSOL, sin datos
    fiscales, sin escribir nada en FACTUSOL."""
    contacto = session.get(Contact, payload.contact_id)
    if contacto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "contact_not_found", "detail": "El contacto no existe.",
        })
    plantilla = None
    if payload.plantilla_id:
        plantilla = _plantilla_o_404(session, payload.plantilla_id)
    try:
        oferta = svc.crear_oferta(
            session, contacto=contacto, serie=payload.serie, usuario=current_user,
            plantilla=plantilla, lineas=_lineas(payload.lineas), idioma=payload.idioma,
            con_iva=payload.con_iva, validez_dias=payload.validez_dias, fecha=payload.fecha,
            referencia=payload.referencia, notas=payload.notas,
        )
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.oferta_como_dict(session, oferta)


@router.get("/convertir/status/{job_id}")
def convertir_status_endpoint(
    job_id: str,
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado del job de conversión (mismo contrato que las proformas)."""
    _ = current_user
    from app.erp.api.factusol import _rq_quote_status  # noqa: PLC0415

    return _rq_quote_status(job_id)


@router.get("/{oferta_id}")
def get_oferta_endpoint(
    oferta_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    _ = current_user
    return svc.oferta_como_dict(session, _oferta_o_404(session, oferta_id))


@router.patch("/{oferta_id}")
def patch_oferta_endpoint(
    oferta_id: str,
    payload: OfertaPatch,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """Editar (también enviada): la fecha pasa a hoy y la validez se recalcula,
    salvo que venga `fecha` a mano."""
    oferta = _oferta_o_404(session, oferta_id)
    try:
        svc.actualizar_oferta(
            session, oferta, usuario=current_user, lineas=_lineas(payload.lineas),
            serie=payload.serie, idioma=payload.idioma, con_iva=payload.con_iva,
            validez_dias=payload.validez_dias, fecha=payload.fecha,
            referencia=payload.referencia, notas=payload.notas,
        )
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    session.commit()
    return svc.oferta_como_dict(session, oferta)


@router.get("/{oferta_id}/pdf")
def pdf_oferta_endpoint(
    oferta_id: str,
    lang: str | None = Query(default=None, pattern="^(es|en|de|fr|nl)$"),
    bank: int | None = Query(default=None, ge=0, le=20),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    _ = current_user
    from app.erp.factusol_pdf import bank_accounts, company_for_serie  # noqa: PLC0415

    oferta = _oferta_o_404(session, oferta_id)
    selected_bank = None
    if bank is not None:
        accounts = bank_accounts(company_for_serie(session, int(oferta.serie)))
        if accounts:
            selected_bank = accounts[min(bank, len(accounts) - 1)]
    contenido = svc.pdf(session, oferta, lang=lang, bank=selected_bank)
    nombre = svc.nombre_fichero(session, oferta, lang)
    return Response(
        content=contenido, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre}"'},
    )


@router.get("/{oferta_id}/email-preview")
def email_preview_endpoint(
    oferta_id: str,
    lang: str | None = Query(default=None, pattern="^(es|en|de|fr|nl)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_email_client),
) -> dict[str, Any]:
    oferta = _oferta_o_404(session, oferta_id)
    try:
        return svc.preview_envio(session, oferta, current_user=current_user, lang_override=lang)
    except svc.OfertaError as exc:
        raise _http(exc) from exc


class OfertaEmailIn(BaseModel):
    """`confirm` OBLIGATORIO: enviar un correo a un cliente es irreversible."""

    confirm: bool = False
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = Field(min_length=1, max_length=500)
    body_text: str = Field(min_length=1)
    #: El HTML del borrador (el del asistente o el del editor); sin él, el texto.
    body_html: str | None = Field(default=None, max_length=200_000)
    lang: str = Field(pattern="^(es|en|de|fr|nl)$")
    from_alias: str = Field(min_length=3, max_length=255)
    bank: int | None = Field(default=None, ge=0, le=20)


@router.post("/{oferta_id}/email", status_code=status.HTTP_201_CREATED)
def enviar_oferta_endpoint(
    oferta_id: str,
    payload: OfertaEmailIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_email_client),
) -> dict[str, Any]:
    """Envía la oferta con el PDF adjunto por el camino de siempre (Gmail,
    remitente verificado). Nada se envía solo. Al enviar: `enviada` + fecha +
    historial + «Propuesta enviada». No toca FACTUSOL."""
    from app.erp.api.factusol import _require_sender_alias  # noqa: PLC0415
    from app.erp.factusol_pdf import bank_accounts, company_for_serie  # noqa: PLC0415
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        GmailScopeMissingError,
    )

    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "El envío requiere confirmación explícita.",
        })
    to = [t.strip() for t in payload.to if t and t.strip()]
    if not to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_recipient",
            "detail": "Falta el destinatario: elige un contacto o escribe una dirección.",
        })
    cc = [t.strip() for t in payload.cc if t and t.strip()]
    bcc = [t.strip() for t in payload.bcc if t and t.strip()]
    _require_sender_alias(session, current_user, payload.from_alias)
    oferta = _oferta_o_404(session, oferta_id)
    selected_bank = None
    if payload.bank is not None:
        accounts = bank_accounts(company_for_serie(session, int(oferta.serie)))
        if accounts:
            selected_bank = accounts[min(payload.bank, len(accounts) - 1)]
    try:
        return svc.enviar(
            session, oferta, current_user=current_user, to=to, cc=cc, bcc=bcc,
            subject=payload.subject, body_text=payload.body_text, body_html=payload.body_html,
            lang=payload.lang, from_alias=payload.from_alias, bank=selected_bank,
        )
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    except (GmailNotConnectedError, GmailScopeMissingError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "gmail_unavailable", "detail": str(exc)[:200],
        }) from exc


# --- convertir en presupuesto de FACTUSOL ---------------------------------------------


@router.post("/{oferta_id}/convertir", status_code=status.HTTP_202_ACCEPTED)
def convertir_oferta_endpoint(
    oferta_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_proformas),
) -> dict[str, Any]:
    """«Convertir en presupuesto»: el ÚNICO camino de las ofertas que escribe
    en FACTUSOL, y solo cuando una persona lo pulsa. Antes de encolar se
    comprueba aquí mismo que hay empresa vinculada (si no, 409
    `company_not_linked` → la pantalla abre el flujo de crear o vincular la
    empresa, el mismo de #502) y que todas las líneas llevan artículo (422
    `lineas_sin_articulo`). El job corre en `factusol:writes`; el frontend
    sondea `/convertir/status/{job_id}`."""
    from app.integrations.factusol.jobs import enqueue_convert_oferta  # noqa: PLC0415

    oferta = _oferta_o_404(session, oferta_id)
    try:
        svc.prechequeo_conversion(session, oferta)
    except svc.OfertaError as exc:
        raise _http(exc) from exc
    job_id = enqueue_convert_oferta(oferta.id, actor_user_id=current_user.id)
    logger.info("ofertas: conversión encolada %s job=%s por %s", oferta.numero, job_id,
                current_user.email)
    return {"job_id": job_id, "oferta_id": oferta.id, "numero": oferta.numero}
