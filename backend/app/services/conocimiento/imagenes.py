"""Las imágenes de las webs para los correos compuestos (hero y muestras).

El índice sale de la biblioteca de medios de cada web del grupo (WordPress, API
REST pública, sin credenciales):

    GET https://{host}/wp-json/wp/v2/media?media_type=image&per_page=100&page=N

Se pagina por `X-WP-TotalPages`: las páginas pueden venir cortas (WordPress
quita después de paginar los adjuntos de entradas privadas), así que una
página de menos de 100 no es la última.

Solo se indexa lo que vale para un hero o una galería de muestras (medido el
11-12/10/2026 sobre las ocho webs: de 4.697 imágenes pasan unas 350):

1. ancho ≥ 1200 px;
2. apaisada: ancho / alto entre 1,3 y 3,2;
3. el título no es de un logo, icono, recorte, captura, firma, sello o QR, ni
   un código de artículo («AB-1234», «MCO-21005_2», «B100617»);
4. no está adjunta a un producto de la tienda: su imagen ya está en el
   catálogo (los productos se leen de la web, `/wp-json/wp/v2/product`, y de
   las tiendas del catálogo);
5. JPEG, PNG o GIF: Outlook no enseña WebP.

Las webs no tienen texto alternativo: el TÍTULO es lo único por lo que se
puede buscar, y es el `alt` de la imagen en el correo. De cada imagen se
guardan sus tamaños con las mismas proporciones (los recortes cuadrados del
tema, fuera) para pedir en el correo el que toca a su ancho, y no la original
de varios megas. Favoritas y ocultas se marcan a mano en la pantalla; una
web caída deja su índice como estaba; lo que desaparece de la web se marca
`ido_en`, no se borra.
"""
from __future__ import annotations

import html
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
    FUENTE_IMAGENES,
    FUENTE_WOOCOMMERCE,
    SYNC_PROGRAMADA,
    CatalogoSincronizacion,
    ImagenWeb,
    ProductoCatalogo,
)
from app.services.conocimiento import catalogo
from app.services.web_forms import sitios as registro_sitios
from app.services.web_forms.sitios import Sitio

logger = logging.getLogger(__name__)

RUTA_MEDIOS = "wp-json/wp/v2/media"
RUTA_PRODUCTOS = "wp-json/wp/v2/product"
CAMPOS_MEDIOS = ("id,date,title,mime_type,media_type,media_details,source_url,post,link")
POR_PAGINA = 100
#: 60 páginas de 100: ninguna web del grupo llega (fluxlasers.es, la mayor, 18).
MAX_PAGINAS = 60
TIMEOUT_SEGUNDOS = 30.0
#: Una página que no responde se pide otra vez (www.mqeurope.com tarda a veces).
REINTENTOS = 1
EN_PARALELO = 4

ANCHO_MIN = 1200
PROPORCION_MIN = 1.3
PROPORCION_MAX = 3.2
#: Un tamaño del tema con otras proporciones (más de un 3 %) es un recorte.
TOLERANCIA_PROPORCION = 0.03
MIMES_CORREO: tuple[str, ...] = ("image/jpeg", "image/png", "image/gif")

#: Lo que el título dice que no es una foto para un correo.
_RUIDO_RE = re.compile(
    r"logo|loguito|icon|favicon|cropped|screenshot|captura|firma|sello|(?<![a-z])qr(?![a-z])",
    re.IGNORECASE)
#: Un código de artículo: letras, guion y cifras («AB-1234», «MCO-21005_2»,
#: «PSTY-007») o una letra y cinco o seis cifras («B100617», recambios de FLUX).
_CODIGO_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,5}-\d{3,6}|[A-Za-z]\d{5,6})(?![0-9])")

#: Por qué no se indexó una imagen (los recuentos de la pasada).
DESCARTE_SIN_MEDIDAS = "sin_medidas"
DESCARTE_ESTRECHA = "estrecha"
DESCARTE_PROPORCION = "proporcion"
DESCARTE_TITULO = "titulo"
DESCARTE_CODIGO = "codigo"
DESCARTE_PRODUCTO = "producto"
DESCARTE_FORMATO = "formato"

#: Cuántas se le ofrecen a la IA en un turno.
PARA_CORREO_MAX = 12


class MediosError(RuntimeError):
    """La web no ha respondido bien (red, 5xx, JSON ilegible)."""


def _cliente() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SEGUNDOS), follow_redirects=True,
        headers={"User-Agent": "BoHub/kb-imagenes", "Accept": "application/json"},
    )


def _get(http: httpx.Client, url: str, params: dict[str, Any], etiqueta: str) -> httpx.Response:
    ultimo: Exception | None = None
    for _ in range(REINTENTOS + 1):
        try:
            respuesta = http.get(url, params=params)
        except httpx.HTTPError as exc:
            ultimo = exc
            continue
        if respuesta.status_code >= 500:
            ultimo = MediosError(f"{etiqueta}: HTTP {respuesta.status_code}")
            continue
        return respuesta
    raise MediosError(f"{etiqueta}: {type(ultimo).__name__}: {ultimo}") from ultimo


def _pagina(http: httpx.Client, url: str, params: dict[str, Any], etiqueta: str, pagina: int,
            por_pagina: int = POR_PAGINA) -> tuple[list[dict[str, Any]] | None, int]:
    """`(elementos, total de páginas)` de una página; `(None, 0)` si la ruta no
    existe (404) y `([], 0)` si la página ya no existe (400)."""
    respuesta = _get(http, url, {**params, "per_page": por_pagina, "page": pagina},
                     f"{etiqueta} p{pagina}")
    if respuesta.status_code == 404:
        return None, 0
    if respuesta.status_code == 400 and pagina > 1:
        return [], 0                                # «rest_post_invalid_page_number»
    if respuesta.status_code != 200:
        raise MediosError(f"{etiqueta} p{pagina}: HTTP {respuesta.status_code}")
    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise MediosError(f"{etiqueta} p{pagina}: respuesta ilegible") from exc
    if not isinstance(datos, list):
        raise MediosError(f"{etiqueta} p{pagina}: la respuesta no es una lista")
    try:
        total = int(respuesta.headers.get("X-WP-TotalPages") or 1)
    except ValueError:
        total = 1
    return [d for d in datos if isinstance(d, dict)], total


def _paginas(http: httpx.Client, url: str, params: dict[str, Any], etiqueta: str, *,
             por_pagina: int = POR_PAGINA) -> list[dict[str, Any]] | None:
    """Todas las páginas de una colección de la API REST de WordPress, por
    `X-WP-TotalPages`: la primera dice cuántas hay y las demás van en paralelo.
    None si la ruta no existe (404: esa web no la tiene)."""
    primera, total = _pagina(http, url, params, etiqueta, 1, por_pagina)
    if primera is None:
        return None
    if total > MAX_PAGINAS:
        logger.warning("kb.imagenes: %s cortado en %d páginas de %d", etiqueta, MAX_PAGINAS, total)
    resto = list(range(2, min(total, MAX_PAGINAS) + 1))
    salida = list(primera)
    if resto:
        with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
            for elementos, _ in pool.map(
                    lambda n: _pagina(http, url, params, etiqueta, n, por_pagina), resto):
                salida.extend(elementos or [])
    return salida


def listar_medios(http: httpx.Client, sitio: Sitio) -> list[dict[str, Any]]:
    """Las imágenes de la biblioteca de medios de la web (todas las páginas)."""
    url = f"https://{sitio.web}/{RUTA_MEDIOS}"
    medios = _paginas(http, url, {"media_type": "image", "_fields": CAMPOS_MEDIOS},
                      f"{sitio.clave} medios")
    if medios is None:
        raise MediosError(f"{sitio.clave}: sin API de medios (HTTP 404)")
    return medios


def ids_de_productos_web(http: httpx.Client, sitio: Sitio) -> set[int]:
    """Los ids de los productos de la tienda de la web (una web sin tienda:
    ninguno)."""
    url = f"https://{sitio.web}/{RUTA_PRODUCTOS}"
    productos = _paginas(http, url, {"_fields": "id"}, f"{sitio.clave} productos")
    return {int(p["id"]) for p in productos or [] if str(p.get("id") or "").isdigit()}


def ids_de_productos_catalogo(session: Session, clave_sitio: str) -> set[int]:
    """Los ids de WooCommerce de los productos de esa web que tiene el catálogo
    (todas sus variantes de idioma, vivas o idas)."""
    try:
        origenes = session.scalars(select(ProductoCatalogo.origen_id).where(
            ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE,
            ProductoCatalogo.sitio == clave_sitio))
    except (OperationalError, ProgrammingError):
        return set()
    salida: set[int] = set()
    for origen in origenes:
        resto = str(origen).rsplit(":", 1)[-1]
        if resto.isdigit():
            salida.add(int(resto))
    return salida


# --- los filtros -------------------------------------------------------------------------------


def titulo_de(item: dict[str, Any]) -> str:
    titulo = item.get("title")
    if isinstance(titulo, dict):
        titulo = titulo.get("rendered")
    return html.unescape(re.sub(r"<[^>]+>", " ", str(titulo or ""))).strip()


def _medidas(item: dict[str, Any]) -> tuple[int, int]:
    md = item.get("media_details") if isinstance(item.get("media_details"), dict) else {}
    try:
        return int(md.get("width") or 0), int(md.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0


def motivo_descarte(item: dict[str, Any], productos: set[int]) -> str | None:
    """Por qué una imagen de la biblioteca no vale para un correo (o None si
    vale): los cinco filtros, en ese orden."""
    ancho, alto = _medidas(item)
    if ancho <= 0 or alto <= 0:
        return DESCARTE_SIN_MEDIDAS
    if ancho < ANCHO_MIN:
        return DESCARTE_ESTRECHA
    if not PROPORCION_MIN <= ancho / alto <= PROPORCION_MAX:
        return DESCARTE_PROPORCION
    titulo = titulo_de(item)
    if _RUIDO_RE.search(titulo):
        return DESCARTE_TITULO
    if _CODIGO_RE.search(titulo):
        return DESCARTE_CODIGO
    padre = item.get("post")
    if isinstance(padre, int) and padre in productos:
        return DESCARTE_PRODUCTO
    if str(item.get("mime_type") or "").lower() not in MIMES_CORREO:
        return DESCARTE_FORMATO
    return None


def tamanos_de(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Los tamaños de la imagen con sus mismas proporciones (y la original), de
    menor a mayor ancho, sin repetir ancho. Los recortes del tema (cuadrados,
    banners) no entran."""
    ancho, alto = _medidas(item)
    original = str(item.get("source_url") or "").strip()
    salida: dict[int, dict[str, Any]] = {}
    if ancho > 0 and alto > 0 and original:
        salida[ancho] = {"url": original, "ancho": ancho, "alto": alto}
    md = item.get("media_details") if isinstance(item.get("media_details"), dict) else {}
    sizes = md.get("sizes") if isinstance(md.get("sizes"), dict) else {}
    proporcion = ancho / alto if ancho > 0 and alto > 0 else 0
    for datos in sizes.values():
        if not isinstance(datos, dict):
            continue
        try:
            w, h = int(datos.get("width") or 0), int(datos.get("height") or 0)
        except (TypeError, ValueError):
            continue
        url = str(datos.get("source_url") or "").strip()
        if w <= 0 or h <= 0 or not url.startswith(("http://", "https://")) or w in salida:
            continue
        if proporcion and abs((w / h) - proporcion) / proporcion > TOLERANCIA_PROPORCION:
            continue
        salida[w] = {"url": url, "ancho": w, "alto": h}
    return [salida[w] for w in sorted(salida)]


def _fecha(valor: Any) -> datetime | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def fila_de(item: dict[str, Any], *, sitio: Sitio) -> dict[str, Any]:
    ancho, alto = _medidas(item)
    padre = item.get("post")
    return {
        "titulo": titulo_de(item)[:255], "url": str(item.get("source_url") or "")[:500],
        "tamanos_json": json.dumps(tamanos_de(item), ensure_ascii=False),
        "ancho": ancho, "alto": alto, "mime": str(item.get("mime_type") or "")[:40] or None,
        "pagina": str(item.get("link") or "")[:500] or None,
        "padre_id": padre if isinstance(padre, int) and padre > 0 else None,
        "publicado_en": _fecha(item.get("date")),
    }


# --- la pasada ---------------------------------------------------------------------------------


def _bajar(http: httpx.Client, sitio: Sitio) -> tuple[list[dict[str, Any]], set[int], str | None]:
    """`(medios, ids de productos de la web, aviso)`: sin la lista de productos
    de la web se usan los del catálogo y se dice."""
    medios = listar_medios(http, sitio)
    try:
        return medios, ids_de_productos_web(http, sitio), None
    except MediosError as exc:
        return medios, set(), f"productos de la web sin leer ({exc}); se usan los del catálogo"


def _escribir(session: Session, sitio: Sitio, medios: list[dict[str, Any]], productos: set[int],
              *, ahora: datetime) -> dict[str, Any]:
    existentes = {f.media_id: f for f in session.scalars(
        select(ImagenWeb).where(ImagenWeb.sitio == sitio.clave))}
    descartes: dict[str, int] = {}
    vistas: set[int] = set()
    nuevas = actualizadas = 0
    for item in medios:
        mid = item.get("id")
        if not isinstance(mid, int) or mid in vistas:
            continue
        motivo = motivo_descarte(item, productos)
        if motivo is not None:
            descartes[motivo] = descartes.get(motivo, 0) + 1
            continue
        vistas.add(mid)
        datos = fila_de(item, sitio=sitio)
        fila = existentes.get(mid)
        if fila is None:
            session.add(ImagenWeb(sitio=sitio.clave, media_id=mid, visto_en=ahora, **datos))
            nuevas += 1
            continue
        cambio = fila.ido_en is not None
        for campo, valor in datos.items():
            if getattr(fila, campo) != valor:
                setattr(fila, campo, valor)
                cambio = True
        fila.ido_en = None
        fila.visto_en = ahora
        actualizadas += int(cambio)
    idas = 0
    for mid, fila in existentes.items():
        if mid not in vistas and fila.ido_en is None:
            fila.ido_en = ahora
            idas += 1
    session.flush()
    return {"medios": len(medios), "productos": len(vistas), "nuevos": nuevas,
            "actualizados": actualizadas, "idos": idas, "descartes": descartes, "error": None}


def sincronizar(
    session: Session, *, origen: str = SYNC_PROGRAMADA, http: httpx.Client | None = None,
    ahora: datetime | None = None, sitios: list[str] | None = None,
) -> CatalogoSincronizacion:
    """Una pasada por la biblioteca de medios de todas las webs activas (o de
    las dadas). La red en paralelo, la base después. Devuelve la fila del
    histórico, ya en la sesión (el que llama hace commit)."""
    ahora = ahora or datetime.now(UTC)
    pasada = catalogo.abrir_pasada(session, fuente=FUENTE_IMAGENES, origen=origen, ahora=ahora)
    webs = [s for s in registro_sitios.cargar(session).webs
            if sitios is None or s.clave in sitios]
    propio = http is None
    cliente = http or _cliente()
    try:
        def _una(sitio: Sitio) -> Any:
            try:
                return _bajar(cliente, sitio)
            except Exception as exc:  # noqa: BLE001 — una web caída no tira las demás
                return exc

        with ThreadPoolExecutor(max_workers=EN_PARALELO) as pool:
            bajadas = list(pool.map(_una, webs))
    finally:
        if propio:
            cliente.close()
    resumen: dict[str, dict[str, Any]] = {}
    errores: list[str] = []
    for sitio, bajada in zip(webs, bajadas, strict=True):
        if isinstance(bajada, Exception):
            logger.warning("kb.imagenes: %s sin sincronizar, índice anterior intacto: %s",
                           sitio.clave, bajada)
            resumen[sitio.clave] = {"productos": 0, "nuevos": 0, "actualizados": 0, "idos": 0,
                                    "error": str(bajada)[:300]}
            errores.append(f"{sitio.clave}: {str(bajada)[:200]}")
            continue
        medios, productos_web, aviso = bajada
        productos = productos_web | ids_de_productos_catalogo(session, sitio.clave)
        resumen[sitio.clave] = _escribir(session, sitio, medios, productos, ahora=ahora)
        if aviso:
            resumen[sitio.clave]["aviso"] = aviso
    if sitios is None:
        # Lo indexado de webs que ya no están activas deja de ser vigente.
        claves = {s.clave for s in webs}
        idas = 0
        for fila in session.scalars(select(ImagenWeb).where(ImagenWeb.ido_en.is_(None))):
            if fila.sitio not in claves:
                fila.ido_en = ahora
                idas += 1
        if idas:
            resumen["webs_inactivas"] = {"productos": 0, "nuevos": 0, "actualizados": 0,
                                         "idos": idas, "error": None}
    catalogo.cerrar_pasada(pasada, resumen, errores, total=len(webs))
    session.flush()
    logger.info("kb.imagenes: %s — %d indexadas, %d nuevas, %d cambiadas, %d idas%s",
                pasada.estado, pasada.productos, pasada.nuevos, pasada.actualizados, pasada.idos,
                f", {len(errores)} webs con error" if errores else "")
    return pasada


# --- consultas ---------------------------------------------------------------------------------


def tamanos(fila: ImagenWeb) -> list[dict[str, Any]]:
    try:
        datos = json.loads(fila.tamanos_json or "[]")
    except (TypeError, ValueError):
        datos = []
    salida = [d for d in datos if isinstance(d, dict) and d.get("url")] if isinstance(
        datos, list) else []
    return salida or [{"url": fila.url, "ancho": fila.ancho, "alto": fila.alto}]


def url_para(tamanos_lista: list[dict[str, Any]], ancho: int) -> dict[str, Any]:
    """El tamaño más pequeño que cubre `ancho` (en píxeles reales: el doble del
    ancho en el correo, por las pantallas de alta densidad); si ninguno llega,
    el mayor."""
    ordenados = sorted(tamanos_lista, key=lambda t: int(t.get("ancho") or 0))
    for t in ordenados:
        if int(t.get("ancho") or 0) >= ancho:
            return t
    return ordenados[-1] if ordenados else {}


def como_dict(fila: ImagenWeb) -> dict[str, Any]:
    lista = tamanos(fila)
    return {
        "id": fila.id, "sitio": fila.sitio, "media_id": fila.media_id, "titulo": fila.titulo,
        "url": fila.url, "miniatura": url_para(lista, 400).get("url") or fila.url,
        "ancho": fila.ancho, "alto": fila.alto, "mime": fila.mime, "pagina": fila.pagina,
        "publicado_en": fila.publicado_en.isoformat() if fila.publicado_en else None,
        "favorita": bool(fila.favorita), "oculta": bool(fila.oculta),
        "ido_en": fila.ido_en.isoformat() if fila.ido_en else None,
    }


def _normalizar(texto: str | None) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", " ", sin_tildes.decode("ascii").lower()).strip()


def listar(
    session: Session, *, sitio: str | None = None, texto: str | None = None,
    favoritas: bool = False, con_ocultas: bool = True, con_idas: bool = False,
    limite: int = 600,
) -> list[ImagenWeb]:
    """Las imágenes de la pantalla: las favoritas primero, luego las más
    recientes. La búsqueda es por el título (todas sus palabras)."""
    consulta = select(ImagenWeb)
    if not con_idas:
        consulta = consulta.where(ImagenWeb.ido_en.is_(None))
    if not con_ocultas:
        consulta = consulta.where(ImagenWeb.oculta.is_(False))
    if favoritas:
        consulta = consulta.where(ImagenWeb.favorita.is_(True))
    if sitio:
        consulta = consulta.where(ImagenWeb.sitio == sitio)
    consulta = consulta.order_by(ImagenWeb.favorita.desc(), ImagenWeb.publicado_en.desc(),
                                 ImagenWeb.titulo)
    try:
        filas = list(session.scalars(consulta))
    except (OperationalError, ProgrammingError):
        return []
    palabras = _normalizar(texto).split()
    if palabras:
        filas = [f for f in filas if all(p in _normalizar(f.titulo) for p in palabras)]
    return filas[:max(1, min(int(limite), 5000))]


def actualizar(session: Session, imagen_id: str, cambios: dict[str, Any]) -> ImagenWeb:
    fila = session.get(ImagenWeb, imagen_id)
    if fila is None:
        raise LookupError("Esa imagen no está en el índice.")
    for campo in ("favorita", "oculta"):
        if campo in cambios:
            if not isinstance(cambios[campo], bool):
                raise ValueError(f"«{campo}» tiene que ser sí o no.")
            setattr(fila, campo, cambios[campo])
    if fila.favorita and fila.oculta:
        # Las dos a la vez no tienen sentido: manda la última que se marcó.
        if cambios.get("oculta") is True:
            fila.favorita = False
        else:
            fila.oculta = False
    session.flush()
    return fila


def estado(session: Session) -> dict[str, Any]:
    """Recuentos para la pantalla y la última pasada."""
    try:
        por_sitio = session.execute(
            select(ImagenWeb.sitio, func.count()).where(ImagenWeb.ido_en.is_(None))
            .group_by(ImagenWeb.sitio)).all()
        favoritas = session.scalar(select(func.count()).select_from(ImagenWeb).where(
            ImagenWeb.ido_en.is_(None), ImagenWeb.favorita.is_(True))) or 0
        ocultas = session.scalar(select(func.count()).select_from(ImagenWeb).where(
            ImagenWeb.ido_en.is_(None), ImagenWeb.oculta.is_(True))) or 0
    except (OperationalError, ProgrammingError):
        por_sitio, favoritas, ocultas = [], 0, 0
    return {
        "total": sum(int(n) for _, n in por_sitio),
        "por_sitio": {s: int(n) for s, n in por_sitio},
        "favoritas": int(favoritas), "ocultas": int(ocultas),
        "ultima_sincronizacion": catalogo.sincronizacion_como_dict(
            catalogo.ultima_sincronizacion(session, FUENTE_IMAGENES)),
    }


# --- lo que ve el correo -----------------------------------------------------------------------

#: Las palabras de una instrucción que no dicen nada de la imagen que se busca.
_SIN_PESO: frozenset[str] = frozenset({
    "pon", "ponle", "ponme", "pongo", "pone", "quita", "cambia", "usa", "mete", "anade", "añade",
    "prepara", "preparale", "mandale", "manda", "envia", "enviale", "mandar", "haz", "hazle",
    "correo", "email", "mail", "hero", "cabecera", "banner", "imagen", "imagenes", "foto",
    "fotos", "muestras", "muestra", "galeria", "trabajos", "con", "las", "los", "del", "una",
    "uno", "unos", "unas", "que", "para", "por", "sus", "mas", "este", "esta", "ese", "esa",
    "the", "and", "with", "for", "from", "her", "his", "him", "them", "their", "image",
    "images", "photo", "photos", "picture", "pictures", "samples", "sample", "gallery",
    "header", "die", "der", "das", "und", "mit", "les", "des", "avec", "pour", "het", "een",
    "met", "voor", "gama", "range", "maquina", "maquinas", "machine", "machines", "producto",
    "productos", "products", "le", "la", "el", "de", "en", "y", "a", "al", "lo", "se", "te",
})


def palabras_de(*textos: str | None) -> list[str]:
    """Las palabras de búsqueda de unos textos (la instrucción, los nombres de
    las máquinas, el interés): normalizadas, sin las que no dicen nada, en
    orden y sin repetir. «UV» y los códigos con cifras sí cuentan."""
    salida: list[str] = []
    for texto in textos:
        for p in _normalizar(texto).split():
            if p in _SIN_PESO or p in salida:
                continue
            if len(p) >= 3 or p == "uv" or (len(p) >= 2 and any(c.isdigit() for c in p)):
                salida.append(p)
    return salida


def _puntos(titulo: str, palabras: Iterable[str]) -> int:
    tokens = _normalizar(titulo).split()
    puntos = 0
    for p in palabras:
        if p in tokens:
            puntos += 2
        elif len(p) >= 4 and any(t.startswith(p) or (len(t) >= 4 and p.startswith(t))
                                 for t in tokens):
            puntos += 1
    return puntos


def para_correo(
    session: Session, *, palabras: Iterable[str], sitio: str | None = None,
    maximo: int = PARA_CORREO_MAX,
) -> list[dict[str, Any]]:
    """Las imágenes que se le ofrecen a la IA para un hero o unas muestras: las
    que casan con las palabras (instrucción, máquinas, interés), las favoritas
    delante a igualdad, y la misma foto subida a varias webs una sola vez (la
    de la web del hilo, si está). Sin palabras que casen, solo las favoritas.
    Nunca las ocultas ni las idas."""
    lista_palabras = list(palabras)
    try:
        filas = list(session.scalars(select(ImagenWeb).where(
            ImagenWeb.ido_en.is_(None), ImagenWeb.oculta.is_(False))))
    except (OperationalError, ProgrammingError):
        return []
    puntuadas = []
    for f in filas:
        puntos = _puntos(f.titulo, lista_palabras) if lista_palabras else 0
        if puntos <= 0 and not f.favorita:
            continue
        puntuadas.append((puntos, f))
    if any(p > 0 for p, _ in puntuadas):
        puntuadas = [(p, f) for p, f in puntuadas if p > 0]
    puntuadas.sort(key=lambda pf: (
        -pf[0], not pf[1].favorita, pf[1].sitio != sitio,
        -(pf[1].publicado_en.timestamp() if pf[1].publicado_en else 0), pf[1].titulo))
    salida: list[dict[str, Any]] = []
    vistas: set[str] = set()
    for _, f in puntuadas:
        clave = _normalizar(f.titulo) or f.id
        if clave in vistas:
            continue
        vistas.add(clave)
        salida.append({"id": f.id, "titulo": f.titulo, "sitio": f.sitio, "ancho": f.ancho,
                       "alto": f.alto, "favorita": bool(f.favorita)})
        if len(salida) >= maximo:
            break
    return salida


def por_ids(session: Session, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Las imágenes vigentes por su id, con sus tamaños (el generador elige el
    que toca a su ancho). Lo que ya no está en la web no sale."""
    lista = [str(i) for i in ids if i]
    if not lista:
        return {}
    try:
        filas = session.scalars(select(ImagenWeb).where(
            ImagenWeb.id.in_(lista), ImagenWeb.ido_en.is_(None)))
    except (OperationalError, ProgrammingError):
        return {}
    return {f.id: {"id": f.id, "titulo": f.titulo, "ancho": f.ancho, "alto": f.alto,
                   "url": f.url, "tamanos": tamanos(f), "sitio": f.sitio} for f in filas}
