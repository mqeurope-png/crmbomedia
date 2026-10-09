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
- `umbral_confianza`: por debajo, la Fase 2 no enviará. En la Fase 1 solo lo
  lee la pantalla (marca en rojo lo que está por debajo).
- `antiguedad_horas`: solo se procesan leads más recientes que esto. Nada de
  histórico. Es el defecto del paso «Clasificar lead» cuando el paso no fija
  el suyo; el trigger tiene el suyo propio (`max_age_hours`).
- `ventana`: la ventana horaria con la que se siembra el paso de espera del
  workflow (el paso guarda la suya).
- `mapa`: interés × idioma → plantilla; sin entrada se busca por nombre.
- `remitentes`: la web de cada lead → remitente; cuenta de Agile → web.

El interruptor y el tope los aplica el paso «Clasificar lead»
(`app.workflows.steps`): apagado, o con el tope del día alcanzado, el lead
sale por la rama «omitido» sin clasificar, sin borrador y sin tarea.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.workflows.trigger_definitions import LEAD_MAX_AGE_HOURS_DEFECTO

CONFIG_KEY = "lead_response"
TOPE_DIARIO_DEFECTO = 20
UMBRAL_DEFECTO = 0.7
ANTIGUEDAD_HORAS_DEFECTO = LEAD_MAX_AGE_HOURS_DEFECTO
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


def _es_correo(valor: str) -> bool:
    return "@" in valor and "." in valor.rsplit("@", 1)[-1] and " " not in valor


def validar(
    payload: Any, actual: Any = None, *, plantillas_validas: set[str] | None = None,
) -> dict[str, Any]:
    """Valida lo que llega del PATCH de Configuración ERP y lo FUNDE con lo
    guardado: lo que no viene se conserva. `ValueError` con un mensaje
    legible si algo no vale."""
    from app.services.leads.clasificador import IDIOMAS, INTERESES_COMERCIALES  # noqa: PLC0415
    from app.services.web_forms.sitios import WEBS  # noqa: PLC0415
    from app.workflows.ventana import _hora  # noqa: PLC0415

    if not isinstance(payload, dict):
        raise ValueError("La configuración de respuesta a leads no es válida.")
    base = normalizar(actual)
    if "activo" in payload:
        if not isinstance(payload["activo"], bool):
            raise ValueError("«Respuesta a leads activa» tiene que ser sí o no.")
        base["activo"] = payload["activo"]
    for clave, etiqueta, minimo, maximo in (
        ("tope_diario", "Tope diario", 1, 1000),
        ("antiguedad_horas", "Antigüedad máxima (horas)", 1, 24 * 90),
    ):
        if clave in payload:
            try:
                valor = int(payload[clave])
            except (TypeError, ValueError):
                raise ValueError(f"{etiqueta}: tiene que ser un número.") from None
            if not minimo <= valor <= maximo:
                raise ValueError(f"{etiqueta}: entre {minimo} y {maximo}.")
            base[clave] = valor
    if "umbral_confianza" in payload:
        try:
            umbral = float(payload["umbral_confianza"])
        except (TypeError, ValueError):
            raise ValueError("Umbral de confianza: tiene que ser un número entre 0 y 1.") from None
        if not 0.0 <= umbral <= 1.0:
            raise ValueError("Umbral de confianza: entre 0 y 1.")
        base["umbral_confianza"] = umbral
    if "ventana" in payload:
        ventana = payload["ventana"]
        if not isinstance(ventana, dict):
            raise ValueError("La ventana horaria no es válida.")
        nueva = {**base["ventana"], **{k: v for k, v in ventana.items() if k in VENTANA_DEFECTO}}
        inicio, fin = _hora(nueva.get("start")), _hora(nueva.get("end"))
        if inicio is None or fin is None:
            raise ValueError("Ventana horaria: las horas van en formato HH:MM.")
        if fin <= inicio:
            raise ValueError(
                "Ventana horaria: la hora de fin tiene que ser posterior a la de inicio."
            )
        if not isinstance(nueva.get("enabled", True), bool) or not isinstance(
                nueva.get("weekdays_only", True), bool):
            raise ValueError("Ventana horaria: «activa» y «solo laborables» son sí o no.")
        base["ventana"] = {
            "enabled": nueva.get("enabled", True), "start": inicio.strftime("%H:%M"),
            "end": fin.strftime("%H:%M"), "weekdays_only": nueva.get("weekdays_only", True),
        }
    if "mapa" in payload:
        mapa = payload["mapa"]
        if not isinstance(mapa, dict):
            raise ValueError("El mapa de plantillas no es válido.")
        limpio: dict[str, str] = {}
        for clave, valor in mapa.items():
            interes, _, idioma = str(clave).partition(":")
            if interes not in INTERESES_COMERCIALES or idioma not in IDIOMAS:
                raise ValueError(f"Mapa de plantillas: la clave {clave!r} no es interés:idioma.")
            template_id = str(valor or "").strip()
            desconocida = plantillas_validas is not None and template_id not in plantillas_validas
            if template_id and desconocida:
                raise ValueError(
                    f"Mapa de plantillas: la plantilla de {clave} no existe ({template_id})."
                )
            limpio[f"{interes}:{idioma}"] = template_id
        base["mapa"] = limpio
    if "remitentes" in payload:
        remitentes = payload["remitentes"]
        if not isinstance(remitentes, dict):
            raise ValueError("Los remitentes no son válidos.")
        if "por_web" in remitentes:
            por_web = remitentes["por_web"]
            if not isinstance(por_web, dict):
                raise ValueError("Remitentes por web: no es válido.")
            limpio_web: dict[str, str] = {}
            for sitio, correo in por_web.items():
                correo = str(correo or "").strip()
                if correo and not _es_correo(correo):
                    raise ValueError(f"Remitente de {sitio}: {correo!r} no es una dirección.")
                if correo:
                    limpio_web[str(sitio).strip()] = correo
            base["remitentes"]["por_web"] = limpio_web
        if "por_cuenta_agile" in remitentes:
            por_cuenta = remitentes["por_cuenta_agile"]
            if not isinstance(por_cuenta, dict):
                raise ValueError("Webs por cuenta de AgileCRM: no es válido.")
            conocidas = set(WEBS) | set(base["remitentes"]["por_web"])
            limpio_cuenta: dict[str, str] = {}
            for cuenta, sitio in por_cuenta.items():
                sitio = str(sitio or "").strip()
                if sitio and sitio not in conocidas:
                    raise ValueError(
                        f"Cuenta de AgileCRM {cuenta}: la web {sitio!r} no se conoce."
                    )
                if sitio:
                    limpio_cuenta[str(cuenta).strip()] = sitio
            base["remitentes"]["por_cuenta_agile"] = limpio_cuenta
    return base


def catalogo(session: Session) -> dict[str, Any]:
    """Lo que la pantalla necesita para pintar la configuración: intereses,
    idiomas, plantillas candidatas (y la que se resuelve por nombre), webs con
    su remitente por defecto y cuentas de AgileCRM."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.email_templates.models import EmailTemplate  # noqa: PLC0415
    from app.models.crm import ExternalSystem  # noqa: PLC0415
    from app.models.integration_settings import IntegrationAccount  # noqa: PLC0415
    from app.services.leads import plantillas  # noqa: PLC0415
    from app.services.leads.clasificador import (  # noqa: PLC0415
        ETIQUETAS_INTERES,
        INTERESES,
        INTERESES_COMERCIALES,
    )
    from app.services.web_forms.sitios import MARCAS, REMITENTES, WEBS  # noqa: PLC0415

    candidatas = list(session.scalars(
        select(EmailTemplate).where(EmailTemplate.name.like("Lead%"))
        .order_by(EmailTemplate.name)
    ))
    por_nombre: dict[str, str | None] = {}
    for interes in sorted(INTERESES_COMERCIALES):
        for idioma in plantillas.IDIOMAS_CON_PLANTILLA:
            tpl = plantillas.plantilla_para(session, interes, idioma)
            por_nombre[plantillas.clave_mapa(interes, idioma)] = tpl.id if tpl else None
    cuentas = list(session.scalars(
        select(IntegrationAccount).where(IntegrationAccount.system == ExternalSystem.AGILECRM)
        .order_by(IntegrationAccount.display_name)
    ))
    return {
        "intereses": [
            {"id": i, "label": ETIQUETAS_INTERES[i], "comercial": i in INTERESES_COMERCIALES}
            for i in INTERESES
        ],
        "idiomas": list(plantillas.IDIOMAS_CON_PLANTILLA),
        "plantillas": [{"id": t.id, "name": t.name} for t in candidatas],
        "mapa_por_nombre": por_nombre,
        "webs": [
            {"clave": clave, "web": web, "marca": MARCAS.get(clave, clave),
             "remitente_defecto": REMITENTES.get(clave)}
            for clave, web in WEBS.items()
        ],
        "cuentas_agile": [
            {"account_id": c.account_id, "display_name": c.display_name, "enabled": c.enabled}
            for c in cuentas
        ],
    }
