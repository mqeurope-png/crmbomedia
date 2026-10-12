"""Los modelos de Claude que puede usar el asistente y cómo se les habla.

Dos ajustes separados en Configuración ERP (`asistente.modelo_borrador` y
`asistente.modelo_correccion`): el primer borrador lleva el contexto entero y
quiere el modelo grande; una corrección («más corto», «en inglés», «quita el
precio») trabaja sobre un texto ya escrito y le basta Haiku. La lista de
modelos que se ofrece también sale de configuración, para probar uno nuevo
sin desplegar.

El pensamiento extendido se factura como salida (lo caro) y redactar un correo
con todo el contexto ya montado delante no lo necesita: se apaga donde el
modelo lo permite y, donde no se puede apagar (Opus 5.5, Fable), se baja el
esfuerzo. Cada familia tiene su manera de decirlo (octubre de 2026) y una
familia que no se conozca se queda con el defecto del modelo, que es lo que
no rompe. Lo que sale de aquí viaja como `extra_body` de la petición.
"""
from __future__ import annotations

import copy
import re
from typing import Any

MODELO_CORRECCION_DEFECTO = "claude-haiku-5-5"
#: La lista de partida del desplegable; se edita en Configuración ERP.
MODELOS_DE_PARTIDA: tuple[str, ...] = (
    "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-4-6",
    "claude-haiku-4-5",
)
_NOMBRE_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,79}$")

_APAGADO = {"thinking": {"type": "disabled"}}
_ESFUERZO_BAJO = {"output_config": {"effort": "low"}}
#: Cómo se apaga el pensamiento, por prefijo de modelo; manda el prefijo más
#: largo que case (`claude-opus-5-5` antes que `claude-opus-5`).
_SIN_PENSAMIENTO: tuple[tuple[str, dict[str, Any]], ...] = (
    # No se puede apagar: `disabled` devuelve 400. Esfuerzo bajo.
    ("claude-opus-5-5", _ESFUERZO_BAJO),
    ("claude-fable", _ESFUERZO_BAJO),
    ("claude-mythos", _ESFUERZO_BAJO),
    # Sonnet 5.5: `disabled` da 400; `between_tools` lo deja sin pensar (aquí
    # no hay herramientas).
    ("claude-sonnet-5-5", {"thinking": {"type": "between_tools"}}),
    ("claude-haiku-5-5", _APAGADO),
    ("claude-opus-5", _APAGADO),
    ("claude-opus-4-8", _APAGADO),
    ("claude-opus-4-7", _APAGADO),
    ("claude-opus-4-6", _APAGADO),
    ("claude-sonnet-5", _APAGADO),
    ("claude-sonnet-4-6", _APAGADO),
    # Haiku 4.5, Sonnet 4.5 y anteriores no piensan si no se les pide: nada que apagar.
)


def es_nombre_valido(modelo: Any) -> bool:
    return isinstance(modelo, str) and bool(_NOMBRE_RE.match(modelo.strip()))


def normalizar_lista(raw: Any, defecto: tuple[str, ...] = MODELOS_DE_PARTIDA) -> list[str]:
    """La lista de modelos tal como se guarda: nombres válidos, sin repetir."""
    if not isinstance(raw, list):
        return list(defecto)
    salida: list[str] = []
    for m in raw:
        nombre = str(m or "").strip().lower()
        if es_nombre_valido(nombre) and nombre not in salida:
            salida.append(nombre)
    return salida or list(defecto)


#: Lo que el pensamiento puede gastar encima de la respuesta. Lo pensado cuenta
#: dentro de `max_tokens`: sin este margen, un correo compuesto con el
#: pensamiento encendido se quedaba sin sitio para escribir y la API devolvía
#: solo el bloque de pensamiento (12/10/2026: «Provider returned non-text
#: content (blocks: thinking)»). Es un tope: se cobra lo que se piensa de verdad.
MARGEN_PENSAMIENTO = 8000
#: Con el esfuerzo bajo (donde el pensamiento no se puede apagar: Opus 5.5,
#: Fable) se piensa poco: menos margen, menos gasto en el peor caso.
MARGEN_PENSAMIENTO_BAJO = 3000
#: Las familias que no piensan si no se les pide: sin margen (alguna tiene un
#: tope de salida pequeño, y pasarse da 400). Una familia que no está aquí ni
#: lleva el pensamiento apagado cuenta como que piensa (las nuevas lo hacen).
_NO_PIENSAN_SOLAS: tuple[str, ...] = (
    "claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5", "claude-opus-4-1",
    "claude-opus-4-2", "claude-sonnet-4-2", "claude-3",
)


def parametros_de_llamada(modelo: str | None, *, pensamiento_extendido: bool) -> dict[str, Any]:
    """Lo que se añade a la petición para ese modelo y esa elección. Con el
    pensamiento extendido encendido no se añade nada: manda el defecto del
    modelo (adaptativo en los actuales, apagado en los antiguos)."""
    if pensamiento_extendido:
        return {}
    nombre = (modelo or "").strip().lower()
    for prefijo, parametros in sorted(_SIN_PENSAMIENTO, key=lambda p: -len(p[0])):
        if nombre.startswith(prefijo):
            return copy.deepcopy(parametros)
    return {}


def tope_de_salida(modelo: str | None, parametros: dict[str, Any] | None,
                   respuesta: int) -> int:
    """El `max_tokens` de una llamada: lo que ocupa la respuesta y, si el
    modelo va a pensar con los `parametros` que se le mandan, sitio para pensar
    antes de escribirla. Sin margen si el pensamiento va apagado (`disabled`, o
    `between_tools` sin herramientas) o si la familia no piensa sola; margen
    pequeño con el esfuerzo bajo; el entero en el resto (el defecto de los
    modelos actuales es pensar)."""
    p = parametros or {}
    tipo = (p.get("thinking") or {}).get("type")
    if tipo in ("disabled", "between_tools"):
        return respuesta
    if (p.get("output_config") or {}).get("effort") == "low":
        return respuesta + MARGEN_PENSAMIENTO_BAJO
    if tipo is None and (modelo or "").strip().lower().startswith(_NO_PIENSAN_SOLAS):
        return respuesta
    return respuesta + MARGEN_PENSAMIENTO
