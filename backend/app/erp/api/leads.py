"""Respuesta a leads · Fase 1 — API (capacidad `erp.config`, la misma que
Configuración ERP, donde vive la pantalla).

- GET  /api/erp/leads/clasificaciones?dias=15     la lista corregible
- POST /api/erp/leads/clasificaciones/{id}/corregir
- POST /api/erp/leads/en-seco {dias, limite}      qué habría hecho, sin escribir
- POST /api/erp/leads/workflow                     crea el workflow en borrador

La configuración (interruptor, tope, umbral, mapa, remitentes, antigüedad,
ventana) va por `GET/PATCH /api/erp/settings` (`lead_response`), con el resto
de ajustes del ERP.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.db.session import get_session
from app.erp.api.deps import require_config
from app.models.crm import Contact, User
from app.models.leads import LeadClassification
from app.services.leads import en_seco, registro
from app.services.leads import workflow as workflow_leads
from app.services.leads.clasificador import IDIOMAS, INTERESES, etiqueta_interes
from app.services.leads.config import configuracion
from app.services.web_forms.sitios import web_de_sitio

router = APIRouter(prefix="/api/erp/leads", tags=["erp-leads"])


class CorregirIn(BaseModel):
    idioma: str | None = Field(default=None, max_length=5)
    interes: str | None = Field(default=None, max_length=40)
    es_spam: bool | None = None
    nota: str | None = Field(default=None, max_length=500)


class EnSecoIn(BaseModel):
    dias: int = Field(default=15, ge=1, le=90)
    limite: int = Field(default=en_seco.LIMITE_DEFECTO, ge=1, le=en_seco.LIMITE_MAXIMO)


def _usuario(session: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    u = session.get(User, user_id)
    return (u.full_name or u.email) if u is not None else None


def _item(session: Session, fila: LeadClassification, umbral: float) -> dict[str, Any]:
    contacto = session.get(Contact, fila.contact_id)
    contexto = fila.contexto()
    confianza = float(fila.confidence or 0.0)
    return {
        "id": fila.id,
        "contacto": {
            "id": fila.contact_id,
            "nombre": (" ".join(p for p in (contacto.first_name, contacto.last_name) if p).strip()
                       if contacto is not None else ""),
            "email": contacto.email if contacto is not None else "",
        },
        "fuente": fila.source, "referencia": fila.source_ref,
        "lead_at": fila.lead_at.isoformat() if fila.lead_at else None,
        "web": web_de_sitio(contexto.get("sitio")) if contexto.get("sitio") else None,
        "cuenta_agile": contexto.get("cuenta_agile"),
        "productos": contexto.get("productos") or [],
        "texto": (fila.input_text or "")[:400],
        "idioma": fila.language, "idioma_fuente": fila.language_source,
        "idioma_formulario": fila.form_language, "discrepancia_idioma": fila.language_mismatch,
        "interes": fila.interest, "interes_texto": etiqueta_interes(fila.interest),
        "interes_fuente": fila.interest_source,
        "es_spam": fila.is_spam, "confianza": confianza, "bajo_umbral": confianza < umbral,
        "motivo": fila.reason, "proveedor": fila.provider, "modelo": fila.model,
        "estado": fila.status, "estado_detalle": fila.status_detail,
        "plantilla": fila.template_name, "remitente": fila.sender_email,
        "borrador_id": fila.draft_id, "borrador_url": registro.url_borrador(fila.draft_id),
        "tarea_id": fila.task_id, "run_id": fila.workflow_run_id,
        "efectivo": {
            "idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
            "interes_texto": etiqueta_interes(fila.interes_efectivo),
            "es_spam": fila.es_spam_efectivo,
        },
        "correccion": {
            "corregida": fila.corregida,
            "idioma": fila.corrected_language, "interes": fila.corrected_interest,
            "es_spam": fila.corrected_is_spam, "nota": fila.correction_note,
            "por": _usuario(session, fila.corrected_by_user_id),
            "cuando": fila.corrected_at.isoformat() if fila.corrected_at else None,
        },
        "creado": fila.created_at.isoformat() if fila.created_at else None,
    }


@router.get("/clasificaciones")
def clasificaciones(
    dias: int = Query(default=15, ge=1, le=365),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Los últimos leads clasificados, con su clasificación corregible."""
    _ = current_user
    conf = configuracion(session)
    umbral = float(conf.get("umbral_confianza") or 0.0)
    filas = en_seco.clasificaciones_de(session, dias=dias)
    items = [_item(session, f, umbral) for f in filas]
    aciertos = sum(1 for f in filas if f.corregida)
    return {
        "dias": dias, "umbral_confianza": umbral, "total": len(items),
        "corregidas": aciertos, "items": items,
        "opciones": {"idiomas": list(IDIOMAS),
                     "intereses": [{"id": i, "label": etiqueta_interes(i)} for i in INTERESES]},
    }


@router.post("/clasificaciones/{clasificacion_id}/corregir")
def corregir(
    clasificacion_id: str,
    payload: CorregirIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """La corrección a mano: queda registrada (quién, cuándo, qué) y manda
    sobre la clasificación en la ficha del contacto."""
    fila = session.get(LeadClassification, clasificacion_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clasificación no encontrada.")
    if payload.idioma is not None and payload.idioma not in IDIOMAS:
        raise HTTPException(400, f"Idioma no válido: {payload.idioma!r}.")
    if payload.interes is not None and payload.interes not in INTERESES:
        raise HTTPException(400, f"Interés no válido: {payload.interes!r}.")
    if payload.idioma is None and payload.interes is None and payload.es_spam is None:
        raise HTTPException(400, "No hay nada que corregir.")
    antes = {"idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
             "es_spam": fila.es_spam_efectivo}
    if payload.idioma is not None:
        fila.corrected_language = payload.idioma
    if payload.interes is not None:
        fila.corrected_interest = payload.interes
    if payload.es_spam is not None:
        fila.corrected_is_spam = payload.es_spam
    fila.corrected_by_user_id = current_user.id
    fila.corrected_at = datetime.now(UTC)
    fila.correction_note = (payload.nota or "").strip()[:500] or None
    contacto = session.get(Contact, fila.contact_id)
    if contacto is not None:
        registro.copiar_al_contacto(contacto, fila)
    record_event(
        session, action=Action.LEAD_CLASSIFICATION_CORRECTED, target_type="contact",
        target_id=fila.contact_id, actor=current_user,
        metadata={
            "lead_classification_id": fila.id, "antes": antes,
            "despues": {"idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
                        "es_spam": fila.es_spam_efectivo},
            "nota": fila.correction_note,
        },
    )
    session.commit()
    session.refresh(fila)
    return _item(session, fila, float(configuracion(session).get("umbral_confianza") or 0.0))


@router.post("/en-seco")
def en_seco_endpoint(
    payload: EnSecoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Qué habría hecho la Fase 1 con los leads de los últimos N días. No
    escribe absolutamente nada."""
    _ = current_user
    informe = en_seco.simular(session, dias=payload.dias, limite=payload.limite)
    session.rollback()      # por si acaso: en seco es en seco
    return informe


@router.post("/workflow", status_code=status.HTTP_201_CREATED)
def crear_workflow(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Crea el workflow «Respuesta a leads (Fase 1)» en BORRADOR, con el
    pipeline «Ventas B2B» resuelto por nombre. Bart lo revisa y lo activa."""
    try:
        wf = workflow_leads.crear_workflow(session, actor_user_id=current_user.id)
    except workflow_leads.WorkflowYaExiste as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "workflow_exists", "detail": str(exc), "workflow_id": exc.workflow_id},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    session.commit()
    return {"id": wf.id, "name": wf.name, "status": wf.status.value,
            "url": f"/admin/workflows/{wf.id}"}


@router.get("/workflow")
def estado_workflow(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Si existe el workflow de la Fase 1 y en qué estado está."""
    _ = current_user
    wf = workflow_leads.existente(session)
    try:
        workflow_leads.pipeline_ventas(session)
        pipeline_ok, pipeline_aviso = True, None
    except LookupError as exc:
        pipeline_ok, pipeline_aviso = False, str(exc)
    return {
        "existe": wf is not None,
        "id": wf.id if wf else None, "status": wf.status.value if wf else None,
        "url": f"/admin/workflows/{wf.id}" if wf else None,
        "pipeline_ok": pipeline_ok, "pipeline_aviso": pipeline_aviso,
    }
