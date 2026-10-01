"""Mensajes de los jobs RQ que fallan, para enseñar en pantalla.

RQ guarda en `job.exc_info` el TRACEBACK entero. La pantalla solo debe ver el
mensaje útil (el de la excepción: «Este pedido aún no está en FACTUSOL…»); la
traza se queda en el log del servidor (el propio job la registra al fallar).
"""
from __future__ import annotations

import re

#: Última línea de un traceback: `paquete.modulo.Clase: mensaje`.
_LINEA_EXCEPCION = re.compile(r"^[A-Za-z_][\w.]*:\s+(?P<msg>.+)$")
#: Marca del fallo «el pedido web aún no está en FACTUSOL» (service.emit_invoice).
NO_EN_FACTUSOL_MARCA = "aún no está en FACTUSOL"
NO_EN_FACTUSOL_CODE = "pedido_no_en_factusol"


def mensaje_de_fallo(exc_info: str | None, defecto: str) -> str:
    """El mensaje de la excepción de un job fallido, sin la traza ni el nombre
    de la clase (`app.…FactusolError: Este pedido…` → `Este pedido…`). Sin
    traza, `defecto`."""
    lineas = [ln.strip() for ln in (exc_info or "").strip().splitlines() if ln.strip()]
    if not lineas:
        return defecto
    ultima = lineas[-1]
    m = _LINEA_EXCEPCION.match(ultima)
    mensaje = m.group("msg") if m else ultima
    return mensaje[:400]


def estado_fallido(exc_info: str | None, defecto: str) -> dict[str, str]:
    """`{"status": "failed", "error": <mensaje limpio>}` y, si es el caso del
    pedido web que aún no está en FACTUSOL, su `code` (la pantalla ofrece
    «Volver a comprobar»)."""
    error = mensaje_de_fallo(exc_info, defecto)
    out = {"status": "failed", "error": error}
    if NO_EN_FACTUSOL_MARCA in error:
        out["code"] = NO_EN_FACTUSOL_CODE
    return out
