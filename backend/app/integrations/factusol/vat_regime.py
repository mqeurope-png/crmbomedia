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

Cómo se decide el régimen: **por la PAREJA** país del emisor → país del
cliente. No depende solo del cliente, y antes sí: `"ES"` estaba escrito a
fuego como país de quien vende, y BoHub emite desde dos empresas en países
distintos (Streamtec, S.L. en España y MQ EUROPE BV en Bélgica).

- emisor == cliente → **nacional**, con el IVA de ese país.
- los dos en la UE y distintos, con NIF-IVA válido del cliente →
  **intracomunitario** (exento).
- los dos en la UE y distintos, sin NIF-IVA válido → **nacional** del país
  del EMISOR (consumidor final).
- cliente fuera de la UE → **exportación**.
- país del cliente desconocido / vacío → nacional (como hasta ahora), sin
  tocar el país.

Lo que esto arreglaba, visto en producción el 08/10: CDCOPIADVD S.L.U
(ESB65623175, Barcelona), NIF-IVA válido en VIES, documento de la serie 2 (MQ
Europe). El modal decía «(España → nacional)» y la proforma salió con 21 %;
debía salir exenta. Y al revés es peor: facturar SIN IVA a un cliente belga de
MQ Europe deja a la empresa debiendo ese IVA.

El NIF-IVA y su validez en VIES son del CLIENTE y no dependen de quién
factura: lo que depende de la pareja es si ese NIF-IVA sirve para eximir.

Canarias, Ceuta y Melilla (IGIC/IPSI) quedan fuera: hoy no se distinguen de
la Península en el CRM.

Solo lógica pura: quién escribe en FACTUSOL es `customers.py`.
"""
from __future__ import annotations

import re
from typing import Any

from app.erp.language import (
    country_display_name,
    country_numeric,
    normalize_country,
)

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

#: País del emisor cuando el llamador no lo sabe. Es el de Streamtec, que es
#: la serie por defecto; se deja explícito para que la suposición esté en UN
#: sitio y se vea, en vez de repartida por el código como estaba.
DEFAULT_ISSUER_ISO2 = "ES"

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


def country_display(iso2: str | None) -> str:
    """«BE» → «Bélgica». Para el motivo que lee el operador."""
    return country_display_name(iso2) or str(iso2 or "")


def _emisor(issuer_iso2: str | None) -> str:
    """País del emisor, normalizado. Sin dato, el de la serie por defecto."""
    return normalize_country(issuer_iso2) or DEFAULT_ISSUER_ISO2 \
        if issuer_iso2 else DEFAULT_ISSUER_ISO2


def regime_for(
    country_iso2: str | None, *, issuer_iso2: str | None = None,
    vat: Any = None, nif: Any = None, vies_valid: bool | None = None,
) -> str:
    """Régimen de la PAREJA emisor → cliente. Ver la cabecera del módulo.

    `issuer_iso2` es el país de la empresa que emite (de su serie). Sin él se
    supone `DEFAULT_ISSUER_ISO2`, que es lo que hacía siempre el código viejo.

    Fase VIES: `vies_valid=False` (VIES dice que el NIF-IVA NO es válido)
    impide eximir → nacional con IVA aunque los dos países sean de la UE y
    haya NIF-IVA. `True` lo confirma; `None` (pendiente / VIES caído) no
    cambia la regla: se sigue por países + NIF-IVA sin bloquear."""
    cliente = normalize_country(country_iso2) if country_iso2 else None
    emisor = _emisor(issuer_iso2)
    if cliente is None:
        return REGIME_NACIONAL
    if cliente == emisor:
        return REGIME_NACIONAL
    if cliente not in EU_ISO2:
        return REGIME_EXPORTACION
    if emisor not in EU_ISO2:
        # Emisor fuera de la UE vendiendo a la UE: desde su punto de vista es
        # una exportación. Hoy las dos empresas son de la UE, así que esta
        # rama no se da; está para no decidir mal en silencio si algún día
        # entra una tercera.
        return REGIME_EXPORTACION
    if eu_vat_for(cliente, vat=vat, nif=nif) and vies_valid is not False:
        return REGIME_INTRACOMUNITARIO
    # Consumidor final de otro país de la UE: IVA del país del EMISOR.
    return REGIME_NACIONAL


def regime_reason(
    country_iso2: str | None, *, issuer_iso2: str | None = None,
    vat: Any = None, nif: Any = None, vies_valid: bool | None = None,
) -> str:
    """Frase para el operador: por qué sale ese régimen, **nombrando las dos
    puntas**. Antes decía «España → nacional» aunque emitiera una empresa
    belga, que es justo lo que escondía el fallo."""
    cliente = normalize_country(country_iso2) if country_iso2 else None
    emisor = _emisor(issuer_iso2)
    de = country_display(emisor)
    if cliente is None:
        return f"{de} → sin país en el CRM → nacional (por defecto)"
    a = country_display(cliente)
    if cliente == emisor:
        return f"{de} → {a} (el mismo país) → nacional"
    if cliente not in EU_ISO2:
        return f"{de} → {a} (fuera de la UE) → exportación"
    if emisor not in EU_ISO2:
        return f"{de} (fuera de la UE) → {a} → exportación"
    number = eu_vat_for(cliente, vat=vat, nif=nif)
    if number and vies_valid is False:
        return (f"{de} → {a} (UE) con NIF-IVA {number} NO válido en VIES → "
                "nacional (no se puede eximir)")
    if number and vies_valid is True:
        return (f"{de} → {a} (UE) con NIF-IVA {number} verificado en VIES → "
                "intracomunitario")
    if number:
        return f"{de} → {a} (UE) con NIF-IVA {number} → intracomunitario"
    return (f"{de} → {a} (UE) sin NIF-IVA → nacional "
            f"(IVA de {de})")


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


def regimes_por_emisor(
    emisores: Any, country_iso2: str | None, *, vat: Any = None,
    nif: Any = None, vies_valid: bool | None = None,
) -> dict[str, str]:
    """`{país del emisor: régimen}` para cada empresa que puede facturar.

    Es lo que hace ver que un mismo cliente no tiene UN régimen: CDCOPIADVD
    (ES) es nacional para Streamtec y intracomunitario para MQ Europe."""
    return {
        emisor: regime_for(country_iso2, issuer_iso2=emisor, vat=vat, nif=nif,
                           vies_valid=vies_valid)
        for emisor in dict.fromkeys(
            normalize_country(e) or str(e or "").upper() for e in (emisores or ())
            if str(e or "").strip()
        )
    }


def regime_unico(
    emisores: Any, country_iso2: str | None, *, vat: Any = None,
    nif: Any = None, vies_valid: bool | None = None,
) -> str | None:
    """El régimen si TODAS las empresas emisoras coinciden; `None` si no.

    La ficha F_CLI es una sola (hay una única base de datos de FACTUSOL) y
    solo puede guardar un régimen, así que cuando las empresas discrepan no
    hay nada que escribir que no sea mentira para alguna de ellas."""
    por_emisor = regimes_por_emisor(
        emisores, country_iso2, vat=vat, nif=nif, vies_valid=vies_valid,
    )
    distintos = set(por_emisor.values())
    return distintos.pop() if len(distintos) == 1 else None


def proposed_fcli_values(
    country_iso2: str | None, *, issuer_iso2: str | None = None,
    vat: Any = None, nif: Any = None, vies_valid: bool | None = None,
) -> tuple[str, dict[str, Any]]:
    """`(régimen, {columna: valor})` que BoHub quiere en la ficha F_CLI del
    cliente: las columnas del régimen y, si el país se reconoce, `PAICLI`."""
    regime = regime_for(country_iso2, issuer_iso2=issuer_iso2, vat=vat, nif=nif,
                        vies_valid=vies_valid)
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
