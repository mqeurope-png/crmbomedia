"""Configuración de la base de conocimiento (Configuración ERP → «Base de
conocimiento»). Vive en el blob `factusol_series_json` de `ErpSettings`, bajo
la clave `conocimiento` (sin migración):

    {"flux_sync_activa": true, "flux_sync_hora": "04:30",
     "flux_palabras_kb": [...], "flux_palabras_soporte": [...],
     "catalogo_sync_activa": true, "catalogo_sync_hora": "04:45",
     "precio_meses_orientativo": 6,
     "iva_por_fuente": {"compositor": null, "woocommerce": null},
     "interes_productos_revisado": false}

- `flux_sync_activa` / `flux_sync_hora`: la sincronización diaria del índice
  de la KB de FLUX (worker-sync). Encendida por defecto: es solo lectura.
- `flux_palabras_kb`: señales de «cómo se hace / software / calibración /
  materiales» → se enlaza la KB de FLUX en su idioma y modelo.
- `flux_palabras_soporte`: señales de «averiada / repuesto / garantía / que
  vaya alguien» → se enlaza nuestro soporte. Si no está claro, el nuestro.
- `catalogo_sync_activa` / `catalogo_sync_hora`: la sincronización diaria del
  catálogo (compositor y tiendas). Encendida por defecto: solo lectura.
- `precio_meses_orientativo`: un precio cuya fuente no se ha confirmado en más
  de estos meses es «orientativo» y el correo lo dice.
- `iva_por_fuente`: si los precios de cada fuente llevan IVA (`con`), no lo
  llevan (`sin`) o no se ha declarado (`null`): se declara, nunca se deduce
  del número. Sin declarar, el correo dice que está pendiente de confirmar.
- `interes_productos_revisado`: Bart ha dado por revisada la propuesta de
  interés → máquinas (la pantalla avisa mientras no lo esté).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

CONFIG_KEY = "conocimiento"
HORA_DEFECTO = "04:30"
HORA_CATALOGO_DEFECTO = "04:45"
PRECIO_MESES_DEFECTO = 6
PRECIO_MESES_MIN = 1
PRECIO_MESES_MAX = 36
FUENTES_IVA: tuple[str, ...] = ("compositor", "woocommerce")
IVA_VALORES: tuple[str, ...] = ("con", "sin")
_HORA_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

PALABRAS_KB_DEFECTO: tuple[str, ...] = (
    "cómo", "como se", "how to", "how do", "wie kann", "wie man", "comment", "hoe",
    "software", "beam studio", "firmware", "calibr", "kalibr", "enfoque", "focus", "fokus",
    "cámara", "camera", "kamera", "material", "ajuste", "ajustar", "setting", "einstell",
    "paramèt", "instelling", "configur", "wifi", "conectar", "connect", "verbind", "driver",
    "svg", "dxf", "potencia", "power", "leistung", "puissance", "velocidad", "speed",
    "geschwindigkeit", "vitesse",
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


def _meses(raw: Any) -> int | None:
    try:
        valor = int(raw)
    except (TypeError, ValueError):
        return None
    return valor if PRECIO_MESES_MIN <= valor <= PRECIO_MESES_MAX else None


def _iva(raw: Any, *, estricto: bool = False) -> dict[str, str | None]:
    """`{"compositor": "con"|"sin"|None, "woocommerce": ...}`. Con `estricto`,
    un valor que no sea con / sin / vacío es un `ValueError`."""
    salida: dict[str, str | None] = dict.fromkeys(FUENTES_IVA)
    if not isinstance(raw, dict):
        if estricto and raw is not None:
            raise ValueError("El IVA por fuente tiene que ser un objeto fuente → con / sin.")
        return salida
    for fuente in FUENTES_IVA:
        if fuente not in raw:
            continue
        valor = str(raw[fuente] or "").strip().lower() or None
        if valor is not None and valor not in IVA_VALORES:
            if estricto:
                raise ValueError(
                    f"IVA de {fuente} no válido: {raw[fuente]!r} (con, sin o vacío).")
            valor = None
        salida[fuente] = valor
    return salida


def defectos() -> dict[str, Any]:
    return {
        "flux_sync_activa": True,
        "flux_sync_hora": HORA_DEFECTO,
        "flux_palabras_kb": list(PALABRAS_KB_DEFECTO),
        "flux_palabras_soporte": list(PALABRAS_SOPORTE_DEFECTO),
        "catalogo_sync_activa": True,
        "catalogo_sync_hora": HORA_CATALOGO_DEFECTO,
        "precio_meses_orientativo": PRECIO_MESES_DEFECTO,
        "iva_por_fuente": dict.fromkeys(FUENTES_IVA),
        "interes_productos_revisado": False,
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
    if "catalogo_sync_activa" in data:
        base["catalogo_sync_activa"] = bool(data["catalogo_sync_activa"])
    base["catalogo_sync_hora"] = _hora(data.get("catalogo_sync_hora")) or HORA_CATALOGO_DEFECTO
    base["precio_meses_orientativo"] = (
        _meses(data.get("precio_meses_orientativo")) or PRECIO_MESES_DEFECTO)
    base["iva_por_fuente"] = _iva(data.get("iva_por_fuente"))
    base["interes_productos_revisado"] = bool(data.get("interes_productos_revisado", False))
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
    if "catalogo_sync_activa" in payload:
        if not isinstance(payload["catalogo_sync_activa"], bool):
            raise ValueError("«Sincronizar el catálogo cada día» tiene que ser sí o no.")
        base["catalogo_sync_activa"] = payload["catalogo_sync_activa"]
    if "catalogo_sync_hora" in payload:
        hora = _hora(payload["catalogo_sync_hora"])
        if hora is None:
            raise ValueError(
                f"Hora de la sincronización del catálogo no válida: "
                f"{payload['catalogo_sync_hora']!r} (formato HH:MM).")
        base["catalogo_sync_hora"] = hora
    if "precio_meses_orientativo" in payload:
        meses = _meses(payload["precio_meses_orientativo"])
        if meses is None:
            raise ValueError(
                f"Meses hasta «orientativo»: entre {PRECIO_MESES_MIN} y {PRECIO_MESES_MAX}.")
        base["precio_meses_orientativo"] = meses
    if "iva_por_fuente" in payload:
        # Solo cambian las fuentes que vienen en el PATCH; las demás se conservan.
        crudo = payload["iva_por_fuente"]
        nuevo = _iva(crudo, estricto=True)
        if isinstance(crudo, dict):
            base["iva_por_fuente"].update({f: nuevo[f] for f in FUENTES_IVA if f in crudo})
    if "interes_productos_revisado" in payload:
        if not isinstance(payload["interes_productos_revisado"], bool):
            raise ValueError("«Propuesta revisada» tiene que ser sí o no.")
        base["interes_productos_revisado"] = payload["interes_productos_revisado"]
    return base


def configuracion(session: Session) -> dict[str, Any]:
    """Configuración efectiva (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
