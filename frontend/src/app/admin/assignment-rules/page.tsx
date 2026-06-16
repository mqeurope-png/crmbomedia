"use client";

/**
 * Sprint Reglas-Assign PR-E. UI admin/manager para reglas de
 * auto-asignación. Lista + editor (drawer) + preview/run en vivo.
 *
 * Las reglas se evalúan en orden ascendente de `priority` y aplican
 * según `apply_to`. El backend respalda el ciclo completo con auto-
 * desactivación si el primary del target queda inactivo (PR-Ca/PR-C)
 * — pintamos un warning en esa fila para que el operador re-asigne.
 */
import { Pencil, Play, Plus, Power, Search, Trash2, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { RuleEditorDrawer } from "../../components/RuleEditorDrawer";
import {
  type AssignmentRule,
  type AssignmentRuleApplyTo,
  type AssignmentRuleDryRunResult,
  deleteAssignmentRule,
  dryRunAssignmentRule,
  listAssignmentRules,
  runAssignmentRule,
  updateAssignmentRule,
} from "../../lib/assignmentRulesApi";
import { extractErrorMessage } from "../../lib/errors";
import { getCurrentUser, type User } from "../../lib/api";

const APPLY_TO_LABEL: Record<string, string> = {
  new_only: "Sólo leads nuevos",
  unassigned_only: "Sólo sin asignar",
  all_matching: "Todos los que matchean",
  all: "Todos los que matchean",
};

export default function AssignmentRulesPage() {
  const [user, setUser] = useState<User | null>(null);
  const [rules, setRules] = useState<AssignmentRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<
    | { kind: "closed" }
    | { kind: "create" }
    | { kind: "edit"; rule: AssignmentRule }
  >({ kind: "closed" });
  const [runningId, setRunningId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setRules(await listAssignmentRules());
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar las reglas."),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    Promise.all([getCurrentUser(), load()])
      .then(([currentUser]) => setUser(currentUser))
      .catch(() => undefined);
  }, []);

  const canManage = user?.role === "admin" || user?.role === "manager";

  async function onToggleActive(rule: AssignmentRule) {
    try {
      await updateAssignmentRule(rule.id, {
        name: rule.name,
        description: rule.description,
        is_active: !rule.is_active,
        priority: rule.priority,
        conditions: rule.conditions,
        primary_user_id: rule.primary_user_id,
        secondary_user_ids: rule.secondary_user_ids,
        apply_to: rule.apply_to as AssignmentRuleApplyTo,
        override_existing: rule.override_existing,
        stop_on_match: rule.stop_on_match,
      });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar el estado."));
    }
  }

  async function onDelete(rule: AssignmentRule) {
    if (!confirm(`¿Borrar la regla "${rule.name}"?`)) return;
    try {
      await deleteAssignmentRule(rule.id);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la regla."));
    }
  }

  function formatRunSummary(
    name: string,
    result: AssignmentRuleDryRunResult,
  ): string {
    if (result.auto_disabled) {
      return `"${name}" desactivada automáticamente — ${
        result.reason ?? "target inactivo"
      }`;
    }
    if (result.error) {
      return `"${name}" no se ejecutó: ${result.error}`;
    }
    return `"${name}": ${result.matched} matches · ${result.applied} aplicadas`;
  }

  async function onDryRun(rule: AssignmentRule) {
    setRunningId(rule.id);
    setMessage(null);
    setError(null);
    try {
      const result = await dryRunAssignmentRule(rule.id);
      setMessage(`PREVIEW — ${formatRunSummary(rule.name, result)}`);
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo lanzar la preview."));
    } finally {
      setRunningId(null);
    }
  }

  async function onRun(rule: AssignmentRule) {
    if (
      !confirm(
        `Aplicar "${rule.name}" sobre todos los contactos que matcheen ahora mismo?`,
      )
    ) {
      return;
    }
    setRunningId(rule.id);
    setMessage(null);
    setError(null);
    try {
      const result = await runAssignmentRule(rule.id);
      setMessage(formatRunSummary(rule.name, result));
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo ejecutar la regla."));
    } finally {
      setRunningId(null);
    }
  }

  return (
    <main className="shell">
      <PageHeader
        title="Reglas de asignación"
        eyebrow="Administración"
        description="El motor evalúa cada lead entrante y al ejecutar manualmente: ordena las reglas por prioridad y aplica la primera que matchee. Si stop_on_match está activo, corta la cadena. apply_to delimita el universo (solo leads nuevos, sólo sin asignar, todos los que matchean)."
        actions={
          canManage ? (
            <button
              type="button"
              className="button small"
              onClick={() => setDrawer({ kind: "create" })}
            >
              <Plus size={12} aria-hidden /> Nueva regla
            </button>
          ) : undefined
        }
      />

      {message ? <p className="alert success">{message}</p> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}

      {loading ? (
        <p className="muted">Cargando reglas…</p>
      ) : rules.length === 0 ? (
        <div className="empty-state">
          <Wand2 size={32} aria-hidden />
          <p>No hay reglas todavía. Crea una para empezar a auto-asignar contactos.</p>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: "60px" }}>#</th>
              <th>Nombre</th>
              <th>Primary</th>
              <th>Secundarios</th>
              <th>Apply to</th>
              <th>Activa</th>
              <th style={{ width: "200px" }}>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <RuleRow
                key={rule.id}
                rule={rule}
                canManage={canManage}
                applyToLabel={APPLY_TO_LABEL[rule.apply_to] ?? rule.apply_to}
                running={runningId === rule.id}
                onEdit={() => setDrawer({ kind: "edit", rule })}
                onDelete={() => onDelete(rule)}
                onToggle={() => onToggleActive(rule)}
                onDryRun={() => onDryRun(rule)}
                onRun={() => onRun(rule)}
              />
            ))}
          </tbody>
        </table>
      )}

      {drawer.kind !== "closed" ? (
        <RuleEditorDrawer
          mode={drawer.kind}
          rule={drawer.kind === "edit" ? drawer.rule : undefined}
          onClose={() => setDrawer({ kind: "closed" })}
          onSaved={() => {
            setDrawer({ kind: "closed" });
            void load();
          }}
        />
      ) : null}
    </main>
  );
}

function RuleRow({
  rule,
  canManage,
  applyToLabel,
  running,
  onEdit,
  onDelete,
  onToggle,
  onDryRun,
  onRun,
}: {
  rule: AssignmentRule;
  canManage: boolean;
  applyToLabel: string;
  running: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
  onDryRun: () => void;
  onRun: () => void;
}) {
  return (
    <tr className={rule.is_active ? "" : "is-inactive"}>
      <td>{rule.priority}</td>
      <td>
        <strong>{rule.name}</strong>
        {rule.description ? (
          <div className="muted small">{rule.description}</div>
        ) : null}
        {rule.stop_on_match ? (
          <span className="badge muted small" title="Si matchea, no evalúa reglas posteriores">
            stop_on_match
          </span>
        ) : null}
        {rule.override_existing ? (
          <span className="badge warn small" title="Reemplaza asignaciones existentes">
            override
          </span>
        ) : null}
      </td>
      <td>
        {rule.primary_user_id ? (
          <code className="small">{rule.primary_user_id.slice(0, 8)}…</code>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        {rule.secondary_user_ids.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <span className="badge">{rule.secondary_user_ids.length}</span>
        )}
      </td>
      <td>{applyToLabel}</td>
      <td>
        {rule.is_active ? (
          <span className="badge ok">Activa</span>
        ) : (
          <span
            className="badge bad"
            title="La regla apuntaba a un usuario inactivo o el operador la desactivó. Edita el primary para reactivar."
          >
            Inactiva
          </span>
        )}
      </td>
      <td>
        {canManage ? (
          <div className="row-actions">
            <button
              type="button"
              className="btn small"
              onClick={onDryRun}
              disabled={running}
              title="Preview — cuenta cuántos matchean sin aplicar"
            >
              <Search size={11} aria-hidden /> Preview
            </button>
            <button
              type="button"
              className="btn small"
              onClick={onRun}
              disabled={running || !rule.is_active}
              title={rule.is_active ? "Aplicar ahora a los matches" : "Reactiva la regla primero"}
            >
              <Play size={11} aria-hidden /> Run
            </button>
            <button
              type="button"
              className="btn small"
              onClick={onEdit}
              title="Editar regla"
            >
              <Pencil size={11} aria-hidden />
            </button>
            <button
              type="button"
              className="btn small"
              onClick={onToggle}
              title={rule.is_active ? "Desactivar" : "Activar"}
            >
              <Power size={11} aria-hidden />
            </button>
            <button
              type="button"
              className="btn small"
              onClick={onDelete}
              title="Borrar regla"
            >
              <Trash2 size={11} aria-hidden />
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}
