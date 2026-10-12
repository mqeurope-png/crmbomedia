"""Configuración de la base de conocimiento (Configuración ERP → «Base de
conocimiento»). Vive en el blob `factusol_series_json` de `ErpSettings`, bajo
la clave `conocimiento` (sin migración):

    {"flux_sync_activa": true, "flux_sync_hora": "04:30",
     "flux_palabras_kb": [...], "flux_palabras_soporte": [...],
     "catalogo_sync_activa": true, "catalogo_sync_hora": "04:45",
     "precio_meses_orientativo": 6,
     "iva_por_fuente": {"compositor": null, "woocommerce": null},
     "interes_productos_revisado": false,
     "medios_sync_activa": true,
     "youtube_canales": [{"handle": "@fluxinc", "marca": "flux"}, ...],
     "interes_palabras": {"dtf": ["textil*", "t-shirt*", ...]}}

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
- `medios_sync_activa`: la sincronización diaria de las imágenes de las webs y
  de la tabla de vídeos, detrás de la del catálogo. Encendida por defecto: es
  solo lectura.
- `youtube_canales`: los canales de YouTube cuyos vídeos van a la tabla, con
  la marca del catálogo que se les supone cuando el título no la dice (vacía
  en un canal de varias marcas).
- `interes_palabras`: las palabras por interés CAMBIADAS en la pantalla
  interés → máquinas (solo esas; las demás, las de partida de
  `catalogo.PALABRAS_DE_PARTIDA`). Se guardan por su API
  (`guardar_palabras_interes`): el «Guardar cambios» de Configuración ERP no
  las toca, para que una pestaña abierta de antes no las pise.
"""
from __future__ import annotations

import json
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
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,100}$")
_INTERES_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_CANALES = 20

#: Los canales de YouTube del grupo (12/10/2026) y su marca del catálogo.
CANALES_DE_PARTIDA: tuple[dict[str, str], ...] = (
    {"handle": "@MQEuropebvba", "marca": ""},
    {"handle": "@artisjeteurope", "marca": "artisjet"},
    {"handle": "@fluxinc", "marca": "flux"},
    {"handle": "@artisJet", "marca": "artisjet"},
)

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


def _canales(raw: Any, *, estricto: bool = False) -> list[dict[str, str]] | None:
    """La lista de canales limpia (`{"handle": "@…", "marca": "…"}`), sin
    repetir. None si no es una lista; con `estricto`, `ValueError` por un
    canal que no vale."""
    if not isinstance(raw, list):
        if estricto:
            raise ValueError("Los canales de YouTube tienen que ser una lista.")
        return None
    salida: list[dict[str, str]] = []
    for c in raw:
        handle = str((c.get("handle") if isinstance(c, dict) else c) or "").strip()
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        if not _HANDLE_RE.match(handle):
            if estricto:
                raise ValueError(f"Canal de YouTube no válido: {handle or c!r} (p. ej. @fluxinc).")
            continue
        if any(x["handle"].lower() == handle.lower() for x in salida):
            continue
        marca = str((c.get("marca") if isinstance(c, dict) else "") or "").strip()[:80]
        salida.append({"handle": handle, "marca": marca})
    if estricto and len(salida) > MAX_CANALES:
        raise ValueError(f"Como mucho {MAX_CANALES} canales de YouTube.")
    return salida[:MAX_CANALES]


def _interes_palabras(raw: Any) -> dict[str, list[str]]:
    """Las palabras por interés cambiadas (código → palabras), limpias. Lo que
    no vale se descarta: esto lee lo guardado, no valida una entrada."""
    from app.services.conocimiento.catalogo import normalizar_palabras  # noqa: PLC0415

    if not isinstance(raw, dict):
        return {}
    salida: dict[str, list[str]] = {}
    for codigo, palabras in raw.items():
        clave = str(codigo or "").strip().lower()
        if not _INTERES_RE.match(clave) or not isinstance(palabras, list):
            continue
        try:
            salida[clave] = normalizar_palabras(palabras)
        except ValueError:
            continue
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
        "medios_sync_activa": True,
        "youtube_canales": [dict(c) for c in CANALES_DE_PARTIDA],
        "interes_palabras": {},
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
    if "medios_sync_activa" in data:
        base["medios_sync_activa"] = bool(data["medios_sync_activa"])
    canales = _canales(data.get("youtube_canales"))
    if canales is not None:
        base["youtube_canales"] = canales
    base["interes_palabras"] = _interes_palabras(data.get("interes_palabras"))
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
    if "medios_sync_activa" in payload:
        if not isinstance(payload["medios_sync_activa"], bool):
            raise ValueError("«Sincronizar imágenes y vídeos cada día» tiene que ser sí o no.")
        base["medios_sync_activa"] = payload["medios_sync_activa"]
    if "youtube_canales" in payload:
        base["youtube_canales"] = _canales(payload["youtube_canales"], estricto=True) or []
    # `interes_palabras` no se toca aquí: va por `guardar_palabras_interes`.
    return base


def guardar_palabras_interes(session: Session, interes: Any,
                              palabras: list[Any] | None) -> list[str] | None:
    """Las palabras de un interés en la pantalla interés → máquinas (sin
    commit). `None` vuelve a las de partida. Devuelve las guardadas (o None).
    `ValueError` legible si el interés o alguna palabra no vale."""
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415
    from app.services.conocimiento.catalogo import normalizar_palabras  # noqa: PLC0415

    clave = str(interes or "").strip().lower()
    if not _INTERES_RE.match(clave):
        raise ValueError("El interés es su código (p. ej. dtf o uv).")
    limpias = None if palabras is None else normalizar_palabras(palabras)
    fila = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if fila is None:
        fila = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        session.add(fila)
    try:
        blob = json.loads(fila.factusol_series_json or "{}")
    except (TypeError, ValueError):
        blob = {}
    blob = blob if isinstance(blob, dict) else {}
    actual = normalizar(blob.get(CONFIG_KEY))
    propias = dict(actual["interes_palabras"])
    if limpias is None:
        propias.pop(clave, None)
    else:
        propias[clave] = limpias
    actual["interes_palabras"] = propias
    blob[CONFIG_KEY] = actual
    fila.factusol_series_json = json.dumps(blob, ensure_ascii=False)
    session.flush()
    return limpias


def configuracion(session: Session) -> dict[str, Any]:
    """Configuración efectiva (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
