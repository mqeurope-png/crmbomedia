"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  QueryBuilder,
  type ActionWithRulesProps,
  type Field,
  type RuleGroupType,
  type RuleType,
  type ValueEditorProps,
} from "react-querybuilder";
import "react-querybuilder/dist/query-builder.css";
import { listSegmentFields, type SegmentFieldDescriptor } from "../lib/api";
import {
  backendOpToQB,
  backendToQB,
  pruneRulesTree,
  qbOpToBackend,
  qbToBackend,
} from "../lib/segmentTranslator";
import { SegmentValueEditor } from "./SegmentValueEditor";

type Props = {
  rules: Record<string, unknown>;
  onChange: (rules: Record<string, unknown>) => void;
};

/** Spanish labels for the engine's comparators (shared vocabulary with
 * the /segments builder). */
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

/** Brevo-style query builder for the contacts list.
 *
 * Shape contract (mirrors Brevo's own filter UX):
 * - The ROOT group is an implicit OR of "cards".
 * - Each card is an implicit AND group of rules.
 * - There is NO combinator dropdown anywhere — the structure carries
 *   the semantics. "+ Y" (inside a card) adds an AND rule; "+ O"
 *   (below the cards) adds a new OR card with one starter rule.
 *
 * Two production lessons baked in:
 * - react-querybuilder v8 ignores a per-field `valueEditor` component
 *   (it only reads the `valueEditorType` STRING), so the typed
 *   SegmentValueEditor never mounted and every value went to the
 *   backend as a free-text string. The editor is now wired globally
 *   via `controlElements.valueEditor`, reading the field spec from
 *   `fieldData`.
 * - Empty/half-typed rules used to reach the backend and 400
 *   ("requires a non-empty list", "Expected int", "Expected ISO
 *   date"). `pruneRulesTree` now drops unfinished rules and coerces
 *   values to the engine's expected types before anything is emitted
 *   to the parent.
 */
export function ContactQueryBuilder({ rules, onChange }: Props) {
  const [fields, setFields] = useState<SegmentFieldDescriptor[]>([]);
  const [query, setQuery] = useState<RuleGroupType>(() =>
    toBrevoShape(
      rules && Object.keys(rules).length > 0
        ? backendToQB(rules)
        : { combinator: "or", rules: [] },
    ),
  );
  const fieldsRef = useRef<SegmentFieldDescriptor[]>([]);

  useEffect(() => {
    listSegmentFields()
      .then((specs) => {
        fieldsRef.current = specs;
        setFields(specs);
        // Empty builder → seed one card with one starter rule so the
        // operator lands on something editable (Brevo does the same).
        setQuery((current) => {
          if (current.rules.length > 0 || specs.length === 0) return current;
          return {
            combinator: "or",
            rules: [
              {
                combinator: "and",
                rules: [defaultRuleFor(specs[0])],
              },
            ],
          };
        });
      })
      .catch(() => setFields([]));
  }, []);

  // Re-sync when the parent loads a different saved view. Compare the
  // PRUNED projection of the current tree against the incoming prop —
  // the parent only ever stores pruned trees, so comparing raw would
  // wipe a half-typed rule the user is still editing.
  useEffect(() => {
    const incoming = JSON.stringify(rules ?? {});
    const current = JSON.stringify(
      pruneRulesTree(qbToBackend(query), fieldsRef.current),
    );
    if (incoming !== current) {
      setQuery(
        toBrevoShape(
          rules && Object.keys(rules).length > 0
            ? backendToQB(rules)
            : { combinator: "or", rules: [] },
        ),
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
      // Carried through to the global value editor via `fieldData`.
      // (A per-field `valueEditor` component is IGNORED by the
      // library — only controlElements.valueEditor renders.)
      spec,
    }));
  }, [fields]);

  function handleQbChange(next: RuleGroupType) {
    const shaped = toBrevoShape(next);
    setQuery(shaped);
    onChange(pruneRulesTree(qbToBackend(shaped), fieldsRef.current));
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
        addRuleToNewGroups
        translations={{
          addRule: { label: "+ Y", title: "Añadir condición (Y)" },
          addGroup: { label: "+ O", title: "Añadir grupo alternativo (O)" },
          removeRule: { label: "×", title: "Quitar condición" },
          removeGroup: { label: "×", title: "Quitar grupo" },
        }}
        controlElements={{
          // No combinator dropdown anywhere: position carries the
          // AND/OR semantics, like Brevo (Bug 1).
          combinatorSelector: null,
          valueEditor: TypedValueEditor,
          addRuleAction: AddRuleButton,
          addGroupAction: AddGroupButton,
        }}
        controlClassnames={{
          queryBuilder: "qb-root contact-qb-root",
          ruleGroup: "qb-group contact-qb-group",
          addRule: "button secondary small contact-qb-add-rule",
          addGroup: "button secondary small contact-qb-add-group",
          removeRule: "button secondary small contact-qb-remove",
          removeGroup: "button secondary small contact-qb-remove",
        }}
      />
      <p className="muted small contact-qb-hint">
        Las condiciones dentro de una tarjeta se combinan con <strong>Y</strong>;
        las tarjetas entre sí con <strong>O</strong>. Las condiciones sin valor
        se ignoran hasta que las completes.
      </p>
    </div>
  );
}

/** Global value editor: react-querybuilder hands us the field via
 * `fieldData`; we unwrap the engine spec and delegate to the shared
 * typed editor (tags picker / enum select / date input / number
 * input) used by the segments builder. */
function TypedValueEditor(props: ValueEditorProps) {
  const spec = (props.fieldData as { spec?: SegmentFieldDescriptor })?.spec;
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

/** "+ Y" — only inside cards (level > 0). The root group holds cards,
 * not loose rules, so adding a rule at root would break the
 * OR-of-ANDs shape. */
function AddRuleButton(props: ActionWithRulesProps) {
  if (props.level === 0) return null;
  return (
    <button
      type="button"
      className={props.className}
      title={props.title}
      onClick={(e) => props.handleOnClick(e)}
    >
      {props.label as string}
    </button>
  );
}

/** "+ O" — only at root. One level of nesting, like Brevo: cards
 * don't contain sub-groups. */
function AddGroupButton(props: ActionWithRulesProps) {
  if (props.level !== 0) return null;
  return (
    <button
      type="button"
      className={props.className}
      title={props.title}
      onClick={(e) => props.handleOnClick(e)}
    >
      {props.label as string}
    </button>
  );
}

function defaultRuleFor(spec: SegmentFieldDescriptor): RuleType {
  return {
    field: spec.key,
    operator: backendOpToQB(spec.comparators[0] ?? "eq"),
    value: "",
  };
}

/** Normalise any tree into the builder's canonical shape:
 * root = OR group whose children are AND "cards" of rules.
 *
 * - Loose rules at root get wrapped into their own card.
 * - Child groups keep their rules but their combinator is forced to
 *   "and" (the dropdown that used to change it is gone).
 * - Deeper nesting is flattened one level by lifting grandchildren —
 *   trees that deep can only come from the /segments advanced editor
 *   and the contacts builder intentionally simplifies them.
 */
export function toBrevoShape(group: RuleGroupType): RuleGroupType {
  const cards: RuleGroupType[] = [];
  let looseRules: RuleType[] = [];

  const flush = () => {
    if (looseRules.length > 0) {
      cards.push({ combinator: "and", rules: looseRules });
      looseRules = [];
    }
  };

  for (const item of group.rules) {
    if (typeof item === "string") continue; // independent combinators unused
    if ("rules" in item) {
      flush();
      const inner = (item.rules as Array<RuleType | RuleGroupType | string>)
        .flatMap((child) => {
          if (typeof child === "string") return [];
          if ("rules" in child) {
            // Flatten one nesting level: keep the grandchild rules.
            return (child.rules as Array<RuleType | string>).filter(
              (grand): grand is RuleType =>
                typeof grand !== "string" && !("rules" in grand),
            );
          }
          return [child];
        });
      cards.push({ combinator: "and", rules: inner });
    } else {
      looseRules.push(item);
    }
  }
  flush();

  return { combinator: "or", rules: cards };
}
