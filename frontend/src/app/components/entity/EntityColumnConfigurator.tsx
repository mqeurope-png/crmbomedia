"use client";

/**
 * Sprint Filtros & Listas (PR-Eb) — popover de configuración de
 * columnas con drag-and-drop real vía `@dnd-kit/sortable`.
 *
 * Antes (PR-C) el reorden era con HTML5 drag-and-drop nativo, que
 * en algunos navegadores se quedaba pegado o no daba el feedback
 * correcto. Bart pidió drag-drop con feedback visual claro y orden
 * que persista en la vista guardada / localStorage. dnd-kit ya
 * estaba instalado para `/pipelines/[id]/edit-stages` así que no
 * añade peso al bundle.
 *
 * El listado del configurator muestra:
 *  - Columnas visibles primero, en su orden actual, con drag handle.
 *  - Columnas ocultas después, sin drag handle (toggle del checkbox
 *    las mueve al final de las visibles).
 *  - El padre recibe el array `visible` ordenado en `onApply`.
 */
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  type DragEndEvent,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
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
  // Local draft so cancelling discards changes.
  const [draft, setDraft] = useState<string[]>(visible);
  useEffect(() => setDraft(visible), [visible]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      // Activation distance pequeño pero >0 evita "clicks accidentales"
      // que dispararían drag al togglear el checkbox.
      activationConstraint: { distance: 4 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const displayable = useMemo(
    () => fields.filter((f) => f.displayable),
    [fields],
  );
  const fieldByKey = useMemo(() => {
    const out: Record<string, FieldDescriptor> = {};
    for (const f of displayable) out[f.key] = f;
    return out;
  }, [displayable]);

  const visibleSet = useMemo(() => new Set(draft), [draft]);
  const hiddenKeys = useMemo(
    () => displayable.filter((f) => !visibleSet.has(f.key)).map((f) => f.key),
    [displayable, visibleSet],
  );

  function toggle(key: string) {
    setDraft((cur) =>
      cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key],
    );
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setDraft((cur) => {
      const oldIdx = cur.indexOf(String(active.id));
      const newIdx = cur.indexOf(String(over.id));
      if (oldIdx === -1 || newIdx === -1) return cur;
      const next = cur.slice();
      next.splice(oldIdx, 1);
      next.splice(newIdx, 0, String(active.id));
      return next;
    });
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

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={draft} strategy={verticalListSortingStrategy}>
          <ul className="entity-column-configurator-list">
            {draft.map((key) => {
              const field = fieldByKey[key];
              if (!field) return null;
              return (
                <SortableRow
                  key={key}
                  id={key}
                  field={field}
                  isVisible
                  onToggle={() => toggle(key)}
                />
              );
            })}
          </ul>
        </SortableContext>
      </DndContext>

      {hiddenKeys.length > 0 ? (
        <>
          <p className="entity-column-section-label muted small">
            Ocultas
          </p>
          <ul className="entity-column-configurator-list">
            {hiddenKeys.map((key) => {
              const field = fieldByKey[key];
              if (!field) return null;
              return (
                <li
                  key={key}
                  className="entity-column-row"
                  onClick={() => toggle(key)}
                >
                  <button
                    type="button"
                    className="entity-column-toggle"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(key);
                    }}
                    aria-pressed={false}
                  >
                    <span className="entity-column-toggle-blank" />
                  </button>
                  <span className="entity-column-grip-spacer" aria-hidden />
                  <span className="entity-column-label">{field.label}</span>
                  {field.grouped_under ? (
                    <span className="entity-column-group muted small">
                      {field.grouped_under}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

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

function SortableRow({
  id,
  field,
  isVisible,
  onToggle,
}: {
  id: string;
  field: FieldDescriptor;
  isVisible: boolean;
  onToggle: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`entity-column-row is-visible${isDragging ? " is-dragging" : ""}`}
    >
      <button
        type="button"
        className="entity-column-toggle"
        onClick={onToggle}
        aria-pressed={isVisible}
      >
        <Check size={12} />
      </button>
      <button
        type="button"
        className="entity-column-grip-button"
        aria-label={`Reordenar ${field.label}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical size={12} aria-hidden />
      </button>
      <span className="entity-column-label">{field.label}</span>
      {field.grouped_under ? (
        <span className="entity-column-group muted small">
          {field.grouped_under}
        </span>
      ) : null}
    </li>
  );
}
