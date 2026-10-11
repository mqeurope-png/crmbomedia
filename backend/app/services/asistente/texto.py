"""Texto: de HTML a texto plano, recortes, normalización para comparar, el
paso inverso (el borrador en texto plano → HTML para el compositor) y, desde
el 11/10/2026, el borrador en HTML sencillo: el subconjunto de etiquetas que
admite el editor, limpiado con bleach (lo que no está en la lista se quita
conservando el contenido) y su texto plano para la guardia y la comparación."""
from __future__ import annotations

import html as html_mod
import re
from collections.abc import Callable, Iterable

import bleach

from app.services.html_sanitizer import html_to_text

_FIRMA_RE = re.compile(r"<!--crmbo:signature-->[\s\S]*?<!--/crmbo:signature-->", re.IGNORECASE)
_ESPACIOS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")

#: Las etiquetas que el borrador de la IA puede traer y el editor entiende:
#: párrafos, saltos, negrita, cursiva, subrayado, encabezados, listas, enlaces
#: y una tabla simple. Todo lo demás (span, div, img, estilos, colores) se
#: quita, conservando el texto. `b`/`i` se normalizan a `strong`/`em`.
ETIQUETAS_BORRADOR: tuple[str, ...] = (
    "p", "br", "strong", "em", "u", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "a",
    "table", "thead", "tbody", "tr", "th", "td",
)
_ETIQUETA_RE = re.compile(
    r"<(p|br|strong|b|em|i|u|h[1-6]|ul|ol|li|a|table|tr|td|th|div|span)\b[^>]*>", re.IGNORECASE)
_SIN_CONTENIDO_RE = re.compile(
    r"<(script|style|iframe|object|embed|head|title)\b[^>]*>[\s\S]*?</\1\s*>", re.IGNORECASE)
_COMENTARIO_RE = re.compile(r"<!--[\s\S]*?-->")
#: `b`/`i` pasan a `strong`/`em`; los bloques que no entendemos (`div`,
#: `section`, `blockquote`…) pasan a párrafo para que su texto no quede suelto.
_B_I_RE = (
    (re.compile(r"<b(\s[^>]*)?>", re.IGNORECASE), "<strong>"),
    (re.compile(r"</b\s*>", re.IGNORECASE), "</strong>"),
    (re.compile(r"<i(\s[^>]*)?>", re.IGNORECASE), "<em>"),
    (re.compile(r"</i\s*>", re.IGNORECASE), "</em>"),
    (re.compile(r"<(div|section|article|blockquote|pre|address)(\s[^>]*)?>", re.IGNORECASE),
     "<p>"),
    (re.compile(r"</(div|section|article|blockquote|pre|address)\s*>", re.IGNORECASE), "</p>"),
)
_PARRAFO_VACIO_RE = re.compile(r"<p>(\s|&nbsp;|<br\s*/?>)*</p>", re.IGNORECASE)
_ENLACE_SIN_HREF_RE = re.compile(r"<a>([\s\S]*?)</a>", re.IGNORECASE)
_HREF_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
#: Cabeceras de respuesta citada («El 3/10/2026, Fulano escribió:», «On … wrote:»,
#: «Am … schrieb:», «Le … a écrit :», «Op … schreef:») y las líneas con `>`.
#: La cabecera de la cita lleva una fecha (cifras), el verbo y acaba en dos
#: puntos AL FINAL de la línea («El 10/10/2026, Admin escribió:»); una frase
#: normal con «escribió esto: …» no cumple y no se toca.
_CITA_RE = re.compile(
    r"(^|\n)\s*(el|on|am|le|op)\s[^\n]*\d[^\n]*(escribi[óo]|wrote|schrieb|a [ée]crit|schreef)"
    r"[^\n]{0,80}:[ \t]*(\n[\s\S]*)?$",
    re.IGNORECASE,
)
_LINEAS_CITADAS_RE = re.compile(r"(^|\n)\s*>[^\n]*", re.MULTILINE)
_SEPARADOR_RE = re.compile(r"\n-{2,}\s*(Original Message|Mensaje original|Ursprüngliche Nachricht)"
                           r"[\s\S]*$", re.IGNORECASE)


def texto_de_mensaje(body_text: str | None, body_html: str | None) -> str:
    """El texto de un correo: el plano si lo hay, si no el HTML pasado a texto."""
    texto = (body_text or "").strip()
    if not texto and body_html:
        texto = html_to_text(body_html).strip()
    return texto


def sin_cita(texto: str) -> str:
    """Quita lo que el cliente citó del mensaje anterior: en un hilo ya tenemos
    cada mensaje, así que la cita solo duplica tokens."""
    salida = _CITA_RE.sub("", texto)
    salida = _SEPARADOR_RE.sub("", salida)
    salida = _LINEAS_CITADAS_RE.sub("", salida)
    return salida.strip() or texto.strip()


def recortar(texto: str, maximo: int) -> str:
    texto = texto.strip()
    if len(texto) <= maximo:
        return texto
    return texto[: maximo - 1].rstrip() + "…"


def sin_firma(html: str | None) -> str:
    """El HTML del compositor sin el bloque de firma que él mismo añadió."""
    return _FIRMA_RE.sub("", html or "")


def normalizado(texto: str | None) -> str:
    """Para comparar el borrador con lo que se envió: minúsculas, sin
    puntuación suelta ni espacios de más."""
    plano = html_mod.unescape(texto or "").lower()
    plano = re.sub(r"[—–\-–—«»\"'“”‘’*_|]", " ", plano)
    plano = re.sub(r"[.,;:!?¡¿()\[\]]", " ", plano)
    return _ESPACIOS_RE.sub(" ", plano).strip()


#: Frases con las que la IA, en vez de redactar el correo, le habla a la persona
#: de BoHub: «he revisado el hilo», «no tengo una pregunta del cliente a la que
#: contestar», «dímelo y lo redacto». Solo las inequívocas: un cierre normal de
#: un correo a un cliente («dime si te va bien el jueves», «let me know if you
#: have any questions», «se lo preparo esta semana», «el pedido no me ha
#: llegado») NO es un informe, y la revisión del 11/10/2026 encontró que una
#: lista más larga se cargaba borradores buenos.
_INFORME_RE = re.compile(
    r"\bhe (revisado|repasado|le[ií]do) (el|la|este|esta|tu|su) (hilo|conversaci[oó]n|"
    r"intercambio|expediente|correo del cliente|mensaje del cliente)\b"
    r"|\bno (tengo|hay|encuentro|veo) (una |ninguna |ningún |nada )?(pregunta|mensaje|consulta|"
    r"nada)[^.\n]{0,60}\b(contestar|responder)\b"
    r"|\bno hay (ningún |nada |ningún mensaje |ningún correo )?(mensaje |correo )?"
    r"nuevo del cliente\b"
    r"|\bd[ií]melo y (te )?lo (redacto|preparo|escribo)\b"
    r"|\bdime (qu[eé]|si|c[oó]mo)[^.\n]{0,60}\by (te )?lo (redacto|preparo|escribo)\b"
    r"|\bsi (quieres|prefieres) que (te )?(prepare|redacte|escriba) (un |el )?(seguimiento|"
    r"borrador|correo)\b"
    r"|\bI('ve| have) (reviewed|checked|looked at|gone through) (the|this) (thread|conversation|"
    r"exchange|email thread)\b"
    r"|\bthere('s| is) (nothing|no (new )?(message|question)) (new )?(from the (client|customer) )?"
    r"to (reply|respond|answer)\b"
    r"|\bich habe (den|die|das|diesen|diese) (Verlauf|Thread|Konversation|E-Mail-Verlauf)\b"
    r"[^.\n]{0,40}\b(geprüft|durchgesehen|gelesen|angesehen)\b"
    r"|\bj'ai (relu|vérifié|examiné|parcouru) (le fil|l'échange|la conversation)\b"
    r"|\bik heb (de|het|deze) (thread|conversatie|gesprek|e-mailwisseling) (bekeken|nagelezen|"
    r"gecontroleerd|doorgenomen)\b",
    re.IGNORECASE,
)
_SALUDO_RE = re.compile(
    r"^\W*(hola|hi|hello|hey|hallo|bonjour|salut|hoi|dag|buenos d[ií]as|buenas( tardes)?|"
    r"guten (tag|morgen)|good (morning|afternoon))\W+(?P<nombre>[^\W\d_]+)",
    re.IGNORECASE,
)


def parece_informe_al_usuario(
    cuerpo: str, *, nombre_usuario: str | None = None, nombre_destinatario: str | None = None,
) -> str | None:
    """Por qué el cuerpo NO es un correo al destinatario sino un informe para la
    persona de BoHub, o None si parece un correo. Dos señales: frases de
    informe inequívocas («he revisado el hilo», «dímelo y lo redacto») y un
    saludo al usuario de BoHub por su nombre cuando se SABE que el destinatario
    se llama de otra manera (sin ficha no se sabe, y un cliente puede llamarse
    como quien pulsa)."""
    texto = (cuerpo or "").strip()
    if not texto:
        return None
    m = _INFORME_RE.search(texto)
    if m:
        return f"habla a quien lo pide, no al cliente («{m.group(0).strip()}»)"
    pila = (nombre_usuario or "").strip().split(" ")[0].lower()
    destino = (nombre_destinatario or "").strip().split(" ")[0].lower()
    saludo = _SALUDO_RE.match(texto)
    if pila and destino and destino != pila and saludo and saludo.group("nombre").lower() == pila:
        return f"saluda a {saludo.group('nombre')}, que es quien lo pide, no el destinatario"
    return None


def a_html(texto: str, enlaces: Iterable[str] = ()) -> str:
    """Párrafos (separados por línea en blanco) → `<p>`; saltos simples →
    `<br>`; las URL que están en la lista de enlaces, como `<a>`."""
    permitidas = {u.strip() for u in enlaces if u and u.strip()}

    def _enlazar(parrafo: str) -> str:
        escapado = html_mod.escape(parrafo)

        def _sustituir(m: re.Match[str]) -> str:
            url = html_mod.unescape(m.group(0))
            if url in permitidas:
                seguro = html_mod.escape(url, quote=True)
                return f'<a href="{seguro}">{seguro}</a>'
            return m.group(0)

        return _URL_RE.sub(_sustituir, escapado).replace("\n", "<br>")

    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    return "".join(f"<p>{_enlazar(p)}</p>" for p in parrafos)


# --- el borrador en HTML sencillo -----------------------------------------------------------


def parece_html(cuerpo: str) -> bool:
    """Si lo que devolvió el modelo (o lo que hay en el editor) trae etiquetas
    de las que entendemos; si no, es texto plano con párrafos."""
    return bool(_ETIQUETA_RE.search(cuerpo or ""))


def enlaces_de(html: str) -> list[str]:
    """Las URL de los `href` de un HTML (lo que la persona ya tenía enlazado en
    el editor: en una corrección esos enlaces se conservan)."""
    return [html_mod.unescape(a or b) for a, b in _HREF_RE.findall(html or "") if (a or b)]


def _filtro_href(permitidas: set[str] | None) -> Callable[[str, str, str], bool]:
    def _vale(tag: str, name: str, value: str) -> bool:
        if name != "href":
            return False
        url = value.strip()
        if not re.match(r"^(https?://|mailto:)", url, re.IGNORECASE):
            return False
        return permitidas is None or url in permitidas
    return _vale


def limpiar_html(html: str | None, enlaces: Iterable[str] | None = None) -> str:
    """El HTML del borrador reducido a lo que el editor admite. Lo que no está
    en `ETIQUETAS_BORRADOR` se quita conservando su contenido (un `<span>` o
    un `<div>` dejan su texto; un `<img>` no tiene texto y desaparece), los
    estilos y clases se van, los comentarios también, y `b`/`i` pasan a
    `strong`/`em`. Enlaces: solo `http(s)`/`mailto`; con `enlaces`, solo los
    de esa lista (regla 4: ninguno inventado) y el resto deja su texto sin
    enlace. Nunca se pierde contenido."""
    crudo = _SIN_CONTENIDO_RE.sub("", html or "")
    crudo = _COMENTARIO_RE.sub("", crudo)
    for patron, sustituto in _B_I_RE:
        crudo = patron.sub(sustituto, crudo)
    permitidas = None if enlaces is None else {u.strip() for u in enlaces if u and u.strip()}
    limpio = bleach.clean(
        crudo, tags=list(ETIQUETAS_BORRADOR), attributes={"a": _filtro_href(permitidas)},
        protocols=["http", "https", "mailto"], strip=True, strip_comments=True,
    )
    limpio = _ENLACE_SIN_HREF_RE.sub(r"\1", limpio)
    limpio = _PARRAFO_VACIO_RE.sub("", limpio)
    return limpio.strip()


def html_a_texto(html: str | None) -> str:
    """El texto plano de un borrador en HTML, legible: un párrafo por bloque,
    los elementos de lista con «- » (o «1. » en las numeradas), los enlaces como
    «texto (url)» cuando el texto no es la propia URL. Es lo que mira la
    guardia del cuerpo y lo que se guarda en `texto`."""
    if not html:
        return ""
    s = _COMENTARIO_RE.sub("", html)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)

    def _enlace(m: re.Match[str]) -> str:
        url = html_mod.unescape(m.group(1) or m.group(2) or "").strip()
        texto_enlace = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if not texto_enlace:
            return url
        return texto_enlace if texto_enlace == url or not url else f"{texto_enlace} ({url})"

    s = re.sub(r"""<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>([\s\S]*?)</a>""",
               _enlace, s, flags=re.IGNORECASE)
    # Listas numeradas: cada <li> con su número; las demás, con un guion.
    def _ol(m: re.Match[str]) -> str:
        items = re.findall(r"<li\b[^>]*>([\s\S]*?)</li>", m.group(1), flags=re.IGNORECASE)
        return "\n" + "\n".join(f"{i}. {re.sub(r'<[^>]+>', '', it).strip()}"
                                for i, it in enumerate(items, 1)) + "\n\n"
    s = re.sub(r"<ol\b[^>]*>([\s\S]*?)</ol>", _ol, s, flags=re.IGNORECASE)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"</(td|th)>", "\t", s, flags=re.IGNORECASE)
    s = re.sub(r"</(p|h[1-6]|li|tr|table|ul|ol|thead|tbody|div)>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s).replace("\xa0", " ")
    lineas = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    plano = "\n".join(lineas)
    plano = re.sub(r"\n{3,}", "\n\n", plano)
    # Los elementos de una misma lista van seguidos, sin línea en blanco.
    plano = re.sub(r"\n\n(?=(- |\d+\. ))", "\n", plano)
    return plano.strip()


def cuerpo_a_html(cuerpo: str, enlaces: Iterable[str] = ()) -> tuple[str, str]:
    """`(html, texto)` de lo que devolvió el modelo en «cuerpo»: si trae
    etiquetas, se limpia al subconjunto del editor (enlaces solo de la lista)
    y el texto plano se deriva de él; si es texto plano con párrafos, se pasa
    a HTML como antes."""
    cuerpo = (cuerpo or "").strip()
    if not cuerpo:
        return "", ""
    lista = list(enlaces)
    if parece_html(cuerpo):
        html = limpiar_html(cuerpo, lista)
        return html, html_a_texto(html)
    return a_html(cuerpo, lista), cuerpo


def empalmar(html_actual: str, seleccion_html: str, fragmento_html: str) -> str | None:
    """El cuerpo entero con la selección sustituida por el fragmento
    reescrito, si la selección aparece tal cual (una vez al menos) en el
    cuerpo; None si no (el compositor la sustituye entonces en el editor)."""
    sel = (seleccion_html or "").strip()
    if not sel or sel not in (html_actual or ""):
        return None
    return html_actual.replace(sel, fragmento_html, 1)
