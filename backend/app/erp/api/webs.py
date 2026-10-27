"""Índice de las webs propias — la API (`/api/erp/webs`).

Buscar en el índice (para el redactor), el estado por web y tipo, «Reindexar
ahora» (en cola, en worker-web) y las reglas de qué entra y qué no. Solo lee
de las webs, por sus endpoints públicos.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/erp/webs", tags=["erp-webs"])
