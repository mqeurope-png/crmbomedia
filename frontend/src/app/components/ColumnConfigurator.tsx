"use client";

import { useEffect, useRef, useState } from "react";
import {
  CONTACT_COLUMNS,
  type ContactColumnKey,
  findColumn,
} from "../lib/contactColumns";

type Props = {
  /** Source-of-truth ordering, including hidden keys at the tail. */
  order: ContactColumnKey[];
  visible: ContactColumnKey[];
  onApply: (next: { order: ContactColumnKey[]; visible: ContactColumnKey[] }) => void;
};

/**
 * Popover with two interactions:
 *   1. Checkbox toggles per row to flip visibility.
 *   2. Drag handle (☰) reorders the list — native HTML5 DnD; no
 *      external dependency.
 *
 * "Apply" emits the new (order, visible) and closes; "Cancel" drops
 * pending edits. We don't auto-apply on each tweak so the operator
 * can rearrange + uncheck multiple rows without paying for re-render
 * mid-edit.
 */
export function ColumnConfigurator({ order, visible, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [localOrder, setLocalOrder] = useState<ContactColumnKey[]>(order);
  const [localVisible, setLocalVisible] = useState<ContactColumnKey[]>(visible);
  const wrapper = useRef<HTMLDivElement>(null);
  const dragKey = useRef<ContactColumnKey | null>(null);

  useEffect(() => {
    if (open) {
      setLocalOrder(order);
      setLocalVisible(visible);
    }
  }, [open, order, visible]);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function toggleVisibility(key: ContactColumnKey) {
    if (findColumn(key)?.alwaysVisible) return;
    setLocalVisible((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    );
  }

  function reorder(source: ContactColumnKey, target: ContactColumnKey) {
    if (source === target) return;
    setLocalOrder((current) => {
      const next = current.filter((k) => k !== source);
      const idx = next.indexOf(target);
      if (idx === -1) return current;
      next.splice(idx, 0, source);
      return next;
    });
  }

  function apply() {
    onApply({ order: localOrder, visible: localVisible });
    setOpen(false);
  }

  return (
    <div ref={wrapper} className="column-config">
      <button
        type="button"
        className="button secondary small"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        ⚙️ Columnas
      </button>
      {open ? (
        <div className="column-config-panel" role="dialog">
          <p className="muted small">
            Arrastra para reordenar. Desmarca para ocultar.
          </p>
          <ul className="column-config-list">
            {localOrder.map((key) => {
              const def = findColumn(key);
              if (!def) return null;
              const isVisible = localVisible.includes(key);
              return (
                <li
                  key={key}
                  className={`column-config-row${isVisible ? "" : " is-hidden"}`}
                  draggable
                  onDragStart={() => {
                    dragKey.current = key;
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (dragKey.current && dragKey.current !== key) {
                      reorder(dragKey.current, key);
                    }
                    dragKey.current = null;
                  }}
                >
                  <span className="column-config-handle" aria-hidden>
                    ☰
                  </span>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={isVisible}
                      disabled={def.alwaysVisible}
                      onChange={() => toggleVisibility(key)}
                    />
                    <span>{def.label}</span>
                  </label>
                </li>
              );
            })}
            {CONTACT_COLUMNS.filter(
              (column) => !localOrder.includes(column.key),
            ).map((column) => {
              const key = column.key;
              const isVisible = localVisible.includes(key);
              return (
                <li
                  key={key}
                  className={`column-config-row${isVisible ? "" : " is-hidden"}`}
                  draggable
                  onDragStart={() => {
                    dragKey.current = key;
                    setLocalOrder((current) =>
                      current.includes(key) ? current : [...current, key],
                    );
                  }}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => {
                    dragKey.current = null;
                  }}
                >
                  <span className="column-config-handle" aria-hidden>
                    ☰
                  </span>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={isVisible}
                      disabled={column.alwaysVisible}
                      onChange={() => {
                        toggleVisibility(key);
                        setLocalOrder((current) =>
                          current.includes(key) ? current : [...current, key],
                        );
                      }}
                    />
                    <span>{column.label}</span>
                  </label>
                </li>
              );
            })}
          </ul>
          <div className="form-actions">
            <button type="button" className="button small" onClick={apply}>
              Aplicar
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => setOpen(false)}
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
