"use client";

/**
 * Sprint Filtros & Listas (PR-C) — `<EntityFilterBuilder>` genérico.
 *
 * Edita el árbol IR del motor (`{operator, children}` /
 * `{type: 'rule', field, comparator, value}`) usando `react-querybuilder`
 * como capa visual, alimentado por el `filter-schema` de la entidad.
 *
 * CRM-1.6 — capa de UX sobre el querybuilder (NO lo sustituye):
 *  - Los campos del selector se agrupan por `grouped_under` (`OptionGroup[]`).
 *  - Chip stack de filtros aplicados arriba + «Limpiar todo».
 *  - Accordion colapsable con las secciones del schema: cada sección lista sus
 *    campos como botones de «añadir filtro» y muestra el nº de filtros aplicados
 *    en esa sección. El estado abierto/cerrado se persiste en localStorage.
 *  - El `<QueryBuilder>` sigue siendo la superficie de EDICIÓN (valores, AND/OR).
 *
 * Decisión de la Parte B (2 opciones del spec): **Opción 2** — envolver el
 * querybuilder con optgroups + accordion-guía + chips, en vez de reimplementar
 * un panel de secciones con inputs inline (eso sería sustituir el querybuilder,
 * fuera de alcance). Usable sin reescribir la capa compartida por otras
 * entidades.
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
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
  /** CRM-1.6: clave localStorage del estado abierto/cerrado de secciones.
   *  Por entidad, para que /contacts y otras no se pisen. */
  sectionsStorageKey?: string;
  /** Secciones abiertas por defecto la primera vez (sin nada en localStorage). */
  defaultOpenSections?: string[];
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

let _ruleSeq = 0;

export function EntityFilterBuilder({
  fields,
  value,
  onChange,
  sectionsStorageKey = "crm-entity-filter-sections",
  defaultOpenSections = [],
}: Props) {
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

  // Parte B/D — estado abierto/cerrado de secciones, persistido.
  const [openSections, setOpenSections] = useState<Set<string>>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(sectionsStorageKey);
        if (raw) return new Set(JSON.parse(raw) as string[]);
      } catch {
        /* ignore */
      }
    }
    return new Set(defaultOpenSections);
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(
        sectionsStorageKey,
        JSON.stringify([...openSections]),
      );
    } catch {
      /* ignore */
    }
  }, [openSections, sectionsStorageKey]);

  function toggleSection(label: string) {
    setOpenSections((cur) => {
      const next = new Set(cur);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }

  function apply(next: RuleGroupType) {
    setQuery(next);
    onChange(qbToBackend(next));
  }

  const appliedRules = useMemo(() => flattenRules(query), [query]);

  // Nº de filtros aplicados por sección (para el contador de la cabecera).
  const countBySection = useMemo(() => {
    const counts = new Map<string, number>();
    for (const rule of appliedRules) {
      const spec = fieldsByKey.get(String(rule.field));
      const group = spec?.grouped_under || "Otros";
      counts.set(group, (counts.get(group) ?? 0) + 1);
    }
    return counts;
  }, [appliedRules, fieldsByKey]);

  function addFilter(spec: FieldDescriptor) {
    _ruleSeq += 1;
    const rule: RuleType = {
      id: `r-${_ruleSeq}-${spec.key}`,
      field: spec.key,
      operator: backendOpToQB(spec.comparators[0] ?? "eq"),
      value: "",
    };
    apply({ ...query, rules: [...query.rules, rule] });
  }

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
            {appliedRules.map((rule) => (
              <span key={rule.id} className="tag-chip efb-chip" role="listitem">
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

        {/* Parte B — accordion de secciones para añadir filtros. */}
        <div className="efb-sections">
          {groups.map((g) => {
            const open = openSections.has(g.label);
            const count = countBySection.get(g.label) ?? 0;
            return (
              <div key={g.label} className="efb-section">
                <button
                  type="button"
                  className="efb-section-header"
                  aria-expanded={open}
                  onClick={() => toggleSection(g.label)}
                >
                  <span>{open ? "▾" : "▸"}</span>
                  <span className="efb-section-title">{g.label}</span>
                  <span
                    className={count > 0 ? "efb-section-count is-active" : "efb-section-count muted"}
                  >
                    ({count})
                  </span>
                </button>
                {open ? (
                  <div className="efb-section-body">
                    {g.fields.map((spec) => (
                      <button
                        key={spec.key}
                        type="button"
                        className="button small secondary efb-add-field"
                        onClick={() => addFilter(spec)}
                      >
                        + {spec.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>

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
