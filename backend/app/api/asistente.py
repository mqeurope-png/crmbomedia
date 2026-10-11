"""«Ayúdame a contestar» — API del asistente de respuesta.

- POST /api/emails/threads/{thread_id}/asistente              genera un borrador
- POST /api/emails/threads/{thread_id}/asistente/{id}/descartar

Puede pedirlo cualquier usuario que pueda ver el hilo (la misma regla que el
detalle del hilo). El borrador vuelve con destinatario, asunto y texto para
abrir el compositor; el envío lo hace la persona desde el compositor
(`POST /api/emails/send` con `asistente_borrador_id`, que anota el resultado).
No hay aquí ningún camino que envíe correo.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import EmailMessage, EmailThread, User, UserRole
from app.models.ia import ALCANCE_CONTACTO, ALCANCES, ESTADO_GENERADO, BorradorAsistente
from app.services import llm
from app.services.asistente import borradores, redactor
from app.services.email_aliases import thread_is_visible

router = APIRouter(prefix="/api/emails/threads/{thread_id}/asistente", tags=["asistente"])


class GenerarIn(BaseModel):
    alcance: str = Field(default=ALCANCE_CONTACTO, max_length=10)
    #: El borrador anterior, al pulsar «Regenerar»: queda como regenerado.
    regenerar_de: str | None = Field(default=None, max_length=36)


def _hilo_visible(session: Session, thread_id: str, user: User) -> EmailThread:
    thread = session.scalar(
        select(EmailThread).where(EmailThread.id == thread_id)
        .options(selectinload(EmailThread.messages).selectinload(EmailMessage.labels))
    )
    if thread is None:
        raise not_found("EmailThread")
    privileged = user.role in (UserRole.ADMIN, UserRole.MANAGER)
    if not privileged and not thread_is_visible(session, user, thread):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este hilo.")
    return thread


@router.post("", status_code=status.HTTP_201_CREATED)
def generar(
    thread_id: str,
    payload: GenerarIn,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Genera el borrador y lo devuelve con lo que entró en el contexto. No
    envía nada: la persona lo revisa en el compositor."""
    if payload.alcance not in ALCANCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Alcance desconocido: {payload.alcance}.")
    thread = _hilo_visible(session, thread_id, current_user)
    if not thread.messages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El hilo no tiene mensajes.")
    try:
        borrador = redactor.generar(
            session, thread=thread, usuario=current_user, alcance=payload.alcance,
            regenerar_de=payload.regenerar_de,
        )
    except llm.LLMNotConfiguredError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except llm.LLMRateLimitError as exc:
        session.commit()                 # la llamada fallida queda anotada
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "La IA está saturada ahora mismo; vuelve a intentarlo en un minuto.") from exc
    except llm.LLMError as exc:
        session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"La IA no ha podido redactar: {exc}") from exc
    record_event(
        session, action=Action.IA_BORRADOR_GENERADO, target_type="email_thread",
        target_id=thread.id, actor=current_user,
        metadata={
            "borrador_id": borrador.id, "modelo": borrador.modelo,
            "tokens_entrada": borrador.tokens_entrada, "tokens_salida": borrador.tokens_salida,
            "coste_usd": borrador.coste_usd, "idioma": borrador.idioma, "alcance": borrador.alcance,
            "contact_id": borrador.contact_id, "regenerar_de": payload.regenerar_de,
        },
        request=request,
    )
    session.commit()
    session.refresh(borrador)
    return {"borrador": borradores.como_dict(borrador)}


@router.post("/{borrador_id}/descartar")
def descartar(
    thread_id: str,
    borrador_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    _hilo_visible(session, thread_id, current_user)
    borrador = session.get(BorradorAsistente, borrador_id)
    if borrador is None or borrador.thread_id != thread_id:
        raise not_found("BorradorAsistente")
    # Solo quien lo pidió (o admin/manager) lo descarta: un compañero que abre
    # el mismo hilo no puede resolver el borrador de otro.
    privileged = current_user.role in (UserRole.ADMIN, UserRole.MANAGER)
    if borrador.usuario_id != current_user.id and not privileged:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ese borrador lo pidió otra persona.")
    if borrador.estado != ESTADO_GENERADO:
        return {"borrador": borradores.como_dict(borrador)}     # ya resuelto: nada que anotar
    borradores.marcar_descartado(session, borrador)
    record_event(
        session, action=Action.IA_BORRADOR_RESUELTO, target_type="ia_borrador",
        target_id=borrador.id, actor=current_user,
        metadata={"estado": borrador.estado, "thread_id": thread_id}, request=request,
    )
    session.commit()
    return {"borrador": borradores.como_dict(borrador)}
