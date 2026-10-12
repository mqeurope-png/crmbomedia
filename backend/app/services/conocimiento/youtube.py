"""El cliente de la API de datos de YouTube (v3) y de sus miniaturas.

- La clave (`YOUTUBE_API_KEY`, en `.env.production`) va en la cabecera
  `X-Goog-Api-Key`, nunca en la URL: así no sale en ningún log ni en ningún
  mensaje de error. Solo hace falta para LISTAR los vídeos de los canales y
  pedir sus datos (título, duración, idioma); cada pasada gasta unas
  decenas de unidades de la cuota diaria (10.000).
- `channels.list?forHandle=@canal` da la lista de subidas del canal;
  `playlistItems.list` sus vídeos y `videos.list` los datos de cada uno, de 50
  en 50. Ni el RSS ni la página del canal: el RSS da 404 y el identificador
  que se saca raspando la página es erróneo.
- Las miniaturas salen de `img.youtube.com/vi/{id}/{calidad}.jpg`, sin la API.
  Para un correo valen las dos en 16:9: `maxresdefault` (1280×720, solo si el
  vídeo se subió en HD; si no, 404) y `mqdefault` (320×180, siempre).
  `sddefault` y `hqdefault` son 4:3 y le ponen bandas negras a un vídeo
  apaisado.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://www.googleapis.com/youtube/v3"
MINIATURAS = "https://img.youtube.com/vi"
TIMEOUT_SEGUNDOS = 20.0
POR_PAGINA = 50
#: 40 páginas de 50 = 2.000 vídeos por canal: ninguno del grupo llega (artisJet, 291).
MAX_PAGINAS = 40

MINIATURA_BUENA = "maxresdefault"
MINIATURA_SEGURA = "mqdefault"

_DURACION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


class YoutubeError(RuntimeError):
    """La API no ha respondido bien (red, cuota, clave no válida)."""


def cliente(api_key: str | None) -> httpx.Client:
    """El cliente de la API: con la clave en su cabecera si la hay."""
    cabeceras = {"User-Agent": "BoHub/kb-videos", "Accept": "application/json"}
    if api_key:
        cabeceras["X-Goog-Api-Key"] = api_key
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
                        headers=cabeceras)


def _get(http: httpx.Client, ruta: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        respuesta = http.get(f"{API}/{ruta}", params=params)
    except httpx.HTTPError as exc:
        raise YoutubeError(f"{ruta}: {type(exc).__name__}: {exc}") from exc
    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise YoutubeError(f"{ruta}: HTTP {respuesta.status_code}, respuesta ilegible") from exc
    if respuesta.status_code != 200 or not isinstance(datos, dict):
        error = datos.get("error") if isinstance(datos, dict) else None
        motivo = ""
        if isinstance(error, dict):
            motivo = str(error.get("message") or "")
            razones = [str(e.get("reason")) for e in error.get("errors") or []
                       if isinstance(e, dict) and e.get("reason")]
            if razones:
                motivo = f"{motivo} ({', '.join(razones)})".strip()
        raise YoutubeError(f"{ruta}: HTTP {respuesta.status_code} {motivo}".strip()[:400])
    return datos


def canal(http: httpx.Client, handle: str) -> dict[str, Any] | None:
    """El canal de un @handle: `{"id", "titulo", "subidas"}`, o None si no existe."""
    arroba = handle if handle.startswith("@") else f"@{handle}"
    datos = _get(http, "channels", {"part": "snippet,contentDetails", "forHandle": arroba})
    items = datos.get("items") or []
    if not items:
        return None
    item = items[0]
    subidas = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    return {"id": item.get("id"), "titulo": (item.get("snippet") or {}).get("title") or handle,
            "subidas": subidas}


def ids_de_lista(http: httpx.Client, lista: str) -> list[str]:
    """Los ids de los vídeos de una lista (las subidas de un canal), en orden."""
    salida: list[str] = []
    token: str | None = None
    for _ in range(MAX_PAGINAS):
        params: dict[str, Any] = {"part": "contentDetails", "maxResults": POR_PAGINA,
                                  "playlistId": lista}
        if token:
            params["pageToken"] = token
        datos = _get(http, "playlistItems", params)
        for item in datos.get("items") or []:
            vid = (item.get("contentDetails") or {}).get("videoId")
            if vid and vid not in salida:
                salida.append(str(vid))
        token = datos.get("nextPageToken")
        if not token:
            break
    return salida


def detalles(http: httpx.Client, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Los datos de cada vídeo (título, descripción, duración, idiomas,
    etiquetas, canal, fecha), de 50 en 50. Un id que ya no existe no sale."""
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), POR_PAGINA):
        lote = ids[i:i + POR_PAGINA]
        datos = _get(http, "videos", {"part": "snippet,contentDetails,status",
                                      "id": ",".join(lote), "maxResults": POR_PAGINA})
        for item in datos.get("items") or []:
            if item.get("id"):
                salida[str(item["id"])] = item
    return salida


def duracion_segundos(iso: str | None) -> int | None:
    """«PT2M36S» → 156. None si no se entiende."""
    m = _DURACION_RE.match(str(iso or "").strip())
    if not m or not any(m.groups()):
        return None
    dias, horas, minutos, segundos = (int(g or 0) for g in m.groups())
    return dias * 86400 + horas * 3600 + minutos * 60 + segundos


def url_miniatura(video_id: str, calidad: str) -> str:
    return f"{MINIATURAS}/{video_id}/{calidad}.jpg"


def calidad_miniatura(http: httpx.Client, video_id: str) -> str | None:
    """La miniatura buena si existe (`maxresdefault`), la segura si no
    (`mqdefault`); None si img.youtube.com no ha contestado (se vuelve a probar
    en la siguiente pasada)."""
    try:
        respuesta = http.head(url_miniatura(video_id, MINIATURA_BUENA))
    except httpx.HTTPError:
        return None
    if respuesta.status_code == 200:
        return MINIATURA_BUENA
    if respuesta.status_code == 404:
        return MINIATURA_SEGURA
    return None
