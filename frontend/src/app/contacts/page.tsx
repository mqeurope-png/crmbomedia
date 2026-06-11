"use client";

import { History, ListPlus, RotateCcw, Save } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ColumnConfigurator } from "../components/ColumnConfigurator";
import { ContactFiltersBuilder } from "../components/ContactFiltersBuilder";
import { ContactsBulkBar } from "../components/ContactsBulkBar";
import {
  ContactViewEditorModal,
  type ContactViewDraft,
} from "../components/ContactViewEditorModal";
import { ContactViewsTabs } from "../components/ContactViewsTabs";
import { ErrorState } from "../components/ErrorState";
import { OriginChipsSummary } from "../components/OriginChips";
import { PageHeader } from "../components/PageHeader";
import { PushViewToBrevoModal } from "../components/PushViewToBrevoModal";
import { TagChips } from "../components/TagChips";
import {
  createSavedView,
  deleteSavedView,
  duplicateSavedView,
  getCurrentUser,
  listSavedViews,
  pushViewToBrevoList,
  saveViewAsSegment,
  searchContacts,
  setDefaultSavedView,
  updateSavedView,
  type Contact,
  type ContactListPage,
  type SavedView,
  type User,
} from "../lib/api";
import {
  ALL_COLUMN_KEYS,
  COLUMN_SORT_KEY,
  DEFAULT_VISIBLE_COLUMNS,
  findColumn,
  type ContactColumnKey,
} from "../lib/contactColumns";
import { legacyFiltersToRulesTree } from "../lib/contactRulesMigration";
import { pruneRulesTree } from "../lib/segmentTranslator";
import {
  readUrlState,
  serializeUrlState,
} from "../lib/contactsUrlState";
import {
  loadLocalColumns,
  saveLocalColumns,
} from "../lib/contactColumnsStorage";
import { extractErrorMessage } from "../lib/errors";

const PAGE_SIZE = 25;
const EMPTY_RULES: Record<string, unknown> = {};

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
      return contact.external_references_summary?.length ? (
        <OriginChipsSummary summary={contact.external_references_summary} />
      ) : (
        contact.origin ?? "—"
      );
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
    case "created_at_external":
      return formatDate(contact.created_at_external);
    case "updated_at_external":
      return formatDate(contact.updated_at_external);
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
  if (!order) return [...ALL_COLUMN_KEYS];
  const valid = order.filter((k): k is ContactColumnKey =>
    ALL_COLUMN_KEYS.includes(k as ContactColumnKey),
  );
  const remaining = ALL_COLUMN_KEYS.filter((k) => !valid.includes(k));
  return [...valid, ...remaining];
}

function normaliseVisible(visible: string[] | undefined): ContactColumnKey[] {
  if (!visible) return [...DEFAULT_VISIBLE_COLUMNS];
  const valid = visible.filter((k): k is ContactColumnKey =>
    ALL_COLUMN_KEYS.includes(k as ContactColumnKey),
  );
  return valid.length ? valid : [...DEFAULT_VISIBLE_COLUMNS];
}

/** The view's rules tree as we keep it in memory — pulled from the
 * new `filters.rules_json` field when present, falling back to a
 * translation of the legacy flat dropdown filters so old views still
 * load into the query builder. */
function viewToRulesTree(view: SavedView): Record<string, unknown> {
  return legacyFiltersToRulesTree(view.filters);
}

function rulesEqual(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  // Normalise both sides through the same prune the builder applies on
  // output. Views saved before the prune landed store the raw tree
  // (e.g. a single-rule AND wrapper); without this the dirty badge
  // would light up the moment the builder re-emits the semantically
  // identical pruned shape.
  return (
    JSON.stringify(pruneRulesTree(a ?? {}, [])) ===
    JSON.stringify(pruneRulesTree(b ?? {}, []))
  );
}

export default function ContactsListPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [views, setViews] = useState<SavedView[]>([]);
  const [activeView, setActiveView] = useState<SavedView | null>(null);
  const [rules, setRules] = useState<Record<string, unknown>>(EMPTY_RULES);
  const [q, setQ] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const [columnOrder, setColumnOrder] = useState<ContactColumnKey[]>(() =>
    normaliseOrder(loadLocalColumns()?.order),
  );
  const [visibleColumns, setVisibleColumns] = useState<ContactColumnKey[]>(() =>
    normaliseVisible(loadLocalColumns()?.visible),
  );

  const [page, setPage] = useState<ContactListPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  // Bulk-action selection. `selected` is a Set of contact ids for
  // O(1) membership checks; cleared whenever the underlying page
  // changes so stale rows can't be acted on.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [editorMode, setEditorMode] = useState<
    { kind: "create" } | { kind: "edit"; view: SavedView } | null
  >(null);
  const [showBrevoModal, setShowBrevoModal] = useState(false);
  const [actionsMenu, setActionsMenu] = useState(false);
  // When the user picks "Enviar a lista Brevo" with no active view we
  // auto-create a transient one for them (the push endpoint needs a
  // view id). This flag tells the post-save hook to chain straight
  // into the Brevo modal instead of dropping back to the list.
  const [pushAfterSave, setPushAfterSave] = useState(false);

  const firstLoadRef = useRef(true);

  // ---------------------------------------------------------------------------
  // Views + initial state (URL → view_id → default view → blank)
  // ---------------------------------------------------------------------------

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

  const applyView = useCallback((view: SavedView) => {
    setActiveView(view);
    setRules(viewToRulesTree(view));
    setColumnOrder(normaliseOrder(view.columns?.order));
    setVisibleColumns(normaliseVisible(view.columns?.visible));
    setQ(view.filters.q ?? "");
    setSearchInput(view.filters.q ?? "");
    setSortBy((view.sort?.sort_by as string) ?? "created_at");
    setSortDir((view.sort?.sort_dir as "asc" | "desc") ?? "desc");
    setOffset(0);
  }, []);

  useEffect(() => {
    getCurrentUser()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  useEffect(() => {
    loadViews().then((list) => {
      if (!firstLoadRef.current) return;
      firstLoadRef.current = false;
      const url = readUrlState(new URLSearchParams(searchParams.toString()));
      if (url.viewId) {
        const view = list.find((v) => v.id === url.viewId);
        if (view) {
          applyView(view);
          if (url.q) {
            setQ(url.q);
            setSearchInput(url.q);
          }
          if (url.sortBy) setSortBy(url.sortBy);
          if (url.sortDir) setSortDir(url.sortDir);
          if (url.columns) setVisibleColumns(normaliseVisible(url.columns));
          return;
        }
      }
      if (url.rules) {
        setRules(url.rules);
        setQ(url.q);
        setSearchInput(url.q);
        setSortBy(url.sortBy);
        setSortDir(url.sortDir);
        if (url.columns) setVisibleColumns(normaliseVisible(url.columns));
        return;
      }
      const def = list.find((v) => v.is_default);
      if (def) applyView(def);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // URL sync — push state to history so back-navigation from a contact
  // detail page lands on the same filter, sort and columns.
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const params = serializeUrlState({
      viewId: activeView?.id ?? null,
      rules: activeView ? null : rules,
      q,
      sortBy,
      sortDir,
      columns: visibleColumns,
    });
    const next = params ? `/contacts?${params}` : "/contacts";
    router.replace(next, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView?.id, rules, q, sortBy, sortDir, visibleColumns]);

  // ---------------------------------------------------------------------------
  // Search box debounce
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setQ(searchInput.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  // ---------------------------------------------------------------------------
  // Fetch contacts (POST /api/contacts/search)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    searchContacts({
      rules_json: Object.keys(rules).length > 0 ? rules : null,
      q: q || null,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: PAGE_SIZE,
      offset,
    })
      .then((result) => {
        if (!cancelled) {
          setPage(result);
          // Drop selections for rows that no longer match the page.
          setSelected((prev) => {
            const visible = new Set(result.items.map((r) => r.id));
            const next = new Set<string>();
            prev.forEach((id) => {
              if (visible.has(id)) next.add(id);
            });
            return next;
          });
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
  }, [rules, q, sortBy, sortDir, offset]);

  const totalPages = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.max(1, Math.ceil(page.total / page.limit));
  }, [page]);
  const currentPage = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.floor(page.offset / page.limit) + 1;
  }, [page]);

  const isDirty = useMemo(() => {
    if (!activeView) return Object.keys(rules).length > 0;
    return !rulesEqual(rules, viewToRulesTree(activeView));
  }, [activeView, rules]);

  function goToPage(nextPage: number) {
    if (!page) return;
    const clamped = Math.max(1, Math.min(totalPages, nextPage));
    setOffset((clamped - 1) * PAGE_SIZE);
  }

  // ---------------------------------------------------------------------------
  // View actions
  // ---------------------------------------------------------------------------

  function buildPayloadFromState() {
    return {
      filters: {
        q: q || null,
        rules_json: Object.keys(rules).length > 0 ? rules : null,
      },
      columns: {
        order: columnOrder,
        visible: visibleColumns,
        widths: {},
      },
      sort: { sort_by: sortBy, sort_dir: sortDir },
    };
  }

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
    }
    await loadViews();
    setEditorMode(null);
    // Chained flow: the operator clicked "Enviar a lista Brevo" before
    // any view existed, we forced them through the save modal, and
    // now we drop them straight onto the Brevo push modal so they
    // don't lose the action click.
    if (pushAfterSave) {
      setPushAfterSave(false);
      setShowBrevoModal(true);
    }
  }

  async function handleSaveExistingView() {
    if (!activeView) {
      setEditorMode({ kind: "create" });
      return;
    }
    try {
      const updated = await updateSavedView(activeView.id, buildPayloadFromState());
      setActiveView(updated);
      await loadViews();
      setMessage("Vista guardada.");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la vista."));
    }
  }

  function handleRevert() {
    if (!activeView) {
      setRules(EMPTY_RULES);
      setSortBy("created_at");
      setSortDir("desc");
      setQ("");
      setSearchInput("");
      return;
    }
    applyView(activeView);
  }

  /** Click cycles asc → desc → default (created_at desc). Clicking a
   *  different column starts at asc on that one. */
  function handleHeaderSort(sortKey: string) {
    if (sortKey !== sortBy) {
      setSortBy(sortKey);
      setSortDir("asc");
    } else if (sortDir === "asc") {
      setSortDir("desc");
    } else {
      setSortBy("created_at");
      setSortDir("desc");
    }
    setOffset(0);
  }

  async function handleSaveAsSegment() {
    if (!activeView) {
      setError(
        "Guarda la consulta como vista antes de promoverla a segmento.",
      );
      return;
    }
    const name = window.prompt(
      "Nombre del nuevo segmento",
      `Segmento desde "${activeView.name}"`,
    );
    if (!name?.trim()) return;
    try {
      const created = await saveViewAsSegment(activeView.id, {
        name: name.trim(),
      });
      setMessage(`Segmento "${created.name}" creado.`);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el segmento."));
    }
  }

  async function handlePushToBrevo(payload: {
    brevo_account_id: string;
    brevo_list_id?: number;
    new_list_name?: string;
  }) {
    if (!activeView) return;
    const result = await pushViewToBrevoList(activeView.id, payload);
    setMessage(
      `${result.contacts_to_push} contacto${
        result.contacts_to_push === 1 ? "" : "s"
      } en cola para sincronizar a Brevo (lista #${result.brevo_list_id}).`,
    );
    setShowBrevoModal(false);
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
        setRules(EMPTY_RULES);
      }
      await loadViews();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la vista."));
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Lista de contactos"
        eyebrow="Contactos"
        description="Filtros AND/OR estilo Brevo. Guarda combinaciones como vistas o promociónalas a segmentos / listas de Brevo."
        actions={
          <Link href="/contacts/new" className="button small">
            + Crear contacto
          </Link>
        }
      />

      <ContactViewsTabs
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
            setOffset(0);
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
            className="search-input"
            placeholder="Buscar por nombre, email o teléfono…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            aria-label="Búsqueda de contactos"
          />
          <label className="sort-select">
            <span>Ordenar por</span>
            <select
              value={`${sortBy}:${sortDir}`}
              onChange={(e) => {
                const [sb, sd] = e.target.value.split(":") as [
                  string,
                  "asc" | "desc",
                ];
                setSortBy(sb);
                setSortDir(sd);
                setOffset(0);
              }}
            >
              <option value="created_at:desc">Más recientes primero</option>
              <option value="created_at:asc">Más antiguos primero</option>
              <option value="updated_at:desc">Última actualización</option>
              <option value="updated_at_external:desc">
                Última actualización en origen
              </option>
              <option value="created_at_external:desc">
                Creación en origen (más recientes)
              </option>
              <option value="name:asc">Nombre (A→Z)</option>
              <option value="email:asc">Email (A→Z)</option>
              <option value="lead_score:desc">Lead score (mayor primero)</option>
            </select>
          </label>
          <ColumnConfigurator
            order={columnOrder}
            visible={visibleColumns}
            onApply={({ order, visible }) => {
              setColumnOrder(order);
              setVisibleColumns(visible);
              // Persist BEFORE the URL effect re-runs: PATCH the
              // active view's columns_json so every member sees the
              // same config, or fall back to localStorage on the
              // "Todos los contactos" tab so a reload keeps the
              // operator's pick.
              if (activeView) {
                updateSavedView(activeView.id, {
                  columns: { order, visible, widths: {} },
                })
                  .then((updated) => {
                    setActiveView(updated);
                  })
                  .catch((err) =>
                    setError(
                      extractErrorMessage(
                        err,
                        "No se pudo guardar la configuración de columnas.",
                      ),
                    ),
                  );
              } else {
                saveLocalColumns({ order, visible });
              }
            }}
          />
          <button
            type="button"
            className="button"
            onClick={handleSaveExistingView}
            disabled={!isDirty}
            title={isDirty ? "Guardar cambios en la vista" : "Sin cambios"}
          >
            <Save size={13} aria-hidden /> Guardar
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={handleRevert}
            disabled={!isDirty}
            title="Descartar cambios"
          >
            <RotateCcw size={13} aria-hidden /> Revertir
          </button>
          <div className="dropdown">
            <button
              type="button"
              className="button secondary"
              onClick={() => setActionsMenu((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={actionsMenu}
            >
              Acciones ▾
            </button>
            {actionsMenu ? (
              <ActionsMenu
                onClose={() => setActionsMenu(false)}
                hasView={!!activeView}
                onSaveAsNewView={() => {
                  setActionsMenu(false);
                  setEditorMode({ kind: "create" });
                }}
                onSaveAsSegment={async () => {
                  setActionsMenu(false);
                  await handleSaveAsSegment();
                }}
                onPushToBrevo={() => {
                  setActionsMenu(false);
                  if (!activeView) {
                    // The push endpoint needs a view id, so divert
                    // the operator through the save modal first and
                    // chain into the Brevo modal once it lands
                    // (`pushAfterSave` flag handled by handleSaveView).
                    setPushAfterSave(true);
                    setEditorMode({ kind: "create" });
                    return;
                  }
                  setShowBrevoModal(true);
                }}
              />
            ) : null}
          </div>
        </div>

        <ContactFiltersBuilder rules={rules} onChange={setRules} />

        {error ? <ErrorState title="Error" message={error} /> : null}
        {message ? (
          <p className="muted" role="status">
            {message}
          </p>
        ) : null}

        {isLoading && !page ? (
          <p className="muted">Cargando contactos…</p>
        ) : page && page.items.length === 0 ? (
          <p className="muted">
            Ningún contacto coincide con los filtros aplicados.
          </p>
        ) : page ? (
          <>
            <ContactsBulkBar
              selectedIds={Array.from(selected)}
              currentUser={currentUser}
              onAfterAction={(action, affected) => {
                setMessage(
                  `${action === "deactivate" ? "Desactivados" : "Actualizados"} ${affected} contacto${affected === 1 ? "" : "s"}.`,
                );
                setSelected(new Set());
                setOffset((cur) => cur);
                // Force a reload via offset change trick: bump rules
                // identity to refire the fetch effect.
                setRules((r) => ({ ...r }));
              }}
              onClear={() => setSelected(new Set())}
            />
            <div className="table-wrapper">
              <table className="data-table contacts-table">
                <thead>
                  <tr>
                    <th scope="col" className="bulk-checkbox-cell">
                      <input
                        type="checkbox"
                        aria-label="Seleccionar todos los visibles"
                        checked={
                          page.items.length > 0 &&
                          page.items.every((c) => selected.has(c.id))
                        }
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelected(new Set(page.items.map((c) => c.id)));
                          } else {
                            setSelected(new Set());
                          }
                        }}
                      />
                    </th>
                    {visibleColumns.map((key) => {
                      const def = findColumn(key);
                      if (!def) return null;
                      const sortKey = COLUMN_SORT_KEY[key];
                      const isActive = sortKey && sortKey === sortBy;
                      const arrow =
                        isActive && sortDir === "asc"
                          ? "▲"
                          : isActive
                            ? "▼"
                            : "";
                      return (
                        <th
                          key={key}
                          scope="col"
                          className={sortKey ? "sortable" : undefined}
                          onClick={
                            sortKey
                              ? () => handleHeaderSort(sortKey)
                              : undefined
                          }
                          aria-sort={
                            !isActive
                              ? undefined
                              : sortDir === "asc"
                                ? "ascending"
                                : "descending"
                          }
                        >
                          {def.label}
                          {sortKey ? (
                            <span className="sort-arrow">{arrow}</span>
                          ) : null}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((contact) => (
                    <tr
                      key={contact.id}
                      className={selected.has(contact.id) ? "is-selected" : undefined}
                    >
                      <td className="bulk-checkbox-cell">
                        <input
                          type="checkbox"
                          checked={selected.has(contact.id)}
                          onChange={(e) => {
                            setSelected((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(contact.id);
                              else next.delete(contact.id);
                              return next;
                            });
                          }}
                          aria-label={`Seleccionar ${contact.first_name}`}
                        />
                      </td>
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
                {page.total} contacto{page.total === 1 ? "" : "s"} · Página{" "}
                {currentPage} / {totalPages}
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

      <ContactViewEditorModal
        open={editorMode !== null}
        title={editorMode?.kind === "edit" ? "Editar vista" : "Guardar como vista"}
        submitLabel={
          editorMode?.kind === "edit" ? "Guardar cambios" : "Crear vista"
        }
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

      {showBrevoModal && activeView ? (
        <PushViewToBrevoModal
          viewName={activeView.name}
          contactsCount={page?.total ?? 0}
          onSubmit={handlePushToBrevo}
          onClose={() => setShowBrevoModal(false)}
        />
      ) : null}
    </main>
  );
}

function ActionsMenu({
  onClose,
  hasView,
  onSaveAsNewView,
  onSaveAsSegment,
  onPushToBrevo,
}: {
  onClose: () => void;
  hasView: boolean;
  onSaveAsNewView: () => void;
  onSaveAsSegment: () => void;
  onPushToBrevo: () => void;
}) {
  return (
    <>
      <div className="dropdown-overlay" onClick={onClose} aria-hidden />
      <ul className="dropdown-menu" role="menu">
        <li>
          <button type="button" onClick={onSaveAsNewView}>
            <Save size={12} aria-hidden /> Guardar como vista nueva
          </button>
        </li>
        <li>
          <button
            type="button"
            onClick={onSaveAsSegment}
            disabled={!hasView}
            title={
              hasView
                ? "Crea un segmento con las mismas reglas"
                : "Guarda primero la consulta como vista"
            }
          >
            <History size={12} aria-hidden /> Guardar como segmento
          </button>
        </li>
        <li>
          <button
            type="button"
            onClick={onPushToBrevo}
            title={
              hasView
                ? "Encola un push a una lista Brevo"
                : "Te guiamos por el guardado de vista antes del push"
            }
          >
            <ListPlus size={12} aria-hidden /> Enviar contactos a lista Brevo
          </button>
        </li>
      </ul>
    </>
  );
}
