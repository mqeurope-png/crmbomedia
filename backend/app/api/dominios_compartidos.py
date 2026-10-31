"""Administración → Dominios compartidos (1/11/2026): la lista de dominios de
correo que nunca vinculan un contacto a una empresa. Leerla, añadir y quitar.
Añadir un dominio no despega nada: los vínculos que ya hay los despega el
script `despegar_dominios_compartidos` (con `--probar` antes)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.db.session import get_session
from app.models.crm import Contact, User
from app.services import dominios_compartidos as dc

router = APIRouter(prefix="/api/admin/dominios-compartidos", tags=["admin-dominios"])

ERRORES = {
    "dominio_no_valido": "Eso no es un dominio (por ejemplo: xtec.cat).",
    "tipo_no_valido": "El tipo tiene que ser «compartido» o «correo».",
    "ya_esta": "Ese dominio ya está en la lista.",
    "ya_lo_cubre": "Ya está en la lista el dominio del que este es subdominio.",
}


class DominioIn(BaseModel):
    dominio: str = Field(min_length=3, max_length=255)
    tipo: str = dc.TIPO_COMPARTIDO
    nota: str | None = Field(default=None, max_length=255)


@router.get("")
def listar(session: Session = Depends(get_session),
           _: User = Depends(require_admin)) -> list[dict[str, Any]]:
    """La lista, con cuántos contactos tienen correo de cada dominio."""
    return dc.listar(session, emails=session.scalars(select(Contact.email)))


@router.post("", status_code=status.HTTP_201_CREATED)
def anadir(payload: DominioIn, session: Session = Depends(get_session),
           user: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        return dc.anadir(session, payload.dominio, tipo=payload.tipo, nota=payload.nota,
                         por=user.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=ERRORES.get(str(exc), str(exc))) from exc


@router.delete("/{dominio}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response)
def quitar(dominio: str, session: Session = Depends(get_session),
           _: User = Depends(require_admin)) -> Response:
    if not dc.quitar(session, dominio):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Ese dominio no está en la lista.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
