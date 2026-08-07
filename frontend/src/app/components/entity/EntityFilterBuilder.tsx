"use client";

/**
 * Sprint Filtros & Listas (PR-C) — `<EntityFilterBuilder>` genérico.
 *
 * Edita el árbol IR del motor (`{operator, children}` /
 * `{type: 'rule', field, comparator, value}`) usando `react-querybuilder`
 * como capa visual, alimentado por el `filter-schema` de la entidad.
 *
 * CRM-1.6 — capa de UX sobre el querybuilder (NO lo sustituye):
 *  - Los campos del selector se agrupan por `grouped_under` (`OptionGroup[]`),
 *    de modo que el desplegable de campo ya sale ordenado por sección.
 *  - Chip stack de filtros aplicados arriba + «Limpiar todo».
 *  - El `<QueryBuilder>` sigue siendo la superficie de EDICIÓN (valores, AND/OR).
 *
 * CRM-1.6-fix1 — retirado el panel accordion-guía que añadía CRM-1.6: con el
 * desplegable ya agrupado por `<optgroup>` (Parte A) esa capa sólo duplicaba
 * la selección de campo y metía ruido. Se conservan optgroups + chip stack.
 */
import { createContext, useContext, useMemo, useState } from "react";
import {
  QueryBuilder,
  type Field,
  type OptionGroup,
  type RuleGroupType,
  type RuleType,
  type ValueEditorProps,
} from "react-querybuilder";
import "react-querybuilder/dist/query-builder.css";
import type { FieldDescriptor } from "../../lib/entitySchema";
import {
  backendOpToQB,
  backendToQB,
  EMPTY_QB_GROUP,
  qbOpToBackend,
  qbToBackend,
} from "../../lib/segmentTranslator";
import { SegmentValueEditor } from "../SegmentValueEditor";

type Props = {
  fields: FieldDescriptor[];
  /** Initial IR tree. Cambios posteriores son ignorados — ver doc. */
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

const COMPARATOR_LABELS: Record<string, string> = {
  eq: "es igual a",
  neq: "no es igual a",
  contains: "contiene",
  not_contains: "no contiene",
  starts_with: "empieza por",
  ends_with: "termina por",
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
  older_than_n_days: "hace más de N días",
  contains_any: "incluye alguno",
  contains_all: "incluye todos",
  contains_none: "no incluye ninguno",
  tag_name_contains: "tag cuyo nombre contiene",
};

const FieldsByKeyContext = createContext<Map<string, FieldDescriptor>>(
  new Map(),
);

function GlobalValueEditor(props: ValueEditorProps) {
  const fieldsByKey = useContext(FieldsByKeyContext);
  const spec = fieldsByKey.get(String(props.field));
  if (!spec) {
    return (
      <input
        type="text"
        className="qb-value"
        value={typeof props.value === "string" ? props.value : ""}
        onChange={(event) => props.handleOnChange(event.target.value)}
      />
    );
  }
  return (
    <SegmentValueEditor
      spec={spec}
      comparator={qbOpToBackend(props.operator)}
      value={props.value}
      onChange={props.handleOnChange}
    />
  );
}

/** Campos filtrables agrupados por `grouped_under`, en orden de aparición. */
function groupFields(
  fields: FieldDescriptor[],
): { label: string; fields: FieldDescriptor[] }[] {
  const order: string[] = [];
  const byGroup = new Map<string, FieldDescriptor[]>();
  for (const spec of fields) {
    if (!spec.filterable) continue;
    const group = spec.grouped_under || "Otros";
    if (!byGroup.has(group)) {
      order.push(group);
      byGroup.set(group, []);
    }
    byGroup.get(group)!.push(spec);
  }
  return order.map((label) => ({ label, fields: byGroup.get(label)! }));
}

/** Aplana el árbol RQB a la lista de reglas hoja (con su `id`). */
function flattenRules(group: RuleGroupType): RuleType[] {
  const out: RuleType[] = [];
  for (const r of group.rules) {
    if (r && typeof r === "object" && "rules" in r) {
      out.push(...flattenRules(r as RuleGroupType));
    } else if (r && typeof r === "object" && "field" in r) {
      out.push(r as RuleType);
    }
  }
  return out;
}

/** Devuelve una copia del árbol sin la regla `id` (recursivo). */
function removeRuleById(group: RuleGroupType, id: string): RuleGroupType {
  return {
    ...group,
    rules: group.rules
      .filter((r) => !(r && typeof r === "object" && "id" in r && r.id === id))
      .map((r) =>
        r && typeof r === "object" && "rules" in r
          ? removeRuleById(r as RuleGroupType, id)
          : r,
      ),
  };
}

export function EntityFilterBuilder({ fields, value, onChange }: Props) {
  const [query, setQuery] = useState<RuleGroupType>(() =>
    value && Object.keys(value).length ? backendToQB(value) : EMPTY_QB_GROUP,
  );

  const fieldsByKey = useMemo(
    () => new Map(fields.map((spec) => [spec.key, spec])),
    [fields],
  );

  const groups = useMemo(() => groupFields(fields), [fields]);

  // Parte A — el selector de campo del querybuilder agrupa por sección.
  const qbFields = useMemo<OptionGroup<Field>[]>(
    () =>
      groups.map((g) => ({
        label: g.label,
        options: g.fields.map((spec) => ({
          name: spec.key,
          label: spec.label,
          operators: spec.comparators.map((c) => ({
            name: backendOpToQB(c),
            label: COMPARATOR_LABELS[c] ?? c,
          })),
        })),
      })),
    [groups],
  );

  function apply(next: RuleGroupType) {
    setQuery(next);
    onChange(qbToBackend(next));
  }

  const appliedRules = useMemo(() => flattenRules(query), [query]);

  function removeChip(id: string) {
    apply(removeRuleById(query, id));
  }

  function clearAll() {
    apply({ ...EMPTY_QB_GROUP, rules: [] });
  }

  function chipLabel(rule: RuleType): string {
    const spec = fieldsByKey.get(String(rule.field));
    const field = spec?.label ?? String(rule.field);
    const op = COMPARATOR_LABELS[qbOpToBackend(rule.operator)] ?? rule.operator;
    const val = Array.isArray(rule.value)
      ? rule.value.join(" – ")
      : rule.value === "" || rule.value == null
        ? "…"
        : String(rule.value);
    return `${field} ${op} ${val}`;
  }

  if (fields.length === 0) {
    return <p className="muted">Cargando campos…</p>;
  }

  return (
    <FieldsByKeyContext.Provider value={fieldsByKey}>
      <div className="entity-filter-builder">
        {/* Parte C — chip stack de filtros aplicados. */}
        {appliedRules.length > 0 ? (
          <div className="efb-chips" role="list" aria-label="Filtros aplicados">
            <span className="muted small">Filtros:</span>
            {appliedRules.map((rule, idx) => (
              <span
                key={rule.id ?? `${rule.field}-${idx}`}
                className="tag-chip efb-chip"
                role="listitem"
              >
                {chipLabel(rule)}
                <button
                  type="button"
                  className="tag-chip-remove"
                  aria-label={`Quitar filtro ${chipLabel(rule)}`}
                  onClick={() => removeChip(String(rule.id))}
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="button small secondary efb-clear-all"
              onClick={clearAll}
            >
              Limpiar todo
            </button>
          </div>
        ) : null}

        {/* Superficie de edición: valores + AND/OR. */}
        <QueryBuilder
          fields={qbFields}
          query={query}
          onQueryChange={(next) => apply(next as RuleGroupType)}
          controlElements={{ valueEditor: GlobalValueEditor }}
          controlClassnames={{
            queryBuilder: "qb-root",
            ruleGroup: "qb-group",
            combinators: "qb-combinator",
            addRule: "button secondary small",
            addGroup: "button secondary small",
            removeRule: "button secondary small",
            removeGroup: "button secondary small",
          }}
        />
      </div>
    </FieldsByKeyContext.Provider>
  );
}
