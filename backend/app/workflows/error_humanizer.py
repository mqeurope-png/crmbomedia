"""PR-Fix-Pestaña-Workflows-Y-Humanizar #2.

Traduce los códigos técnicos que escriben los handlers de step y el
finalize del run (`StepResult.error` / `WorkflowRun.error_summary`)
a mensajes legibles para el operador.

Decisión arquitectónica: humanizamos al **momento de escribir en
BD**, en una sola capa (`_record_history` y `_finalize` del motor).
Los handlers no se tocan — siguen emitiendo códigos técnicos. El
SQL `SELECT error_summary FROM workflow_run_history` devuelve ya el
texto en español, sin doble lectura ni JOIN.

Códigos que son markers no-error (no se traducen para que el frontend
los pueda detectar por prefijo):

  - `completed_with_skipped:N`  — marker del finalize.
  - `contact_deleted`           — cancelación de runs por hard delete.
  - `workflow_empty`            — trigger sin sucesor.
"""
from __future__ import annotations

import re

# Markers que el frontend interpreta por prefijo — no traducimos para
# no romper la detección por `startswith()`.
_NON_TRANSLATED_MARKERS = (
    "completed_with_skipped:",
    "contact_deleted",
    "workflow_empty",
)

# Mapping código → mensaje humano. Patrones con `{}` corresponden a
# códigos que llevan un parámetro tras `:` (ej. `gmail_not_ready:bart@…`).
_EXACT_MAP: dict[str, str] = {
    # Configuración de step incompleta.
    "empty_tag": (
        "El paso no tiene tag configurado. Edita el workflow para "
        "elegir uno."
    ),
    "empty_field": (
        "El paso 'Modificar campo' no tiene el campo elegido."
    ),
    "empty_status": (
        "El paso 'Cambiar estado del ciclo' no tiene estado elegido."
    ),
    "zero_delta": (
        "El paso 'Modificar lead score' tiene delta 0 — no haría nada."
    ),
    "no_user_id": (
        "El paso 'Asignar propietario' no tiene user seleccionado."
    ),
    "user_inactive_or_missing": (
        "El user destino no existe o está inactivo."
    ),
    # Datos del contacto que bloquean el step.
    "contact_no_email": (
        "El contacto no tiene email — no se puede enviarle nada."
    ),
    "contact_inactive": (
        "El contacto está inactivo (desactivado del CRM)."
    ),
    "contact_unsubscribed": (
        "El contacto se dio de baja de marketing."
    ),
    "contact_no_owner": (
        "El contacto no tiene propietario asignado. No se puede usar "
        "el modo 'Alias del propietario'."
    ),
    "owner_missing": (
        "El propietario asignado al contacto ya no existe."
    ),
    "owner_has_no_aliases": (
        "El propietario del contacto no tiene aliases configurados en "
        "/account."
    ),
    "no_assignee_no_owner": (
        "No hay user al que asignar la tarea ni propietario del contacto."
    ),
    "no_stage_id": (
        "El paso 'Mover oportunidad' no tiene stage destino."
    ),
    "no_opportunity": (
        "El contacto no tiene oportunidad activa en el pipeline."
    ),
    "no_manager": (
        "El propietario del contacto no tiene manager asignado."
    ),
    # Cap diario / quota.
    "email_cap_reached": (
        "Cuota diaria de Gmail rebasada. El envío se reintentará mañana."
    ),
    "email_send_failed:quota_exceeded": (
        "Cuota diaria de Gmail rebasada. El envío se reintentará mañana."
    ),
    "email_send_failed:bounce": (
        "El email rebotó (destinatario inexistente o casilla llena)."
    ),
    # Triggers / waits.
    "wait_for_event_timeout": (
        "Esperó el evento configurado pero no ocurrió dentro del plazo."
    ),
    "wait_for_event_no_event_type": (
        "El paso 'Esperar evento' no tiene tipo de evento configurado."
    ),
    "invalid_absolute_at": (
        "El paso 'Esperar hasta' tiene fecha absoluta mal formada."
    ),
    # Engine.
    "step_missing": "Un paso del workflow ya no existe en la BD.",
    "contact_missing": "El contacto del run ya no existe en la BD.",
}

# Códigos con parámetro tras `:` — patron + template.
_PATTERN_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^gmail_not_ready:(.+)$"),
        "La cuenta Gmail de {0} no está disponible (token caducado o "
        "desconectado). Reconecta desde /account.",
    ),
    (
        re.compile(r"^template_not_found:(.+)$"),
        "La plantilla {0} referenciada ya no existe en el CRM.",
    ),
    (
        re.compile(r"^specific_alias_missing_fallback_used:(.+)$"),
        "El alias específico configurado ya no existe en el propietario. "
        "Se usó el predeterminado: {0}.",
    ),
    (
        re.compile(r"^unknown_step_type:(.+)$"),
        "Tipo de step '{0}' desconocido — paquete de handlers no cargado.",
    ),
    (
        re.compile(r"^unsupported_field:(.+)$"),
        "Campo '{0}' no soportado por el evaluador.",
    ),
)


def humanize_error_summary(code: str | None) -> str | None:
    """Traduce un código técnico a mensaje humano. Si el código no
    está mapeado, devuelve el original — preferimos exponer el código
    raw a perder información, y el operador puede reportar el código
    para que añadamos su traducción."""
    if not code:
        return code
    # Markers que el frontend lee por prefijo: no tocar.
    for prefix in _NON_TRANSLATED_MARKERS:
        if code.startswith(prefix):
            return code
    if code in _EXACT_MAP:
        return _EXACT_MAP[code]
    for pattern, template in _PATTERN_MAP:
        match = pattern.match(code)
        if match:
            return template.format(*match.groups())
    return code
