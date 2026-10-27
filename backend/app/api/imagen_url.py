"""Imagen por URL para el editor de correo (`/api/emails/imagen-url`).

Bart pega el enlace de una imagen (suya, de sus webs) y va en el `<img>` TAL
CUAL: no se descarga para guardarla, no se rehospeda, no se sube a ninguna
biblioteca. Aquí solo se COMPRUEBA antes de insertarla: HTTPS, que responda
200 con `Content-Type: image/*`, cuánto pesa (más de 1 MB se avisa) y que no
apunte a la red interna (`red.comprobar_url`).

- POST /api/emails/imagen-url/comprobar  `{url}`

Responde SIEMPRE 200 (el editor enseña el motivo y decide si inserta):

- `{"ok": true, "url", "content_type", "bytes" (o null si no se sabe),
  "pesada", "aviso"}`: se puede insertar (con aviso si pesa más de 1 MB).
- `{"ok": false, "url", "codigo", "motivo"}`: no se inserta. `codigo`:
  `no_https`, `red_interna`, `no_resuelve`, `no_responde`, `no_es_imagen`,
  `url_invalida`, `puerto_no_permitido`, `tiempo_agotado`.

La URL que entra es la que sale: no se reescribe, no se sigue la redirección
para cambiarla, no se guarda nada.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import require_user
from app.models.crm import User
from app.services.externo import red

router = APIRouter(prefix="/api/emails/imagen-url", tags=["emails"])

#: Más de esto se inserta igual, pero se avisa: el correo tarda en cargar.
UMBRAL_PESADA = 1024 * 1024
#: Lo que se descarga como mucho para medir una imagen cuando el servidor no
#: dice cuánto pesa (o no admite HEAD): se corta ahí y se da por pesada.
MAX_BYTES_MEDIR = 15 * 1024 * 1024
#: Tiempo máximo de cada petición (HEAD y, si hace falta, GET).
TIMEOUT = 8.0
#: Una URL más larga que esto no es un enlace de imagen razonable.
MAX_URL = 2048

#: Para los tests: un `httpx.Client` con `MockTransport` y un resolutor de DNS
#: falso. En producción, vacío (red de verdad, con los cerrojos de `red`).
_RED_PRUEBAS: dict[str, Any] = {}


def fijar_red_para_tests(*, cliente: httpx.Client | None = None, resolver: Any = None) -> None:
    """Sustituye la red (y el DNS) de la comprobación. Sin argumentos, vuelve
    a la de verdad."""
    _RED_PRUEBAS.clear()
    if cliente is not None:
        _RED_PRUEBAS["cliente"] = cliente
    if resolver is not None:
        _RED_PRUEBAS["resolver"] = resolver


class ComprobarIn(BaseModel):
    # El tope de verdad (`MAX_URL`) se mira a mano, para contestar 200 con el
    # motivo en vez de un 422 que el editor no sabría enseñar.
    url: str = Field(default="", max_length=20_000)


def _no(url: str, codigo: str, motivo: str) -> dict[str, Any]:
    return {"ok": False, "url": url, "codigo": codigo, "motivo": motivo}


def _megas(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}".replace(".", ",")


def _si(url: str, tipo: str, peso: int | None, *, truncado: bool = False) -> dict[str, Any]:
    pesada = truncado or (peso is not None and peso > UMBRAL_PESADA)
    aviso = None
    if truncado:
        aviso = (f"La imagen pesa más de {_megas(MAX_BYTES_MEDIR)} MB: se insertará igual, pero "
                 "el correo tardará mucho en cargar.")
    elif pesada and peso is not None:
        aviso = (f"La imagen pesa {_megas(peso)} MB: se insertará igual, pero el correo tardará "
                 "en cargar.")
    elif tipo == "image/svg+xml":
        aviso = ("Es un SVG: muchos clientes de correo (Gmail, Outlook) no lo enseñan. Mejor "
                 "una imagen JPG o PNG.")
    return {"ok": True, "url": url, "content_type": tipo, "bytes": peso, "pesada": pesada,
            "aviso": aviso}


def _error_de_red(url: str, exc: red.RedError) -> dict[str, Any]:
    """`UrlNoPermitida` trae ya su código y su motivo (los de la API); los
    errores de la conexión se dicen como «no responde»."""
    if isinstance(exc, red.UrlNoPermitida):
        codigo = exc.codigo if exc.codigo in (
            "no_https", "red_interna", "no_resuelve", "url_invalida",
            "puerto_no_permitido") else "url_invalida"
        return _no(url, codigo, str(exc))
    if exc.codigo == "tiempo_agotado":
        return _no(url, "tiempo_agotado",
                   "La web de la imagen ha tardado demasiado en responder: prueba otra vez o usa "
                   "otro enlace.")
    return _no(url, "no_responde", f"La dirección no responde: {exc}")


def comprobar(url: str, *, cliente: httpx.Client | None = None,
              resolver: Any = None) -> dict[str, Any]:
    """Comprueba una URL de imagen (ver el docstring del módulo). Primero HEAD
    (tipo y tamaño sin descargar nada); si el servidor no lo admite o no dice
    el tipo, un GET que se corta a los `MAX_BYTES_MEDIR` para medir. Toda
    petición pasa por `red.obtener` (sin red interna, redirecciones
    comprobadas, tiempo y tamaño máximos)."""
    limpia = (url or "").strip()
    if not limpia:
        return _no(limpia, "url_invalida", "Pega el enlace de la imagen (https://…).")
    if len(limpia) > MAX_URL:
        return _no(limpia, "url_invalida", "El enlace es demasiado largo para ser una imagen.")
    red_args: dict[str, Any] = {"timeout": TIMEOUT, "cliente": cliente, "resolver": resolver}
    try:
        cabeza = red.obtener(limpia, metodo="HEAD", max_bytes=0, **red_args)
    except red.UrlNoPermitida as exc:
        return _error_de_red(limpia, exc)
    except red.RedError as exc:
        if exc.codigo == "tiempo_agotado":
            return _error_de_red(limpia, exc)
        cabeza = None                       # hay servidores que cortan el HEAD: se prueba GET
    if cabeza is not None and cabeza.status in (404, 410):
        return _no(limpia, "no_responde",
                   f"La imagen no está en esa dirección (HTTP {cabeza.status}).")
    if cabeza is not None and cabeza.status == 200 and cabeza.tipo:
        if not cabeza.tipo.startswith("image/"):
            return _no(limpia, "no_es_imagen",
                       f"El enlace no es una imagen (es {cabeza.tipo}): copia la dirección de la "
                       "imagen, no la de la página.")
        if cabeza.longitud_anunciada is not None:
            return _si(limpia, cabeza.tipo, cabeza.longitud_anunciada)
    # HEAD no vale (405/501, sin tipo, otro estado) o no dijo cuánto pesa: GET
    # para ver el tipo y medir, cortando en el tope.
    try:
        cuerpo = red.obtener(limpia, max_bytes=MAX_BYTES_MEDIR, aceptar_truncado=True,
                             **red_args)
    except red.RedError as exc:
        return _error_de_red(limpia, exc)
    if cuerpo.status != 200:
        return _no(limpia, "no_responde",
                   f"La dirección no devuelve la imagen (HTTP {cuerpo.status}).")
    if not cuerpo.tipo.startswith("image/"):
        return _no(limpia, "no_es_imagen",
                   f"El enlace no es una imagen (es {cuerpo.tipo or 'de un tipo desconocido'}): "
                   "copia la dirección de la imagen, no la de la página.")
    if cuerpo.truncado:
        return _si(limpia, cuerpo.tipo, cuerpo.longitud_anunciada, truncado=True)
    return _si(limpia, cuerpo.tipo, cuerpo.longitud_anunciada or len(cuerpo.contenido))


@router.post("/comprobar")
def comprobar_imagen(payload: ComprobarIn, current_user: User = Depends(require_user),
                     ) -> dict[str, Any]:
    """¿Se puede insertar esta imagen por URL? No guarda nada ni la sube a
    ningún sitio: la URL que entra es la que sale."""
    _ = current_user
    return comprobar(payload.url, cliente=_RED_PRUEBAS.get("cliente"),
                     resolver=_RED_PRUEBAS.get("resolver"))
