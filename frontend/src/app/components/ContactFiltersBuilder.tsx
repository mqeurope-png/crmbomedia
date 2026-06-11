"use client";

import { Plus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { listSegmentFields, type SegmentFieldDescriptor } from "../lib/api";
import { TagMultiSelectFilter } from "./TagMultiSelectFilter";

// ---------------------------------------------------------------------------
// Data model — flat 2-level tree, matches the operator's mental model:
//   tree     = OR of cards
//   card     = AND of conditions
//   condition= { field, operator, value }
// ---------------------------------------------------------------------------

export type FilterCondition = {
  id: string;
  field: string;
  operator: string;
  value: unknown;
};

export type FilterCard = {
  id: string;
  conditions: FilterCondition[];
};

export type FilterTree = FilterCard[];

type Props = {
  /** Engine-shaped rules tree from a loaded view. Empty `{}` → start
   * with one card + one blank condition. */
  rules: Record<string, unknown>;
  onChange: (rules: Record<string, unknown>) => void;
};

const PER_TYPE_OPERATORS: Record<string, Array<[string, string]>> = {
  string: [
    ["eq", "es exactamente"],
    ["neq", "no es"],
    ["contains", "contiene"],
    ["not_contains", "no contiene"],
    ["starts_with", "empieza por"],
    ["ends_with", "termina por"],
    ["in", "es uno de"],
    ["not_in", "no es ninguno de"],
    ["is_null", "está vacío"],
    ["is_not_null", "no está vacío"],
  ],
  int: [
    ["eq", "es igual a"],
    ["neq", "no es"],
    ["gt", "mayor que"],
    ["gte", "mayor o igual"],
    ["lt", "menor que"],
    ["lte", "menor o igual"],
    ["between", "entre"],
    ["is_null", "está vacío"],
  ],
  date: [
    ["before", "antes de"],
    ["after", "después de"],
    ["between", "entre"],
    ["in_last_n_days", "en los últimos N días"],
    ["not_in_last_n_days", "fuera de los últimos N días"],
    ["older_than_n_days", "hace más de N días"],
    ["is_null", "está vacío"],
    ["is_not_null", "no está vacío"],
  ],
  enum: [
    ["eq", "es"],
    ["neq", "no es"],
    ["in", "es uno de"],
    ["not_in", "no es ninguno de"],
  ],
  "tag-multi": [
    ["contains_any", "contiene alguno de"],
    ["contains_all", "contiene todos de"],
    ["contains_none", "no contiene ninguno de"],
  ],
  "uuid-multi": [
    ["in", "es uno de"],
    ["not_in", "no es ninguno de"],
  ],
  bool: [
    ["eq", "es"],
  ],
};

const NO_VALUE_OPERATORS = new Set(["is_null", "is_not_null"]);
const LIST_OPERATORS = new Set([
  "in",
  "not_in",
  "contains_any",
  "contains_all",
  "contains_none",
]);

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function operatorsForField(spec: SegmentFieldDescriptor): Array<[string, string]> {
  const allowed = new Set(spec.comparators);
  const byType = PER_TYPE_OPERATORS[spec.type] ?? PER_TYPE_OPERATORS.string;
  // Intersect the per-type catalog with the engine's whitelist so the
  // dropdown never offers a comparator the backend would reject.
  return byType.filter(([op]) => allowed.has(op));
}

function emptyConditionFor(spec: SegmentFieldDescriptor): FilterCondition {
  const ops = operatorsForField(spec);
  return {
    id: newId(),
    field: spec.key,
    operator: ops[0]?.[0] ?? spec.comparators[0] ?? "eq",
    value: LIST_OPERATORS.has(ops[0]?.[0] ?? "") ? [] : "",
  };
}

function emptyCardFor(spec: SegmentFieldDescriptor): FilterCard {
  return { id: newId(), conditions: [emptyConditionFor(spec)] };
}

// ---------------------------------------------------------------------------
// Tree ⇄ engine rules_json conversion
// ---------------------------------------------------------------------------

type EngineRule = {
  type: "rule";
  field: string;
  comparator: string;
  value: unknown;
};

type EngineNode = {
  operator?: string;
  children?: Array<EngineNode | EngineRule>;
  type?: string;
  field?: string;
  comparator?: string;
  value?: unknown;
};

function coerceValue(value: unknown, fieldType: string, operator: string): unknown {
  if (NO_VALUE_OPERATORS.has(operator)) return null;
  if (LIST_OPERATORS.has(operator) || operator === "between") {
    if (!Array.isArray(value)) return null;
    const items = value
      .map((item) => coerceScalar(item, fieldType))
      .filter((item) => item !== null && item !== "");
    if (operator === "between" && items.length !== 2) return null;
    if (items.length === 0) return null;
    return items;
  }
  if (operator === "in_last_n_days" || operator === "not_in_last_n_days") {
    return coerceScalar(value, "int");
  }
  return coerceScalar(value, fieldType);
}

function coerceScalar(value: unknown, fieldType: string): unknown {
  if (value === null || value === undefined) return null;
  if (fieldType === "int") {
    if (typeof value === "number") return Number.isNaN(value) ? null : value;
    const parsed = Number.parseInt(String(value).trim(), 10);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (fieldType === "bool") {
    if (typeof value === "boolean") return value;
    if (value === "true") return true;
    if (value === "false") return false;
    return null;
  }
  if (fieldType === "date") {
    const raw = String(value).trim();
    if (!raw) return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const parsed = new Date(raw);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  }
  return value;
}

export function serializeTree(
  tree: FilterTree,
  fields: SegmentFieldDescriptor[],
): Record<string, unknown> {
  const specByKey = Object.fromEntries(fields.map((f) => [f.key, f]));
  const groups = tree
    .map((card) => {
      const rules = card.conditions
        .map((c) => {
          const spec = specByKey[c.field];
          const fieldType = spec?.type ?? "string";
          const value = coerceValue(c.value, fieldType, c.operator);
          if (
            !NO_VALUE_OPERATORS.has(c.operator) &&
            (value === null || value === "" || (Array.isArray(value) && value.length === 0))
          ) {
            return null;
          }
          return {
            type: "rule" as const,
            field: c.field,
            comparator: c.operator,
            value,
          };
        })
        .filter((r): r is EngineRule => r !== null);
      if (rules.length === 0) return null;
      if (rules.length === 1) return rules[0] as EngineRule | EngineNode;
      return { operator: "AND", children: rules } as EngineNode;
    })
    .filter((g): g is EngineNode | EngineRule => g !== null);
  if (groups.length === 0) return {};
  if (groups.length === 1) return groups[0] as Record<string, unknown>;
  return { operator: "OR", children: groups } as Record<string, unknown>;
}

/** Best-effort conversion of an engine rules tree into the 2-level
 * card/condition shape. Anything that doesn't fit (NOT, 3+ levels of
 * nesting) gets flattened — the operator can edit the simplified
 * version; the original tree is preserved as long as they don't
 * change anything. */
export function deserializeTree(
  rules: Record<string, unknown>,
  fields: SegmentFieldDescriptor[],
): FilterTree {
  if (!rules || Object.keys(rules).length === 0) {
    return fields.length > 0 ? [emptyCardFor(fields[0])] : [];
  }
  const cards: FilterTree = [];
  const op = String((rules as EngineNode).operator ?? "").toUpperCase();
  if (op === "OR") {
    const children = ((rules as EngineNode).children ?? []) as Array<EngineNode | EngineRule>;
    for (const child of children) {
      cards.push(cardFromNode(child));
    }
  } else if (op === "AND" || op === "NOT") {
    cards.push(cardFromNode(rules as EngineNode));
  } else if ((rules as EngineRule).type === "rule") {
    cards.push({
      id: newId(),
      conditions: [conditionFromRule(rules as EngineRule)],
    });
  } else {
    // Unrecognised shape — drop back to a blank starter so the operator
    // sees something editable.
    return fields.length > 0 ? [emptyCardFor(fields[0])] : [];
  }
  if (cards.length === 0 && fields.length > 0) cards.push(emptyCardFor(fields[0]));
  return cards;
}

function cardFromNode(node: EngineNode | EngineRule): FilterCard {
  if ((node as EngineRule).type === "rule") {
    return { id: newId(), conditions: [conditionFromRule(node as EngineRule)] };
  }
  // AND group (or any group); flatten one level.
  const rules: EngineRule[] = [];
  const walk = (n: EngineNode | EngineRule): void => {
    if ((n as EngineRule).type === "rule") {
      rules.push(n as EngineRule);
      return;
    }
    for (const child of (n as EngineNode).children ?? []) {
      walk(child as EngineNode | EngineRule);
    }
  };
  walk(node);
  return {
    id: newId(),
    conditions: rules.map(conditionFromRule),
  };
}

function conditionFromRule(rule: EngineRule): FilterCondition {
  return {
    id: newId(),
    field: String(rule.field ?? ""),
    operator: String(rule.comparator ?? "eq"),
    value: rule.value ?? "",
  };
}

// ---------------------------------------------------------------------------
// React component
// ---------------------------------------------------------------------------

export function ContactFiltersBuilder({ rules, onChange }: Props) {
  const [fields, setFields] = useState<SegmentFieldDescriptor[]>([]);
  const [tree, setTree] = useState<FilterTree>([]);
  const lastEmitted = useRef<string>("{}");

  // Load the field catalogue once.
  useEffect(() => {
    listSegmentFields()
      .then((specs) => {
        setFields(specs);
        const initial = deserializeTree(rules, specs);
        setTree(initial.length > 0 ? initial : [emptyCardFor(specs[0])]);
      })
      .catch(() => setFields([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-sync when the parent loads a different view (rules prop changes
  // from outside). We compare against the last tree we emitted so a
  // half-typed value the operator is editing doesn't get wiped by the
  // round-trip.
  useEffect(() => {
    if (fields.length === 0) return;
    const incoming = JSON.stringify(rules ?? {});
    if (incoming === lastEmitted.current) return;
    setTree(deserializeTree(rules, fields));
    lastEmitted.current = incoming;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules]);

  function commit(next: FilterTree) {
    setTree(next);
    const serialized = serializeTree(next, fields);
    lastEmitted.current = JSON.stringify(serialized);
    onChange(serialized);
  }

  function updateCondition(
    cardId: string,
    conditionId: string,
    patch: Partial<FilterCondition>,
  ) {
    commit(
      tree.map((card) =>
        card.id === cardId
          ? {
              ...card,
              conditions: card.conditions.map((c) =>
                c.id === conditionId ? { ...c, ...patch } : c,
              ),
            }
          : card,
      ),
    );
  }

  function addConditionToCard(cardId: string) {
    if (fields.length === 0) return;
    commit(
      tree.map((card) =>
        card.id === cardId
          ? {
              ...card,
              conditions: [...card.conditions, emptyConditionFor(fields[0])],
            }
          : card,
      ),
    );
  }

  function addCard() {
    if (fields.length === 0) return;
    commit([...tree, emptyCardFor(fields[0])]);
  }

  function removeCondition(cardId: string, conditionId: string) {
    if (fields.length === 0) return;
    const nextCards = tree
      .map((card) =>
        card.id === cardId
          ? {
              ...card,
              conditions: card.conditions.filter((c) => c.id !== conditionId),
            }
          : card,
      )
      .filter((card) => card.conditions.length > 0);
    commit(nextCards.length > 0 ? nextCards : [emptyCardFor(fields[0])]);
  }

  if (fields.length === 0) {
    return <p className="muted">Cargando filtros…</p>;
  }

  return (
    <div className="filter-builder">
      {tree.map((card, idx) => (
        <div key={card.id}>
          {idx > 0 ? (
            <div className="filter-or-separator">
              <span>O</span>
            </div>
          ) : null}
          <FilterCardEditor
            card={card}
            fields={fields}
            onUpdateCondition={(cid, patch) => updateCondition(card.id, cid, patch)}
            onAddCondition={() => addConditionToCard(card.id)}
            onRemoveCondition={(cid) => removeCondition(card.id, cid)}
          />
        </div>
      ))}
      <div className="filter-add-card-row">
        <button
          type="button"
          className="filter-pill"
          onClick={addCard}
          aria-label="Añadir grupo alternativo"
        >
          <Plus size={12} aria-hidden /> O
        </button>
      </div>
    </div>
  );
}

function FilterCardEditor({
  card,
  fields,
  onUpdateCondition,
  onAddCondition,
  onRemoveCondition,
}: {
  card: FilterCard;
  fields: SegmentFieldDescriptor[];
  onUpdateCondition: (conditionId: string, patch: Partial<FilterCondition>) => void;
  onAddCondition: () => void;
  onRemoveCondition: (conditionId: string) => void;
}) {
  return (
    <div className="filter-card">
      {card.conditions.map((condition) => (
        <ConditionRow
          key={condition.id}
          condition={condition}
          fields={fields}
          onUpdate={(patch) => onUpdateCondition(condition.id, patch)}
          onRemove={() => onRemoveCondition(condition.id)}
        />
      ))}
      <button
        type="button"
        className="filter-pill filter-pill-y"
        onClick={onAddCondition}
        aria-label="Añadir condición"
      >
        <Plus size={12} aria-hidden /> Y
      </button>
    </div>
  );
}

function ConditionRow({
  condition,
  fields,
  onUpdate,
  onRemove,
}: {
  condition: FilterCondition;
  fields: SegmentFieldDescriptor[];
  onUpdate: (patch: Partial<FilterCondition>) => void;
  onRemove: () => void;
}) {
  const spec = fields.find((f) => f.key === condition.field) ?? fields[0];
  const operators = operatorsForField(spec);
  return (
    <div className="filter-condition">
      <select
        className="filter-field"
        value={spec.key}
        onChange={(e) => {
          const nextSpec = fields.find((f) => f.key === e.target.value) ?? fields[0];
          const nextOps = operatorsForField(nextSpec);
          const nextOp = nextOps[0]?.[0] ?? "eq";
          onUpdate({
            field: nextSpec.key,
            operator: nextOp,
            value: LIST_OPERATORS.has(nextOp) ? [] : "",
          });
        }}
      >
        {fields.map((field) => (
          <option key={field.key} value={field.key}>
            {field.label}
          </option>
        ))}
      </select>
      <select
        className="filter-operator"
        value={condition.operator}
        onChange={(e) => {
          const nextOp = e.target.value;
          // Reset the value shape if the new operator wants a different
          // container (list ↔ scalar).
          const wasList = LIST_OPERATORS.has(condition.operator);
          const isList = LIST_OPERATORS.has(nextOp);
          const isBetween = nextOp === "between";
          let nextValue = condition.value;
          if (wasList !== isList) nextValue = isList ? [] : "";
          if (isBetween && !Array.isArray(condition.value)) nextValue = ["", ""];
          onUpdate({ operator: nextOp, value: nextValue });
        }}
      >
        {operators.map(([op, label]) => (
          <option key={op} value={op}>
            {label}
          </option>
        ))}
      </select>
      {!NO_VALUE_OPERATORS.has(condition.operator) ? (
        <ValueInput
          spec={spec}
          operator={condition.operator}
          value={condition.value}
          onChange={(value) => onUpdate({ value })}
        />
      ) : (
        <span className="filter-value-placeholder">—</span>
      )}
      <button
        type="button"
        className="filter-remove"
        aria-label="Quitar condición"
        onClick={onRemove}
      >
        <X size={13} aria-hidden />
      </button>
    </div>
  );
}

function ValueInput({
  spec,
  operator,
  value,
  onChange,
}: {
  spec: SegmentFieldDescriptor;
  operator: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (operator === "between") {
    const arr = Array.isArray(value) ? value : ["", ""];
    return (
      <span className="filter-value-between">
        <ScalarInput
          spec={spec}
          value={arr[0] ?? ""}
          onChange={(v) => onChange([v, arr[1] ?? ""])}
        />
        <span className="muted small">y</span>
        <ScalarInput
          spec={spec}
          value={arr[1] ?? ""}
          onChange={(v) => onChange([arr[0] ?? "", v])}
        />
      </span>
    );
  }
  if (LIST_OPERATORS.has(operator)) {
    if (spec.type === "tag-multi") {
      return <TagPickerInput value={value} onChange={onChange} />;
    }
    return <CsvList spec={spec} value={value} onChange={onChange} />;
  }
  if (spec.type === "enum") {
    return (
      <select
        className="filter-value"
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Selecciona…</option>
        {spec.enum_values.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  if (spec.type === "bool") {
    return (
      <select
        className="filter-value"
        value={value === true || value === "true" ? "true" : "false"}
        onChange={(e) => onChange(e.target.value === "true")}
      >
        <option value="true">activo</option>
        <option value="false">inactivo</option>
      </select>
    );
  }
  return <ScalarInput spec={spec} value={value} onChange={onChange} />;
}

function ScalarInput({
  spec,
  value,
  onChange,
}: {
  spec: SegmentFieldDescriptor;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const safe = typeof value === "string" || typeof value === "number" ? String(value) : "";
  if (spec.type === "date") {
    return (
      <input
        type="date"
        className="filter-value"
        value={safe.slice(0, 10)}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (spec.type === "int") {
    return (
      <input
        type="number"
        className="filter-value"
        value={safe}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <input
      type="text"
      className="filter-value"
      value={safe}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Tag picker that resolves names to UUIDs.
 *
 * Bug fix: previously this slot used a CSV input expecting the
 * operator to type UUIDs by hand. When the operator typed tag
 * NAMES instead, the engine's `WHERE tag_id IN (...)` matched
 * zero rows; for `contains_none` that silently degraded into a
 * no-op (NOT IN empty-set ≡ TRUE) so the filter returned every
 * contact, including those carrying the tag. Reusing
 * `<TagMultiSelectFilter>` (same component segments use) emits
 * the right UUIDs.
 */
function TagPickerInput({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const selectedIds = useMemo(() => {
    if (!Array.isArray(value)) return [] as string[];
    return value.filter((item) => typeof item === "string") as string[];
  }, [value]);
  return (
    <TagMultiSelectFilter
      selectedIds={selectedIds}
      onChange={(next) => onChange(next)}
      placeholder="Buscar tag…"
    />
  );
}

/** Comma-separated multi-value input. Keeps the keystrokes responsive
 * (no chip rendering noise) for tag-multi / uuid-multi / enum-in
 * fields. */
function CsvList({
  spec,
  value,
  onChange,
}: {
  spec: SegmentFieldDescriptor;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const arr = Array.isArray(value) ? value.map(String) : [];
  const display = arr.join(", ");
  return (
    <input
      type="text"
      className="filter-value"
      value={display}
      placeholder={
        spec.type === "tag-multi" || spec.type === "uuid-multi"
          ? "separa UUIDs por coma"
          : "separa por coma"
      }
      onChange={(e) => {
        const items = e.target.value
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        onChange(items);
      }}
    />
  );
}
