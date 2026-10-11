"""Configuración de la base de conocimiento (Configuración ERP → «Base de
conocimiento»). Vive en el blob `factusol_series_json` de `ErpSettings`, bajo
la clave `conocimiento` (sin migración):

    {"flux_sync_activa": true, "flux_sync_hora": "04:30",
     "flux_palabras_kb": [...], "flux_palabras_soporte": [...]}

- `flux_sync_activa` / `flux_sync_hora`: la sincronización diaria del índice
  de la KB de FLUX (worker-sync). Encendida por defecto: es solo lectura.
- `flux_palabras_kb`: señales de «cómo se hace / software / calibración /
  materiales» → se enlaza la KB de FLUX en su idioma y modelo.
- `flux_palabras_soporte`: señales de «averiada / repuesto / garantía / que
  vaya alguien» → se enlaza nuestro soporte. Si no está claro, el nuestro.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

CONFIG_KEY = "conocimiento"
HORA_DEFECTO = "04:30"
_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

PALABRAS_KB_DEFECTO: tuple[str, ...] = (
    "cómo", "como se", "how to", "how do", "wie kann", "wie man", "comment", "hoe",
    "software", "beam studio", "firmware", "calibr", "kalibr", "enfoque", "focus", "fokus",
    "cámara", "camera", "kamera", "material", "ajuste", "ajustar", "setting", "einstell",
    "paramèt", "instelling", "configur", "wifi", "conectar", "connect", "verbind", "driver",
    "import", "svg", "dxf", "potencia", "power", "leistung", "puissance", "velocidad",
    "speed", "geschwindigkeit", "vitesse",
)
PALABRAS_SOPORTE_DEFECTO: tuple[str, ...] = (
    "averi", "no funciona", "no enciende", "no arranca", "roto", "rota", "repuesto",
    "recambio", "garantía", "garantia", "warranty", "garantie", "defekt", "kaputt",
    "ersatzteil", "panne", "pièce", "cassé", "defect", "onderdeel", "técnico", "tecnico",
    "technician", "techniker", "technicien", "reparar", "repair", "reparatur", "réparer",
    "repareren", "visita", "que venga", "humo", "smoke", "rauch", "fumée", "código de error",
    "error code", "fehlercode", "code d'erreur", "chispa", "spark", "quemado", "burnt",
)


def _hora(raw: Any) -> str | None:
    m = _HORA_RE.match(str(raw or "").strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _palabras(raw: Any, defecto: Iterable[str]) -> list[str]:
    if not isinstance(raw, list):
        return list(defecto)
    salida: list[str] = []
    for p in raw:
        texto = re.sub(r"\s+", " ", str(p or "").strip().lower())
        if texto and texto not in salida:
            salida.append(texto)
    return salida


def defectos() -> dict[str, Any]:
    return {
        "flux_sync_activa": True,
        "flux_sync_hora": HORA_DEFECTO,
        "flux_palabras_kb": list(PALABRAS_KB_DEFECTO),
        "flux_palabras_soporte": list(PALABRAS_SOPORTE_DEFECTO),
    }


def normalizar(raw: Any) -> dict[str, Any]:
    """Config completa (con defaults) a partir de lo guardado. Tolera basura."""
    data = raw if isinstance(raw, dict) else {}
    base = defectos()
    if "flux_sync_activa" in data:
        base["flux_sync_activa"] = bool(data["flux_sync_activa"])
    base["flux_sync_hora"] = _hora(data.get("flux_sync_hora")) or HORA_DEFECTO
    if "flux_palabras_kb" in data:
        base["flux_palabras_kb"] = _palabras(data["flux_palabras_kb"], PALABRAS_KB_DEFECTO)
    if "flux_palabras_soporte" in data:
        base["flux_palabras_soporte"] = _palabras(
            data["flux_palabras_soporte"], PALABRAS_SOPORTE_DEFECTO)
    return base


def validar(payload: Any, actual: Any = None) -> dict[str, Any]:
    """Valida lo que llega del PATCH y lo funde con lo guardado. `ValueError`
    legible si algo no vale."""
    if not isinstance(payload, dict):
        raise ValueError("La configuración de la base de conocimiento no es válida.")
    base = normalizar(actual)
    if "flux_sync_activa" in payload:
        if not isinstance(payload["flux_sync_activa"], bool):
            raise ValueError("«Sincronizar la KB de FLUX cada día» tiene que ser sí o no.")
        base["flux_sync_activa"] = payload["flux_sync_activa"]
    if "flux_sync_hora" in payload:
        hora = _hora(payload["flux_sync_hora"])
        if hora is None:
            raise ValueError(
                f"Hora de la sincronización no válida: {payload['flux_sync_hora']!r} "
                "(formato HH:MM).")
        base["flux_sync_hora"] = hora
    for clave in ("flux_palabras_kb", "flux_palabras_soporte"):
        if clave in payload:
            if not isinstance(payload[clave], list):
                raise ValueError("Las palabras tienen que ser una lista.")
            base[clave] = _palabras(payload[clave], ())
    return base


def configuracion(session: Session) -> dict[str, Any]:
    """Configuración efectiva (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
