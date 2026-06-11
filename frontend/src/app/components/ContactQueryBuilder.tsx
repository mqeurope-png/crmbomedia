"use client";

import { useEffect, useMemo, useState } from "react";
import {
  QueryBuilder,
  type Field,
  type RuleGroupType,
  type ValueEditorProps,
} from "react-querybuilder";
import "react-querybuilder/dist/query-builder.css";
import { listSegmentFields, type SegmentFieldDescriptor } from "../lib/api";
import {
  backendOpToQB,
  backendToQB,
  EMPTY_QB_GROUP,
  qbOpToBackend,
  qbToBackend,
} from "../lib/segmentTranslator";
import { SegmentValueEditor } from "./SegmentValueEditor";

type Props = {
  rules: Record<string, unknown>;
  onChange: (rules: Record<string, unknown>) => void;
};

/** Spanish translations for the segment engine's comparators.
 * Reused (and extended) from the SegmentRuleBuilder so the two builders
 * speak the same language — the operator already uses these labels in
 * `/segments`. */
const COMPARATOR_LABELS: Record<string, string> = {
  eq: "es exactamente",
  neq: "no es",
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
  contains_any: "incluye alguno de",
  contains_all: "incluye todos de",
  contains_none: "no incluye ninguno de",
};

/** Brevo-style query builder for the contacts list. Wraps
 * react-querybuilder with the segments-engine field whitelist + the
 * shared typed value editor, plus a small Spanish translation layer
 * so the operator sees "Y" / "O" / "AÑADIR REGLA" / "AÑADIR GRUPO"
 * instead of the library defaults.
 *
 * The component stays controlled: every change converts the QB tree
 * to the backend `rules_json` shape and bubbles it up through
 * `onChange`. The parent owns persistence to the URL + the search
 * API call.
 */
export function ContactQueryBuilder({ rules, onChange }: Props) {
  const [fields, setFields] = useState<SegmentFieldDescriptor[]>([]);
  const [query, setQuery] = useState<RuleGroupType>(() =>
    rules && Object.keys(rules).length > 0 ? backendToQB(rules) : EMPTY_QB_GROUP,
  );

  useEffect(() => {
    listSegmentFields()
      .then(setFields)
      .catch(() => setFields([]));
  }, []);

  // Re-sync the QB tree when the parent loads a different saved view
  // (the rules prop changes from outside). Comparing JSON identities
  // avoids the infinite loop a deep-equality check would cause when
  // the user types into a value editor.
  useEffect(() => {
    const incoming = JSON.stringify(rules ?? {});
    const current = JSON.stringify(qbToBackend(query));
    if (incoming !== current) {
      setQuery(
        rules && Object.keys(rules).length > 0
          ? backendToQB(rules)
          : EMPTY_QB_GROUP,
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules]);

  const qbFields: Field[] = useMemo(() => {
    return fields.map((spec) => ({
      name: spec.key,
      label: spec.label,
      operators: spec.comparators.map((c) => ({
        name: backendOpToQB(c),
        label: COMPARATOR_LABELS[c] ?? c,
      })),
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
    return <p className="muted">Cargando filtros…</p>;
  }

  return (
    <div className="contact-query-builder">
      <QueryBuilder
        fields={qbFields}
        query={query}
        onQueryChange={(next) => handleQbChange(next as RuleGroupType)}
        translations={{
          addRule: { label: "+ Y" },
          addGroup: { label: "+ O" },
          removeRule: { label: "×", title: "Quitar regla" },
          removeGroup: { label: "×", title: "Quitar grupo" },
        }}
        controlClassnames={{
          queryBuilder: "qb-root contact-qb-root",
          ruleGroup: "qb-group contact-qb-group",
          combinators: "qb-combinator contact-qb-combinator",
          addRule: "button secondary small contact-qb-add-rule",
          addGroup: "button secondary small contact-qb-add-group",
          removeRule: "button secondary small contact-qb-remove",
          removeGroup: "button secondary small contact-qb-remove",
        }}
      />
    </div>
  );
}
