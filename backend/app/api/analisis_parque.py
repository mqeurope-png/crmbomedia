"""API · El análisis del parque de una empresa (1/11/2026).

- `GET /api/companies/{id}/analisis-parque`: lo guardado y si está al día. No
  llama a la IA (la ficha pide generar si hace falta).
- `POST /api/companies/{id}/analisis-parque/generar`: lo genera si toca (o
  siempre, con `forzar`: «Volver a generar») y lo guarda.
- `POST /api/analisis-parque/generar-lote`: lo pide para varias empresas desde
  una lista (en segundo plano, como mucho 50; salta las que están al día).

Permisos: mientras Bart lo prueba (`solo_admin` en Configuración ERP), solo
administradores; después, quien ve el parque (`erp.office.view`). No escribe en
FACTUSOL, no manda nada y no escribe correos: eso es del redactor.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import ROLE_LEVELS, get_current_user
from app.core.errors import forbidden, not_found
from app.db.session import get_session
from app.models.crm import Company, User, UserRole
from app.services import llm
from app.services.analisis_parque import config as cfg_mod
from app.services.analisis_parque import servicio

router = APIRouter(tags=["analisis-parque"])


def puede_verlo(session: Session, user: User) -> bool:
    if ROLE_LEVELS[user.role] >= ROLE_LEVELS[UserRole.ADMIN]:
        return True
    if cfg_mod.configuracion(session)["solo_admin"]:
        return False
    from app.erp.capabilities import Cap, has_capability  # noqa: PLC0415

    return has_capability(user, Cap.OFFICE_VIEW)


def _permiso(session: Session = Depends(get_session),
             user: User = Depends(get_current_user)) -> User:
    if not puede_verlo(session, user):
        raise forbidden()
    return user


def _empresa(session: Session, company_id: str) -> Company:
    empresa = session.get(Company, company_id)
    if empresa is None:
        raise not_found("Company")
    return empresa


@router.get("/api/analisis-parque/permiso")
def permiso(session: Session = Depends(get_session),
            user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Si este usuario lo ve (la pantalla no lo enseña si no) y si está en
    prueba (solo administradores)."""
    return {"puede": puede_verlo(session, user),
            "solo_admin": cfg_mod.configuracion(session)["solo_admin"]}


@router.get("/api/companies/{company_id}/analisis-parque")
def leer(company_id: str, session: Session = Depends(get_session),
         _: User = Depends(_permiso)) -> dict[str, Any]:
    return servicio.leer(session, _empresa(session, company_id))


class GenerarIn(BaseModel):
    forzar: bool = False


@router.post("/api/companies/{company_id}/analisis-parque/generar")
def generar(company_id: str, body: GenerarIn | None = None,
            session: Session = Depends(get_session),
            user: User = Depends(_permiso)) -> dict[str, Any]:
    empresa = _empresa(session, company_id)
    try:
        return servicio.generar(session, empresa, por=user.email or user.id,
                                forzar=bool(body and body.forzar))
    except llm.LLMNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "La IA no está configurada en el servidor.") from exc
    except llm.LLMRateLimitError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "La IA está saturada: vuelve a probar en un momento.") from exc
    except llm.LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "La IA no ha respondido. No se ha guardado nada; vuelve a probar."
                            ) from exc


class LoteIn(BaseModel):
    company_ids: list[str] = Field(min_length=1, max_length=servicio.MAX_LOTE)


@router.post("/api/analisis-parque/generar-lote", status_code=status.HTTP_202_ACCEPTED)
def generar_lote(body: LoteIn, tareas: BackgroundTasks,
                 user: User = Depends(_permiso)) -> dict[str, Any]:
    ids = list(dict.fromkeys(body.company_ids))
    tareas.add_task(servicio.generar_lote, ids, por=user.email or user.id)
    return {"encolados": len(ids)}
