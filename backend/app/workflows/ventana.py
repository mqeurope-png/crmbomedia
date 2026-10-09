"""Ventana horaria de los pasos de espera.

Un `wait_time` de 12 horas que vence a las tres de la mañana no debe disparar
a las tres de la mañana: con una ventana («de 9 a 18, laborables») el
despertar se mueve al siguiente hueco. La hora es la de Madrid; el motor
sigue guardando `wake_at` en UTC.

Config del paso: `{"window": {"enabled": true, "start": "09:00",
"end": "18:00", "weekdays_only": true}}`. Sin `window` (o apagada) el paso
se comporta como siempre.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ZONA = "Europe/Madrid"
_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class VentanaHoraria:
    inicio: time
    fin: time
    laborables: bool = True

    def como_dict(self) -> dict[str, Any]:
        return {
            "enabled": True, "start": self.inicio.strftime("%H:%M"),
            "end": self.fin.strftime("%H:%M"), "weekdays_only": self.laborables,
        }


def _hora(raw: Any) -> time | None:
    m = _HORA_RE.match(str(raw or "").strip())
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def ventana_desde_config(cfg: dict[str, Any] | None) -> VentanaHoraria | None:
    """La ventana del config del paso, o `None` si no hay (o no vale)."""
    raw = (cfg or {}).get("window")
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None
    inicio = _hora(raw.get("start") or "09:00")
    fin = _hora(raw.get("end") or "18:00")
    if inicio is None or fin is None or fin <= inicio:
        return None
    return VentanaHoraria(inicio=inicio, fin=fin, laborables=bool(raw.get("weekdays_only", True)))


def ajustar_a_ventana(
    momento: datetime, ventana: VentanaHoraria | None, zona: str = ZONA,
) -> datetime:
    """El mismo instante si cae dentro de la ventana; si no, el siguiente
    inicio de ventana (hoy, mañana o el lunes). Devuelve UTC."""
    if ventana is None:
        return momento
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    tz = ZoneInfo(zona)
    local = momento.astimezone(tz)
    for _ in range(10):      # como mucho una semana de fiestas por delante
        es_laborable = local.weekday() < 5 or not ventana.laborables
        if es_laborable and ventana.inicio <= local.time() < ventana.fin:
            return local.astimezone(UTC)
        if es_laborable and local.time() < ventana.inicio:
            local = local.replace(
                hour=ventana.inicio.hour, minute=ventana.inicio.minute, second=0, microsecond=0,
            )
            return local.astimezone(UTC)
        # Fuera de hora (o fin de semana): el inicio del día siguiente.
        local = (local + timedelta(days=1)).replace(
            hour=ventana.inicio.hour, minute=ventana.inicio.minute, second=0, microsecond=0,
        )
    return local.astimezone(UTC)
