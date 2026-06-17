"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import {
  getUsers,
  listPipelines,
  listSegmentAvailableCountries,
  listSegmentAvailableOriginAccounts,
  listSegments,
  type Pipeline,
  type Segment,
  type SegmentCountryOption,
  type SegmentFieldDescriptor,
  type SegmentOriginAccountOption,
  type User,
} from "../lib/api";
import {
  type BrevoCampaign,
  type BrevoList,
  listBrevoCampaigns,
  listBrevoLists,
  resolvePrimaryBrevoAccount,
} from "../lib/brevoApi";
import {
  type Company,
  listCompanies,
} from "../lib/companiesApi";
import type { DashboardWindow } from "../lib/dashboardApi";
import { PeriodSelector } from "./dashboard/PeriodSelector";
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
  // PR-Ce: el motor soporta "hace más de N días" desde Sprint P.3; el
  // editor lo olvidaba y caía a `DateEditor` (calendario) — usuario
  // esperaba un input numérico de días. Ahora dispara `NumberEditor`.
  "older_than_n_days",
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

  // PR-Ce — pickers nuevos para los 4 campos reference / uuid-multi que
  // caían a text/CsvEditor (auditoría §3.2 y §3.1). Cada uno trae su
  // propia API + agrupación + ellipsis; comparten patrón con
  // `OriginAccountEditor` (autocomplete cuando hay >20 items).
  if (spec.key === "owner_user_id") {
    return (
      <UserPicker comparator={comparator} value={value} onChange={onChange} />
    );
  }
  if (spec.key === "company_id") {
    return (
      <CompanyPicker comparator={comparator} value={value} onChange={onChange} />
    );
  }
  if (spec.key === "in_segment") {
    return (
      <SegmentPicker comparator={comparator} value={value} onChange={onChange} />
    );
  }
  if (spec.key === "in_brevo_list") {
    return (
      <BrevoListPicker
        comparator={comparator}
        value={value}
        onChange={onChange}
      />
    );
  }

  if (spec.key === "brevo_campaign_interaction") {
    return (
      <BrevoCampaignInteractionEditor value={value} onChange={onChange} />
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

// ---------------------------------------------------------------------------
// PR-Ce — pickers genéricos para `owner_user_id`, `company_id`,
// `in_segment`, `in_brevo_list`. Comparten patrón con `OriginAccountEditor`:
// dropdown plano cuando hay <=20 items, autocomplete + chips cuando hay
// más. PR-Cg movió todos los pickers de "fetch full list + filter
// cliente" a "fetch debounced server-side" — los caps por consulta
// viven ahora en cada llamada al endpoint (top 100 sin q, top 50
// con q), siguiendo la convención que Bart fijó para autocompletado.

/**
 * Trunca strings largos con ellipsis CSS + tooltip nativo `title`. Se
 * usa en pickers donde los nombres pueden ser largos
 * (`brevo-list:fespa-warm-leads-2024-q4`).
 */
function PickerLabel({ text }: { text: string }) {
  return (
    <span className="picker-label" title={text}>
      {text}
    </span>
  );
}

/**
 * Agrupa items por prefijo (`brevo-list:` / primer segmento separado por
 * `:` o `-`). Si un grupo solo tiene 1 item, lo deja suelto. Devuelve un
 * Map ordenado.
 */
function groupByPrefix<T extends { name: string }>(
  items: T[],
): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const head = item.name.split(/[:\-]/)[0] ?? item.name;
    const prefix = head.length < item.name.length ? `${head}*` : "_solo";
    const bucket = groups.get(prefix) ?? [];
    bucket.push(item);
    groups.set(prefix, bucket);
  }
  // Colapsa los _solo en un grupo común "Otros" al final.
  const out = new Map<string, T[]>();
  const otros: T[] = [];
  for (const [key, bucket] of groups) {
    if (key === "_solo" || bucket.length === 1) otros.push(...bucket);
    else out.set(key, bucket);
  }
  if (otros.length) out.set("Otros", otros);
  return out;
}

function UserPicker({
  comparator,
  value,
  onChange,
}: {
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  // PR-Cg: refactor a fetch debounced server-side. El operador puede
  // tener 200+ usuarios; descargar y filtrar en cliente corta los que
  // entren en cualquier slice arbitrario y obliga a teclear el nombre
  // exacto para encontrarlos.
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getUsers({ q: debouncedQuery || undefined, limit: 100 })
      .then((u) => {
        if (!cancelled) setUsers(u.filter((row) => row.is_active));
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  const multi = comparator === "in" || comparator === "not_in";

  if (multi) {
    const selected = Array.isArray(value)
      ? (value.filter((v) => typeof v === "string") as string[])
      : [];
    const toggle = (id: string) =>
      onChange(
        selected.includes(id)
          ? selected.filter((x) => x !== id)
          : [...selected, id],
      );
    return (
      <div className="qb-value-multi qb-value-multi-stacked">
        <input
          type="search"
          className="qb-value"
          value={query}
          placeholder="Buscar usuario…"
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="qb-value-multi">
          {loading ? (
            <span className="muted small">Cargando…</span>
          ) : users.length === 0 ? (
            <span className="muted small">Sin resultados.</span>
          ) : (
            users.map((u) => (
              <label key={u.id} className="qb-value-chip">
                <input
                  type="checkbox"
                  checked={selected.includes(u.id)}
                  onChange={() => toggle(u.id)}
                />
                <PickerLabel text={`${u.full_name} (${u.email})`} />
              </label>
            ))
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="qb-value-multi qb-value-multi-stacked">
      <input
        type="search"
        className="qb-value"
        value={query}
        placeholder="Buscar usuario…"
        onChange={(e) => setQuery(e.target.value)}
      />
      <select
        className="qb-value"
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">— elige usuario —</option>
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.full_name} ({u.email})
          </option>
        ))}
      </select>
    </div>
  );
}

function CompanyPicker({
  comparator,
  value,
  onChange,
}: {
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  // Companies pueden ser miles → siempre con búsqueda contra el endpoint.
  const [query, setQuery] = useState("");
  const deferredQuery = useDebouncedValue(query, 300);
  const [results, setResults] = useState<Company[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // PR-Cg: convención "top 100 sin q, top 50 con q" — suficiente
    // para autocomplete; el operador refina la query si necesita
    // más resolución. Backend cap `limit` a 200.
    listCompanies({
      q: deferredQuery || undefined,
      limit: deferredQuery ? 50 : 100,
    })
      .then((page) => {
        if (!cancelled) setResults(page.items);
      })
      .catch(() => {
        if (!cancelled) setResults([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deferredQuery]);

  const multi = comparator === "in" || comparator === "not_in";
  if (multi) {
    const selected = Array.isArray(value)
      ? (value.filter((v) => typeof v === "string") as string[])
      : [];
    const toggle = (id: string) =>
      onChange(
        selected.includes(id)
          ? selected.filter((x) => x !== id)
          : [...selected, id],
      );
    return (
      <div className="qb-value-multi qb-value-multi-stacked">
        <input
          type="search"
          className="qb-value"
          value={query}
          placeholder="Buscar empresa por nombre / dominio…"
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="qb-value-multi">
          {loading ? (
            <span className="muted small">Cargando…</span>
          ) : results.length === 0 ? (
            <span className="muted small">Sin resultados.</span>
          ) : (
            results.map((c) => (
              <label key={c.id} className="qb-value-chip">
                <input
                  type="checkbox"
                  checked={selected.includes(c.id)}
                  onChange={() => toggle(c.id)}
                />
                <PickerLabel
                  text={c.domain ? `${c.name} · ${c.domain}` : c.name}
                />
              </label>
            ))
          )}
        </div>
      </div>
    );
  }

  // single value (eq / neq) → select libre con búsqueda
  return (
    <div className="qb-value-multi qb-value-multi-stacked">
      <input
        type="search"
        className="qb-value"
        value={query}
        placeholder="Buscar empresa…"
        onChange={(e) => setQuery(e.target.value)}
      />
      <select
        className="qb-value"
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">— elige empresa —</option>
        {results.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
            {c.domain ? ` · ${c.domain}` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

function SegmentPicker({
  comparator,
  value,
  onChange,
}: {
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  // PR-Cg: server-side debounced. La cuenta puede tener cientos de
  // segmentos (especialmente con espejos Brevo); descargar todo y
  // filtrar en cliente cortaba alfabéticamente.
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listSegments({
      q: debouncedQuery || undefined,
      limit: debouncedQuery ? 50 : 100,
    })
      .then((rows) => {
        if (!cancelled) setSegments(rows);
      })
      .catch(() => {
        if (!cancelled) setSegments([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  // in_segment es uuid-multi → siempre multi-select.
  const selected = Array.isArray(value)
    ? (value.filter((v) => typeof v === "string") as string[])
    : [];
  const toggle = (id: string) =>
    onChange(
      selected.includes(id)
        ? selected.filter((x) => x !== id)
        : [...selected, id],
    );
  return (
    <div className="qb-value-multi qb-value-multi-stacked">
      <input
        type="search"
        className="qb-value"
        value={query}
        placeholder="Buscar segmento…"
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="qb-value-multi">
        {loading ? (
          <span className="muted small">Cargando…</span>
        ) : segments.length === 0 ? (
          <span className="muted small">
            Sin resultados — crea segmentos en{" "}
            <Link href="/segments">/segments</Link>.
          </span>
        ) : (
          segments.map((s) => (
            <label key={s.id} className="qb-value-chip">
              <input
                type="checkbox"
                checked={selected.includes(s.id)}
                onChange={() => toggle(s.id)}
              />
              <PickerLabel text={s.name} />
              {s.cached_count != null ? (
                <span className="muted small">{` (${s.cached_count})`}</span>
              ) : null}
            </label>
          ))
        )}
      </div>
      <span className="muted small">
        {comparator === "not_in"
          ? "Contactos que NO están en ninguno de los segmentos elegidos."
          : "Contactos que pertenecen a alguno de los segmentos."}
      </span>
    </div>
  );
}

function BrevoListPicker({
  value,
  onChange,
}: {
  // `comparator` se omite porque `in_brevo_list` es uuid-multi y sólo
  // expone `in/not_in` — ambos pintan multi-select; la diferencia es
  // semántica server-side.
  comparator: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const [accountId, setAccountId] = useState<string | null | undefined>(
    undefined,
  );
  const [lists, setLists] = useState<BrevoList[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    new Set(),
  );

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then((id) => setAccountId(id ?? null))
      .catch(() => setAccountId(null));
  }, []);
  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    setLoading(true);
    // PR-Cg: server-side `q`. Brevo no soporta búsqueda nativa; el
    // endpoint pagina hasta 1000 listas en backend, filtra y devuelve
    // el subset. Cuentas con miles necesitan teclear suficiente
    // texto para entrar en el cap — el cliente no descarga la base.
    listBrevoLists(accountId, {
      q: debouncedQuery || undefined,
      limit: debouncedQuery ? 50 : 100,
    })
      .then((rows) => {
        if (!cancelled) setLists(rows);
      })
      .catch(() => {
        if (!cancelled) setLists([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, debouncedQuery]);

  if (accountId === undefined) {
    return <span className="muted small">Cargando cuenta Brevo…</span>;
  }
  if (accountId === null) {
    return (
      <span className="muted small">
        Configura una cuenta Brevo en{" "}
        <a href="/admin/integrations">/admin/integrations</a> primero.
      </span>
    );
  }

  // PR-Ce: id de Brevo viene como número; el motor lo trata como string.
  // El picker envía siempre el id como string para que el round-trip
  // RQB↔IR no rompa.
  const grouped = groupByPrefix(lists);

  const selected = Array.isArray(value)
    ? value.map((v) => String(v))
    : [];
  const toggle = (id: string) =>
    onChange(
      selected.includes(id)
        ? selected.filter((x) => x !== id)
        : [...selected, id],
    );
  const toggleGroup = (group: string) =>
    setCollapsedGroups((cur) => {
      const next = new Set(cur);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  return (
    <div className="qb-value-multi qb-value-multi-stacked">
      <input
        type="search"
        className="qb-value"
        value={query}
        placeholder="Buscar lista Brevo…"
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="qb-value-multi qb-value-multi-stacked">
        {loading ? (
          <span className="muted small">Cargando…</span>
        ) : lists.length === 0 ? (
          <span className="muted small">Sin resultados.</span>
        ) : null}
        {[...grouped.entries()].map(([group, bucket]) => {
          const collapsed = collapsedGroups.has(group);
          return (
            <div key={group} className="picker-group">
              <button
                type="button"
                className="picker-group-header"
                onClick={() => toggleGroup(group)}
                aria-expanded={!collapsed}
              >
                {collapsed ? "▸" : "▾"} {group}{" "}
                <span className="muted small">({bucket.length})</span>
              </button>
              {!collapsed ? (
                <div className="qb-value-multi">
                  {bucket.map((l) => {
                    const id = String(l.id);
                    return (
                      <label key={l.id} className="qb-value-chip">
                        <input
                          type="checkbox"
                          checked={selected.includes(id)}
                          onChange={() => toggle(id)}
                        />
                        <PickerLabel text={l.name} />
                        <span className="muted small">
                          {` (${l.total_subscribers})`}
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PR-E3 (Deuda #8) — editor composite del field `brevo_campaign_interaction`
// ---------------------------------------------------------------------------

type BrevoInteractionValue = {
  campaigns: number[];
  action: string;
  period: string;
  start?: string | null;
  end?: string | null;
};

const BREVO_ACTION_OPTIONS: ReadonlyArray<[string, string]> = [
  ["received", "recibió"],
  ["opened", "abrió"],
  ["clicked", "clickeó"],
  ["not_opened", "NO abrió"],
  ["not_clicked", "NO clickeó"],
  ["bounced", "rebotó"],
  ["unsubscribed", "se dió de baja"],
];

function asInteractionValue(raw: unknown): BrevoInteractionValue {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const v = raw as Record<string, unknown>;
    return {
      campaigns: Array.isArray(v.campaigns)
        ? v.campaigns.map((c) => Number(c)).filter((n) => !Number.isNaN(n))
        : [],
      action: typeof v.action === "string" ? v.action : "opened",
      period: typeof v.period === "string" ? v.period : "all",
      start: typeof v.start === "string" ? v.start : null,
      end: typeof v.end === "string" ? v.end : null,
    };
  }
  return { campaigns: [], action: "opened", period: "all" };
}

function BrevoCampaignInteractionEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const current = asInteractionValue(value);
  const [accountId, setAccountId] = useState<string | null | undefined>(
    undefined,
  );
  const [campaigns, setCampaigns] = useState<BrevoCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  // PR-E4: el picker de campañas pasa a dropdown contraído. Sólo se
  // expande al click del trigger; las seleccionadas quedan como chips
  // bajo el trigger. Patrón parecido a TagPicker.
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then((id) => setAccountId(id ?? null))
      .catch(() => setAccountId(null));
  }, []);

  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    setLoading(true);
    listBrevoCampaigns(accountId, { status: "sent" })
      .then((rows) => {
        if (!cancelled) setCampaigns(rows);
      })
      .catch(() => {
        if (!cancelled) setCampaigns([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  useEffect(() => {
    if (!pickerOpen) return;
    function onDocClick(e: MouseEvent) {
      if (
        pickerRef.current &&
        !pickerRef.current.contains(e.target as Node | null)
      ) {
        setPickerOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [pickerOpen]);

  if (accountId === undefined) {
    return <span className="muted small">Cargando cuenta Brevo…</span>;
  }
  if (accountId === null) {
    return (
      <span className="muted small">
        Configura una cuenta Brevo en{" "}
        <a href="/admin/integrations">/admin/integrations</a> primero.
      </span>
    );
  }

  const needle = debouncedQuery.trim().toLowerCase();
  const filtered = needle
    ? campaigns.filter((c) => c.name.toLowerCase().includes(needle))
    : campaigns;
  const selected = new Set(current.campaigns);
  const campaignsById = new Map(
    campaigns.map((c) => [c.brevo_campaign_id, c]),
  );

  const patch = (next: Partial<BrevoInteractionValue>) =>
    onChange({ ...current, ...next });

  const toggleCampaign = (id: number) => {
    const nextSet = new Set(selected);
    if (nextSet.has(id)) nextSet.delete(id);
    else nextSet.add(id);
    patch({ campaigns: [...nextSet] });
  };

  const windowValue: DashboardWindow = {
    period: current.period as DashboardWindow["period"],
    start: current.start,
    end: current.end,
  };

  const triggerLabel =
    selected.size === 0
      ? "+ Seleccionar campañas"
      : `${selected.size} campaña${selected.size === 1 ? "" : "s"} seleccionada${
          selected.size === 1 ? "" : "s"
        }`;

  return (
    <div className="brevo-interaction-editor">
      {/* PR-E4: Acción + Período en UNA fila horizontal. */}
      <div className="brevo-interaction-controls">
        <select
          className="qb-value brevo-interaction-action"
          value={current.action}
          onChange={(e) => patch({ action: e.target.value })}
          aria-label="Acción"
        >
          {BREVO_ACTION_OPTIONS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <PeriodSelector
          value={windowValue}
          includeAll
          onChange={(w) =>
            patch({ period: w.period, start: w.start, end: w.end })
          }
        />
      </div>

      <div className="brevo-interaction-picker" ref={pickerRef}>
        <button
          type="button"
          className="button secondary small brevo-interaction-trigger"
          onClick={() => setPickerOpen((open) => !open)}
          aria-expanded={pickerOpen}
        >
          {triggerLabel} ▾
        </button>
        {pickerOpen ? (
          <div className="brevo-interaction-popover">
            <input
              type="search"
              className="qb-value"
              value={query}
              placeholder="Buscar campaña…"
              autoFocus
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="brevo-interaction-options">
              {loading ? (
                <span className="muted small">Cargando campañas…</span>
              ) : filtered.length === 0 ? (
                <span className="muted small">Sin campañas.</span>
              ) : (
                filtered.map((c) => (
                  <label
                    key={c.brevo_campaign_id}
                    className="brevo-interaction-option"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(c.brevo_campaign_id)}
                      onChange={() => toggleCampaign(c.brevo_campaign_id)}
                    />
                    <PickerLabel text={c.name} />
                  </label>
                ))
              )}
            </div>
          </div>
        ) : null}
      </div>

      {/* Chips de campañas seleccionadas — X para deseleccionar. */}
      {selected.size > 0 ? (
        <div className="brevo-interaction-chips">
          {[...selected].map((id) => {
            const c = campaignsById.get(id);
            const label = c?.name ?? `Campaña #${id}`;
            return (
              <span key={id} className="brevo-interaction-chip">
                <span className="brevo-interaction-chip-name" title={label}>
                  {label}
                </span>
                <button
                  type="button"
                  className="brevo-interaction-chip-remove"
                  aria-label={`Quitar ${label}`}
                  onClick={() => toggleCampaign(id)}
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
