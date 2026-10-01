"""Mensajes de los jobs RQ que fallan, para enseñar en pantalla.

RQ guarda en `job.exc_info` el TRACEBACK entero. La pantalla solo debe ver el
mensaje útil (el de la excepción: «Este pedido aún no está en FACTUSOL…»); la
traza se queda en el log del servidor (el propio job la registra al fallar).
"""
from __future__ import annotations

import re

#: Última línea de un traceback: `paquete.modulo.Clase: mensaje`.
_LINEA_EXCEPCION = re.compile(r"^[A-Za-z_][\w.]*:\s+(?P<msg>.+)$")
#: Una excepción sin mensaje: solo `paquete.modulo.Clase`.
_SOLO_CLASE = re.compile(r"^[A-Za-z_][\w.]*$")
#: Marca del fallo «el pedido web aún no está en FACTUSOL» (service.emit_invoice).
NO_EN_FACTUSOL_MARCA = "aún no está en FACTUSOL"
NO_EN_FACTUSOL_CODE = "pedido_no_en_factusol"


def mensaje_de_fallo(exc_info: str | None, defecto: str) -> str:
    """El mensaje de la excepción de un job fallido, sin la traza ni el nombre
    de la clase (`app.…FactusolError: Este pedido…` → `Este pedido…`). Es todo
    lo que va tras el ÚLTIMO marco de la traza (con excepciones encadenadas,
    la última), en una línea: un mensaje de varias líneas (el cuerpo HTML o
    JSON de un 502 que FACTUSOL mete en el error) no se queda en su última
    línea suelta. Sin traza, el texto tal cual; vacío, `defecto`."""
    lineas = (exc_info or "").splitlines()
    marcos = [i for i, ln in enumerate(lineas) if ln.startswith('  File "')]
    if marcos:
        j = marcos[-1] + 1
        # La línea de código del marco (y los «^^^^» de 3.11), sangradas.
        while j < len(lineas) and (lineas[j].startswith(" ") or not lineas[j].strip()):
            j += 1
        cuerpo = lineas[j:]
    else:
        cuerpo = [ln for ln in lineas if not ln.startswith("Traceback")]
    cuerpo = [ln.strip() for ln in cuerpo if ln.strip()]
    if not cuerpo:
        return defecto
    m = _LINEA_EXCEPCION.match(cuerpo[0])
    if m:
        cuerpo[0] = m.group("msg")
    elif marcos and _SOLO_CLASE.match(cuerpo[0]):
        cuerpo = cuerpo[1:]        # excepción sin mensaje: solo su clase
    mensaje = " ".join(cuerpo)
    return mensaje[:400] if mensaje else defecto


def estado_fallido(exc_info: str | None, defecto: str) -> dict[str, str]:
    """`{"status": "failed", "error": <mensaje limpio>}` y, si es el caso del
    pedido web que aún no está en FACTUSOL, su `code` (la pantalla ofrece
    «Volver a comprobar»)."""
    error = mensaje_de_fallo(exc_info, defecto)
    out = {"status": "failed", "error": error}
    if NO_EN_FACTUSOL_MARCA in error:
        out["code"] = NO_EN_FACTUSOL_CODE
    return out
