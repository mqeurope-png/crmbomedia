"""PR-Import-Agile-Completo.

Reglas de tratamiento de custom fields específicos de las cuentas
Agile reales (artisjetspain, boprint24, mboprinters, boprint…) que
hoy se descartaban silenciosamente porque no estaban en
`brevo.mapper.CUSTOM_FIELDS_WHITELIST`.

Decisiones (auditoría detallada en PR description):

  - `Horario`: campo de texto unificado entre cuentas. Whitelist
    already-incluye `HORARIO` y el matching es case-insensitive
    via `.upper()`, así que llega al mismo key en el JSON. Aquí
    solo lo confirmamos como semánticamente whitelist'eado.
  - `CONTACTO Persona`: campo de texto (nombre de una persona de
    contacto). Lo whitelist'eamos para que sobreviva al filtro.
  - `Productos`, `Producto`, `etiquetas`, `interests`: contenido
    semánticamente equivalente a tags del CRM. Convertimos cada
    valor en N tags split por coma / pipe / saltos de línea. NO
    los persistimos como custom fields.

Este módulo es la lógica COMPARTIDA entre el sync futuro (mapper)
y la migración de backfill que tienen que aplicar la misma
transformación a contactos ya importados.
"""
from __future__ import annotations

import re
from typing import Any

# Aliases (case-insensitive) que el mapper debe interceptar y
# transformar a tags. La clave es la forma canónica para reportar
# en logs; los aliases son las variantes vistas en CSVs reales.
_TAGLIKE_FIELDS: dict[str, tuple[str, ...]] = {
    "productos": ("productos", "producto"),
    "etiquetas": ("etiquetas",),
    "interests": ("interests", "interest"),
}

# Aliases case-insensitive para campos que se mantienen como custom
# field pero no estaban en el whitelist Brevo. Incluyen el
# acomodamiento del whitespace y la normalización de la key
# canónica con la que se guardan en el JSON.
_KEEP_AS_CUSTOM_FIELDS: dict[str, str] = {
    # Lower-case key del input → key canónica en el JSON.
    "horario": "Horario",
    "contacto persona": "CONTACTO Persona",
    "contacto_persona": "CONTACTO Persona",
}


# Separadores comunes en exports de Agile: coma, punto y coma,
# barra vertical, salto de línea. Bart confirmó "split por coma /
# pipe / lo que aplique" en el spec.
_SPLIT_REGEX = re.compile(r"[,;|\n\r]+")


def is_taglike_custom_field(name: str) -> bool:
    """`Productos`, `Producto`, `etiquetas`, `interests` (en cualquier
    capitalización) se convierten a tags. El mapper los intercepta
    antes del whitelist."""
    return name.strip().lower() in {
        alias
        for aliases in _TAGLIKE_FIELDS.values()
        for alias in aliases
    }


def split_taglike_value(value: Any) -> list[str]:
    """Convierte el valor de un custom field tag-like en una lista de
    tag names. Acepta strings con separadores múltiples, listas de
    strings o lo que la API devuelva.

    Cada token se trimea; tokens vacíos / 'null' / 'none' se filtran.
    Se preserva el orden original (no se normaliza la capitalización
    aquí — el upserter de tags es case-insensitive)."""
    if value is None:
        return []
    if isinstance(value, list):
        candidates: list[Any] = value
    elif isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value
        candidates = _SPLIT_REGEX.split(text)
    else:
        candidates = [value]
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, (str, int, float)):
            continue
        token = str(raw).strip().strip("\"'")
        if not token:
            continue
        if token.lower() in {"null", "none", "n/a", "-"}:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def canonical_keep_key(name: str) -> str | None:
    """Si `name` corresponde a un custom field que se preserva pero
    con key canónica unificada, devuelve la key. Si no, None."""
    return _KEEP_AS_CUSTOM_FIELDS.get(name.strip().lower())


# Nombres adicionales que añadimos al whitelist efectivo para el
# matching `.upper()` que hace el filtro Brevo. La integración los
# acepta sin tocar el código del módulo Brevo.
AGILE_EXTRA_WHITELIST_UPPER: frozenset[str] = frozenset(
    {
        # Horario ya está en el whitelist Brevo, pero documentamos
        # aquí que también lo queremos por la ruta Agile.
        "HORARIO",
        "CONTACTO PERSONA",
        "CONTACTO_PERSONA",
    }
)


def is_kept_custom_field(name: str) -> bool:
    """¿Este nombre está en la whitelist Brevo o en la nuestra de
    Agile? Si sí, sobrevive al filtro y va al JSON. Se compara en
    upper-case igual que el mapper Brevo."""
    from app.integrations.brevo.mapper import (  # noqa: PLC0415
        CUSTOM_FIELDS_WHITELIST,
    )

    upper = name.strip().upper()
    return upper in CUSTOM_FIELDS_WHITELIST or upper in AGILE_EXTRA_WHITELIST_UPPER
