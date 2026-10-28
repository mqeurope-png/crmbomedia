"""La IA elige la estructura de un correo compuesto; nunca escribe su HTML.

- `modo_pedido`: si la instrucción pide un correo compuesto («mándale la gama
  DTF», «prepárale un correo con las máquinas»), uno normal («dile que el
  pedido sale el martes») o no lo dice (entonces elige la IA, por defecto
  normal). El interruptor de la franja manda sobre todo esto.
- `Referencias`: lo que la IA puede nombrar, siempre por referencia: los
  productos del CATÁLOGO (`P1`, `C1`), los productos de nuestras webs que el
  catálogo no tiene (`W1`: la pieza de «Nuestras webs» de tipo producto, con
  su ficha montada desde el índice; si es la misma página que una del
  catálogo, va la del catálogo), los ENLACES (`L1`), los vídeos (`V1`:
  los de YouTube o Vimeo que salen en el hilo o en la instrucción, y los de la
  tabla de vídeos que van con las máquinas del correo), las imágenes de las
  webs para el hero y las muestras (`I1`, del índice de imágenes) y la web de
  cada marca (`M:mbo_dtf`). Una referencia que no existe no entra: así no hay
  productos, precios, enlaces, vídeos ni imágenes inventados.
- `estructura_desde_ia` convierte la lista de bloques que devuelve la IA (sus
  textos en markdown sencillo) en una estructura (`bloques.normalizar`). Si un
  texto trae etiquetas HTML, se quitan, se cuenta y se registra.
- `aplicar_operaciones`: una corrección sobre un compuesto («quita el vídeo»,
  «deja solo dos máquinas») son operaciones sobre la estructura actual; lo que
  no tocan se queda igual, byte a byte.
"""

from __future__ import annotations

import copy
import html as html_mod
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import videos as videos_mod
from app.services.correo_compuesto.generador import clave_marca

logger = logging.getLogger(__name__)

MODO_NORMAL = "normal"
MODO_COMPUESTO = "compuesto"
FORMATO_AUTO = "auto"
FORMATOS = (FORMATO_AUTO, MODO_NORMAL, MODO_COMPUESTO)

_VERBO_PRESENTAR = (
    r"(m[aá]nd|envi|env[ií]|prep[aá]r|haz|hazle|p[aá]s[ae]|ofr[eé]c|prop[oó]n|ense[ñn]|muestr|"
    r"pres[eé]nt|send|prepar|show|offer|present|schick|sende|zeig|envoie|montre|propose|stuur|"
    r"toon)\w*"
)
# Productos, no «opciones» ni «condiciones»: «le mando las opciones de pago»
# es un correo de texto.
_OBJETO_GAMA = (
    r"(gama|cat[aá]logo|fichas?|m[aá]quinas|modelos|equipos|impresoras|productos|"
    r"range|lineup|line-up|catalog(ue)?|machines|models|products|printers|"
    r"maschinen|modelle|produkte|drucker|gamme|machines|mod[eè]les|produits|imprimantes|"
    r"machines|modellen|producten|printers)"
)
_PIDE_GAMA_RE = re.compile(
    rf"\b{_VERBO_PRESENTAR}\b[^.?!\n]{{0,60}}\b{_OBJETO_GAMA}\b", re.IGNORECASE)
_PIDE_FICHAS_RE = re.compile(
    r"\b(maquetad[oa]|con im[aá]genes|con fotos|con fichas|fichas de producto|newsletter|"
    r"with (images|pictures|photos)|product cards?|mit bildern|avec (images|photos))\b",
    re.IGNORECASE,
)
_PIDE_NORMAL_RE = re.compile(
    r"^\s*(dile|d[ií]gale|cont[eé]stale|contesta|resp[oó]ndele|responde|av[ií]sale|avisa|"
    r"conf[ií]rmale|confirma|expl[ií]cale|explica|preg[uú]ntale|pregunta|recu[eé]rdale|"
    r"recuerda|tell|reply|answer|confirm|ask|remind|let (him|her|them) know)\b",
    re.IGNORECASE,
)
_PIDE_TEXTO_RE = re.compile(
    r"\b(solo texto|en texto|a texto|como texto|texto normal|texto plano|sin fichas|"
    r"sin im[aá]genes|sin maquetar|correo normal|plain text|text only|no images)\b",
    re.IGNORECASE,
)


def modo_pedido(instruccion: str | None) -> str | None:
    """`compuesto` si la instrucción pide presentar productos o un correo con
    fichas; `normal` si pide decir algo concreto sin productos; None si no se
    sabe (entonces elige la IA, y por defecto es normal)."""
    texto = (instruccion or "").strip()
    if not texto:
        return None
    if _PIDE_TEXTO_RE.search(texto):
        return MODO_NORMAL
    if _PIDE_FICHAS_RE.search(texto):
        return MODO_COMPUESTO
    # «Dile que…», «contéstale…»: un mensaje concreto, en texto, aunque hable
    # de máquinas («dile que le mando la gama la semana que viene»).
    if _PIDE_NORMAL_RE.search(texto):
        return MODO_NORMAL
    if _PIDE_GAMA_RE.search(texto):
        return MODO_COMPUESTO
    return None


def modo_del_turno(*, formato: str | None, instruccion: str | None,
                   modo_actual: str | None) -> str | None:
    """El formato de un turno: el del interruptor si lo fuerza; si no, en una
    conversación ya compuesta sigue compuesta (salvo que se pida texto), en
    una normal sigue normal (salvo que se pidan fichas); en el primer turno,
    el que pida la instrucción, o None (elige la IA)."""
    if formato in (MODO_NORMAL, MODO_COMPUESTO):
        return formato
    pedido = modo_pedido(instruccion)
    if modo_actual == MODO_COMPUESTO:
        return MODO_NORMAL if pedido == MODO_NORMAL and _PIDE_TEXTO_RE.search(
            instruccion or "") else MODO_COMPUESTO
    if modo_actual == MODO_NORMAL:
        return MODO_COMPUESTO if pedido == MODO_COMPUESTO else MODO_NORMAL
    return pedido


_VIDEO_EN_TEXTO_RE = re.compile(
    r"https?://(?:(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s<>\"']*v=|shorts/|embed/|live/)|"
    r"youtu\.be/)[A-Za-z0-9_-]{11}|(?:www\.|player\.)?vimeo\.com/(?:video/|channels/[\w-]+/|"
    r"groups/[\w-]+/videos/)?\d{5,12})[^\s<>\"')\]]*")


def videos_de(*textos: str | None) -> list[dict[str, str]]:
    """Los vídeos de YouTube o Vimeo cuyas URL aparecen en los textos, sin
    repetir: qué vídeo es (`plataforma`, `id`) y su URL."""
    salida: list[dict[str, str]] = []
    vistos: set[str] = set()
    for texto in textos:
        for url in _VIDEO_EN_TEXTO_RE.findall(texto or ""):
            video = videos_mod.de_url(url.rstrip(".,;:"))
            if video and videos_mod.clave(video) not in vistos:
                vistos.add(videos_mod.clave(video))
                salida.append({**video, "url": videos_mod.url_de(video)})
    return salida


def _video_ref(valor: Any) -> dict[str, str] | None:
    """Un vídeo de las referencias: `{"plataforma", "id", "url"}` (y lo que
    traiga su origen, como el título), de un dict o de una URL."""
    video = videos_mod.identidad(valor)
    if video is None:
        return None
    extra = {k: str(v) for k, v in valor.items()
             if k in ("titulo", "idioma") and v} if isinstance(valor, dict) else {}
    return {**video, "url": videos_mod.url_de(video), **extra}


_MILES_PUNTO = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"          # 14.900 / 14.900,50
_MILES_COMA = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"           # 14,900 / 1,299.50
_MILES_ESPACIO = r"\d{1,3}(?:\s\d{3})+(?:[.,]\d{1,2})?"     # 14 900
#: El número entero: ni «900» de «14,900» ni «14,90» de «14,900».
_NUMERO = (rf"(?<![\d.,])(?:{_MILES_PUNTO}|{_MILES_COMA}|{_MILES_ESPACIO}|\d+(?:[.,]\d{{1,2}})?)"
           r"(?![\d.,]*\d)")
#: Un importe: un número con la moneda al lado («14.900 €», «€ 14900», «14900
#: euros», «€14,900»). Un número suelto no: «MBO 6090» es un modelo, no un precio.
_IMPORTE_USUARIO_RE = re.compile(
    rf"(?:(?:€|\$|£|eur\b|euros?\b)\s*({_NUMERO}))|(?:({_NUMERO})\s*(?:€|\$|£|eur\b|euros?\b))",
    re.IGNORECASE)


def _normalizado(crudo: str) -> str | None:
    if re.fullmatch(_MILES_PUNTO, crudo):
        crudo = crudo.replace(".", "").replace(",", ".")
    elif re.fullmatch(_MILES_COMA, crudo):
        crudo = crudo.replace(",", "")
    else:
        crudo = re.sub(r"\s", "", crudo).replace(",", ".")
    try:
        valor = float(crudo)
    except ValueError:
        return None
    return str(int(valor)) if valor == int(valor) else f"{valor:.2f}"


def importes_de(texto: str | None) -> set[str]:
    """Los importes de un texto, normalizados («14.900,00 €» y «€14,900» →
    «14900»): solo los que llevan la moneda al lado."""
    return {n for n in (_normalizado(m.group(1) or m.group(2))
                        for m in _IMPORTE_USUARIO_RE.finditer(texto or "")) if n}


_CORTE_RE = re.compile(r"[;\n]|,\s|\.\s")


def _ivas_de_importes(texto: str) -> dict[str, set[str]]:
    """El IVA que dice quien envía junto a cada importe («14.900 € + IVA»):
    lo de su lado de la frase. Un importe sin IVA al lado toma el que dice la
    instrucción sin pegarlo a ningún importe, si es uno solo («a 14.900 € y
    la normal a 12.000 €, ambas + IVA»; «IVA aparte, …»); el IVA pegado a
    otro importe no cuenta para este."""
    from app.services.correo_compuesto.generador import (  # noqa: PLC0415
        iva_del_texto,
        ivas_del_texto,
    )

    salida: dict[str, set[str]] = {}
    importes = list(_IMPORTE_USUARIO_RE.finditer(texto))
    sueltos: list[str] = []
    resto = list(texto)                 # lo que no va pegado a ningún importe
    for i, m in enumerate(importes):
        # Lo de su lado, sin pasar de una coma, un punto y coma o un salto de
        # línea, ni llegar al importe de al lado.
        desde = max(m.start() - 12, importes[i - 1].end() if i else 0)
        antes = _CORTE_RE.split(texto[desde:m.start()])[-1]
        desde = m.start() - len(antes)
        hasta = importes[i + 1].start() if i + 1 < len(importes) else m.end() + 40
        hasta = m.end() + len(_CORTE_RE.split(texto[m.end():hasta])[0])
        resto[desde:hasta] = [" "] * (hasta - desde)
        numero = _normalizado(m.group(1) or m.group(2))
        iva = iva_del_texto(texto[desde:hasta])
        if numero and iva:
            salida.setdefault(numero, set()).add(iva)
        elif numero:
            sueltos.append(numero)
    generales = ivas_del_texto("".join(resto))
    if len(generales) == 1:
        for numero in sueltos:
            salida.setdefault(numero, set()).update(generales)
    return salida


def _cifras_de(texto: str) -> set[str]:
    """Las cifras de un texto, sin separadores («16.500» → «16500»)."""
    return {re.sub(r"\D", "", c) for c in re.findall(r"\d[\d.,]*\d|\d", texto)}


def _pagina(url: str | None) -> str:
    """Host y ruta de una página, sin `www.`, idioma ni barra final: la misma
    ficha en el catálogo y en el índice."""
    from app.services.webs import fichas as fichas_web  # noqa: PLC0415

    return fichas_web.pagina(url)


@dataclass
class Referencias:
    """Lo que la IA puede nombrar en un correo compuesto, por referencia."""

    productos: dict[str, dict[str, str]] = field(default_factory=dict)   # P1 → clave
    enlaces_producto: dict[str, str] = field(default_factory=dict)       # P1 → URL
    enlaces: dict[str, str] = field(default_factory=dict)                # L1 → URL
    videos: dict[str, dict[str, str]] = field(default_factory=dict)      # V1 → vídeo
    marcas: dict[str, dict[str, Any]] = field(default_factory=dict)      # clave → marca
    imagenes: dict[str, dict[str, Any]] = field(default_factory=dict)    # I1 → imagen
    #: Fichas de la web que son la misma página que una del catálogo: W2 → P1.
    equivalentes: dict[str, str] = field(default_factory=dict)
    #: Los importes que ha escrito quien envía en sus instrucciones: los únicos
    #: que valen como precio de una ficha de la web (nunca uno deducido). Con
    #: el IVA que dijo junto a cada uno y todas las cifras que escribió.
    importes_usuario: set[str] = field(default_factory=set)
    ivas_usuario: dict[str, set[str]] = field(default_factory=dict)
    cifras_usuario: set[str] = field(default_factory=set)

    @classmethod
    def desde(cls, *, maquinas: list[dict[str, Any]], consumibles: list[dict[str, Any]],
              enlaces: list[dict[str, Any]], videos: list[Any],
              marcas: dict[str, dict[str, Any]],
              imagenes: list[dict[str, Any]] | None = None) -> Referencias:
        refs = cls(marcas=dict(marcas))
        for prefijo, lista in (("P", maquinas), ("C", consumibles)):
            for i, p in enumerate(lista, 1):
                if p.get("origen_id") and p.get("fuente"):
                    ref = f"{prefijo}{i}"
                    refs.productos[ref] = {"fuente": str(p["fuente"]),
                                           "origen_id": str(p["origen_id"])}
                    if bq.url_valida(p.get("enlace")):
                        refs.enlaces_producto[ref] = str(p["enlace"])
        for i, e in enumerate(enlaces, 1):
            if bq.url_valida(e.get("url")):
                refs.enlaces[f"L{i}"] = str(e["url"])
        for valor in videos:
            refs.anadir_video(valor)
        for valor in imagenes or []:
            refs.anadir_imagen(valor)
        return refs

    def como_dict(self) -> dict[str, Any]:
        return {"productos": self.productos, "enlaces_producto": self.enlaces_producto,
                "enlaces": self.enlaces, "videos": self.videos, "imagenes": self.imagenes}

    @classmethod
    def de_dict(cls, datos: dict[str, Any] | None, *, marcas: dict[str, dict[str, Any]],
                videos: list[Any] | None = None,
                imagenes: list[dict[str, Any]] | None = None) -> Referencias:
        datos = datos if isinstance(datos, dict) else {}
        refs = cls(productos=dict(datos.get("productos") or {}),
                   enlaces_producto=dict(datos.get("enlaces_producto") or {}),
                   enlaces=dict(datos.get("enlaces") or {}), marcas=dict(marcas))
        guardados = datos.get("videos") if isinstance(datos.get("videos"), dict) else {}
        for ref, valor in guardados.items():
            video = _video_ref(valor)
            if video is not None:
                refs.videos[str(ref)] = video
        for valor in videos or []:
            refs.anadir_video(valor)
        guardadas = datos.get("imagenes") if isinstance(datos.get("imagenes"), dict) else {}
        for ref, valor in guardadas.items():
            if isinstance(valor, dict) and bq.clave_imagen(valor):
                refs.imagenes[str(ref)] = dict(valor)
        for valor in imagenes or []:
            refs.anadir_imagen(valor)
        return refs

    def anadir_imagen(self, valor: Any) -> str | None:
        """Añade una imagen del índice (`{"id", "titulo", …}`) con la siguiente
        `I#`, si no estaba; devuelve su referencia."""
        clave_img = bq.clave_imagen(valor)
        if clave_img is None:
            return None
        ya = self.ref_de_imagen(clave_img["id"])
        if ya:
            return ya
        ref = f"I{len(self.imagenes) + 1}"
        extra = {k: valor[k] for k in ("titulo", "sitio", "ancho", "alto")
                 if isinstance(valor, dict) and valor.get(k) is not None}
        self.imagenes[ref] = {**clave_img, **extra}
        return ref

    def imagen(self, ref: Any) -> dict[str, str] | None:
        """La imagen de una referencia `I#` (o del id de una de ellas)."""
        s = str(ref or "").strip()
        if s in self.imagenes:
            return {"id": str(self.imagenes[s]["id"])}
        ya = self.ref_de_imagen(s)
        return {"id": s} if ya else None

    def ref_de_imagen(self, imagen_id: str) -> str | None:
        return next((ref for ref, v in self.imagenes.items() if v.get("id") == imagen_id), None)

    def anadir_video(self, valor: Any) -> str | None:
        """Añade un vídeo (dict o URL) con la siguiente `V#`, si no estaba;
        devuelve su referencia."""
        video = _video_ref(valor)
        if video is None:
            return None
        ya = self.ref_de_video(video)
        if ya:
            return ya
        ref = f"V{len(self.videos) + 1}"
        self.videos[ref] = video
        return ref

    # -- de referencia a valor, y vuelta ---------------------------------------------------

    def url_marca(self, marca: str, idioma: str) -> str | None:
        m = self.marcas.get(clave_marca(marca))
        return bq.url_valida(bq.en_idioma((m or {}).get("urls") or {}, idioma)) if m else None

    def permitidas(self, idioma: str) -> set[str]:
        urls = set(self.enlaces.values()) | set(self.enlaces_producto.values()) | {
            v["url"] for v in self.videos.values()}
        for clave in self.marcas:
            url = self.url_marca(clave, idioma)
            if url:
                urls.add(url)
        return urls

    def url(self, ref: Any, idioma: str) -> str | None:
        """La URL de una referencia (L#, P#, V#, M:marca) o de una URL tal cual
        si es de las permitidas; None si no vale."""
        s = str(ref or "").strip()
        if not s:
            return None
        if s in self.enlaces:
            return self.enlaces[s]
        if s in self.enlaces_producto:
            return self.enlaces_producto[s]
        if s in self.videos:
            return self.videos[s]["url"]
        if s.upper().startswith("M:"):
            return self.url_marca(s[2:], idioma)
        if s.lower().startswith("mailto:") and "@" in s:
            return s
        if s in self.permitidas(idioma):
            return s
        # La URL de un vídeo de la lista escrita de otra forma (youtu.be…).
        video = self.video(s)
        return video["url"] if video else None

    def video(self, ref: Any) -> dict[str, str] | None:
        """El vídeo de una referencia `V#`, o de una URL que es uno de ellos."""
        s = str(ref or "").strip()
        if s in self.videos:
            return self.videos[s]
        identidad = videos_mod.de_url(s)
        if identidad is None:
            return None
        ya = self.ref_de_video(identidad)
        return self.videos[ya] if ya else None

    def ref_de_video(self, video: dict[str, str]) -> str | None:
        clave = videos_mod.clave(video)
        return next((ref for ref, v in self.videos.items() if videos_mod.clave(v) == clave),
                    None)

    def ref_de_url(self, url: str) -> str:
        for mapa in (self.enlaces, self.enlaces_producto):
            for ref, u in mapa.items():
                if u == url:
                    return ref
        video = self.video(url)
        return (self.ref_de_video(video) or url) if video else url

    def ref_de_producto(self, clave: dict[str, str]) -> str | None:
        for ref, c in self.productos.items():
            if (c.get("fuente"), c.get("origen_id")) == (clave.get("fuente"),
                                                         clave.get("origen_id")):
                return ref
        return None

    def anadir_producto_web(self, ref: str, clave: dict[str, str], url: str | None) -> None:
        """Una pieza de «Nuestras webs» de tipo producto, como ficha (`W#`). Si
        su página es la de un producto del catálogo, manda el del catálogo."""
        self.productos[ref] = {"fuente": clave["fuente"], "origen_id": clave["origen_id"]}
        if bq.url_valida(url):
            self.enlaces_producto[ref] = str(url)
            propia = _pagina(url)
            for otra, enlace in self.enlaces_producto.items():
                if otra != ref and otra[:1] in ("P", "C") and propia and _pagina(enlace) == propia:
                    self.equivalentes[ref] = otra
                    break

    def anadir_equivalente_catalogo(self, ref: str, producto: dict[str, Any]) -> None:
        """La ficha de la web `ref` es un producto del catálogo que no está
        entre los `P#` del correo: se le da uno, y `ref` saca esa ficha."""
        clave = {"fuente": str(producto["fuente"]), "origen_id": str(producto["origen_id"])}
        propia = self.ref_de_producto(clave)
        if propia is None:
            numeros = [int(r[1:]) for r in self.productos if r[:1] == "P" and r[1:].isdigit()]
            propia = f"P{max(numeros, default=0) + 1}"
            self.productos[propia] = clave
            if bq.url_valida(producto.get("enlace")):
                self.enlaces_producto[propia] = str(producto["enlace"])
        self.equivalentes[ref] = propia

    def anotar_instrucciones(self, texto: str) -> None:
        """Lo que ha escrito quien envía (todas sus instrucciones): de ahí, y
        solo de ahí, puede salir el precio de una ficha de la web."""
        self.importes_usuario = importes_de(texto)
        self.ivas_usuario = _ivas_de_importes(texto or "")
        self.cifras_usuario = _cifras_de(texto or "")

    def precio_permitido(self, precio: str) -> bool:
        """¿El precio que la IA pone a una ficha de la web es uno que ha
        escrito quien envía? Lleva algún importe; todos están en sus
        instrucciones; no añade cifras que no escribió («antes 16.500») ni un
        IVA que no dijo junto a ese importe."""
        from app.services.correo_compuesto.generador import iva_del_texto  # noqa: PLC0415

        importes = importes_de(precio)
        if not importes or not importes <= self.importes_usuario:
            return False
        if not _cifras_de(_IMPORTE_USUARIO_RE.sub(" ", precio)) <= self.cifras_usuario:
            return False
        iva = iva_del_texto(precio)
        return iva is None or all(iva in self.ivas_usuario.get(n, set()) for n in importes)


@dataclass
class Resultado:
    estructura: dict[str, Any]
    avisos: list[str] = field(default_factory=list)
    #: Cuántos textos traían HTML (se quitó y se registró).
    html_descartado: int = 0


class _Conversor:
    """De los bloques de la IA (referencias y markdown) a la estructura."""

    def __init__(self, refs: Referencias, idioma: str) -> None:
        self.refs = refs
        self.idioma = idioma
        self.avisos: list[str] = []
        self.html_descartado = 0
        # Al editar un bloque guardado: las URL y los productos que ya tenía
        # (pueden no estar entre las referencias de este turno).
        self.conocidas: set[str] = set()
        self.productos_conocidos: dict[str, dict[str, str]] = {}
        self.videos_conocidos: dict[str, dict[str, str]] = {}
        self.imagenes_conocidas: set[str] = set()
        # Las imágenes que ya lleva el correo (la IA las ve como `?<id>`): valen
        # al reemplazar o añadir un bloque aunque no estén entre las del turno.
        self.imagenes_del_correo: set[str] = set()
        # Lo mismo con las fichas que ya lleva (`?<origen_id>`): una de la web
        # puesta en una corrección anterior no tiene `W#` en este turno.
        self.productos_del_correo: dict[str, dict[str, str]] = {}

    def _url(self, destino: Any) -> str | None:
        url = self.refs.url(destino, self.idioma)
        if url is None and str(destino or "").strip() in self.conocidas:
            url = str(destino).strip()
        return url

    def _sin_html(self, valor: Any) -> str:
        texto = "" if valor is None else str(valor)
        if bq.tiene_marcado(texto):
            self.html_descartado += 1
            texto = re.sub(r"<\s*br\s*/?\s*>", "\n", texto, flags=re.IGNORECASE)
            texto = re.sub(r"</\s*(p|li|div|h[1-6]|tr)\s*>", "\n", texto, flags=re.IGNORECASE)
            texto = bq.texto_plano(texto, bq.MAX_TEXTO_RICO)
        return texto

    def _video(self, valor: Any) -> dict[str, str] | None:
        """Qué vídeo pone un bloque: una `V#` (o la URL de uno de ellos) o el
        que el bloque ya tenía; nunca uno inventado."""
        video = self.refs.video(valor)
        if video is None:
            identidad = videos_mod.identidad(valor)
            if identidad is not None:
                video = self.videos_conocidos.get(videos_mod.clave(identidad))
        return {"plataforma": video["plataforma"], "id": video["id"]} if video else None

    def _imagen(self, valor: Any) -> dict[str, str] | None:
        """Qué imagen pone un bloque: una `I#` de las IMÁGENES o una que el
        bloque ya tenía (`?<id>`); nunca una inventada."""
        imagen = self.refs.imagen(valor)
        if imagen is None:
            s = str(valor.get("id") if isinstance(valor, dict) else valor or "").strip()
            s = s[1:] if s.startswith("?") else s
            if s in self.imagenes_conocidas or s in self.imagenes_del_correo:
                imagen = {"id": s}
        return imagen

    def _botones_de(self, valor: Any) -> list[dict[str, Any]]:
        botones = []
        for bt in valor if isinstance(valor, list) else []:
            if not isinstance(bt, dict):
                continue
            url = self._url(bt.get("enlace") or bt.get("url"))
            if not url:
                self.avisos.append(f"Un botón con un enlace que no está en la lista "
                                   f"(«{bt.get('enlace') or bt.get('url')}»): no sale.")
                continue
            botones.append({"texto": self._sin_html(bt.get("texto")), "url": url})
        return botones

    def _producto(self, ref: Any) -> dict[str, str] | None:
        extra = ref if isinstance(ref, dict) else {}
        if extra:
            ref = extra.get("ref") or extra.get("producto") or extra.get("id")
        r = str(ref or "").strip()
        if r in self.refs.equivalentes:
            otra = self.refs.equivalentes[r]
            self.avisos.append(f"La ficha de la web «{r}» es el mismo producto que «{otra}» del "
                               "catálogo: va la del catálogo, con su precio de venta.")
            r = otra
        clave = (self.refs.productos.get(r) or self.productos_conocidos.get(r)
                 or self.productos_del_correo.get(r))
        if clave is None:
            self.avisos.append(f"La IA nombró un producto que no está en el catálogo («{ref}»): "
                               "no sale.")
            return None
        clave = dict(clave)
        precio = " ".join(str(extra.get("precio") or "").split())
        if clave.get("fuente") == bq.FUENTE_WEB:
            if precio:
                if len(precio) <= bq.MAX_PRECIO_USUARIO and self.refs.precio_permitido(precio):
                    clave["precio"] = precio
                else:
                    clave.pop("precio", None)
                    motivo = ("es demasiado largo para una ficha" if len(precio)
                              > bq.MAX_PRECIO_USUARIO else "no sale de tus instrucciones")
                    self.avisos.append(
                        f"La IA puso a la ficha «{r}» un precio («{precio}») que {motivo}: no se "
                        "usa (va el de la web, o «precio a consultar»).")
            if extra.get("imagen"):
                clave["imagen"] = str(extra["imagen"])
        elif precio:
            self.avisos.append(f"La IA quiso cambiar el precio de «{r}», del catálogo: el precio "
                               "de una ficha del catálogo sale del catálogo y no se cambia.")
        return bq.clave_producto(clave)

    def _enlace_texto(self, destino: str) -> str | None:
        url = self._url(destino)
        if url is None:
            logger.warning("correo compuesto: enlace fuera de la lista descartado: %s", destino)
        return url

    def campo(self, bloque: dict[str, Any], campo: str, valor: Any) -> dict[str, Any] | None:
        """Lo que cambia en un bloque guardado al editar UN campo (en el
        formato de la IA: referencias y markdown), o None si no vale. Solo se
        convierte ese campo: los productos, botones y enlaces que el bloque ya
        tenía no vuelven a pasar por las referencias del turno."""
        self.conocidas = _urls_de(bloque)
        self.productos_conocidos = {
            f"?{p['origen_id']}": p for p in [*(bloque.get("productos") or []),
                                              *([bloque["producto"]] if bloque.get("producto")
                                                else [])]}
        propio = videos_mod.identidad(bloque.get("video")) if bloque.get("video") else None
        self.videos_conocidos = {videos_mod.clave(propio): propio} if propio else {}
        self.imagenes_conocidas = set(bq.imagenes_de({"bloques": [bloque]}))
        try:
            cambio = self._campo(bloque.get("tipo"), campo, valor)
        finally:
            self.conocidas, self.productos_conocidos, self.videos_conocidos = set(), {}, {}
            self.imagenes_conocidas = set()
        if cambio and "imagenes" in cambio and "pies" not in cambio:
            # Cada pie sigue a su imagen: la que se queda conserva el suyo y la
            # nueva entra sin pie.
            antes = {img["id"]: pie for img, pie in zip(
                [bq.clave_imagen(r) for r in bloque.get("imagenes") or []],
                bloque.get("pies") or [], strict=False) if img}
            cambio["pies"] = [antes.get(img["id"], "") for img in cambio["imagenes"]]
        return cambio

    def _campo(self, tipo: Any, campo: str, valor: Any) -> dict[str, Any] | None:
        if tipo == bq.TIPO_TEXTO and campo in ("texto", "html"):
            return {"html": bq.markdown_a_html(self._sin_html(valor), self._enlace_texto)}
        if campo in ("titulo", "texto", "boton", "subtitulo") and tipo != bq.TIPO_TEXTO:
            return {campo: self._sin_html(valor)}
        if campo == "estilo" and tipo in (bq.TIPO_DESTACADO, bq.TIPO_SEPARADOR):
            return {"estilo": str(valor or "")}
        if campo == "disposicion" and tipo == bq.TIPO_HERO:
            return {"disposicion": str(valor or "")}
        if campo == "puntos" and tipo == bq.TIPO_HERO:
            lista = valor if isinstance(valor, list) else [valor]
            return {campo: [self._sin_html(v) for v in lista if v]}
        if campo == "pies" and tipo == bq.TIPO_MUESTRAS:
            # Un pie por imagen, en su sitio: uno vacío se queda vacío y no
            # corre los demás a la imagen de al lado.
            lista = valor if isinstance(valor, list) else [valor]
            return {"pies": [self._sin_html(v) if v else "" for v in lista]}
        if campo == "botones" and tipo in (bq.TIPO_CTA, bq.TIPO_HERO):
            botones = self._botones_de(valor)
            return {"botones": botones} if botones else None
        if campo == "imagen" and tipo == bq.TIPO_HERO:
            imagen = self._imagen(valor)
            return {"imagen": imagen} if imagen else None
        if campo == "imagenes" and tipo == bq.TIPO_MUESTRAS:
            imagenes = [i for i in (self._imagen(v) for v in (valor if isinstance(valor, list)
                                                               else [])) if i]
            return {"imagenes": imagenes} if imagenes else None
        if campo in ("productos", "producto", "video", "url", "botones", "marca"):
            parcial = self.bloque({"tipo": tipo, campo: valor})
            if parcial is None:
                return None
            clave = {"url": "video"}.get(campo, campo)
            return {clave: parcial[clave]} if clave in parcial else None
        return None

    def bloque(self, b: Any) -> dict[str, Any] | None:
        if not isinstance(b, dict):
            return None
        tipo = str(b.get("tipo") or "").strip().lower()
        salida: dict[str, Any] = {"tipo": tipo}
        if b.get("id"):
            salida["id"] = str(b["id"])
        if tipo == bq.TIPO_TEXTO:
            salida["html"] = bq.markdown_a_html(self._sin_html(b.get("texto") or b.get("html")),
                                                self._enlace_texto)
        elif tipo == bq.TIPO_PRODUCTO:
            salida["producto"] = self._producto(b.get("producto"))
            if b.get("boton"):
                salida["boton"] = self._sin_html(b["boton"])
        elif tipo == bq.TIPO_PRODUCTOS_FILA:
            salida["productos"] = [c for c in (self._producto(r) for r in b.get("productos") or [])
                                   if c]
            if b.get("boton"):
                salida["boton"] = self._sin_html(b["boton"])
        elif tipo == bq.TIPO_MARCA:
            marca = str(b.get("marca") or "").strip()
            if clave_marca(marca) not in self.refs.marcas:
                self.avisos.append(f"La IA puso una tira de una marca que no está en el catálogo "
                                   f"(«{marca}»): no sale.")
                return None
            salida["marca"] = marca
            if b.get("texto"):
                salida["texto"] = self._sin_html(b["texto"])
        elif tipo == bq.TIPO_VIDEO:
            video = self._video(b.get("video") or b.get("url"))
            if video is None:
                self.avisos.append("La IA puso un vídeo que no está entre los que tiene a mano "
                                   "(los del hilo y la instrucción): no sale.")
                return None
            salida["video"] = video
            for campo in ("titulo", "texto", "boton"):
                if b.get(campo):
                    salida[campo] = self._sin_html(b[campo])
        elif tipo == bq.TIPO_CTA:
            salida["botones"] = self._botones_de(b.get("botones"))
        elif tipo == bq.TIPO_HERO:
            imagen = self._imagen(b.get("imagen"))
            if imagen is None:
                self.avisos.append("La IA puso un hero con una imagen que no está entre las "
                                   "IMÁGENES de las webs: no sale.")
                return None
            salida["imagen"] = imagen
            for campo in ("titulo", "subtitulo"):
                if b.get(campo):
                    salida[campo] = self._sin_html(b[campo])
            if isinstance(b.get("puntos"), list):
                salida["puntos"] = [self._sin_html(p) for p in b["puntos"] if p]
            botones = self._botones_de(b.get("botones"))
            if botones:
                salida["botones"] = botones
            if b.get("disposicion"):
                salida["disposicion"] = str(b["disposicion"])
        elif tipo == bq.TIPO_MUESTRAS:
            pies_ia = b.get("pies") if isinstance(b.get("pies"), list) else []
            imagenes: list[dict[str, str]] = []
            pies: list[str] = []
            fuera = 0
            for i, r in enumerate(b.get("imagenes") or []):
                imagen = self._imagen(r)
                if imagen is None:
                    fuera += 1
                    continue
                imagenes.append(imagen)
                pies.append(self._sin_html(pies_ia[i]) if i < len(pies_ia) and pies_ia[i] else "")
            if fuera:
                self.avisos.append(f"La IA puso {fuera} imagen(es) en las muestras que no están "
                                   "entre las IMÁGENES de las webs: no salen.")
            salida["imagenes"] = imagenes
            if any(pies):
                salida["pies"] = pies
            if b.get("titulo"):
                salida["titulo"] = self._sin_html(b["titulo"])
        elif tipo == bq.TIPO_DESTACADO:
            for campo in ("titulo", "texto", "estilo"):
                if b.get(campo):
                    salida[campo] = self._sin_html(b[campo])
        elif tipo == bq.TIPO_SEPARADOR:
            if b.get("estilo"):
                salida["estilo"] = str(b["estilo"])
        else:
            self.avisos.append(f"La IA pidió un bloque que BoHub no tiene («{tipo or '—'}»): "
                               "no sale.")
            return None
        return salida


def _urls_de(bloque: dict[str, Any]) -> set[str]:
    """Las URL que ya lleva un bloque guardado: las de sus enlaces de texto,
    su vídeo y sus botones."""
    urls = {html_mod.unescape(u) for u in re.findall(r'href="([^"]*)"', bloque.get("html") or "")}
    video = videos_mod.identidad(bloque.get("video")) if bloque.get("video") else None
    if video:
        urls.add(videos_mod.url_de(video))
    for bt in bloque.get("botones") or []:
        valor = bt.get("url")
        urls.update(valor.values() if isinstance(valor, dict) else [valor] if valor else [])
    return {str(u) for u in urls if u}


def _registrar_html(n: int, contexto: str) -> None:
    if n:
        logger.warning("correo compuesto: la IA devolvió HTML en %d texto(s) de %s; descartado "
                       "(el HTML lo genera BoHub)", n, contexto)


def estructura_desde_ia(bloques: Any, refs: Referencias, *, idioma: str,
                        marca: str | None = None) -> Resultado:
    """La estructura a partir de la lista de bloques de la IA."""
    conv = _Conversor(refs, idioma)
    convertidos = [c for c in (conv.bloque(b) for b in (bloques if isinstance(bloques, list)
                                                         else [])) if c]
    avisos = list(conv.avisos)
    estructura = bq.normalizar({"idioma": idioma, "marca": marca, "bloques": convertidos}, avisos)
    if estructura.get("marca") is None:
        primera = next((b.get("marca") for b in estructura["bloques"]
                        if b["tipo"] == bq.TIPO_MARCA), None)
        estructura["marca"] = primera
    _registrar_html(conv.html_descartado, "una estructura nueva")
    return Resultado(estructura=estructura, avisos=avisos, html_descartado=conv.html_descartado)


# --- la estructura, como la ve la IA -------------------------------------------------------

def html_a_markdown(html: str, refs: Referencias) -> str:
    """El HTML de un bloque «texto» en el markdown sencillo de la IA."""
    s = str(html or "")

    def _enlace(m: re.Match[str]) -> str:
        url = html_mod.unescape(m.group(1))
        return f"[{m.group(2)}]({refs.ref_de_url(url)})"

    s = re.sub(r'<a href="([^"]*)">(.*?)</a>', _enlace, s, flags=re.DOTALL)
    s = re.sub(r"</?strong>", "**", s)
    s = re.sub(r"</?em>", "*", s)
    s = re.sub(r"</?u>", "", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<li>", "- ", s)
    s = re.sub(r"</li>", "\n", s)
    s = re.sub(r"</?(ul|ol)>", "\n", s)
    s = re.sub(r"</p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _producto_para_ia(p: dict[str, str], refs: Referencias) -> Any:
    """Un producto de un bloque como lo ve la IA: su referencia; una ficha de
    la web con el precio que indicó quien envía (o con otra imagen), con ellos,
    para que una corrección no los pierda."""
    ref = refs.ref_de_producto(p) or f"?{p['origen_id']}"
    extra = {k: p[k] for k in ("precio", "imagen") if p.get(k)}
    return {"ref": ref, **extra} if extra else ref


def bloque_para_ia(b: dict[str, Any], refs: Referencias) -> dict[str, Any]:
    """Un bloque guardado en el formato con el que lo ve (y lo devuelve) la IA."""
    salida: dict[str, Any] = {"id": b["id"], "tipo": b["tipo"]}
    tipo = b["tipo"]
    if tipo == bq.TIPO_TEXTO:
        salida["texto"] = html_a_markdown(b.get("html") or "", refs)
    elif tipo == bq.TIPO_PRODUCTO:
        salida["producto"] = _producto_para_ia(b["producto"], refs)
    elif tipo == bq.TIPO_PRODUCTOS_FILA:
        salida["productos"] = [_producto_para_ia(p, refs) for p in b.get("productos") or []]
    elif tipo == bq.TIPO_VIDEO:
        video = videos_mod.identidad(b.get("video"))
        salida["video"] = (refs.ref_de_video(video) or videos_mod.url_de(video)) if video else ""
    elif tipo == bq.TIPO_HERO:
        imagen = bq.clave_imagen(b.get("imagen"))
        salida["imagen"] = (refs.ref_de_imagen(imagen["id"]) or f"?{imagen['id']}") if imagen \
            else ""
        if b.get("puntos"):
            salida["puntos"] = [bq.en_idioma(p, None) for p in b["puntos"]]
    elif tipo == bq.TIPO_MUESTRAS:
        salida["imagenes"] = [refs.ref_de_imagen(i["id"]) or f"?{i['id']}"
                              for i in (bq.clave_imagen(r) for r in b.get("imagenes") or []) if i]
        if b.get("pies"):
            salida["pies"] = [bq.en_idioma(p, None) for p in b["pies"]]
    if tipo in (bq.TIPO_CTA, bq.TIPO_HERO) and b.get("botones"):
        salida["botones"] = [{"texto": bq.en_idioma(bt.get("texto"), None),
                              "enlace": refs.ref_de_url(bq.en_idioma(bt.get("url"), None))}
                             for bt in b.get("botones") or []]
    for campo in ("marca", "titulo", "subtitulo", "boton", "estilo", "disposicion"):
        if b.get(campo):
            salida[campo] = (b[campo] if campo in ("marca", "estilo", "disposicion")
                             else bq.en_idioma(b[campo], None))
    if tipo in (bq.TIPO_DESTACADO, bq.TIPO_VIDEO, bq.TIPO_MARCA) and b.get("texto"):
        salida["texto"] = bq.en_idioma(b["texto"], None)
    return salida


def estructura_para_ia(estructura: dict[str, Any], refs: Referencias) -> str:
    """La estructura actual (con los id de bloque) como JSON para el prompt."""
    return json.dumps({"idioma": estructura.get("idioma"), "bloques": [
        bloque_para_ia(b, refs) for b in estructura.get("bloques") or []]},
        ensure_ascii=False)


# --- correcciones: operaciones sobre la estructura ------------------------------------------

def _posicion(bloques: list[dict[str, Any]], ref: Any) -> int | None:
    """El índice DESPUÉS del que se inserta (`inicio` → 0; sin ref → al final)."""
    if ref in (None, ""):
        return len(bloques)
    if str(ref).lower() in ("inicio", "principio", "start"):
        return 0
    for i, b in enumerate(bloques):
        if b["id"] == ref:
            return i + 1
    return None


def aplicar_operaciones(estructura: dict[str, Any], operaciones: Any, refs: Referencias,
                        ) -> Resultado:
    """La estructura después de las operaciones de una corrección. Lo que no
    nombran se queda igual; una operación que no se entiende se salta y se
    dice por qué."""
    e = copy.deepcopy(bq.normalizar(estructura))
    idioma = e["idioma"]
    conv = _Conversor(refs, idioma)
    conv.imagenes_del_correo = set(bq.imagenes_de(e))
    conv.productos_del_correo = {f"?{p['origen_id']}": p for p in bq.productos_de(e)}
    avisos: list[str] = []
    bloques: list[dict[str, Any]] = e["bloques"]

    def _indice(bid: Any) -> int | None:
        return next((i for i, b in enumerate(bloques) if b["id"] == bid), None)

    for op in operaciones if isinstance(operaciones, list) else []:
        if not isinstance(op, dict):
            continue
        tipo = str(op.get("op") or "").strip().lower()
        i = _indice(op.get("id"))
        if tipo in ("quitar", "editar", "mover", "duplicar", "reemplazar") and i is None:
            avisos.append(f"La corrección nombra un bloque que no existe («{op.get('id')}»).")
            continue
        if tipo == "quitar":
            bloques.pop(i)
        elif tipo == "editar":
            campo = str(op.get("campo") or "").strip()
            cambio = conv.campo(bloques[i], campo, op.get("valor"))
            limpio = (bq.normalizar({"bloques": [{**bloques[i], **cambio}]}, avisos)["bloques"]
                      if cambio else [])
            if not limpio:
                avisos.append(f"La corrección quería cambiar «{campo or '—'}» de un bloque y "
                              "no se ha podido: el bloque se queda como estaba.")
                continue
            limpio[0]["id"] = bloques[i]["id"]
            # Una fila que pasa de tres se parte en varias (filas parejas): van todas.
            bloques[i:i + 1] = limpio
        elif tipo in ("añadir", "anadir", "add"):
            nuevo = conv.bloque(op.get("bloque"))
            limpio = bq.normalizar({"bloques": [nuevo]}, avisos)["bloques"] if nuevo else []
            donde = _posicion(bloques, op.get("despues_de"))
            if not limpio or donde is None:
                avisos.append("Un bloque que la corrección quería añadir no se ha podido poner.")
                continue
            for b in limpio:
                b["id"] = bq.nuevo_id()
            bloques[donde:donde] = limpio
        elif tipo == "mover":
            bloque = bloques.pop(i)
            donde = _posicion(bloques, op.get("despues_de"))
            bloques.insert(len(bloques) if donde is None else donde, bloque)
        elif tipo == "duplicar":
            copia = copy.deepcopy(bloques[i])
            copia["id"] = bq.nuevo_id()
            bloques.insert(i + 1, copia)
        elif tipo == "reemplazar":
            nuevo = conv.bloque(op.get("bloque"))
            limpio = bq.normalizar({"bloques": [nuevo]}, avisos)["bloques"] if nuevo else []
            if limpio:
                limpio[0]["id"] = bloques[i]["id"]
                bloques[i:i + 1] = limpio
        elif tipo == "idioma":
            e["idioma"] = str(op.get("valor") or idioma).strip().lower()[:2] or idioma
        else:
            avisos.append(f"Una operación que BoHub no conoce («{tipo or '—'}»): se salta.")
    avisos.extend(conv.avisos)
    resultado = bq.normalizar(e, avisos)
    _registrar_html(conv.html_descartado, "una corrección")
    return Resultado(estructura=resultado, avisos=avisos, html_descartado=conv.html_descartado)


# --- lo que la guardia y las reglas miran ----------------------------------------------------

def texto_libre(estructura: dict[str, Any]) -> str:
    """Los textos que escribe la IA (no los del catálogo), en texto plano: lo
    que mira la guardia del cuerpo y la regla de los precios."""
    from app.services.asistente.texto import html_a_texto  # noqa: PLC0415

    partes: list[str] = []
    idioma = estructura.get("idioma")
    for b in estructura.get("bloques") or []:
        if b["tipo"] == bq.TIPO_TEXTO:
            partes.append(html_a_texto(b.get("html") or ""))
        for campo in ("titulo", "subtitulo", "texto", "boton"):
            if b["tipo"] != bq.TIPO_TEXTO and b.get(campo):
                partes.append(bq.en_idioma(b[campo], idioma))
        for campo in ("puntos", "pies"):
            partes.extend(bq.en_idioma(p, idioma) for p in b.get(campo) or [])
        for bt in b.get("botones") or []:
            partes.append(bq.en_idioma(bt.get("texto"), idioma))
    return "\n\n".join(p for p in partes if p)


_IMPORTE_RE = re.compile(
    r"(?:(?:€|\$|£|EUR|USD|GBP)\s?\d[\d.,\s]*\d|\d[\d.,\s]*\d?\s?(?:€|\$|£|EUR|USD|GBP)\b|"
    r"\d[\d.,\s]*\d?\s?(?:€|\$|£))")


#: Un código de modelo en un texto: letras y al menos tres cifras seguidas
#: («3000U», «5000U», «i3200»); un número solo («3000 Stück») no lo es.
_CODIGO_TEXTO_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d{3})(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,}\b")
#: Ni una medida con su unidad («1200mm», «2000W», «230V», «1000ml», «60x90»).
_MEDIDA_RE = re.compile(
    r"^\d+(?:[.,]\d+)?(?:mm|cm|m|km|w|kw|v|kv|a|ma|mah|ml|cl|l|g|kg|dpi|ppp|rpm|hz|khz|s|ms|"
    r"h|min|gb|mb|tb|°c|x\d+)$", re.IGNORECASE)
#: En el nombre de una ficha también vale el número solo («MBO 3050»).
_CODIGO_FICHA_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d{3})[A-Za-z0-9]{3,}\b")


def modelos_con_otro_nombre(texto: str, nombres: list[str]) -> list[tuple[str, str]]:
    """Los códigos de modelo del texto («3000U») con el número de una máquina
    que el correo enseña en ficha con otro código («artisJet 3000Pro
    Freebird»): puede que el texto hable de ella como si fuera otra. El
    12/10/2026 el correo enseñaba la ficha del 3000Pro y decía «Den artisJet
    3000U PRO prüfe ich noch»: el cliente lo había marcado con el nombre de
    su formulario. Hasta que el compositor tenga alias por producto, se avisa
    para que la persona lo revise; el texto no se toca."""
    def _numero(codigo: str) -> str:
        m = re.search(r"\d{3,}", codigo)
        return m.group(0) if m else ""

    fichas = []
    for nombre in nombres:
        codigos = {m.group(0).lower() for m in _CODIGO_FICHA_RE.finditer(nombre or "")}
        fichas.append((nombre, codigos, {_numero(c) for c in codigos} - {""}))
    todos = set().union(*(codigos for _, codigos, _ in fichas)) if fichas else set()
    salida: list[tuple[str, str]] = []
    for m in _CODIGO_TEXTO_RE.finditer(texto or ""):
        codigo = m.group(0)
        # Si es el código de alguna ficha del correo, nombra esa; una medida, nada.
        if codigo.lower() in todos or _MEDIDA_RE.match(codigo):
            continue
        for nombre, codigos, numeros in fichas:
            if (_numero(codigo) in numeros and codigo.lower() not in codigos
                    and (codigo, nombre) not in salida):
                salida.append((codigo, nombre))
    return salida


def importes_ajenos(texto: str, precios: list[str]) -> list[str]:
    """Los importes de los textos de la IA que no son ninguno de los precios
    del catálogo que salen en el correo (la regla: ningún precio que no venga
    del catálogo; los precios los ponen las fichas)."""
    def _num(s: str) -> str:
        return re.sub(r"\D", "", s)

    validos = {_num(p) for p in precios if _num(p)}
    return [m.group(0).strip() for m in _IMPORTE_RE.finditer(texto or "")
            if _num(m.group(0)) and _num(m.group(0)) not in validos]
