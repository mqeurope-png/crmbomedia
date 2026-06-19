"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../lib/api";
import { listCompanies, type Company } from "../../lib/companiesApi";
import { getUsers, type User } from "../../lib/api";

type CustomFieldKey = {
  key: string;
  type: string;
  /** PR-Fixes-Pase-4 Bug 6. "manual" | "agilecrm" | "inferred" | … */
  source?: string;
  /** Etiqueta humana opcional — definida en admin para campos manuales. */
  label?: string;
};

type FieldKind =
  | "text"
  | "number"
  | "date"
  | "boolean"
  | "url"
  | "select"
  | "user_ref"
  | "company_ref";

type FieldOption = {
  key: string;
  /** Label visible en el dropdown. */
  label: string;
  /** Tipo del input "Nuevo valor". */
  kind: FieldKind;
  /** Para `kind === "select"`: opciones del dropdown. */
  enumValues?: { value: string; label: string }[];
  /** "native" | "custom-manual" | "custom-agile" | … */
  origin: string;
};

type Props = {
  value: string;
  valueValue: string;
  onChange: (next: { field: string; value: string; type: string }) => void;
};

/**
 * PR-Fixes-Pase-3 Bug 6 + Pase-4 Bug 6 + Pase-5 Bug 1.
 *
 * Selector unificado de campo del contacto para el step "Modificar
 * campo". Lista dos grupos en el dropdown:
 *
 *  1. **Campos nativos**: atributos directos del Contact que el
 *     operador edita en la ficha (nombre, email, lead_score, owner,
 *     estado del ciclo, etc.). Sincronizado con la whitelist
 *     `_CONTACT_NATIVE_FIELDS` del backend.
 *  2. **Custom fields**: lo que devuelve `/api/contacts/custom-field-keys`
 *     (definiciones manuales + lo inferido de los contactos).
 *
 * El input "Nuevo valor" adapta su tipo según el campo elegido:
 * texto / número / fecha / URL / dropdown enum / dropdown de users
 * / dropdown de empresas.
 */
export function CustomFieldSelector({
  value,
  valueValue,
  onChange,
}: Props) {
  const [customKeys, setCustomKeys] = useState<CustomFieldKey[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [newFieldMode, setNewFieldMode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch<CustomFieldKey[]>("/api/contacts/custom-field-keys").catch(
        () => [] as CustomFieldKey[],
      ),
      // Users + companies son baratos (≤500 cada uno) y los usamos
      // para resolver los dropdowns de owner / empresa al elegir
      // esos campos nativos.
      getUsers().catch(() => [] as User[]),
      listCompanies({ limit: 200 }).catch(() => ({ items: [] as Company[], total: 0 })),
    ])
      .then(([keys, usersRes, companiesRes]) => {
        if (cancelled) return;
        setCustomKeys(keys);
        setUsers(usersRes);
        setCompanies(companiesRes.items);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const nativeFields = useMemo<FieldOption[]>(
    () => _buildNativeFields(users, companies),
    [users, companies],
  );

  const customOptions = useMemo<FieldOption[]>(
    () =>
      customKeys.map((k) => ({
        key: k.key,
        label: k.label || k.key,
        kind: (k.type === "boolean" ? "boolean" : (k.type as FieldKind)) ?? "text",
        origin: k.source === "manual" ? "custom-manual" : "custom-agile",
      })),
    [customKeys],
  );

  const allOptions = useMemo(
    () => [...nativeFields, ...customOptions],
    [nativeFields, customOptions],
  );

  const selected = allOptions.find((o) => o.key === value);
  const kind: FieldKind = selected?.kind ?? "text";

  if (loading) {
    return <p className="muted small">Cargando campos…</p>;
  }

  const onChangeField = (nextKey: string) => {
    const next = allOptions.find((o) => o.key === nextKey);
    onChange({
      field: nextKey,
      value: valueValue,
      type: next?.kind ?? "text",
    });
  };

  return (
    <>
      <label>
        Campo a modificar
        {newFieldMode || (value && !selected) ? (
          <>
            <input
              type="text"
              value={value}
              onChange={(e) =>
                onChange({
                  field: e.target.value,
                  value: valueValue,
                  type: kind,
                })
              }
              placeholder="ej. sector"
            />
            <button
              type="button"
              className="muted small workflow-link-button"
              onClick={() => {
                setNewFieldMode(false);
                onChange({ field: "", value: valueValue, type: kind });
              }}
            >
              ← elegir uno existente
            </button>
          </>
        ) : (
          <>
            <select
              value={value}
              onChange={(e) => onChangeField(e.target.value)}
            >
              <option value="">— Selecciona —</option>
              <optgroup label="Campos nativos">
                {nativeFields.map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label} ({_kindLabel(f.kind)})
                  </option>
                ))}
              </optgroup>
              <optgroup label="Custom fields">
                {customOptions.map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label} ({_kindLabel(f.kind)} · {_originLabel(f.origin)})
                  </option>
                ))}
              </optgroup>
            </select>
            <button
              type="button"
              className="muted small workflow-link-button"
              onClick={() => setNewFieldMode(true)}
            >
              + crear nuevo campo
            </button>
          </>
        )}
      </label>
      <ValueInput
        kind={kind}
        value={valueValue}
        field={value}
        option={selected}
        onChange={(v) => onChange({ field: value, value: v, type: kind })}
      />
    </>
  );
}

function ValueInput({
  kind,
  value,
  field,
  option,
  onChange,
}: {
  kind: FieldKind;
  value: string;
  field: string;
  option?: FieldOption;
  onChange: (next: string) => void;
}) {
  return (
    <label>
      Nuevo valor
      {kind === "number" ? (
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : kind === "date" ? (
        <input
          type="date"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : kind === "boolean" ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          <option value="true">Sí</option>
          <option value="false">No</option>
        </select>
      ) : kind === "url" ? (
        <input
          type="url"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="https://ejemplo.com"
        />
      ) : kind === "select" ||
        kind === "user_ref" ||
        kind === "company_ref" ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">— Selecciona —</option>
          {(option?.enumValues ?? []).map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder='Texto o variable {{ contact.first_name }}'
        />
      )}
      {field && _isRequired(field) ? (
        <span className="muted small">
          Este campo es obligatorio, no puede dejarse vacío.
        </span>
      ) : null}
    </label>
  );
}

// ---------------------------------------------------------------------
// Native field catalog — mirror del whitelist del backend.
// ---------------------------------------------------------------------

const _REQUIRED_FIELDS = new Set(["first_name"]);

function _isRequired(field: string): boolean {
  return _REQUIRED_FIELDS.has(field);
}

const COMMERCIAL_STATUS_OPTIONS = [
  { value: "new", label: "Nuevo" },
  { value: "qualified", label: "Cualificado" },
  { value: "working", label: "Trabajando" },
  { value: "won", label: "Cliente" },
  { value: "lost", label: "Perdido" },
];

function _buildNativeFields(
  users: User[],
  companies: Company[],
): FieldOption[] {
  const userOptions = users
    .filter((u) => u.is_active)
    .map((u) => ({ value: u.id, label: u.full_name || u.email }));
  const companyOptions = companies.map((c) => ({
    value: c.id,
    label: c.name,
  }));
  return [
    { key: "first_name", label: "Nombre", kind: "text", origin: "native" },
    { key: "last_name", label: "Apellidos", kind: "text", origin: "native" },
    { key: "email", label: "Email", kind: "text", origin: "native" },
    { key: "phone", label: "Teléfono principal", kind: "text", origin: "native" },
    { key: "job_title", label: "Puesto / Job title", kind: "text", origin: "native" },
    {
      key: "commercial_status",
      label: "Estado del ciclo",
      kind: "select",
      enumValues: COMMERCIAL_STATUS_OPTIONS,
      origin: "native",
    },
    { key: "lead_score", label: "Lead score", kind: "number", origin: "native" },
    {
      key: "owner_user_id",
      label: "Propietario",
      kind: "user_ref",
      enumValues: userOptions,
      origin: "native",
    },
    {
      key: "company_id",
      label: "Empresa",
      kind: "company_ref",
      enumValues: companyOptions,
      origin: "native",
    },
    { key: "origin", label: "Origen del lead", kind: "text", origin: "native" },
    { key: "linkedin_url", label: "LinkedIn URL", kind: "url", origin: "native" },
    { key: "personal_website", label: "Web personal URL", kind: "url", origin: "native" },
    { key: "address_line", label: "Dirección · calle", kind: "text", origin: "native" },
    { key: "address_city", label: "Dirección · ciudad", kind: "text", origin: "native" },
    { key: "address_state", label: "Dirección · estado/provincia", kind: "text", origin: "native" },
    { key: "address_region", label: "Dirección · región", kind: "text", origin: "native" },
    { key: "address_postal_code", label: "Dirección · código postal", kind: "text", origin: "native" },
    { key: "address_country", label: "Dirección · país (código)", kind: "text", origin: "native" },
    { key: "address_country_name", label: "Dirección · país (nombre)", kind: "text", origin: "native" },
  ];
}

function _kindLabel(kind: FieldKind): string {
  return (
    {
      text: "texto",
      number: "número",
      date: "fecha",
      boolean: "sí/no",
      url: "URL",
      select: "selector",
      user_ref: "selector usuarios",
      company_ref: "selector empresas",
    }[kind] ?? kind
  );
}

function _originLabel(origin: string): string {
  switch (origin) {
    case "native":
      return "nativo";
    case "custom-manual":
      return "manual";
    case "custom-agile":
      return "de AgileCRM";
    default:
      return origin;
  }
}
