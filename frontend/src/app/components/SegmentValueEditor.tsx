"use client";

import { useEffect, useMemo, useState } from "react";
import {
  listPipelines,
  listSegmentAvailableCountries,
  listSegmentAvailableOriginAccounts,
  type Pipeline,
  type SegmentCountryOption,
  type SegmentFieldDescriptor,
  type SegmentOriginAccountOption,
} from "../lib/api";
import { TagMultiSelectFilter } from "./TagMultiSelectFilter";

/**
 * Human-readable labels for the `origin_system` enum. The backend
 * stores slugs (`agilecrm`, `brevo`, …); the picker needs to show
 * something operators can recognise without learning the integration
 * codename.
 */
const ORIGIN_SYSTEM_LABELS: Record<string, string> = {
  agilecrm: "AgileCRM",
  brevo: "Brevo",
  freshdesk: "Freshdesk",
  factusol: "FactuSOL",
  manual: "Manual",
};

function labelForEnumValue(spec: SegmentFieldDescriptor, value: string): string {
  if (spec.key === "origin_system") {
    return ORIGIN_SYSTEM_LABELS[value] ?? value;
  }
  return value;
}

/**
 * Typed value editor for a segment rule. Keeps every field on the
 * shape it really has on the backend so the route's `validate_value`
 * receives a list of UUIDs for `tags`, an enum string for
 * `marketing_consent`, an int for `lead_score`, etc.
 *
 * Used by both the simple flat-list builder and the advanced
 * react-querybuilder view (injected via Field.valueEditor).
 */
type Props = {
  spec: SegmentFieldDescriptor;
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
};

const NULL_COMPARATORS = new Set(["is_null", "is_not_null"]);
const MULTI_COMPARATORS = new Set([
  "in",
  "not_in",
  "contains_any",
  "contains_all",
  "contains_none",
]);
const RANGE_COMPARATORS = new Set(["between"]);
const NUMERIC_DURATION_COMPARATORS = new Set([
  "in_last_n_days",
  "not_in_last_n_days",
]);

export function SegmentValueEditor({
  spec,
  comparator,
  value,
  onChange,
}: Props) {
  if (NULL_COMPARATORS.has(comparator)) {
    return <span className="muted small">sin valor</span>;
  }

  if (NUMERIC_DURATION_COMPARATORS.has(comparator)) {
    return (
      <NumberEditor
        value={value}
        onChange={onChange}
        placeholder="N días"
        min={1}
      />
    );
  }

  if (RANGE_COMPARATORS.has(comparator)) {
    return (
      <RangeEditor spec={spec} value={value} onChange={onChange} />
    );
  }

  if (spec.type === "tag-multi") {
    // PR-Cc: substring match by tag name — single free-text value, not
    // a list of tag ids. Avoids the chips picker when the operator
    // wants "todos los tags con 'mbo' en el nombre".
    if (comparator === "tag_name_contains") {
      return (
        <input
          type="text"
          className="qb-value"
          value={typeof value === "string" ? value : ""}
          placeholder="texto del tag"
          onChange={(event) => onChange(event.target.value)}
        />
      );
    }
    return <TagsEditor value={value} onChange={onChange} />;
  }

  if (spec.key === "address_country") {
    return (
      <CountryEditor
        comparator={comparator}
        value={value}
        onChange={onChange}
      />
    );
  }

  if (spec.key === "origin_account_id") {
    return (
      <OriginAccountEditor
        comparator={comparator}
        value={value}
        onChange={onChange}
      />
    );
  }

  if (spec.key === "pipeline_id") {
    return (
      <PipelineEditor
        kind="pipeline"
        value={value}
        onChange={onChange}
      />
    );
  }
  if (spec.key === "pipeline_stage_id") {
    return (
      <PipelineEditor
        kind="stage"
        value={value}
        onChange={onChange}
      />
    );
  }

  if (spec.enum_values.length > 0) {
    const enumOptions = spec.enum_values.map((v) => ({
      value: v,
      label: labelForEnumValue(spec, v),
    }));
    if (MULTI_COMPARATORS.has(comparator)) {
      return (
        <EnumMultiEditor
          options={enumOptions}
          value={value}
          onChange={onChange}
        />
      );
    }
    return (
      <EnumEditor options={enumOptions} value={value} onChange={onChange} />
    );
  }

  if (spec.type === "bool") {
    return <BoolEditor value={value} onChange={onChange} />;
  }

  if (spec.type === "int") {
    if (MULTI_COMPARATORS.has(comparator)) {
      return (
        <CsvEditor
          value={value}
          onChange={(items) =>
            onChange(items.map((item) => Number.parseInt(item, 10)).filter((n) => !Number.isNaN(n)))
          }
          placeholder="70, 80, 90"
        />
      );
    }
    return <NumberEditor value={value} onChange={onChange} />;
  }

  if (spec.type === "date") {
    return <DateEditor value={value} onChange={onChange} />;
  }

  if (MULTI_COMPARATORS.has(comparator)) {
    return (
      <CsvEditor
        value={value}
        onChange={onChange}
        placeholder="separa con coma"
      />
    );
  }

  return (
    <input
      type="text"
      className="qb-value"
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// ---------- editors ----------

function NumberEditor({
  value,
  onChange,
  placeholder,
  min,
}: {
  value: unknown;
  onChange: (n: number) => void;
  placeholder?: string;
  min?: number;
}) {
  const safe = typeof value === "number" || typeof value === "string" ? String(value) : "";
  return (
    <input
      type="number"
      className="qb-value"
      value={safe}
      placeholder={placeholder}
      min={min}
      onChange={(event) => {
        const parsed = Number.parseInt(event.target.value, 10);
        onChange(Number.isNaN(parsed) ? 0 : parsed);
      }}
    />
  );
}

function DateEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (s: string) => void;
}) {
  const safe = typeof value === "string" ? value.slice(0, 10) : "";
  return (
    <input
      type="date"
      className="qb-value"
      value={safe}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function BoolEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (b: boolean) => void;
}) {
  const checked = value === true || value === "true";
  return (
    <select
      className="qb-value"
      value={checked ? "true" : "false"}
      onChange={(event) => onChange(event.target.value === "true")}
    >
      <option value="true">Sí</option>
      <option value="false">No</option>
    </select>
  );
}

type EnumOption = { value: string; label: string };

function EnumEditor({
  options,
  value,
  onChange,
}: {
  options: EnumOption[];
  value: unknown;
  onChange: (s: string) => void;
}) {
  const safe = typeof value === "string" ? value : "";
  return (
    <select
      className="qb-value"
      value={safe}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">— elige —</option>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function EnumMultiEditor({
  options,
  value,
  onChange,
}: {
  options: EnumOption[];
  value: unknown;
  onChange: (s: string[]) => void;
}) {
  const selected = Array.isArray(value)
    ? (value.filter((item) => typeof item === "string") as string[])
    : [];
  function toggle(opt: string) {
    if (selected.includes(opt)) {
      onChange(selected.filter((s) => s !== opt));
    } else {
      onChange([...selected, opt]);
    }
  }
  return (
    <div className="qb-value-multi">
      {options.map((opt) => (
        <label key={opt.value} className="qb-value-chip">
          <input
            type="checkbox"
            checked={selected.includes(opt.value)}
            onChange={() => toggle(opt.value)}
          />
          {opt.label}
        </label>
      ))}
    </div>
  );
}

function CsvEditor({
  value,
  onChange,
  placeholder,
}: {
  value: unknown;
  onChange: (items: string[]) => void;
  placeholder?: string;
}) {
  const initial = Array.isArray(value)
    ? value.map((item) => String(item)).join(", ")
    : "";
  const [draft, setDraft] = useState(initial);
  useEffect(() => {
    setDraft(initial);
  }, [initial]);
  return (
    <input
      type="text"
      className="qb-value"
      value={draft}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const items = draft
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        onChange(items);
      }}
    />
  );
}

function RangeEditor({
  spec,
  value,
  onChange,
}: {
  spec: SegmentFieldDescriptor;
  value: unknown;
  onChange: (pair: unknown[]) => void;
}) {
  const pair = Array.isArray(value) && value.length === 2 ? value : [null, null];
  const inputType = spec.type === "date" ? "date" : spec.type === "int" ? "number" : "text";
  function setIndex(index: 0 | 1, raw: string) {
    const next = [...pair];
    if (spec.type === "int") {
      const parsed = Number.parseInt(raw, 10);
      next[index] = Number.isNaN(parsed) ? null : parsed;
    } else {
      next[index] = raw;
    }
    onChange(next);
  }
  return (
    <div className="qb-value-range">
      <input
        type={inputType}
        className="qb-value"
        value={pair[0] == null ? "" : String(pair[0])}
        onChange={(event) => setIndex(0, event.target.value)}
      />
      <span className="muted small">y</span>
      <input
        type={inputType}
        className="qb-value"
        value={pair[1] == null ? "" : String(pair[1])}
        onChange={(event) => setIndex(1, event.target.value)}
      />
    </div>
  );
}

function TagsEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (ids: string[]) => void;
}) {
  const selected = useMemo(() => {
    if (!Array.isArray(value)) return [] as string[];
    return value.filter((item) => typeof item === "string") as string[];
  }, [value]);
  // Reuses the same dropdown-with-search component the contacts list
  // uses; with 30+ tags an inline checkbox wall was unreadable.
  return (
    <TagMultiSelectFilter
      selectedIds={selected}
      onChange={onChange}
      placeholder="Buscar tag…"
    />
  );
}

function CountryEditor({
  comparator,
  value,
  onChange,
}: {
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const [countries, setCountries] = useState<SegmentCountryOption[] | null>(null);
  useEffect(() => {
    listSegmentAvailableCountries()
      .then(setCountries)
      .catch(() => setCountries([]));
  }, []);

  if (countries === null) {
    return <span className="muted small">Cargando países…</span>;
  }
  if (countries.length === 0) {
    return (
      <span className="muted small">
        Aún no hay países cargados en los contactos.
      </span>
    );
  }
  const multi = comparator === "in";
  if (multi) {
    const selected = Array.isArray(value)
      ? (value.filter((item) => typeof item === "string") as string[])
      : [];
    function toggle(code: string) {
      if (selected.includes(code)) onChange(selected.filter((c) => c !== code));
      else onChange([...selected, code]);
    }
    return (
      <div className="qb-value-multi">
        {countries.map((c) => (
          <label key={c.code} className="qb-value-chip">
            <input
              type="checkbox"
              checked={selected.includes(c.code)}
              onChange={() => toggle(c.code)}
            />
            {c.code}
            <span className="muted small"> · {c.contact_count}</span>
          </label>
        ))}
      </div>
    );
  }
  const safe = typeof value === "string" ? value : "";
  return (
    <select
      className="qb-value"
      value={safe}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">— elige país —</option>
      {countries.map((c) => (
        <option key={c.code} value={c.code}>
          {c.code} ({c.contact_count})
        </option>
      ))}
    </select>
  );
}

function OriginAccountEditor({
  comparator,
  value,
  onChange,
}: {
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const [accounts, setAccounts] = useState<
    SegmentOriginAccountOption[] | null
  >(null);
  const [query, setQuery] = useState("");
  useEffect(() => {
    listSegmentAvailableOriginAccounts()
      .then(setAccounts)
      .catch(() => setAccounts([]));
  }, []);

  if (accounts === null) {
    return <span className="muted small">Cargando cuentas…</span>;
  }
  if (accounts.length === 0) {
    return (
      <span className="muted small">
        Configura una integración en{" "}
        <a href="/admin/integrations">/admin/integrations</a> primero.
      </span>
    );
  }

  const multi = comparator === "in";
  // Autocomplete kicks in past 20 accounts — below that the plain
  // dropdown / chip list is more direct.
  const showSearch = accounts.length > 20;
  const filtered =
    showSearch && query.trim()
      ? accounts.filter((acc) =>
          acc.label.toLowerCase().includes(query.trim().toLowerCase()),
        )
      : accounts;

  if (multi) {
    const selected = Array.isArray(value)
      ? (value.filter((item) => typeof item === "string") as string[])
      : [];
    function toggle(slug: string) {
      if (selected.includes(slug)) onChange(selected.filter((s) => s !== slug));
      else onChange([...selected, slug]);
    }
    return (
      <div className="qb-value-multi qb-value-multi-stacked">
        {showSearch ? (
          <input
            type="search"
            className="qb-value"
            value={query}
            placeholder="Buscar cuenta…"
            onChange={(event) => setQuery(event.target.value)}
          />
        ) : null}
        <div className="qb-value-multi">
          {filtered.map((acc) => (
            <label key={acc.value} className="qb-value-chip">
              <input
                type="checkbox"
                checked={selected.includes(acc.value)}
                onChange={() => toggle(acc.value)}
              />
              {acc.label}
            </label>
          ))}
        </div>
      </div>
    );
  }
  const safe = typeof value === "string" ? value : "";
  return (
    <select
      className="qb-value"
      value={safe}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">— elige cuenta —</option>
      {accounts.map((acc) => (
        <option key={acc.value} value={acc.value}>
          {acc.label}
        </option>
      ))}
    </select>
  );
}

function PipelineEditor({
  kind,
  value,
  onChange,
}: {
  kind: "pipeline" | "stage";
  value: unknown;
  onChange: (ids: string[]) => void;
}) {
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null);
  useEffect(() => {
    listPipelines()
      .then(setPipelines)
      .catch(() => setPipelines([]));
  }, []);
  const selected = useMemo(() => {
    if (!Array.isArray(value)) return [] as string[];
    return value.filter((item) => typeof item === "string") as string[];
  }, [value]);

  function toggle(id: string) {
    if (selected.includes(id)) onChange(selected.filter((s) => s !== id));
    else onChange([...selected, id]);
  }

  if (pipelines === null) {
    return <span className="muted small">Cargando pipelines…</span>;
  }
  if (pipelines.length === 0) {
    return <span className="muted small">No hay pipelines.</span>;
  }

  if (kind === "pipeline") {
    return (
      <div className="qb-value-multi">
        {pipelines.map((p) => (
          <label key={p.id} className="qb-value-chip">
            <input
              type="checkbox"
              checked={selected.includes(p.id)}
              onChange={() => toggle(p.id)}
            />
            {p.color ? (
              <span
                className="tag-color-swatch"
                style={{ background: p.color }}
                aria-hidden
              />
            ) : null}
            {p.name}
          </label>
        ))}
      </div>
    );
  }

  return (
    <div className="qb-value-multi qb-value-multi-stacked">
      {pipelines.map((p) => (
        <fieldset key={p.id} className="qb-value-pipeline-group">
          <legend className="muted small">{p.name}</legend>
          {p.stages.map((stage) => (
            <label key={stage.id} className="qb-value-chip">
              <input
                type="checkbox"
                checked={selected.includes(stage.id)}
                onChange={() => toggle(stage.id)}
              />
              {stage.name}
            </label>
          ))}
        </fieldset>
      ))}
    </div>
  );
}
