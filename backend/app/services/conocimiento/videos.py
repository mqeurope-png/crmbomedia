"""La tabla de vídeos de los correos compuestos (solo YouTube).

De dónde salen (`kb_videos.origen`):

- `canal`: los vídeos de los canales de YouTube del grupo (la lista vive en la
  configuración de la base de conocimiento, `youtube_canales`; de partida
  @MQEuropebvba, @artisjeteurope, @fluxinc y @artisJet), con la API de datos y
  la clave `YOUTUBE_API_KEY` (ver `youtube.py`).
- `web`: los enlazados en las páginas y entradas de las webs del grupo que no
  son de esos canales.
- `manual`: pegados a mano desde la pantalla (una URL de YouTube suelta).

Lo que se deduce y se puede corregir a mano (lo corregido no lo pisa la
sincronización, `manual_json`):

- El **idioma**: el del título cuando sus palabras lo dicen claro (`titulo`);
  si no, el de los metadatos del vídeo en YouTube (`defaultLanguage`) y, por
  último, el del audio (`defaultAudioLanguage`). Medido el 12/10/2026: el del
  audio dice «en» en los veinte vídeos de FREEBIRD en neerlandés, alemán,
  francés, castellano y portugués, y «de-DE» en quince vídeos en castellano de
  mqeurope; el título y los metadatos aciertan.
- El **tema** agrupa el mismo vídeo en varios idiomas, que es lo que permite
  elegir la versión del idioma del cliente: se detecta cuando un canal sube el
  mismo día varios vídeos de la misma duración, cada uno en un idioma (así
  llegaron los de FREEBIRD: cuatro vídeos en cinco idiomas).
- La **marca** y los **productos** del catálogo, por el título: el nombre de
  la máquina o su número de modelo («artis 3000U Pro» es la «artisJet
  3000Pro»), y la marca del canal si el título no la dice. Los vídeos de un
  mismo tema comparten productos.
- La **miniatura**: `maxresdefault` si existe, si no `mqdefault`, y cuál se
  usó; la de `mqdefault` se vuelve a probar en cada pasada.

Un «short» (#shorts, vertical) se guarda pero no se le ofrece a la IA. Lo que
desaparece del canal o de las webs se marca `ido_en`, no se borra; lo pegado a
mano no se va nunca.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.conocimiento import (
    FUENTE_VIDEOS,
    SYNC_ERROR,
    SYNC_PROGRAMADA,
    VIDEO_ORIGEN_CANAL,
    VIDEO_ORIGEN_MANUAL,
    VIDEO_ORIGEN_WEB,
    CatalogoSincronizacion,
    ProductoCatalogo,
    VideoKb,
)
from app.services.conocimiento import catalogo, imagenes, youtube
from app.services.web_forms import sitios as registro_sitios

logger = logging.getLogger(__name__)

PLATAFORMA = "youtube"
IDIOMAS: tuple[str, ...] = ("es", "en", "de", "fr", "nl", "pt", "it")
IDIOMA_RESPALDO = "en"
#: Los campos que se pueden fijar a mano (y entonces no los pisa la pasada).
CAMPOS_MANUALES: tuple[str, ...] = ("idioma", "tema", "marca", "productos")
MAX_DESCRIPCION = 2000
#: Cuántos se le ofrecen a la IA en un turno.
PARA_CORREO_MAX = 6
EN_PARALELO = 8

SIN_CLAVE = ("Sin clave de YouTube: falta YOUTUBE_API_KEY en .env.production (la API de datos "
             "solo hace falta para listar los vídeos de los canales).")

FUENTE_IDIOMA_MANUAL = "manual"
FUENTE_IDIOMA_TITULO = "titulo"
FUENTE_IDIOMA_METADATOS = "metadatos"
FUENTE_IDIOMA_AUDIO = "audio"

_YOUTUBE_EN_HTML_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:[^\"'<>\s]*?&(?:amp;)?)?v=|embed/|shorts/|live/)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})")
_CORTO_RE = re.compile(r"#\s*(?:youtube)?shorts?\b", re.IGNORECASE)


# --- el idioma ------------------------------------------------------------------------------

#: Palabras que solo (o casi solo) se escriben en un idioma. Las que comparten
#: varios («de», «la», «en», «a», «clics», «resultados») no cuentan.
_MARCADORES: dict[str, frozenset[str]] = {
    "es": frozenset({
        "con", "el", "los", "las", "del", "para", "tus", "tu", "sus", "más", "cómo", "como",
        "donde", "dónde", "se", "y", "nuevo", "nueva", "nuevas", "nuevos", "impresora",
        "impresoras", "impresión", "personalización", "menos", "revolución", "acelera",
        "proyectos", "convierte", "versatilidad", "posibilidades", "funciona", "funcionan",
        "configurar", "configuración", "perfiles", "instalación", "costes", "producción",
        "entrar", "papel", "horno", "guía", "inicio", "crear", "sin", "límites", "descubriendo",
        "corte", "grabado", "camisetas", "velas", "funda", "móvil", "presentación", "español",
        "española", "habilitar", "importar", "archivos", "imágenes", "calibración", "pedido",
    }),
    "pt": frozenset({
        "com", "os", "seus", "seu", "sua", "suas", "da", "das", "mais", "não", "impressora",
        "impressoras", "impressão", "personalização", "cliques", "revolução", "acelere",
        "projetos", "onde", "encontra", "versatilidade", "possibilidades", "em", "é", "uma",
        "também", "você",
    }),
    "fr": frozenset({
        "avec", "le", "les", "des", "vos", "votre", "pour", "plus", "où", "comment", "moins",
        "résultats", "imprimante", "imprimantes", "rime", "polyvalence", "accélérez", "projets",
        "personnalisation", "révolution", "et", "du", "aux", "sur", "une", "découvrez",
        "nouvelle", "nouveau", "impression",
    }),
    "de": frozenset({
        "mit", "der", "die", "das", "und", "ihre", "ihr", "sie", "wie", "wird", "weniger",
        "mehr", "für", "von", "auf", "wo", "beschleunigen", "drucker", "ergebnisse", "klicks",
        "vielseitigkeit", "möglichkeiten", "trifft", "ein", "eine", "zu", "im", "den", "dem",
        "neue", "neuer", "druck", "drucken",
    }),
    "nl": frozenset({
        "met", "het", "je", "jouw", "van", "een", "meer", "minder", "hoe", "wordt", "waar",
        "voor", "op", "klikken", "resultaat", "versnel", "maatwerkprojecten", "printen",
        "veelzijdigheid", "mogelijkheden", "creëert", "revolutie", "telefoonhoesjes",
        "productinformatie", "universele", "etiketteerder", "automatische", "lader", "zakjes",
        "doosjes", "identificatie", "compacte", "producten", "ballen", "nieuwe",
    }),
    "en": frozenset({
        "the", "with", "your", "and", "for", "how", "to", "of", "on", "is", "are", "you",
        "new", "meet", "meets", "introducing", "where", "what", "why", "from", "our", "faster",
        "smarter", "custom", "large", "small", "test", "speed", "user", "users", "interview",
        "printing", "questions", "support", "applications", "guide", "tutorial", "unboxing",
        "demo", "maintenance", "daily", "weekly", "quarterly", "highlights", "launched", "into",
        "every", "more", "than", "this", "that", "its", "can", "made", "simple", "experience",
        "inspiration", "available", "now", "visit", "agent", "effect", "effects", "signs",
        "creative", "creativity", "deserves", "impact", "start", "stop", "changing", "people",
        "life", "lives", "workflow", "real", "transformed", "my", "smart", "low", "cost",
    }),
}
_ELISION_FR_RE = re.compile(r"(?<![\w])[dlj][’'](?=\w)", re.IGNORECASE)


def idioma_de_titulo(titulo: str | None) -> str | None:
    """El idioma del título cuando sus palabras lo dicen claro: al menos dos
    palabras propias de un idioma y al menos una más que el siguiente. None
    si no está claro (un título de nombres propios, «ARTIS YOUNG PROUD»)."""
    texto = str(titulo or "").lower()
    palabras = re.findall(r"[^\W\d_]+", texto)
    puntos = {idioma: sum(1 for p in palabras if p in marcadores)
              for idioma, marcadores in _MARCADORES.items()}
    puntos["fr"] += len(_ELISION_FR_RE.findall(texto))
    orden = sorted(puntos.items(), key=lambda kv: -kv[1])
    (mejor, n1), (_, n2) = orden[0], orden[1]
    if n1 >= 2 and n1 >= n2 + 1:
        return mejor
    return None


def _codigo_idioma(valor: Any) -> str | None:
    """«de-DE» → «de»; «pt-PT» → «pt»; uno que no es de los nuestros → None."""
    codigo = str(valor or "").strip().lower().replace("_", "-").split("-")[0]
    return codigo if codigo in IDIOMAS else None


def idioma_efectivo(titulo: str | None, metadatos: Any, audio: Any,
                    ) -> tuple[str | None, str | None]:
    """`(idioma, de dónde sale)`: el del título si está claro; si no, el de los
    metadatos de YouTube; si no, el del audio."""
    del_titulo = idioma_de_titulo(titulo)
    if del_titulo:
        return del_titulo, FUENTE_IDIOMA_TITULO
    if _codigo_idioma(metadatos):
        return _codigo_idioma(metadatos), FUENTE_IDIOMA_METADATOS
    if _codigo_idioma(audio):
        return _codigo_idioma(audio), FUENTE_IDIOMA_AUDIO
    return None, None


# --- el tema --------------------------------------------------------------------------------


def slug(texto: str | None, maximo: int = 60) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore")
    base = re.sub(r"[^a-z0-9]+", "-", sin_tildes.decode("ascii").lower()).strip("-")
    if len(base) > maximo:
        base = base[:maximo].rsplit("-", 1)[0]
    return base


def temas_detectados(videos: list[VideoKb]) -> dict[str, str]:
    """Video id de la fila → tema, para los grupos que se reconocen solos: el
    mismo canal, el mismo día de publicación y la misma duración (al segundo),
    dos o más vídeos y cada uno en un idioma distinto. El tema se nombra por el
    título en castellano (o en inglés, o el primero)."""
    grupos: dict[tuple[str, str, int], list[VideoKb]] = {}
    for v in videos:
        if not v.canal_id or not v.publicado_en or not v.duracion or not v.idioma:
            continue
        clave = (v.canal_id, v.publicado_en.date().isoformat(), int(v.duracion))
        grupos.setdefault(clave, []).append(v)
    salida: dict[str, str] = {}
    for miembros in grupos.values():
        idiomas = [m.idioma for m in miembros]
        if len(miembros) < 2 or len(set(idiomas)) != len(idiomas):
            continue
        nombre = next((m.titulo for m in miembros if m.idioma == "es"), None) or next(
            (m.titulo for m in miembros if m.idioma == "en"), None) or miembros[0].titulo
        tema = slug(nombre) or slug(miembros[0].video_id)
        for m in miembros:
            salida[m.id] = tema
    return salida


# --- marca y productos --------------------------------------------------------------------------


def _nucleo(token: str) -> str | None:
    """Las cifras de un número de modelo de tres o más («3000U» → «3000»,
    «UV2512G» → «2512»); None si no tiene."""
    m = re.search(r"\d{3,}", token)
    return m.group(0) if m else None


def marca_en_titulo(titulo: str | None, marcas: Iterable[str]) -> str | None:
    """La marca del catálogo que nombra el título (por su clave: «artisJet»,
    «FLUX», «MBO3020»)."""
    texto = catalogo._clave(titulo)
    for marca in sorted(marcas, key=lambda m: -len(catalogo._clave(m))):
        clave = catalogo._clave(marca)
        if len(clave) >= 3 and re.search(rf"(?<![a-z]){re.escape(clave)}", " ".join(
                re.findall(r"[a-z0-9]+", str(titulo or "").lower()))):
            return marca
        if len(clave) >= 5 and clave in texto:
            return marca
    return None


def productos_en_titulo(titulo: str | None, grupos: dict[str, list[ProductoCatalogo]],
                        marca: str | None) -> list[dict[str, str]]:
    """Las máquinas del catálogo que nombra el título: por su nombre (el de
    `catalogo.menciones_en`; con una sola palabra del nombre, solo si la marca
    del vídeo es la de la máquina) o por su número de modelo dentro de la
    marca del vídeo («artis 3000U Pro» → «artisJet 3000Pro»). Sin marca, el
    número solo no basta («stand 3000» no es ninguna máquina)."""
    salida: list[dict[str, str]] = []
    vistos: set[str] = set()
    clave_marca = catalogo._clave(marca) if marca else ""

    def _misma_marca(producto: ProductoCatalogo) -> bool:
        return not clave_marca or catalogo._clave(producto.marca) == clave_marca

    # En minúsculas: en un título de YouTube Todas Las Palabras Van En Mayúscula,
    # y una palabra suelta no es un nombre propio por eso.
    for origen_id, fuerza in catalogo.menciones_en(str(titulo or "").lower(), grupos):
        if fuerza == catalogo.FUERZA_DESCRIPCION or origen_id in vistos:
            continue
        base = grupos[origen_id][0]
        if not _misma_marca(base) or (fuerza == catalogo.FUERZA_PALABRA and not clave_marca):
            continue
        vistos.add(origen_id)
        salida.append({"fuente": base.fuente, "origen_id": origen_id, "nombre": base.nombre})
    nucleos_titulo = {n for t in re.findall(r"[a-z0-9]+", str(titulo or "").lower())
                      if (n := _nucleo(t))}
    if nucleos_titulo and clave_marca:
        for origen_id, variantes in grupos.items():
            base = variantes[0]
            if origen_id in vistos or not _misma_marca(base):
                continue
            nucleos = {n for t in re.findall(r"[a-z0-9]+", base.nombre.lower())
                       if (n := _nucleo(t))}
            if nucleos and nucleos <= nucleos_titulo:
                vistos.add(origen_id)
                salida.append({"fuente": base.fuente, "origen_id": origen_id,
                               "nombre": base.nombre})
    return salida


def _es_corto(titulo: str | None, descripcion: str | None, etiquetas: Iterable[str]) -> bool:
    return bool(_CORTO_RE.search(f"{titulo or ''}\n{descripcion or ''}")) or any(
        catalogo._clave(e) in ("shorts", "short", "youtubeshorts") for e in etiquetas)


# --- la configuración de los canales ------------------------------------------------------------


def canales_configurados(session: Session) -> list[dict[str, str]]:
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    return list(configuracion(session).get("youtube_canales") or [])


def _marcas_catalogo(session: Session) -> list[str]:
    return [m.nombre for m in catalogo.marcas(session) if m.ido_en is None]


# --- la pasada ---------------------------------------------------------------------------------


def _manuales(fila: VideoKb) -> set[str]:
    return {str(c) for c in catalogo._json_lista(fila.manual_json)}


def _aplicar_detalles(fila: VideoKb, item: dict[str, Any], *,
                      marca_de_canal: dict[str, str] | None = None,
                      canales_leidos: Iterable[str] = ()) -> bool:
    """Los datos de YouTube en la fila; True si algo cambió. El idioma no se
    toca si se fijó a mano. La marca del canal (la de la configuración) se
    guarda con la fila, y lo que se deduce luego fuera de la pasada (volver a
    lo automático, un vídeo pegado) la sigue teniendo; si su canal no se leyó
    en esta pasada, se queda la que tenía."""
    sn = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    cd = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    titulo = str(sn.get("title") or "").strip()[:255]
    descripcion = str(sn.get("description") or "").strip()[:MAX_DESCRIPCION] or None
    etiquetas = [str(t) for t in sn.get("tags") or [] if t][:30]
    publicado = imagenes._fecha(sn.get("publishedAt"))
    canal_id = str(sn.get("channelId") or "")[:40] or None
    if canal_id and canal_id in set(canales_leidos):
        marca_canal = (marca_de_canal or {}).get(canal_id) or None
    else:
        marca_canal = catalogo._json_dict(fila.datos_json).get("marca_canal") or None
    datos = {
        "titulo": titulo, "descripcion": descripcion,
        "duracion": youtube.duracion_segundos(cd.get("duration")),
        "publicado_en": publicado,
        "canal": str(sn.get("channelTitle") or "")[:120] or None,
        "canal_id": canal_id,
        "corto": _es_corto(titulo, descripcion, etiquetas),
        "datos_json": json.dumps({
            "idioma_metadatos": sn.get("defaultLanguage"),
            "idioma_audio": sn.get("defaultAudioLanguage"), "etiquetas": etiquetas,
            "marca_canal": marca_canal,
        }, ensure_ascii=False),
    }
    if "idioma" not in _manuales(fila):
        idioma, fuente = idioma_efectivo(titulo, sn.get("defaultLanguage"),
                                         sn.get("defaultAudioLanguage"))
        datos["idioma"], datos["idioma_fuente"] = idioma, fuente
    cambio = False
    for campo, valor in datos.items():
        anterior = getattr(fila, campo)
        if isinstance(anterior, datetime) and isinstance(valor, datetime):
            iguales = anterior.replace(tzinfo=anterior.tzinfo or UTC) == valor
        else:
            iguales = anterior == valor
        if not iguales:
            setattr(fila, campo, valor)
            cambio = True
    return cambio


def _nuevo(video_id: str, origen: str, ahora: datetime) -> VideoKb:
    return VideoKb(plataforma=PLATAFORMA, video_id=video_id, origen=origen,
                   url=f"https://www.youtube.com/watch?v={video_id}", titulo="", marca="",
                   visible=True, visto_en=ahora)


def enlazados_en_webs(session: Session, http: httpx.Client) -> tuple[dict[str, set[str]],
                                                                       list[str]]:
    """Los vídeos de YouTube enlazados en las páginas y entradas de cada web:
    id → webs. Y las webs que no se pudieron leer."""
    salida: dict[str, set[str]] = {}
    caidas: list[str] = []
    pares = [(sitio, tipo) for sitio in registro_sitios.cargar(session).webs
             for tipo in ("pages", "posts")]

    def _leer(par: tuple[Any, str]) -> Any:
        sitio, tipo = par
        try:
            return imagenes._paginas(http, f"https://{sitio.web}/wp-json/wp/v2/{tipo}",
                                     {"_fields": "content"}, f"{sitio.clave} {tipo}",
                                     por_pagina=50)
        except imagenes.MediosError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=imagenes.EN_PARALELO) as pool:
        leidos = list(pool.map(_leer, pares))
    for (sitio, tipo), elementos in zip(pares, leidos, strict=True):
        if isinstance(elementos, Exception):
            caidas.append(f"{sitio.clave} {tipo}: {str(elementos)[:160]}")
            continue
        for e in elementos or []:
            contenido = e.get("content")
            html = contenido.get("rendered") if isinstance(contenido, dict) else ""
            for vid in _YOUTUBE_EN_HTML_RE.findall(str(html or "")):
                salida.setdefault(vid, set()).add(sitio.clave)
    return salida, caidas


def comprobar_miniaturas(filas: list[VideoKb], http: httpx.Client, *, ahora: datetime,
                         ) -> dict[str, int]:
    """La miniatura de cada vídeo que aún no tiene la buena: `maxresdefault` si
    existe, si no `mqdefault`. Lo que img.youtube.com no conteste se queda como
    estaba y se prueba en la siguiente pasada."""
    pendientes = [f for f in filas if f.miniatura_calidad != youtube.MINIATURA_BUENA]
    with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
        calidades = list(pool.map(lambda f: youtube.calidad_miniatura(http, f.video_id),
                                  pendientes))
    recuento = {"buena": 0, "segura": 0, "sin_respuesta": 0}
    for fila, calidad in zip(pendientes, calidades, strict=True):
        if calidad is None:
            recuento["sin_respuesta"] += 1
            if not fila.miniatura:
                fila.miniatura = youtube.url_miniatura(fila.video_id, youtube.MINIATURA_SEGURA)
                fila.miniatura_calidad = youtube.MINIATURA_SEGURA
            continue
        fila.miniatura = youtube.url_miniatura(fila.video_id, calidad)
        fila.miniatura_calidad = calidad
        fila.miniatura_comprobada_en = ahora
        recuento["buena" if calidad == youtube.MINIATURA_BUENA else "segura"] += 1
    return recuento


def deducir(session: Session, filas: list[VideoKb]) -> None:
    """Marca, productos y tema de cada vídeo (lo fijado a mano se queda): la
    marca por el título o por la de su canal (la guardada en la fila); las
    máquinas por el título; los temas por los grupos que se reconocen solos, y
    los vídeos de un tema comparten productos."""
    grupos = catalogo._maquinas_por_producto(session)
    marcas = _marcas_catalogo(session)
    for f in filas:
        manuales = _manuales(f)
        marca = (f.marca if "marca" in manuales else None) or marca_en_titulo(f.titulo, marcas) \
            or catalogo._json_dict(f.datos_json).get("marca_canal") or ""
        if "productos" not in manuales:
            productos = productos_en_titulo(f.titulo, grupos, marca or None)
            f.productos_json = json.dumps(productos, ensure_ascii=False)
            if "marca" not in manuales and not marca:
                propias = {p["origen_id"] for p in productos}
                marcas_productos = {grupos[o][0].marca for o in propias if o in grupos}
                if len(marcas_productos) == 1:
                    marca = marcas_productos.pop()
        if "marca" not in manuales:
            f.marca = (marca or "")[:80]
    detectados = temas_detectados(filas)
    for f in filas:
        if "tema" not in _manuales(f):
            f.tema = detectados.get(f.id)
    # Los vídeos de un tema son el mismo vídeo: comparten productos.
    por_tema: dict[str, list[VideoKb]] = {}
    for f in filas:
        if f.tema:
            por_tema.setdefault(f.tema, []).append(f)
    for miembros in por_tema.values():
        union: dict[str, dict[str, str]] = {}
        for m in miembros:
            for p in catalogo._json_lista(m.productos_json):
                if isinstance(p, dict) and p.get("origen_id"):
                    union.setdefault(str(p["origen_id"]), p)
        if not union:
            continue
        for m in miembros:
            if "productos" not in _manuales(m):
                m.productos_json = json.dumps(list(union.values()), ensure_ascii=False)


def sincronizar(
    session: Session, *, origen: str = SYNC_PROGRAMADA, http: httpx.Client | None = None,
    http_webs: httpx.Client | None = None, ahora: datetime | None = None,
    api_key: str | None = None, canales: list[dict[str, str]] | None = None,
    permitir_sin_clave: bool = False, con_webs: bool = True,
) -> CatalogoSincronizacion:
    """Una pasada: los canales, los vídeos enlazados en las webs, los pegados a
    mano (se refrescan sus datos), las miniaturas y lo que se deduce. Sin clave
    o con la API caída: estado `error`, motivo en `detalle`, tabla anterior
    intacta. Devuelve la fila del histórico (el que llama hace commit)."""
    from app.core.config import get_settings  # noqa: PLC0415

    ahora = ahora or datetime.now(UTC)
    clave = api_key if api_key is not None else (get_settings().youtube_api_key or "")
    pasada = catalogo.abrir_pasada(session, fuente=FUENTE_VIDEOS, origen=origen, ahora=ahora)
    if not clave and not permitir_sin_clave:
        pasada.estado = SYNC_ERROR
        pasada.detalle = SIN_CLAVE
        pasada.fin = datetime.now(UTC)
        session.flush()
        logger.warning("kb.videos: sin sincronizar: %s", SIN_CLAVE)
        return pasada
    lista_canales = canales if canales is not None else canales_configurados(session)
    propio = http is None
    propio_webs = http_webs is None
    yt = http or youtube.cliente(clave or None)
    webs_http = http_webs or imagenes._cliente()
    errores: list[str] = []
    resumen_canales: dict[str, dict[str, Any]] = {}
    try:
        por_canal: dict[str, list[str]] = {}
        marca_de_canal: dict[str, str] = {}
        for c in lista_canales:
            handle = str(c.get("handle") or "").strip()
            try:
                info = youtube.canal(yt, handle)
                if info is None or not info.get("subidas"):
                    raise youtube.YoutubeError(f"{handle}: el canal no existe")
                ids = youtube.ids_de_lista(yt, str(info["subidas"]))
            except youtube.YoutubeError as exc:
                errores.append(str(exc)[:300])
                resumen_canales[handle] = {"videos": 0, "error": str(exc)[:300]}
                continue
            por_canal[str(info["id"])] = ids
            if c.get("marca"):
                marca_de_canal[str(info["id"])] = str(c["marca"])
            resumen_canales[handle] = {"canal": info.get("titulo"), "videos": len(ids),
                                       "error": None}
        enlazados: dict[str, set[str]] = {}
        caidas: list[str] = []
        if con_webs:
            enlazados, caidas = enlazados_en_webs(session, webs_http)
        existentes = {f.video_id: f for f in session.scalars(
            select(VideoKb).where(VideoKb.plataforma == PLATAFORMA))}
        de_canales = {vid for ids in por_canal.values() for vid in ids}
        webs_leidas = con_webs and not caidas
        pedir = list(dict.fromkeys(
            [vid for ids in por_canal.values() for vid in ids] + sorted(enlazados)
            + [vid for vid, f in existentes.items()
               if f.origen == VIDEO_ORIGEN_MANUAL
               or (f.origen == VIDEO_ORIGEN_WEB and not webs_leidas)]))
        try:
            detalles = youtube.detalles(yt, pedir) if pedir else {}
        except youtube.YoutubeError as exc:
            pasada.estado = SYNC_ERROR
            pasada.detalle = f"Los datos de los vídeos no se pudieron leer: {str(exc)[:600]}"
            pasada.resumen_json = json.dumps({"canales": resumen_canales}, ensure_ascii=False)
            pasada.fin = datetime.now(UTC)
            session.flush()
            logger.warning("kb.videos: sin sincronizar, tabla anterior intacta: %s", exc)
            return pasada
        nuevos = actualizados = idos = 0
        tocados: list[VideoKb] = []
        for vid in pedir:
            item = detalles.get(vid)
            fila = existentes.get(vid)
            if item is None:
                # Borrado o privado en YouTube: lo que no es a mano se va.
                if fila is not None and fila.origen != VIDEO_ORIGEN_MANUAL and fila.ido_en is None:
                    fila.ido_en = ahora
                    idos += 1
                continue
            if fila is None:
                fila = _nuevo(vid, VIDEO_ORIGEN_CANAL if vid in de_canales else VIDEO_ORIGEN_WEB,
                              ahora)
                session.add(fila)
                existentes[vid] = fila
                nuevos += 1
                _aplicar_detalles(fila, item, marca_de_canal=marca_de_canal,
                                  canales_leidos=por_canal)
            else:
                cambio = _aplicar_detalles(fila, item, marca_de_canal=marca_de_canal,
                                           canales_leidos=por_canal) or fila.ido_en is not None
                if fila.origen == VIDEO_ORIGEN_WEB and vid in de_canales:
                    fila.origen = VIDEO_ORIGEN_CANAL
                    cambio = True
                actualizados += int(cambio)
            webs = sorted(enlazados.get(vid, set()))
            fila.webs_json = json.dumps(webs) if webs else None
            fila.ido_en = None
            fila.visto_en = ahora
            tocados.append(fila)
        # Lo que ya no está en su canal (si ese canal se leyó) ni enlazado en
        # las webs (si se leyeron todas) se va; lo pegado a mano, nunca.
        canales_leidos = set(por_canal)
        for vid, fila in existentes.items():
            if fila.ido_en is not None or fila.origen == VIDEO_ORIGEN_MANUAL or vid in pedir:
                continue
            if fila.origen == VIDEO_ORIGEN_CANAL and fila.canal_id in canales_leidos:
                fila.ido_en = ahora
                idos += 1
            elif fila.origen == VIDEO_ORIGEN_WEB and webs_leidas:
                fila.ido_en = ahora
                idos += 1
        session.flush()
        miniaturas = comprobar_miniaturas(tocados, webs_http, ahora=ahora)
        vigentes = list(session.scalars(select(VideoKb).where(
            VideoKb.plataforma == PLATAFORMA, VideoKb.ido_en.is_(None))))
        deducir(session, vigentes)
    finally:
        if propio:
            yt.close()
        if propio_webs:
            webs_http.close()
    resumen = {
        "total": {"productos": len(vigentes), "nuevos": nuevos, "actualizados": actualizados,
                  "idos": idos, "error": None},
        "canales": resumen_canales,
        "webs": {"enlazados": len(enlazados), "caidas": caidas},
        "miniaturas": miniaturas,
    }
    # Error si no se pudo leer ningún canal; parcial si falló alguno o alguna web.
    errores.extend(f"web {c}" for c in caidas)
    catalogo.cerrar_pasada(pasada, resumen, errores,
                           total=len(lista_canales) + (len(caidas) if por_canal else 0))
    session.flush()
    logger.info("kb.videos: %s — %d vigentes, %d nuevos, %d cambiados, %d idos; miniaturas %s%s",
                pasada.estado, pasada.productos, nuevos, actualizados, idos, miniaturas,
                f"; {len(errores)} errores" if errores else "")
    return pasada


# --- a mano ------------------------------------------------------------------------------------


def anadir_por_url(
    session: Session, url: str, *, http: httpx.Client | None = None,
    http_miniaturas: httpx.Client | None = None, api_key: str | None = None,
    ahora: datetime | None = None,
) -> VideoKb:
    """Un vídeo pegado a mano. Si ya está, el que hay (vuelve si se había ido).
    Con la clave se piden sus datos a YouTube; sin ella entra con lo que da su
    URL (la miniatura) y el título se pone a mano."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services.correo_compuesto import videos as videos_correo  # noqa: PLC0415

    identidad = videos_correo.de_url(url)
    if identidad is None or identidad["plataforma"] != PLATAFORMA:
        raise ValueError("Pega la URL de un vídeo de YouTube (watch, youtu.be o shorts).")
    ahora = ahora or datetime.now(UTC)
    vid = identidad["id"]
    fila = session.scalar(select(VideoKb).where(VideoKb.plataforma == PLATAFORMA,
                                                VideoKb.video_id == vid))
    if fila is not None:
        if fila.ido_en is not None:
            fila.ido_en = None
            fila.origen = VIDEO_ORIGEN_MANUAL
            fila.visto_en = ahora
            session.flush()
        return fila
    clave = api_key if api_key is not None else (get_settings().youtube_api_key or "")
    fila = _nuevo(vid, VIDEO_ORIGEN_MANUAL, ahora)
    session.add(fila)
    if clave or http is not None:
        propio = http is None
        yt = http or youtube.cliente(clave)
        try:
            item = youtube.detalles(yt, [vid]).get(vid)
        except youtube.YoutubeError as exc:
            logger.warning("kb.videos: datos de %s sin leer: %s", vid, exc)
            item = None
        finally:
            if propio:
                yt.close()
        if item is not None:
            _aplicar_detalles(fila, item)
    propio_img = http_miniaturas is None
    img = http_miniaturas or imagenes._cliente()
    try:
        comprobar_miniaturas([fila], img, ahora=ahora)
    finally:
        if propio_img:
            img.close()
    session.flush()
    deducir(session, [fila])
    session.flush()
    return fila


def actualizar(session: Session, fila_id: str, cambios: dict[str, Any]) -> VideoKb:
    """Lo que se corrige a mano: idioma, tema, marca, productos y si es
    visible. Lo corregido queda fijado (la pasada no lo pisa) salvo que se
    pida volver a lo automático (`automatico`: lista de campos)."""
    fila = session.get(VideoKb, fila_id)
    if fila is None:
        raise LookupError("Ese vídeo no está en la tabla.")
    manuales = _manuales(fila)
    if "idioma" in cambios:
        valor = str(cambios["idioma"] or "").strip().lower()
        if valor and valor not in IDIOMAS:
            raise ValueError(f"Idioma no válido: {cambios['idioma']!r} ({', '.join(IDIOMAS)}).")
        fila.idioma = valor or None
        fila.idioma_fuente = FUENTE_IDIOMA_MANUAL
        manuales.add("idioma")
    if "tema" in cambios:
        fila.tema = slug(cambios["tema"], 80) or None
        manuales.add("tema")
    if "marca" in cambios:
        fila.marca = str(cambios["marca"] or "").strip()[:80]
        manuales.add("marca")
    if "productos" in cambios:
        fila.productos_json = json.dumps(_validar_productos(session, cambios["productos"]),
                                         ensure_ascii=False)
        manuales.add("productos")
    if "visible" in cambios:
        if not isinstance(cambios["visible"], bool):
            raise ValueError("«Visible» tiene que ser sí o no.")
        fila.visible = cambios["visible"]
    automatico = [c for c in cambios.get("automatico") or [] if c in CAMPOS_MANUALES]
    manuales -= set(automatico)
    fila.manual_json = json.dumps(sorted(manuales)) if manuales else None
    if automatico:
        if "idioma" in automatico:
            datos = catalogo._json_dict(fila.datos_json)
            fila.idioma, fila.idioma_fuente = idioma_efectivo(
                fila.titulo, datos.get("idioma_metadatos"), datos.get("idioma_audio"))
        vigentes = list(session.scalars(select(VideoKb).where(VideoKb.ido_en.is_(None))))
        deducir(session, vigentes)
    session.flush()
    return fila


def _validar_productos(session: Session, valor: Any) -> list[dict[str, str]]:
    if not isinstance(valor, list):
        raise ValueError("Los productos tienen que ser una lista.")
    salida: list[dict[str, str]] = []
    for p in valor:
        origen_id = str((p.get("origen_id") if isinstance(p, dict) else p) or "").strip()
        if not origen_id or any(x["origen_id"] == origen_id for x in salida):
            continue
        fila = session.scalar(select(ProductoCatalogo).where(
            ProductoCatalogo.origen_id == origen_id).limit(1))
        if fila is None:
            raise ValueError(f"El producto «{origen_id}» no está en el catálogo.")
        salida.append({"fuente": fila.fuente, "origen_id": origen_id, "nombre": fila.nombre})
    return salida


# --- consultas ---------------------------------------------------------------------------------


def productos(fila: VideoKb) -> list[dict[str, str]]:
    return [p for p in catalogo._json_lista(fila.productos_json) if isinstance(p, dict)]


def como_dict(fila: VideoKb) -> dict[str, Any]:
    datos = catalogo._json_dict(fila.datos_json)
    return {
        "id": fila.id, "plataforma": fila.plataforma, "video_id": fila.video_id, "url": fila.url,
        "titulo": fila.titulo, "descripcion": fila.descripcion, "idioma": fila.idioma,
        "idioma_fuente": fila.idioma_fuente, "idioma_metadatos": datos.get("idioma_metadatos"),
        "idioma_audio": datos.get("idioma_audio"), "tema": fila.tema, "marca": fila.marca,
        "productos": productos(fila), "manual": sorted(_manuales(fila)),
        "miniatura": fila.miniatura, "miniatura_calidad": fila.miniatura_calidad,
        "duracion": fila.duracion, "corto": bool(fila.corto), "canal": fila.canal,
        "origen": fila.origen, "webs": [str(w) for w in catalogo._json_lista(fila.webs_json)],
        "visible": bool(fila.visible),
        "publicado_en": fila.publicado_en.isoformat() if fila.publicado_en else None,
        "ido_en": fila.ido_en.isoformat() if fila.ido_en else None,
    }


def listar(
    session: Session, *, texto: str | None = None, idioma: str | None = None,
    canal: str | None = None, origen: str | None = None, tema: str | None = None,
    con_ocultos: bool = True, con_cortos: bool = True, con_idos: bool = False,
    limite: int = 1000,
) -> list[VideoKb]:
    """Los vídeos de la pantalla, los más recientes primero."""
    consulta = select(VideoKb)
    if not con_idos:
        consulta = consulta.where(VideoKb.ido_en.is_(None))
    if not con_ocultos:
        consulta = consulta.where(VideoKb.visible.is_(True))
    if not con_cortos:
        consulta = consulta.where(VideoKb.corto.is_(False))
    if idioma:
        consulta = consulta.where(VideoKb.idioma == idioma)
    if canal:
        consulta = consulta.where(VideoKb.canal == canal)
    if origen:
        consulta = consulta.where(VideoKb.origen == origen)
    if tema:
        consulta = consulta.where(VideoKb.tema == tema)
    consulta = consulta.order_by(VideoKb.publicado_en.desc(), VideoKb.titulo)
    try:
        filas = list(session.scalars(consulta))
    except (OperationalError, ProgrammingError):
        return []
    palabras = imagenes._normalizar(texto).split()
    if palabras:
        filas = [f for f in filas if all(
            p in imagenes._normalizar(f"{f.titulo} {f.tema or ''} {f.video_id}") for p in palabras)]
    return filas[:max(1, min(int(limite), 5000))]


def estado(session: Session) -> dict[str, Any]:
    from app.core.config import get_settings  # noqa: PLC0415

    try:
        por_idioma = session.execute(
            select(VideoKb.idioma, func.count()).where(VideoKb.ido_en.is_(None))
            .group_by(VideoKb.idioma)).all()
        por_canal = session.execute(
            select(VideoKb.canal, func.count()).where(VideoKb.ido_en.is_(None))
            .group_by(VideoKb.canal)).all()
        temas = session.scalar(select(func.count(func.distinct(VideoKb.tema))).where(
            VideoKb.ido_en.is_(None), VideoKb.tema.is_not(None))) or 0
        ocultos = session.scalar(select(func.count()).select_from(VideoKb).where(
            VideoKb.ido_en.is_(None), VideoKb.visible.is_(False))) or 0
        cortos = session.scalar(select(func.count()).select_from(VideoKb).where(
            VideoKb.ido_en.is_(None), VideoKb.corto.is_(True))) or 0
    except (OperationalError, ProgrammingError):
        por_idioma, por_canal, temas, ocultos, cortos = [], [], 0, 0, 0
    return {
        "total": sum(int(n) for _, n in por_idioma),
        "por_idioma": {(i or "—"): int(n) for i, n in por_idioma},
        "por_canal": {(c or "—"): int(n) for c, n in por_canal},
        "temas": int(temas), "ocultos": int(ocultos), "cortos": int(cortos),
        "clave_configurada": bool(get_settings().youtube_api_key),
        "ultima_sincronizacion": catalogo.sincronizacion_como_dict(
            catalogo.ultima_sincronizacion(session, FUENTE_VIDEOS)),
    }


# --- lo que ve el correo -----------------------------------------------------------------------


def _version(miembros: list[VideoKb], idioma: str) -> VideoKb | None:
    """La versión de un tema en el idioma pedido; si no la hay, en inglés."""
    for codigo in (idioma, IDIOMA_RESPALDO):
        for m in miembros:
            if m.idioma == codigo:
                return m
    return None


def para_correo(
    session: Session, *, idioma: str, productos: Iterable[str] = (),
    palabras: Iterable[str] = (), maximo: int = PARA_CORREO_MAX,
) -> list[dict[str, Any]]:
    """Los vídeos que se le ofrecen a la IA: los de las máquinas del correo
    (`productos`: sus `origen_id`) y los que casan con las palabras de la
    instrucción, visibles y sin «shorts». De un tema, la versión del idioma del
    correo o, si no la hay, la inglesa (y si tampoco, ninguna); un vídeo sin
    tema, si está en ese idioma, en inglés o no se sabe en cuál."""
    ids_productos = {str(p) for p in productos if p}
    lista_palabras = [p for p in palabras if p]
    try:
        filas = list(session.scalars(select(VideoKb).where(
            VideoKb.ido_en.is_(None), VideoKb.visible.is_(True), VideoKb.corto.is_(False))))
    except (OperationalError, ProgrammingError):
        return []
    puntuados: list[tuple[int, VideoKb]] = []
    for f in filas:
        de_producto = bool(ids_productos & {p.get("origen_id") for p in productos_de_fila(f)})
        puntos_texto = imagenes._puntos(f"{f.titulo} {f.tema or ''}", lista_palabras)
        if not de_producto and puntos_texto <= 0:
            continue
        puntuados.append(((10 if de_producto else 0) + puntos_texto, f))
    por_tema: dict[str, list[VideoKb]] = {}
    for f in filas:
        if f.tema:
            por_tema.setdefault(f.tema, []).append(f)
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    puntuados.sort(key=lambda pf: (-pf[0], -(pf[1].publicado_en.timestamp()
                                             if pf[1].publicado_en else 0)))
    for _, f in puntuados:
        if f.tema:
            if f.tema in vistos:
                continue
            vistos.add(f.tema)
            elegido = _version(por_tema.get(f.tema, [f]), idioma)
            if elegido is None:
                continue
        else:
            if f.idioma not in (idioma, IDIOMA_RESPALDO, None):
                continue
            elegido = f
        salida.append({"plataforma": elegido.plataforma, "id": elegido.video_id,
                       "url": elegido.url, "titulo": elegido.titulo, "idioma": elegido.idioma,
                       "duracion": elegido.duracion})
        if len(salida) >= maximo:
            break
    return salida


def productos_de_fila(fila: VideoKb) -> list[dict[str, str]]:
    return productos(fila)


def por_identidades(session: Session, identidades: Iterable[dict[str, str]], *, idioma: str,
                    ) -> dict[str, VideoKb]:
    """Para el generador: `"youtube:{id}"` → la fila con los datos de ese vídeo,
    o de su versión en el idioma del correo si es de un tema que la tiene."""
    ids = [i["id"] for i in identidades if i.get("plataforma") == PLATAFORMA and i.get("id")]
    if not ids:
        return {}
    try:
        filas = list(session.scalars(select(VideoKb).where(
            VideoKb.plataforma == PLATAFORMA, VideoKb.video_id.in_(ids))))
        temas = sorted({f.tema for f in filas if f.tema})
        hermanos = list(session.scalars(select(VideoKb).where(
            VideoKb.tema.in_(temas), VideoKb.ido_en.is_(None),
            VideoKb.visible.is_(True)))) if temas else []
    except (OperationalError, ProgrammingError):
        return {}
    por_tema: dict[str, list[VideoKb]] = {}
    for h in hermanos:
        por_tema.setdefault(str(h.tema), []).append(h)
    salida: dict[str, VideoKb] = {}
    for f in filas:
        elegido = f
        if f.tema and f.idioma != idioma:
            elegido = next((h for h in por_tema.get(f.tema, []) if h.idioma == idioma), f)
        salida[f"{PLATAFORMA}:{f.video_id}"] = elegido
    return salida
