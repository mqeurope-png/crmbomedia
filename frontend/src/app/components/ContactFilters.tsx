"use client";

import type { ContactListFilters } from "../lib/api";
import { OriginAccountMultiSelect } from "./OriginAccountMultiSelect";
import { TagMultiSelectFilter } from "./TagMultiSelectFilter";

type Props = {
  filters: ContactListFilters;
  onChange: (next: ContactListFilters) => void;
  onReset: () => void;
};

const COMMERCIAL_STATUSES = [
  { value: "", label: "Cualquier estado" },
  { value: "new", label: "Nuevo" },
  { value: "qualified", label: "Cualificado" },
  { value: "won", label: "Ganado" },
  { value: "lost", label: "Perdido" },
];

const MARKETING_CONSENTS = [
  { value: "", label: "Cualquier consentimiento" },
  { value: "granted", label: "Concedido" },
  { value: "denied", label: "Denegado" },
  { value: "unknown", label: "Desconocido" },
  { value: "unsubscribed", label: "Baja" },
];

export function ContactFilters({ filters, onChange, onReset }: Props) {
  const update = (patch: Partial<ContactListFilters>) =>
    onChange({ ...filters, ...patch, skip: 0 });

  const selectedTagIds = filters.tag_ids ?? [];

  return (
    <div className="contact-filters" role="group" aria-label="Filtros de contactos">
      <div className="filter-block filter-block-tags">
        <span className="filter-label">Tags</span>
        <TagMultiSelectFilter
          selectedIds={selectedTagIds}
          onChange={(next) =>
            update({ tag_ids: next.length ? next : undefined })
          }
          footer={
            <label className="checkbox">
              <input
                type="checkbox"
                checked={filters.tag_match_mode === "all"}
                onChange={(event) =>
                  update({
                    tag_match_mode: event.target.checked ? "all" : "any",
                  })
                }
              />
              <span>Todas las tags (en vez de cualquiera)</span>
            </label>
          }
        />
      </div>

      <label>
        <span>Estado comercial</span>
        <select
          value={filters.commercial_status ?? ""}
          onChange={(event) =>
            update({ commercial_status: event.target.value || undefined })
          }
        >
          {COMMERCIAL_STATUSES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label>
        <span>Consentimiento marketing</span>
        <select
          value={filters.marketing_consent ?? ""}
          onChange={(event) =>
            update({ marketing_consent: event.target.value || undefined })
          }
        >
          {MARKETING_CONSENTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <div className="filter-block">
        <span className="filter-label">Origen</span>
        <OriginAccountMultiSelect
          selectedKeys={filters.origin_account_keys ?? []}
          onChange={(next) =>
            update({
              origin_account_keys: next.length ? next : undefined,
              // Drop the legacy fields the moment the operator picks a
              // concrete account. They stay set if a saved view loaded
              // them and the operator hasn't touched the origin filter.
              origin_system: next.length ? undefined : filters.origin_system,
              origin_account_id: next.length
                ? undefined
                : filters.origin_account_id,
            })
          }
        />
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={Boolean(filters.include_inactive)}
          onChange={(event) =>
            update({ include_inactive: event.target.checked || undefined })
          }
        />
        <span>Incluir inactivos</span>
      </label>

      <button type="button" className="button secondary small" onClick={onReset}>
        Limpiar filtros
      </button>
    </div>
  );
}
