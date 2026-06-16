"use client";

/**
 * Sprint Filtros & Listas (PR-C) — `<EntityTable>` genérica.
 *
 * Encima de TanStack Table v8 (headless). Renderiza una página de
 * resultados del endpoint genérico `/api/entities/{entity}/search`
 * usando el esquema declarativo de campos:
 *
 *   - Columnas mostradas = `visibleColumns` (orden + visibility);
 *   - Cabeceras clicables para `sortable` fields (un sólo orden a la
 *     vez en v1; multi-sort queda diferido por decisión de Bart);
 *   - Selección con checkbox por fila + cabecera "todas visibles";
 *   - Paginación de servidor (los datos vienen ya paginados de
 *     `searchEntity`; este componente solo emite el callback).
 *
 * El componente es **controlado**: la pantalla mantiene `rows / total /
 * limit / offset / sort / selection / visibleColumns` y le pasa todo
 * por props + onChange. Esto deja la integración con vistas guardadas y
 * URL state en la pantalla, donde tiene sentido para PR-E/F/G/H.
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
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Header,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Settings } from "lucide-react";
import { useMemo, useState } from "react";
import type { FieldDescriptor } from "../../lib/entitySchema";
import { TagChips } from "../TagChips";
import { EntityColumnConfigurator } from "./EntityColumnConfigurator";

const MAX_TAG_CHIPS = 3;

type Row = Record<string, unknown>;

export type SortState = {
  field: string;
  direction: "asc" | "desc";
};

export type EntityTableProps = {
  fields: FieldDescriptor[];
  visibleColumns: string[];
  onVisibleColumnsChange: (next: string[]) => void;

  rows: Row[];
  total: number;
  limit: number;
  offset: number;
  onPageChange: (nextOffset: number) => void;

  sort: SortState | null;
  onSortChange: (next: SortState | null) => void;

  selection: Set<string>;
  onSelectionChange: (next: Set<string>) => void;

  loading?: boolean;
  /** Override cell rendering for a field. Return `undefined` para
   * delegar al defaultRender genérico (útil para customizar solo
   * algunas columnas, e.g. `owner_user_id` → user name). */
  renderCell?: (field: FieldDescriptor, row: Row) => React.ReactNode | undefined;
  /** Click handler on a row body (excludes the checkbox cell). */
  onRowClick?: (row: Row) => void;
};

const columnHelper = createColumnHelper<Row>();

function defaultRender(field: FieldDescriptor, row: Row): React.ReactNode {
  const value = row[field.key];
  if (value === null || value === undefined || value === "") {
    return <span className="muted">—</span>;
  }
  // PR-Cd: tag-multi columns ship as `[{id, name, color}]` from the
  // backend (`EntityDescriptor.serialize_row` expands `relation='tags'`).
  // Render up to 3 chips + "+N" badge for the overflow so the cell
  // stays narrow.
  if (field.type === "tag-multi" && Array.isArray(value)) {
    const tags = value as Array<{ id: string; name: string; color?: string | null }>;
    if (tags.length === 0) return <span className="muted">—</span>;
    const visible = tags.slice(0, MAX_TAG_CHIPS);
    const overflow = tags.length - visible.length;
    return (
      <span className="entity-table-tag-cell">
        <TagChips tags={visible} size="dense" />
        {overflow > 0 ? (
          <span className="entity-table-tag-overflow muted small">
            +{overflow}
          </span>
        ) : null}
      </span>
    );
  }
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (field.type === "date" || field.type === "datetime") {
    if (typeof value !== "string") return String(value);
    try {
      return new Date(value).toLocaleString("es-ES", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
    } catch {
      return value;
    }
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function EntityTable({
  fields,
  visibleColumns,
  onVisibleColumnsChange,
  rows,
  total,
  limit,
  offset,
  onPageChange,
  sort,
  onSortChange,
  selection,
  onSelectionChange,
  loading = false,
  renderCell,
  onRowClick,
}: EntityTableProps) {
  const [configOpen, setConfigOpen] = useState(false);

  const fieldByKey = useMemo(() => {
    const out: Record<string, FieldDescriptor> = {};
    for (const f of fields) out[f.key] = f;
    return out;
  }, [fields]);

  // PR-Fa: fallback genérico cuando el consumer pasa `visibleColumns=[]`
  // (e.g. la pantalla `/contacts` recibió `?rules=…` por URL sin
  // `view_id` ni columnas guardadas en localStorage; el redirect desde
  // `/marketing/listas/[id]` cae aquí). En ese caso usamos las
  // columnas marcadas `default_visible=true` en el schema declarativo
  // — la fuente de verdad que el backend ya emite por entidad. El
  // consumer no necesita conocer `default_visible` para que la tabla
  // arranque utilizable. La primera vez que el usuario abra el
  // configurator o reordene, su elección se materializa vía
  // `onVisibleColumnsChange` y a partir de ahí manda `visibleColumns`.
  const effectiveColumns = useMemo(() => {
    if (visibleColumns.length > 0) return visibleColumns;
    return fields
      .filter((f) => f.displayable && f.default_visible)
      .map((f) => f.key);
  }, [visibleColumns, fields]);

  // Build TanStack columns from the visible-column order. Selection +
  // sort are owned by the parent (controlled), so we don't pass sort
  // state into TanStack — we just render the header arrows manually.
  const columns = useMemo<ColumnDef<Row, unknown>[]>(() => {
    const select: ColumnDef<Row, unknown> = {
      id: "_select",
      header: () => {
        const visibleIds = rows.map((r) => String(r.id));
        const allSelected =
          visibleIds.length > 0 && visibleIds.every((id) => selection.has(id));
        const someSelected = visibleIds.some((id) => selection.has(id));
        return (
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = !allSelected && someSelected;
            }}
            onChange={(e) => {
              const next = new Set(selection);
              if (e.target.checked) {
                for (const id of visibleIds) next.add(id);
              } else {
                for (const id of visibleIds) next.delete(id);
              }
              onSelectionChange(next);
            }}
            aria-label="Seleccionar todas las filas visibles"
          />
        );
      },
      cell: (info) => {
        const id = String(info.row.original.id);
        const checked = selection.has(id);
        return (
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => {
              e.stopPropagation();
              const next = new Set(selection);
              if (e.target.checked) next.add(id);
              else next.delete(id);
              onSelectionChange(next);
            }}
            onClick={(e) => e.stopPropagation()}
            aria-label="Seleccionar fila"
          />
        );
      },
      enableSorting: false,
    };

    const dataCols = effectiveColumns
      .map((key) => fieldByKey[key])
      .filter((f): f is FieldDescriptor => Boolean(f && f.displayable))
      .map((field) =>
        columnHelper.display({
          id: field.key,
          header: field.label,
          cell: (info) => {
            // PR-E: renderCell devuelve undefined → fallback al
            // defaultRender genérico. Permite personalizar solo
            // contadas columnas (e.g. owner_user_id → user.full_name).
            if (renderCell) {
              const custom = renderCell(field, info.row.original);
              if (custom !== undefined) return custom;
            }
            return defaultRender(field, info.row.original);
          },
        }),
      );

    return [select, ...dataCols];
  }, [
    fieldByKey,
    effectiveColumns,
    rows,
    selection,
    onSelectionChange,
    renderCell,
  ]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(row.id),
  });

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.min(totalPages, Math.floor(offset / limit) + 1);

  function cycleSort(field: FieldDescriptor) {
    if (!field.sortable) return;
    if (!sort || sort.field !== field.key) {
      onSortChange({ field: field.key, direction: "asc" });
      return;
    }
    if (sort.direction === "asc") {
      onSortChange({ field: field.key, direction: "desc" });
      return;
    }
    onSortChange(null); // back to default
  }

  // PR-Eb: drag-reorder de columnas desde los headers. dnd-kit ya
  // está en package.json. PointerSensor activación a 6px evita
  // confundir un click de sort con un drag accidental.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  function handleHeaderDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    // PR-Fa: arranca desde `effectiveColumns` para que el reorden
    // sobre el set por defecto materialise una elección explícita —
    // a partir de ahí el consumer manda el array no-vacío.
    const oldIdx = effectiveColumns.indexOf(String(active.id));
    const newIdx = effectiveColumns.indexOf(String(over.id));
    if (oldIdx === -1 || newIdx === -1) return;
    const next = effectiveColumns.slice();
    next.splice(oldIdx, 1);
    next.splice(newIdx, 0, String(active.id));
    onVisibleColumnsChange(next);
  }

  return (
    <div className="entity-table-wrapper">
      <div className="entity-table-toolbar">
        <div className="entity-table-toolbar-left muted small">
          {loading ? "Cargando…" : `${total} resultados`}
        </div>
        <div className="entity-table-toolbar-right">
          <button
            type="button"
            className="button secondary small"
            onClick={() => setConfigOpen((v) => !v)}
            aria-expanded={configOpen}
          >
            <Settings size={12} aria-hidden /> Columnas
          </button>
        </div>
        {configOpen ? (
          <div className="entity-table-configurator-popover">
            <EntityColumnConfigurator
              fields={fields}
              visible={effectiveColumns}
              onApply={onVisibleColumnsChange}
              onClose={() => setConfigOpen(false)}
            />
          </div>
        ) : null}
      </div>

      <table className="entity-table">
        <thead>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleHeaderDragEnd}
          >
            <SortableContext
              items={effectiveColumns}
              strategy={horizontalListSortingStrategy}
            >
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => {
                    const headerCol = header.column.id;
                    const field = fieldByKey[headerCol];
                    const isSelectCol = headerCol === "_select";
                    if (isSelectCol) {
                      return (
                        <th key={header.id} className="entity-table-th">
                          <span className="entity-table-th-inner">
                            {flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )}
                          </span>
                        </th>
                      );
                    }
                    return (
                      <SortableHeader
                        key={header.id}
                        header={header}
                        field={field}
                        sort={sort}
                        onCycleSort={cycleSort}
                      />
                    );
                  })}
                </tr>
              ))}
            </SortableContext>
          </DndContext>
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 ? (
            <tr>
              <td
                colSpan={effectiveColumns.length + 1}
                className="entity-table-empty muted"
              >
                {loading ? "Cargando…" : "Sin resultados."}
              </td>
            </tr>
          ) : (
            table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className={`entity-table-row${
                  selection.has(row.id) ? " is-selected" : ""
                }${onRowClick ? " is-clickable" : ""}`}
                onClick={
                  onRowClick ? () => onRowClick(row.original) : undefined
                }
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="entity-table-td">
                    {flexRender(
                      cell.column.columnDef.cell,
                      cell.getContext(),
                    )}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>

      <nav className="entity-table-pagination" aria-label="Paginación">
        <button
          type="button"
          className="button secondary small"
          disabled={offset <= 0 || loading}
          onClick={() => onPageChange(Math.max(0, offset - limit))}
        >
          Anterior
        </button>
        <span className="muted small">
          Página {currentPage} / {totalPages}
        </span>
        <button
          type="button"
          className="button secondary small"
          disabled={offset + limit >= total || loading}
          onClick={() => onPageChange(offset + limit)}
        >
          Siguiente
        </button>
      </nav>
    </div>
  );
}

/**
 * Header reordenable (PR-Eb). Mismo render que el header normal +
 * los atributos de dnd-kit. La activación a 6px de PointerSensor
 * desambigua el click-para-sort del drag-para-reorder.
 */
function SortableHeader({
  header,
  field,
  sort,
  onCycleSort,
}: {
  header: Header<Row, unknown>;
  field: FieldDescriptor | undefined;
  sort: SortState | null;
  onCycleSort: (field: FieldDescriptor) => void;
}) {
  const id = header.column.id;
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
    position: "relative",
  };

  const isActiveSort = sort?.field === id;
  return (
    <th
      ref={setNodeRef}
      style={style}
      className={`entity-table-th${
        field?.sortable ? " is-sortable" : ""
      }${isActiveSort ? " is-active-sort" : ""}${isDragging ? " is-dragging" : ""}`}
      onClick={field?.sortable ? () => onCycleSort(field) : undefined}
      aria-sort={
        isActiveSort
          ? sort.direction === "asc"
            ? "ascending"
            : "descending"
          : undefined
      }
      {...attributes}
      {...listeners}
    >
      <span className="entity-table-th-inner">
        {flexRender(header.column.columnDef.header, header.getContext())}
        {field?.sortable ? (
          isActiveSort ? (
            sort.direction === "asc" ? (
              <ArrowUp size={11} aria-hidden />
            ) : (
              <ArrowDown size={11} aria-hidden />
            )
          ) : (
            <ArrowUpDown size={11} aria-hidden className="muted" />
          )
        ) : null}
      </span>
    </th>
  );
}
