"use client";

/**
 * Sprint Reglas-Assign PR-E — editor de regla de auto-asignación.
 *
 * Drawer lateral con todos los campos del modelo y un botón "Preview"
 * que llama al endpoint `/api/assignment-rules/preview` (rule no
 * guardada) para mostrar "X contactos matchean ahora" mientras el
 * operador construye las condiciones.
 *
 * El builder de condiciones reutiliza `<EntityFilterBuilder>` con el
 * schema declarativo de `contact` (mismo motor que `/contacts`, así
 * cualquier campo / comparador soportado en filtros también es válido
 * en reglas — sin duplicar el whitelist).
 */
import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { EntityFilterBuilder } from "./entity/EntityFilterBuilder";
import { extractErrorMessage } from "../lib/errors";
import {
  getEntityFilterSchema,
  type FieldDescriptor,
} from "../lib/entitySchema";
import { getUsers, type User } from "../lib/api";
import {
  type AssignmentRule,
  type AssignmentRuleApplyTo,
  type AssignmentRuleDryRunResult,
  type AssignmentRuleWritePayload,
  createAssignmentRule,
  previewAssignmentRule,
  updateAssignmentRule,
} from "../lib/assignmentRulesApi";

type Props = {
  mode: "create" | "edit";
  rule?: AssignmentRule;
  onClose: () => void;
  onSaved: () => void;
};

type FormState = {
  name: string;
  description: string;
  is_active: boolean;
  priority: number;
  conditions: Record<string, unknown>;
  primary_user_id: string | null;
  secondary_user_ids: string[];
  apply_to: AssignmentRuleApplyTo;
  override_existing: boolean;
  stop_on_match: boolean;
};

function toForm(rule?: AssignmentRule): FormState {
  if (!rule) {
    return {
      name: "",
      description: "",
      is_active: true,
      priority: 100,
      conditions: { operator: "AND", children: [] },
      primary_user_id: null,
      secondary_user_ids: [],
      apply_to: "unassigned_only",
      override_existing: false,
      stop_on_match: true,
    };
  }
  const apply_to = (
    ["new_only", "unassigned_only", "all_matching", "all"].includes(rule.apply_to)
      ? rule.apply_to
      : "unassigned_only"
  ) as AssignmentRuleApplyTo;
  return {
    name: rule.name,
    description: rule.description ?? "",
    is_active: rule.is_active,
    priority: rule.priority,
    conditions:
      rule.conditions && Object.keys(rule.conditions).length > 0
        ? rule.conditions
        : { operator: "AND", children: [] },
    primary_user_id: rule.primary_user_id,
    secondary_user_ids: rule.secondary_user_ids,
    apply_to,
    override_existing: rule.override_existing,
    stop_on_match: rule.stop_on_match,
  };
}

function toPayload(state: FormState): AssignmentRuleWritePayload {
  return {
    name: state.name.trim(),
    description: state.description.trim() || null,
    is_active: state.is_active,
    priority: state.priority,
    conditions: state.conditions,
    primary_user_id: state.primary_user_id,
    secondary_user_ids: state.secondary_user_ids,
    apply_to: state.apply_to,
    override_existing: state.override_existing,
    stop_on_match: state.stop_on_match,
  };
}

export function RuleEditorDrawer({ mode, rule, onClose, onSaved }: Props) {
  const [state, setState] = useState<FormState>(() => toForm(rule));
  const [users, setUsers] = useState<User[]>([]);
  const [fields, setFields] = useState<FieldDescriptor[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<AssignmentRuleDryRunResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUsers({ limit: 200 })
      .then((rows) => {
        if (cancelled) return;
        setUsers(rows.filter((u) => u.is_active));
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      });
    getEntityFilterSchema("contact")
      .then((schema) => {
        if (!cancelled) setFields(schema.fields);
      })
      .catch(() => {
        if (!cancelled) setFields([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave() {
    if (!state.name.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = toPayload(state);
      if (mode === "edit" && rule) {
        await updateAssignmentRule(rule.id, payload);
      } else {
        await createAssignmentRule(payload);
      }
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la regla."));
    } finally {
      setSaving(false);
    }
  }

  async function onPreview() {
    if (!state.primary_user_id) {
      setError("Necesitas seleccionar un primary user para hacer preview.");
      return;
    }
    setPreviewing(true);
    setError(null);
    setPreview(null);
    try {
      const result = await previewAssignmentRule(toPayload(state));
      setPreview(result);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo lanzar la preview."));
    } finally {
      setPreviewing(false);
    }
  }

  function toggleSecondary(uid: string) {
    setState((s) => {
      const has = s.secondary_user_ids.includes(uid);
      return {
        ...s,
        secondary_user_ids: has
          ? s.secondary_user_ids.filter((id) => id !== uid)
          : [...s.secondary_user_ids, uid],
      };
    });
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside
        className="drawer drawer-wide"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={mode === "create" ? "Nueva regla" : "Editar regla"}
      >
        <header className="drawer-header">
          <h3>{mode === "create" ? "Nueva regla" : `Editar — ${rule?.name}`}</h3>
          <button
            type="button"
            className="btn small"
            onClick={onClose}
            aria-label="Cerrar"
          >
            <X size={12} aria-hidden />
          </button>
        </header>

        {error ? <p className="form-error">{error}</p> : null}

        <div className="drawer-body">
          <section className="form-section">
            <label className="field">
              <span>Nombre</span>
              <input
                type="text"
                value={state.name}
                onChange={(e) => setState((s) => ({ ...s, name: e.target.value }))}
                maxLength={120}
                required
              />
            </label>
            <label className="field">
              <span>Descripción</span>
              <textarea
                value={state.description}
                onChange={(e) =>
                  setState((s) => ({ ...s, description: e.target.value }))
                }
                rows={2}
              />
            </label>
            <div className="form-row">
              <label className="field field-narrow">
                <span>Prioridad</span>
                <input
                  type="number"
                  value={state.priority}
                  onChange={(e) =>
                    setState((s) => ({
                      ...s,
                      priority: parseInt(e.target.value, 10) || 0,
                    }))
                  }
                  min={0}
                />
                <span className="muted small">menor = primero</span>
              </label>
              <label className="field field-narrow">
                <span>Apply to</span>
                <select
                  value={state.apply_to}
                  onChange={(e) =>
                    setState((s) => ({
                      ...s,
                      apply_to: e.target.value as AssignmentRuleApplyTo,
                    }))
                  }
                >
                  <option value="unassigned_only">Sólo sin asignar</option>
                  <option value="new_only">Sólo leads nuevos (post-creación)</option>
                  <option value="all_matching">Todos los que matchean</option>
                </select>
              </label>
            </div>
            <div className="form-row form-toggles">
              <label className="field-toggle">
                <input
                  type="checkbox"
                  checked={state.is_active}
                  onChange={(e) =>
                    setState((s) => ({ ...s, is_active: e.target.checked }))
                  }
                />
                <span>Activa</span>
              </label>
              <label className="field-toggle">
                <input
                  type="checkbox"
                  checked={state.stop_on_match}
                  onChange={(e) =>
                    setState((s) => ({ ...s, stop_on_match: e.target.checked }))
                  }
                />
                <span>Cortar cadena al matchear</span>
              </label>
              <label className="field-toggle">
                <input
                  type="checkbox"
                  checked={state.override_existing}
                  onChange={(e) =>
                    setState((s) => ({
                      ...s,
                      override_existing: e.target.checked,
                    }))
                  }
                />
                <span>Sobreescribir asignaciones existentes</span>
              </label>
            </div>
          </section>

          <section className="form-section">
            <h4>Targets</h4>
            <label className="field">
              <span>Primary (responsable)</span>
              <select
                value={state.primary_user_id ?? ""}
                onChange={(e) =>
                  setState((s) => ({
                    ...s,
                    primary_user_id: e.target.value || null,
                  }))
                }
              >
                <option value="">— sin primary —</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name || u.email}
                  </option>
                ))}
              </select>
            </label>
            <div className="field">
              <span>Secundarios (multi)</span>
              <div className="rule-secondaries">
                {users.map((u) => {
                  const checked = state.secondary_user_ids.includes(u.id);
                  return (
                    <label
                      key={u.id}
                      className={`rule-secondary-chip${checked ? " is-on" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSecondary(u.id)}
                      />
                      {u.full_name || u.email}
                    </label>
                  );
                })}
                {users.length === 0 ? (
                  <p className="muted small">No hay usuarios activos.</p>
                ) : null}
              </div>
            </div>
          </section>

          <section className="form-section">
            <h4>Condiciones</h4>
            {fields === null ? (
              <p className="muted">Cargando schema…</p>
            ) : (
              <EntityFilterBuilder
                fields={fields}
                value={state.conditions}
                onChange={(next) => setState((s) => ({ ...s, conditions: next }))}
              />
            )}
          </section>

          {preview ? (
            <section className="form-section">
              <p className={preview.auto_disabled ? "alert warn" : "alert success"}>
                {preview.auto_disabled
                  ? `Regla se desactivaría — ${preview.reason ?? "primary inactivo"}`
                  : preview.error
                  ? `Error: ${preview.error}`
                  : `${preview.matched} contactos matchean ahora mismo.`}
              </p>
            </section>
          ) : null}
        </div>

        <footer className="drawer-footer">
          <button
            type="button"
            className="button small secondary"
            onClick={onPreview}
            disabled={previewing}
          >
            <Search size={11} aria-hidden /> {previewing ? "Calculando…" : "Preview"}
          </button>
          <div className="drawer-footer-end">
            <button
              type="button"
              className="button small"
              onClick={onClose}
              disabled={saving}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="button small primary"
              onClick={onSave}
              disabled={saving}
            >
              {saving ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </footer>
      </aside>
    </div>
  );
}
