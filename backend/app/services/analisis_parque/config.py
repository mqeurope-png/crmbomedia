"""Configuración del análisis del parque (Configuración ERP → «Análisis del
parque»). Vive en el blob `factusol_series_json` de `ErpSettings`, bajo la
clave `analisis_parque` (sin migración), como la del asistente:

    {"modelo": "claude-sonnet-5-5", "caducidad_meses": 3,
     "anios_sin_confirmar": 6, "meses_catalogo": 24, "solo_admin": true}

- `modelo`: el que escribe la lectura y «por dónde entrar». Se cambia aquí, sin
  desplegar. La lista que se ofrece es la del asistente.
- `caducidad_meses`: un análisis guardado con más meses que estos se vuelve a
  generar al abrir la ficha, aunque los datos no hayan cambiado.
- `anios_sin_confirmar`: una máquina con más años que estos y sin baja sale en
  «lo que no se sabe» (puede que ya no esté en servicio).
- `meses_catalogo`: solo se propone un consumible vendido a algún cliente en
  estos meses (lo descatalogado, no).
- `solo_admin`: mientras Bart lo prueba, solo lo ven los administradores.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

CONFIG_KEY = "analisis_parque"
MODELO_DEFECTO = "claude-sonnet-5-5"


def defectos() -> dict[str, Any]:
    return {"modelo": MODELO_DEFECTO, "caducidad_meses": 3, "anios_sin_confirmar": 6,
            "meses_catalogo": 24, "solo_admin": True}


def _entero(raw: Any, defecto: int, minimo: int, maximo: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, v))


def normalizar(raw: Any) -> dict[str, Any]:
    base = defectos()
    if not isinstance(raw, dict):
        return base
    from app.services.ia.modelos import es_nombre_valido  # noqa: PLC0415

    modelo = str(raw.get("modelo") or "").strip()
    if modelo and es_nombre_valido(modelo):
        base["modelo"] = modelo
    base["caducidad_meses"] = _entero(raw.get("caducidad_meses"), base["caducidad_meses"], 1, 24)
    base["anios_sin_confirmar"] = _entero(raw.get("anios_sin_confirmar"),
                                          base["anios_sin_confirmar"], 1, 30)
    base["meses_catalogo"] = _entero(raw.get("meses_catalogo"), base["meses_catalogo"], 3, 120)
    if isinstance(raw.get("solo_admin"), bool):
        base["solo_admin"] = raw["solo_admin"]
    return base


def validar(payload: Any, actual: Any = None) -> dict[str, Any]:
    """Lo que manda la pantalla, sobre lo que había. ValueError si el modelo no
    es un nombre válido."""
    base = normalizar(actual)
    if not isinstance(payload, dict):
        return base
    if "modelo" in payload:
        from app.services.ia.modelos import es_nombre_valido  # noqa: PLC0415

        modelo = str(payload.get("modelo") or "").strip()
        if not es_nombre_valido(modelo):
            raise ValueError("modelo")
    return normalizar({**base, **payload})


def configuracion(session: Session) -> dict[str, Any]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar(series_config(session).get(CONFIG_KEY))
