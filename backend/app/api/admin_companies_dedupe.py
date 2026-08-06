"""Deduplicar empresas por NIF en masa (Fase C · C-7).

Mounted at `/api/admin/companies`. Admin-only: el apply **borra** filas.

    GET  /api/admin/companies/duplicates?by=tax_id — dry-run, solo lee.
    POST /api/admin/companies/merge                — fusiona los grupos marcados.

Ya existía `POST /api/companies/{id}/merge/{target}` para fusionar dos empresas
desde la ficha. Se mantiene, pero **no se reutiliza**: solo mueve contactos, y
las FK de `tasks` y `orders` son `ON DELETE SET NULL`, así que borrar la empresa
las vacía en silencio. Esta ruta mueve las tres y guarda snapshot.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.db.session import get_session
from app.models.crm import User

router = APIRouter(prefix="/api/admin/companies", tags=["admin-companies"])
logger = logging.getLogger(__name__)


@router.get("/duplicates")
def list_duplicate_companies(
    by: str = Query(default="tax_id", pattern="^tax_id$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Grupos de empresas que comparten NIF. **No modifica nada.**

    `by` solo acepta `tax_id`: agrupar por nombre o email daría falsos
    positivos y este endpoint borra empresas al aplicarse."""
    _ = current_user, by
    from app.services.company_dedupe import find_duplicates  # noqa: PLC0415

    return find_duplicates(session)


class MergeOperation(BaseModel):
    keep_id: str = Field(min_length=1, max_length=36)
    merge_ids: list[str] = Field(default_factory=list)


class MergePayload(BaseModel):
    operations: list[MergeOperation] = Field(default_factory=list)


@router.post("/merge")
def merge_duplicate_companies(
    payload: MergePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Absorbe cada grupo en su principal y borra las demás.

    Una operación por transacción y **siempre 200**: lo que falle va en
    `errors` con su `keep_id`. Un grupo raro no puede tirar un lote en el que
    ya se han borrado empresas."""
    from app.services.company_dedupe import merge_groups  # noqa: PLC0415

    if not payload.operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que fusionar.",
        })
    return merge_groups(
        session,
        operations=[op.model_dump() for op in payload.operations],
        actor=current_user,
    )
