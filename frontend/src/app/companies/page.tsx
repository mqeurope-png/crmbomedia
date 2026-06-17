"use client";

/**
 * Sprint Filtros & Listas — PR-F. `/companies` rehecha sobre el stack
 * nuevo (PR-A → PR-Eb):
 *
 *  - `<EntityTable>` (TanStack) sustituye el `<table>` inline +
 *    columnas hardcoded del legacy.
 *  - `<EntityFilterBuilder>` (RQB advanced) sustituye los 4 selects
 *    locales (q + country + source + has_contacts) — todos los
 *    campos del schema declarativo de Company están disponibles
 *    como filtro AND/OR/NOT.
 *  - `<EntityViewsTabs>` + `/api/entity-views/company` aporta vistas
 *    guardadas (la pantalla legacy no tenía).
 *  - URL state genérico (view_id, rules, q, sort, cols).
 *  - Bulk actions nuevas: activate, deactivate, change_sector vía
 *    `POST /api/companies/bulk-action` (PR-F backend).
 *  - Click en fila → `/companies/[id]`.
 *
 * Cierre de **Deuda #1** del backlog: paginación (ya funcionaba pero
 * ahora compartida con todas las entidades) + bulk actions + sort
 * por cabecera + filtros en URL + columnas configurables.
 *
 * Limitación conocida (deuda menor, no bloqueante): la columna
 * #Contactos del legacy no se expone en el schema de Company
 * (`fields_company.py` solo lista columnas; el contador requiere
 * subquery). Si lo necesitamos, se añade en una iteración aparte.
 */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorState } from "../components/ErrorState";
import { PageHeader } from "../components/PageHeader";
import { EntityFilterBuilder } from "../components/entity/EntityFilterBuilder";
import {
  EntityTable,
  type SortState,
} from "../components/entity/EntityTable";
import { EntityViewsTabs } from "../components/entity/EntityViewsTabs";
import {
  bulkCompanyAction,
  type CompanyBulkAction,
} from "../lib/companiesApi";
import {
  loadColumnConfig,
  saveColumnConfig,
} from "../lib/entityColumnsStorage";
import {
  getEntityFilterSchema,
  searchEntity,
  searchEntityIds,
  type EntityFilterSchema,
} from "../lib/entitySchema";
import {
  createEntityView,
  deleteEntityView,
  duplicateEntityView,
  listEntityViews,
  setDefaultEntityView,
  updateEntityView,
  type EntityView,
} from "../lib/entityViewsApi";
import { extractErrorMessage } from "../lib/errors";
import { pruneRulesTree } from "../lib/segmentTranslator";

const PAGE_SIZE = 50;
const EMPTY_RULES: Record<string, unknown> = {};

type CompanyRow = Record<string, unknown>;

export default function CompaniesListPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [schema, setSchema] = useState<EntityFilterSchema | null>(null);
  const [views, setViews] = useState<EntityView[]>([]);
  const firstLoadRef = useRef(true);

  const [rules, setRules] = useState<Record<string, unknown>>(EMPTY_RULES);
  const [q, setQ] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [sort, setSort] = useState<SortState | null>({
    field: "name",
    direction: "asc",
  });

  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeView, setActiveView] = useState<EntityView | null>(null);
  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  const [builderKey, setBuilderKey] = useState(0);

  const [rows, setRows] = useState<CompanyRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [editorMode, setEditorMode] = useState<
    | { kind: "create" }
    | { kind: "edit"; view: EntityView }
    | null
  >(null);

  const reloadViews = useCallback(async () => {
    const list = await listEntityViews("company");
    setViews(list);
    return list;
  }, []);

  const applyView = useCallback((view: EntityView) => {
    setActiveView(view);
    const filters = view.filters as Record<string, unknown>;
    const treeFromView =
      (filters?.rules_json as Record<string, unknown> | undefined) ?? {};
    setRules(treeFromView);
    setQ((filters?.q as string) ?? "");
    setSearchInput((filters?.q as string) ?? "");
    if (view.sort?.sort_by) {
      setSort({
        field: view.sort.sort_by,
        direction: view.sort.sort_dir === "desc" ? "desc" : "asc",
      });
    } else {
      setSort({ field: "name", direction: "asc" });
    }
    if (view.columns?.visible && view.columns.visible.length > 0) {
      setVisibleColumns(view.columns.visible);
    }
    setOffset(0);
    setBuilderKey((k) => k + 1);
  }, []);

  // Carga inicial
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sch, viewList] = await Promise.all([
          getEntityFilterSchema("company"),
          listEntityViews("company").catch(() => [] as EntityView[]),
        ]);
        if (cancelled) return;
        setSchema(sch);
        setViews(viewList);

        if (firstLoadRef.current) {
          firstLoadRef.current = false;
          // PR-E4 (B): URL → si vacía, restaurar última vista de
          // localStorage antes del default.
          let qsToHydrate = searchParams.toString();
          if (!qsToHydrate) {
            const stored = readStoredView();
            if (stored) qsToHydrate = stored;
          }
          const urlState = readUrlState(new URLSearchParams(qsToHydrate));
          const applyCommon = () => {
            if (urlState.sortBy)
              setSort({
                field: urlState.sortBy,
                direction: urlState.sortDir ?? "asc",
              });
            if (urlState.offset !== null) setOffset(urlState.offset);
            if (urlState.columns && urlState.columns.length > 0)
              setVisibleColumns(urlState.columns);
          };
          if (urlState.viewId) {
            const view = viewList.find((v) => v.id === urlState.viewId);
            if (view) {
              applyView(view);
              // PR-E4b: rules dirty encima de la vista — sobrescriben el
              // baseline después del applyView.
              if (urlState.rules) {
                setRules(urlState.rules);
              }
              if (urlState.q) {
                setQ(urlState.q);
                setSearchInput(urlState.q);
              }
              applyCommon();
              return;
            }
          }
          if (urlState.rules) {
            setRules(urlState.rules);
            setQ(urlState.q ?? "");
            setSearchInput(urlState.q ?? "");
            applyCommon();
            return;
          }
          const def = viewList.find((v) => v.is_default);
          if (def) {
            applyView(def);
            applyCommon();
            return;
          }
          if (!urlState.columns || urlState.columns.length === 0) {
            const defaults = sch.fields
              .filter((f) => f.displayable && f.default_visible)
              .map((f) => f.key);
            const stored = loadColumnConfig("company", defaults);
            setVisibleColumns(stored.visible);
          }
          applyCommon();
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            extractErrorMessage(
              err,
              "No se pudo cargar el esquema de empresas.",
            ),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // PR-E4b: ver explicación en /contacts. Si el operador encadena
  // reglas extra encima de una vista, hay que serializarlas o el
  // browser back las pierde.
  const viewBaselineRules = useMemo(() => {
    if (!activeView) return null;
    const filters = activeView.filters as Record<string, unknown> | undefined;
    return (filters?.rules_json as Record<string, unknown> | undefined) ?? {};
  }, [activeView]);
  const rulesAreDirty = useMemo(() => {
    if (!activeView) return false;
    return (
      JSON.stringify(rules ?? {}) !== JSON.stringify(viewBaselineRules ?? {})
    );
  }, [activeView, rules, viewBaselineRules]);

  // URL sync
  useEffect(() => {
    if (firstLoadRef.current) return;
    const params = serializeUrlState({
      viewId: activeView?.id ?? null,
      rules: !activeView || rulesAreDirty ? rules : null,
      q,
      sortBy: sort?.field ?? "name",
      sortDir: sort?.direction ?? "asc",
      columns: visibleColumns,
      offset,
    });
    const next = params ? `/companies?${params}` : "/companies";
    router.replace(next, { scroll: false });
    writeStoredView(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, rules, rulesAreDirty, q, sort, visibleColumns, offset]);

  // Debounce búsqueda libre → q
  useEffect(() => {
    const handle = setTimeout(() => setQ(searchInput.trim()), 250);
    return () => clearTimeout(handle);
  }, [searchInput]);

  // Fetch
  const fetchKey = useMemo(
    () => JSON.stringify({ rules, q, sort, offset }),
    [rules, q, sort, offset],
  );

  useEffect(() => {
    if (!schema) return;
    let cancelled = false;
    setLoading(true);
    const liteSpecs = schema.fields.map((f) => ({
      key: f.key,
      type: f.type,
    }));
    const branches: Record<string, unknown>[] = [];
    if (Object.keys(rules).length > 0) branches.push(rules);
    const trimmed = q.trim();
    if (trimmed) {
      branches.push({
        operator: "OR",
        children: [
          { type: "rule", field: "name", comparator: "contains", value: trimmed },
          { type: "rule", field: "domain", comparator: "contains", value: trimmed },
          { type: "rule", field: "tax_id", comparator: "contains", value: trimmed },
        ],
      });
    }
    const effective =
      branches.length === 0
        ? null
        : branches.length === 1
          ? branches[0]
          : { operator: "AND", children: branches };
    const pruned = effective
      ? pruneRulesTree(effective as Record<string, unknown>, liteSpecs)
      : {};
    searchEntity<CompanyRow>("company", {
      rules_json: Object.keys(pruned).length ? pruned : null,
      sort_by: sort?.field ?? "name",
      sort_dir: sort?.direction ?? "asc",
      limit: PAGE_SIZE,
      offset,
    })
      .then((page) => {
        if (cancelled) return;
        setRows(page.items);
        setTotal(page.total);
        const visible = new Set(page.items.map((r) => String(r.id)));
        setSelected((prev) => {
          const next = new Set<string>();
          prev.forEach((id) => {
            if (visible.has(id)) next.add(id);
          });
          return next;
        });
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          extractErrorMessage(err, "No se pudieron cargar las empresas."),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey, schema]);

  function buildViewPayload() {
    return {
      filters: {
        q: q || null,
        rules_json: Object.keys(rules).length > 0 ? rules : null,
      },
      columns: { visible: visibleColumns, order: visibleColumns, widths: {} },
      sort: sort
        ? { sort_by: sort.field, sort_dir: sort.direction }
        : { sort_by: "name", sort_dir: "asc" as const },
    };
  }

  async function handleSaveExistingView() {
    if (!activeView) {
      setEditorMode({ kind: "create" });
      return;
    }
    try {
      const updated = await updateEntityView(
        "company",
        activeView.id,
        buildViewPayload(),
      );
      setActiveView(updated);
      await reloadViews();
      setMessage("Vista guardada.");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la vista."));
    }
  }

  async function handleSaveView(draft: {
    name: string;
    description: string;
    isShared: boolean;
  }) {
    const payload = {
      name: draft.name,
      description: draft.description || null,
      is_shared: draft.isShared,
      ...buildViewPayload(),
    };
    if (editorMode?.kind === "edit") {
      const updated = await updateEntityView(
        "company",
        editorMode.view.id,
        payload,
      );
      setActiveView(updated);
    } else {
      const created = await createEntityView("company", payload);
      setActiveView(created);
    }
    await reloadViews();
    setEditorMode(null);
  }

  async function handleDuplicateView(view: EntityView) {
    try {
      const copy = await duplicateEntityView("company", view.id);
      await reloadViews();
      applyView(copy);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar la vista."));
    }
  }

  async function handleSetDefault(view: EntityView) {
    try {
      if (view.is_default) {
        await updateEntityView("company", view.id, { is_default: false });
      } else {
        await setDefaultEntityView("company", view.id);
      }
      const list = await reloadViews();
      const refreshed = list.find((v) => v.id === view.id);
      if (refreshed && activeView?.id === view.id) setActiveView(refreshed);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar la vista."));
    }
  }

  async function handleDeleteView(view: EntityView) {
    if (!window.confirm(`¿Borrar la vista "${view.name}"?`)) return;
    try {
      await deleteEntityView("company", view.id);
      if (activeView?.id === view.id) {
        setActiveView(null);
        setRules(EMPTY_RULES);
        setBuilderKey((k) => k + 1);
      }
      await reloadViews();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la vista."));
    }
  }

  function handleVisibleColumnsChange(next: string[]) {
    setVisibleColumns(next);
    if (activeView) {
      updateEntityView("company", activeView.id, {
        columns: { visible: next, order: next, widths: {} },
      }).catch((err) =>
        setError(extractErrorMessage(err, "No se pudo guardar la vista.")),
      );
    } else {
      saveColumnConfig("company", { visible: next });
    }
  }

  function refireFetch() {
    setRules((r) => ({ ...r }));
  }

  async function runBulk(
    action: CompanyBulkAction,
    payload: Record<string, unknown> = {},
  ) {
    if (selected.size === 0) return;
    try {
      const result = await bulkCompanyAction(
        Array.from(selected),
        action,
        payload,
      );
      const verb =
        action === "activate"
          ? "activadas"
          : action === "deactivate"
            ? "desactivadas"
            : "actualizadas";
      setMessage(
        `${result.affected_count} empresa${result.affected_count === 1 ? "" : "s"} ${verb}.`,
      );
      setSelected(new Set());
      refireFetch();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo aplicar la acción."));
    }
  }

  async function handleSelectAllFiltered() {
    try {
      const liteSpecs = schema!.fields.map((f) => ({
        key: f.key,
        type: f.type,
      }));
      const branches: Record<string, unknown>[] = [];
      if (Object.keys(rules).length) branches.push(rules);
      const trimmed = q.trim();
      if (trimmed) {
        branches.push({
          operator: "OR",
          children: [
            { type: "rule", field: "name", comparator: "contains", value: trimmed },
            { type: "rule", field: "domain", comparator: "contains", value: trimmed },
            { type: "rule", field: "tax_id", comparator: "contains", value: trimmed },
          ],
        });
      }
      const effective =
        branches.length === 0
          ? null
          : branches.length === 1
            ? branches[0]
            : { operator: "AND", children: branches };
      const pruned = effective
        ? pruneRulesTree(effective as Record<string, unknown>, liteSpecs)
        : {};
      const result = await searchEntityIds("company", {
        rules_json: Object.keys(pruned).length ? pruned : null,
      });
      setSelected(new Set(result.ids));
      if (result.truncated) {
        setError(
          `Solo se pudieron seleccionar las primeras ${result.max_ids}. Filtra más para abarcar todas.`,
        );
      }
    } catch (err) {
      setError(
        extractErrorMessage(
          err,
          "No se pudo expandir la selección al filtro completo.",
        ),
      );
    }
  }

  const isDirty = useMemo(() => {
    if (!activeView) return Object.keys(rules).length > 0 || q.length > 0;
    const filters = activeView.filters as Record<string, unknown>;
    const savedTree =
      (filters?.rules_json as Record<string, unknown> | undefined) ?? {};
    const savedQ = (filters?.q as string) ?? "";
    return (
      JSON.stringify(savedTree) !== JSON.stringify(rules) || savedQ !== q
    );
  }, [activeView, rules, q]);

  if (!schema) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Empresas" eyebrow="Empresas" />
        {error ? (
          <ErrorState title="Error" message={error} />
        ) : (
          <p>Cargando…</p>
        )}
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Empresas"
        eyebrow="Empresas"
        description="Filtros AND/OR/NOT, vistas guardadas, bulk actions y columnas configurables."
        actions={
          <Link href="/companies/new" className="button small">
            + Crear empresa
          </Link>
        }
      />

      <EntityViewsTabs
        views={views}
        activeId={activeView?.id ?? null}
        isDirty={isDirty}
        onSelect={(view) => {
          if (view) {
            applyView(view);
          } else {
            setActiveView(null);
            setRules(EMPTY_RULES);
            setQ("");
            setSearchInput("");
            setSort({ field: "name", direction: "asc" });
            setOffset(0);
            setBuilderKey((k) => k + 1);
          }
        }}
        onCreate={() => setEditorMode({ kind: "create" })}
        onEdit={(view) => setEditorMode({ kind: "edit", view })}
        onDuplicate={handleDuplicateView}
        onSetDefault={handleSetDefault}
        onDelete={handleDeleteView}
      />

      <section className="panel contacts-panel">
        <div className="contact-toolbar">
          <input
            type="search"
            className="contact-search"
            placeholder="Buscar por nombre, dominio o CIF…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <div className="contact-toolbar-spacer" />
          <button
            type="button"
            className="button small"
            onClick={handleSaveExistingView}
            disabled={activeView ? !isDirty : false}
            title={
              activeView ? "Guardar vista actual" : "Guardar como nueva"
            }
          >
            Guardar
          </button>
        </div>

        <EntityFilterBuilder
          key={`builder:${builderKey}`}
          fields={schema.fields}
          value={rules}
          onChange={(next) => {
            setRules(next);
            setOffset(0);
          }}
        />

        {message ? <p className="notice notice-success">{message}</p> : null}
        {error ? <p className="form-error">{error}</p> : null}

        <CompaniesBulkBar
          selectedCount={selected.size}
          onActivate={() => runBulk("activate")}
          onDeactivate={() => runBulk("deactivate")}
          onChangeSector={(sector) => runBulk("change_sector", { sector })}
          onClear={() => setSelected(new Set())}
        />

        {(() => {
          const visibleIds = rows.map((r) => String(r.id));
          const allVisibleSelected =
            visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
          const moreInFilter = total > visibleIds.length;
          const allFilterSelected = selected.size >= total;
          if (!allVisibleSelected || !moreInFilter) return null;
          if (allFilterSelected) {
            return (
              <div className="select-all-banner">
                <span>✓ {selected.size} empresas seleccionadas.</span>
                <button
                  type="button"
                  className="button small secondary"
                  onClick={() => setSelected(new Set())}
                >
                  Deseleccionar todas
                </button>
              </div>
            );
          }
          return (
            <div className="select-all-banner">
              <span>
                ✓ {selected.size} empresas seleccionadas en esta página.
              </span>
              <button
                type="button"
                className="button small"
                onClick={handleSelectAllFiltered}
              >
                Seleccionar las {total} empresas que cumplen el filtro
              </button>
            </div>
          );
        })()}

        <EntityTable
          fields={schema.fields}
          visibleColumns={visibleColumns}
          onVisibleColumnsChange={handleVisibleColumnsChange}
          rows={rows}
          total={total}
          limit={PAGE_SIZE}
          offset={offset}
          onPageChange={setOffset}
          sort={sort}
          onSortChange={(next) => {
            setSort(next);
            setOffset(0);
          }}
          selection={selected}
          onSelectionChange={setSelected}
          loading={loading}
          onRowClick={(row) => router.push(`/companies/${row.id}`)}
        />
      </section>

      {editorMode ? (
        <ViewEditorModal
          mode={editorMode}
          onClose={() => setEditorMode(null)}
          onSave={handleSaveView}
        />
      ) : null}
    </main>
  );
}

// --- URL state -----------------------------------------------------

// PR-E4 (B): mismo patrón que /contacts — espejamos el URL state en
// localStorage para que el browser back resucitando la página desde
// el Router Cache de Next siga restaurando el filtro.
const VIEW_STATE_KEY = "crmbomedia_view_state:companies:full";

function readStoredView(): string | null {
  try {
    return window.localStorage.getItem(VIEW_STATE_KEY);
  } catch {
    return null;
  }
}

function writeStoredView(qs: string): void {
  try {
    window.localStorage.setItem(VIEW_STATE_KEY, qs);
  } catch {
    // best-effort
  }
}

type UrlState = {
  viewId: string | null;
  rules: Record<string, unknown> | null;
  q: string | null;
  sortBy: string | null;
  sortDir: "asc" | "desc" | null;
  offset: number | null;
  columns: string[] | null;
};

function readUrlState(params: URLSearchParams): UrlState {
  const viewId = params.get("view_id");
  const rulesRaw = params.get("rules");
  let rules: Record<string, unknown> | null = null;
  if (rulesRaw) {
    try {
      const decoded = decodeURIComponent(atob(rulesRaw));
      rules = JSON.parse(decoded);
    } catch {
      rules = null;
    }
  }
  const sort = params.get("sort");
  let sortBy: string | null = null;
  let sortDir: "asc" | "desc" | null = null;
  if (sort) {
    const [by, dir] = sort.split(":");
    sortBy = by || null;
    sortDir = dir === "desc" ? "desc" : "asc";
  }
  const offsetRaw = params.get("offset");
  const offset =
    offsetRaw && /^\d+$/.test(offsetRaw) ? Number(offsetRaw) : null;
  const colsRaw = params.get("cols");
  const columns = colsRaw ? colsRaw.split(",").filter(Boolean) : null;
  return {
    viewId,
    rules,
    q: params.get("q"),
    sortBy,
    sortDir,
    offset,
    columns,
  };
}

function serializeUrlState(state: {
  viewId: string | null;
  rules: Record<string, unknown> | null;
  q: string;
  sortBy: string;
  sortDir: "asc" | "desc";
  columns: string[];
  offset: number;
}): string {
  const params = new URLSearchParams();
  if (state.viewId) params.set("view_id", state.viewId);
  if (state.rules && Object.keys(state.rules).length > 0) {
    const raw = btoa(encodeURIComponent(JSON.stringify(state.rules)));
    params.set("rules", raw);
  }
  if (state.q) params.set("q", state.q);
  if (state.sortBy !== "name" || state.sortDir !== "asc") {
    params.set("sort", `${state.sortBy}:${state.sortDir}`);
  }
  if (state.columns.length > 0) {
    params.set("cols", state.columns.join(","));
  }
  if (state.offset > 0) {
    params.set("offset", String(state.offset));
  }
  return params.toString();
}

// --- Bulk bar ------------------------------------------------------

function CompaniesBulkBar({
  selectedCount,
  onActivate,
  onDeactivate,
  onChangeSector,
  onClear,
}: {
  selectedCount: number;
  onActivate: () => void;
  onDeactivate: () => void;
  onChangeSector: (sector: string) => void;
  onClear: () => void;
}) {
  const [sectorMode, setSectorMode] = useState(false);
  const [sector, setSector] = useState("");
  if (selectedCount === 0) return null;
  return (
    <div className="bulk-bar">
      <strong>
        {selectedCount} empresa{selectedCount === 1 ? "" : "s"} seleccionada
        {selectedCount === 1 ? "" : "s"}
      </strong>
      <div className="bulk-bar-actions">
        <button type="button" className="button small" onClick={onActivate}>
          Activar
        </button>
        <button
          type="button"
          className="button small secondary danger"
          onClick={onDeactivate}
        >
          Desactivar
        </button>
        {sectorMode ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!sector.trim()) return;
              onChangeSector(sector.trim());
              setSectorMode(false);
              setSector("");
            }}
            style={{ display: "inline-flex", gap: 4 }}
          >
            <input
              type="text"
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              placeholder="Nuevo sector"
              autoFocus
            />
            <button
              type="submit"
              className="button small"
              disabled={!sector.trim()}
            >
              Aplicar
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={() => {
                setSectorMode(false);
                setSector("");
              }}
            >
              Cancelar
            </button>
          </form>
        ) : (
          <button
            type="button"
            className="button secondary small"
            onClick={() => setSectorMode(true)}
          >
            Cambiar sector
          </button>
        )}
      </div>
      <button
        type="button"
        className="bulk-bar-close"
        onClick={onClear}
        aria-label="Limpiar selección"
      >
        ×
      </button>
    </div>
  );
}

// --- View editor modal --------------------------------------------

function ViewEditorModal({
  mode,
  onSave,
  onClose,
}: {
  mode: { kind: "create" } | { kind: "edit"; view: EntityView };
  onSave: (draft: {
    name: string;
    description: string;
    isShared: boolean;
  }) => Promise<void>;
  onClose: () => void;
}) {
  const initial =
    mode.kind === "edit"
      ? mode.view
      : { name: "", description: null, is_shared: false };
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description ?? "");
  const [isShared, setIsShared] = useState(initial.is_shared ?? false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setErr("El nombre es obligatorio.");
      return;
    }
    setBusy(true);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        isShared,
      });
    } catch (error) {
      setErr(extractErrorMessage(error, "No se pudo guardar."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog">
      <div
        className="modal-dialog modal-dialog-form"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2>{mode.kind === "edit" ? "Editar vista" : "Nueva vista"}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ×
          </button>
        </header>
        <form onSubmit={handleSubmit} className="modal-form">
          <label>
            Nombre
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              required
            />
          </label>
          <label>
            Descripción (opcional)
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </label>
          <label className="form-check">
            <input
              type="checkbox"
              checked={isShared}
              onChange={(e) => setIsShared(e.target.checked)}
            />{" "}
            Compartir vista con todo el equipo (sólo lectura)
          </label>
          {err ? <p className="form-error">{err}</p> : null}
          <div className="modal-actions">
            <button
              type="button"
              className="button secondary small"
              onClick={onClose}
              disabled={busy}
            >
              Cancelar
            </button>
            <button type="submit" className="button small" disabled={busy}>
              {busy ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
