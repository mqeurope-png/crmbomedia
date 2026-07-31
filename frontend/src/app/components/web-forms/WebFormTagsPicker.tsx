"use client";

import { useEffect, useState } from "react";
import { getTagsSelectable, type TagOption } from "../../lib/formsApi";

type TagValue = { value?: string; label: string; tag_id?: string };

/** v2 Bug 2. Sub-panel de un campo tipo `tags`: autocomplete de tags
 *  reales del CRM + chips de los seleccionados. El `options_json` se
 *  guarda como [{tag_id, label}] (label capturado al añadir, tolerante a
 *  renombrados posteriores de la tag). */
export function WebFormTagsPicker({
  value,
  onChange,
  fieldIndex,
}: {
  value: TagValue[];
  onChange: (opts: TagValue[]) => void;
  fieldIndex: number;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<TagOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    getTagsSelectable(search)
      .then((r) => {
        if (!cancelled) setResults(r);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [search]);

  const selectedIds = new Set(value.map((v) => v.tag_id));

  function add(t: TagOption) {
    if (selectedIds.has(t.id)) return;
    onChange([...value, { tag_id: t.id, label: t.name }]);
  }
  function remove(id?: string) {
    onChange(value.filter((v) => v.tag_id !== id));
  }

  return (
    <div className="wf-field-options" aria-label={`Tags campo ${fieldIndex + 1}`}>
      <span className="muted small">Tags disponibles como opciones</span>
      <input
        type="text"
        placeholder="Buscar tag del CRM…"
        value={search}
        aria-label={`Buscar tag campo ${fieldIndex + 1}`}
        onChange={(e) => setSearch(e.target.value)}
      />
      {results.filter((t) => !selectedIds.has(t.id)).length > 0 ? (
        <ul className="wf-tag-results">
          {results
            .filter((t) => !selectedIds.has(t.id))
            .slice(0, 8)
            .map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className="button small secondary"
                  onClick={() => add(t)}
                >
                  + {t.name}
                </button>
              </li>
            ))}
        </ul>
      ) : null}
      <div className="wf-tag-chips">
        {value.map((v) => (
          <span className="wf-tag-chip" key={v.tag_id ?? v.label}>
            {v.label}
            <button
              type="button"
              aria-label={`Quitar tag ${v.label}`}
              onClick={() => remove(v.tag_id)}
            >
              ×
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
