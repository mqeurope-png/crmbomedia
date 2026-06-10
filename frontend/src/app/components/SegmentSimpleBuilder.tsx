"use client";

import { useMemo } from "react";
import type { SegmentFieldDescriptor } from "../lib/api";
import { SegmentValueEditor } from "./SegmentValueEditor";

/**
 * Flat-list rule builder inspired by Brevo / ActiveCampaign: one row
 * per condition, a global AND/OR toggle on top, "+ Añadir condición"
 * at the bottom. Designed for the 90% of segments that don't need
 * nested groups; complex trees fall back to the advanced builder
 * (`SegmentRuleBuilder` detects shape and routes accordingly).
 */
type Props = {
  fields: SegmentFieldDescriptor[];
  rules: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

const COMPARATOR_LABELS: Record<string, string> = {
  eq: "es igual a",
  neq: "no es igual a",
  contains: "contiene",
  not_contains: "no contiene",
  starts_with: "empieza por",
  is_null: "está vacío",
  is_not_null: "no está vacío",
  in: "es uno de",
  not_in: "no es ninguno de",
  gt: "mayor que",
  gte: "mayor o igual",
  lt: "menor que",
  lte: "menor o igual",
  between: "entre",
  before: "antes de",
  after: "después de",
  in_last_n_days: "en los últimos N días",
  not_in_last_n_days: "fuera de los últimos N días",
  contains_any: "incluye alguno",
  contains_all: "incluye todos",
  contains_none: "no incluye ninguno",
};

type FlatRow = {
  field: string;
  comparator: string;
  value: unknown;
};

export function SegmentSimpleBuilder({ fields, rules, onChange }: Props) {
  const { combinator, rows } = useMemo(() => parseFlat(rules), [rules]);

  function emit(nextCombinator: "AND" | "OR", nextRows: FlatRow[]) {
    onChange(serializeFlat(nextCombinator, nextRows));
  }

  function setCombinator(next: "AND" | "OR") {
    emit(next, rows);
  }

  function addRow() {
    if (fields.length === 0) return;
    const first = fields[0];
    const initialComparator = first.comparators[0];
    emit(combinator, [
      ...rows,
      {
        field: first.key,
        comparator: initialComparator,
        value: initialValueForComparator(first, initialComparator),
      },
    ]);
  }

  function updateRow(index: number, patch: Partial<FlatRow>) {
    const next = rows.map((row, idx) => {
      if (idx !== index) return row;
      const merged = { ...row, ...patch };
      // Field changed → pick a sensible comparator + reset value to
      // the right shape so we don't carry an array into a string-only
      // comparator (or vice versa).
      if (patch.field) {
        const spec = fields.find((f) => f.key === patch.field);
        if (spec) {
          const comparator = spec.comparators.includes(merged.comparator)
            ? merged.comparator
            : spec.comparators[0];
          return {
            field: patch.field,
            comparator,
            value: initialValueForComparator(spec, comparator),
          };
        }
      }
      if (patch.comparator) {
        const spec = fields.find((f) => f.key === merged.field);
        if (spec) {
          merged.value = initialValueForComparator(spec, patch.comparator);
        }
      }
      return merged;
    });
    emit(combinator, next);
  }

  function deleteRow(index: number) {
    emit(
      combinator,
      rows.filter((_, idx) => idx !== index),
    );
  }

  return (
    <div className="simple-builder">
      <div className="simple-builder-toolbar">
        <span className="muted small">Coincidir</span>
        <button
          type="button"
          className={`button small ${combinator === "AND" ? "" : "secondary"}`}
          onClick={() => setCombinator("AND")}
        >
          todas (AND)
        </button>
        <button
          type="button"
          className={`button small ${combinator === "OR" ? "" : "secondary"}`}
          onClick={() => setCombinator("OR")}
        >
          cualquiera (OR)
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="muted small">
          Sin condiciones. Sin filtros = todos los contactos.
        </p>
      ) : (
        <ul className="simple-builder-list">
          {rows.map((row, index) => {
            const spec = fields.find((f) => f.key === row.field);
            return (
              <li key={index} className="simple-builder-row">
                <select
                  className="qb-value"
                  value={row.field}
                  onChange={(event) =>
                    updateRow(index, { field: event.target.value })
                  }
                >
                  {fields.map((f) => (
                    <option key={f.key} value={f.key}>
                      {f.label}
                    </option>
                  ))}
                </select>
                <select
                  className="qb-value"
                  value={row.comparator}
                  onChange={(event) =>
                    updateRow(index, { comparator: event.target.value })
                  }
                >
                  {(spec?.comparators ?? []).map((c) => (
                    <option key={c} value={c}>
                      {COMPARATOR_LABELS[c] ?? c}
                    </option>
                  ))}
                </select>
                <div className="simple-builder-value">
                  {spec ? (
                    <SegmentValueEditor
                      spec={spec}
                      comparator={row.comparator}
                      value={row.value}
                      onChange={(value) => updateRow(index, { value })}
                    />
                  ) : (
                    <span className="muted small">Campo desconocido</span>
                  )}
                </div>
                <button
                  type="button"
                  className="button secondary small"
                  aria-label="Eliminar condición"
                  onClick={() => deleteRow(index)}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="form-actions">
        <button
          type="button"
          className="button secondary small"
          onClick={addRow}
        >
          + Añadir condición
        </button>
      </div>
    </div>
  );
}

// ---------- tree shape helpers ----------

export function isSimpleTree(tree: Record<string, unknown> | null | undefined): boolean {
  if (!tree || Object.keys(tree).length === 0) return true;
  if (tree.type === "rule") return true;
  if (tree.not === true || (typeof tree.operator === "string" && tree.operator.toUpperCase() === "NOT")) {
    return false;
  }
  const operator = typeof tree.operator === "string" ? tree.operator.toUpperCase() : null;
  if (operator !== "AND" && operator !== "OR") return false;
  const children = Array.isArray(tree.children) ? tree.children : [];
  return children.every((child) => {
    return (
      child &&
      typeof child === "object" &&
      (child as { type?: string }).type === "rule"
    );
  });
}

function parseFlat(tree: Record<string, unknown> | null | undefined): {
  combinator: "AND" | "OR";
  rows: FlatRow[];
} {
  if (!tree || Object.keys(tree).length === 0) {
    return { combinator: "AND", rows: [] };
  }
  if (tree.type === "rule") {
    return {
      combinator: "AND",
      rows: [
        {
          field: String(tree.field ?? ""),
          comparator: String(tree.comparator ?? "eq"),
          value: tree.value,
        },
      ],
    };
  }
  const operator =
    typeof tree.operator === "string" && tree.operator.toUpperCase() === "OR"
      ? "OR"
      : "AND";
  const children = Array.isArray(tree.children) ? tree.children : [];
  const rows = children
    .filter((child): child is Record<string, unknown> => Boolean(child) && typeof child === "object")
    .filter((child) => child.type === "rule")
    .map((child) => ({
      field: String(child.field ?? ""),
      comparator: String(child.comparator ?? "eq"),
      value: child.value,
    }));
  return { combinator: operator as "AND" | "OR", rows };
}

function serializeFlat(
  combinator: "AND" | "OR",
  rows: FlatRow[],
): Record<string, unknown> {
  if (rows.length === 0) return {};
  if (rows.length === 1) {
    return {
      type: "rule",
      field: rows[0].field,
      comparator: rows[0].comparator,
      value: rows[0].value,
    };
  }
  return {
    operator: combinator,
    children: rows.map((row) => ({
      type: "rule",
      field: row.field,
      comparator: row.comparator,
      value: row.value,
    })),
  };
}

function initialValueForComparator(
  spec: SegmentFieldDescriptor,
  comparator: string,
): unknown {
  if (comparator === "is_null" || comparator === "is_not_null") return null;
  if (
    [
      "in",
      "not_in",
      "contains_any",
      "contains_all",
      "contains_none",
    ].includes(comparator)
  ) {
    return [];
  }
  if (comparator === "between") return [null, null];
  if (comparator === "in_last_n_days" || comparator === "not_in_last_n_days") {
    return 30;
  }
  if (spec.type === "bool") return true;
  if (spec.type === "int") return 0;
  if (spec.type === "date") return "";
  if (spec.enum_values.length > 0) return "";
  return "";
}
