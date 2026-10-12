"""Los vídeos de un correo compuesto: qué vídeo es y de dónde salen sus datos.

El bloque «vídeo» guarda solo QUÉ vídeo es: `{"plataforma": "youtube" | "vimeo",
"id": …}`. Lo demás —la URL, la miniatura, el título, la duración, el idioma— lo
resuelve BoHub al generar el correo (`resolver`), como los datos de los
productos salen del catálogo vivo. Los orígenes posibles:

- `url`: una URL pegada (en el hilo, en la instrucción, en la vista previa).
  De YouTube se deriva la miniatura de su dirección estática (ver
  `MINIATURAS_YOUTUBE`); un vídeo de Vimeo suelto sale sin imagen (su
  miniatura pediría su API, que no se consulta), con su título y su botón.
- La tabla de vídeos de BoHub (siguiente fase): los vídeos de los canales de
  YouTube o añadidos a mano pegando una URL, con título, miniatura, duración,
  idioma, tema (el mismo vídeo en varios idiomas: así se elige la versión del
  idioma del cliente), marca y productos. Cuando un vídeo esté en la tabla,
  sus datos mandan sobre lo derivado de la URL; la forma del bloque y el
  generador no cambian.

El título, el texto y el botón que lleve el propio bloque (los escribe la IA o
la persona en la vista previa) mandan sobre los del origen.
"""

from __future__ import annotations

import re
from typing import Any

PLATAFORMA_YOUTUBE = "youtube"
PLATAFORMA_VIMEO = "vimeo"
PLATAFORMAS: tuple[str, ...] = (PLATAFORMA_YOUTUBE, PLATAFORMA_VIMEO)

#: De dónde salieron los datos de un vídeo resuelto.
ORIGEN_URL = "url"

#: Las miniaturas de YouTube que valen para un correo, de mejor a peor: las
#: dos en 16:9, sin bandas negras (`sddefault` y `hqdefault` son 4:3 y a un
#: vídeo apaisado le ponen bandas arriba y abajo). `maxresdefault` (1280×720)
#: solo existe si el vídeo se subió en HD; `mqdefault` (320×180) existe
#: siempre y la ficha la escala a su ancho. Sin comprobar nada, la segura;
#: la tabla de vídeos comprobará la buena y guardará cuál usó (para volver a
#: probar `maxresdefault` si el vídeo se resube en HD).
MINIATURAS_YOUTUBE: tuple[str, ...] = ("maxresdefault", "mqdefault")
MINIATURA_YOUTUBE_SEGURA = "mqdefault"

_ID_RE = {
    PLATAFORMA_YOUTUBE: re.compile(r"^[A-Za-z0-9_-]{11}$"),
    PLATAFORMA_VIMEO: re.compile(r"^\d{5,12}$"),
}
_YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/|embed/|live/)|"
    r"youtu\.be/)([A-Za-z0-9_-]{11})(?:[?&#/].*)?$",
)
_VIMEO_RE = re.compile(
    r"^https?://(?:www\.|player\.)?vimeo\.com/(?:video/|channels/[\w-]+/|"
    r"groups/[\w-]+/videos/)?(\d{5,12})(?:[/?#].*)?$",
)


def de_url(url: Any) -> dict[str, str] | None:
    """Qué vídeo es una URL de YouTube (watch, youtu.be, shorts, embed) o de
    Vimeo, o None si no es de ninguna de las dos."""
    s = str(url or "").strip()
    if m := _YOUTUBE_RE.match(s):
        return {"plataforma": PLATAFORMA_YOUTUBE, "id": m.group(1)}
    if m := _VIMEO_RE.match(s):
        return {"plataforma": PLATAFORMA_VIMEO, "id": m.group(1)}
    return None


def identidad(valor: Any) -> dict[str, str] | None:
    """Qué vídeo es: `{"plataforma", "id"}` válido (de un bloque guardado, o
    de la tabla de vídeos) o una URL pegada. None si no se reconoce."""
    if isinstance(valor, dict):
        plataforma = str(valor.get("plataforma") or "").strip().lower()
        vid = str(valor.get("id") or "").strip()
        if plataforma in _ID_RE and _ID_RE[plataforma].match(vid):
            return {"plataforma": plataforma, "id": vid}
        return de_url(valor.get("url"))
    return de_url(valor)


def clave(video: dict[str, str]) -> str:
    return f"{video['plataforma']}:{video['id']}"


def url_de(video: dict[str, str]) -> str:
    """La URL pública del vídeo."""
    if video["plataforma"] == PLATAFORMA_VIMEO:
        return f"https://vimeo.com/{video['id']}"
    return f"https://www.youtube.com/watch?v={video['id']}"


def miniatura_youtube(video_id: str, calidad: str = MINIATURA_YOUTUBE_SEGURA) -> str:
    """La dirección de una miniatura de YouTube (`MINIATURAS_YOUTUBE`)."""
    return f"https://img.youtube.com/vi/{video_id}/{calidad}.jpg"


def miniatura_derivada(video: dict[str, str]) -> str | None:
    """La miniatura que se puede sacar de la propia URL, sin preguntar a nadie:
    la de YouTube que existe siempre en 16:9; la de un vídeo de Vimeo pediría
    su API (no se consulta)."""
    if video["plataforma"] == PLATAFORMA_YOUTUBE:
        return miniatura_youtube(video["id"])
    return None


def desde_url(video: dict[str, str]) -> dict[str, Any]:
    """Los datos de un vídeo con lo que da su URL (sin título ni duración)."""
    miniatura = miniatura_derivada(video)
    return {
        "plataforma": video["plataforma"], "id": video["id"], "url": url_de(video),
        "miniatura": miniatura,
        "miniatura_calidad": MINIATURA_YOUTUBE_SEGURA if miniatura else None,
        "titulo": None, "duracion": None, "idioma": None, "origen": ORIGEN_URL,
    }


def resolver(session: Any, identidades: list[dict[str, str]], *, idioma: str,
             ) -> dict[str, dict[str, Any]]:
    """Los datos de cada vídeo de un correo, por su clave. Hoy, lo que da su
    URL; cuando exista la tabla de vídeos, primero la tabla (por plataforma e
    id, y la versión del mismo vídeo en `idioma` si la hay) y lo derivado de la
    URL solo para lo que no esté. `session` es la de la base de datos."""
    _ = session, idioma
    return {clave(v): desde_url(v) for v in identidades}


def duracion_texto(segundos: Any) -> str:
    """«2:31», «1:02:03»; vacío si no se sabe."""
    try:
        total = int(segundos)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"
