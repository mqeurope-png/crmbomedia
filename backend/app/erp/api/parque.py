"""ERP · Parque instalado en la ficha de empresa (30/10/2026).

Qué máquinas tiene un cliente y qué ha comprado (todos sus documentos, de
2014 a hoy). Lee lo volcado de FACTUSOL; no escribe en FACTUSOL.

Permisos: ver, `erp.office.view` (como el histórico; Usuario y Responsable
del CRM lo ven, decidido el 30/10). Añadir una máquina a mano o darla de
baja, quien trabaja pedidos (`erp.orders.create`).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp import parque as svc
from app.erp.api.deps import require_erp_edit, require_office_view
from app.erp.historico_documento import documentos_de_cliente
from app.erp.models.historico import HistoricoDocumento
from app.models.crm import Company, User

router = APIRouter(prefix="/api/erp/parque", tags=["erp-parque"])


def _empresa(session: Session, company_id: str) -> Company:
    c = session.get(Company, company_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "empresa_no_existe", "detail": "Esa empresa no existe."})
    return c


def _por(user: User) -> str:
    return user.email or str(user.id)


def _no_es_suya(exc: LookupError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, {
        "code": "maquina_no_existe", "detail": "Esa máquina no está en el parque de esta empresa."})


@router.get("/empresas/{company_id}")
def parque_empresa(
    company_id: str,
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    return svc.parque(session, _empresa(session, company_id))


@router.get("/empresas/{company_id}/compras")
def compras_empresa(
    company_id: str,
    ejercicio: str | None = Query(None, pattern=r"^(\d{4}|todos)$"),
    session: Session = Depends(get_session),
    _: User = Depends(require_office_view),
) -> dict[str, Any]:
    """Los documentos (facturas, albaranes, proformas) del cliente. Por
    defecto, el ejercicio en curso; `todos` trae de 2014 a hoy."""
    from app.erp.ejercicio import ejercicio_en_curso  # noqa: PLC0415

    company = _empresa(session, company_id)
    codcli = (company.factusol_company_id or "").strip() or None
    en_curso = ejercicio_en_curso(session)
    elegido = ejercicio or en_curso
    if codcli is None:
        return {"codcli": None, "sin_cliente_factusol": True, "ejercicio": elegido,
                "en_curso": en_curso, "ejercicios": [], "documentos": []}
    codigos = svc.codigos_cliente(codcli)
    ejercicios = sorted({e for e in session.scalars(
        select(HistoricoDocumento.ejercicio).where(HistoricoDocumento.codcli.in_(codigos))
        .distinct())}, reverse=True)
    docs = documentos_de_cliente(session, codcli,
                                 ejercicio=None if elegido == "todos" else elegido)
    return {"codcli": codcli, "sin_cliente_factusol": False, "ejercicio": elegido,
            "en_curso": en_curso, "ejercicios": ejercicios, "documentos": docs}


class MaquinaIn(BaseModel):
    descripcion: str = Field(min_length=1, max_length=255)
    codart: str | None = Field(default=None, max_length=64)
    gama: str | None = Field(default=None, max_length=32)
    fecha: date | None = None
    nota: str | None = Field(default=None, max_length=255)


@router.post("/empresas/{company_id}/maquinas", status_code=status.HTTP_201_CREATED)
def anadir_maquina(
    company_id: str,
    body: MaquinaIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    company = _empresa(session, company_id)
    try:
        m = svc.anadir_manual(session, company.id, body.model_dump(), por=_por(user))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": f"{exc}_no_valida",
            "detail": "Esa gama no existe." if str(exc) == "gama" else "Falta la descripción.",
        }) from exc
    return {"id": m.id}


@router.delete("/empresas/{company_id}/maquinas/{maquina_id}",
               status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def borrar_maquina(
    company_id: str,
    maquina_id: str,
    session: Session = Depends(get_session),
    _: User = Depends(require_erp_edit),
) -> Response:
    try:
        svc.borrar_manual(session, _empresa(session, company_id).id, maquina_id)
    except LookupError as exc:
        raise _no_es_suya(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class BajaIn(BaseModel):
    """Una máquina volcada se identifica por su `clave`; una manual, por `maquina_id`."""

    clave: str | None = Field(default=None, max_length=160)
    maquina_id: str | None = Field(default=None, max_length=36)
    motivo: str | None = Field(default=None, max_length=255)


@router.post("/empresas/{company_id}/bajas")
def dar_baja(
    company_id: str,
    body: BajaIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    company = _empresa(session, company_id)
    try:
        svc.dar_baja(session, company, clave=body.clave, maquina_id=body.maquina_id,
                     motivo=body.motivo, por=_por(user))
    except LookupError as exc:
        raise _no_es_suya(exc) from exc
    return svc.parque(session, company)


@router.post("/empresas/{company_id}/bajas/deshacer")
def deshacer_baja(
    company_id: str,
    body: BajaIn,
    session: Session = Depends(get_session),
    _: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    company = _empresa(session, company_id)
    try:
        svc.reactivar(session, company, clave=body.clave, maquina_id=body.maquina_id)
    except LookupError as exc:
        raise _no_es_suya(exc) from exc
    return svc.parque(session, company)
