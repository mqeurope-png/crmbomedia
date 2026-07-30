"use client";

import {
  Download,
  GitBranch,
  ListPlus,
  Mail,
  Tag as TagIcon,
  UserCheck,
  Workflow as WorkflowIcon,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { getUsers, listSegments, listTags, type User } from "../lib/api";
import {
  addContactsToBrevoList,
  listBrevoLists,
  resolvePrimaryBrevoAccount,
  type BrevoList,
} from "../lib/brevoApi";
import {
  bulkContactAction,
  bulkExportContactsCsv,
  bulkOwnershipPreview,
  type BulkAction,
  type OwnershipPreview,
} from "../lib/bulkApi";
import { extractErrorMessage } from "../lib/errors";
import { listWorkflows, type WorkflowRead } from "../lib/workflowsApi";
import { AddToPipelineModal } from "./AddToPipelineModal";
import { TagPicker } from "./TagPicker";

type Props = {
  selectedIds: string[];
  currentUser: User | null;
  /** Called after a successful action so the list reloads + selection
   *  clears. `action` is widened to string so the Brevo-list flow (which
   *  goes through a different endpoint) can reuse the same success path. */
  onAfterAction: (action: BulkAction | string, affected: number) => void;
  onClear: () => void;
};

type TagOption = { id: string; name: string };
type SegmentOption = { id: string; name: string; is_dynamic: boolean };

// Panel key = una BulkAction o una acción UI-only (Brevo list vive en
// otro endpoint).
type PanelKey = BulkAction | "add_to_brevo_list";

const STATUS_OPTIONS = [
  ["new", "Nuevo"],
  ["qualified", "Calificado"],
  ["working", "Trabajando"],
  ["won", "Ganado"],
  ["lost", "Perdido"],
] as const;

/** Floating action bar that pops up when 1+ contacts are selected.
 *  PR-Hotfix-Notas-Workflows Item C. Acciones masivas completas: tag
 *  (con crear al vuelo), quitar tag, asignar propietario, cambiar
 *  estado, añadir a pipeline / workflow / segmento / lista Brevo, crear
 *  tarea, exportar CSV (admin). El componente está pensado para
 *  reutilizarse en otras listas (empresas, etc.).
 */
export function ContactsBulkBar({
  selectedIds,
  currentUser,
  onAfterAction,
  onClear,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<PanelKey | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [tags, setTags] = useState<TagOption[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowRead[]>([]);
  const [segments, setSegments] = useState<SegmentOption[]>([]);
  const [brevoLists, setBrevoLists] = useState<BrevoList[]>([]);
  const [brevoAccount, setBrevoAccount] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDue, setTaskDue] = useState("");
  // PR-Bulk-Comerciales. Preview de propiedad de la selección (solo se
  // consulta para comerciales) + acción pendiente de confirmar cuando la
  // selección incluye contactos ajenos.
  const [preview, setPreview] = useState<OwnershipPreview | null>(null);
  const [pending, setPending] = useState<
    { label: string; run: () => Promise<void> } | null
  >(null);

  const role = currentUser?.role;
  const canAssign = role === "admin" || role === "manager" || role === "user";
  const canDeactivate = role === "admin";
  // PR-Bulk-Comerciales. admin/manager operan sobre todos los contactos;
  // el comercial (`user`) solo sobre los suyos (el backend filtra y el
  // frontend avisa con un modal si la selección incluye ajenos).
  const isPrivileged = role === "admin" || role === "manager";
  // Export abierto a comerciales (el backend exporta solo los suyos).
  const canExport = role === "admin" || role === "manager" || role === "user";
  // Comercial que seleccionó SOLO contactos ajenos → no puede hacer nada.
  const noOwned = !isPrivileged && preview !== null && preview.owned_by_me === 0;

  const count = selectedIds.length;
  const plural = count === 1 ? "" : "s";

  // Lazy-load de opciones al abrir cada panel.
  useEffect(() => {
    if (open === "assign_owner" && users.length === 0) {
      getUsers()
        .then(setUsers)
        .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar los usuarios.")));
    } else if (open === "remove_tag" && tags.length === 0) {
      listTags()
        .then((page) => setTags(page.items.map((t) => ({ id: t.id, name: t.name }))))
        .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar los tags.")));
    } else if (open === "add_to_workflow" && workflows.length === 0) {
      listWorkflows()
        .then((rows) => setWorkflows(rows.filter((w) => w.status === "active")))
        .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar los workflows.")));
    } else if (open === "add_to_segment" && segments.length === 0) {
      listSegments()
        .then((rows) =>
          setSegments(
            rows
              .filter((s) => !s.is_dynamic)
              .map((s) => ({ id: s.id, name: s.name, is_dynamic: s.is_dynamic })),
          ),
        )
        .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar los segmentos.")));
    } else if (open === "add_to_brevo_list" && brevoLists.length === 0) {
      resolvePrimaryBrevoAccount()
        .then(async (account) => {
          if (!account) {
            setError("No hay una cuenta Brevo configurada.");
            return;
          }
          setBrevoAccount(account);
          setBrevoLists(await listBrevoLists(account));
        })
        .catch((err) => setError(extractErrorMessage(err, "No se pudieron cargar las listas Brevo.")));
    }
  }, [open, users.length, tags.length, workflows.length, segments.length, brevoLists.length]);

  // PR-Bulk-Comerciales. Para comerciales, consulta la partición de la
  // selección (propios vs ajenos) cada vez que cambia la selección.
  useEffect(() => {
    if (isPrivileged || count === 0) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    bulkOwnershipPreview(selectedIds)
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch(() => {
        if (!cancelled) setPreview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isPrivileged, count, selectedIds]);

  if (count === 0) return null;

  /** PR-Bulk-Comerciales. Ejecuta `thunk` directamente para admin/manager
   *  o cuando no hay ajenos; si la selección del comercial incluye
   *  contactos ajenos, abre el modal de aviso con el conteo. */
  async function guard(label: string, thunk: () => Promise<void>) {
    if (isPrivileged) {
      await thunk();
      return;
    }
    let p = preview;
    if (!p) {
      try {
        p = await bulkOwnershipPreview(selectedIds);
        setPreview(p);
      } catch {
        /* si falla el preview, el backend filtra igual */
      }
    }
    if (p && p.foreign > 0) {
      setPending({ label, run: thunk });
    } else {
      await thunk();
    }
  }

  async function doBulk(action: BulkAction, payload: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const result = await bulkContactAction(selectedIds, action, payload);
      onAfterAction(action, result.affected_count);
      setOpen(null);
    } catch (err) {
      setError(extractErrorMessage(err, "La acción bulk falló."));
    } finally {
      setBusy(false);
    }
  }

  function run(
    action: BulkAction,
    payload: Record<string, unknown> = {},
    label = "esta acción",
  ) {
    return guard(label, () => doBulk(action, payload));
  }

  async function handleCreateTask() {
    if (!taskTitle.trim()) return;
    const payload: Record<string, unknown> = { title: taskTitle.trim() };
    if (taskDue) payload.due_at = new Date(taskDue).toISOString();
    await run("create_task", payload, "Crear tarea");
    setTaskTitle("");
    setTaskDue("");
  }

  async function handleAddToBrevoList(listId: number) {
    if (!brevoAccount) return;
    await guard("Empujar a lista Brevo", async () => {
      setBusy(true);
      setError(null);
      try {
        const result = await addContactsToBrevoList(brevoAccount, listId, {
          contact_ids: selectedIds,
        });
        onAfterAction("add_to_brevo_list", result.sent);
        setOpen(null);
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo añadir a la lista Brevo."));
      } finally {
        setBusy(false);
      }
    });
  }

  async function handleDeactivate() {
    if (
      !window.confirm(
        `¿Desactivar ${count} contacto${plural}? Esto los oculta del listado.`,
      )
    ) {
      return;
    }
    await run("deactivate");
  }

  async function handleExport() {
    await guard("Exportar CSV", async () => {
      setBusy(true);
      setError(null);
      try {
        const blob = await bulkExportContactsCsv(selectedIds);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `contacts-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo exportar el CSV."));
      } finally {
        setBusy(false);
      }
    });
  }

  const toggle = (key: PanelKey) => setOpen(open === key ? null : key);
  // PR-Bulk-Comerciales. Comercial sin contactos propios en la selección:
  // acciones deshabilitadas con tooltip.
  const actDisabled = busy || noOwned;
  const noOwnedTitle = noOwned
    ? "No tienes contactos en esta selección"
    : undefined;

  return (
    <>
    <div className="bulk-bar" role="region" aria-label="Acciones masivas">
      <strong>
        {count} contacto{plural} seleccionado{plural}
        {!isPrivileged && preview !== null && preview.foreign > 0 ? (
          <span className="muted small"> · {preview.owned_by_me} tuyo{preview.owned_by_me === 1 ? "" : "s"}</span>
        ) : null}
      </strong>
      <div className="bulk-bar-actions">
        {canAssign ? (
          <button type="button" className="button small" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("assign_owner")}>
            <UserCheck size={11} aria-hidden /> Asignar a…
          </button>
        ) : null}
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("add_tag")}>
          <TagIcon size={11} aria-hidden /> Añadir tag
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("remove_tag")}>
          <TagIcon size={11} aria-hidden /> Quitar tag
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("change_status")}>
          Cambiar estado
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("add_to_pipeline")}>
          <GitBranch size={11} aria-hidden /> A pipeline
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("add_to_workflow")}>
          <WorkflowIcon size={11} aria-hidden /> A workflow
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("add_to_segment")}>
          <ListPlus size={11} aria-hidden /> A segmento
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("add_to_brevo_list")}>
          <Mail size={11} aria-hidden /> A lista Brevo
        </button>
        <button type="button" className="button small secondary" disabled={actDisabled} title={noOwnedTitle} onClick={() => toggle("create_task")}>
          Crear tarea
        </button>
        {canExport ? (
          <button type="button" className="button small secondary" disabled={actDisabled} onClick={handleExport} title={noOwnedTitle ?? "Exportar la selección a CSV"}>
            <Download size={11} aria-hidden /> Exportar CSV
          </button>
        ) : null}
        {canDeactivate ? (
          <button type="button" className="button small danger" disabled={busy} onClick={handleDeactivate}>
            <XCircle size={11} aria-hidden /> Desactivar
          </button>
        ) : null}
        <button type="button" className="bulk-bar-close" onClick={onClear} title="Limpiar selección">
          <X size={14} aria-hidden />
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}

      {open === "assign_owner" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Se aplicará a {count} contacto{plural}.</p>
          {users.length === 0 ? (
            <p className="muted small">Cargando usuarios…</p>
          ) : (
            <ul className="bulk-bar-options">
              {users.filter((u) => u.is_active).map((u) => (
                <li key={u.id}>
                  <button type="button" className="button small secondary" disabled={busy} onClick={() => run("assign_owner", { owner_user_id: u.id }, "Cambiar propietario")}>
                    {u.full_name || u.email}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {open === "add_tag" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Se aplicará a {count} contacto{plural}. Elige un tag o crea uno nuevo.</p>
          <TagPicker onPick={(choice) => run("add_tag", choice, "Añadir tag")} />
        </div>
      ) : null}

      {open === "remove_tag" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Quitar un tag de {count} contacto{plural}.</p>
          {tags.length === 0 ? (
            <p className="muted small">Cargando tags…</p>
          ) : (
            <ul className="bulk-bar-options">
              {tags.map((t) => (
                <li key={t.id}>
                  <button type="button" className="button small secondary" disabled={busy} onClick={() => run("remove_tag", { tag_id: t.id }, "Quitar tag")}>
                    {t.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {open === "change_status" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Cambiar el estado de {count} contacto{plural}.</p>
          <ul className="bulk-bar-options">
            {STATUS_OPTIONS.map(([value, label]) => (
              <li key={value}>
                <button type="button" className="button small secondary" disabled={busy} onClick={() => run("change_status", { new_status: value }, "Cambiar estado")}>
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {open === "add_to_workflow" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Añadir {count} contacto{plural} a un workflow activo.</p>
          {workflows.length === 0 ? (
            <p className="muted small">No hay workflows activos.</p>
          ) : (
            <ul className="bulk-bar-options">
              {workflows.map((w) => (
                <li key={w.id}>
                  <button type="button" className="button small secondary" disabled={busy} onClick={() => run("add_to_workflow", { workflow_id: w.id }, "Añadir a workflow")}>
                    {w.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {open === "add_to_segment" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Añadir {count} contacto{plural} a un segmento estático.</p>
          {segments.length === 0 ? (
            <p className="muted small">No hay segmentos estáticos.</p>
          ) : (
            <ul className="bulk-bar-options">
              {segments.map((s) => (
                <li key={s.id}>
                  <button type="button" className="button small secondary" disabled={busy} onClick={() => run("add_to_segment", { segment_id: s.id }, "Añadir a segmento")}>
                    {s.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {open === "add_to_brevo_list" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Añadir {count} contacto{plural} a una lista Brevo.</p>
          {brevoLists.length === 0 ? (
            <p className="muted small">Cargando listas…</p>
          ) : (
            <ul className="bulk-bar-options">
              {brevoLists.map((l) => (
                <li key={l.id}>
                  <button type="button" className="button small secondary" disabled={busy} onClick={() => handleAddToBrevoList(l.id)}>
                    {l.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {open === "create_task" ? (
        <div className="bulk-bar-panel">
          <p className="muted small">Crear una tarea por cada uno de los {count} contacto{plural}.</p>
          <div className="bulk-bar-task-form">
            <input
              type="text"
              placeholder="Título de la tarea"
              value={taskTitle}
              onChange={(e) => setTaskTitle(e.target.value)}
              disabled={busy}
            />
            <input
              type="datetime-local"
              value={taskDue}
              onChange={(e) => setTaskDue(e.target.value)}
              disabled={busy}
              title="Fecha de vencimiento (opcional)"
            />
            <button type="button" className="button small" disabled={busy || !taskTitle.trim()} onClick={handleCreateTask}>
              Crear {count} tarea{plural}
            </button>
          </div>
        </div>
      ) : null}

      <AddToPipelineModal
        open={open === "add_to_pipeline"}
        excludePipelineIds={[]}
        onSubmit={(pipelineId, stageId) =>
          run("add_to_pipeline", { pipeline_id: pipelineId, stage_id: stageId }, "Añadir a pipeline")
        }
        onClose={() => setOpen(null)}
      />
    </div>

    {/* PR-Bulk-Comerciales. Aviso antes de ejecutar cuando la selección
      * del comercial incluye contactos ajenos (serán ignorados). */}
    {pending && preview ? (
      <div className="modal-overlay" role="dialog" aria-modal="true">
        <div className="modal-dialog small">
          <header className="modal-header">
            <h2>⚠️ Contactos ajenos en la selección</h2>
            <button type="button" className="modal-close" onClick={() => setPending(null)}>×</button>
          </header>
          <div className="modal-body">
            <p>
              Vas a aplicar «{pending.label}» a {preview.total} contacto
              {preview.total === 1 ? "" : "s"}, pero {preview.foreign} no{" "}
              {preview.foreign === 1 ? "es tuyo" : "son tuyos"} y{" "}
              {preview.foreign === 1 ? "será ignorado" : "serán ignorados"}. La
              acción solo se aplicará a {preview.owned_by_me === 1 ? "tu" : "tus"}{" "}
              {preview.owned_by_me} contacto{preview.owned_by_me === 1 ? "" : "s"}.
            </p>
            <footer className="modal-footer">
              <button type="button" className="button secondary" disabled={busy} onClick={() => setPending(null)}>
                Cancelar
              </button>
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={async () => {
                  const p = pending;
                  setPending(null);
                  await p.run();
                }}
              >
                Continuar con {preview.owned_by_me}
              </button>
            </footer>
          </div>
        </div>
      </div>
    ) : null}
    </>
  );
}
