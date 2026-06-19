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

/** PR-Fixes-Pase-4 Bug 2. Lee tags del config aceptando el nuevo
 *  `cfg.tags = [...]` y la forma legacy `cfg.tag = "name"`. */
function _readTagNames(cfg: Record<string, unknown>): string[] {
  const list = cfg.tags;
  if (Array.isArray(list)) {
    return list.map((t) => String(t ?? "").trim()).filter(Boolean);
  }
  const single = cfg.tag;
  if (typeof single === "string" && single.trim()) return [single.trim()];
  return [];
}

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
  // PR-Fixes #9: "Recurrente (preset)" era técnico. Le llamamos
  // "Horario fijo" en la UI del selector y en cualquier label
  // posterior.
  "cron.recurring": "Horario fijo",
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
 * Look-ups que el editor cachea al cargar (templates de email,
 * stages de pipeline, tags, custom fields). Pasados al humanizer
 * para que el label resuelva ids a nombres legibles.
 */
export type LookupCaches = {
  templates?: Record<string, string>;
  pipelineStages?: Record<string, string>;
  users?: Record<string, string>;
  triggerType?: string;
};

/**
 * Devuelve el label corto que va en el header del nodo. Usa display_name
 * si el operador lo asignó, si no calcula uno legible.
 *
 * PR-Fixes-Workflows-Editor:
 * - Bug 1: el nodo `trigger` ya no muestra "Inicio del workflow"; usa
 *   `lookups.triggerType` para reflejar el tipo elegido por el operador.
 * - Bug 2: las acciones con id (template, stage, tag, user) ahora
 *   resuelven el nombre humano si el caller pasa los `lookups`. Sin
 *   caches caen al placeholder previo.
 */
export function humanizeStepLabel(
  step: StepLike,
  lookups: LookupCaches = {},
): string {
  if (step.display_name && step.display_name.trim()) return step.display_name;
  const cfg = step.config ?? {};
  switch (step.type) {
    case "trigger":
      if (lookups.triggerType) {
        return humanizeTrigger(lookups.triggerType);
      }
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
    case "action_add_tag": {
      const names = _readTagNames(cfg);
      if (names.length === 0) return "Añadir tag";
      if (names.length === 1) return `Añadir tag: ${names[0]}`;
      return `Añadir ${names.length} tags: ${names.join(", ").slice(0, 60)}`;
    }
    case "action_remove_tag": {
      const names = _readTagNames(cfg);
      if (names.length === 0) return "Quitar tag";
      if (names.length === 1) return `Quitar tag: ${names[0]}`;
      return `Quitar ${names.length} tags: ${names.join(", ").slice(0, 60)}`;
    }
    case "action_change_lifecycle_status":
      return `Cambiar estado a: ${(cfg.status as string) || "?"}`;
    case "action_set_custom_field": {
      const field = (cfg.field as string) || "campo";
      const value = cfg.value;
      if (value === undefined || value === "" || value === null) {
        return `Modificar campo "${field}"`;
      }
      return `Modificar campo "${field}" = ${String(value).slice(0, 30)}`;
    }
    case "action_change_lead_score": {
      const d = (cfg.delta as number) ?? 0;
      return d >= 0
        ? `Sumar ${d} puntos lead score`
        : `Restar ${Math.abs(d)} puntos lead score`;
    }
    case "action_assign_owner": {
      const uid = cfg.user_id as string | undefined;
      const name = uid ? lookups.users?.[uid] : undefined;
      return name ? `Asignar a: ${name}` : "Asignar propietario";
    }
    case "action_create_task":
      return `Crear tarea: ${
        ((cfg.title as string) || "").slice(0, 40) || "(sin título)"
      }`;
    case "action_send_email": {
      const tid = cfg.template_id as string | undefined;
      if (tid && lookups.templates?.[tid]) {
        return `Enviar email: ${lookups.templates[tid]}`;
      }
      return `Enviar email: ${
        ((cfg.subject as string) || "").slice(0, 40) || "(sin asunto)"
      }`;
    }
    case "action_move_opportunity_stage": {
      const sid = cfg.stage_id as string | undefined;
      const name = sid ? lookups.pipelineStages?.[sid] : undefined;
      if (name) return `Mover oportunidad a "${name}"`;
      if (sid) return `Mover oportunidad a stage ${sid.slice(0, 8)}…`;
      return "Mover oportunidad de stage";
    }
    case "action_notify_owner":
      return "Notificar al propietario";
    case "action_notify_manager":
      return "Notificar al manager";
    case "action_push_to_brevo":
      return "Sincronizar con Brevo";
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
 *
 * PR-Fixes-Pase-4 Bug 4. Cuando el step está inválido devolvemos el
 * primer error específico (ya humanizado) en vez del placeholder
 * "(condición incompleta)". El StepNode renderiza este texto y el
 * tooltip del ⚠️ lista TODOS los errores.
 */
export function stepSummary(step: StepLike): string {
  const cfg = step.config ?? {};
  if (step.type === "condition") {
    const validation = validateStepConfig(step);
    if (!validation.valid) {
      const msgs = humanizeValidationMessages(validation.missing);
      return msgs[0] ?? "(condición incompleta)";
    }
    // Caso completo: resumen humano del primer rule del árbol IR.
    const c = cfg.condition as Record<string, unknown> | undefined;
    if (!c) return "";
    return _summarizeConditionTree(c) || "(condición configurada)";
  }
  if (step.type === "wait_for_event") {
    const timeout = cfg.timeout_minutes as number | undefined;
    return timeout ? `Timeout: ${humanizeDuration(timeout)}` : "";
  }
  return "";
}

function _summarizeConditionTree(
  tree: Record<string, unknown>,
): string {
  if (tree.operator) {
    const children = (tree.children as Record<string, unknown>[]) ?? [];
    if (children.length === 0) return "";
    const op = String(tree.operator).toLowerCase();
    if (children.length === 1) return _summarizeConditionTree(children[0]);
    return (
      `${_summarizeConditionTree(children[0])} (${op} ${children.length - 1} más)`
    );
  }
  if (tree.type === "rule") {
    const f = (tree.field as string) || "?";
    const c = (tree.comparator as string) || "?";
    const v = tree.value;
    if (FILTER_NO_VALUE_COMPARATORS.has(c)) {
      return `${f} ${c}`;
    }
    return `${f} ${c} ${String(v ?? "").slice(0, 30)}`;
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
  // PR-Fixes-Pase-4 Bug 2: la validación per-step de tags vive en
  // `validateStepConfig` (acepta `tags[]` y legacy `tag`).
  action_add_tag: [],
  action_remove_tag: [],
  action_change_lifecycle_status: ["status"],
  // PR-Fixes #3: el step "Modificar campo" requiere AMBOS `field` y
  // `value` — sin valor el efecto sería NULL, que el operador no
  // suele querer expresar implícitamente.
  action_set_custom_field: ["field", "value"],
  action_change_lead_score: ["delta"],
  action_assign_owner: ["user_id"],
  action_create_task: ["title"],
  action_send_email: ["subject", "body_html"],
  // PR-Fixes-Pase-2 Bug C: pasamos a pedir pipeline_id Y stage_id —
  // el dropdown cascade los rellena juntos.
  action_move_opportunity_stage: ["pipeline_id", "stage_id"],
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

/** PR-Fixes-Pase-3 Bug 4 + Pase-4 Bug 4. Mensaje humano por id de
 *  campo faltante. Si una entrada no está aquí, devolvemos el id
 *  literal — al menos el operador ve algo nombrado. */
const HUMAN_MISSING_MESSAGES: Record<string, string> = {
  template_id_or_inline:
    "Falta seleccionar plantilla o escribir contenido",
  subject: "Falta el asunto del email",
  body_html: "Falta el cuerpo del email",
  duration_minutes: "Falta indicar la duración",
  event_type: "Elige qué evento esperar",
  timeout_minutes: "Falta indicar el timeout",
  "condition.field": "Falta seleccionar el campo a evaluar",
  "condition.op": "Falta seleccionar el operador",
  "condition.rules_empty": "La condición no tiene reglas",
  field: "Falta seleccionar el campo a modificar",
  cases: "Añade al menos un valor de caso",
  tag: "Falta seleccionar al menos 1 tag",
  status: "Falta seleccionar el estado",
  value: "Falta el nuevo valor",
  delta: "Indica cuántos puntos sumar o restar (positivo o negativo)",
  user_id: "Falta seleccionar el usuario propietario",
  title: "Falta el título de la tarea",
  pipeline_id: "Falta seleccionar el pipeline",
  stage_id: "Falta seleccionar el stage destino",
  from_alias_display_name:
    "Falta elegir el display name del alias del propietario",
};

/** PR-Fixes-Pase-4 Bug 4. Comparators del FilterBuilder que NO
 *  requieren valor — set membership (existe / no existe / vacío).
 *  Espejo de `NO_VALUE_COMPARATORS` en `segmentTranslator.ts`. */
const FILTER_NO_VALUE_COMPARATORS = new Set([
  "is_null",
  "is_not_null",
  "is_empty",
  "is_not_empty",
]);

/** Traduce los ids de error con sufijo (`condition.rule[2].value`,
 *  `condition.rule[0].field`) a mensajes con número de regla 1-based,
 *  que es lo que el operador ve en la UI. Otros ids pasan por la
 *  tabla literal. */
export function humanizeValidationMessages(missing: string[]): string[] {
  return missing.map((m) => {
    const ruleMatch = m.match(/^condition\.rule\[(\d+)\]\.(field|value)$/);
    if (ruleMatch) {
      const idx = parseInt(ruleMatch[1], 10) + 1;
      const part = ruleMatch[2];
      if (part === "field") {
        return `Falta el campo en la regla ${idx}`;
      }
      return `Falta el valor en la regla ${idx}`;
    }
    return HUMAN_MISSING_MESSAGES[m] ?? `Falta: ${m}`;
  });
}

/** PR-Fixes-Pase-4 Bug 4. Recorre el árbol IR del FilterBuilder y
 *  reporta IDs específicos de cada regla incompleta. El árbol tiene
 *  forma `{operator: AND|OR|NOT, children: [...]}` para grupos y
 *  `{type: "rule", field, comparator, value}` para hojas. Devuelve
 *  un array de ids tipo `condition.rule[N].value` que la UI traduce
 *  a "Falta el valor en la regla N+1".
 */
function _validateConditionTree(
  tree: Record<string, unknown> | undefined,
  counter: { idx: number },
): string[] {
  if (!tree || typeof tree !== "object") return [];
  if (tree.operator) {
    const children = (tree.children as Record<string, unknown>[]) ?? [];
    if (children.length === 0) {
      return ["condition.rules_empty"];
    }
    const errors: string[] = [];
    for (const child of children) {
      errors.push(..._validateConditionTree(child, counter));
    }
    return errors;
  }
  if (tree.type === "rule") {
    const idx = counter.idx;
    counter.idx += 1;
    const errors: string[] = [];
    const field = tree.field as string | undefined;
    const comparator = tree.comparator as string | undefined;
    if (!field || !field.trim()) {
      errors.push(`condition.rule[${idx}].field`);
    }
    if (
      comparator &&
      !FILTER_NO_VALUE_COMPARATORS.has(comparator)
    ) {
      const value = tree.value;
      const isEmpty =
        value === undefined ||
        value === null ||
        value === "" ||
        (Array.isArray(value) && value.length === 0);
      if (isEmpty) {
        errors.push(`condition.rule[${idx}].value`);
      }
    }
    return errors;
  }
  return [];
}

export function validateStepConfig(step: StepLike): StepValidationResult {
  const cfg = step.config ?? {};
  // PR-Fixes #8: action_send_email es válido si tiene template_id O
  // si tiene subject+body_html. La validación genérica de
  // REQUIRED_FIELDS no expresa el OR — lo hacemos a mano aquí.
  if (step.type === "action_send_email") {
    const missing: string[] = [];
    const tpl = cfg.template_id;
    const hasTpl = typeof tpl === "string" && tpl.trim();
    const subject = cfg.subject as string | undefined;
    const body = cfg.body_html as string | undefined;
    if (!hasTpl) {
      if (!subject?.trim()) missing.push("subject");
      if (!body?.trim()) missing.push("body_html");
      if (missing.length > 0) missing.unshift("template_id_or_inline");
    }
    // PR-Fixes-Pase-4 Bug 8: mode "owner_specific" requiere haber
    // elegido un display_name del owner.
    const aliasMode = cfg.from_alias_mode as string | undefined;
    if (aliasMode === "owner_specific") {
      const dn = cfg.from_alias_display_name as string | undefined;
      if (!dn || !dn.trim()) missing.push("from_alias_display_name");
    }
    return { valid: missing.length === 0, missing };
  }
  // PR-Fixes-Pase-4 Bug 2: tags multi-select. Inválido si lista vacía
  // (aceptamos también el legacy `cfg.tag`).
  if (step.type === "action_add_tag" || step.type === "action_remove_tag") {
    const names = _readTagNames(cfg);
    if (names.length === 0) {
      return { valid: false, missing: ["tag"] };
    }
    return { valid: true, missing: [] };
  }
  // PR-Fixes-Pase-4 Bug 4: el step `condition` con FilterBuilder
  // necesita inspección profunda — REQUIRED_FIELDS solo mira el
  // top-level que no aplica al árbol IR.
  if (step.type === "condition") {
    const tree = cfg.condition as Record<string, unknown> | undefined;
    if (!tree || Object.keys(tree).length === 0) {
      return { valid: false, missing: ["condition.rules_empty"] };
    }
    const errors = _validateConditionTree(tree, { idx: 0 });
    return { valid: errors.length === 0, missing: errors };
  }
  const required = REQUIRED_FIELDS[step.type] ?? [];
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
 *
 * `lookups` se pasa a `humanizeStepLabel` para resolver ids a nombres
 * (template, stage, user) cuando el caller los tiene cacheados.
 */
export function buildNarrativeSummary(
  triggerType: string,
  steps: NarrativeNode[],
  edges: NarrativeEdge[],
  entryId: string,
  lookups: LookupCaches = {},
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
    const label = humanizeStepLabel(
      {
        type: step.type,
        config: step.config,
        display_name: step.display_name,
      },
      { ...lookups, triggerType },
    );
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
