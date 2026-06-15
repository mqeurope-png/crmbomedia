"use client";

/**
 * Sprint Filtros & Listas (PR-C) — `<EntityFilterBuilder>` genérico.
 *
 * Edita el árbol IR del motor (`{operator, children}` /
 * `{type: 'rule', field, comparator, value}`) usando
 * `react-querybuilder` como capa visual. Está alimentado por el
 * `filter-schema` de la entidad (lista de `FieldDescriptor` con
 * comparators + enum_values).
 *
 * Es un derivado más limpio de `SegmentRuleBuilder` (que es contact-only
 * y conserva un modo "simple" de 2 niveles): este builder es siempre
 * "advanced" — AND/OR/NOT con anidamiento arbitrario sin pérdida en el
 * round-trip, vía el `segmentTranslator` ya existente.
 *
 * El componente es controlado solo en su mounting: el `value` siembra
 * el árbol al inicio; cambios posteriores del padre se ignoran. Para
 * forzar un árbol nuevo (cargar vista guardada, cambiar de entidad),
 * el consumer monta con `key={someStableId}` (sandbox lo hace con
 * `key={`${entity}:${activeViewId}`}`). Sin esto, cada re-render del
 * padre forzaba un setQuery → remount del input → pérdida de foco.
 *
 * PR-Cd: el editor de valores se inyecta por `controlElements`
 * (global, vía React Context con el mapa de specs) en lugar de
 * `Field.valueEditor` per-field. El patrón global garantiza que RQB
 * use NUESTRO editor para CUALQUIER operador, incluidos los nombres
 * custom como `contains_any` / `tag_name_contains` — los operadores
 * built-in de RQB (`=`, `in`, `null`, …) tienen tratamiento especial
 * y a veces bypassean el editor per-field.
 */
import { createContext, useContext, useMemo, useState } from "react";
import {
  QueryBuilder,
  type Field,
  type RuleGroupType,
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

// Mapa de field-key → FieldDescriptor para que el editor global sepa
// qué tipo / enum / comparator está editando. Context para evitar
// recrear el componente editor en cada render (la identidad estable es
// lo que permite que RQB no remontee el input en cada keystroke).
const FieldsByKeyContext = createContext<Map<string, FieldDescriptor>>(
  new Map(),
);

function GlobalValueEditor(props: ValueEditorProps) {
  const fieldsByKey = useContext(FieldsByKeyContext);
  const spec = fieldsByKey.get(String(props.field));
  if (!spec) {
    // Fallback al input por defecto de RQB si el field no está en el
    // schema (no debería pasar — `qbFields` se deriva del mismo array).
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

export function EntityFilterBuilder({ fields, value, onChange }: Props) {
  const [query, setQuery] = useState<RuleGroupType>(() =>
    value && Object.keys(value).length ? backendToQB(value) : EMPTY_QB_GROUP,
  );

  const fieldsByKey = useMemo(
    () => new Map(fields.map((spec) => [spec.key, spec])),
    [fields],
  );

  const qbFields: Field[] = useMemo(() => {
    return fields
      .filter((spec) => spec.filterable)
      .map((spec) => ({
        name: spec.key,
        label: spec.label,
        group: spec.grouped_under,
        operators: spec.comparators.map((c) => ({
          name: backendOpToQB(c),
          label: COMPARATOR_LABELS[c] ?? c,
        })),
      }));
  }, [fields]);

  function handleQbChange(next: RuleGroupType) {
    setQuery(next);
    onChange(qbToBackend(next));
  }

  if (fields.length === 0) {
    return <p className="muted">Cargando campos…</p>;
  }

  return (
    <FieldsByKeyContext.Provider value={fieldsByKey}>
      <div className="entity-filter-builder">
        <QueryBuilder
          fields={qbFields}
          query={query}
          onQueryChange={(next) => handleQbChange(next as RuleGroupType)}
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
