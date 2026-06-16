"use client";

/**
 * Sprint Filtros & Listas — PR-E. `/contacts` rehecha sobre los
 * componentes genéricos (PR-A…PR-Cg):
 *
 *  - `<EntityTable>` (TanStack headless) sustituye el `<table>` inline
 *    de 200+ líneas que el legacy tenía.
 *  - `<EntityFilterBuilder>` (react-querybuilder advanced) sustituye al
 *    `<ContactFiltersBuilder>` casero de 2 niveles AND/OR. Soporta
 *    NOT + anidamiento arbitrario y los 4 pickers nuevos
 *    (User/Company/Segment/BrevoList) que PR-Cd…Cg trajeron.
 *  - `<EntityViewsTabs>` + `/api/entity-views/contact` sustituyen al
 *    `<ContactViewsTabs>` + `/api/contact-views`. El backend lee la
 *    misma tabla (`contact_views` con `entity_type='contact'`), así que
 *    las 4 vistas guardadas existentes ("lead score alto", "test",
 *    "xav", "NL") se cargan idénticas.
 *  - `<EntityColumnConfigurator>` (heredado por EntityTable) sustituye
 *    al `<ColumnConfigurator>` legacy.
 *
 * Funcionalidad preservada:
 *  - búsqueda libre `q` (traducida a `OR(name/email/phone contains q)`
 *    contra el motor genérico, ver `lib/contactsRules.ts`).
 *  - toggle "Solo asignados a mí" (traducido a
 *    `assigned_users contains_any [current_user.id]`, que cubre tanto
 *    al primary como a watchers — PR-B Reglas-Assign).
 *  - sort por cabecera + select dropdown (1 columna).
 *  - paginación offset/limit con PAGE_SIZE=25.
 *  - vistas guardadas (crear, editar inline, duplicar, set-default,
 *    borrar, dirty indicator).
 *  - acciones de vista: guardar-como-segmento + push a lista Brevo
 *    (legacy `/api/contact-views/{id}/...` que comparten la misma
 *    tabla → siguen funcionando sobre views entity-typed 'contact').
 *  - bulk actions (`<ContactsBulkBar>` legacy) — el bulk
 *    set-based viene en PR posterior.
 *  - "seleccionar los N filtrados" via
 *    `/api/entities/contact/search/ids` (la versión nueva que aplica
 *    `build_entity_filter` + el `segment_resolver` inyectado por PR-Cf).
 *  - click en fila → `/contacts/[id]`.
 *  - URL state (view_id, rules, q, sort, cols) — `contactsUrlState`.
 *  - back-compat con vistas legacy (flat filters → rules tree via
 *    `legacyFiltersToRulesTree`).
 *
 * Endpoints LEGACY no migrados (siguen sirviendo a otras pantallas;
 * limpieza en PR-H):
 *  - `POST /api/contacts/search` y `/search/ids` — Brevo sync targets,
 *    integrations module.
 *  - `GET /api/contact-views` y CRUD — back-compat URL.
 *  - `POST /api/contact-views/{id}/save-as-segment` — usado aquí
 *    porque comparte tabla con `entity_views`.
 *  - `POST /api/contact-views/{id}/push-to-brevo-list` — idem.
 *  - `POST /api/contacts/bulk-action` — usado por `<ContactsBulkBar>`.
 */
import { History, ListPlus, RotateCcw, Save } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ContactsBulkBar } from "../components/ContactsBulkBar";
import { ErrorState } from "../components/ErrorState";
import { OriginChipsSummary } from "../components/OriginChips";
import { PageHeader } from "../components/PageHeader";
import { PushViewToBrevoModal } from "../components/PushViewToBrevoModal";
import { EntityFilterBuilder } from "../components/entity/EntityFilterBuilder";
import {
  EntityTable,
  type SortState,
} from "../components/entity/EntityTable";
import { EntityViewsTabs } from "../components/entity/EntityViewsTabs";
import {
  getCurrentUser,
  getUsers,
  pushViewToBrevoList,
  saveViewAsSegment,
  type User,
} from "../lib/api";
import { buildContactQuery } from "../lib/contactsRules";
import {
  loadColumnConfig,
  saveColumnConfig,
} from "../lib/entityColumnsStorage";
import {
  getEntityFilterSchema,
  searchEntity,
  searchEntityIds,
  type EntityFilterSchema,
  type FieldDescriptor,
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

const PAGE_SIZE = 25;
const EMPTY_RULES: Record<string, unknown> = {};

type ContactRow = Record<string, unknown>;

export default function ContactsListPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Carga inicial
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [userMap, setUserMap] = useState<Map<string, User>>(new Map());
  const [schema, setSchema] = useState<EntityFilterSchema | null>(null);
  const [views, setViews] = useState<EntityView[]>([]);
  const firstLoadRef = useRef(true);

  // Filtros / búsqueda
  const [rules, setRules] = useState<Record<string, unknown>>(EMPTY_RULES);
  const [q, setQ] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [assignedToMe, setAssignedToMe] = useState(false);
  const [sort, setSort] = useState<SortState | null>({
    field: "created_at",
    direction: "desc",
  });

  // Paginación + selección + view activa
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeView, setActiveView] = useState<EntityView | null>(null);
  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  // Re-mount key del builder para que cargar una vista no remontee
  // el input + pierda foco (decisión §3.8 / PR-Cc).
  const [builderKey, setBuilderKey] = useState(0);

  // Resultados
  const [rows, setRows] = useState<ContactRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // UI de acciones / modales
  const [actionsOpen, setActionsOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<
    | { kind: "create" }
    | { kind: "edit"; view: EntityView }
    | null
  >(null);
  const [pushAfterSave, setPushAfterSave] = useState(false);
  const [showBrevoModal, setShowBrevoModal] = useState(false);

  // --- Loaders ---------------------------------------------------

  const reloadViews = useCallback(async () => {
    const list = await listEntityViews("contact");
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
        direction: view.sort.sort_dir === "asc" ? "asc" : "desc",
      });
    } else {
      setSort({ field: "created_at", direction: "desc" });
    }
    if (view.columns?.visible && view.columns.visible.length > 0) {
      setVisibleColumns(view.columns.visible);
    }
    setOffset(0);
    setBuilderKey((k) => k + 1);
  }, []);

  // Carga única: usuario, schema, vistas, lista de users para el
  // mapa de owner → full_name en la columna "Propietario".
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [me, sch, viewList, allUsers] = await Promise.all([
          getCurrentUser().catch(() => null),
          getEntityFilterSchema("contact"),
          listEntityViews("contact").catch(() => [] as EntityView[]),
          getUsers({ limit: 100 }).catch(() => [] as User[]),
        ]);
        if (cancelled) return;
        setCurrentUser(me);
        setSchema(sch);
        setViews(viewList);
        setUserMap(new Map(allUsers.map((u) => [u.id, u])));
        // Sales → solo míos por defecto.
        if (me?.role === "user") setAssignedToMe(true);

        // Hidrata estado desde URL si la tiene, si no carga la vista
        // por defecto (si hay una).
        if (firstLoadRef.current) {
          firstLoadRef.current = false;
          const urlState = readUrlState(
            new URLSearchParams(searchParams.toString()),
          );
          if (urlState.viewId) {
            const view = viewList.find((v) => v.id === urlState.viewId);
            if (view) {
              applyView(view);
              if (urlState.q) {
                setQ(urlState.q);
                setSearchInput(urlState.q);
              }
              if (urlState.sortBy)
                setSort({
                  field: urlState.sortBy,
                  direction: urlState.sortDir ?? "desc",
                });
              return;
            }
          }
          if (urlState.rules) {
            setRules(urlState.rules);
            setQ(urlState.q ?? "");
            setSearchInput(urlState.q ?? "");
            if (urlState.sortBy)
              setSort({
                field: urlState.sortBy,
                direction: urlState.sortDir ?? "desc",
              });
            return;
          }
          const def = viewList.find((v) => v.is_default);
          if (def) {
            applyView(def);
            return;
          }
          // Sin vista activa → columnas default del schema o
          // localStorage del usuario.
          const defaults = sch.fields
            .filter((f) => f.displayable && f.default_visible)
            .map((f) => f.key);
          const stored = loadColumnConfig("contact", defaults);
          setVisibleColumns(stored.visible);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            extractErrorMessage(
              err,
              "No se pudo cargar el esquema de contactos.",
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

  // --- URL sync --------------------------------------------------

  useEffect(() => {
    if (firstLoadRef.current) return;
    const params = serializeUrlState({
      viewId: activeView?.id ?? null,
      rules: activeView ? null : rules,
      q,
      sortBy: sort?.field ?? "created_at",
      sortDir: sort?.direction ?? "desc",
      columns: visibleColumns,
    });
    const next = params ? `/contacts?${params}` : "/contacts";
    router.replace(next, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, rules, q, sort, visibleColumns]);

  // --- Debounce búsqueda libre -----------------------------------

  useEffect(() => {
    const handle = setTimeout(() => setQ(searchInput.trim()), 250);
    return () => clearTimeout(handle);
  }, [searchInput]);

  // --- Fetch -----------------------------------------------------

  const fetchKey = useMemo(
    () =>
      JSON.stringify({
        rules,
        q,
        assignedToMe,
        owner: currentUser?.id ?? null,
        sort,
        offset,
      }),
    [rules, q, assignedToMe, currentUser, sort, offset],
  );

  useEffect(() => {
    if (!schema) return;
    let cancelled = false;
    setLoading(true);
    const liteSpecs = schema.fields.map((f) => ({
      key: f.key,
      type: f.type,
    }));
    const effective = buildContactQuery({
      rules,
      q,
      assignedToMe,
      currentUserId: currentUser?.id ?? null,
    });
    const pruned = effective
      ? pruneRulesTree(effective as Record<string, unknown>, liteSpecs)
      : {};
    searchEntity<ContactRow>("contact", {
      rules_json: Object.keys(pruned).length ? pruned : null,
      sort_by: sort?.field ?? "created_at",
      sort_dir: sort?.direction ?? "desc",
      limit: PAGE_SIZE,
      offset,
    })
      .then((page) => {
        if (cancelled) return;
        setRows(page.items);
        setTotal(page.total);
        // Drop selecciones que ya no estén visibles.
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
          extractErrorMessage(err, "No se pudieron cargar los contactos."),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // fetchKey absorbe todas las deps relevantes; lo bypasea schema
    // que apunta al mismo array de campos cargado una vez.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey, schema]);

  // --- View CRUD -------------------------------------------------

  function buildViewPayload() {
    return {
      filters: {
        q: q || null,
        rules_json: Object.keys(rules).length > 0 ? rules : null,
      },
      columns: { visible: visibleColumns, order: visibleColumns, widths: {} },
      sort: sort
        ? { sort_by: sort.field, sort_dir: sort.direction }
        : { sort_by: "created_at", sort_dir: "desc" as const },
    };
  }

  async function handleSaveExistingView() {
    if (!activeView) {
      setEditorMode({ kind: "create" });
      return;
    }
    try {
      const updated = await updateEntityView(
        "contact",
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
        "contact",
        editorMode.view.id,
        payload,
      );
      setActiveView(updated);
    } else {
      const created = await createEntityView("contact", payload);
      setActiveView(created);
    }
    await reloadViews();
    setEditorMode(null);
    if (pushAfterSave) {
      setPushAfterSave(false);
      setShowBrevoModal(true);
    }
  }

  function handleRevert() {
    if (!activeView) {
      setRules(EMPTY_RULES);
      setQ("");
      setSearchInput("");
      setSort({ field: "created_at", direction: "desc" });
      setBuilderKey((k) => k + 1);
      return;
    }
    applyView(activeView);
  }

  async function handleSaveAsSegment() {
    if (!activeView) {
      setError("Guarda la consulta como vista antes de promoverla a segmento.");
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
      `${result.contacts_to_push} contacto${result.contacts_to_push === 1 ? "" : "s"} en cola para sincronizar a Brevo (lista #${result.brevo_list_id}).`,
    );
    setShowBrevoModal(false);
  }

  async function handleDuplicateView(view: EntityView) {
    try {
      const copy = await duplicateEntityView("contact", view.id);
      await reloadViews();
      applyView(copy);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar la vista."));
    }
  }

  async function handleSetDefault(view: EntityView) {
    try {
      if (view.is_default) {
        await updateEntityView("contact", view.id, { is_default: false });
      } else {
        await setDefaultEntityView("contact", view.id);
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
      await deleteEntityView("contact", view.id);
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

  // --- Columnas --------------------------------------------------

  function handleVisibleColumnsChange(next: string[]) {
    setVisibleColumns(next);
    if (activeView) {
      updateEntityView("contact", activeView.id, {
        columns: { visible: next, order: next, widths: {} },
      }).catch((err) =>
        setError(extractErrorMessage(err, "No se pudo guardar la vista.")),
      );
    } else {
      saveColumnConfig("contact", { visible: next });
    }
  }

  // --- Selección + bulk ------------------------------------------

  function refireFetch() {
    // Bump identidad de rules para forzar re-fetch tras un bulk
    // action (mismo truco que el legacy usaba).
    setRules((r) => ({ ...r }));
  }

  async function handleSelectAllFiltered() {
    try {
      const liteSpecs = schema!.fields.map((f) => ({
        key: f.key,
        type: f.type,
      }));
      const effective = buildContactQuery({
        rules,
        q,
        assignedToMe,
        currentUserId: currentUser?.id ?? null,
      });
      const pruned = effective
        ? pruneRulesTree(effective as Record<string, unknown>, liteSpecs)
        : {};
      const result = await searchEntityIds("contact", {
        rules_json: Object.keys(pruned).length ? pruned : null,
      });
      setSelected(new Set(result.ids));
      if (result.truncated) {
        setError(
          `Solo se pudieron seleccionar los primeros ${result.max_ids}. Filtra más para abarcar todos.`,
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

  // --- Render ----------------------------------------------------

  const renderCell = useCallback(
    (field: FieldDescriptor, row: ContactRow) => {
      if (field.key === "name") {
        const name = String(row.name ?? "").trim();
        const email = String(row.email ?? "");
        return (
          <strong className="contact-name-cell">
            {name || email || "—"}
          </strong>
        );
      }
      if (field.key === "owner_user_id") {
        const id = row.owner_user_id ? String(row.owner_user_id) : "";
        const user = userMap.get(id);
        if (!user) return <span className="muted">—</span>;
        return <span>{user.full_name}</span>;
      }
      if (field.key === "origin" || field.key === "origin_system") {
        const origin = String(row.origin ?? "");
        if (!origin) return <span className="muted">—</span>;
        return (
          <OriginChipsSummary
            summary={[{ system: origin, account_id: "" }]}
          />
        );
      }
      // El resto cae al defaultRender genérico (tag-multi, date,
      // bool, string, etc).
      return undefined;
    },
    [userMap],
  );

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
        <PageHeader title="Contactos" eyebrow="Contactos" />
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
        title="Lista de contactos"
        eyebrow="Contactos"
        description="Filtros AND/OR/NOT estilo Brevo. Guarda combinaciones como vistas o promociónalas a segmentos / listas de Brevo."
        actions={
          <Link href="/contacts/new" className="button small">
            + Crear contacto
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
            setSort({ field: "created_at", direction: "desc" });
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
          <div
            className="assigned-toggle"
            role="group"
            aria-label="Filtrar por asignación"
          >
            <button
              type="button"
              className={`button small ${assignedToMe ? "" : "secondary"}`}
              onClick={() => {
                setAssignedToMe(true);
                setOffset(0);
              }}
            >
              Solo asignados a mí
            </button>
            <button
              type="button"
              className={`button small ${assignedToMe ? "secondary" : ""}`}
              onClick={() => {
                setAssignedToMe(false);
                setOffset(0);
              }}
            >
              Todos
            </button>
          </div>
          <input
            type="search"
            className="contact-search"
            placeholder="Buscar por nombre, email o teléfono…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setQ(searchInput.trim());
                setOffset(0);
              }
            }}
          />
          <div className="contact-toolbar-spacer" />
          <button
            type="button"
            className="button secondary small"
            onClick={handleRevert}
            title="Descartar cambios"
          >
            <RotateCcw size={11} aria-hidden /> Revertir
          </button>
          <button
            type="button"
            className="button small"
            onClick={handleSaveExistingView}
            disabled={activeView ? !isDirty : false}
            title={activeView ? "Guardar vista actual" : "Guardar como nueva"}
          >
            <Save size={11} aria-hidden /> Guardar
          </button>
          <div className="actions-dropdown">
            <button
              type="button"
              className="button secondary small"
              onClick={() => setActionsOpen((v) => !v)}
            >
              Acciones ▾
            </button>
            {actionsOpen ? (
              <ActionsMenu
                onClose={() => setActionsOpen(false)}
                hasView={Boolean(activeView)}
                onSaveAsNewView={() => {
                  setActionsOpen(false);
                  setEditorMode({ kind: "create" });
                }}
                onSaveAsSegment={() => {
                  setActionsOpen(false);
                  handleSaveAsSegment();
                }}
                onPushToBrevo={() => {
                  setActionsOpen(false);
                  if (!activeView) {
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

        <EntityFilterBuilder
          key={`builder:${builderKey}`}
          fields={schema.fields}
          value={rules}
          onChange={(next) => {
            setRules(next);
            setOffset(0);
          }}
        />

        {message ? (
          <p className="notice notice-success">{message}</p>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}

        <ContactsBulkBar
          selectedIds={Array.from(selected)}
          currentUser={currentUser}
          onAfterAction={(action, affected) => {
            setMessage(
              `${action === "deactivate" ? "Desactivados" : "Actualizados"} ${affected} contacto${affected === 1 ? "" : "s"}.`,
            );
            setSelected(new Set());
            refireFetch();
          }}
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
                <span>✓ {selected.size} contactos seleccionados.</span>
                <button
                  type="button"
                  className="button small secondary"
                  onClick={() => setSelected(new Set())}
                >
                  Deseleccionar todos
                </button>
              </div>
            );
          }
          return (
            <div className="select-all-banner">
              <span>
                ✓ {selected.size} contactos seleccionados en esta página.
              </span>
              <button
                type="button"
                className="button small"
                onClick={handleSelectAllFiltered}
              >
                Seleccionar los {total} contactos que cumplen el filtro
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
          renderCell={renderCell}
          onRowClick={(row) => router.push(`/contacts/${row.id}`)}
        />
      </section>

      {editorMode ? (
        <ViewEditorModal
          mode={editorMode}
          onClose={() => {
            setEditorMode(null);
            setPushAfterSave(false);
          }}
          onSave={handleSaveView}
        />
      ) : null}
      {showBrevoModal && activeView ? (
        <PushViewToBrevoModal
          viewName={activeView.name}
          contactsCount={total}
          onClose={() => setShowBrevoModal(false)}
          onSubmit={handlePushToBrevo}
        />
      ) : null}
    </main>
  );
}

// --- URL state -----------------------------------------------------

type UrlState = {
  viewId: string | null;
  rules: Record<string, unknown> | null;
  q: string | null;
  sortBy: string | null;
  sortDir: "asc" | "desc" | null;
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
    sortDir = dir === "asc" ? "asc" : "desc";
  }
  return {
    viewId,
    rules,
    q: params.get("q"),
    sortBy,
    sortDir,
  };
}

function serializeUrlState(state: {
  viewId: string | null;
  rules: Record<string, unknown> | null;
  q: string;
  sortBy: string;
  sortDir: "asc" | "desc";
  columns: string[];
}): string {
  const params = new URLSearchParams();
  if (state.viewId) params.set("view_id", state.viewId);
  if (state.rules && Object.keys(state.rules).length > 0) {
    const raw = btoa(encodeURIComponent(JSON.stringify(state.rules)));
    params.set("rules", raw);
  }
  if (state.q) params.set("q", state.q);
  if (state.sortBy !== "created_at" || state.sortDir !== "desc") {
    params.set("sort", `${state.sortBy}:${state.sortDir}`);
  }
  if (state.columns.length > 0) {
    params.set("cols", state.columns.join(","));
  }
  return params.toString();
}

// --- ActionsMenu ---------------------------------------------------

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

// --- ViewEditorModal ----------------------------------------------

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
            <button
              type="submit"
              className="button small"
              disabled={busy}
            >
              {busy ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
