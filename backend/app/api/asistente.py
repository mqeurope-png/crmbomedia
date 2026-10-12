"""«Ayúdame a contestar» — API del asistente de respuesta.

Sobre un hilo:
- POST /api/emails/threads/{thread_id}/asistente          el primer borrador (turno 0):
                                                            `alcance`, `instruccion`
                                                            (opcional), `destinatario`
- POST /api/emails/threads/{thread_id}/asistente/{id}/descartar

Sin hilo (un correo nuevo desde Redactar o desde la ficha) y la conversación:
- POST /api/emails/asistente                              el primer borrador de un correo
                                                            nuevo: `destinatario`,
                                                            `instruccion` (obligatoria),
                                                            `alcance` (contacto | empresa)
- POST /api/emails/asistente/{id}/turno                   una corrección («más corto»):
                                                            un turno más, con el contexto ya
                                                            montado, sobre lo que hay AHORA
                                                            en el editor (`texto_actual_html`)
                                                            o solo sobre la selección
                                                            (`seleccion_html`)
- POST /api/emails/asistente/{id}/descartar               descarta la conversación entera
- GET  /api/emails/asistente/{id}/conversacion            los turnos de la conversación (para
                                                            reabrir un borrador guardado con
                                                            su franja)
- GET  /api/emails/asistente/interno?direccion=           si una dirección es del grupo (el
                                                            aviso «vas a escribir a un
                                                            compañero» al cambiar «Para»)

Puede pedirlo cualquier usuario que pueda ver el hilo (la misma regla que el
detalle del hilo); un borrador sin hilo solo lo toca quien lo pidió (o
admin/manager). El borrador vuelve con destinatario, asunto, texto y aviso
para la franja del compositor; el envío lo hace la persona desde el compositor
(`POST /api/emails/send` con `asistente_borrador_id`, que anota el resultado).
No hay aquí ningún camino que envíe correo.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import Action, record_event
from app.core.auth import require_user
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import EmailMessage, EmailThread, User, UserRole
from app.models.ia import (
    ALCANCE_CONTACTO,
    ALCANCES,
    ALCANCES_SIN_HILO,
    ESTADO_DESCARTADO,
    ESTADO_GENERADO,
    ESTADO_REGENERADO,
    BorradorAsistente,
)
from app.services import llm
from app.services.asistente import borradores, redactor
from app.services.email_aliases import thread_is_visible
from app.services.web_forms import sitios as registro_sitios

router = APIRouter(prefix="/api/emails/threads/{thread_id}/asistente", tags=["asistente"])
router_conversacion = APIRouter(prefix="/api/emails/asistente", tags=["asistente"])


class _SobreElEditor(BaseModel):
    """Lo que hay ahora mismo en el editor viaja con cada petición: la IA
    corrige ESO (el borrador retocado, un correo escrito a mano, un borrador
    guardado reabierto), y con una selección, solo ese fragmento."""
    #: El HTML del editor (sin la firma del compositor), si hay algo escrito.
    texto_actual_html: str | None = Field(default=None, max_length=redactor.MAX_TEXTO_EDITOR)
    #: El fragmento seleccionado en el editor: la corrección se aplica solo a él.
    seleccion_html: str | None = Field(default=None, max_length=redactor.MAX_TEXTO_EDITOR)
    #: El interruptor de la franja: `auto` (decide la instrucción o la IA; por
    #: defecto, texto), `normal` (texto) o `compuesto` (correo con fichas).
    formato: Literal["auto", "normal", "compuesto"] = "auto"
    #: El correo compuesto que hay ahora en la vista previa (con los textos
    #: retocados): la corrección se aplica sobre ESA estructura.
    estructura_actual: dict[str, Any] | None = None


class GenerarIn(_SobreElEditor):
    alcance: str = Field(default=ALCANCE_CONTACTO, max_length=10)
    #: Qué quiere decirle la persona; vacío = la IA deduce del hilo.
    instruccion: str | None = Field(default=None, max_length=redactor.MAX_INSTRUCCION)
    #: La dirección de «Para» si la persona la cambió; vacío = la que sugiere el hilo.
    destinatario: str | None = Field(default=None, max_length=255)
    #: El borrador anterior, al empezar de nuevo o cambiar de alcance: queda regenerado.
    regenerar_de: str | None = Field(default=None, max_length=36)


class GenerarNuevoIn(_SobreElEditor):
    destinatario: str = Field(min_length=3, max_length=255)
    instruccion: str = Field(min_length=1, max_length=redactor.MAX_INSTRUCCION)
    alcance: str = Field(default=ALCANCE_CONTACTO, max_length=10)
    contact_id: str | None = Field(default=None, max_length=36)
    regenerar_de: str | None = Field(default=None, max_length=36)


class TurnoIn(_SobreElEditor):
    instruccion: str = Field(min_length=1, max_length=redactor.MAX_INSTRUCCION)


def _privilegiado(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.MANAGER)


def _hilo_visible(session: Session, thread_id: str, user: User) -> EmailThread:
    thread = session.scalar(
        select(EmailThread).where(EmailThread.id == thread_id)
        .options(selectinload(EmailThread.messages).selectinload(EmailMessage.labels))
    )
    if thread is None:
        raise not_found("EmailThread")
    if not _privilegiado(user) and not thread_is_visible(session, user, thread):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este hilo.")
    return thread


def _borrador_accesible(session: Session, borrador_id: str, user: User) -> BorradorAsistente:
    """El borrador si la persona puede tocarlo: solo quien lo pidió (o
    admin/manager). Corregir o descartar resuelve el turno anterior; un
    compañero que ve el mismo hilo no puede resolver la conversación de otro."""
    borrador = session.get(BorradorAsistente, borrador_id)
    if borrador is None:
        raise not_found("BorradorAsistente")
    if borrador.usuario_id == user.id or _privilegiado(user):
        return borrador
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Ese borrador lo pidió otra persona.")


def _redactar(session: Session, trabajo: Callable[[], BorradorAsistente]) -> BorradorAsistente:
    """Llama al modelo y traduce lo que puede salir mal a HTTP; la llamada
    fallida queda anotada."""
    try:
        return trabajo()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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


def _anotar_y_devolver(
    session: Session, request: Request, borrador: BorradorAsistente, actor: User,
    extra: dict[str, Any],
) -> dict[str, Any]:
    resumen = borradores.como_dict(borrador)
    record_event(
        session, action=Action.IA_BORRADOR_GENERADO,
        target_type="email_thread" if borrador.thread_id else "ia_borrador",
        target_id=borrador.thread_id or borrador.id, actor=actor,
        metadata={
            "borrador_id": borrador.id, "conversacion_id": borrador.conversacion_id,
            "turno": borrador.turno, "modelo": borrador.modelo,
            "tokens_entrada": borrador.tokens_entrada, "tokens_salida": borrador.tokens_salida,
            "tokens_cache_lectura": borrador.tokens_cache_lectura,
            "coste_usd": borrador.coste_usd, "idioma": borrador.idioma,
            "alcance": borrador.alcance, "contact_id": borrador.contact_id,
            "con_instruccion": bool(borrador.instruccion), "cuerpo_vacio": not borrador.texto,
            "sobre_editor": bool(resumen.get("sobre_editor")),
            "con_seleccion": bool(resumen.get("seleccion")),
            **extra,
        },
        request=request,
    )
    session.commit()
    session.refresh(borrador)
    return {"borrador": borradores.como_dict(borrador)}


def _descartar(
    session: Session, request: Request, borrador: BorradorAsistente, actor: User,
) -> dict[str, Any]:
    if borrador.estado not in (ESTADO_GENERADO, ESTADO_REGENERADO):
        return {"borrador": borradores.como_dict(borrador)}     # ya resuelto: nada que anotar
    pendientes = [t for t in borradores.turnos_de(session, borrador) if t.estado == ESTADO_GENERADO]
    if not pendientes:
        return {"borrador": borradores.como_dict(borrador)}
    borradores.marcar_descartado(session, borrador)
    record_event(
        session, action=Action.IA_BORRADOR_RESUELTO, target_type="ia_borrador",
        target_id=borrador.id, actor=actor,
        metadata={"estado": "descartado", "thread_id": borrador.thread_id,
                  "conversacion_id": borrador.conversacion_id, "turnos": len(pendientes)},
        request=request,
    )
    session.commit()
    session.refresh(borrador)
    return {"borrador": borradores.como_dict(borrador)}


# --- sobre un hilo --------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def generar(
    thread_id: str,
    payload: GenerarIn,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """El primer borrador de la respuesta a un hilo. Con `instruccion`, el
    correo que pide la persona; sin ella, la IA deduce del hilo, y si el
    cliente no ha escrito nada nuevo no se llama al modelo: cuerpo vacío y el
    motivo en `aviso`. No envía nada: la persona lo revisa en el compositor."""
    if payload.alcance not in ALCANCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Alcance desconocido: {payload.alcance}.")
    thread = _hilo_visible(session, thread_id, current_user)
    if not thread.messages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El hilo no tiene mensajes.")
    borrador = _redactar(session, lambda: redactor.generar(
        session, thread=thread, usuario=current_user, alcance=payload.alcance,
        instruccion=payload.instruccion, destinatario=payload.destinatario,
        regenerar_de=payload.regenerar_de, texto_actual_html=payload.texto_actual_html,
        seleccion_html=payload.seleccion_html, formato=payload.formato,
        estructura_actual=payload.estructura_actual,
    ))
    return _anotar_y_devolver(session, request, borrador, current_user,
                              {"regenerar_de": payload.regenerar_de})


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
    if borrador.usuario_id != current_user.id and not _privilegiado(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ese borrador lo pidió otra persona.")
    return _descartar(session, request, borrador, current_user)


# --- sin hilo, y la conversación --------------------------------------------------------


@router_conversacion.post("", status_code=status.HTTP_201_CREATED)
def generar_nuevo(
    payload: GenerarNuevoIn,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """El primer borrador de un correo NUEVO, sin hilo: el contexto lo dispara
    el destinatario (su ficha, si es un contacto) y la instrucción es
    obligatoria, porque no hay nada de lo que deducir la intención."""
    if payload.alcance not in ALCANCES_SIN_HILO:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Sin hilo el alcance tiene que ser {' o '.join(ALCANCES_SIN_HILO)}.")
    if not payload.instruccion.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dime qué quieres decirle.")
    contacto = None
    if payload.contact_id:
        from app.models.crm import Contact  # noqa: PLC0415

        contacto = session.get(Contact, payload.contact_id)
    borrador = _redactar(session, lambda: redactor.generar(
        session, thread=None, usuario=current_user, alcance=payload.alcance,
        instruccion=payload.instruccion, destinatario=payload.destinatario.strip(),
        contacto=contacto, regenerar_de=payload.regenerar_de,
        texto_actual_html=payload.texto_actual_html, seleccion_html=payload.seleccion_html,
        formato=payload.formato, estructura_actual=payload.estructura_actual,
    ))
    return _anotar_y_devolver(session, request, borrador, current_user,
                              {"sin_hilo": True, "regenerar_de": payload.regenerar_de})


@router_conversacion.post("/{borrador_id}/turno", status_code=status.HTTP_201_CREATED)
def turno(
    borrador_id: str,
    payload: TurnoIn,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Una corrección: un turno más de la conversación, con el contexto ya
    montado (y en caché), lo que hay ahora en el editor (`texto_actual_html`;
    si no llega, el borrador) y la instrucción. Con `seleccion_html` solo se
    reescribe ese fragmento. El borrador corregido queda como regenerado."""
    borrador = _borrador_accesible(session, borrador_id, current_user)
    if borrador.estado not in (ESTADO_GENERADO, ESTADO_REGENERADO) or (
            borradores.conversacion_descartada(session, borrador)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ese borrador ya se resolvió (enviado o descartado).")
    nuevo = _redactar(session, lambda: redactor.corregir(
        session, borrador=borrador, instruccion=payload.instruccion, usuario=current_user,
        texto_actual_html=payload.texto_actual_html, seleccion_html=payload.seleccion_html,
        formato=payload.formato, estructura_actual=payload.estructura_actual))
    return _anotar_y_devolver(session, request, nuevo, current_user, {"padre_id": borrador.id})


@router_conversacion.post("/{borrador_id}/descartar")
def descartar_conversacion(
    borrador_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    borrador = _borrador_accesible(session, borrador_id, current_user)
    return _descartar(session, request, borrador, current_user)


@router_conversacion.get("/interno")
def destinatario_interno(
    direccion: str = Query(default="", max_length=255),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Si la dirección de «Para» es del grupo (un compañero, no un cliente):
    la franja lo pregunta cada vez que cambia el destinatario, con borrador o
    sin él."""
    _ = current_user
    limpia = direccion.strip()
    interno = bool(limpia) and registro_sitios.es_del_grupo(
        limpia, registro_sitios.dominios_del_grupo(session))
    return {"direccion": limpia, "interno": interno}


@router_conversacion.get("/{borrador_id}/conversacion")
def conversacion(
    borrador_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_user),
) -> dict[str, Any]:
    """Los turnos de la conversación a la que pertenece el borrador, para que
    un borrador guardado vuelva a abrirse con su franja (turnos, coste). Una
    conversación descartada no vuelve: lista vacía."""
    borrador = _borrador_accesible(session, borrador_id, current_user)
    turnos = borradores.turnos_de(session, borrador)
    # Descartar es descartar la conversación: con un turno descartado no
    # vuelve, ni por un turno anterior (regenerado) que guardara un borrador.
    if borrador.estado == ESTADO_DESCARTADO or any(t.estado == ESTADO_DESCARTADO for t in turnos):
        return {"turnos": [], "actual": None}
    vivos = [t for t in turnos if t.estado != ESTADO_DESCARTADO]
    actual = borrador.id if any(t.id == borrador.id for t in vivos) else vivos[-1].id
    return {"turnos": [borradores.como_dict(t) for t in vivos], "actual": actual}
