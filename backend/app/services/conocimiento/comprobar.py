"""«Comprobar las URL»: que cada ruta de la tabla responda 200.

Una petición por ruta activa (HEAD y, si la web no lo admite, GET), siguiendo
redirecciones; se guarda en la fila cuándo, el código final y el error si no
hubo respuesta. Es lo que mira Bart antes de confiar en que un enlace que va
a salir en un borrador sigue vivo.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conocimiento import RutaConocimiento
from app.services.conocimiento.rutas import sembrar_si_vacio

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 6.0
#: Peticiones a la vez: 29 rutas con tres webs caídas tardan un par de
#: tiempos de espera, no treinta.
EN_PARALELO = 8


def _cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
        headers={"User-Agent": "BoHub/kb-comprobar"},
    )


def comprobar_url(http: httpx.Client, url: str) -> tuple[int | None, str | None]:
    """`(código HTTP final, error)`. HEAD primero; si la web no lo admite
    (405/403/501), GET. Si no responde, se anota el error sin insistir."""
    try:
        r = http.head(url)
        if r.status_code in (403, 405, 501):
            r = http.get(url)
        return r.status_code, None
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:200]}".strip(": ")


def comprobar(
    session: Session, *, http: httpx.Client | None = None, solo_activas: bool = True,
    ahora: datetime | None = None,
) -> list[dict[str, Any]]:
    """Comprueba todas las rutas (las activas por defecto) y guarda el
    resultado en cada fila. Devuelve la lista con el código de cada una."""
    sembrar_si_vacio(session)
    ahora = ahora or datetime.now(UTC)
    consulta = select(RutaConocimiento).order_by(RutaConocimiento.posicion)
    if solo_activas:
        consulta = consulta.where(RutaConocimiento.activo.is_(True))
    filas = list(session.scalars(consulta))
    propio = http is None
    cliente = http or _cliente()
    salida: list[dict[str, Any]] = []
    try:
        # La red, en paralelo (el cliente de httpx admite hilos); la base, aquí.
        with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
            resultados = list(pool.map(lambda f: comprobar_url(cliente, f.url), filas))
        for fila, (codigo, error) in zip(filas, resultados, strict=True):
            fila.comprobado_en = ahora
            fila.comprobado_http = codigo
            fila.comprobado_error = error
            salida.append({"id": fila.id, "url": fila.url, "http": codigo,
                           "ok": codigo == 200, "error": error})
    finally:
        if propio:
            cliente.close()
    session.flush()
    mal = [s for s in salida if not s["ok"]]
    logger.info("kb: %d URL comprobadas, %d con problema", len(salida), len(mal))
    return salida
