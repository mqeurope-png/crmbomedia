"""Régimen de IVA del cliente FACTUSOL (Tarea C · Parte 2).

Mapeo de `F_CLI` **CONFIRMADO con volcados reales** (`--cli-row 3392 3011 525`
y `--cli-row 4279 3392`, 2026-09-12): nacional 3011 (ES), intracomunitarios
3392 (BE) y 4279 (DE, configurado a mano en el escritorio), exportación 525
(NO). Tres columnas acopladas:

| Régimen          | `IFICLI` (tipo documento) | `IVACLI` (aplicar IVA) | `TIVCLI` (tipo impos.) |
|------------------|---------------------------|------------------------|------------------------|
| nacional         | 0 = N.I.F.                | 0                      | 1 = 21 %               |
| intracomunitario | 2 = NIF/IVA intracomunit. | 2                      | 4 = Exento             |
| exportación      | (sin forzar, ver abajo)   | 3                      | 3 = 0 %                |

- `IFICLI` es el tipo de documento del identificador (NO `DOCCLI`, que vale 0
  hasta en el cliente bien configurado).
- Exportación: el único cliente de exportación volcado (525, Noruega) no
  estaba bien configurado (`IFICLI=0`), así que **no se fuerza `IFICLI`** en
  ese régimen hasta tener un volcado de referencia (decisión de Bart).
- Además `PAICLI` tiene que ser el ISO 3166-1 numérico REAL del país (el 525
  tenía el literal «Norway»).

Cómo se decide el régimen (país del CRM + NIF-IVA):
- España → nacional.
- País de la UE con NIF-IVA válido (`Company.vat` o un NIF con prefijo del
  país: `BE0812240188`, `DE455128445`) → intracomunitario; sin NIF-IVA →
  nacional (consumidor final: IVA español).
- Fuera de la UE → exportación.
- País desconocido / vacío → nacional (como hasta ahora), sin tocar el país.

Canarias, Ceuta y Melilla (IGIC/IPSI) quedan fuera: hoy no se distinguen de
la Península en el CRM.

Solo lógica pura: quién escribe en FACTUSOL es `customers.py`.
"""
from __future__ import annotations

import re
from typing import Any

from app.erp.language import country_numeric, normalize_country

#: Los 27 estados miembros (ISO2). Grecia usa el prefijo «EL» en el NIF-IVA.
EU_ISO2: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})
#: Prefijos de NIF-IVA intracomunitario → país. «EL» es Grecia; «XI» es
#: Irlanda del Norte (sigue en el régimen de IVA de la UE para bienes).
_VAT_PREFIX_TO_ISO2: dict[str, str] = {
    **{c: c for c in EU_ISO2}, "EL": "GR", "XI": "GB",
}
_VAT_RE = re.compile(r"^([A-Z]{2})([A-Z0-9]{2,12})$")

REGIME_NACIONAL = "nacional"
REGIME_INTRACOMUNITARIO = "intracomunitario"
REGIME_EXPORTACION = "exportacion"
REGIMES: tuple[str, ...] = (REGIME_NACIONAL, REGIME_INTRACOMUNITARIO, REGIME_EXPORTACION)
REGIME_LABELS: dict[str, str] = {
    REGIME_NACIONAL: "Nacional (con IVA)",
    REGIME_INTRACOMUNITARIO: "Intracomunitario (exento)",
    REGIME_EXPORTACION: "Exportación (0 %)",
}

#: Columnas de F_CLI que fija cada régimen (el mapeo confirmado de arriba).
FCLI_REGIME_COLUMNS: dict[str, dict[str, int]] = {
    REGIME_NACIONAL: {"IFICLI": 0, "IVACLI": 0, "TIVCLI": 1},
    REGIME_INTRACOMUNITARIO: {"IFICLI": 2, "IVACLI": 2, "TIVCLI": 4},
    REGIME_EXPORTACION: {"IVACLI": 3, "TIVCLI": 3},
}
#: Todas las columnas de régimen que BoHub puede escribir (para el guard).
FCLI_REGIME_COLUMN_NAMES: tuple[str, ...] = ("IFICLI", "IVACLI", "TIVCLI")
FCLI_COLUMN_LABELS: dict[str, str] = {
    "IFICLI": "Tipo de documento",
    "IVACLI": "Aplicar IVA",
    "TIVCLI": "Tipo impositivo",
    "PAICLI": "País",
}
IFICLI_LABELS: dict[int, str] = {0: "N.I.F.", 2: "NIF/IVA operador intracomunitario"}
IVACLI_LABELS: dict[int, str] = {0: "Sí (nacional)", 2: "Intracomunitario", 3: "Exportación"}
TIVCLI_LABELS: dict[int, str] = {1: "21 %", 3: "0 %", 4: "Exento"}
_VALUE_LABELS: dict[str, dict[int, str]] = {
    "IFICLI": IFICLI_LABELS, "IVACLI": IVACLI_LABELS, "TIVCLI": TIVCLI_LABELS,
}


def normalize_vat(value: Any) -> str | None:
    """NIF-IVA intracomunitario normalizado (`be 0812.240.188` → `BE0812240188`)
    o None si no tiene la forma «prefijo UE + 2-12 alfanuméricos»."""
    raw = re.sub(r"[\s.\-]", "", str(value or "")).upper()
    m = _VAT_RE.match(raw)
    if not m or m.group(1) not in _VAT_PREFIX_TO_ISO2:
        return None
    return raw


def eu_vat_for(country_iso2: str | None, *, vat: Any = None, nif: Any = None) -> str | None:
    """El NIF-IVA intracomunitario del cliente, si lo tiene: `Company.vat`
    primero (hoy nadie lo leía) y, si no, el NIF con prefijo del país
    (`DE455128445`). Un prefijo de OTRO país que el del cliente no cuenta."""
    country = (country_iso2 or "").upper() or None
    for candidate in (vat, nif):
        number = normalize_vat(candidate)
        if number is None:
            continue
        prefix_country = _VAT_PREFIX_TO_ISO2[number[:2]]
        if country is None or prefix_country == country:
            return number
    return None


def is_eu(country_iso2: str | None) -> bool:
    return (country_iso2 or "").upper() in EU_ISO2


def regime_for(country_iso2: str | None, *, vat: Any = None, nif: Any = None) -> str:
    """Régimen por país (ISO2) + NIF-IVA. Ver la cabecera del módulo."""
    country = normalize_country(country_iso2) if country_iso2 else None
    if country is None or country == "ES":
        return REGIME_NACIONAL
    if country in EU_ISO2:
        if eu_vat_for(country, vat=vat, nif=nif):
            return REGIME_INTRACOMUNITARIO
        return REGIME_NACIONAL
    return REGIME_EXPORTACION


def regime_reason(country_iso2: str | None, *, vat: Any = None, nif: Any = None) -> str:
    """Frase para el operador: por qué sale ese régimen."""
    country = normalize_country(country_iso2) if country_iso2 else None
    if country is None:
        return "sin país en el CRM → nacional (por defecto)"
    if country == "ES":
        return "España → nacional"
    if country in EU_ISO2:
        number = eu_vat_for(country, vat=vat, nif=nif)
        if number:
            return f"{country} (UE) con NIF-IVA {number} → intracomunitario"
        return f"{country} (UE) sin NIF-IVA → nacional (IVA español)"
    return f"{country} (fuera de la UE) → exportación"


def regime_columns(regime: str) -> dict[str, int]:
    """Columnas de F_CLI que fija el régimen (copia)."""
    if regime not in FCLI_REGIME_COLUMNS:
        raise ValueError(f"régimen desconocido: {regime!r}")
    return dict(FCLI_REGIME_COLUMNS[regime])


def iva_pct_for(regime: str | None, default_pct: float) -> float:
    """% de IVA que BoHub aplica en los documentos que CALCULA (proformas,
    albarán manual): el de las líneas en nacional, 0 en intracomunitario y
    exportación."""
    if regime in (REGIME_INTRACOMUNITARIO, REGIME_EXPORTACION):
        return 0.0
    return default_pct


def _int(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def regime_from_fcli_row(row: dict[str, Any] | None) -> str | None:
    """Régimen que codifica una fila REAL de F_CLI (`IVACLI` manda: 0/2/3), o
    None si la combinación no es ninguna de las confirmadas."""
    if not row:
        return None
    ivacli = _int(row.get("IVACLI"))
    for regime, cols in FCLI_REGIME_COLUMNS.items():
        if ivacli == cols["IVACLI"]:
            return regime
    return None


def value_label(column: str, value: Any) -> str:
    """`IVACLI=2` → «2 · Intracomunitario»; lo que no se conoce, tal cual."""
    number = _int(value)
    label = _VALUE_LABELS.get(column, {}).get(number) if number is not None else None
    text = "" if value is None else str(value)
    return f"{text} · {label}" if label else text


def paicli_for(country_iso2: str | None) -> str | None:
    """`PAICLI` (ISO numérico de 3 cifras) del país del CRM, o None si no se
    reconoce — nunca España por defecto."""
    return country_numeric(country_iso2) if country_iso2 else None


def proposed_fcli_values(
    country_iso2: str | None, *, vat: Any = None, nif: Any = None,
) -> tuple[str, dict[str, Any]]:
    """`(régimen, {columna: valor})` que BoHub quiere en la ficha F_CLI del
    cliente: las columnas del régimen y, si el país se reconoce, `PAICLI`."""
    regime = regime_for(country_iso2, vat=vat, nif=nif)
    values: dict[str, Any] = regime_columns(regime)
    paicli = paicli_for(country_iso2)
    if paicli is not None:
        values["PAICLI"] = paicli
    return regime, values


def fcli_changes(row: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    """Qué columnas de la fila REAL difieren de lo propuesto (para el preview
    y para escribir SOLO lo que cambia). `PAICLI` compara como texto sin ceros
    a la izquierda («56» == «056»)."""
    out: list[dict[str, Any]] = []
    for column, wanted in proposed.items():
        current = row.get(column)
        if column == "PAICLI":
            same = str(current or "").strip().lstrip("0") == str(wanted).lstrip("0") \
                and str(current or "").strip() != ""
        else:
            same = _int(current) == _int(wanted)
        if same:
            continue
        out.append({
            "column": column, "label": FCLI_COLUMN_LABELS.get(column, column),
            "current": current, "current_label": value_label(column, current),
            "proposed": wanted, "proposed_label": value_label(column, wanted),
        })
    return out
