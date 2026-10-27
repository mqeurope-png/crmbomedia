"""Texto: de HTML a texto plano, recortes, normalización para comparar, el
paso inverso (el borrador en texto plano → HTML para el compositor) y, desde
el 11/10/2026, el borrador en HTML sencillo: el subconjunto de etiquetas que
admite el editor, limpiado con bleach (lo que no está en la lista se quita
conservando el contenido) y su texto plano para la guardia y la comparación."""
from __future__ import annotations

import html as html_mod
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

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


#: La guardia del cuerpo (regla primera) en dos niveles. INFORME, lo que delata
#: sin duda un texto dirigido a la persona de BoHub y no al cliente: hablar del
#: hilo como quien lo ha revisado («he revisado el hilo con Marny»), decir que
#: no hay nada que contestar, decir que no puede hacer algo por falta de datos
#: («no tengo datos para…»), preguntarle qué quiere decirle, hablar del
#: cliente o de quien pide en tercera persona cuando se saben sus nombres
#: («Manel ya le envió»), o saludar a quien pide por su nombre. DUDOSO, lo que
#: puede ser las dos cosas: «el cliente» o «el hilo» como objeto, el nombre de
#: quien pide fuera de la despedida. Ante la duda se avisa y no se borra. NO
#: delata una oferta de hacer algo («dímelo y te lo preparo», «dime si te va
#: bien el jueves», «let me know if you have any questions»): el primer día de
#: uso real (11/10/2026) se vació un correo válido por eso.
_INFORME_RE = re.compile(
    r"\bhe (revisado|repasado|le[ií]do) (el|la|este|esta|tu|su) (hilo|conversaci[oó]n|"
    r"intercambio|expediente|correo del cliente|mensaje del cliente)\b"
    r"|\bno (tengo|hay|encuentro|veo) (una |ninguna |ningún |nada )?(pregunta|mensaje|consulta|"
    r"nada)[^.\n]{0,60}\b(contestar|responder)\b"
    r"|\bno hay (ningún |nada |ningún mensaje |ningún correo )?(mensaje |correo )?"
    r"nuevo del cliente\b"
    r"|\bno (tengo|dispongo de|cuento con) (los |suficientes |más |bastantes |la )?(datos|"
    r"informaci[oó]n|detalles|elementos)( suficientes| necesarios)? (para|como para|sobre|de)\b"
    r"|\bsin (esos |los |m[aá]s |esa |la )?(datos|informaci[oó]n) no puedo\b"
    r"|\bno puedo (confirmar|redactar|preparar|responder|contestar|calcular|dar|indicar)"
    r"[^.\n]{0,60}\b(sin (los |esos |m[aá]s )?datos|porque no (tengo|hay|dispongo)|porque falta)\b"
    r"|\b(dime|d[ií]game|cu[eé]ntame|ind[ií]came) qu[eé] quieres (decirle|que le diga|contarle)\b"
    r"|\bI('ve| have) (reviewed|checked|looked at|gone through) (the|this) (thread|conversation|"
    r"exchange|email thread)\b"
    r"|\bthere('s| is) (nothing|no (new )?(message|question)) (new )?(from the (client|customer) )?"
    r"to (reply|respond|answer)\b"
    r"|\bI (don't|do not) have (the |enough |any |sufficient )?(data|information|details)"
    r"( needed| required)? (to|for|about|on)\b"
    r"|\bich habe (den|die|das|diesen|diese) (Verlauf|Thread|Konversation|E-Mail-Verlauf)\b"
    r"[^.\n]{0,40}\b(geprüft|durchgesehen|gelesen|angesehen)\b"
    r"|\bich habe (keine|nicht genug|nicht genügend) (Daten|Informationen|Angaben)\b"
    r"|\bj'ai (relu|vérifié|examiné|parcouru) (le fil|l'échange|la conversation)\b"
    r"|\bje n'ai pas (les |de |assez de |suffisamment d['e] ?)?(données|informations)\b"
    r"|\bik heb (de|het|deze) (thread|conversatie|gesprek|e-mailwisseling) (bekeken|nagelezen|"
    r"gecontroleerd|doorgenomen)\b"
    r"|\bik heb (geen|niet genoeg|onvoldoende) (gegevens|informatie)\b",
    re.IGNORECASE,
)
_DUDOSO_RE = re.compile(
    r"\b(el|la|al|del|este|esta) client[ea]\b"
    r"|\bthe (client|customer)\b"
    r"|\b(der|die|dem|den) (Kunde|Kundin|Kunden)\b"
    r"|\b(le|la|au|du) cliente?\b"
    r"|\bde klant\b"
    r"|\b(en|del|este|el|al) (hilo|expediente)\b"
    r"|\b(in|the) (this )?thread\b",
    re.IGNORECASE,
)
_SALUDO_RE = re.compile(
    r"^\W*(hola|hi|hello|hey|hallo|bonjour|salut|hoi|dag|buenos d[ií]as|buenas( tardes)?|"
    r"guten (tag|morgen)|good (morning|afternoon))\W+(?P<nombre>[^\W\d_]+)",
    re.IGNORECASE,
)
#: Lo que precede a un nombre cuando se le está hablando («Hola Marny», «Estimada
#: Marny», «Gracias, Marny»): esa mención no es tercera persona.
_VOCATIVO_PREVIO_RE = re.compile(
    r"\b(hola|hi|hello|hey|hallo|bonjour|salut|hoi|dag|dear|estimad[oa]|querid[oa]|apreciad[oa]|"
    r"lieber?|ch[eè]re?|beste|buenos d[ií]as|buenas( tardes| noches)?|guten (tag|morgen)|"
    r"good (morning|afternoon|evening)|gracias|thanks|thank you|danke|merci|bedankt|saludos|"
    r"un saludo|abrazos?|regards|cheers|hasta (pronto|luego|el \w+)|see you|bis bald|"
    r"à bientôt|tot ziens)[^\w\n]*$",            # en la misma línea que el nombre
    re.IGNORECASE,
)

NIVEL_CORREO = "correo"
NIVEL_DUDOSO = "dudoso"
NIVEL_INFORME = "informe"


@dataclass(frozen=True)
class Veredicto:
    """Lo que la guardia dice del cuerpo: `correo` (se envía tal cual),
    `dudoso` (se queda en el editor, con aviso) o `informe` (se vacía)."""
    nivel: str
    motivo: str | None = None


def _pila(nombre: str | None) -> str:
    return (nombre or "").strip().split(" ")[0]


#: Lo que precede a un nombre cuando se habla DE esa persona, no con ella: una
#: preposición («a Marny», «con Marny», «with John») o un verbo del que es
#: sujeto pospuesto («que pidió Marny»).
_TERCERA_PREVIO_RE = re.compile(
    r"\b(a|al|con|para|de|sobre|seg[uú]n|to|with|for|about|from|von|mit|f[uü]r|an|à|avec|pour|"
    r"chez|aan|met|voor|bij"
    r"|pidi[oó]|dijo|pregunt[oó]|quiere|quer[ií]a|llam[oó]|escribi[oó]|coment[oó]|envi[oó]|"
    r"mand[oó]|tiene|necesita|asked|said|wants|needs|told|called|emailed)\W*$",
    re.IGNORECASE,
)
#: Lo que sigue a un nombre cuando es el sujeto de una frase sobre él («Marny
#: ya tiene», «Marny pidió», «John wants», «Marny está interesada»).
_TERCERA_SIGUIENTE_RE = re.compile(
    r"^\s+((ya|no|también|nos|me|les|os|le|lo|la) )*(tiene|ten[ií]a|quiere|quer[ií]a|pide|"
    r"pidi[oó]|necesita|dice|dijo|pregunta|pregunt[oó]|est[aá]|estaba|ha |hab[ií]a |es |era |"
    r"has |had |is |was |wants|needs|asked|asks|said|says|hat |ist |will |möchte|braucht|a |"
    r"est |veut|demande|heeft|wil |vraagt)",
    re.IGNORECASE,
)


def _tercera_persona(texto: str, nombre: str) -> tuple[str, bool] | None:
    """El trozo donde el texto nombra a `nombre` sin dirigirse a él, y si está
    claro que habla DE esa persona (a media frase, tras una preposición o un
    verbo, «con Marny», «que pidió Marny», o como sujeto, «que Marny ya
    tiene») o solo la nombra. No cuentan un saludo («Hola Marny,»), un
    vocativo («Marny, te adjunto…», «…con calma, Marny.») ni la palabra en
    minúscula: un nombre que también es palabra corriente («Will» en «we will
    send», «Rosa» en «la tinta rosa») no puede cargarse un correo válido. A
    principio de frase la mayúscula no distingue el nombre de la palabra
    («Rosa es el color…»): ahí, como mucho, dudoso. Si alguna mención está
    clara, esa; si no, la primera."""
    primera: tuple[str, bool] | None = None
    for m in re.finditer(rf"\b{re.escape(nombre)}\b", texto, re.IGNORECASE):
        if not m.group(0)[:1].isupper():
            continue
        antes = texto[max(0, m.start() - 30):m.start()]
        despues = texto[m.end():m.end() + 24]
        if _VOCATIVO_PREVIO_RE.search(antes) or re.match(r"^\s*[,:!]", despues):
            continue
        if re.search(r",\s*$", antes) and re.match(r"^\s*([.!?…]|$)", despues):
            continue                                   # «…con calma, Marny.»
        previo = texto[:m.start()].rstrip(" \t")
        inicio_de_frase = not previo or previo[-1] in ".!?¡¿:;\n"
        trozo = texto[max(0, m.start() - 25):m.end() + 25].replace("\n", " ").strip()
        if not inicio_de_frase and (
                _TERCERA_PREVIO_RE.search(antes) or _TERCERA_SIGUIENTE_RE.match(despues)):
            return trozo, True
        if primera is None:
            primera = (trozo, False)
    return primera


def _sin_despedida(texto: str) -> str:
    """El cuerpo sin las dos últimas líneas con texto (la despedida y la firma
    con el nombre de pila de quien envía, que es normal que esté ahí)."""
    lineas = [ln for ln in texto.split("\n") if ln.strip()]
    return "\n".join(lineas[:-2]) if len(lineas) > 2 else ""


def evaluar_cuerpo(
    cuerpo: str, *, nombre_usuario: str | None = None, nombre_destinatario: str | None = None,
) -> Veredicto:
    """La guardia del cuerpo. INFORME (se vacía) solo cuando está claro que el
    texto va dirigido a la persona de BoHub; DUDOSO (se queda, con aviso) cuando
    puede ser las dos cosas; CORREO si nada lo delata. Los nombres: el de pila
    de quien pide y el del destinatario, si se saben (sin ficha no se sabe, y
    si los dos se llaman igual no se puede distinguir por el nombre)."""
    texto = (cuerpo or "").strip()
    if not texto:
        return Veredicto(NIVEL_CORREO)
    m = _INFORME_RE.search(texto)
    if m:
        return Veredicto(
            NIVEL_INFORME, f"habla a quien lo pide, no al cliente («{m.group(0).strip()}»)")
    pila, destino = _pila(nombre_usuario), _pila(nombre_destinatario)
    mismo_nombre = bool(pila) and pila.lower() == destino.lower()
    if pila and destino and not mismo_nombre:
        saludo = _SALUDO_RE.match(texto)
        if saludo and saludo.group("nombre").lower() == pila.lower():
            return Veredicto(
                NIVEL_INFORME,
                f"saluda a {saludo.group('nombre')}, que es quien lo pide, no el destinatario")
    hallado = _tercera_persona(texto, destino) if destino and not mismo_nombre else None
    if hallado and hallado[1]:
        return Veredicto(NIVEL_INFORME, f"habla de {destino} en tercera persona («{hallado[0]}»)")
    if pila and not mismo_nombre:
        # «Manel ya le envió el presupuesto»: le cuenta a quien pide lo que él
        # mismo hizo. («Manel os atenderá el jueves» puede ser un correo
        # válido: eso queda en dudoso, más abajo.)
        m = re.search(
            rf"\b{re.escape(pila)}\b (ya )?((le|te|os|les|nos|lo|la) )?(envi[oó]|mand[oó]|pas[oó]|"
            r"respondi[oó]|contest[oó]|dijo|coment[oó]|adjunt[oó]|escribi[oó]|llam[oó])\b",
            texto, re.IGNORECASE)
        if m:
            return Veredicto(
                NIVEL_INFORME,
                f"habla de {pila} (quien lo pide) en tercera persona («{m.group(0)}»)")
    m = _DUDOSO_RE.search(texto)
    if m:
        return Veredicto(
            NIVEL_DUDOSO,
            f"menciona «{m.group(0).strip()}»: puede estar hablándote a ti y no al destinatario")
    if pila and not mismo_nombre and re.search(rf"\b{re.escape(pila)}\b", _sin_despedida(texto),
                                               re.IGNORECASE):
        return Veredicto(NIVEL_DUDOSO, f"nombra a {pila} (quien lo pide) fuera de la despedida")
    if hallado:
        # Nombra al destinatario fuera del saludo y sin hablarle: puede que
        # hable de esa persona (ante la duda, se queda en el editor con aviso).
        return Veredicto(
            NIVEL_DUDOSO, f"nombra a {destino} fuera del saludo («{hallado[0]}»): puede estar "
                          "hablando de esa persona en vez de hablarle a ella")
    return Veredicto(NIVEL_CORREO)


def parece_informe_al_usuario(
    cuerpo: str, *, nombre_usuario: str | None = None, nombre_destinatario: str | None = None,
) -> str | None:
    """Por qué el cuerpo NO es un correo al destinatario sino un informe para la
    persona de BoHub (se vacía), o None si parece un correo o solo es dudoso."""
    veredicto = evaluar_cuerpo(
        cuerpo, nombre_usuario=nombre_usuario, nombre_destinatario=nombre_destinatario)
    return veredicto.motivo if veredicto.nivel == NIVEL_INFORME else None


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


_BLOQUE_RE = re.compile(r"<(p|div|ul|ol|li|h[1-6]|table|tr|td|th|blockquote|pre)\b", re.IGNORECASE)


def encajar_fragmento(fragmento_html: str, seleccion_html: str) -> str:
    """Un fragmento devuelto como un solo párrafo cuando la selección era texto
    DENTRO de un párrafo (sin bloques): sin el `<p>`, para no anidar párrafos
    al empalmarlo ni partir el párrafo al sustituir la selección en el editor."""
    if _BLOQUE_RE.search(seleccion_html or ""):
        return fragmento_html
    m = re.fullmatch(r"\s*<p>(.*)</p>\s*", fragmento_html or "", re.DOTALL)
    if not m or _BLOQUE_RE.search(m.group(1)):
        return fragmento_html
    return m.group(1).strip()


def empalmar(html_actual: str, seleccion_html: str, fragmento_html: str) -> str | None:
    """El cuerpo entero con la selección sustituida por el fragmento
    reescrito, si la selección aparece tal cual (una vez al menos) en el
    cuerpo; None si no (el compositor la sustituye entonces en el editor)."""
    sel = (seleccion_html or "").strip()
    if not sel or sel not in (html_actual or ""):
        return None
    return html_actual.replace(sel, fragmento_html, 1)


# --- las imágenes del editor en una corrección ----------------------------------------------
#
# El borrador es HTML sencillo SIN imágenes (`ETIQUETAS_BORRADOR`): lo que
# devuelve el modelo pasa por `limpiar_html`, y un `<img>` desaparece. Pero en
# el editor sí puede haber imágenes (la que Bart pega por URL), y el compositor
# pone la corrección («más corto») sin preguntar: la imagen se perdía en
# silencio. Ahora, al mandar el editor al modelo, cada `<img>` se cambia por un
# marcador estable («[IMAGEN 1]») que el modelo tiene que conservar, y al volver
# se pone otra vez la etiqueta ORIGINAL en su sitio (la `src` tal cual; solo
# https y atributos seguros). Si el modelo quitó un marcador, la imagen vuelve
# al final y se avisa: nunca se pierde en silencio. Las subidas nuestras (desde
# el PC, o de una plantilla importada) no entran: se tratan como antes de los
# marcadores, y un editor que solo lleva esas da el turno de siempre.

#: Cómo se ve una imagen en el texto que lee el modelo.
MARCA_IMAGEN = "[IMAGEN {n}]"
_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
#: El marcador de vuelta. Se acepta traducido o con otra caja («[Image 2]»,
#: «[BILD 2]»): una corrección «en inglés» puede traducirlo aunque se le diga
#: que no, y la imagen no debe perderse por eso.
_MARCA_IMAGEN_RE = re.compile(
    r"\[\s*(?:imagen|image|imagem|immagine|bild|afbeelding|img)\s*(\d{1,3})\s*\]", re.IGNORECASE)
#: Los atributos de un `<img>` que vuelven (los mismos que admite el envío,
#: `html_sanitizer`, sin clases; del estilo, solo `_ESTILO_IMG`).
_ATRIBUTOS_IMG = ["src", "alt", "width", "height", "title"]
#: El único estilo que vuelve con la imagen: el que pone «Imagen por URL»
#: (`htmlImagenUrl` en frontend/src/app/components/email/imagenPorUrl.ts) para
#: que no se salga del ancho del correo. Se reconoce como lo guarde el editor
#: («max-width: 100%; height: auto;») y vuelve escrito así; otro CSS, nunca.
_ESTILO_IMG = ("max-width:100%", "height:auto")
_STYLE_RE = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
#: Las imágenes nuestras, por su `src`: las subidas desde el PC
#: (`/assets/email-templates/AAAA/MM/<sha256>.<ext>`) y las de una plantilla
#: importada (`/api/email-templates/<id>/attachments/by-cid/<cid>`), con origen
#: o desde la raíz. Son las que el envío cambia por adjuntos en línea
#: (`_ASSET_SRC_PATTERN` y `_CRM_ATTACHMENT_SRC_PATTERN` en
#: app/integrations/gmail/service.py; aquí, sobre el valor de la `src`).
_SRC_CRUDA_RE = re.compile(r"""\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_SRC_PROPIAS: tuple[re.Pattern[str], ...] = (
    re.compile(r"""(?:https?://[^"']+?)?/assets/email-templates/"""
               r"""\d{4}/\d{2}/[0-9a-f]{64}(?:\.[A-Za-z0-9]{1,5})?"""),
    re.compile(r"""(?:https?://[^'"]+?)?/api/email-templates/"""
               r"""[^/'"?#]+/attachments/by-cid/[^'"?#]+(?:[?#][^'"]*)?""", re.IGNORECASE),
)


@dataclass
class ImagenesEditor:
    """Las imágenes del editor de un turno, apartadas para el modelo.

    - `texto` / `seleccion`: el editor (y la selección) para el prompt, ya
      limpios (`limpiar_html`), con cada imagen cambiada por su marcador.
    - `limpio`: el editor limpio SIN marcadores (lo de siempre, para los turnos
      que no admiten imágenes: un correo compuesto rehecho desde el texto).
    - `etiquetas`: número → la etiqueta `<img>` saneada que vuelve en su sitio.
    - `en_texto` / `en_seleccion`: qué marcadores hay en cada uno (los que
      tienen que volver).
    - `descartadas`: imágenes que no se pueden conservar (no son https).
    - `descartadas_seleccion`: las de `descartadas` que están en la selección
      (en una corrección de la selección, las de fuera no se tocan)."""

    texto: str
    seleccion: str
    limpio: str
    etiquetas: dict[int, str]
    en_texto: list[int]
    en_seleccion: list[int]
    descartadas: int = 0
    descartadas_seleccion: int = 0

    @property
    def marcadores(self) -> list[str]:
        return [MARCA_IMAGEN.format(n=n) for n in sorted(self.etiquetas)]


def _estilo_img(etiqueta: str) -> str:
    """Del `style` de la etiqueta, solo las declaraciones de `_ESTILO_IMG`
    (escritas siempre igual); "" si no lleva ninguna."""
    m = _STYLE_RE.search(etiqueta)
    if not m:
        return ""
    puestas = {re.sub(r"\s+", "", d).lower()
               for d in html_mod.unescape(m.group(1) or m.group(2) or "").split(";")}
    return ";".join(d for d in _ESTILO_IMG if d in puestas)


def img_segura(etiqueta: str) -> str | None:
    """La etiqueta `<img>` saneada (solo `src` https, los atributos de
    `_ATRIBUTOS_IMG` y, del estilo, el de «Imagen por URL»), o None si no
    tiene una `src` https: una imagen en http:// la bloquean los clientes de
    correo, y `data:`/`cid:`/rutas relativas no son de fiar fuera del editor."""
    limpio = bleach.clean(etiqueta, tags=["img"], attributes={"img": _ATRIBUTOS_IMG},
                          protocols=["https"], strip=True).strip()
    m = re.search(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", limpio, re.IGNORECASE)
    src = html_mod.unescape((m.group(1) or m.group(2)) if m else "").strip()
    if not src.lower().startswith("https://") or not limpio.lower().startswith("<img"):
        return None
    estilo = _estilo_img(etiqueta)
    if estilo and limpio.endswith(">"):
        limpio = f'{limpio[:-1].rstrip(" /")} style="{estilo}">'
    return limpio


def es_imagen_propia(etiqueta: str) -> bool:
    """¿Es una imagen nuestra (subida desde el PC o de una plantilla
    importada, `_SRC_PROPIAS`)? Esas no van como marcador."""
    m = _SRC_CRUDA_RE.search(etiqueta)
    src = ((m.group(1) or m.group(2)) if m else "").strip()
    return bool(src) and any(p.fullmatch(src) for p in _SRC_PROPIAS)


def apartar_imagenes(html: str | None, seleccion_html: str | None = None,
                     ) -> ImagenesEditor | None:
    """Las imágenes de `html` (el editor sin la firma) y de la selección,
    cambiadas por marcadores. None si no hay ninguna `<img>` que no sea
    nuestra (`es_imagen_propia`): entonces el turno es exactamente el de
    antes. Las nuestras se quedan como antes (fuera del texto, sin marcador
    ni aviso). La misma imagen (misma etiqueta saneada) lleva el mismo número
    en el texto y en la selección."""
    crudo = html or ""
    if all(es_imagen_propia(t)
           for t in _IMG_RE.findall(crudo) + _IMG_RE.findall(seleccion_html or "")):
        return None
    etiquetas: dict[int, str] = {}
    por_etiqueta: dict[str, int] = {}
    descartadas = 0

    def _marcar(fragmento: str, vistos: list[int]) -> str:
        def _cambiar(m: re.Match[str]) -> str:
            nonlocal descartadas
            if es_imagen_propia(m.group(0)):
                # Como antes de los marcadores: `limpiar_html` la quita.
                return m.group(0)
            segura = img_segura(m.group(0))
            if segura is None:
                descartadas += 1
                return ""
            n = por_etiqueta.get(segura)
            if n is None:
                n = len(etiquetas) + 1
                por_etiqueta[segura] = n
                etiquetas[n] = segura
            if n not in vistos:
                vistos.append(n)
            return MARCA_IMAGEN.format(n=n)

        return _IMG_RE.sub(_cambiar, fragmento)

    en_texto: list[int] = []
    texto = limpiar_html(_marcar(crudo, en_texto))
    sueltas = descartadas
    en_seleccion: list[int] = []
    seleccion = limpiar_html(_marcar(seleccion_html, en_seleccion)) if seleccion_html else ""
    # Las de la selección ya se contaron en el texto (la selección está dentro):
    # aparte, para que una corrección de la selección avise solo de las suyas.
    return ImagenesEditor(texto=texto, seleccion=seleccion, limpio=limpiar_html(crudo),
                          etiquetas=etiquetas, en_texto=en_texto, en_seleccion=en_seleccion,
                          descartadas=sueltas, descartadas_seleccion=descartadas - sueltas)


def restaurar_imagenes(html: str, etiquetas: dict[int, str], esperadas: Iterable[int], *,
                       en_bloque: bool = True) -> tuple[str, list[int]]:
    """El HTML que devolvió el modelo con cada marcador cambiado por su
    imagen. Un marcador que no estaba (inventado, o de una imagen que no es
    de este trozo) se quita; las imágenes `esperadas` cuyo marcador ya no
    está vuelven al final (en su párrafo si `en_bloque`; sueltas si el turno
    es un fragmento que va dentro de un párrafo). Devuelve el HTML y los
    números de las que volvieron al final (para avisar)."""
    esperadas = [n for n in dict.fromkeys(esperadas) if n in etiquetas]
    presentes: set[int] = set()

    def _cambiar(m: re.Match[str]) -> str:
        n = int(m.group(1))
        if n in esperadas:
            presentes.add(n)
            return etiquetas[n]
        return ""

    salida = _MARCA_IMAGEN_RE.sub(_cambiar, html or "")
    faltan = [n for n in esperadas if n not in presentes]
    for n in faltan:
        salida += f"<p>{etiquetas[n]}</p>" if en_bloque else etiquetas[n]
    salida = _PARRAFO_VACIO_RE.sub("", salida)
    return salida.strip(), faltan


def sin_marcas_de_imagen(texto: str) -> str:
    """El texto sin los marcadores de imagen (lo que quede de ellos en un
    texto plano), sin dejar líneas en blanco de más."""
    salida = _MARCA_IMAGEN_RE.sub("", texto or "")
    salida = re.sub(r"[ \t]+\n", "\n", salida)
    return re.sub(r"\n{3,}", "\n\n", salida).strip()
