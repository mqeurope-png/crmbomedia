/**
 * Sprint UX-Workflows-Editor — helpers para presentar workflows en
 * lenguaje humano.
 *
 * Sin estos helpers el editor expone `contact.lead_score` y `eq` a
 * usuarios no técnicos. Aquí traducimos config + tipo de step a:
 *   - icono (emoji o lucide-react),
 *   - label corto (visible en el nodo del canvas),
 *   - resumen una línea (visible debajo del label),
 *   - descripción larga (visible en el resumen narrativo).
 */

export type StepLike = {
  type: string;
  config?: Record<string, unknown>;
  display_name?: string | null;
};

/** Minutes → cadena legible en español. */
export function humanizeDuration(minutes: number | undefined | null): string {
  if (!minutes || minutes <= 0) return "0 min";
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 60 * 24) {
    const h = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest > 0 ? `${h}h ${rest}min` : `${h}h`;
  }
  if (minutes < 60 * 24 * 7) {
    const d = Math.floor(minutes / (60 * 24));
    return d === 1 ? "1 día" : `${d} días`;
  }
  if (minutes < 60 * 24 * 60) {
    const w = Math.floor(minutes / (60 * 24 * 7));
    return w === 1 ? "1 semana" : `${w} semanas`;
  }
  const m = Math.floor(minutes / (60 * 24 * 30));
  return `~${m} mes${m === 1 ? "" : "es"}`;
}

const TRIGGER_LABELS: Record<string, string> = {
  "contact.created": "Contacto creado",
  "contact.updated": "Contacto actualizado",
  "contact.lifecycle_changed": "Cambio de estado del ciclo",
  "contact.unsubscribed": "Contacto se da de baja",
  "contact.date_field": "Fecha del contacto coincide",
  "email.crm.opened": "Email del CRM abierto",
  "email.crm.clicked": "Link de email CRM cliqueado",
  "email.crm.replied": "Email del CRM respondido",
  "email.brevo.opened": "Email campaña Brevo abierto",
  "email.brevo.clicked": "Link Brevo cliqueado",
  "engagement.brevo.composed": "Engagement Brevo compuesto",
  "task.created": "Tarea creada",
  "task.completed": "Tarea completada",
  "task.overdue": "Tarea vencida",
  "opportunity.created": "Oportunidad creada",
  "opportunity.stage_changed": "Oportunidad cambia de stage",
  "opportunity.won": "Oportunidad ganada",
  "opportunity.lost": "Oportunidad perdida",
  "cron.recurring": "Tarea programada recurrente",
};

const STEP_ICONS: Record<string, string> = {
  trigger: "🚀",
  wait_time: "⏱️",
  wait_until: "📅",
  wait_for_event: "👀",
  condition: "❓",
  switch: "🔀",
  action_add_tag: "🏷️",
  action_remove_tag: "🏷️",
  action_change_lifecycle_status: "🔄",
  action_set_custom_field: "📊",
  action_change_lead_score: "🎯",
  action_assign_owner: "👤",
  action_create_task: "📋",
  action_send_email: "✉️",
  action_move_opportunity_stage: "💼",
  action_notify_owner: "🔔",
  action_notify_manager: "🔔",
  action_push_to_brevo: "🔗",
  action_force_agilecrm_resync: "🔗",
  exit_natural: "🚪",
  exit_won: "✅",
  exit_lost: "❌",
};

export function stepIcon(stepType: string): string {
  return STEP_ICONS[stepType] ?? "•";
}

export function humanizeTrigger(triggerType: string): string {
  return TRIGGER_LABELS[triggerType] ?? triggerType;
}

/**
 * Devuelve el label corto que va en el header del nodo. Usa display_name
 * si el operador lo asignó, si no calcula uno legible.
 */
export function humanizeStepLabel(step: StepLike): string {
  if (step.display_name && step.display_name.trim()) return step.display_name;
  const cfg = step.config ?? {};
  switch (step.type) {
    case "trigger":
      return "Inicio del workflow";
    case "wait_time":
      return `Esperar ${humanizeDuration(cfg.duration_minutes as number)}`;
    case "wait_until":
      return "Esperar hasta fecha";
    case "wait_for_event": {
      const ev = (cfg.event_type as string) ?? "evento";
      return `Esperar a ${humanizeTrigger(ev) || ev}`;
    }
    case "condition":
      return "Si...";
    case "switch":
      return "Según...";
    case "action_add_tag":
      return `Añadir tag: ${(cfg.tag as string) || "?"}`;
    case "action_remove_tag":
      return `Quitar tag: ${(cfg.tag as string) || "?"}`;
    case "action_change_lifecycle_status":
      return `Cambiar estado a: ${(cfg.status as string) || "?"}`;
    case "action_set_custom_field":
      return `Modificar ${(cfg.field as string) || "campo"}`;
    case "action_change_lead_score": {
      const d = (cfg.delta as number) ?? 0;
      return d >= 0
        ? `Sumar ${d} puntos lead score`
        : `Restar ${Math.abs(d)} puntos lead score`;
    }
    case "action_assign_owner":
      return "Asignar propietario";
    case "action_create_task":
      return `Crear tarea: ${
        ((cfg.title as string) || "").slice(0, 40) || "(sin título)"
      }`;
    case "action_send_email":
      return `Enviar email: ${
        ((cfg.subject as string) || "").slice(0, 40) || "(sin asunto)"
      }`;
    case "action_move_opportunity_stage":
      return "Mover oportunidad de stage";
    case "action_notify_owner":
      return "Notificar al propietario";
    case "action_notify_manager":
      return "Notificar al manager";
    case "action_push_to_brevo":
      return "Push contacto a Brevo";
    case "action_force_agilecrm_resync":
      return "Forzar resync AgileCRM";
    case "exit_natural":
      return "Salida natural";
    case "exit_won":
      return "Salida ganada";
    case "exit_lost":
      return "Salida perdida";
    default:
      return step.type;
  }
}

/**
 * Resumen una línea bajo el label (opcional, contextual).
 */
export function stepSummary(step: StepLike): string {
  const cfg = step.config ?? {};
  if (step.type === "condition") {
    const c = cfg.condition as Record<string, unknown> | undefined;
    if (!c) return "(sin condición configurada)";
    const f = c.field as string | undefined;
    const o = c.op as string | undefined;
    const v = c.value;
    if (!f || !o) return "(condición incompleta)";
    if (o === "empty") return `${f} está vacío`;
    if (o === "not_empty") return `${f} no está vacío`;
    return `${f} ${o} ${v}`;
  }
  if (step.type === "wait_for_event") {
    const timeout = cfg.timeout_minutes as number | undefined;
    return timeout ? `Timeout: ${humanizeDuration(timeout)}` : "";
  }
  return "";
}

// ---------------------------------------------------------------------
// Validación per-step
// ---------------------------------------------------------------------

export type StepValidationResult = {
  valid: boolean;
  missing: string[];
};

/** Campos requeridos por tipo. Si falta alguno, el nodo se pinta en rojo. */
const REQUIRED_FIELDS: Record<string, string[]> = {
  trigger: [],
  wait_time: ["duration_minutes"],
  wait_until: [],
  wait_for_event: ["event_type", "timeout_minutes"],
  condition: ["condition.field", "condition.op"],
  switch: ["field", "cases"],
  action_add_tag: ["tag"],
  action_remove_tag: ["tag"],
  action_change_lifecycle_status: ["status"],
  action_set_custom_field: ["field"],
  action_change_lead_score: ["delta"],
  action_assign_owner: ["user_id"],
  action_create_task: ["title"],
  action_send_email: ["subject", "body_html"],
  action_move_opportunity_stage: ["stage_id"],
  action_notify_owner: [],
  action_notify_manager: [],
  action_push_to_brevo: [],
  action_force_agilecrm_resync: [],
  exit_natural: [],
  exit_won: [],
  exit_lost: [],
};

function _getDeepValue(
  obj: Record<string, unknown>,
  path: string,
): unknown {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return cur;
}

export function validateStepConfig(step: StepLike): StepValidationResult {
  const required = REQUIRED_FIELDS[step.type] ?? [];
  const cfg = step.config ?? {};
  const missing: string[] = [];
  for (const field of required) {
    const value = _getDeepValue(cfg, field);
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      (Array.isArray(value) && value.length === 0)
    ) {
      missing.push(field);
    }
  }
  return { valid: missing.length === 0, missing };
}

// ---------------------------------------------------------------------
// Narrative summary (resumen del grafo en lenguaje plano)
// ---------------------------------------------------------------------

export type NarrativeNode = {
  id: string;
  type: string;
  config?: Record<string, unknown>;
  display_name?: string | null;
};

export type NarrativeEdge = {
  from_step_id: string;
  to_step_id: string;
  branch_label: string;
};

/**
 * Walk del grafo desde el entry step. Concatena descripciones con
 * conectores ("Cuando...", "entonces", "esperar", "si", "después",
 * "finalmente"). Ramas se identan con "─".
 */
export function buildNarrativeSummary(
  triggerType: string,
  steps: NarrativeNode[],
  edges: NarrativeEdge[],
  entryId: string,
): string {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const outgoing = (sid: string) =>
    edges.filter((e) => e.from_step_id === sid);

  const lines: string[] = [`Cuando ocurre «${humanizeTrigger(triggerType)}»`];
  const visited = new Set<string>();

  function walk(stepId: string, depth: number): void {
    if (visited.has(stepId)) {
      lines.push(`${"  ".repeat(depth)}↪ vuelve a paso ya visitado`);
      return;
    }
    visited.add(stepId);
    const step = byId.get(stepId);
    if (!step) return;
    const indent = "  ".repeat(depth);
    const label = humanizeStepLabel({
      type: step.type,
      config: step.config,
      display_name: step.display_name,
    });
    if (step.type === "trigger") {
      // Ya se mencionó arriba; saltamos al siguiente.
    } else {
      lines.push(`${indent}→ ${label}`);
    }
    const next = outgoing(stepId);
    if (next.length === 0) return;
    if (next.length === 1) {
      walk(next[0].to_step_id, depth);
      return;
    }
    // Múltiples ramas → identadas con label.
    for (const edge of next) {
      lines.push(`${indent}  · Si «${edge.branch_label}»:`);
      walk(edge.to_step_id, depth + 2);
    }
  }

  walk(entryId, 0);
  return lines.join("\n");
}
