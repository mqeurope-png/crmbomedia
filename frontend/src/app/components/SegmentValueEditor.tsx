"use client";

import { useEffect, useMemo, useState } from "react";
import {
  listPipelines,
  listTags,
  type Pipeline,
  type SegmentFieldDescriptor,
  type TagDetail,
} from "../lib/api";

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
    return <TagsEditor value={value} onChange={onChange} />;
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
    if (MULTI_COMPARATORS.has(comparator)) {
      return (
        <EnumMultiEditor
          options={spec.enum_values}
          value={value}
          onChange={onChange}
        />
      );
    }
    return (
      <EnumEditor
        options={spec.enum_values}
        value={value}
        onChange={onChange}
      />
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

function EnumEditor({
  options,
  value,
  onChange,
}: {
  options: string[];
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
        <option key={opt} value={opt}>
          {opt}
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
  options: string[];
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
        <label key={opt} className="qb-value-chip">
          <input
            type="checkbox"
            checked={selected.includes(opt)}
            onChange={() => toggle(opt)}
          />
          {opt}
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
  const [tags, setTags] = useState<TagDetail[] | null>(null);
  useEffect(() => {
    listTags()
      .then((page) => setTags(page.items))
      .catch(() => setTags([]));
  }, []);
  const selected = useMemo(() => {
    if (!Array.isArray(value)) return [] as string[];
    return value.filter((item) => typeof item === "string") as string[];
  }, [value]);

  function toggle(id: string) {
    if (selected.includes(id)) onChange(selected.filter((s) => s !== id));
    else onChange([...selected, id]);
  }

  if (tags === null) return <span className="muted small">Cargando tags…</span>;
  if (tags.length === 0) {
    return <span className="muted small">No hay tags todavía.</span>;
  }

  return (
    <div className="qb-value-multi">
      {tags.map((tag) => (
        <label key={tag.id} className="qb-value-chip">
          <input
            type="checkbox"
            checked={selected.includes(tag.id)}
            onChange={() => toggle(tag.id)}
          />
          {tag.color ? (
            <span
              className="tag-color-swatch"
              style={{ background: tag.color }}
              aria-hidden
            />
          ) : null}
          {tag.name}
        </label>
      ))}
    </div>
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
