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
_CITA_RE = re.compile(
    r"(^|\n)\s*(el|on|am|le|op)\s[^\n]{5,160}(escribi[óo]|wrote|schrieb|a [ée]crit|schreef)"
    r"[^\n]{0,80}:[\s\S]*$",
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
