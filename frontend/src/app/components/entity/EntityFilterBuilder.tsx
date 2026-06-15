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
 * El componente es controlado: la pantalla mantiene `value`
 * (árbol IR) y le pasa `onChange` con cada edición. La pantalla decide
 * cuándo persistir (en `entity_views.filters_json.rules_json` o en
 * estado local).
 */
import { useMemo, useState } from "react";
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
  /** Initial IR tree. CHANGES TO `value` AFTER MOUNT ARE IGNORED on
   * purpose — see the comment below for why. To force the builder to
   * adopt a different tree (e.g. when loading a saved view), remount
   * the component via `key={someStableId}`. */
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

export function EntityFilterBuilder({ fields, value, onChange }: Props) {
  // PR-Cc fix: the builder is "uncontrolled with initialValue". The
  // `value` prop seeds the RQB query at mount time only; further parent
  // updates are NOT pushed back into RQB state. Without this rule, the
  // previous `useEffect(..., [value])` re-sync remounted every value
  // input on every keystroke (parent → setRules → setQuery → React
  // remount → cursor lost). The parent forces a fresh tree by changing
  // the component's `key`.
  const [query, setQuery] = useState<RuleGroupType>(() =>
    value && Object.keys(value).length ? backendToQB(value) : EMPTY_QB_GROUP,
  );

  const qbFields: Field[] = useMemo(() => {
    return fields
      .filter((spec) => spec.filterable)
      .map((spec) => ({
        name: spec.key,
        label: spec.label,
        // RQB groups by the first field's `group` (string) when set.
        group: spec.grouped_under,
        operators: spec.comparators.map((c) => ({
          name: backendOpToQB(c),
          label: COMPARATOR_LABELS[c] ?? c,
        })),
        // Reuse the segment value editor — `FieldDescriptor` is a
        // superset of `SegmentFieldDescriptor` so it accepts the new
        // shape unchanged (key/label/type/comparators/enum_values are
        // identical; extra columns are ignored).
        valueEditor: (props: ValueEditorProps) => (
          <SegmentValueEditor
            spec={spec}
            comparator={qbOpToBackend(props.operator)}
            value={props.value}
            onChange={props.handleOnChange}
          />
        ),
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
    <div className="entity-filter-builder">
      <QueryBuilder
        fields={qbFields}
        query={query}
        onQueryChange={(next) => handleQbChange(next as RuleGroupType)}
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
  );
}
