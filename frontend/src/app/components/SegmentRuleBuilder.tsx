"use client";

import { useEffect, useMemo, useState } from "react";
import { QueryBuilder, type Field, type RuleGroupType } from "react-querybuilder";
import "react-querybuilder/dist/query-builder.css";
import { listSegmentFields, type SegmentFieldDescriptor } from "../lib/api";
import {
  backendOpToQB,
  backendToQB,
  EMPTY_QB_GROUP,
  qbToBackend,
} from "../lib/segmentTranslator";

type Props = {
  initialRules: Record<string, unknown>;
  onChange: (rules: Record<string, unknown>) => void;
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

export function SegmentRuleBuilder({ initialRules, onChange }: Props) {
  const [fields, setFields] = useState<SegmentFieldDescriptor[]>([]);
  const [query, setQuery] = useState<RuleGroupType>(() =>
    initialRules ? backendToQB(initialRules) : EMPTY_QB_GROUP,
  );

  useEffect(() => {
    listSegmentFields()
      .then(setFields)
      .catch(() => setFields([]));
  }, []);

  // Whenever the operator edits the tree, translate back to the
  // backend shape and bubble up.
  useEffect(() => {
    onChange(qbToBackend(query));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const qbFields: Field[] = useMemo(() => {
    return fields.map((spec) => ({
      name: spec.key,
      label: spec.label,
      operators: spec.comparators.map((c) => ({
        name: backendOpToQB(c),
        label: COMPARATOR_LABELS[c] ?? c,
      })),
      valueEditorType:
        spec.type === "bool"
          ? "checkbox"
          : spec.enum_values.length > 0
            ? "select"
            : spec.type === "int" || spec.type === "date"
              ? "text"
              : "text",
      values: spec.enum_values.length
        ? spec.enum_values.map((value) => ({ name: value, label: value }))
        : undefined,
      inputType:
        spec.type === "int"
          ? "number"
          : spec.type === "date"
            ? "date"
            : "text",
    }));
  }, [fields]);

  if (fields.length === 0) {
    return <p className="muted">Cargando campos disponibles…</p>;
  }

  return (
    <div className="segment-builder">
      <QueryBuilder
        fields={qbFields}
        query={query}
        onQueryChange={(next) => setQuery(next as RuleGroupType)}
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
