"""Configuración de la respuesta a leads (Configuración ERP → «Respuesta a
leads»).

Vive en el blob `factusol_series_json` de `ErpSettings`, bajo la clave
`lead_response` (sin migración, como el Cuadre):

    {"activo": false, "tope_diario": 20, "umbral_confianza": 0.7,
     "antiguedad_horas": 72,
     "ventana": {"enabled": true, "start": "09:00", "end": "18:00",
                 "weekdays_only": true},
     "mapa": {"vending:es": "<template_id>", ...},
     "remitentes": {"por_web": {"pimpam": "info@pimpam-vending.com"},
                    "por_cuenta_agile": {"<account_id>": "artisjet-es"}}}

- `activo`: el interruptor general. APAGADO por defecto: la Fase 1 se enciende
  después de revisar la clasificación en seco con Bart.
- `tope_diario`: leads que se procesan al día; al llegar, se para y se avisa.
- `umbral_confianza`: por debajo, la Fase 2 no enviará (en la Fase 1 solo se
  enseña).
- `antiguedad_horas`: solo se procesan leads más recientes que esto. Nada de
  histórico.
- `mapa`: interés × idioma → plantilla; sin entrada se busca por nombre.
- `remitentes`: la web de cada lead → remitente; cuenta de Agile → web.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

CONFIG_KEY = "lead_response"
TOPE_DIARIO_DEFECTO = 20
UMBRAL_DEFECTO = 0.7
ANTIGUEDAD_HORAS_DEFECTO = 72
VENTANA_DEFECTO: dict[str, Any] = {
    "enabled": True, "start": "09:00", "end": "18:00", "weekdays_only": True,
}


def defectos() -> dict[str, Any]:
    return {
        "activo": False,
        "tope_diario": TOPE_DIARIO_DEFECTO,
        "umbral_confianza": UMBRAL_DEFECTO,
        "antiguedad_horas": ANTIGUEDAD_HORAS_DEFECTO,
        "ventana": dict(VENTANA_DEFECTO),
        "mapa": {},
        "remitentes": {"por_web": {}, "por_cuenta_agile": {}},
    }


def normalizar(raw: Any) -> dict[str, Any]:
    """La configuración completa a partir de lo guardado; lo que no se
    entiende, por defecto."""
    base = defectos()
    data = raw if isinstance(raw, dict) else {}
    base["activo"] = bool(data.get("activo", False))
    for clave, minimo, maximo in (("tope_diario", 1, 1000), ("antiguedad_horas", 1, 24 * 90)):
        try:
            valor = int(data.get(clave))
        except (TypeError, ValueError):
            continue
        if minimo <= valor <= maximo:
            base[clave] = valor
    try:
        umbral = float(data.get("umbral_confianza"))
    except (TypeError, ValueError):
        umbral = None
    if umbral is not None and 0.0 <= umbral <= 1.0:
        base["umbral_confianza"] = umbral
    ventana = data.get("ventana")
    if isinstance(ventana, dict):
        base["ventana"] = {**VENTANA_DEFECTO, **{
            k: v for k, v in ventana.items() if k in VENTANA_DEFECTO
        }}
    mapa = data.get("mapa")
    if isinstance(mapa, dict):
        base["mapa"] = {str(k): (str(v) if v is not None else "") for k, v in mapa.items()}
    remitentes = data.get("remitentes")
    if isinstance(remitentes, dict):
        for sub in ("por_web", "por_cuenta_agile"):
            valores = remitentes.get(sub)
            if isinstance(valores, dict):
                base["remitentes"][sub] = {
                    str(k): str(v).strip() for k, v in valores.items() if str(v or "").strip()
                }
    return base


def configuracion(session: Session) -> dict[str, Any]:
    """La configuración efectiva (con defaults)."""
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
