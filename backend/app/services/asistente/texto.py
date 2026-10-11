"""Texto: de HTML a texto plano, recortes, normalización para comparar y
el paso inverso (el borrador en texto plano → HTML para el compositor)."""
from __future__ import annotations

import html as html_mod
import re
from collections.abc import Iterable

from app.services.html_sanitizer import html_to_text

_FIRMA_RE = re.compile(r"<!--crmbo:signature-->[\s\S]*?<!--/crmbo:signature-->", re.IGNORECASE)
_ESPACIOS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
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
