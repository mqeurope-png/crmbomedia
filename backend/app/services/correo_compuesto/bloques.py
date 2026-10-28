"""Los bloques de un correo compuesto y su estructura.

La estructura es un dict JSON:

    {"version": 1, "idioma": "es", "marca": "mbo_dtf" | None, "pie": None | "…",
     "bloques": [{"id": "b1a2b3c4", "tipo": "texto", …}, …]}

Cada bloque lleva su `id` (estable: las correcciones y la vista previa lo
usan) y los campos de su tipo. Los textos y los enlaces de un bloque pueden
ser un texto o un dict por idioma (`{"es": …, "en": …}`): el generador coge
el del idioma del correo. Los productos van por su clave del catálogo
(`{"fuente", "origen_id"}`): nombre, imagen, precio y enlace salen siempre de
la fila viva de `kb_catalogo`, nunca de la estructura. Un producto que el
catálogo no tiene puede ir con una ficha montada desde su página del índice
de las webs (`{"fuente": "web", "origen_id": "<sitio>:<wp_id>"}`,
`webs.fichas`), que puede llevar dos cosas en la estructura: `precio` (el
que indicó quien envía, verificado contra sus instrucciones) e `imagen` (otra
de la galería de esa página; si deja de estar en ella, va la principal). Los vídeos, igual, por
QUÉ vídeo son (`{"plataforma", "id"}`, ver `videos.py`): su URL, miniatura,
título y duración los resuelve BoHub al generar. Y las imágenes del hero y de
las muestras, por su id del índice de imágenes de las webs (`{"id"}`,
`kb_imagenes`): su URL y su tamaño salen de la fila viva.

`normalizar` deja cualquier estructura (de la IA, de la vista previa, de un
borrador guardado) en esta forma: tipos conocidos, campos limpios, textos sin
marcado, el texto rico reducido a párrafos, negrita, cursiva, listas y
enlaces, URL absolutas y filas de 2 o 3 fichas.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import uuid
from collections.abc import Callable, Iterable
from typing import Any

import bleach

from app.services.correo_compuesto import videos as videos_mod

VERSION = 1

TIPO_TEXTO = "texto"
TIPO_PRODUCTO = "producto"
TIPO_PRODUCTOS_FILA = "productos_fila"
TIPO_MARCA = "marca"
TIPO_VIDEO = "video"
TIPO_CTA = "cta"
TIPO_DESTACADO = "destacado"
TIPO_SEPARADOR = "separador"
TIPO_HERO = "hero"
TIPO_MUESTRAS = "muestras"

TIPOS: tuple[str, ...] = (
    TIPO_TEXTO, TIPO_PRODUCTO, TIPO_PRODUCTOS_FILA, TIPO_MARCA, TIPO_VIDEO, TIPO_CTA,
    TIPO_DESTACADO, TIPO_SEPARADOR, TIPO_HERO, TIPO_MUESTRAS,
)

ESTILOS_DESTACADO: tuple[str, ...] = ("aviso", "promo", "condicion")
ESTILOS_SEPARADOR: tuple[str, ...] = ("linea", "corta", "puntos", "aire")
#: Dónde va la imagen del hero: encima del texto, al lado (en el móvil, encima)
#: o de fondo, con el texto encima (Outlook enseña el color de fondo).
DISPOSICIONES_HERO: tuple[str, ...] = ("arriba", "lateral", "fondo")
PUNTOS_MAX = 5
MUESTRAS_MIN = 2
MUESTRAS_MAX = 6

FILA_MIN = 2
FILA_MAX = 3
BOTONES_MAX = 3
MAX_BLOQUES = 30
MAX_TITULO = 160
MAX_TEXTO_CORTO = 600
MAX_TEXTO_RICO = 6000
MAX_BOTON = 60

IDIOMA_RAIZ = "es"
IDIOMA_RESPALDO = "en"

#: Las etiquetas fijas de los bloques, por idioma (con respaldo en inglés): las
#: mismas que llevan hoy los correos de Bomedia.
TEXTOS: dict[str, dict[str, str]] = {
    "mas_info": {"es": "Más info", "en": "More info", "de": "Mehr Infos",
                 "fr": "Plus d'infos", "nl": "Meer info"},
    "info": {"es": "Info", "en": "Info", "de": "Info", "fr": "Info", "nl": "Info"},
    "detalles": {"es": "Detalles", "en": "Details", "de": "Details", "fr": "Détails",
                 "nl": "Details"},
    "area": {"es": "Área", "en": "Area", "de": "Fläche", "fr": "Surface", "nl": "Oppervlak"},
    "alto_max": {"es": "Alt. máx.", "en": "Max. height", "de": "Max. Höhe", "fr": "Haut. max.",
                 "nl": "Max. hoogte"},
    "alto": {"es": "Alt", "en": "Height", "de": "Höhe", "fr": "Haut.", "nl": "Hoogte"},
    "iva_sin": {"es": "+ IVA", "en": "+ VAT", "de": "+ MwSt", "fr": "+ TVA", "nl": "+ BTW"},
    "iva_con": {"es": "IVA incl.", "en": "incl. VAT", "de": "inkl. MwSt", "fr": "TTC",
                "nl": "incl. btw"},
    #: Una ficha de la web sin precio en la web (y sin uno que haya dicho
    #: quien envía): en el sitio del precio y sin el IVA.
    "precio_consultar": {"es": "Precio a consultar", "en": "Price on request",
                         "de": "Preis auf Anfrage", "fr": "Prix sur demande",
                         "nl": "Prijs op aanvraag"},
    #: El precio sin importe (el origen dice «Consultar»): nunca «Consultar»,
    #: sino que se prepara a medida, en el sitio del precio y sin el IVA.
    "precio_a_medida": {"es": "Precio a medida", "en": "Custom price", "de": "Preis nach Maß",
                        "fr": "Prix sur mesure", "nl": "Prijs op maat"},
    "precio_otra_web": {
        "es": "precio publicado en otra web del grupo",
        "en": "price as published on another of our sites",
        "de": "Preis wie auf einer anderen unserer Websites veröffentlicht",
        "fr": "prix publié sur un autre de nos sites",
        "nl": "prijs zoals gepubliceerd op een andere site van ons",
    },
    "ver_video": {"es": "Ver vídeo", "en": "Watch video", "de": "Video ansehen",
                  "fr": "Voir la vidéo", "nl": "Video bekijken"},
    "video": {"es": "Vídeo", "en": "Video", "de": "Video", "fr": "Vidéo", "nl": "Video"},
}


def texto_fijo(clave: str, idioma: str | None) -> str:
    """Un texto fijo de los bloques en `idioma` (o en inglés, o en castellano)."""
    variantes = TEXTOS.get(clave) or {}
    codigo = (idioma or "").strip().lower()[:2]
    return variantes.get(codigo) or variantes.get(IDIOMA_RESPALDO) or variantes.get(
        IDIOMA_RAIZ) or ""


def en_idioma(valor: Any, idioma: str | None) -> str:
    """Un texto o enlace de bloque en el idioma del correo: si es un dict por
    idioma, el de `idioma`, si no el castellano, si no el primero."""
    if isinstance(valor, dict):
        codigo = (idioma or "").strip().lower()[:2]
        for clave in (codigo, IDIOMA_RAIZ, IDIOMA_RESPALDO):
            if valor.get(clave):
                return str(valor[clave])
        for v in valor.values():
            if v:
                return str(v)
        return ""
    return "" if valor is None else str(valor)


def nuevo_id() -> str:
    return "b" + uuid.uuid4().hex[:8]


# --- textos y enlaces ------------------------------------------------------------------------

_ETIQUETA_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^<>]*>")
_ESPACIOS_RE = re.compile(r"[ \t\r\f\v]+")


def tiene_marcado(valor: str | None) -> bool:
    """Si un texto trae etiquetas HTML («<p>», «<table …>», «</div>»)."""
    return bool(_ETIQUETA_RE.search(valor or ""))


def texto_plano(valor: Any, maximo: int = MAX_TEXTO_CORTO) -> str:
    """Un texto corto de bloque (título, botón, destacado): sin etiquetas, sin
    espacios de sobra, con los saltos de línea que tenga y recortado."""
    s = _ETIQUETA_RE.sub(" ", str(valor or ""))
    s = html_mod.unescape(s).replace("\xa0", " ")
    lineas = [_ESPACIOS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(ln for ln in lineas if ln)
    return s[:maximo].strip()


def _texto_por_idioma(valor: Any, maximo: int) -> Any:
    """Un campo de texto que puede ser un texto o un dict por idioma."""
    if isinstance(valor, dict):
        limpio = {str(k).lower()[:2]: texto_plano(v, maximo) for k, v in valor.items()
                  if texto_plano(v, maximo)}
        return limpio or ""
    return texto_plano(valor, maximo)


def url_valida(url: Any, *, mailto: bool = False) -> str | None:
    """La URL si es absoluta (http/https, o mailto si se admite); None si no."""
    s = str(url or "").strip()
    if not s or any(c in s for c in ' \n\r\t"<>'):
        return None
    minus = s.lower()
    if minus.startswith(("http://", "https://")) and len(s) > 10:
        return s[:1000]
    if mailto and minus.startswith("mailto:") and "@" in s:
        return s[:500]
    return None


def _url_por_idioma(valor: Any, *, mailto: bool = False) -> Any:
    if isinstance(valor, dict):
        limpio = {str(k).lower()[:2]: u for k, v in valor.items()
                  if (u := url_valida(v, mailto=mailto))}
        return limpio or None
    return url_valida(valor, mailto=mailto)


# --- el texto rico de un bloque «texto» -----------------------------------------------------

ETIQUETAS_TEXTO: tuple[str, ...] = ("p", "br", "strong", "em", "u", "ul", "ol", "li", "a")
_BLOQUE_A_P_RE = re.compile(r"<(/?)(div|section|article|blockquote|pre|h[1-6])\b[^>]*>",
                            re.IGNORECASE)
_B_I_RE = re.compile(r"<(/?)(b|i)(\s[^>]*)?>", re.IGNORECASE)
_P_VACIO_RE = re.compile(r"<p>(?:\s|&nbsp;|<br\s*/?>)*</p>", re.IGNORECASE)
_EMPIEZA_BLOQUE_RE = re.compile(r"^\s*<(p|ul|ol)\b", re.IGNORECASE)


def limpiar_texto_rico(valor: Any, maximo: int = MAX_TEXTO_RICO) -> str:
    """El HTML de un bloque «texto» reducido a párrafos, saltos, negrita,
    cursiva, subrayado, listas y enlaces (http, https, mailto). Lo que venga de
    un editor (`<div>` de cada línea, `<b>`, estilos) se convierte o se quita
    conservando el texto. El texto suelto va dentro de un párrafo."""
    s = str(valor or "")[: maximo * 2]
    if not s.strip():
        return ""
    s = _BLOQUE_A_P_RE.sub(lambda m: f"<{m.group(1)}p>", s)
    s = _B_I_RE.sub(lambda m: f"<{m.group(1)}{'strong' if m.group(2).lower() == 'b' else 'em'}>",
                    s)
    s = bleach.clean(s, tags=list(ETIQUETAS_TEXTO), attributes={"a": ["href"]},
                     protocols=["http", "https", "mailto"], strip=True, strip_comments=True)
    # Un enlace sin destino válido se queda en su texto.
    s = re.sub(r"<a>(.*?)</a>", r"\1", s, flags=re.DOTALL)
    s = _P_VACIO_RE.sub("", s).strip()
    if s and not _EMPIEZA_BLOQUE_RE.match(s):
        s = f"<p>{s}</p>"
    if len(s) > maximo:
        # Recortado sin dejar una etiqueta a medias: lo que quede de una
        # etiqueta o una entidad al final se quita y bleach cierra lo abierto.
        corte = re.sub(r"<[^>]*$|&[#\w]*$", "", s[:maximo])
        s = bleach.clean(corte, tags=list(ETIQUETAS_TEXTO), attributes={"a": ["href"]},
                         protocols=["http", "https", "mailto"], strip=True, strip_comments=True)
    return s


_LISTA_RE = re.compile(r"^\s*(?:[-*•]\s+|(\d+)[.)]\s+)(.*)$")
_NEGRITA_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_CURSIVA_RE = re.compile(
    r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"
    r"|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")
_ENLACE_MD_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _en_linea(texto: str, resolver_enlace: Any = None) -> str:
    """Una línea de markdown sencillo a HTML: escapa, enlaces, negrita, cursiva."""
    enlaces: list[str] = []

    def _guardar(m: re.Match[str]) -> str:
        destino = m.group(2)
        url = resolver_enlace(destino) if resolver_enlace else url_valida(destino, mailto=True)
        etiqueta = html_mod.escape(m.group(1))
        if not url:
            enlaces.append(etiqueta)
        else:
            enlaces.append(f'<a href="{html_mod.escape(url, quote=True)}">{etiqueta}</a>')
        return f"\x00{len(enlaces) - 1}\x00"

    s = _ENLACE_MD_RE.sub(_guardar, texto)
    s = html_mod.escape(s, quote=False)
    s = _NEGRITA_RE.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", s)
    s = _CURSIVA_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: enlaces[int(m.group(1))], s)


def markdown_a_html(texto: str, resolver_enlace: Any = None) -> str:
    """El markdown sencillo que escribe la IA (párrafos separados por una línea
    en blanco, «- » para listas, «1. » para listas numeradas, **negrita**,
    *cursiva* y [texto](enlace)) al HTML de un bloque «texto». La IA nunca
    escribe HTML: esto lo genera el código. `resolver_enlace` convierte la
    referencia de un enlace en su URL (o None si no vale)."""
    partes: list[str] = []
    lista: list[str] = []
    tipo_lista = ""
    parrafo: list[str] = []

    def _cerrar_parrafo() -> None:
        if parrafo:
            partes.append("<p>" + "<br>".join(parrafo) + "</p>")
            parrafo.clear()

    def _cerrar_lista() -> None:
        nonlocal tipo_lista
        if lista:
            partes.append(f"<{tipo_lista}>" + "".join(f"<li>{li}</li>" for li in lista)
                          + f"</{tipo_lista}>")
            lista.clear()
        tipo_lista = ""

    for linea in str(texto or "").replace("\r\n", "\n").split("\n"):
        if not linea.strip():
            _cerrar_parrafo()
            _cerrar_lista()
            continue
        m = _LISTA_RE.match(linea)
        if m:
            _cerrar_parrafo()
            tipo = "ol" if m.group(1) else "ul"
            if tipo_lista and tipo != tipo_lista:
                _cerrar_lista()
            tipo_lista = tipo
            lista.append(_en_linea(m.group(2).strip(), resolver_enlace))
            continue
        _cerrar_lista()
        parrafo.append(_en_linea(linea.strip(), resolver_enlace))
    _cerrar_parrafo()
    _cerrar_lista()
    return limpiar_texto_rico("".join(partes))


# --- productos ------------------------------------------------------------------------------

#: La fuente de las fichas montadas desde el índice de las webs.
FUENTE_WEB = "web"
#: Largo del precio que indica quien envía para una ficha de la web.
MAX_PRECIO_USUARIO = 60


def clave_producto(valor: Any) -> dict[str, str] | None:
    """La referencia de un producto del catálogo: `{"fuente", "origen_id"}`.
    Una ficha de la web (`fuente: web`) conserva además `precio` (el que
    indicó quien envía) e `imagen` (una de su galería), si los trae."""
    if not isinstance(valor, dict):
        return None
    fuente = str(valor.get("fuente") or "").strip()[:12]
    origen_id = str(valor.get("origen_id") or "").strip()[:80]
    if not fuente or not origen_id:
        return None
    clave = {"fuente": fuente, "origen_id": origen_id}
    if fuente == FUENTE_WEB:
        precio = " ".join(str(valor.get("precio") or "").split())[:MAX_PRECIO_USUARIO]
        if precio and not re.search(r"[<>]", precio):
            clave["precio"] = precio
        imagen = url_valida(valor.get("imagen"))
        if imagen:
            clave["imagen"] = imagen
    return clave


def clave_de(producto: dict[str, str]) -> str:
    """La clave plana de un producto (para los dicts de datos)."""
    return f"{producto['fuente']}|{producto['origen_id']}"


def clave_imagen(valor: Any) -> dict[str, str] | None:
    """La referencia de una imagen del índice: `{"id"}` (o el id tal cual)."""
    vid = valor.get("id") if isinstance(valor, dict) else valor
    texto = str(vid or "").strip()
    return {"id": texto} if re.fullmatch(r"[A-Za-z0-9-]{8,40}", texto) else None


def imagenes_de(estructura: dict[str, Any]) -> list[str]:
    """Los ids de las imágenes que nombra la estructura (hero y muestras), sin
    repetir, en orden."""
    salida: list[str] = []
    for b in estructura.get("bloques") or []:
        if b.get("tipo") == TIPO_HERO:
            refs = [b.get("imagen")]
        elif b.get("tipo") == TIPO_MUESTRAS:
            refs = list(b.get("imagenes") or [])
        else:
            refs = []
        for r in refs:
            img = clave_imagen(r)
            if img and img["id"] not in salida:
                salida.append(img["id"])
    return salida


def videos_de(estructura: dict[str, Any]) -> list[dict[str, str]]:
    """Los vídeos que nombra la estructura, sin repetir, en orden."""
    vistos: set[str] = set()
    salida: list[dict[str, str]] = []
    for b in estructura.get("bloques") or []:
        video = videos_mod.identidad(b.get("video")) if b.get("tipo") == TIPO_VIDEO else None
        if video and videos_mod.clave(video) not in vistos:
            vistos.add(videos_mod.clave(video))
            salida.append(video)
    return salida


def productos_de(estructura: dict[str, Any]) -> list[dict[str, str]]:
    """Los productos que nombra la estructura, sin repetir, en orden."""
    vistos: set[str] = set()
    salida: list[dict[str, str]] = []
    for b in estructura.get("bloques") or []:
        if b.get("tipo") == TIPO_PRODUCTO:
            refs = [b.get("producto")]
        elif b.get("tipo") == TIPO_PRODUCTOS_FILA:
            refs = list(b.get("productos") or [])
        else:
            refs = []
        for r in refs:
            p = clave_producto(r)
            if p and clave_de(p) not in vistos:
                vistos.add(clave_de(p))
                salida.append(p)
    return salida


def marcas_de(estructura: dict[str, Any]) -> list[str]:
    """Las marcas que nombra la estructura (la principal y las de las tiras)."""
    salida: list[str] = []
    for m in [estructura.get("marca")] + [
            b.get("marca") for b in estructura.get("bloques") or [] if b.get("tipo") == TIPO_MARCA]:
        if m and str(m) not in salida:
            salida.append(str(m))
    return salida


# --- normalizar ------------------------------------------------------------------------------

def _boton(valor: Any) -> dict[str, Any] | None:
    if not isinstance(valor, dict):
        return None
    texto = _texto_por_idioma(valor.get("texto"), MAX_BOTON)
    url = _url_por_idioma(valor.get("url"), mailto=True)
    if not texto or not url:
        return None
    return {"texto": texto, "url": url}


def _normalizar_bloque(b: Any, avisos: list[str]) -> list[dict[str, Any]]:
    """Un bloque limpio, o varios (una fila de más de tres fichas se parte),
    o ninguno (le falta lo esencial)."""
    if not isinstance(b, dict):
        return []
    tipo = str(b.get("tipo") or "").strip().lower()
    if tipo not in TIPOS:
        avisos.append(f"bloque desconocido «{tipo or '—'}»: fuera")
        return []
    bid = str(b.get("id") or "").strip()[:16]
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,16}", bid):
        bid = nuevo_id()
    if tipo == TIPO_TEXTO:
        html = limpiar_texto_rico(b.get("html"))
        return [{"id": bid, "tipo": tipo, "html": html}] if html else []
    if tipo == TIPO_PRODUCTO:
        p = clave_producto(b.get("producto"))
        if p is None:
            return []
        salida = {"id": bid, "tipo": tipo, "producto": p}
        boton = _texto_por_idioma(b.get("boton"), MAX_BOTON)
        if boton:
            salida["boton"] = boton
        return [salida]
    if tipo == TIPO_PRODUCTOS_FILA:
        productos: list[dict[str, str]] = []
        for r in b.get("productos") or []:
            p = clave_producto(r)
            if p and all(clave_de(p) != clave_de(q) for q in productos):
                productos.append(p)
        boton = _texto_por_idioma(b.get("boton"), MAX_BOTON)
        if not productos:
            return []
        trozos = trocear_parejas(productos)
        salida_fila: list[dict[str, Any]] = []
        for i, trozo in enumerate(trozos):
            nuevo: dict[str, Any]
            if len(trozo) < FILA_MIN:
                nuevo = {"id": bid if i == 0 else nuevo_id(), "tipo": TIPO_PRODUCTO,
                         "producto": trozo[0]}
            else:
                nuevo = {"id": bid if i == 0 else nuevo_id(), "tipo": tipo, "productos": trozo}
            if boton:
                nuevo["boton"] = boton
            salida_fila.append(nuevo)
        return salida_fila
    if tipo == TIPO_MARCA:
        marca = str(b.get("marca") or "").strip()[:80]
        if not marca:
            return []
        salida = {"id": bid, "tipo": tipo, "marca": marca}
        texto = _texto_por_idioma(b.get("texto"), MAX_TITULO)
        if texto:
            salida["texto"] = texto
        enlace = _url_por_idioma(b.get("enlace"))
        if enlace:
            salida["enlace"] = enlace
        return [salida]
    if tipo == TIPO_VIDEO:
        # Qué vídeo es (de un bloque guardado o de la tabla de vídeos), o una
        # URL pegada de YouTube o Vimeo; sus datos se resuelven al generar.
        video = videos_mod.identidad(b.get("video") or b.get("url"))
        if video is None:
            avisos.append("un vídeo que no es de YouTube ni de Vimeo: fuera")
            return []
        salida = {"id": bid, "tipo": tipo, "video": video}
        for campo, maximo in (("titulo", MAX_TITULO), ("texto", MAX_TEXTO_CORTO),
                              ("boton", MAX_BOTON)):
            valor = _texto_por_idioma(b.get(campo), maximo)
            if valor:
                salida[campo] = valor
        return [salida]
    if tipo == TIPO_CTA:
        botones = [bt for bt in (_boton(v) for v in (b.get("botones") or [])) if bt]
        if not botones:
            return []
        return [{"id": bid, "tipo": tipo, "botones": botones[:BOTONES_MAX]}]
    if tipo == TIPO_HERO:
        imagen = clave_imagen(b.get("imagen"))
        if imagen is None:
            avisos.append("un hero sin imagen del índice: fuera")
            return []
        disposicion = str(b.get("disposicion") or "arriba").strip().lower()
        salida = {"id": bid, "tipo": tipo, "imagen": imagen,
                  "disposicion": disposicion if disposicion in DISPOSICIONES_HERO else "arriba"}
        for campo, maximo in (("titulo", MAX_TITULO), ("subtitulo", MAX_TEXTO_CORTO)):
            valor = _texto_por_idioma(b.get(campo), maximo)
            if valor:
                salida[campo] = valor
        crudos = b.get("puntos") if isinstance(b.get("puntos"), list) else []
        puntos = [p for p in (_texto_por_idioma(v, MAX_TITULO) for v in crudos) if p]
        if puntos:
            salida["puntos"] = puntos[:PUNTOS_MAX]
        botones = [bt for bt in (_boton(v) for v in (b.get("botones") or [])) if bt]
        if botones:
            salida["botones"] = botones[:BOTONES_MAX]
        return [salida]
    if tipo == TIPO_MUESTRAS:
        imagenes: list[dict[str, str]] = []
        pies: list[Any] = []
        crudos_pies = b.get("pies") if isinstance(b.get("pies"), list) else []
        for i, r in enumerate(b.get("imagenes") or []):
            img = clave_imagen(r)
            if img and all(img["id"] != x["id"] for x in imagenes):
                imagenes.append(img)
                pies.append(_texto_por_idioma(crudos_pies[i], MAX_TITULO)
                            if i < len(crudos_pies) else "")
        if len(imagenes) < MUESTRAS_MIN:
            avisos.append(f"una galería de muestras necesita al menos {MUESTRAS_MIN} imágenes: "
                          "fuera")
            return []
        if len(imagenes) > MUESTRAS_MAX:
            avisos.append(f"una galería de muestras lleva como mucho {MUESTRAS_MAX} imágenes: "
                          "se quedan las primeras")
        salida = {"id": bid, "tipo": tipo, "imagenes": imagenes[:MUESTRAS_MAX]}
        titulo = _texto_por_idioma(b.get("titulo"), MAX_TITULO)
        if titulo:
            salida["titulo"] = titulo
        if any(pies[:MUESTRAS_MAX]):
            salida["pies"] = pies[:MUESTRAS_MAX]
        return [salida]
    if tipo == TIPO_DESTACADO:
        texto = _texto_por_idioma(b.get("texto"), MAX_TEXTO_CORTO)
        titulo = _texto_por_idioma(b.get("titulo"), MAX_TITULO)
        if not texto and not titulo:
            return []
        estilo = str(b.get("estilo") or "aviso").strip().lower()
        salida = {"id": bid, "tipo": tipo,
                  "estilo": estilo if estilo in ESTILOS_DESTACADO else "aviso"}
        if titulo:
            salida["titulo"] = titulo
        if texto:
            salida["texto"] = texto
        return [salida]
    estilo = str(b.get("estilo") or "linea").strip().lower()
    return [{"id": bid, "tipo": TIPO_SEPARADOR,
             "estilo": estilo if estilo in ESTILOS_SEPARADOR else "linea"}]


def normalizar(estructura: Any, avisos: list[str] | None = None) -> dict[str, Any]:
    """La estructura limpia (ver el módulo). `avisos` recoge lo que se quitó y
    por qué. Los ids repetidos se renuevan; nunca más de `MAX_BLOQUES`."""
    avisos = avisos if avisos is not None else []
    origen = estructura if isinstance(estructura, dict) else {}
    idioma = str(origen.get("idioma") or IDIOMA_RAIZ).strip().lower()[:2] or IDIOMA_RAIZ
    marca = str(origen.get("marca") or "").strip()[:80] or None
    bloques: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for b in origen.get("bloques") or []:
        for limpio in _normalizar_bloque(b, avisos):
            if limpio["id"] in vistos:
                limpio["id"] = nuevo_id()
            vistos.add(limpio["id"])
            bloques.append(limpio)
    if len(bloques) > MAX_BLOQUES:
        avisos.append(f"más de {MAX_BLOQUES} bloques: se quedan los primeros")
        bloques = bloques[:MAX_BLOQUES]
    salida: dict[str, Any] = {"version": VERSION, "idioma": idioma, "marca": marca,
                              "bloques": bloques}
    pie = origen.get("pie")
    if pie is not None and texto_plano(pie, MAX_TEXTO_CORTO * 2):
        salida["pie"] = texto_plano(pie, MAX_TEXTO_CORTO * 2)
    return salida


def vacia(estructura: dict[str, Any] | None) -> bool:
    return not (estructura or {}).get("bloques")


def primero_es_texto(estructura: dict[str, Any] | None) -> bool:
    """Si el correo empieza por un bloque de texto (el saludo)."""
    bloques = (estructura or {}).get("bloques") or []
    return bool(bloques) and bloques[0].get("tipo") == TIPO_TEXTO


def abrir_con_saludo(estructura: dict[str, Any]) -> bool:
    """En una respuesta (o en un correo a alguien con quien ya hay
    conversación) el correo empieza por el saludo y la entrada: si delante del
    primer bloque de texto va otro (un hero, una tira de marca, unas fichas),
    ese texto pasa al principio. El hero de apertura es de un correo frío.
    True si ha movido algo."""
    bloques = estructura.get("bloques") or []
    primero = next((i for i, b in enumerate(bloques) if b.get("tipo") == TIPO_TEXTO), None)
    if not primero:
        return False
    bloques.insert(0, bloques.pop(primero))
    return True


def filas_parejas(n: int) -> list[int]:
    """Cuántas fichas por fila para `n` productos: de dos o tres, lo más
    parejas posible (4 → 2+2, 5 → 3+2, 7 → 3+2+2), para que no quede una
    ficha suelta al final; uno, solo."""
    if n <= FILA_MAX:
        return [n] if n > 0 else []
    filas = -(-n // FILA_MAX)
    base, resto = divmod(n, filas)
    return [base + 1] * resto + [base] * (filas - resto)


def trocear_parejas(elementos: list[Any]) -> list[list[Any]]:
    """Los elementos en filas de `filas_parejas`, en orden."""
    trozos: list[list[Any]] = []
    k = 0
    for n in filas_parejas(len(elementos)):
        trozos.append(elementos[k:k + n])
        k += n
    return trozos


def bloques_tocados(estructura: dict[str, Any], base: dict[str, Any] | None) -> set[str]:
    """Los ids de los bloques que ha puesto o cambiado este turno: todos en un
    correo nuevo; en una corrección, los nuevos y los que no son iguales a los
    de la base. Los arreglos de después no tocan lo que la persona dejó así en
    la vista previa."""
    actuales = {b["id"]: b for b in estructura.get("bloques") or []}
    if base is None:
        return set(actuales)
    previos = {b.get("id"): b for b in base.get("bloques") or []}
    return {i for i, b in actuales.items() if previos.get(i) != b}


def juntar_fichas_sueltas(
    estructura: dict[str, Any], marca_de: Callable[[dict[str, str]], str | None], *,
    tocados: set[str] | None = None,
) -> tuple[int, int]:
    """Dos o más fichas sueltas («producto») seguidas de la misma marca van
    en fila (12/10/2026: «añade más modelos» dio tres fichas sueltas, cada una
    en su tabla de 320 px): se juntan en «productos_fila» de dos o tres, lo
    más parejas posible; la misma ficha dos veces seguidas se queda en una.
    Solo los tramos con algún bloque de este turno (`tocados`); una ficha de
    marca desconocida no se junta. Devuelve (fichas puestas en fila, fichas
    repetidas quitadas)."""
    bloques = estructura.get("bloques") or []
    salida: list[dict[str, Any]] = []
    juntadas = repetidas = 0
    i = 0
    while i < len(bloques):
        b = bloques[i]
        marca = marca_de(b["producto"]) if b.get("tipo") == TIPO_PRODUCTO else None
        j = i + 1
        while (marca and j < len(bloques) and bloques[j].get("tipo") == TIPO_PRODUCTO
               and marca_de(bloques[j]["producto"]) == marca):
            j += 1
        tramo = bloques[i:j]
        i = j
        if len(tramo) < 2 or (tocados is not None and not any(
                x["id"] in tocados for x in tramo)):
            salida.extend(tramo)
            continue
        unicos: list[dict[str, Any]] = []
        for x in tramo:
            if all(clave_de(x["producto"]) != clave_de(u["producto"]) for u in unicos):
                unicos.append(x)
        repetidas += len(tramo) - len(unicos)
        for trozo in trocear_parejas(unicos):
            if len(trozo) == 1:
                salida.append(trozo[0])
                continue
            nuevo: dict[str, Any] = {"id": trozo[0]["id"], "tipo": TIPO_PRODUCTOS_FILA,
                                     "productos": [x["producto"] for x in trozo]}
            # Una fila lleva una sola etiqueta de botón: la que tuvieran todas.
            botones = {json.dumps(x.get("boton"), sort_keys=True) for x in trozo}
            if len(botones) == 1 and trozo[0].get("boton"):
                nuevo["boton"] = trozo[0]["boton"]
            salida.append(nuevo)
            juntadas += len(trozo)
    estructura["bloques"] = salida
    return juntadas, repetidas


_SEPARA_RE = r"(?:\s|&nbsp;|<br\s*/?>)+"
_PARRAFO_RE = re.compile(r"<p>(.*?)</p>", re.IGNORECASE | re.DOTALL)


def _para_comparar(texto: str) -> str:
    """Un texto como se comparan las frases: sin etiquetas, minúsculas, el
    apóstrofo tipográfico como el recto y los espacios de uno en uno."""
    s = html_mod.unescape(re.sub(r"<[^>]+>", " ", texto or "")).replace("\u2019", "'")
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().lower()


def quitar_frases(estructura: dict[str, Any], frases: Iterable[str], *,
                  tocados: set[str] | None = None) -> int:
    """Quita de los textos del correo estas frases (los avisos de IVA, VIES y
    «no es una factura» que ya imprime el pie: el 12/10/2026 salían dos
    veces). Un párrafo que solo trae esas frases se va entero, aunque lleven
    negritas; dentro de un párrafo con más texto se quitan tal cual. Un texto
    que se queda vacío, fuera. Solo en los textos de este turno (`tocados`).
    Devuelve cuántas quitó."""
    lista = [f.strip() for f in frases if f and f.strip()]
    comparar = [_para_comparar(f) for f in lista]
    patrones = [re.compile(_SEPARA_RE.join(
        re.escape(html_mod.escape(w, quote=False)).replace("'", "['\u2019]")
        for w in f.split()), re.IGNORECASE) for f in lista]
    quitadas = 0
    bloques: list[dict[str, Any]] = []
    for b in estructura.get("bloques") or []:
        if b.get("tipo") != TIPO_TEXTO or (tocados is not None and b["id"] not in tocados):
            bloques.append(b)
            continue

        def _parrafo(m: re.Match[str]) -> str:
            nonlocal quitadas
            resto = _para_comparar(m.group(1))
            n = 0
            for c in comparar:
                if c and c in resto:
                    resto = resto.replace(c, " ")
                    n += 1
            if n and not resto.strip(" .,;:-–—"):
                quitadas += n
                return ""
            return m.group(0)

        html = _PARRAFO_RE.sub(_parrafo, b.get("html") or "")
        for patron in patrones:
            html, n = patron.subn("", html)
            quitadas += n
        html = _P_VACIO_RE.sub("", html)
        html = re.sub(r"(<p>)(?:\s|<br\s*/?>)+|(?:\s|<br\s*/?>)+(</p>)",
                      lambda m: m.group(1) or m.group(2), html)
        html = re.sub(r"[ \t]{2,}", " ", html)
        if html.strip():
            bloques.append({**b, "html": html})
    estructura["bloques"] = bloques
    return quitadas


def _clave_url(url: Any) -> str:
    if isinstance(url, dict):
        return "|".join(f"{k}={str(v).strip().rstrip('/')}" for k, v in sorted(url.items()))
    return str(url or "").strip().rstrip("/")


def quitar_botones_repetidos(estructura: dict[str, Any]) -> list[str]:
    """Un mismo enlace va en un solo botón del correo (12/10/2026: «Ver la gama
    DTF» salía en el hero y otra vez al final): se queda el primero y los de
    después se quitan; un bloque de botones que se queda sin ninguno, fuera.
    Devuelve los textos quitados."""
    vistos: set[str] = set()
    quitados: list[str] = []
    bloques: list[dict[str, Any]] = []
    for b in estructura.get("bloques") or []:
        if b.get("tipo") in (TIPO_HERO, TIPO_CTA) and b.get("botones"):
            botones = []
            for bt in b["botones"]:
                clave = _clave_url(bt.get("url"))
                if clave and clave in vistos:
                    quitados.append(str(en_idioma(bt.get("texto"), None) or ""))
                    continue
                vistos.add(clave)
                botones.append(bt)
            if not botones and b["tipo"] == TIPO_CTA:
                continue
            if botones:
                b["botones"] = botones
            else:
                b.pop("botones", None)
        bloques.append(b)
    estructura["bloques"] = bloques
    return quitados
