"""Remitentes del «De» de cualquier usuario, desde la ficha de Usuarios.

Lo mismo que cada uno puede hacer para sí en Mi cuenta → Gmail (alias de
envío), hecho por el administrador sobre otro usuario: marcar las direcciones
que le salen en el desplegable «De», elegir su predeterminado y cambiar el
nombre visible sin tocar Gmail. Desde CRM-PERFIL el perfil del comercial es de
solo lectura y esto lo gestiona el administrador; hasta este endpoint no
tenía dónde hacerlo (solo el bloque de direcciones de ENTRADA, que no da
remitentes).

  - GET /api/users/{id}/senders — admin. Los «enviar como» de Gmail con las
    preferencias del usuario, más las direcciones que tiene marcadas o
    registradas como entrada y Gmail no tiene (`en_gmail=False`).
  - PUT /api/users/{id}/senders — admin. Misma semántica que
    `PUT /api/emails/aliases/preferences` (`services/remitentes.guardar`): lo
    marcado queda como elección (`user_opted_in`) y el sync de Gmail lo
    respeta; lo que Gmail no tiene verificado queda elegido pero apagado
    hasta que lo verifique. Cada cambio queda en Auditoría
    (`user.senders_updated`).

Un no-admin no puede ver ni tocar los remitentes de nadie por aquí (403); los
suyos los ve en Mi cuenta.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.audit import Action, record_event
from app.core.auth import require_admin
from app.core.errors import not_found
from app.db.session import get_session
from app.models.crm import User
from app.schemas.emails import AliasPreferencesPayload, EmailAlias
from app.services import remitentes

router = APIRouter(prefix="/api/users", tags=["users"])


def _load_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise not_found("User")
    return user


@router.get("/{user_id}/senders", response_model=list[EmailAlias])
def list_user_senders(
    user_id: str,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> list[EmailAlias]:
    return remitentes.listar(session, _load_user(session, user_id), completo=True)


@router.put("/{user_id}/senders", response_model=list[EmailAlias])
def update_user_senders(
    user_id: str,
    payload: AliasPreferencesPayload,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> list[EmailAlias]:
    user = _load_user(session, user_id)
    # Lo que Gmail no tiene verificado se guarda como elección pero apagado
    # (`guardar`); por eso se pregunta a Gmail antes de guardar.
    gmail = remitentes.consultar_gmail(session, user, completo=True)
    cambios = remitentes.guardar(session, user, payload.preferences,
                                 en_gmail=remitentes.direcciones(gmail))
    if cambios:
        record_event(
            session,
            action=Action.USER_SENDERS_UPDATED,
            target_type="user",
            target_id=user.id,
            actor=current_user,
            message=f"Remitentes de {user.email}: {remitentes.resumen_de_cambios(cambios)}",
            metadata={"target_user_id": user.id, "target_email": user.email,
                      "cambios": cambios},
            request=request,
        )
    session.commit()
    return remitentes.listar(session, user, completo=True, gmail=gmail,
                             gmail_consultado=True)
