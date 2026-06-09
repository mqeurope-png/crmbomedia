"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ColumnConfigurator } from "../components/ColumnConfigurator";
import { ContactFilters } from "../components/ContactFilters";
import {
  ContactViewEditorModal,
  type ContactViewDraft,
} from "../components/ContactViewEditorModal";
import { ContactViewsSidebar } from "../components/ContactViewsSidebar";
import { ErrorState } from "../components/ErrorState";
import { TagChips } from "../components/TagChips";
import {
  createSavedView,
  deleteSavedView,
  duplicateSavedView,
  listContacts,
  listSavedViews,
  setDefaultSavedView,
  updateSavedView,
  type Contact,
  type ContactListFilters,
  type ContactListPage,
  type SavedView,
  type SavedViewColumns,
  type SavedViewFilters,
  type SavedViewSort,
} from "../lib/api";
import {
  ALL_COLUMN_KEYS,
  DEFAULT_VISIBLE_COLUMNS,
  findColumn,
  type ContactColumnKey,
} from "../lib/contactColumns";
import {
  clearLocalConfig,
  loadLocalConfig,
  saveLocalConfig,
} from "../lib/contactViewStorage";
import { extractErrorMessage } from "../lib/errors";

const PAGE_SIZE = 25;

const DEFAULT_FILTERS: ContactListFilters = {
  sort_by: "created_at",
  sort_dir: "desc",
  limit: PAGE_SIZE,
  skip: 0,
};

function fullName(contact: Contact): string {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ").trim();
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function renderCell(key: ContactColumnKey, contact: Contact): React.ReactNode {
  switch (key) {
    case "name":
      return (
        <Link href={`/contacts/${contact.id}`}>
          {fullName(contact) || "(Sin nombre)"}
        </Link>
      );
    case "email":
      return contact.email;
    case "phone":
      return contact.phone ?? "—";
    case "tags":
      return contact.tag_objects?.length ? (
        <TagChips tags={contact.tag_objects} size="dense" />
      ) : (
        "—"
      );
    case "origin":
      return contact.origin ?? "—";
    case "commercial_status":
      return contact.commercial_status;
    case "marketing_consent":
      return (
        <span className={`status status-${contact.marketing_consent}`}>
          {contact.marketing_consent}
        </span>
      );
    case "lead_score":
      return contact.lead_score ?? "—";
    case "is_active":
      return contact.is_active ? "Sí" : "No";
    case "created_at":
      return formatDate(contact.created_at);
    case "updated_at":
      return formatDate(contact.updated_at);
    case "external_data_freshness":
      return contact.external_data_freshness ? (
        <span
          className={`freshness-badge freshness-${contact.external_data_freshness}`}
        >
          {contact.external_data_freshness}
        </span>
      ) : (
        "—"
      );
    case "last_external_refresh_at":
      return formatDate(contact.last_external_refresh_at);
  }
}

function normaliseOrder(order: string[] | undefined): ContactColumnKey[] {
  const valid = (order ?? []).filter((key): key is ContactColumnKey =>
    ALL_COLUMN_KEYS.includes(key as ContactColumnKey),
  );
  const missing = ALL_COLUMN_KEYS.filter((key) => !valid.includes(key));
  return [...valid, ...missing];
}

function normaliseVisible(
  visible: string[] | undefined,
): ContactColumnKey[] {
  const fromInput = (visible ?? DEFAULT_VISIBLE_COLUMNS).filter(
    (key): key is ContactColumnKey =>
      ALL_COLUMN_KEYS.includes(key as ContactColumnKey),
  );
  // "name" is always visible — even if the operator's saved view tried
  // to hide it (legacy data) we force it back in.
  if (!fromInput.includes("name")) return ["name", ...fromInput];
  return fromInput;
}

export default function ContactsListPage() {
  const [views, setViews] = useState<SavedView[]>([]);
  const [activeView, setActiveView] = useState<SavedView | null>(null);
  const [filters, setFilters] = useState<ContactListFilters>(DEFAULT_FILTERS);
  const [columnOrder, setColumnOrder] = useState<ContactColumnKey[]>(() => {
    const local = loadLocalConfig();
    return normaliseOrder(local?.columns.order);
  });
  const [visibleColumns, setVisibleColumns] = useState<ContactColumnKey[]>(() => {
    const local = loadLocalConfig();
    return normaliseVisible(local?.columns.visible);
  });
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState<ContactListPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [editorMode, setEditorMode] = useState<
    | { kind: "create" }
    | { kind: "edit"; view: SavedView }
    | null
  >(null);
  // First-load coordinates the localStorage → default view hand-off so
  // we don't double-apply config on hot reloads.
  const firstLoadRef = useRef(true);

  const loadViews = useCallback(async () => {
    try {
      const list = await listSavedViews();
      setViews(list);
      return list;
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las vistas."));
      return [];
    }
  }, []);

  useEffect(() => {
    loadViews().then((list) => {
      if (!firstLoadRef.current) return;
      firstLoadRef.current = false;
      // Apply default view if present; otherwise stick with localStorage.
      const def = list.find((v) => v.is_default);
      if (def) {
        applyView(def);
      } else {
        const local = loadLocalConfig();
        if (local) {
          setFilters((current) => ({
            ...current,
            ...(local.filters as Partial<ContactListFilters>),
            sort_by: (local.sort.sort_by as ContactListFilters["sort_by"]) ?? current.sort_by,
            sort_dir: local.sort.sort_dir,
            skip: 0,
          }));
        }
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyView = useCallback((view: SavedView) => {
    setActiveView(view);
    setFilters({
      ...DEFAULT_FILTERS,
      view_id: view.id,
      q: view.filters.q ?? undefined,
      tag_ids: view.filters.tag_ids ?? undefined,
      tag_match_mode: view.filters.tag_match_mode ?? undefined,
      origin_system: view.filters.origin_system ?? undefined,
      origin_account_id: view.filters.origin_account_id ?? undefined,
      commercial_status: view.filters.commercial_status ?? undefined,
      marketing_consent: view.filters.marketing_consent ?? undefined,
      lead_score_min: view.filters.lead_score_min ?? undefined,
      lead_score_max: view.filters.lead_score_max ?? undefined,
      include_inactive: view.filters.is_active === false ? true : undefined,
      sort_by: (view.sort?.sort_by as ContactListFilters["sort_by"]) ?? "created_at",
      sort_dir: view.sort?.sort_dir ?? "desc",
    });
    setColumnOrder(normaliseOrder(view.columns?.order));
    setVisibleColumns(normaliseVisible(view.columns?.visible));
    setSearchInput(view.filters.q ?? "");
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((current) => ({
        ...current,
        q: searchInput.trim() || undefined,
        skip: 0,
      }));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    listContacts(filters)
      .then((result) => {
        if (!cancelled) {
          setPage(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            extractErrorMessage(err, "No se pudieron cargar los contactos."),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const totalPages = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.max(1, Math.ceil(page.total / page.limit));
  }, [page]);
  const currentPage = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.floor(page.offset / page.limit) + 1;
  }, [page]);

  function buildPayloadFromState(): {
    filters: SavedViewFilters;
    columns: SavedViewColumns;
    sort: SavedViewSort;
  } {
    return {
      filters: {
        q: filters.q ?? null,
        tag_ids: filters.tag_ids ?? null,
        tag_match_mode: filters.tag_match_mode ?? null,
        origin_system: filters.origin_system ?? null,
        origin_account_id: filters.origin_account_id ?? null,
        commercial_status: filters.commercial_status ?? null,
        marketing_consent: filters.marketing_consent ?? null,
        lead_score_min: filters.lead_score_min ?? null,
        lead_score_max: filters.lead_score_max ?? null,
        is_active: filters.include_inactive ? false : null,
      },
      columns: {
        order: columnOrder,
        visible: visibleColumns,
        widths: {},
      },
      sort: {
        sort_by: filters.sort_by ?? "created_at",
        sort_dir: filters.sort_dir ?? "desc",
      },
    };
  }

  const handleReset = useCallback(() => {
    setActiveView(null);
    setSearchInput("");
    setFilters(DEFAULT_FILTERS);
    clearLocalConfig();
  }, []);

  const handleSortChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const [sort_by, sort_dir] = event.target.value.split(":") as [
        NonNullable<ContactListFilters["sort_by"]>,
        NonNullable<ContactListFilters["sort_dir"]>,
      ];
      setFilters((current) => ({ ...current, sort_by, sort_dir, skip: 0 }));
    },
    [],
  );

  const goToPage = useCallback(
    (nextPage: number) => {
      if (!page) return;
      const clamped = Math.max(1, Math.min(totalPages, nextPage));
      setFilters((current) => ({
        ...current,
        skip: (clamped - 1) * (current.limit ?? PAGE_SIZE),
      }));
    },
    [page, totalPages],
  );

  // Persist localStorage whenever filters/columns change AND no view
  // is active. Saved views own their persistence via the API.
  useEffect(() => {
    if (activeView) return;
    if (firstLoadRef.current) return;
    const payload = buildPayloadFromState();
    saveLocalConfig({
      filters: payload.filters,
      columns: payload.columns,
      sort: payload.sort,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, columnOrder, visibleColumns, activeView]);

  async function handleSaveView(draft: ContactViewDraft) {
    const payload = buildPayloadFromState();
    if (editorMode?.kind === "edit") {
      const updated = await updateSavedView(editorMode.view.id, {
        name: draft.name,
        description: draft.description || null,
        is_shared: draft.isShared,
        ...payload,
      });
      setActiveView(updated);
    } else {
      const created = await createSavedView({
        name: draft.name,
        description: draft.description || null,
        is_shared: draft.isShared,
        ...payload,
      });
      setActiveView(created);
      // First saved view kills the localStorage fallback.
      clearLocalConfig();
    }
    await loadViews();
    setEditorMode(null);
  }

  async function handleDuplicateView(view: SavedView) {
    try {
      const copy = await duplicateSavedView(view.id);
      await loadViews();
      applyView(copy);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar la vista."));
    }
  }

  async function handleSetDefault(view: SavedView) {
    try {
      if (view.is_default) {
        await updateSavedView(view.id, { is_default: false });
      } else {
        await setDefaultSavedView(view.id);
      }
      const list = await loadViews();
      const refreshed = list.find((v) => v.id === view.id);
      if (refreshed && activeView?.id === view.id) setActiveView(refreshed);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo actualizar la vista."));
    }
  }

  async function handleDeleteView(view: SavedView) {
    if (!window.confirm(`¿Borrar la vista "${view.name}"?`)) return;
    try {
      await deleteSavedView(view.id);
      if (activeView?.id === view.id) {
        setActiveView(null);
        setFilters(DEFAULT_FILTERS);
      }
      await loadViews();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la vista."));
    }
  }

  return (
    <main className="shell shell-wide">
      <Link href="/" className="back-link">
        ← Volver al dashboard
      </Link>
      <section className="hero compact">
        <p className="eyebrow">Contactos</p>
        <h1>Lista de contactos</h1>
        <p className="lead">
          Busca, filtra y abre cualquier contacto. Guarda configuraciones
          como vistas para volver a ellas en un click.
        </p>
        <div className="actions">
          <Link href="/contacts/new" className="button">
            Crear contacto
          </Link>
        </div>
      </section>

      <section className="contacts-layout">
        <ContactViewsSidebar
          views={views}
          activeId={activeView?.id ?? null}
          onSelect={applyView}
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
              className="search-input"
              placeholder="Buscar por nombre, email o teléfono…"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              aria-label="Búsqueda de contactos"
            />
            <label className="sort-select">
              <span>Ordenar por</span>
              <select
                value={`${filters.sort_by ?? "created_at"}:${filters.sort_dir ?? "desc"}`}
                onChange={handleSortChange}
              >
                <option value="created_at:desc">Más recientes primero</option>
                <option value="created_at:asc">Más antiguos primero</option>
                <option value="updated_at:desc">Última actualización</option>
                <option value="name:asc">Nombre (A→Z)</option>
                <option value="email:asc">Email (A→Z)</option>
                <option value="lead_score:desc">Lead score (mayor primero)</option>
                <option value="lead_score:asc">Lead score (menor primero)</option>
              </select>
            </label>
            <ColumnConfigurator
              order={columnOrder}
              visible={visibleColumns}
              onApply={({ order, visible }) => {
                setColumnOrder(order);
                setVisibleColumns(visible);
                if (activeView?.is_owner) {
                  updateSavedView(activeView.id, {
                    columns: { order, visible, widths: {} },
                  })
                    .then(async () => {
                      const list = await loadViews();
                      const refreshed = list.find(
                        (v) => v.id === activeView.id,
                      );
                      if (refreshed) setActiveView(refreshed);
                    })
                    .catch((err) =>
                      setError(
                        extractErrorMessage(err, "No se pudo guardar el orden de columnas."),
                      ),
                    );
                }
              }}
            />
            <button
              type="button"
              className="button"
              onClick={() => setEditorMode({ kind: "create" })}
            >
              Guardar vista
            </button>
            <button
              type="button"
              className="button secondary small"
              onClick={handleReset}
            >
              Limpiar filtros
            </button>
          </div>

          <ContactFilters
            filters={filters}
            onChange={setFilters}
            onReset={handleReset}
          />

          {error ? <ErrorState title="Error" message={error} /> : null}

          {isLoading && !page ? (
            <p className="muted">Cargando contactos…</p>
          ) : page && page.items.length === 0 ? (
            <p className="muted">
              Ningún contacto coincide con los filtros aplicados.
            </p>
          ) : page ? (
            <>
              <div className="table-wrapper">
                <table className="data-table contacts-table">
                  <thead>
                    <tr>
                      {visibleColumns.map((key) => {
                        const def = findColumn(key);
                        return def ? (
                          <th key={key} scope="col">
                            {def.label}
                          </th>
                        ) : null;
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((contact) => (
                      <tr key={contact.id}>
                        {visibleColumns.map((key) => (
                          <td key={key}>{renderCell(key, contact)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <span className="muted">
                  {page.total} contacto{page.total === 1 ? "" : "s"} ·
                  {" "}Página {currentPage} / {totalPages}
                </span>
                <div className="pagination-buttons">
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => goToPage(currentPage - 1)}
                    disabled={currentPage <= 1}
                  >
                    Anterior
                  </button>
                  <button
                    type="button"
                    className="button secondary small"
                    onClick={() => goToPage(currentPage + 1)}
                    disabled={currentPage >= totalPages}
                  >
                    Siguiente
                  </button>
                </div>
              </div>
            </>
          ) : null}
        </section>
      </section>

      <ContactViewEditorModal
        open={editorMode !== null}
        title={editorMode?.kind === "edit" ? "Editar vista" : "Guardar como vista"}
        submitLabel={editorMode?.kind === "edit" ? "Guardar cambios" : "Crear vista"}
        initial={
          editorMode?.kind === "edit"
            ? {
                name: editorMode.view.name,
                description: editorMode.view.description ?? "",
                isShared: editorMode.view.is_shared,
              }
            : activeView
              ? {
                  name: "",
                  description: "",
                  isShared: activeView.is_shared,
                }
              : undefined
        }
        onSubmit={handleSaveView}
        onClose={() => setEditorMode(null)}
      />
    </main>
  );
}

