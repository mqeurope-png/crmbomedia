"""Respuesta a leads · Fase 1 — API (capacidad `erp.config`, la misma que
Configuración ERP, donde vive la pantalla).

- GET  /api/erp/leads/clasificaciones?dias=15     la lista corregible
- GET  /api/erp/leads/contactos/{contact_id}       las de un contacto (ficha)
- POST /api/erp/leads/clasificaciones/{id}/corregir
- POST /api/erp/leads/en-seco {dias, limite}      qué habría hecho, sin escribir
- POST /api/erp/leads/workflow                     crea el workflow en borrador
- GET/POST /api/erp/leads/intereses                el catálogo de intereses
- PATCH/DELETE /api/erp/leads/intereses/{codigo}   editar, desactivar, borrar

La configuración (interruptor, tope, umbral, mapa, remitentes, antigüedad,
ventana) va por `GET/PATCH /api/erp/settings` (`lead_response`), con el resto
de ajustes del ERP.

Un lead puede tener VARIOS intereses, ordenados (el primero es el principal):
cada clasificación lleva `interes`/`interes_texto` (el principal, como hasta
ahora) y `intereses`/`intereses_texto` (todos). La corrección a mano manda
`intereses` (lista en el orden elegido) o, como antes, `interes`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_viewer
from app.db.session import get_session
from app.erp.api.deps import require_config
from app.models.crm import Contact, User
from app.models.leads import LeadClassification
from app.services.leads import en_seco, intereses, registro
from app.services.leads import workflow as workflow_leads
from app.services.leads.clasificador import IDIOMAS
from app.services.leads.config import configuracion
from app.services.leads.intereses import Catalogo
from app.services.web_forms.sitios import web_de_sitio

router = APIRouter(prefix="/api/erp/leads", tags=["erp-leads"])


class CorregirIn(BaseModel):
    idioma: str | None = Field(default=None, max_length=5)
    #: Compatibilidad: un solo interés (equivale a `intereses: [interes]`).
    interes: str | None = Field(default=None, max_length=40)
    #: Todos los intereses, en el orden elegido; el primero es el principal.
    intereses: list[str] | None = Field(default=None, max_length=20)
    es_spam: bool | None = None
    nota: str | None = Field(default=None, max_length=500)


class EnSecoIn(BaseModel):
    dias: int = Field(default=15, ge=1, le=90)
    limite: int = Field(default=en_seco.LIMITE_DEFECTO, ge=1, le=en_seco.LIMITE_MAXIMO)


class InteresIn(BaseModel):
    codigo: str = Field(min_length=2, max_length=40)
    etiqueta: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=2000)
    comercial: bool = True
    orden: int | None = Field(default=None, ge=0, le=10000)


class InteresCambiosIn(BaseModel):
    etiqueta: str | None = Field(default=None, max_length=80)
    descripcion: str | None = Field(default=None, max_length=2000)
    comercial: bool | None = None
    orden: int | None = Field(default=None, ge=0, le=10000)
    activo: bool | None = None


def _usuario(session: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    u = session.get(User, user_id)
    return (u.full_name or u.email) if u is not None else None


def _opciones(catalogo: Catalogo) -> dict[str, Any]:
    """Para los desplegables de corrección: todos los intereses (los
    inactivos solo se enseñan si la clasificación ya los lleva)."""
    return {
        "idiomas": list(IDIOMAS),
        "intereses": [
            {"id": i.codigo, "label": i.etiqueta, "comercial": i.comercial, "activo": i.activo}
            for i in catalogo.todos
        ],
    }


def _item(
    session: Session, fila: LeadClassification, umbral: float, *,
    contacto: Contact | None = None, catalogo: Catalogo | None = None,
) -> dict[str, Any]:
    if contacto is None or contacto.id != fila.contact_id:
        contacto = session.get(Contact, fila.contact_id)
    if catalogo is None:
        catalogo = intereses.cargar(session)
    contexto = fila.contexto()
    confianza = float(fila.confidence or 0.0)
    lista = fila.intereses
    efectivos = fila.intereses_efectivos
    corregidos = fila.intereses_corregidos
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
        # El principal (como siempre) y todos, en orden (códigos y etiquetas).
        "interes": fila.interest, "interes_texto": catalogo.etiqueta(fila.interest),
        "intereses": lista, "intereses_texto": catalogo.texto(lista),
        "intereses_etiquetas": catalogo.etiquetas(lista),
        "interes_fuente": fila.interest_source,
        "es_spam": fila.is_spam, "confianza": confianza, "bajo_umbral": confianza < umbral,
        "motivo": fila.reason, "proveedor": fila.provider, "modelo": fila.model,
        "estado": fila.status, "estado_detalle": fila.status_detail,
        "plantilla": fila.template_name, "remitente": fila.sender_email,
        "borrador_id": fila.draft_id, "borrador_url": registro.url_borrador(fila.draft_id),
        "tarea_id": fila.task_id, "run_id": fila.workflow_run_id,
        # Para la ficha del contacto: la consulta entera y el contexto de
        # entrada (web, formulario, idioma del formulario, productos, país…).
        "texto_completo": fila.input_text or "",
        "contexto": contexto,
        "idioma_discrepancia_texto": (
            f"el formulario era {fila.form_language.upper()} pero el texto está en "
            f"{(fila.language or '?').upper()}"
            if fila.language_mismatch and fila.form_language else None
        ),
        "efectivo": {
            "idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
            "interes_texto": catalogo.etiqueta(fila.interes_efectivo),
            "intereses": efectivos, "intereses_texto": catalogo.texto(efectivos),
            "intereses_etiquetas": catalogo.etiquetas(efectivos),
            "es_spam": fila.es_spam_efectivo,
        },
        "correccion": {
            "corregida": fila.corregida,
            "idioma": fila.corrected_language, "interes": fila.corrected_interest,
            "intereses": corregidos,
            "es_spam": fila.corrected_is_spam, "nota": fila.correction_note,
            "por": _usuario(session, fila.corrected_by_user_id),
            "cuando": fila.corrected_at.isoformat() if fila.corrected_at else None,
        },
        "creado": fila.created_at.isoformat() if fila.created_at else None,
    }


@router.get("/clasificaciones")
def clasificaciones(
    dias: int = Query(default=15, ge=1, le=90),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Los últimos leads clasificados, con su clasificación corregible."""
    _ = current_user
    conf = configuracion(session)
    catalogo = intereses.cargar(session)
    umbral = float(conf.get("umbral_confianza") or 0.0)
    filas = en_seco.clasificaciones_de(session, dias=dias)
    items = [_item(session, f, umbral, catalogo=catalogo) for f in filas]
    aciertos = sum(1 for f in filas if f.corregida)
    return {
        "dias": dias, "umbral_confianza": umbral, "total": len(items),
        "corregidas": aciertos, "items": items,
        "opciones": _opciones(catalogo),
    }


@router.get("/contactos/{contact_id}")
def clasificaciones_de_contacto(
    contact_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_viewer),
) -> dict[str, Any]:
    """Todas las clasificaciones de un contacto, la última arriba, para la
    ficha (recuadro del Resumen y pestaña «Análisis IA»). Lo ve quien ve la
    ficha; corregir sigue siendo cosa de `erp.config`."""
    _ = current_user
    contacto = session.get(Contact, contact_id)
    if contacto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contacto no encontrado.")
    umbral = float(configuracion(session).get("umbral_confianza") or 0.0)
    catalogo = intereses.cargar(session)
    filas = list(session.scalars(
        select(LeadClassification)
        .where(LeadClassification.contact_id == contact_id)
        .order_by(
            func.coalesce(LeadClassification.lead_at, LeadClassification.created_at).desc(),
            LeadClassification.created_at.desc(),
        )
    ))
    return {
        "umbral_confianza": umbral, "total": len(filas),
        "items": [_item(session, f, umbral, contacto=contacto, catalogo=catalogo) for f in filas],
        "opciones": _opciones(catalogo),
    }


def _intereses_corregidos(payload: CorregirIn, catalogo: Catalogo) -> list[str] | None:
    """La lista corregida, limpia; `None` si la corrección no toca el
    interés. 400 si trae un código que no existe o se queda vacía."""
    if payload.intereses is None and payload.interes is None:
        return None
    crudos = list(payload.intereses) if payload.intereses is not None else [payload.interes]
    lista = intereses.normalizar_lista(crudos)
    desconocidos = [c for c in lista if not catalogo.conoce(c)]
    if desconocidos:
        raise HTTPException(400, f"Interés no válido: {', '.join(desconocidos)}.")
    if not lista:
        raise HTTPException(400, "La corrección necesita al menos un interés.")
    return lista


@router.post("/clasificaciones/{clasificacion_id}/corregir")
def corregir(
    clasificacion_id: str,
    payload: CorregirIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """La corrección a mano: queda registrada (quién, cuándo, qué) y manda
    sobre la clasificación en la ficha del contacto. Los intereses van en
    lista y en el orden elegido: el primero es el principal."""
    fila = session.get(LeadClassification, clasificacion_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clasificación no encontrada.")
    if payload.idioma is not None and payload.idioma not in IDIOMAS:
        raise HTTPException(400, f"Idioma no válido: {payload.idioma!r}.")
    catalogo = intereses.cargar(session)
    lista = _intereses_corregidos(payload, catalogo)
    if payload.idioma is None and lista is None and payload.es_spam is None:
        raise HTTPException(400, "No hay nada que corregir.")
    antes = {"idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
             "intereses": fila.intereses_efectivos, "es_spam": fila.es_spam_efectivo}
    if payload.idioma is not None:
        fila.corrected_language = payload.idioma
    if lista is not None:
        fila.fijar_intereses_corregidos(lista)
    if payload.es_spam is not None:
        fila.corrected_is_spam = payload.es_spam
    fila.corrected_by_user_id = current_user.id
    fila.corrected_at = datetime.now(UTC)
    fila.correction_note = (payload.nota or "").strip()[:500] or None
    # A la ficha del contacto solo pasa la corrección de su ÚLTIMO lead: un
    # lead de julio corregido no debe pisar lo que dijo el de octubre.
    contacto = session.get(Contact, fila.contact_id)
    ultima = registro.ultima_clasificacion(session, fila.contact_id)
    if contacto is not None and (ultima is None or ultima.id == fila.id):
        registro.copiar_al_contacto(contacto, fila)
    record_event(
        session, action=Action.LEAD_CLASSIFICATION_CORRECTED, target_type="contact",
        target_id=fila.contact_id, actor=current_user,
        metadata={
            "lead_classification_id": fila.id, "antes": antes,
            "despues": {"idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
                        "intereses": fila.intereses_efectivos,
                        "es_spam": fila.es_spam_efectivo},
            "nota": fila.correction_note,
        },
    )
    session.commit()
    session.refresh(fila)
    return _item(
        session, fila, float(configuracion(session).get("umbral_confianza") or 0.0),
        catalogo=catalogo,
    )


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


# --- el catálogo de intereses ------------------------------------------------


def _interes_con_uso(session: Session, interes: intereses.Interes) -> dict[str, Any]:
    clasificaciones, en_mapa = intereses.en_uso(session, interes.codigo)
    return {**interes.como_dict(), "en_uso": {"clasificaciones": clasificaciones,
                                              "en_mapa": en_mapa}}


def _lista_intereses(session: Session) -> dict[str, Any]:
    catalogo = intereses.cargar(session)
    return {"items": [_interes_con_uso(session, i) for i in catalogo.todos]}


def _auditar_interes(
    session: Session, actor: User, codigo: str, accion: str, cambios: dict[str, Any] | None,
) -> None:
    record_event(
        session, action=Action.LEAD_INTEREST_CHANGED, target_type="lead_interest",
        target_id=codigo, actor=actor, metadata={"accion": accion, "cambios": cambios or {}},
    )


@router.get("/intereses")
def listar_intereses(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """El catálogo entero (activos e inactivos), con cuántas clasificaciones
    y filas del mapa usan cada uno: la pantalla no ofrece borrar lo que está
    en uso."""
    _ = current_user
    return _lista_intereses(session)


@router.post("/intereses", status_code=status.HTTP_201_CREATED)
def crear_interes(
    payload: InteresIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Un interés nuevo: entra en el clasificador (el prompt se compone con
    el catálogo en cada clasificación) sin desplegar nada."""
    try:
        fila = intereses.crear(
            session, codigo=payload.codigo, etiqueta=payload.etiqueta,
            descripcion=payload.descripcion or "", comercial=payload.comercial,
            orden=payload.orden,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar_interes(session, current_user, fila.code, "alta", payload.model_dump())
    session.commit()
    catalogo = intereses.cargar(session)
    interes = next(i for i in catalogo.todos if i.codigo == fila.code)
    return _interes_con_uso(session, interes)


@router.patch("/intereses/{codigo}")
def editar_interes(
    codigo: str,
    payload: InteresCambiosIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> dict[str, Any]:
    """Etiqueta, descripción, comercial, orden y activo. El código no cambia:
    es lo que llevan las clasificaciones. Desactivar lo quita de la lista
    del clasificador y de los desplegables; lo clasificado sigue legible."""
    cambios = payload.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que cambiar.")
    try:
        fila = intereses.actualizar(session, codigo, cambios)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _auditar_interes(session, current_user, fila.code, "cambio", cambios)
    session.commit()
    catalogo = intereses.cargar(session)
    interes = next(i for i in catalogo.todos if i.codigo == fila.code)
    return _interes_con_uso(session, interes)


@router.delete("/intereses/{codigo}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_interes(
    codigo: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_config),
) -> Response:
    """Solo si nada lo usa; con clasificaciones o filas del mapa, 409 (lo
    suyo es desactivarlo)."""
    try:
        intereses.borrar(session, codigo)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except intereses.InteresEnUso as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "interes_en_uso", "detail": str(exc)},
        ) from exc
    _auditar_interes(session, current_user, codigo, "baja", None)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
