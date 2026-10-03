"""Configuración del Cuadre (Configuración ERP → «Cuadre»).

Vive en el blob `factusol_series_json` de `ErpSettings`, bajo la clave
`cuadre` (sin migración):

    {"nocturno_activo": false, "hora": "03:00",
     "checks": {"factura_sin_cobro": {"activo": true, "dias": 30}, ...}}

- `nocturno_activo`: el job nocturno de worker-sync. APAGADO por defecto: tras
  desplegar se pulsa «Comprobar ahora», se revisa el primer lote con Bart y
  luego se enciende. Con el interruptor apagado el job sigue armado (no hace
  nada), así que encenderlo surte efecto esa misma noche.
- `hora`: HH:MM (hora de Madrid) del job nocturno; 03:00 por defecto.
- `checks`: por comprobación, si está activa (todas por defecto) y su umbral
  en días (el de la comprobación por defecto).
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

CONFIG_KEY = "cuadre"
HORA_DEFECTO = "03:00"
DIAS_MIN = 1
DIAS_MAX = 3650
_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _hora(raw: Any) -> str | None:
    m = _HORA_RE.match(str(raw or "").strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _dias(raw: Any) -> int | None:
    try:
        valor = int(raw)
    except (TypeError, ValueError):
        return None
    return valor if DIAS_MIN <= valor <= DIAS_MAX else None


def normalizar_config(raw: Any) -> dict[str, Any]:
    """Config completa (con los defaults de cada comprobación registrada) a
    partir de lo guardado. Tolera basura: lo que no se entiende, por defecto."""
    from app.erp.cuadre.registry import registro  # noqa: PLC0415

    data = raw if isinstance(raw, dict) else {}
    guardados = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    checks: dict[str, dict[str, Any]] = {}
    for comp in registro().values():
        entry = guardados.get(comp.id) if isinstance(guardados.get(comp.id), dict) else {}
        dias = None
        if comp.dias_defecto is not None:
            dias = _dias(entry.get("dias")) or comp.dias_defecto
        checks[comp.id] = {"activo": bool(entry.get("activo", True)), "dias": dias}
    return {
        "nocturno_activo": bool(data.get("nocturno_activo", False)),
        "hora": _hora(data.get("hora")) or HORA_DEFECTO,
        "checks": checks,
    }


def validar_config(payload: Any, actual: Any = None) -> dict[str, Any]:
    """Valida lo que llega del PATCH de Configuración ERP y lo FUNDE con lo
    guardado (`actual`): lo que no viene se conserva. Devuelve la config que se
    guarda. `ValueError` con un mensaje legible si algo no vale."""
    from app.erp.cuadre.registry import registro  # noqa: PLC0415

    if not isinstance(payload, dict):
        raise ValueError("La configuración del Cuadre no es válida.")
    base = normalizar_config(actual)
    if "nocturno_activo" in payload:
        if not isinstance(payload["nocturno_activo"], bool):
            raise ValueError("«Comprobar todo cada noche» tiene que ser sí o no.")
        base["nocturno_activo"] = payload["nocturno_activo"]
    if "hora" in payload:
        hora = _hora(payload["hora"])
        if hora is None:
            raise ValueError(
                f"Hora del Cuadre no válida: {payload['hora']!r} (formato HH:MM).")
        base["hora"] = hora
    checks_in = payload.get("checks") or {}
    if not isinstance(checks_in, dict):
        raise ValueError("La lista de comprobaciones del Cuadre no es válida.")
    reg = registro()
    for check_id, entry in checks_in.items():
        if check_id not in reg:
            raise ValueError(f"Comprobación del Cuadre desconocida: {check_id!r}.")
        if not isinstance(entry, dict):
            raise ValueError(f"Configuración no válida para {check_id!r}.")
        destino = base["checks"][check_id]
        if "activo" in entry:
            if not isinstance(entry["activo"], bool):
                raise ValueError(f"«Activa» de «{reg[check_id].titulo}» tiene que ser sí o no.")
            destino["activo"] = entry["activo"]
        if "dias" in entry and reg[check_id].dias_defecto is not None:
            if entry["dias"] is None:
                destino["dias"] = reg[check_id].dias_defecto    # vacío = el de serie
            elif _dias(entry["dias"]) is None:
                raise ValueError(
                    f"Días no válidos para «{reg[check_id].titulo}»: "
                    f"entre {DIAS_MIN} y {DIAS_MAX}."
                )
            else:
                destino["dias"] = _dias(entry["dias"])
    return base


def cuadre_config(session: Session) -> dict[str, Any]:
    """Configuración efectiva del Cuadre (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar_config(series_config(session).get(CONFIG_KEY))


def check_activo(config: dict[str, Any], check_id: str) -> bool:
    return bool((config.get("checks") or {}).get(check_id, {}).get("activo", True))


def check_dias(config: dict[str, Any], check_id: str) -> int | None:
    return (config.get("checks") or {}).get(check_id, {}).get("dias")
