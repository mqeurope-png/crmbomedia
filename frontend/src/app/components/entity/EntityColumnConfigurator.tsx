"use client";

/**
 * Sprint Filtros & Listas (PR-C) — popover para mostrar/ocultar y
 * reordenar columnas de un `<EntityTable>`.
 *
 * Generalización del `ColumnConfigurator` específico de contactos.
 * El listado de columnas viene del `filter-schema` de la entidad
 * (solo campos con `displayable: true`). El callback `onApply` emite
 * el nuevo array de keys visibles en orden — la pantalla decide si lo
 * persiste en `entity_views.columns_json` o en localStorage.
 */
import { Check, GripVertical, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FieldDescriptor } from "../../lib/entitySchema";

type Props = {
  fields: FieldDescriptor[];
  visible: string[]; // ordered list of currently-shown column keys
  onApply: (next: string[]) => void;
  onClose: () => void;
};

export function EntityColumnConfigurator({
  fields,
  visible,
  onApply,
  onClose,
}: Props) {
  // Local draft so cancelling discards changes. Initialised once per open.
  const [draft, setDraft] = useState<string[]>(visible);
  const [dragKey, setDragKey] = useState<string | null>(null);
  useEffect(() => setDraft(visible), [visible]);

  const displayable = useMemo(
    () => fields.filter((f) => f.displayable),
    [fields],
  );

  const visibleSet = useMemo(() => new Set(draft), [draft]);

  // Ordered list = draft (visible) first, then non-visible in the
  // order they appear in the schema. Always-visible fields (like a
  // primary "name" column on contacts) are not forced here; the
  // schema doesn't model `alwaysVisible` yet, so we rely on the
  // entity's spec design to put critical fields first.
  const ordered = useMemo(() => {
    const out: { key: string; visible: boolean; field: FieldDescriptor }[] = [];
    for (const key of draft) {
      const field = displayable.find((f) => f.key === key);
      if (field) out.push({ key, visible: true, field });
    }
    for (const f of displayable) {
      if (!visibleSet.has(f.key)) out.push({ key: f.key, visible: false, field: f });
    }
    return out;
  }, [draft, displayable, visibleSet]);

  function toggle(key: string) {
    setDraft((cur) =>
      cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key],
    );
  }

  function handleDrop(targetKey: string) {
    if (!dragKey || dragKey === targetKey) return;
    setDraft((cur) => {
      const without = cur.filter((k) => k !== dragKey);
      const targetIdx = without.indexOf(targetKey);
      if (targetIdx === -1) {
        // dropping onto a hidden row → put dragged at the end of visible
        return [...without, dragKey];
      }
      return [
        ...without.slice(0, targetIdx),
        dragKey,
        ...without.slice(targetIdx),
      ];
    });
    setDragKey(null);
  }

  return (
    <div className="entity-column-configurator" role="dialog">
      <header className="entity-column-configurator-header">
        <strong>Columnas</strong>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="Cerrar"
        >
          <X size={14} />
        </button>
      </header>
      <ul className="entity-column-configurator-list">
        {ordered.map(({ key, visible: isVisible, field }) => (
          <li
            key={key}
            className={`entity-column-row${isVisible ? " is-visible" : ""}`}
            draggable={isVisible}
            onDragStart={() => setDragKey(key)}
            onDragOver={(e) => {
              if (dragKey && isVisible) e.preventDefault();
            }}
            onDrop={() => handleDrop(key)}
          >
            <button
              type="button"
              className="entity-column-toggle"
              onClick={() => toggle(key)}
              aria-pressed={isVisible}
            >
              {isVisible ? <Check size={12} /> : <span className="entity-column-toggle-blank" />}
            </button>
            {isVisible ? (
              <GripVertical
                size={12}
                className="entity-column-grip"
                aria-hidden
              />
            ) : (
              <span className="entity-column-grip-spacer" aria-hidden />
            )}
            <span className="entity-column-label">{field.label}</span>
            {field.grouped_under ? (
              <span className="entity-column-group muted small">
                {field.grouped_under}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      <footer className="entity-column-configurator-actions">
        <button
          type="button"
          className="button secondary small"
          onClick={onClose}
        >
          Cancelar
        </button>
        <button
          type="button"
          className="button small"
          onClick={() => {
            onApply(draft);
            onClose();
          }}
        >
          Aplicar
        </button>
      </footer>
    </div>
  );
}
