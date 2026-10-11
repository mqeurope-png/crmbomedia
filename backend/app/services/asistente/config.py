"""Configuración del asistente de respuesta (Configuración ERP → «Asistente
de respuesta»). Vive en el blob `factusol_series_json` de `ErpSettings`, bajo
la clave `asistente` (sin migración):

    {"presupuesto_tokens": 12000, "historico_max": 5, "asuntos_max": 15,
     "dominios_gratuitos": ["gmail.com", ...],
     "modelo_borrador": null, "modelo_correccion": "claude-haiku-5-5",
     "modelos": ["claude-haiku-5-5", ...], "pensamiento_extendido": false}

- `presupuesto_tokens`: tokens de entrada por generación. Los niveles se
  llenan por orden (hilo, expediente, correos viejos, empresa) y se corta
  cuando se agota, soltando primero lo más viejo. El hilo no se recorta nunca.
- `historico_max`: cuántos correos viejos con cuerpo entran como mucho.
- `asuntos_max`: cuántos asuntos recientes (títulos) lleva el expediente.
- `dominios_gratuitos`: la empresa se agrupa por el registro de empresa, nunca
  por el dominio del correo; esta lista evita que una «empresa» que sea en
  realidad un dominio gratuito (gmail.com) junte a desconocidos.
- `modelo_borrador`: el modelo del primer borrador; vacío = el de
  `ANTHROPIC_MODEL`. `modelo_correccion`: el de las correcciones («más
  corto», «en inglés»), que trabajan sobre un texto ya escrito y no necesitan
  el modelo grande. `modelos`: la lista que se ofrece en el desplegable, para
  probar uno nuevo sin desplegar. `pensamiento_extendido`: apagado por
  defecto; el pensamiento se factura como salida y redactar con el contexto
  delante no lo necesita (`services/ia/modelos.py`).
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.services.ia.modelos import (
    MODELO_CORRECCION_DEFECTO,
    MODELOS_DE_PARTIDA,
    es_nombre_valido,
    normalizar_lista,
)

CONFIG_KEY = "asistente"
PRESUPUESTO_DEFECTO = 12000
PRESUPUESTO_MIN = 2000
PRESUPUESTO_MAX = 150000
HISTORICO_MAX_DEFECTO = 5
ASUNTOS_MAX_DEFECTO = 15
#: Caracteres por token, de media, en texto europeo mezclado con HTML plano:
#: una estimación conservadora para no pasarse del presupuesto.
CHARS_POR_TOKEN = 3.5

DOMINIOS_GRATUITOS_DEFECTO: tuple[str, ...] = (
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "hotmail.fr", "hotmail.de",
    "hotmail.it", "hotmail.nl", "hotmail.co.uk", "outlook.com", "outlook.es", "outlook.fr",
    "outlook.de", "live.com", "live.nl", "live.fr", "live.de", "msn.com", "yahoo.com",
    "yahoo.es", "yahoo.fr", "yahoo.de", "yahoo.co.uk", "yahoo.it", "ymail.com", "icloud.com",
    "me.com", "mac.com", "aol.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "web.de",
    "t-online.de", "freenet.de", "protonmail.com", "proton.me", "pm.me", "orange.fr",
    "wanadoo.fr", "free.fr", "laposte.net", "sfr.fr", "telenet.be", "skynet.be", "ziggo.nl",
    "kpnmail.nl", "planet.nl", "home.nl", "sapo.pt", "libero.it", "virgilio.it", "zoho.com",
    "mail.com", "yandex.com", "terra.es", "telefonica.net", "movistar.es",
)

_DOMINIO_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def estimar_tokens(texto: str) -> int:
    return int(math.ceil(len(texto or "") / CHARS_POR_TOKEN))


def _entero(raw: Any, defecto: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(raw)
    except (TypeError, ValueError):
        return defecto
    return valor if minimo <= valor <= maximo else defecto


def _dominios(raw: Any, defecto: Iterable[str]) -> list[str]:
    if not isinstance(raw, list):
        return list(defecto)
    salida: list[str] = []
    for d in raw:
        texto = str(d or "").strip().lower().lstrip("@")
        if texto and _DOMINIO_RE.match(texto) and texto not in salida:
            salida.append(texto)
    return salida


def _modelo(raw: Any) -> str | None:
    """Un nombre de modelo válido en minúsculas, o None (vacío o basura)."""
    nombre = str(raw or "").strip().lower()
    return nombre if es_nombre_valido(nombre) else None


def defectos() -> dict[str, Any]:
    return {
        "presupuesto_tokens": PRESUPUESTO_DEFECTO,
        "historico_max": HISTORICO_MAX_DEFECTO,
        "asuntos_max": ASUNTOS_MAX_DEFECTO,
        "dominios_gratuitos": list(DOMINIOS_GRATUITOS_DEFECTO),
        "modelo_borrador": None,
        "modelo_correccion": MODELO_CORRECCION_DEFECTO,
        "modelos": list(MODELOS_DE_PARTIDA),
        "pensamiento_extendido": False,
    }


def normalizar(raw: Any) -> dict[str, Any]:
    """Config completa (con defaults) a partir de lo guardado. Tolera basura."""
    data = raw if isinstance(raw, dict) else {}
    base = defectos()
    base["presupuesto_tokens"] = _entero(
        data.get("presupuesto_tokens"), PRESUPUESTO_DEFECTO, PRESUPUESTO_MIN, PRESUPUESTO_MAX)
    base["historico_max"] = _entero(data.get("historico_max"), HISTORICO_MAX_DEFECTO, 0, 20)
    base["asuntos_max"] = _entero(data.get("asuntos_max"), ASUNTOS_MAX_DEFECTO, 0, 50)
    if "dominios_gratuitos" in data:
        base["dominios_gratuitos"] = _dominios(data["dominios_gratuitos"],
                                               DOMINIOS_GRATUITOS_DEFECTO)
    base["modelo_borrador"] = _modelo(data.get("modelo_borrador"))
    base["modelo_correccion"] = _modelo(data.get("modelo_correccion")) or MODELO_CORRECCION_DEFECTO
    if "modelos" in data:
        base["modelos"] = normalizar_lista(data["modelos"])
    base["pensamiento_extendido"] = bool(data.get("pensamiento_extendido", False))
    return base


def validar(payload: Any, actual: Any = None) -> dict[str, Any]:
    """Valida lo que llega del PATCH y lo funde con lo guardado."""
    if not isinstance(payload, dict):
        raise ValueError("La configuración del asistente no es válida.")
    base = normalizar(actual)
    if "presupuesto_tokens" in payload:
        valor = _entero(payload["presupuesto_tokens"], -1, PRESUPUESTO_MIN, PRESUPUESTO_MAX)
        if valor < 0:
            raise ValueError(
                f"Presupuesto de tokens no válido: entre {PRESUPUESTO_MIN} y {PRESUPUESTO_MAX}.")
        base["presupuesto_tokens"] = valor
    if "historico_max" in payload:
        valor = _entero(payload["historico_max"], -1, 0, 20)
        if valor < 0:
            raise ValueError("Correos viejos como mucho: entre 0 y 20.")
        base["historico_max"] = valor
    if "asuntos_max" in payload:
        valor = _entero(payload["asuntos_max"], -1, 0, 50)
        if valor < 0:
            raise ValueError("Asuntos recientes: entre 0 y 50.")
        base["asuntos_max"] = valor
    if "dominios_gratuitos" in payload:
        if not isinstance(payload["dominios_gratuitos"], list):
            raise ValueError("Los dominios gratuitos tienen que ser una lista.")
        base["dominios_gratuitos"] = _dominios(payload["dominios_gratuitos"], ())
    if "modelos" in payload:
        if not isinstance(payload["modelos"], list):
            raise ValueError("La lista de modelos tiene que ser una lista.")
        malos = [m for m in payload["modelos"] if str(m or "").strip() and not _modelo(m)]
        if malos:
            raise ValueError(f"Nombre de modelo no válido: {malos[0]!r}.")
        base["modelos"] = normalizar_lista(payload["modelos"])
    for clave in ("modelo_borrador", "modelo_correccion"):
        if clave in payload:
            crudo = str(payload[clave] or "").strip()
            if crudo and not _modelo(crudo):
                raise ValueError(f"Nombre de modelo no válido: {crudo!r}.")
            if clave == "modelo_correccion":
                base[clave] = _modelo(crudo) or MODELO_CORRECCION_DEFECTO
            else:
                base[clave] = _modelo(crudo)
    if "pensamiento_extendido" in payload:
        base["pensamiento_extendido"] = bool(payload["pensamiento_extendido"])
    return base


def configuracion(session: Session) -> dict[str, Any]:
    """Configuración efectiva (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
