"""Soporte de terceros — la API (`/api/erp/soporte`).

Buscar y leer artículos de los centros de soporte de la lista blanca
(`soporte_fuentes`). Es CONSULTA: no envía nada. Por eso vive en el ERP y no
en `/api/emails`: el taller (rol `sat`, sin correo) la usa desde la Cola SAT
con `erp.sat.view`; la oficina, también desde el redactor.

La lista blanca (alta, edición, «Comprobar») es de administración
(`erp.config`).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/erp/soporte", tags=["erp-soporte"])
