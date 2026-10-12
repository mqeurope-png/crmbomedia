"""La IA elige la estructura de un correo compuesto; nunca escribe su HTML.

- `modo_pedido`: si la instrucción pide un correo compuesto («mándale la gama
  DTF», «prepárale un correo con las máquinas»), uno normal («dile que el
  pedido sale el martes») o no lo dice (entonces elige la IA, por defecto
  normal). El interruptor de la franja manda sobre todo esto.
- `Referencias`: lo que la IA puede nombrar, siempre por referencia: los
  productos del CATÁLOGO (`P1`, `C1`), los ENLACES (`L1`), los vídeos de
  YouTube que salen en el hilo o en la instrucción (`V1`) y la web de cada
  marca (`M:mbo_dtf`). Una referencia que no existe no entra: así no hay
  productos, precios ni enlaces inventados.
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
_OBJETO_GAMA = (
    r"(gama|cat[aá]logo|fichas?|m[aá]quinas|modelos|equipos|impresoras|productos|opciones|"
    r"range|lineup|line-up|catalog(ue)?|machines|models|products|printers|options|"
    r"maschinen|modelle|produkte|drucker|gamme|machines|mod[eè]les|produits|imprimantes|"
    r"machines|modellen|producten|printers)"
)
_PIDE_COMPUESTO_RE = re.compile(
    rf"\b{_VERBO_PRESENTAR}\b[^.?!\n]{{0,60}}\b{_OBJETO_GAMA}\b"
    r"|\b(maquetad[oa]|con im[aá]genes|con fotos|con fichas|fichas de producto|newsletter|"
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
    r"\b(solo texto|en texto|como texto|texto normal|texto plano|sin fichas|sin im[aá]genes|"
    r"sin maquetar|correo normal|plain text|text only|no images)\b",
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
    if _PIDE_COMPUESTO_RE.search(texto):
        return MODO_COMPUESTO
    if _PIDE_NORMAL_RE.search(texto):
        return MODO_NORMAL
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


_YOUTUBE_EN_TEXTO_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s<>\"']*v=|shorts/|embed/|live/)|"
    r"youtu\.be/)[A-Za-z0-9_-]{11}[^\s<>\"')\]]*")


def videos_de(*textos: str | None) -> list[str]:
    """Las URL de YouTube que aparecen en los textos, sin repetir."""
    salida: list[str] = []
    for texto in textos:
        for url in _YOUTUBE_EN_TEXTO_RE.findall(texto or ""):
            url = url.rstrip(".,;:")
            if bq.video_youtube(url) and url not in salida:
                salida.append(url)
    return salida


@dataclass
class Referencias:
    """Lo que la IA puede nombrar en un correo compuesto, por referencia."""

    productos: dict[str, dict[str, str]] = field(default_factory=dict)   # P1 → clave
    enlaces_producto: dict[str, str] = field(default_factory=dict)       # P1 → URL
    enlaces: dict[str, str] = field(default_factory=dict)                # L1 → URL
    videos: dict[str, str] = field(default_factory=dict)                 # V1 → URL
    marcas: dict[str, dict[str, Any]] = field(default_factory=dict)      # clave → marca

    @classmethod
    def desde(cls, *, maquinas: list[dict[str, Any]], consumibles: list[dict[str, Any]],
              enlaces: list[dict[str, Any]], videos: list[str],
              marcas: dict[str, dict[str, Any]]) -> Referencias:
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
        for i, url in enumerate(videos, 1):
            refs.videos[f"V{i}"] = url
        return refs

    def como_dict(self) -> dict[str, Any]:
        return {"productos": self.productos, "enlaces_producto": self.enlaces_producto,
                "enlaces": self.enlaces, "videos": self.videos}

    @classmethod
    def de_dict(cls, datos: dict[str, Any] | None, *, marcas: dict[str, dict[str, Any]],
                videos: list[str] | None = None) -> Referencias:
        datos = datos if isinstance(datos, dict) else {}
        refs = cls(productos=dict(datos.get("productos") or {}),
                   enlaces_producto=dict(datos.get("enlaces_producto") or {}),
                   enlaces=dict(datos.get("enlaces") or {}),
                   videos=dict(datos.get("videos") or {}), marcas=dict(marcas))
        for url in videos or []:
            if url not in refs.videos.values():
                refs.videos[f"V{len(refs.videos) + 1}"] = url
        return refs

    # -- de referencia a valor, y vuelta ---------------------------------------------------

    def url_marca(self, marca: str, idioma: str) -> str | None:
        m = self.marcas.get(clave_marca(marca))
        return bq.url_valida(bq.en_idioma((m or {}).get("urls") or {}, idioma)) if m else None

    def permitidas(self, idioma: str) -> set[str]:
        urls = set(self.enlaces.values()) | set(self.enlaces_producto.values()) | set(
            self.videos.values())
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
            return self.videos[s]
        if s.upper().startswith("M:"):
            return self.url_marca(s[2:], idioma)
        if s.lower().startswith("mailto:") and "@" in s:
            return s
        return s if s in self.permitidas(idioma) else None

    def ref_de_url(self, url: str) -> str:
        for mapa in (self.enlaces, self.enlaces_producto, self.videos):
            for ref, u in mapa.items():
                if u == url:
                    return ref
        return url

    def ref_de_producto(self, clave: dict[str, str]) -> str | None:
        for ref, c in self.productos.items():
            if c == clave:
                return ref
        return None


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

    def _sin_html(self, valor: Any) -> str:
        texto = "" if valor is None else str(valor)
        if bq.tiene_marcado(texto):
            self.html_descartado += 1
            texto = re.sub(r"<\s*br\s*/?\s*>", "\n", texto, flags=re.IGNORECASE)
            texto = re.sub(r"</\s*(p|li|div|h[1-6]|tr)\s*>", "\n", texto, flags=re.IGNORECASE)
            texto = bq.texto_plano(texto, bq.MAX_TEXTO_RICO)
        return texto

    def _producto(self, ref: Any) -> dict[str, str] | None:
        clave = self.refs.productos.get(str(ref or "").strip())
        if clave is None:
            self.avisos.append(f"La IA nombró un producto que no está en el catálogo («{ref}»): "
                               "no sale.")
        return clave

    def _enlace_texto(self, destino: str) -> str | None:
        url = self.refs.url(destino, self.idioma)
        if url is None:
            logger.warning("correo compuesto: enlace fuera de la lista descartado: %s", destino)
        return url

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
            url = self.refs.url(b.get("video") or b.get("url"), self.idioma)
            if not url or not bq.video_youtube(url):
                self.avisos.append("La IA puso un vídeo que no está en el hilo ni en la "
                                   "instrucción: no sale.")
                return None
            salida["url"] = url
            for campo in ("titulo", "texto", "boton"):
                if b.get(campo):
                    salida[campo] = self._sin_html(b[campo])
        elif tipo == bq.TIPO_CTA:
            botones = []
            for bt in b.get("botones") or []:
                if not isinstance(bt, dict):
                    continue
                url = self.refs.url(bt.get("enlace") or bt.get("url"), self.idioma)
                if not url:
                    self.avisos.append(f"Un botón con un enlace que no está en la lista "
                                       f"(«{bt.get('enlace') or bt.get('url')}»): no sale.")
                    continue
                botones.append({"texto": self._sin_html(bt.get("texto")), "url": url})
            salida["botones"] = botones
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


def bloque_para_ia(b: dict[str, Any], refs: Referencias) -> dict[str, Any]:
    """Un bloque guardado en el formato con el que lo ve (y lo devuelve) la IA."""
    salida: dict[str, Any] = {"id": b["id"], "tipo": b["tipo"]}
    tipo = b["tipo"]
    if tipo == bq.TIPO_TEXTO:
        salida["texto"] = html_a_markdown(b.get("html") or "", refs)
    elif tipo == bq.TIPO_PRODUCTO:
        salida["producto"] = refs.ref_de_producto(b["producto"]) or f"?{b['producto']['origen_id']}"
    elif tipo == bq.TIPO_PRODUCTOS_FILA:
        salida["productos"] = [refs.ref_de_producto(p) or f"?{p['origen_id']}"
                               for p in b.get("productos") or []]
    elif tipo == bq.TIPO_VIDEO:
        salida["video"] = refs.ref_de_url(b.get("url") or "")
    elif tipo == bq.TIPO_CTA:
        salida["botones"] = [{"texto": bq.en_idioma(bt.get("texto"), None),
                              "enlace": refs.ref_de_url(bq.en_idioma(bt.get("url"), None))}
                             for bt in b.get("botones") or []]
    for campo in ("marca", "titulo", "boton", "estilo"):
        if b.get(campo):
            salida[campo] = (b[campo] if campo in ("marca", "estilo")
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
            actual = bloque_para_ia(bloques[i], refs)
            actual[campo] = op.get("valor")
            nuevo = conv.bloque(actual)
            if nuevo is not None:
                limpio = bq.normalizar({"bloques": [nuevo]}, avisos)["bloques"]
                if limpio:
                    limpio[0]["id"] = bloques[i]["id"]
                    bloques[i:i + 1] = limpio[:1]
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
                bloques[i:i + 1] = limpio[:1]
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
    for b in estructura.get("bloques") or []:
        if b["tipo"] == bq.TIPO_TEXTO:
            partes.append(html_a_texto(b.get("html") or ""))
        for campo in ("titulo", "texto", "boton"):
            if b["tipo"] != bq.TIPO_TEXTO and b.get(campo):
                partes.append(bq.en_idioma(b[campo], estructura.get("idioma")))
        for bt in b.get("botones") or []:
            partes.append(bq.en_idioma(bt.get("texto"), estructura.get("idioma")))
    return "\n\n".join(p for p in partes if p)


_IMPORTE_RE = re.compile(
    r"(?:(?:€|\$|£|EUR|USD|GBP)\s?\d[\d.,\s]*\d|\d[\d.,\s]*\d?\s?(?:€|\$|£|EUR|USD|GBP)\b|"
    r"\d[\d.,\s]*\d?\s?(?:€|\$|£))")


def importes_ajenos(texto: str, precios: list[str]) -> list[str]:
    """Los importes de los textos de la IA que no son ninguno de los precios
    del catálogo que salen en el correo (la regla: ningún precio que no venga
    del catálogo; los precios los ponen las fichas)."""
    def _num(s: str) -> str:
        return re.sub(r"\D", "", s)

    validos = {_num(p) for p in precios if _num(p)}
    return [m.group(0).strip() for m in _IMPORTE_RE.finditer(texto or "")
            if _num(m.group(0)) and _num(m.group(0)) not in validos]
