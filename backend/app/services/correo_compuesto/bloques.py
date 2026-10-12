"""Los bloques de un correo compuesto y su estructura.

La estructura es un dict JSON:

    {"version": 1, "idioma": "es", "marca": "mbo_dtf" | None, "pie": None | "…",
     "bloques": [{"id": "b1a2b3c4", "tipo": "texto", …}, …]}

Cada bloque lleva su `id` (estable: las correcciones y la vista previa lo
usan) y los campos de su tipo. Los textos y los enlaces de un bloque pueden
ser un texto o un dict por idioma (`{"es": …, "en": …}`): el generador coge
el del idioma del correo. Los productos van por su clave del catálogo
(`{"fuente", "origen_id"}`): nombre, imagen, precio y enlace salen siempre de
la fila viva de `kb_catalogo`, nunca de la estructura. Los vídeos, igual, por
QUÉ vídeo son (`{"plataforma", "id"}`, ver `videos.py`): su URL, miniatura,
título y duración los resuelve BoHub al generar.

`normalizar` deja cualquier estructura (de la IA, de la vista previa, de un
borrador guardado) en esta forma: tipos conocidos, campos limpios, textos sin
marcado, el texto rico reducido a párrafos, negrita, cursiva, listas y
enlaces, URL absolutas y filas de 2 o 3 fichas.
"""

from __future__ import annotations

import html as html_mod
import re
import uuid
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

#: Los bloques de este PR. El hero y la galería de muestras llegan con el
#: índice de la biblioteca de medios de las webs.
TIPOS: tuple[str, ...] = (
    TIPO_TEXTO, TIPO_PRODUCTO, TIPO_PRODUCTOS_FILA, TIPO_MARCA, TIPO_VIDEO, TIPO_CTA,
    TIPO_DESTACADO, TIPO_SEPARADOR,
)

ESTILOS_DESTACADO: tuple[str, ...] = ("aviso", "promo", "condicion")
ESTILOS_SEPARADOR: tuple[str, ...] = ("linea", "corta", "puntos", "aire")

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

def clave_producto(valor: Any) -> dict[str, str] | None:
    """La referencia de un producto del catálogo: `{"fuente", "origen_id"}`."""
    if not isinstance(valor, dict):
        return None
    fuente = str(valor.get("fuente") or "").strip()[:12]
    origen_id = str(valor.get("origen_id") or "").strip()[:80]
    if not fuente or not origen_id:
        return None
    return {"fuente": fuente, "origen_id": origen_id}


def clave_de(producto: dict[str, str]) -> str:
    """La clave plana de un producto (para los dicts de datos)."""
    return f"{producto['fuente']}|{producto['origen_id']}"


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
        trozos = [productos[i:i + FILA_MAX] for i in range(0, len(productos), FILA_MAX)]
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
