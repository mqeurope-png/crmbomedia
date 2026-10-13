"""Qué empresa ve el cliente, y la marca que ya no existe.

Bomedia salió del ecosistema el 13/10/2026: todo lo que acaba delante de un
cliente (correos, plantillas, firmas, avisos, PDF, formularios) va a nombre de
Streamtec o de MQ Europe, con el mismo criterio que las rutas de soporte
(`conocimiento.rutas`): castellano → Streamtec; el resto de idiomas (en, de,
fr, nl, pt…) → MQ Europe. Si el correo lleva un documento con empresa emisora
y es una de esas dos, manda la emisora; si no —o es la serie 1, que en
FACTUSOL sigue siendo «Bomedia S.L.» por decisión de Bart—, el idioma.

«Bomedia» se busca como mención comercial: las direcciones y los dominios
heredados (`bart@bomedia.net`, `bomedia.net`, `https://bomedia.es/…`) siguen
vivos, reciben correo y no cuentan. Lo usan la guardia del asistente (que
sustituye y avisa con la frase), su cabecera (la línea EMPRESA) y la
comprobación del Cuadre que recorre los textos de cara al cliente.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.conocimiento.rutas import IDIOMA_CASTELLANO

STREAMTEC = "Streamtec"
MQ_EUROPE = "MQ Europe"
#: La marca heredada, tal como se nombra en los avisos.
BOMEDIA = "Bomedia"

#: Serie de FACTUSOL (= empresa emisora) → empresa que firma de cara al
#: cliente. Solo las dos que pueden: la 1 (Bomedia S.L.) y la 4 (Lambert) no
#: están y caen al idioma.
EMPRESA_DE_SERIE: dict[int, str] = {5: STREAMTEC, 2: MQ_EUROPE}

#: De dónde sale la empresa de un correo: de su documento o de su idioma.
ORIGEN_DOCUMENTO = "documento"
ORIGEN_IDIOMA = "idioma"


def empresa_por_idioma(idioma: str | None) -> str:
    """Castellano → Streamtec; cualquier otro idioma → MQ Europe. Sin idioma,
    el de partida de BoHub (castellano)."""
    codigo = (idioma or "").strip().lower()[:2]
    if not codigo or codigo == IDIOMA_CASTELLANO:
        return STREAMTEC
    return MQ_EUROPE


def empresa_de_emisora(valor: Any) -> str | None:
    """Streamtec o MQ Europe si `valor` (el número de serie o el nombre de la
    emisora) es una de las dos; None si es otra (Bomedia S.L., Lambert) o no
    se sabe."""
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    if texto.isdigit():
        return EMPRESA_DE_SERIE.get(int(texto))
    plano = " ".join(texto.lower().split())
    if "streamtec" in plano:
        return STREAMTEC
    if "mq europe" in plano or "mqeurope" in plano:
        return MQ_EUROPE
    return None


def empresa_para(idioma: str | None, emisora: Any = None) -> tuple[str, str]:
    """`(empresa, origen)`: la emisora del documento si es Streamtec o MQ
    Europe (`ORIGEN_DOCUMENTO`); si no, la del idioma (`ORIGEN_IDIOMA`)."""
    del_documento = empresa_de_emisora(emisora)
    if del_documento:
        return del_documento, ORIGEN_DOCUMENTO
    return empresa_por_idioma(idioma), ORIGEN_IDIOMA


# --- «Bomedia» ------------------------------------------------------------------------------

#: La palabra suelta: no pegada a otra (`mbomedia`), ni detrás de una arroba o
#: un punto (`bart@bomedia.net`, `www.bomedia.net`), ni dentro de los
#: parámetros de un enlace (`?utm_source=bomedia`, `#bomedia`, `cid:bomedia`),
#: ni delante de una arroba (`bomedia@bomedia.net`). Detrás de una barra o un
#: guion SÍ cuenta («Streamtec/Bomedia»); las rutas y URL se descartan aparte,
#: mirando la palabra entera (`_es_direccion`).
_BOMEDIA_RE = re.compile(r"(?<![\w@.=?&#%:])bomedia(?![\w@])", re.IGNORECASE)
#: Detrás, un dominio (`bomedia.net`, `bomedia.es`): es una dirección, no una
#: mención. En minúsculas: «Bomedia.Gracias» (sin espacio) es una mención, y
#: «Bomedia. Saludos» también (tras el punto va un espacio).
_DOMINIO_RE = re.compile(r"\.[a-z]{2,}\b")
#: La palabra entera (hasta un espacio, comilla o etiqueta) donde está la mención.
_PALABRA_RE = re.compile(r"[^\s\"'<>]+")
#: Una ruta con fichero al final (`/static/bomedia-v4/logo.png`).
_RUTA_RE = re.compile(r"/[^\s\"'<>]*\.[A-Za-z0-9]{2,4}$")
#: La forma jurídica que a veces la acompaña («Bomedia, S.L.», «Bomedia SL»):
#: se sustituye junto con el nombre (MQ Europe no es una S.L.).
_FORMA_JURIDICA_RE = re.compile(r"\s*,?\s*s\.?\s?l\.?(?:u\.?)?(?!\w)", re.IGNORECASE)
#: Dónde acaba una frase: un punto, cierre o puntos suspensivos seguidos de
#: espacio, o un salto de línea («9.300 €» no corta: tras el punto va una cifra).
_LIMITE_FRASE_RE = re.compile(r"[.!?…](?=\s)|\n")
#: Lo más largo que se enseña de una frase en un aviso o en el Cuadre.
MAX_FRASE = 240


def menciones(texto: str | None) -> list[tuple[int, int]]:
    """Los tramos `(inicio, fin)` de cada mención comercial de «Bomedia» en
    `texto`, con su «, S.L.» si lo lleva. Las direcciones y los dominios no
    son menciones."""
    texto = texto or ""
    salida: list[tuple[int, int]] = []
    for m in _BOMEDIA_RE.finditer(texto):
        if _DOMINIO_RE.match(texto, m.end()) or _es_direccion(texto, m.start()):
            continue
        fin = m.end()
        forma = _FORMA_JURIDICA_RE.match(texto, fin)
        if forma:
            fin = forma.end()
        salida.append((m.start(), fin))
    return salida


def _es_direccion(texto: str, posicion: int) -> bool:
    """Si la palabra entera donde cae `posicion` es una URL, una dirección o
    una ruta de fichero, no un nombre («https://…?ref=x», «…/bomedia-v4/logo.png»)."""
    for m in _PALABRA_RE.finditer(texto, max(0, posicion - 300), posicion + 300):
        if m.start() <= posicion < m.end():
            palabra = m.group(0)
            return ("://" in palabra or "www." in palabra.lower() or "@" in palabra
                    or bool(_RUTA_RE.search(palabra)))
    return False


def hay_bomedia(texto: str | None) -> bool:
    """Si `texto` menciona «Bomedia» (sin contar direcciones ni dominios)."""
    return bool(menciones(texto))


_ETIQUETA_RE = re.compile(r"<[^>]+>")
#: Lo que corta una línea en el HTML sencillo del borrador (o en texto plano).
_CORTE_LINEA_RE = re.compile(
    r"\n|<(?:br|/?p|/?li|/?div|/?h[1-6]|/?ul|/?ol|/?tr|/?td|/?th|/?table)\b[^>]*>",
    re.IGNORECASE)
#: Lo que se salta para ver qué sigue: espacios y etiquetas en línea.
_EN_LINEA_RE = re.compile(r"^(?:[ \t]|&nbsp;|</?(?:strong|b|em|i|u|a|span)\b[^>]*>)*",
                          re.IGNORECASE)


def _cierra_frase(texto: str, inicio: int, fin: int) -> bool:
    """Tras «… Bomedia S.L.» el punto de «S.L.» cerraba también la frase si lo
    que sigue es el final de la línea o una frase nueva (mayúscula, también
    tras «¿», «¡» o «»). Sola en su línea (la firma: «Admin\\nBomedia, S.L.»,
    o «<br>Bomedia, S.L.</p>») no hay frase que cerrar. Las etiquetas en línea
    (`<strong>`, `<em>`) no cuentan como corte."""
    resto = _EN_LINEA_RE.sub("", texto[fin:], count=1)
    fin_de_linea = not resto or bool(_CORTE_LINEA_RE.match(resto))
    antes = _ETIQUETA_RE.sub("", _CORTE_LINEA_RE.split(texto[:inicio])[-1])
    antes = antes.replace("&nbsp;", " ").strip()
    if fin_de_linea:
        return bool(antes)
    return resto.lstrip("¿¡«\"'(")[:1].isupper()


def sustituir(texto: str | None, empresa: str) -> tuple[str, int]:
    """`(texto, cuántas)`: cada mención de «Bomedia» cambiada por `empresa`,
    en mayúsculas si estaba en mayúsculas. Las direcciones y los dominios se
    quedan como están. Vale para texto plano y para el HTML sencillo de un
    borrador (un `href` con el dominio no es una mención)."""
    texto = texto or ""
    tramos = menciones(texto)
    if not tramos:
        return texto, 0
    partes: list[str] = []
    previo = 0
    for inicio, fin in tramos:
        original = texto[inicio:fin]
        nuevo = empresa.upper() if original[:len(BOMEDIA)].isupper() else empresa
        if (fin - inicio > len(BOMEDIA) and original.endswith(".")
                and _cierra_frase(texto, inicio, fin)):
            nuevo += "."
        partes.append(texto[previo:inicio])
        partes.append(nuevo)
        previo = fin
    partes.append(texto[previo:])
    return "".join(partes), len(tramos)


def _recortar_alrededor(frase: str, inicio: int, fin: int, maximo: int) -> str:
    if len(frase) <= maximo:
        return frase
    medio = (inicio + fin) // 2
    desde = max(0, min(medio - maximo // 2, len(frase) - maximo))
    trozo = frase[desde:desde + maximo].strip()
    return ("…" if desde > 0 else "") + trozo + ("…" if desde + maximo < len(frase) else "")


def frases(texto: str | None, *, maximo: int = MAX_FRASE) -> list[str]:
    """Cada frase de `texto` (texto plano) que menciona «Bomedia», sin
    repetir, recortada alrededor de la mención si es muy larga."""
    texto = texto or ""
    tramos = menciones(texto)
    # El punto de «S.L.» es parte de la mención, no el final de la frase.
    limites = [m for m in _LIMITE_FRASE_RE.finditer(texto)
               if not any(i <= m.start() < f for i, f in tramos)]
    vistas: set[str] = set()
    salida: list[str] = []
    for inicio, fin in tramos:
        desde = max((m.end() for m in limites if m.end() <= inicio), default=0)
        hasta = min((m.end() for m in limites if m.start() >= fin), default=len(texto))
        crudo = texto[desde:hasta]
        frase = crudo.strip()
        if not frase or frase in vistas:
            continue
        vistas.add(frase)
        sangria = len(crudo) - len(crudo.lstrip())
        salida.append(_recortar_alrededor(
            frase, inicio - desde - sangria, fin - desde - sangria, maximo))
    return salida


@dataclass(frozen=True)
class Cambio:
    """Una frase que la guardia ha tocado: tal cual la escribió la IA y como
    ha quedado. `campo` dice dónde estaba (`asunto` o `cuerpo`)."""
    campo: str
    antes: str
    despues: str

    def como_dict(self) -> dict[str, str]:
        return {"campo": self.campo, "antes": self.antes, "despues": self.despues}


def cambios_en(texto_plano: str | None, empresa: str, *, campo: str) -> list[Cambio]:
    """Las frases de `texto_plano` que mencionan «Bomedia», antes y después de
    sustituirla por `empresa`: lo que el aviso enseña para que se relean."""
    return [Cambio(campo=campo, antes=f, despues=sustituir(f, empresa)[0])
            for f in frases(texto_plano)]


def sustituir_en(valor: Any, empresa: str) -> tuple[Any, int]:
    """`sustituir` en un texto o, recorriéndolos, en las listas y diccionarios
    que lo contienen (los textos por idioma de un correo compuesto)."""
    if isinstance(valor, str):
        return sustituir(valor, empresa)
    if isinstance(valor, list):
        total = 0
        lista = []
        for v in valor:
            nuevo, n = sustituir_en(v, empresa)
            lista.append(nuevo)
            total += n
        return lista, total
    if isinstance(valor, dict):
        total = 0
        dicc = {}
        for k, v in valor.items():
            nuevo, n = sustituir_en(v, empresa)
            dicc[k] = nuevo
            total += n
        return dicc, total
    return valor, 0


def textos_de(valores: Iterable[Any]) -> list[str]:
    """Los textos sueltos de unos valores (texto, lista o diccionario por
    idioma), para buscar en ellos."""
    salida: list[str] = []
    for v in valores:
        if isinstance(v, str):
            salida.append(v)
        elif isinstance(v, list):
            salida.extend(textos_de(v))
        elif isinstance(v, dict):
            salida.extend(textos_de(v.values()))
    return salida
