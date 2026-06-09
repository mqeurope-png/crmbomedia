"use client";

import { useEffect, useState } from "react";
import { listTags, type ContactListFilters, type TagDetail } from "../lib/api";

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

const ORIGIN_SYSTEMS = [
  { value: "", label: "Cualquier origen" },
  { value: "agilecrm", label: "AgileCRM" },
  { value: "brevo", label: "Brevo" },
  { value: "freshdesk", label: "Freshdesk" },
  { value: "factusol", label: "FactuSOL" },
  { value: "manual", label: "Manual" },
];

export function ContactFilters({ filters, onChange, onReset }: Props) {
  const [tags, setTags] = useState<TagDetail[]>([]);
  const [tagsError, setTagsError] = useState<string | null>(null);

  useEffect(() => {
    listTags()
      .then((page) => setTags(page.items))
      .catch(() => setTagsError("Tags no disponibles"));
  }, []);

  const update = (patch: Partial<ContactListFilters>) =>
    onChange({ ...filters, ...patch, skip: 0 });

  const selectedTagIds = new Set(filters.tag_ids ?? []);

  return (
    <div className="contact-filters" role="group" aria-label="Filtros de contactos">
      <div className="filter-block filter-block-tags">
        <span className="filter-label">Tags</span>
        {tagsError ? (
          <span className="muted small">{tagsError}</span>
        ) : tags.length === 0 ? (
          <span className="muted small">Sin tags configurados</span>
        ) : (
          <div className="filter-tag-list">
            {tags.map((tag) => {
              const checked = selectedTagIds.has(tag.id);
              return (
                <label
                  key={tag.id}
                  className={`filter-tag-chip${checked ? " is-selected" : ""}`}
                  style={tag.color ? { borderColor: tag.color } : undefined}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const next = new Set(selectedTagIds);
                      if (checked) next.delete(tag.id);
                      else next.add(tag.id);
                      update({
                        tag_ids: next.size ? Array.from(next) : undefined,
                      });
                    }}
                  />
                  {tag.color ? (
                    <span
                      className="tag-picker-swatch"
                      style={{ background: tag.color }}
                      aria-hidden
                    />
                  ) : null}
                  <span>{tag.name}</span>
                </label>
              );
            })}
          </div>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={filters.tag_match_mode === "all"}
            onChange={(event) =>
              update({ tag_match_mode: event.target.checked ? "all" : "any" })
            }
          />
          <span>Todas las tags (en vez de cualquiera)</span>
        </label>
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

      <label>
        <span>Origen</span>
        <select
          value={filters.origin_system ?? ""}
          onChange={(event) =>
            update({ origin_system: event.target.value || undefined })
          }
        >
          {ORIGIN_SYSTEMS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

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
