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
import { isSimpleTree, SegmentSimpleBuilder } from "./SegmentSimpleBuilder";
import { SegmentValueEditor } from "./SegmentValueEditor";

type Props = {
  initialRules: Record<string, unknown>;
  onChange: (rules: Record<string, unknown>) => void;
};

type Mode = "simple" | "advanced";

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

const MODE_STORAGE_KEY = "crmbomedia_segment_builder_mode";

export function SegmentRuleBuilder({ initialRules, onChange }: Props) {
  const [fields, setFields] = useState<SegmentFieldDescriptor[]>([]);
  // The advanced view's state is owned by react-querybuilder; the
  // simple view drives the backend tree directly. Both flow through
  // `currentRules` so switching modes doesn't lose work.
  const [currentRules, setCurrentRules] = useState<Record<string, unknown>>(
    initialRules ?? {},
  );
  const [query, setQuery] = useState<RuleGroupType>(() =>
    initialRules ? backendToQB(initialRules) : EMPTY_QB_GROUP,
  );
  // The mode preference persists per-browser. New trees that already
  // need nesting (e.g. saved with the advanced view) auto-bump the
  // user into advanced so they don't see their groups flattened away.
  const [mode, setMode] = useState<Mode>(() => readStoredMode(initialRules));

  useEffect(() => {
    listSegmentFields()
      .then(setFields)
      .catch(() => setFields([]));
  }, []);

  useEffect(() => {
    onChange(currentRules);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRules]);

  // Keep the QB query in sync when the simple builder edits the tree.
  useEffect(() => {
    if (mode !== "simple") return;
    setQuery(backendToQB(currentRules));
  }, [currentRules, mode]);

  const qbFields: Field[] = useMemo(() => {
    return fields.map((spec) => ({
      name: spec.key,
      label: spec.label,
      operators: spec.comparators.map((c) => ({
        name: backendOpToQB(c),
        label: COMPARATOR_LABELS[c] ?? c,
      })),
      // Typed value editor injected via react-querybuilder's per-field
      // override. Centralises every UUID / enum / date / bool concern
      // in one component so the backend never receives a free-form
      // string where it expects a list of tag IDs.
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

  function switchMode(next: Mode) {
    setMode(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODE_STORAGE_KEY, next);
    }
  }

  function handleQbChange(next: RuleGroupType) {
    setQuery(next);
    setCurrentRules(qbToBackend(next));
  }

  function handleSimpleChange(next: Record<string, unknown>) {
    setCurrentRules(next);
  }

  if (fields.length === 0) {
    return <p className="muted">Cargando campos disponibles…</p>;
  }

  const canUseSimple = isSimpleTree(currentRules);

  return (
    <div className="segment-builder">
      <div className="segment-builder-modes">
        <button
          type="button"
          className={`button small ${mode === "simple" ? "" : "secondary"}`}
          onClick={() => switchMode("simple")}
          disabled={!canUseSimple}
          title={
            canUseSimple
              ? "Vista simple"
              : "Este segmento usa grupos anidados o NOT — sólo editable en vista avanzada"
          }
        >
          Vista simple
        </button>
        <button
          type="button"
          className={`button small ${mode === "advanced" ? "" : "secondary"}`}
          onClick={() => switchMode("advanced")}
        >
          Vista avanzada
        </button>
        {!canUseSimple ? (
          <span className="muted small">
            Reglas con grupos anidados o NOT requieren la vista avanzada.
          </span>
        ) : null}
      </div>

      {mode === "simple" && canUseSimple ? (
        <SegmentSimpleBuilder
          fields={fields}
          rules={currentRules}
          onChange={handleSimpleChange}
        />
      ) : (
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
      )}
    </div>
  );
}

function readStoredMode(initialRules: Record<string, unknown>): Mode {
  // Trees that aren't simple must boot into advanced regardless of
  // what the user last picked, otherwise the simple builder would
  // silently drop the nested groups on the first save.
  if (!isSimpleTree(initialRules)) return "advanced";
  if (typeof window === "undefined") return "simple";
  const stored = window.localStorage.getItem(MODE_STORAGE_KEY);
  return stored === "advanced" ? "advanced" : "simple";
}
