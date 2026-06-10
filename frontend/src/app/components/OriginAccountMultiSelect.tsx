"use client";

import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  listIntegrationAccountGroups,
  type IntegrationSystemGroup,
} from "../lib/api";

type Props = {
  selectedKeys: string[];
  onChange: (next: string[]) => void;
  /** Hide accounts that are disabled in `/admin/integrations`. Default false:
   * disabled rows still show in the segment editor because the engine
   * can produce results against them once they're re-enabled. */
  hideDisabled?: boolean;
  placeholder?: string;
};

/**
 * Origin picker grouped by integration system. With 9 AgileCRM
 * accounts the previous flat enum-style dropdown was useless — this
 * one groups by system, lets the operator pick "all AgileCRM" via the
 * group button, and supports filtering by label.
 *
 * Used both on the contacts list filters and on the segment value
 * editor for `origin_account_id`. Emits compound keys
 * `"system:account_id"` so the backend filter can apply
 * `(system, account_id) IN (...)` without ambiguity.
 */
export function OriginAccountMultiSelect({
  selectedKeys,
  onChange,
  hideDisabled = false,
  placeholder = "Filtrar por cuentas de origen",
}: Props) {
  const [groups, setGroups] = useState<IntegrationSystemGroup[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || groups !== null) return;
    listIntegrationAccountGroups()
      .then(setGroups)
      .catch(() => setError("No se pudieron cargar las cuentas."));
  }, [open, groups]);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filteredGroups = useMemo(() => {
    if (!groups) return [] as IntegrationSystemGroup[];
    const normalized = query.trim().toLowerCase();
    return groups
      .map((group) => ({
        ...group,
        accounts: group.accounts
          .filter((acc) => (hideDisabled ? acc.enabled : true))
          .filter((acc) =>
            normalized
              ? acc.label.toLowerCase().includes(normalized) ||
                acc.account_id.toLowerCase().includes(normalized)
              : true,
          ),
      }))
      .filter((group) => group.accounts.length > 0);
  }, [groups, query, hideDisabled]);

  function toggle(systemSlug: string, accountId: string) {
    const key = `${systemSlug}:${accountId}`;
    if (selectedKeys.includes(key)) {
      onChange(selectedKeys.filter((existing) => existing !== key));
    } else {
      onChange([...selectedKeys, key]);
    }
  }

  function toggleGroup(group: IntegrationSystemGroup) {
    const groupKeys = group.accounts.map(
      (acc) => `${group.system}:${acc.account_id}`,
    );
    const allSelected = groupKeys.every((key) => selectedKeys.includes(key));
    if (allSelected) {
      onChange(selectedKeys.filter((key) => !groupKeys.includes(key)));
    } else {
      const merged = new Set([...selectedKeys, ...groupKeys]);
      onChange(Array.from(merged));
    }
  }

  function removeKey(key: string) {
    onChange(selectedKeys.filter((existing) => existing !== key));
  }

  const labelByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const group of groups ?? []) {
      for (const acc of group.accounts) {
        map.set(
          `${group.system}:${acc.account_id}`,
          `${group.system_label} · ${acc.label}`,
        );
      }
    }
    return map;
  }, [groups]);

  return (
    <div ref={wrapper} className="origin-multiselect">
      <button
        type="button"
        className={`origin-multiselect-trigger${open ? " is-open" : ""}`}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selectedKeys.length === 0 ? (
          <span className="muted">{placeholder}</span>
        ) : (
          <span className="origin-multiselect-chips">
            {selectedKeys.slice(0, 3).map((key) => (
              <span key={key} className="origin-multiselect-chip">
                {labelByKey.get(key) ?? key}
                <button
                  type="button"
                  className="origin-multiselect-chip-remove"
                  aria-label={`Quitar ${labelByKey.get(key) ?? key}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    removeKey(key);
                  }}
                >
                  <X size={10} aria-hidden />
                </button>
              </span>
            ))}
            {selectedKeys.length > 3 ? (
              <span className="muted small">
                +{selectedKeys.length - 3} más
              </span>
            ) : null}
          </span>
        )}
        <ChevronDown size={14} aria-hidden />
      </button>

      {open ? (
        <div className="origin-multiselect-panel" role="listbox">
          <div className="origin-multiselect-search">
            <Search size={14} aria-hidden />
            <input
              type="search"
              value={query}
              placeholder="Buscar cuenta…"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {error ? (
            <p className="origin-multiselect-empty">{error}</p>
          ) : groups === null ? (
            <p className="origin-multiselect-empty">Cargando…</p>
          ) : filteredGroups.length === 0 ? (
            <p className="origin-multiselect-empty">Sin resultados.</p>
          ) : (
            <div className="origin-multiselect-groups">
              {filteredGroups.map((group) => {
                const groupKeys = group.accounts.map(
                  (acc) => `${group.system}:${acc.account_id}`,
                );
                const allSelected = groupKeys.every((key) =>
                  selectedKeys.includes(key),
                );
                return (
                  <section
                    key={group.system}
                    className="origin-multiselect-group"
                  >
                    <header>
                      <strong>{group.system_label}</strong>
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={() => toggleGroup(group)}
                      >
                        {allSelected
                          ? `Quitar todas`
                          : `Seleccionar todas`}
                      </button>
                    </header>
                    <ul>
                      {group.accounts.map((acc) => {
                        const key = `${group.system}:${acc.account_id}`;
                        const isSelected = selectedKeys.includes(key);
                        return (
                          <li
                            key={acc.account_id}
                            role="option"
                            aria-selected={isSelected}
                            className={`origin-multiselect-row${
                              isSelected ? " is-selected" : ""
                            }${acc.enabled ? "" : " is-disabled"}`}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              toggle(group.system, acc.account_id);
                            }}
                          >
                            <span className="origin-multiselect-row-name">
                              {acc.label}
                              {!acc.enabled ? (
                                <span className="muted small">
                                  {" "}
                                  · desactivada
                                </span>
                              ) : null}
                            </span>
                            <span className="muted small">
                              {acc.contacts_count.toLocaleString("es-ES")}
                            </span>
                            {isSelected ? (
                              <Check size={14} aria-hidden />
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                );
              })}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
