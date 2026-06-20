"""Handlers para cada `step.type`. Registrados vía
`@register_step("type")` en `app.workflows.engine`.

Cada handler:
  - Lee `step.config_json` (dict ya cargado por el caller).
  - Ejecuta el efecto secundario (envío email, mutate contact, etc.).
  - Devuelve un `StepResult` con `next_step_id` implícito (el engine
    sigue la arista) o `wake_at` para waits.

Lo que NO hace un handler:
  - Decidir el siguiente step de la cadena (lo hace el engine
    siguiendo `WorkflowEdge.branch_label`).
  - Commitear (el engine es el dueño de la transacción).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.models.crm import (
    ContactPipelineStage,
    EmailDirection,
    EmailMessage,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.models.workflows import (
    WorkflowExitKind,
    WorkflowRun,
    WorkflowStep,
)
from app.workflows import conditions, variables
from app.workflows.engine import StepResult, register_step

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _config(step: WorkflowStep) -> dict[str, Any]:
    try:
        return json.loads(step.config_json or "{}")
    except (TypeError, ValueError):
        return {}


def _trigger_payload(run: WorkflowRun) -> dict[str, Any]:
    try:
        return json.loads(run.trigger_payload_json or "{}")
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------
# Trigger (entry node — no effect)
# ---------------------------------------------------------------------


@register_step("trigger")
def _step_trigger(session, run, step, contact) -> StepResult:
    """Nodo raíz. No tiene efecto — solo redirige al siguiente."""
    _ = session, run, step, contact
    return StepResult(status="ok", result={"trigger_passed": True})


# ---------------------------------------------------------------------
# Waits
# ---------------------------------------------------------------------


@register_step("wait_time")
def _step_wait_time(session, run, step, contact) -> StepResult:
    """Espera una duración fija. Config:
      {"duration_minutes": 4320}   # 3 días
    """
    _ = session, contact
    cfg = _config(step)
    minutes = int(cfg.get("duration_minutes") or 0)
    wake_at = datetime.now(UTC) + timedelta(minutes=max(minutes, 1))
    return StepResult(
        wake_at=wake_at,
        result={"sleep_until": wake_at.isoformat()},
    )


@register_step("wait_until")
def _step_wait_until(session, run, step, contact) -> StepResult:
    """Espera hasta una fecha relativa a un campo del contacto. Config:
      {"field": "contact.date_birthday", "hour_local": 9, "offset_days": 0}

    Acepta también una fecha absoluta:
      {"absolute_at": "2026-12-25T09:00:00+00:00"}
    """
    _ = session
    cfg = _config(step)
    if cfg.get("absolute_at"):
        try:
            target = datetime.fromisoformat(cfg["absolute_at"])
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return StepResult(
                wake_at=target, result={"sleep_until": target.isoformat()}
            )
        except ValueError:
            log.warning("workflows wait_until invalid absolute_at")
            return StepResult(status="failed", error="invalid_absolute_at")

    # Field-based. Hoy solo `contact.created_at` y campos genéricos.
    field = cfg.get("field") or "contact.created_at"
    offset_days = int(cfg.get("offset_days") or 0)
    hour_local = int(cfg.get("hour_local") or 9)
    raw = None
    if field == "contact.created_at":
        raw = contact.created_at
    if raw is None:
        return StepResult(
            status="failed", error=f"unsupported_field:{field}"
        )
    target = raw + timedelta(days=offset_days)
    target = target.replace(
        hour=hour_local, minute=0, second=0, microsecond=0
    )
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return StepResult(
        wake_at=target, result={"sleep_until": target.isoformat()}
    )


@register_step("wait_for_event")
def _step_wait_for_event(session, run, step, contact) -> StepResult:
    """Espera a que llegue un evento concreto (o caduque).
    Config:
      {"event_type": "email.crm.opened",
       "timeout_minutes": 10080,   # 7 días
       "condition": {...}}
    """
    _ = session, contact
    cfg = _config(step)
    event_type = cfg.get("event_type")
    if not event_type:
        return StepResult(
            status="failed", error="wait_for_event_no_event_type"
        )
    timeout_minutes = int(cfg.get("timeout_minutes") or 10080)
    timeout_at = datetime.now(UTC) + timedelta(
        minutes=max(timeout_minutes, 1)
    )
    return StepResult(
        wait_for_event={
            "event_type": event_type,
            "condition": cfg.get("condition") or {},
            "timeout_at": timeout_at,
        },
        result={
            "waiting_for": event_type,
            "timeout_at": timeout_at.isoformat(),
        },
    )


# ---------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------


@register_step("condition")
def _step_condition(session, run, step, contact) -> StepResult:
    """if/else. Config:
      {"condition": <árbol JSON>}
    Rama tomada: "true" o "false".
    """
    cfg = _config(step)
    ctx = conditions.EvalContext(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    matched = conditions.evaluate(cfg.get("condition"), ctx)
    return StepResult(
        branch_label="true" if matched else "false",
        result={"matched": matched},
    )


@register_step("switch")
def _step_switch(session, run, step, contact) -> StepResult:
    """N ramas sobre el mismo campo. Config:
      {"field": "contact.lifecycle_status",
       "cases": ["new", "qualified", "customer"]}
    Rama tomada: "case_0", "case_1", ..., "default".
    """
    cfg = _config(step)
    field = cfg.get("field")
    cases = cfg.get("cases") or []
    if not field:
        return StepResult(
            branch_label="default", result={"reason": "no_field"}
        )
    ctx = conditions.EvalContext(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    resolver = conditions._FIELD_RESOLVERS.get(field)
    if resolver is None:
        return StepResult(
            branch_label="default", result={"reason": "unknown_field"}
        )
    actual = resolver(ctx)
    for i, expected in enumerate(cases):
        if actual == expected:
            return StepResult(
                branch_label=f"case_{i}", result={"matched": expected}
            )
    return StepResult(branch_label="default", result={"matched": None})


# ---------------------------------------------------------------------
# Actions — Contact mutations
# ---------------------------------------------------------------------


def _step_tag_names(cfg: dict[str, Any]) -> list[str]:
    """PR-Fixes-Pase-4 Bug 2. Multi-tag para add/remove. Acepta el
    nuevo `cfg.tags = [...]` (lista) y mantiene compat con drafts
    viejos que usaban `cfg.tag = "name"` (string)."""
    raw = cfg.get("tags")
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    single = (cfg.get("tag") or "").strip()
    return [single] if single else []


@register_step("action_add_tag")
def _step_add_tag(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    tags_to_add = _step_tag_names(cfg)
    if not tags_to_add:
        return StepResult(status="skipped", error="empty_tag")
    # `Contact.tags` es CSV — el campo legacy. Lo respetamos para no
    # divergir del resto del CRM (la M:N tabla `contact_tags` también
    # existe pero esto basta para Bloque 1).
    current = [
        t.strip() for t in (contact.tags or "").split(",") if t.strip()
    ]
    existing_lower = {t.lower() for t in current}
    added: list[str] = []
    for tag_name in tags_to_add:
        if tag_name.lower() not in existing_lower:
            current.append(tag_name)
            existing_lower.add(tag_name.lower())
            added.append(tag_name)
    contact.tags = ",".join(current)
    return StepResult(result={"added_tags": added})


@register_step("action_remove_tag")
def _step_remove_tag(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    tags_to_remove = {t.lower() for t in _step_tag_names(cfg)}
    if not tags_to_remove:
        return StepResult(status="skipped", error="empty_tag")
    current = [
        t.strip() for t in (contact.tags or "").split(",") if t.strip()
    ]
    new = [t for t in current if t.lower() not in tags_to_remove]
    contact.tags = ",".join(new)
    return StepResult(result={"removed_tags": sorted(tags_to_remove)})


@register_step("action_change_lifecycle_status")
def _step_change_lifecycle_status(
    session, run, step, contact
) -> StepResult:
    _ = session, run
    cfg = _config(step)
    new = (cfg.get("status") or "").strip()
    if not new:
        return StepResult(status="skipped", error="empty_status")
    old = contact.commercial_status
    contact.commercial_status = new
    return StepResult(result={"old": old, "new": new})


# PR-Fixes-Pase-5 Bug 1. Whitelist de campos nativos del Contact
# que el paso "Modificar campo" puede setear directamente (en lugar
# de meterlos en el JSON custom_fields). Mapping a (attr, type_hint).
# Mantén sincronizado con `ContactUpdate` schema — solo exponemos
# campos que el operador puede modificar legítimamente desde un
# workflow. Campos como `id`, `is_email_valid` o relaciones
# complejas se gestionan vía otros steps específicos.
_CONTACT_NATIVE_FIELDS: dict[str, str] = {
    "first_name": "text",
    "last_name": "text",
    "email": "text",
    "phone": "text",
    "job_title": "text",
    "origin": "text",
    "linkedin_url": "url",
    "personal_website": "url",
    "address_line": "text",
    "address_city": "text",
    "address_state": "text",
    "address_postal_code": "text",
    "address_region": "text",
    "address_country": "text",
    "address_country_name": "text",
    "commercial_status": "enum",
    "lead_score": "number",
    "owner_user_id": "user_ref",
    "company_id": "company_ref",
}

# Campos requeridos del Contact — si el operador intenta dejarlos
# vacíos el step queda en error explícito en lugar de pisar el
# valor existente con NULL silenciosamente.
_CONTACT_REQUIRED_NATIVE_FIELDS = frozenset({"first_name"})


def _coerce_native_field_value(field: str, value: Any) -> Any:
    """Convierte el `value` del config (siempre llega como string del
    frontend) al tipo Python que el Contact espera para ese campo
    nativo. Para campos numéricos vacíos devuelve None (clear)."""
    type_hint = _CONTACT_NATIVE_FIELDS.get(field, "text")
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if type_hint == "number":
            try:
                return int(text)
            except ValueError:
                try:
                    return float(text)
                except ValueError:
                    return None
        return text
    return value


@register_step("action_set_custom_field")
def _step_set_custom_field(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    field = (cfg.get("field") or "").strip()
    raw_value = cfg.get("value")
    if not field:
        return StepResult(status="skipped", error="empty_field")

    # PR-Fixes-Pase-5 Bug 1. Si el campo es nativo del Contact lo
    # seteamos como atributo en lugar de mezclarlo con el JSON de
    # custom fields. El bool `is_native` también puede llegar
    # explícito del frontend, pero la whitelist manda.
    is_native = field in _CONTACT_NATIVE_FIELDS
    if is_native:
        coerced = _coerce_native_field_value(field, raw_value)
        if (
            coerced is None
            and field in _CONTACT_REQUIRED_NATIVE_FIELDS
        ):
            return StepResult(
                status="failed",
                error=(
                    f"required_field_empty:{field}"
                ),
            )
        old = getattr(contact, field, None)
        setattr(contact, field, coerced)
        return StepResult(
            result={"field": field, "old": old, "new": coerced, "native": True}
        )

    raw = {}
    try:
        raw = json.loads(contact.custom_fields or "{}") or {}
    except (TypeError, ValueError):
        raw = {}
    raw[field] = raw_value
    contact.custom_fields = json.dumps(raw, default=str)
    return StepResult(result={"field": field, "value": raw_value})


@register_step("action_change_lead_score")
def _step_change_lead_score(session, run, step, contact) -> StepResult:
    _ = session, run
    cfg = _config(step)
    delta = int(cfg.get("delta") or 0)
    if delta == 0:
        return StepResult(status="skipped", error="zero_delta")
    old = contact.lead_score or 0
    contact.lead_score = old + delta
    return StepResult(result={"old": old, "delta": delta, "new": contact.lead_score})


@register_step("action_assign_owner")
def _step_assign_owner(session, run, step, contact) -> StepResult:
    _ = run
    cfg = _config(step)
    target_user_id = cfg.get("user_id")
    if not target_user_id:
        return StepResult(status="skipped", error="no_user_id")
    user = session.get(User, target_user_id)
    if user is None or not user.is_active:
        return StepResult(status="skipped", error="user_inactive_or_missing")
    # PR-Fix-Assign-Owner-Completo. Antes solo escribíamos
    # `contact.owner_user_id`, dejando `contact_assignments` huérfana.
    # El frontend lee de esa tabla (cabecera, sidebar comerciales,
    # modal Editar), así que la asignación parecía no funcionar pese a
    # que el envío de email sí veía al owner. Replicamos lo que hace
    # el PATCH /api/contacts/{id} manual: `add_assignment` con
    # `is_primary=True` upserta la fila, demote del primary anterior
    # si existía, y recalcula `contacts.owner_user_id` desde el cache.
    from app.repositories import assignments as _assignments  # noqa: PLC0415

    old = contact.owner_user_id
    _assignments.add_assignment(
        session,
        contact_id=contact.id,
        user_id=target_user_id,
        is_primary=True,
        assigned_by_user_id=target_user_id,
        source="workflow",
    )
    return StepResult(result={"old": old, "new": target_user_id})


# ---------------------------------------------------------------------
# Action — Tasks
# ---------------------------------------------------------------------


_DUE_UNIT_TO_RELATIVEDELTA = {
    "minutes": ("minutes",),
    "hours": ("hours",),
    "days": ("days",),
    "weeks": ("weeks",),
    "months": ("months",),
}


def _parse_hhmm(raw: str | None) -> tuple[int, int] | None:
    """`"09:30"` → `(9, 30)`. Devuelve None si falta o malformado."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        hh, mm = text.split(":", 1)
        h = int(hh)
        m = int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23) or not (0 <= m <= 59):
        return None
    return (h, m)


def _resolve_workflow_task_due_at(
    cfg: dict[str, Any], *, now: datetime | None = None
) -> tuple[datetime, bool]:
    """PR-Fixes-Pase-5 Bug 3.

    Devuelve `(due_at, all_day_hint)` según el modo de vencimiento
    del config. `all_day_hint=True` indica que el operador NO eligió
    hora concreta, así que un sync con calendar debe ir all-day.

    Modos:
      - `due_mode == "relative"` (default): ahora + `duration_amount`
        unidades de `duration_unit`. Si hay `duration_hhmm`, se
        sobreescribe la hora del día calculada.
      - `due_mode == "weekday"`: próximo día de la semana
        (`target_weekday` 0=lunes…6=domingo). Si `weekday_hhmm`
        elige una hora futura del mismo día, mantenemos hoy; si la
        hora ya pasó, +7 días.
      - Legacy: si no hay `due_mode` pero hay `due_in_days`, fallback
        al comportamiento del PR #209 (now + days + `event_time_hhmm`).
    """
    from dateutil.relativedelta import relativedelta  # noqa: PLC0415

    if now is None:
        now = datetime.now(UTC)
    mode = (cfg.get("due_mode") or "").lower()

    if mode == "weekday":
        target_weekday_raw = cfg.get("target_weekday")
        try:
            target_weekday = int(target_weekday_raw)
        except (TypeError, ValueError):
            target_weekday = 0  # lunes por defecto
        target_weekday = max(0, min(6, target_weekday))
        hhmm = _parse_hhmm(cfg.get("weekday_hhmm") or cfg.get("event_time_hhmm"))
        days_ahead = (target_weekday - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            second=0, microsecond=0
        )
        if hhmm is not None:
            candidate = candidate.replace(hour=hhmm[0], minute=hhmm[1])
            # Pidió "hoy" pero la hora ya pasó → siguiente semana.
            if days_ahead == 0 and candidate <= now:
                candidate += timedelta(days=7)
        elif days_ahead == 0:
            # "Próximo lunes" un lunes sin hora → la semana que viene,
            # no hoy (que ya está empezado).
            candidate += timedelta(days=7)
        return (candidate, hhmm is None)

    if mode == "relative" or not mode:
        # Legacy: si no hay `due_mode` ni `duration_amount` pero sí
        # `due_in_days`, respeta drafts viejos del PR #209.
        if not mode and "due_in_days" in cfg and "duration_amount" not in cfg:
            due_days = int(cfg.get("due_in_days") or 1)
            hhmm = _parse_hhmm(cfg.get("event_time_hhmm"))
            candidate = now + timedelta(days=due_days)
            if hhmm is not None:
                candidate = candidate.replace(
                    hour=hhmm[0],
                    minute=hhmm[1],
                    second=0,
                    microsecond=0,
                )
            return (candidate, hhmm is None)

        amount_raw = cfg.get("duration_amount", 1)
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            amount = 1
        if amount < 0:
            amount = 0
        unit = (cfg.get("duration_unit") or "days").lower()
        kwargs = {}
        if unit in _DUE_UNIT_TO_RELATIVEDELTA:
            key = _DUE_UNIT_TO_RELATIVEDELTA[unit][0]
            kwargs[key] = amount
        else:
            kwargs["days"] = amount
        candidate = now + relativedelta(**kwargs)
        hhmm = _parse_hhmm(
            cfg.get("duration_hhmm") or cfg.get("event_time_hhmm")
        )
        if hhmm is not None:
            candidate = candidate.replace(
                hour=hhmm[0],
                minute=hhmm[1],
                second=0,
                microsecond=0,
            )
        return (candidate, hhmm is None)

    # Modo desconocido — fallback a 1 día.
    return (now + timedelta(days=1), True)


@register_step("action_create_task")
def _step_create_task(session, run, step, contact) -> StepResult:
    cfg = _config(step)
    ctx = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    title = variables.render(
        cfg.get("title") or "Tarea automática",
        ctx,
        is_html=False,
    )[:200]
    description = variables.render(
        cfg.get("description") or "",
        ctx,
        is_html=False,
    )
    priority_raw = (cfg.get("priority") or "medium").lower()
    try:
        priority = TaskPriority(priority_raw)
    except ValueError:
        priority = TaskPriority.MEDIUM
    # PR-Fixes-Pase-5 Bug 3. El vencimiento de la tarea soporta dos
    # modos del operador:
    #
    #   - "relative":  cantidad + unidad (minutes/hours/days/weeks/
    #                  months) + hora opcional → ahora + N unidades
    #                  (.replace hora si la dan).
    #   - "weekday":   próximo día de la semana + hora opcional →
    #                  ahora + delta hasta el siguiente weekday
    #                  (mismo día si la hora es futura, +7 días si
    #                  ya pasó).
    #
    # Drafts viejos sin `due_mode` siguen leyendo `due_in_days` para
    # compat (campos del panel anterior). `event_time_hhmm` legacy
    # se considera fallback de la hora.
    due_at, all_day_hint = _resolve_workflow_task_due_at(cfg)
    sync_calendar = bool(cfg.get("sync_with_google_calendar"))
    all_day = sync_calendar and all_day_hint

    assignee_id = (
        cfg.get("assign_to_user_id") or contact.owner_user_id
    )
    if not assignee_id:
        return StepResult(
            status="skipped", error="no_assignee_no_owner"
        )
    task = Task(
        title=title,
        description=description or None,
        contact_id=contact.id,
        assigned_user_id=assignee_id,
        created_by_user_id=assignee_id,
        priority=priority,
        due_at=due_at,
        status=TaskStatus.PENDING,
    )
    session.add(task)
    session.flush()
    # PR-Fixes-Pase-4 Bug 7. Sync best-effort con Google Calendar del
    # assignee. La función ya se traga errores y no-ops cuando el
    # usuario no está conectado — el workflow no se rompe.
    if sync_calendar:
        from app.integrations.google_calendar import service as gcal_service  # noqa: PLC0415

        gcal_service.sync_task_to_calendar(
            session, task, all_day=all_day
        )
    return StepResult(
        result={"task_id": task.id, "title": title, "due_at": due_at.isoformat()}
    )


# ---------------------------------------------------------------------
# Action — Opportunity / Pipeline
# ---------------------------------------------------------------------


@register_step("action_move_opportunity_stage")
def _step_move_opportunity_stage(
    session, run, step, contact
) -> StepResult:
    _ = run
    cfg = _config(step)
    target_stage_id = cfg.get("stage_id")
    if not target_stage_id:
        return StepResult(status="skipped", error="no_stage_id")
    row = session.scalar(
        select(ContactPipelineStage)
        .where(ContactPipelineStage.contact_id == contact.id)
        .order_by(ContactPipelineStage.entered_stage_at.desc())
        .limit(1)
    )
    if row is None:
        return StepResult(status="skipped", error="no_opportunity")
    old = row.stage_id
    row.stage_id = target_stage_id
    row.entered_stage_at = datetime.now(UTC)
    return StepResult(result={"old_stage": old, "new_stage": target_stage_id})


# ---------------------------------------------------------------------
# Action — Email
# ---------------------------------------------------------------------


EMAIL_DAILY_CAP = 400  # margen sobre Gmail 500/día


def _resolve_workflow_from_alias(
    *,
    session,
    owner_id: str,
    mode: str,
    fixed_alias: str | None,
    wanted_display_name: str | None,
) -> tuple[str | None, str | None]:
    """PR-Fixes-Pase-4 Bug 8.

    Devuelve `(alias_email, warning)` para los tres modos del paso
    `action_send_email`. `alias_email = None` significa que no se
    puede resolver y el step debe fallar.

    - "fixed": usa el `from_alias` literal del config, sin validar
      contra las prefs del owner (compat con drafts viejos).
    - "owner_default": busca la pref `is_default=True` del owner.
    - "owner_specific": busca la pref del owner con el
      `display_name` deseado. Si no existe, fallback al
      predeterminado del owner + warning.
    """
    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415

    if mode == "fixed":
        return (fixed_alias or "", None)

    owner_prefs = list(
        session.scalars(
            select(UserEmailAliasPref).where(
                UserEmailAliasPref.user_id == owner_id,
                UserEmailAliasPref.is_allowed.is_(True),
            )
        )
    )
    if not owner_prefs:
        return (None, None)

    default_pref = next((p for p in owner_prefs if p.is_default), None)

    if mode == "owner_default":
        if default_pref is None:
            # El owner tiene aliases marcados pero ninguno como ★.
            # Fallback al primero por orden alfabético — no rompemos
            # el send pero avisamos.
            owner_prefs.sort(key=lambda p: p.alias_email)
            return (
                owner_prefs[0].alias_email,
                f"owner_no_default_alias user_id={owner_id}",
            )
        return (default_pref.alias_email, None)

    if mode == "owner_specific":
        wanted = (wanted_display_name or "").strip()
        if wanted:
            for pref in owner_prefs:
                override = (pref.display_name_override or "").strip()
                gmail_name = (pref.gmail_display_name or "").strip()
                resolved = override or gmail_name or ""
                if resolved == wanted:
                    return (pref.alias_email, None)
        # No match — fallback al predeterminado del owner.
        if default_pref is not None:
            return (
                default_pref.alias_email,
                (
                    f"owner_alias_display_name_missing "
                    f"user_id={owner_id} wanted={wanted!r}"
                ),
            )
        owner_prefs.sort(key=lambda p: p.alias_email)
        return (
            owner_prefs[0].alias_email,
            (
                f"owner_alias_display_name_missing "
                f"and no_default user_id={owner_id} wanted={wanted!r}"
            ),
        )

    # Modo desconocido — comportarse como "fixed" sin sorprender.
    return (fixed_alias or "", None)


def _emails_sent_today_by_user(session, user_id: str) -> int:
    """Cuenta envíos del owner hoy. Reutilizamos `email_messages`
    sin nueva tabla."""
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(
        session.scalar(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.direction == EmailDirection.OUTBOUND,
                EmailMessage.created_by_user_id == user_id,
                EmailMessage.sent_at >= today_start,
            )
        )
        or 0
    )


def _next_run_after_cap_reset() -> datetime:
    """Cuando el cap del owner está saturado, defer al día siguiente
    00:01 UTC. (Bart puede tunear si el VPS está en otra timezone.)"""
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0
    )
    return tomorrow


@register_step("action_send_email")
def _step_send_email(session, run, step, contact) -> StepResult:
    """Envía email usando template del CRM o cuerpo inline. Config:
      {"template_id": "...",     # OR
       "subject": "...",
       "body_html": "...",
       "from_alias": "info@bomedia.net",
       "from_name": "Bart"}
    """
    cfg = _config(step)
    if not contact.email:
        return StepResult(status="skipped", error="contact_no_email")
    if not contact.is_active:
        return StepResult(status="skipped", error="contact_inactive")
    if contact.marketing_consent and getattr(
        contact.marketing_consent, "value", str(contact.marketing_consent)
    ) == "unsubscribed":
        return StepResult(
            status="skipped", error="contact_unsubscribed"
        )

    owner_id = contact.owner_user_id
    if not owner_id:
        return StepResult(status="skipped", error="contact_no_owner")

    # Cap diario del owner.
    if _emails_sent_today_by_user(session, owner_id) >= EMAIL_DAILY_CAP:
        log.info(
            "workflows email cap reached for user=%s; deferring",
            owner_id,
        )
        return StepResult(
            status="deferred",
            wake_at=_next_run_after_cap_reset(),
            error="email_cap_reached",
        )

    # PR-Fixes #8. Si el step usa `template_id`, resolvemos la
    # plantilla actual y rellenamos subject/body desde ahí. Las
    # ediciones de la plantilla quedan reflejadas automáticamente
    # porque leemos siempre fresco. Si la plantilla se ha borrado
    # del CRM, marcamos skipped — no es razón para fallar el run.
    #
    # PR-Fixes-Pase-5 Bug 4. Si el operador rellena `subject_override`
    # en modo plantilla, ese asunto pisa al de la plantilla — el
    # cuerpo sigue siendo el de la plantilla. Útil para reusar una
    # misma plantilla con asuntos contextualizados ("Recordatorio
    # FESPA", "Recordatorio MBO") sin duplicarla.
    raw_subject = cfg.get("subject") or ""
    raw_body = cfg.get("body_html") or ""
    template_id = cfg.get("template_id")
    if template_id:
        from app.email_templates.models import EmailTemplate  # noqa: PLC0415

        template = session.get(EmailTemplate, template_id)
        if template is None:
            return StepResult(
                status="skipped",
                error=f"template_not_found:{template_id}",
            )
        raw_subject = template.subject or ""
        raw_body = template.body_html or ""
        subject_override = (cfg.get("subject_override") or "").strip()
        if subject_override:
            raw_subject = subject_override

    # Render template.
    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    subject = variables.render(
        raw_subject,
        ctx_vars,
        is_html=False,
    )[:500]
    body_html = variables.render(
        raw_body,
        ctx_vars,
        is_html=True,
    )
    # PR-Fixes-Pase-4 Bug 8. Resolución del alias del remitente:
    # tres modos.
    #
    #   - "fixed":          legacy — `from_alias` literal del config.
    #   - "owner_default":  el alias marcado ★ por el owner del
    #                       contacto en `/account`. Default para
    #                       steps nuevos.
    #   - "owner_specific": el alias del owner cuyo display_name
    #                       coincide con el `from_alias_display_name`
    #                       que el operador eligió al diseñar el
    #                       workflow. Si el owner ya no tiene ese
    #                       display_name, fallback al predeterminado
    #                       + log warning.
    #
    # Drafts viejos sin `from_alias_mode` siguen interpretándose
    # como "fixed" para no romper comportamiento existente.
    alias_mode = (cfg.get("from_alias_mode") or "fixed").lower()
    from_alias_resolved, alias_warning = _resolve_workflow_from_alias(
        session=session,
        owner_id=owner_id,
        mode=alias_mode,
        fixed_alias=cfg.get("from_alias"),
        wanted_display_name=cfg.get("from_alias_display_name"),
    )
    if from_alias_resolved is None:
        owner_user = session.get(User, owner_id)
        owner_label = owner_user.email if owner_user else owner_id
        return StepResult(
            status="failed",
            error=(
                f"El propietario {owner_label} no tiene aliases "
                f"configurados en /account"
            )[:500],
        )
    if alias_warning:
        log.warning("workflows alias_warning %s", alias_warning)
    from_alias = from_alias_resolved
    from_name = cfg.get("from_name")

    # Delega en gmail_service.send_email. Si falla (Gmail no
    # conectado, etc.), marcamos como skipped — no rompe el run.
    try:
        from app.integrations.gmail.service import (  # noqa: PLC0415
            GmailNotConnectedError,
            GmailScopeMissingError,
            send_email,
        )

        message = send_email(
            session,
            sender_user_id=owner_id,
            from_alias=from_alias or "",
            from_name=from_name,
            to=[contact.email],
            cc=None,
            bcc=None,
            subject=subject,
            body_html=body_html,
            body_text=None,
            contact_id=contact.id,
            in_reply_to_message_id=None,
            include_unsubscribe=False,
        )
    except (GmailNotConnectedError, GmailScopeMissingError) as exc:
        return StepResult(
            status="skipped", error=f"gmail_not_ready:{exc}"
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(status="failed", error=str(exc)[:500])

    return StepResult(
        result={
            "message_id": message.id,
            "subject": subject,
            "to": contact.email,
        }
    )


# ---------------------------------------------------------------------
# Action — Notify
# ---------------------------------------------------------------------


@register_step("action_notify_owner")
def _step_notify_owner(session, run, step, contact) -> StepResult:
    """Mete una notificación in-app + envía email de aviso al owner.
    Reutiliza el helper `notifications` si existe. Si no, simplemente
    audit + email helper."""
    _ = run
    cfg = _config(step)
    owner_id = contact.owner_user_id
    if not owner_id:
        return StepResult(status="skipped", error="contact_no_owner")
    user = session.get(User, owner_id)
    if user is None:
        return StepResult(status="skipped", error="owner_missing")

    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    message = variables.render(
        cfg.get("message") or f"Workflow: revisa {contact.email}",
        ctx_vars,
        is_html=False,
    )[:500]
    # No bloqueamos en errores de email — la notificación in-app es
    # el canal principal. Aquí dejamos solo el audit; un sprint
    # futuro puede añadir notifications table.
    log.info(
        "workflows notify_owner user=%s contact=%s msg=%s",
        owner_id,
        contact.id,
        message,
    )
    return StepResult(
        result={"owner_id": owner_id, "message": message}
    )


@register_step("action_notify_manager")
def _step_notify_manager(session, run, step, contact) -> StepResult:
    """Notifica al manager del owner — del primer manager activo
    encontrado por role."""
    _ = run
    cfg = _config(step)
    from app.models.crm import UserRole  # noqa: PLC0415

    manager = session.scalar(
        select(User)
        .where(User.role == UserRole.MANAGER, User.is_active.is_(True))
        .limit(1)
    )
    if manager is None:
        return StepResult(status="skipped", error="no_manager")
    ctx_vars = variables.build_context(
        session=session,
        contact=contact,
        trigger_payload=_trigger_payload(run),
    )
    message = variables.render(
        cfg.get("message") or f"Workflow escala: {contact.email}",
        ctx_vars,
        is_html=False,
    )[:500]
    log.info(
        "workflows notify_manager manager=%s contact=%s msg=%s",
        manager.id,
        contact.id,
        message,
    )
    return StepResult(
        result={"manager_id": manager.id, "message": message}
    )


# ---------------------------------------------------------------------
# Action — Sync externos
# ---------------------------------------------------------------------


@register_step("action_push_to_brevo")
def _step_push_to_brevo(session, run, step, contact) -> StepResult:
    """Encola push del contacto a Brevo. Lazy import del helper para no
    arrastrar el módulo en tests que no lo necesitan."""
    _ = run, step
    if not contact.email:
        return StepResult(status="skipped", error="contact_no_email")
    try:
        from rq import Queue  # noqa: PLC0415

        from app.workers.queues import (  # noqa: PLC0415
            queue_name,
            redis_connection,
        )

        queue = Queue(
            queue_name("brevo", "push_contact"),
            connection=redis_connection(),
        )
        queue.enqueue(
            "app.integrations.brevo.tasks.push_contact",
            contact.id,
        )
        return StepResult(result={"enqueued_brevo_push": contact.id})
    except Exception as exc:  # noqa: BLE001
        return StepResult(status="skipped", error=str(exc)[:200])


@register_step("action_force_agilecrm_resync")
def _step_force_agilecrm_resync(
    session, run, step, contact
) -> StepResult:
    """Marca external_references del contacto para sync forzado en el
    próximo tick del scheduler Agile."""
    _ = run, step
    from app.models.crm import ExternalReference  # noqa: PLC0415

    rows = list(
        session.scalars(
            select(ExternalReference).where(
                ExternalReference.contact_id == contact.id,
                ExternalReference.system == "agilecrm",
            )
        )
    )
    for row in rows:
        row.external_status = "force_resync"
    return StepResult(
        result={"marked": len(rows)},
    )


# ---------------------------------------------------------------------
# Exit nodes
# ---------------------------------------------------------------------


@register_step("exit_natural")
def _step_exit_natural(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.NATURAL)


@register_step("exit_won")
def _step_exit_won(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.WON)


@register_step("exit_lost")
def _step_exit_lost(session, run, step, contact) -> StepResult:
    _ = session, run, step, contact
    return StepResult(exit_kind=WorkflowExitKind.LOST)


# ---------------------------------------------------------------------
# Catalog para el frontend
# ---------------------------------------------------------------------


STEP_CATALOG: list[dict[str, Any]] = [
    # Lógica
    {"type": "wait_time", "category": "wait", "label": "Esperar tiempo fijo"},
    {"type": "wait_until", "category": "wait", "label": "Esperar hasta fecha"},
    {
        "type": "wait_for_event",
        "category": "wait",
        "label": "Esperar evento (con timeout)",
    },
    {"type": "condition", "category": "logic", "label": "Condición if/else"},
    {"type": "switch", "category": "logic", "label": "Switch N ramas"},
    # Acciones contacto
    {"type": "action_add_tag", "category": "contact", "label": "Añadir tag"},
    {"type": "action_remove_tag", "category": "contact", "label": "Quitar tag"},
    {
        "type": "action_change_lifecycle_status",
        "category": "contact",
        "label": "Cambiar estado del ciclo",
    },
    {
        "type": "action_set_custom_field",
        "category": "contact",
        "label": "Modificar custom field",
    },
    {
        "type": "action_change_lead_score",
        "category": "contact",
        "label": "Sumar/Restar lead score",
    },
    {
        "type": "action_assign_owner",
        "category": "contact",
        "label": "Asignar propietario",
    },
    # Tareas
    {"type": "action_create_task", "category": "task", "label": "Crear tarea"},
    # Email
    {"type": "action_send_email", "category": "email", "label": "Enviar email"},
    # Oportunidades
    {
        "type": "action_move_opportunity_stage",
        "category": "opportunity",
        "label": "Mover oportunidad a stage",
    },
    # Notificaciones
    {
        "type": "action_notify_owner",
        "category": "notify",
        "label": "Notificar propietario",
    },
    {
        "type": "action_notify_manager",
        "category": "notify",
        "label": "Notificar manager",
    },
    # Sync externos
    {
        "type": "action_push_to_brevo",
        "category": "sync",
        "label": "Push contacto a Brevo",
    },
    {
        "type": "action_force_agilecrm_resync",
        "category": "sync",
        "label": "Forzar próximo sync AgileCRM",
    },
    # Salidas
    {"type": "exit_natural", "category": "exit", "label": "Salida natural"},
    {"type": "exit_won", "category": "exit", "label": "Salida ganada"},
    {"type": "exit_lost", "category": "exit", "label": "Salida perdida"},
]
