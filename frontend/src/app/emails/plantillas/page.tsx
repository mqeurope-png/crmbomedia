"use client";

import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderPlus,
  Globe2,
  Lock,
  Pencil,
  Plus,
  Search,
  Share2,
  Star,
  Trash2,
  Users,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";

// TinyMCE touches `window` at module load; keep it client-only via
// next/dynamic. Sprint Email v2.5 — B: reusamos el mismo editor que
// EmailComposerModal (mismas plugins/toolbar/upload de imágenes)
// para que el operador no salte entre dos editores distintos.
const RichEditor = dynamic(
  () =>
    import("../../components/email/RichEditor").then((m) => m.RichEditor),
  {
    ssr: false,
    loading: () => <div className="re-loading">Cargando editor…</div>,
  },
);
import {
  createEmailTemplate,
  createEmailTemplateFolder,
  deleteEmailTemplate,
  deleteEmailTemplateFolder,
  getEmailTemplate,
  listEmailTemplates,
  listEmailTemplateFolders,
  setDefaultTemplateFolder,
  updateEmailTemplate,
  type EmailTemplate,
  type EmailTemplateFolderNode,
  type EmailTemplateFolderVisibility,
  type EmailTemplateListItem,
} from "../../lib/emailTemplatesApi";

// Sprint Email v2.5 — C. Iconos por modo de visibilidad. Coherente
// con la spec de Bart: 🔒 private, 👥 team, 🤝 shared. Lucide
// suministra los tres en estilo monocromo a 11/14 px sin pixelar.
function VisibilityIcon({
  visibility,
  size = 11,
}: {
  visibility: EmailTemplateFolderVisibility;
  size?: number;
}) {
  switch (visibility) {
    case "team":
      return (
        <Users
          size={size}
          aria-label="Carpeta del equipo"
          className="et-visibility-icon"
        />
      );
    case "shared":
      return (
        <Share2
          size={size}
          aria-label="Carpeta compartida"
          className="et-visibility-icon"
        />
      );
    default:
      return (
        <Lock
          size={size}
          aria-label="Carpeta privada"
          className="et-visibility-icon"
        />
      );
  }
}

const ROOT_KEY = "__root__";
const ALL_KEY = "__all__";

type DraftTemplate = {
  name: string;
  subject: string;
  body_html: string;
  folder_id: string | null;
  is_global: boolean;
};

const EMPTY_DRAFT: DraftTemplate = {
  name: "",
  subject: "",
  body_html: "",
  folder_id: null,
  is_global: false,
};

type FolderTreeProps = {
  nodes: EmailTemplateFolderNode[];
  selected: string;
  onSelect: (key: string) => void;
  onToggleDefault: (node: EmailTemplateFolderNode) => void;
  depth?: number;
};

function FolderTree({
  nodes,
  selected,
  onSelect,
  onToggleDefault,
  depth = 0,
}: FolderTreeProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <ul className="et-tree">
      {nodes.map((node) => {
        const isOpen = open[node.id] ?? depth < 1;
        const hasChildren = node.children.length > 0;
        const isSelected = selected === node.id;
        const isDefault = !!node.is_default_for_me;
        return (
          <li key={node.id} className="et-tree-item">
            <div
              className={`et-tree-row${isSelected ? " is-selected" : ""}`}
              style={{ paddingLeft: `${depth * 14 + 8}px` }}
            >
              {hasChildren ? (
                <button
                  type="button"
                  className="et-tree-toggle"
                  aria-label={isOpen ? "Contraer" : "Expandir"}
                  onClick={() =>
                    setOpen((prev) => ({ ...prev, [node.id]: !isOpen }))
                  }
                >
                  {isOpen ? (
                    <ChevronDown size={14} aria-hidden />
                  ) : (
                    <ChevronRight size={14} aria-hidden />
                  )}
                </button>
              ) : (
                <span className="et-tree-toggle is-empty" aria-hidden />
              )}
              <button
                type="button"
                className="et-tree-label"
                onClick={() => onSelect(node.id)}
              >
                <Folder size={14} aria-hidden />
                <span>{node.name}</span>
                <VisibilityIcon visibility={node.visibility} />
                <span className="et-tree-count">{node.template_count}</span>
              </button>
              {/* PR-Workflows-Pipelines-Per-User mini-fix. Toggle de
                  carpeta predeterminada per-user (★). Solo UNA por
                  user — al marcar otra, el backend desmarca la previa. */}
              <button
                type="button"
                className={`et-folder-default-star${
                  isDefault ? " is-default" : ""
                }`}
                title={
                  isDefault
                    ? "Quitar como carpeta predeterminada"
                    : "Marcar como carpeta predeterminada al cargar plantilla"
                }
                aria-label={
                  isDefault
                    ? "Quitar como carpeta predeterminada"
                    : "Marcar como carpeta predeterminada"
                }
                aria-pressed={isDefault}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleDefault(node);
                }}
              >
                <Star
                  size={13}
                  aria-hidden
                  fill={isDefault ? "currentColor" : "none"}
                />
              </button>
            </div>
            {hasChildren && isOpen ? (
              <FolderTree
                nodes={node.children}
                selected={selected}
                onSelect={onSelect}
                onToggleDefault={onToggleDefault}
                depth={depth + 1}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function flattenFolders(
  nodes: EmailTemplateFolderNode[],
  depth = 0,
): Array<{ id: string; name: string; depth: number }> {
  const out: Array<{ id: string; name: string; depth: number }> = [];
  for (const node of nodes) {
    out.push({ id: node.id, name: node.name, depth });
    out.push(...flattenFolders(node.children, depth + 1));
  }
  return out;
}

export default function PlantillasPage() {
  const [folders, setFolders] = useState<EmailTemplateFolderNode[]>([]);
  const [templates, setTemplates] = useState<EmailTemplateListItem[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<string>(ALL_KEY);
  const [searchInput, setSearchInput] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftTemplate>(EMPTY_DRAFT);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftSaving, setDraftSaving] = useState(false);

  const [previewing, setPreviewing] = useState<EmailTemplate | null>(null);

  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [newFolderParent, setNewFolderParent] = useState<string | null>(null);
  const [newFolderVisibility, setNewFolderVisibility] =
    useState<EmailTemplateFolderVisibility>("private");
  const [folderError, setFolderError] = useState<string | null>(null);

  const flatFolders = useMemo(() => flattenFolders(folders), [folders]);

  const refreshFolders = useCallback(async () => {
    try {
      const tree = await listEmailTemplateFolders();
      setFolders(tree);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las carpetas."));
    }
  }, []);

  const refreshTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const filters: Parameters<typeof listEmailTemplates>[0] = {};
      if (selectedFolder === ROOT_KEY) filters.folder_id = null;
      else if (selectedFolder !== ALL_KEY) filters.folder_id = selectedFolder;
      if (debouncedQ) filters.q = debouncedQ;
      const rows = await listEmailTemplates(filters);
      setTemplates(rows);
      setError(null);
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudieron cargar las plantillas."),
      );
    } finally {
      setLoading(false);
    }
  }, [selectedFolder, debouncedQ]);

  useEffect(() => {
    refreshFolders();
  }, [refreshFolders]);

  useEffect(() => {
    refreshTemplates();
  }, [refreshTemplates]);

  // Debounce search input by 300ms — same pattern as the contacts page.
  useEffect(() => {
    const handle = window.setTimeout(
      () => setDebouncedQ(searchInput.trim()),
      300,
    );
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  function openCreate() {
    setEditingId(null);
    setDraft({
      ...EMPTY_DRAFT,
      folder_id:
        selectedFolder === ALL_KEY || selectedFolder === ROOT_KEY
          ? null
          : selectedFolder,
    });
    setDraftError(null);
    setEditorOpen(true);
  }

  async function openEdit(item: EmailTemplateListItem) {
    try {
      const full = await getEmailTemplate(item.id);
      setEditingId(full.id);
      setDraft({
        name: full.name,
        subject: full.subject ?? "",
        body_html: full.body_html,
        folder_id: full.folder_id,
        is_global: full.is_global,
      });
      setDraftError(null);
      setEditorOpen(true);
      setPreviewing(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la plantilla."));
    }
  }

  async function handleDuplicate(item: EmailTemplateListItem) {
    try {
      const full = await getEmailTemplate(item.id);
      await createEmailTemplate({
        name: `${full.name} (copia)`,
        subject: full.subject,
        body_html: full.body_html,
        folder_id: full.folder_id,
        is_global: false,
      });
      setPreviewing(null);
      await refreshFolders();
      await refreshTemplates();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar la plantilla."));
    }
  }

  async function handleDelete(item: EmailTemplateListItem) {
    if (
      !window.confirm(
        `¿Borrar la plantilla "${item.name}"? Esta acción no se puede deshacer.`,
      )
    )
      return;
    try {
      await deleteEmailTemplate(item.id);
      setPreviewing(null);
      await refreshFolders();
      await refreshTemplates();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la plantilla."));
    }
  }

  async function handleSaveDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim()) {
      setDraftError("El nombre es obligatorio.");
      return;
    }
    if (!draft.body_html.trim()) {
      setDraftError("El cuerpo HTML es obligatorio.");
      return;
    }
    setDraftSaving(true);
    setDraftError(null);
    try {
      const payload = {
        name: draft.name.trim(),
        subject: draft.subject.trim() || null,
        body_html: draft.body_html,
        folder_id: draft.folder_id,
        is_global: draft.is_global,
      };
      if (editingId) {
        await updateEmailTemplate(editingId, payload);
      } else {
        await createEmailTemplate(payload);
      }
      setEditorOpen(false);
      setDraft(EMPTY_DRAFT);
      setEditingId(null);
      await refreshFolders();
      await refreshTemplates();
    } catch (err) {
      setDraftError(
        extractErrorMessage(err, "No se pudo guardar la plantilla."),
      );
    } finally {
      setDraftSaving(false);
    }
  }

  async function handleCreateFolder(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newFolderName.trim()) {
      setFolderError("El nombre es obligatorio.");
      return;
    }
    try {
      await createEmailTemplateFolder({
        name: newFolderName.trim(),
        parent_folder_id: newFolderParent,
        visibility: newFolderVisibility,
      });
      setFolderModalOpen(false);
      setNewFolderName("");
      setNewFolderParent(null);
      setNewFolderVisibility("private");
      setFolderError(null);
      await refreshFolders();
    } catch (err) {
      setFolderError(
        extractErrorMessage(err, "No se pudo crear la carpeta."),
      );
    }
  }

  // PR-Workflows-Pipelines-Per-User mini-fix. Marcar / desmarcar la
  // carpeta predeterminada del current_user. Una sola por user — al
  // marcar otra, el backend desmarca la previa automáticamente.
  async function handleToggleDefault(node: EmailTemplateFolderNode) {
    const willMark = !node.is_default_for_me;
    try {
      await setDefaultTemplateFolder(willMark ? node.id : null);
      await refreshFolders();
    } catch (err) {
      setError(
        extractErrorMessage(
          err,
          "No se pudo actualizar la carpeta predeterminada.",
        ),
      );
    }
  }

  async function handleDeleteFolder(folderId: string) {
    const node = flatFolders.find((f) => f.id === folderId);
    if (!node) return;
    if (
      !window.confirm(
        `¿Borrar la carpeta "${node.name}"? Las plantillas dentro quedarán sin carpeta.`,
      )
    )
      return;
    try {
      await deleteEmailTemplateFolder(folderId);
      if (selectedFolder === folderId) setSelectedFolder(ALL_KEY);
      await refreshFolders();
      await refreshTemplates();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la carpeta."));
    }
  }

  const selectedFolderNode =
    selectedFolder !== ALL_KEY && selectedFolder !== ROOT_KEY
      ? flatFolders.find((f) => f.id === selectedFolder) ?? null
      : null;

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Plantillas de email"
        eyebrow="Emails"
        description="Plantillas reutilizables para enviar desde el CRM. Organízalas en carpetas (máx. 3 niveles)."
      />
      {error ? <ErrorState title="Error" message={error} /> : null}

      <section className="et-layout">
        <aside className="et-sidebar card">
          <div className="et-sidebar-header">
            <h3>Carpetas</h3>
            <button
              type="button"
              className="button secondary small"
              onClick={() => {
                setNewFolderParent(
                  selectedFolder === ALL_KEY || selectedFolder === ROOT_KEY
                    ? null
                    : selectedFolder,
                );
                setNewFolderName("");
                setFolderError(null);
                setFolderModalOpen(true);
              }}
            >
              <FolderPlus size={13} aria-hidden /> Nueva
            </button>
          </div>
          <ul className="et-tree et-tree-root">
            <li className="et-tree-item">
              <div
                className={`et-tree-row${selectedFolder === ALL_KEY ? " is-selected" : ""}`}
                style={{ paddingLeft: "8px" }}
              >
                <span className="et-tree-toggle is-empty" aria-hidden />
                <button
                  type="button"
                  className="et-tree-label"
                  onClick={() => setSelectedFolder(ALL_KEY)}
                >
                  <Folder size={14} aria-hidden />
                  <span>Todas</span>
                </button>
              </div>
            </li>
            <li className="et-tree-item">
              <div
                className={`et-tree-row${selectedFolder === ROOT_KEY ? " is-selected" : ""}`}
                style={{ paddingLeft: "8px" }}
              >
                <span className="et-tree-toggle is-empty" aria-hidden />
                <button
                  type="button"
                  className="et-tree-label"
                  onClick={() => setSelectedFolder(ROOT_KEY)}
                >
                  <Folder size={14} aria-hidden />
                  <span>Sin carpeta</span>
                </button>
              </div>
            </li>
          </ul>
          <FolderTree
            nodes={folders}
            selected={selectedFolder}
            onSelect={setSelectedFolder}
            onToggleDefault={handleToggleDefault}
          />
          {selectedFolderNode ? (
            <div className="et-sidebar-actions">
              <button
                type="button"
                className="button secondary small"
                onClick={() => handleDeleteFolder(selectedFolderNode.id)}
              >
                <Trash2 size={13} aria-hidden /> Borrar carpeta
              </button>
            </div>
          ) : null}
        </aside>

        <article className="et-main card">
          <div className="et-toolbar">
            <div className="et-search">
              <Search size={13} aria-hidden />
              <input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Buscar plantilla…"
              />
            </div>
            <button type="button" className="button" onClick={openCreate}>
              <Plus size={14} aria-hidden /> Nueva plantilla
            </button>
          </div>
          {loading && templates.length === 0 ? (
            <p className="muted">Cargando…</p>
          ) : templates.length === 0 ? (
            <p className="muted">No hay plantillas que coincidan.</p>
          ) : (
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Nombre</th>
                    <th>Asunto</th>
                    <th>Uso</th>
                    <th>Última vez</th>
                    <th aria-label="Acciones" />
                  </tr>
                </thead>
                <tbody>
                  {templates.map((t) => (
                    <tr
                      key={t.id}
                      className="et-template-row"
                      onClick={() =>
                        getEmailTemplate(t.id).then(setPreviewing).catch((err) =>
                          setError(
                            extractErrorMessage(
                              err,
                              "No se pudo cargar la plantilla.",
                            ),
                          ),
                        )
                      }
                    >
                      <td>
                        <strong>{t.name}</strong>
                        {t.is_global ? (
                          <Globe2
                            size={11}
                            aria-label="Global"
                            className="et-global-icon"
                          />
                        ) : null}
                      </td>
                      <td className="muted">{t.subject || "—"}</td>
                      <td>{t.usage_count}</td>
                      <td className="muted">
                        {t.last_used_at
                          ? new Date(t.last_used_at).toLocaleDateString(
                              "es-ES",
                              {
                                day: "2-digit",
                                month: "short",
                                year: "numeric",
                              },
                            )
                          : "—"}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={(e) => {
                            e.stopPropagation();
                            openEdit(t);
                          }}
                        >
                          <Pencil size={12} aria-hidden /> Editar
                        </button>
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(t);
                          }}
                        >
                          <Trash2 size={12} aria-hidden /> Borrar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
      </section>

      {editorOpen ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setEditorOpen(false);
          }}
        >
          <div className="modal-dialog et-editor-dialog">
            <div className="modal-header">
              <h2>{editingId ? "Editar plantilla" : "Nueva plantilla"}</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setEditorOpen(false)}
                aria-label="Cerrar"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form className="modal-form" onSubmit={handleSaveDraft}>
                <label>
                  <span>Nombre</span>
                  <input
                    type="text"
                    required
                    maxLength={200}
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                </label>
                <label>
                  <span>Asunto</span>
                  <input
                    type="text"
                    maxLength={500}
                    value={draft.subject}
                    onChange={(e) =>
                      setDraft({ ...draft, subject: e.target.value })
                    }
                  />
                </label>
                <label>
                  <span>Carpeta</span>
                  <select
                    value={draft.folder_id ?? ""}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        folder_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">Sin carpeta</option>
                    {flatFolders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {"— ".repeat(f.depth)}
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Cuerpo</span>
                  <RichEditor
                    value={draft.body_html}
                    onChange={(html) =>
                      setDraft({ ...draft, body_html: html })
                    }
                    placeholder="Escribe la plantilla. Usa {nombre}, {empresa}, {email} para personalizar."
                    minHeight={420}
                    draftKey={
                      editingId ? `template-${editingId}` : "template-new"
                    }
                  />
                  <small className="muted">
                    Variables disponibles: <code>{"{nombre}"}</code>,{" "}
                    <code>{"{empresa}"}</code>, <code>{"{email}"}</code>. Se
                    reemplazan al enviar con los datos del contacto
                    destinatario.
                  </small>
                </label>
                {draftError ? (
                  <p className="modal-error">{draftError}</p>
                ) : null}
                <div className="modal-footer">
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setEditorOpen(false)}
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    className="button"
                    disabled={draftSaving}
                  >
                    {draftSaving
                      ? "Guardando…"
                      : editingId
                        ? "Guardar cambios"
                        : "Crear plantilla"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      ) : null}

      {previewing ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setPreviewing(null);
          }}
        >
          <div className="modal-dialog et-preview-dialog">
            <div className="modal-header">
              <h2>{previewing.name}</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setPreviewing(null)}
                aria-label="Cerrar"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              {previewing.subject ? (
                <p className="et-preview-subject">
                  <strong>Asunto:</strong> {previewing.subject}
                </p>
              ) : null}
              <div
                className="et-preview-html"
                // The preview is intentionally rendered as-is. The
                // editor is owner/admin gated; we never inject untrusted
                // content here. Tiptap (2.2b) will sanitize on save.
                dangerouslySetInnerHTML={{ __html: previewing.body_html }}
              />
              <div className="modal-footer">
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => handleDuplicate(previewing)}
                >
                  Duplicar
                </button>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => handleDelete(previewing)}
                  >
                    Borrar
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() => openEdit(previewing)}
                  >
                    Editar
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {folderModalOpen ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setFolderModalOpen(false);
          }}
        >
          <div className="modal-dialog small">
            <div className="modal-header">
              <h2>Nueva carpeta</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setFolderModalOpen(false)}
                aria-label="Cerrar"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form className="modal-form" onSubmit={handleCreateFolder}>
                <label>
                  <span>Nombre</span>
                  <input
                    type="text"
                    required
                    maxLength={200}
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    autoFocus
                  />
                </label>
                <label>
                  <span>Carpeta padre</span>
                  <select
                    value={newFolderParent ?? ""}
                    onChange={(e) =>
                      setNewFolderParent(e.target.value || null)
                    }
                  >
                    <option value="">Raíz</option>
                    {flatFolders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {"— ".repeat(f.depth)}
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Visibilidad</span>
                  <select
                    value={newFolderVisibility}
                    onChange={(e) =>
                      setNewFolderVisibility(
                        e.target.value as EmailTemplateFolderVisibility,
                      )
                    }
                  >
                    <option value="private">
                      🔒 Privada — solo tú
                    </option>
                    <option value="team">
                      👥 Del equipo — todos pueden ver / editar
                    </option>
                    <option value="shared">
                      🤝 Compartida — invitas a usuarios concretos
                    </option>
                  </select>
                  <small className="muted">
                    Las plantillas dentro heredan el modo. Puedes cambiarlo
                    luego desde la carpeta.
                  </small>
                </label>
                {folderError ? (
                  <p className="modal-error">{folderError}</p>
                ) : null}
                <div className="modal-footer">
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setFolderModalOpen(false)}
                  >
                    Cancelar
                  </button>
                  <button type="submit" className="button">
                    Crear carpeta
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
