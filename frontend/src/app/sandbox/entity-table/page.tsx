"use client";

/**
 * Sprint Filtros & Listas (PR-C) — sandbox visual.
 *
 * Monta `<EntityTable>` + `<EntityFilterBuilder>` + `<EntityViewsTabs>`
 * contra el endpoint real `/api/entities/{entity}/search` para validar
 * la integración sin tocar ninguna pantalla de producción (las
 * migraciones reales viven en PR-E…H).
 *
 * URL: `/sandbox/entity-table?entity=company` (o cualquier entidad
 * registrada en el backend). La ruta queda permanente — no hace daño
 * en prod porque nadie la enlaza; un operador con la URL puede usarla
 * para probar manualmente.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { EntityFilterBuilder } from "../../components/entity/EntityFilterBuilder";
import { EntityTable, type SortState } from "../../components/entity/EntityTable";
import { EntityViewsTabs } from "../../components/entity/EntityViewsTabs";
import { extractErrorMessage } from "../../lib/errors";
import {
  getEntityFilterSchema,
  searchEntity,
  type EntityFilterSchema,
  type EntityKey,
  type FieldDescriptor,
} from "../../lib/entitySchema";
import {
  loadColumnConfig,
  saveColumnConfig,
} from "../../lib/entityColumnsStorage";
import {
  createEntityView,
  deleteEntityView,
  duplicateEntityView,
  listEntityViews,
  setDefaultEntityView,
  type EntityView,
} from "../../lib/entityViewsApi";
import { pruneRulesTree } from "../../lib/segmentTranslator";

const SUPPORTED: EntityKey[] = [
  "contact",
  "company",
  "email_thread",
  "brevo_template",
  "brevo_campaign",
];

function readEntityFromUrl(): EntityKey {
  if (typeof window === "undefined") return "company";
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("entity");
  return (SUPPORTED as string[]).includes(raw ?? "")
    ? (raw as EntityKey)
    : "company";
}

const PAGE_SIZE = 25;

export default function EntityTableSandboxPage() {
  const [entity, setEntity] = useState<EntityKey>(() => readEntityFromUrl());
  const [schema, setSchema] = useState<EntityFilterSchema | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  const [sort, setSort] = useState<SortState | null>(null);
  const [offset, setOffset] = useState(0);
  const [rules, setRules] = useState<Record<string, unknown>>({});
  const [selection, setSelection] = useState<Set<string>>(new Set());

  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const [views, setViews] = useState<EntityView[]>([]);
  const [activeViewId, setActiveViewId] = useState<string | null>(null);

  // 1. Load the entity's filter schema + default visible columns +
  //    saved views whenever the entity selection changes.
  useEffect(() => {
    let cancelled = false;
    setSchema(null);
    setError(null);
    Promise.all([
      getEntityFilterSchema(entity),
      listEntityViews(entity).catch(() => [] as EntityView[]),
    ])
      .then(([sch, viewList]) => {
        if (cancelled) return;
        setSchema(sch);
        const defaults = sch.fields
          .filter((f) => f.displayable && f.default_visible)
          .map((f) => f.key);
        const stored = loadColumnConfig(entity, defaults);
        setVisibleColumns(stored.visible);
        setSort({
          field: sch.default_sort,
          direction: sch.default_sort_dir,
        });
        setOffset(0);
        setRules({});
        setSelection(new Set());
        setViews(viewList);
        const def = viewList.find((v) => v.is_default);
        setActiveViewId(def?.id ?? null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(extractErrorMessage(err, "No se pudo cargar el esquema."));
      });
    return () => {
      cancelled = true;
    };
  }, [entity]);

  // 2. Fetch the page whenever filters / sort / offset / entity changes.
  useEffect(() => {
    if (!schema) return;
    let cancelled = false;
    setLoading(true);
    // PR-Cb hotfix: prune half-typed rules (e.g. a Tags rule whose
    // multi-select picker is still empty) so the engine doesn't 400
    // with "Comparator 'contains_any' requires a non-empty list". The
    // builder state stays unpruned so the half-typed rule remains
    // visible to the operator while they fill it in.
    const pruned = pruneRulesTree(
      rules,
      schema.fields.map((f) => ({ key: f.key, type: f.type })),
    );
    searchEntity(entity, {
      rules_json: Object.keys(pruned).length ? pruned : null,
      sort_by: sort?.field ?? schema.default_sort,
      sort_dir: sort?.direction ?? schema.default_sort_dir,
      limit: PAGE_SIZE,
      offset,
    })
      .then((page) => {
        if (cancelled) return;
        setRows(page.items as Record<string, unknown>[]);
        setTotal(page.total);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(extractErrorMessage(err, "No se pudo cargar la lista."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entity, schema, rules, sort, offset]);

  const handleColumnsChange = useCallback(
    (next: string[]) => {
      setVisibleColumns(next);
      saveColumnConfig(entity, { visible: next });
    },
    [entity],
  );

  const handleSelectView = useCallback(
    (view: EntityView | null) => {
      setActiveViewId(view ? view.id : null);
      if (!view) {
        setRules({});
        return;
      }
      const tree =
        (view.filters.rules_json as Record<string, unknown> | undefined) ?? {};
      setRules(tree);
      const visible = view.columns.visible;
      if (visible && visible.length) setVisibleColumns(visible);
      if (view.sort?.sort_by) {
        setSort({
          field: view.sort.sort_by,
          direction: view.sort.sort_dir === "asc" ? "asc" : "desc",
        });
      }
      setOffset(0);
    },
    [],
  );

  const fields: FieldDescriptor[] = useMemo(
    () => schema?.fields ?? [],
    [schema],
  );

  if (!schema) {
    return (
      <div className="page-container">
        <h2>Entity sandbox</h2>
        {error ? <p className="form-error">{error}</p> : <p>Cargando…</p>}
      </div>
    );
  }

  return (
    <div className="page-container">
      <header style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>Sandbox · {schema.label}</h2>
          <p className="muted small" style={{ margin: 0 }}>
            `/sandbox/entity-table?entity={entity}` · sólo verificación visual
          </p>
        </div>
        <select
          value={entity}
          onChange={(e) => setEntity(e.target.value as EntityKey)}
        >
          {SUPPORTED.map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      </header>

      <section style={{ marginTop: 12 }}>
        <EntityViewsTabs
          views={views}
          activeId={activeViewId}
          isDirty={false}
          onSelect={handleSelectView}
          onCreate={() => {
            const name = window.prompt("Nombre de la vista:");
            if (!name) return;
            createEntityView(entity, {
              name,
              filters:
                Object.keys(rules).length ? { rules_json: rules } : {},
              columns: { visible: visibleColumns },
              sort: sort
                ? { sort_by: sort.field, sort_dir: sort.direction }
                : {},
            })
              .then((view) => {
                setViews((cur) => [...cur, view]);
                setActiveViewId(view.id);
              })
              .catch((err) =>
                setError(extractErrorMessage(err, "No se pudo crear.")),
              );
          }}
          onEdit={() => {
            window.alert("Edición pendiente (PR-D/E)");
          }}
          onDuplicate={(view) => {
            duplicateEntityView(entity, view.id)
              .then((dup) => setViews((cur) => [...cur, dup]))
              .catch((err) =>
                setError(extractErrorMessage(err, "No se pudo duplicar.")),
              );
          }}
          onSetDefault={(view) => {
            setDefaultEntityView(entity, view.id)
              .then(() => listEntityViews(entity))
              .then(setViews)
              .catch((err) =>
                setError(extractErrorMessage(err, "No se pudo marcar.")),
              );
          }}
          onDelete={(view) => {
            if (!window.confirm(`Borrar "${view.name}"?`)) return;
            deleteEntityView(entity, view.id)
              .then(() => {
                setViews((cur) => cur.filter((v) => v.id !== view.id));
                if (activeViewId === view.id) setActiveViewId(null);
              })
              .catch((err) =>
                setError(extractErrorMessage(err, "No se pudo borrar.")),
              );
          }}
        />
      </section>

      <section style={{ marginTop: 12 }}>
        <h4 style={{ margin: "0 0 6px 0" }}>Filtros</h4>
        <EntityFilterBuilder
          fields={fields}
          value={rules}
          onChange={(next) => {
            setRules(next);
            setOffset(0);
          }}
        />
      </section>

      {error ? (
        <p className="form-error" style={{ marginTop: 12 }}>
          {error}
        </p>
      ) : null}

      <section style={{ marginTop: 12 }}>
        <EntityTable
          fields={fields}
          visibleColumns={visibleColumns}
          onVisibleColumnsChange={handleColumnsChange}
          rows={rows}
          total={total}
          limit={PAGE_SIZE}
          offset={offset}
          onPageChange={setOffset}
          sort={sort}
          onSortChange={setSort}
          selection={selection}
          onSelectionChange={setSelection}
          loading={loading}
        />
      </section>
    </div>
  );
}
