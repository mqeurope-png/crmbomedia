"use client";

import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  createEmailTemplate,
  listEmailTemplateFolders,
  type EmailTemplateFolderNode,
} from "../../lib/emailTemplatesApi";

type Props = {
  bodyHtml: string;
  subject?: string | null;
  onClose: () => void;
  onSaved: () => void;
};

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

export function SaveTemplateModal({ bodyHtml, subject, onClose, onSaved }: Props) {
  const [name, setName] = useState("");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [folders, setFolders] = useState<EmailTemplateFolderNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    listEmailTemplateFolders()
      .then(setFolders)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar las carpetas."),
        ),
      );
  }, []);

  const flat = useMemo(() => flattenFolders(folders), [folders]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) {
      setError("El nombre es obligatorio.");
      return;
    }
    if (!bodyHtml.trim()) {
      setError("El cuerpo del email está vacío.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createEmailTemplate({
        name: name.trim(),
        subject: subject?.trim() || null,
        body_html: bodyHtml,
        folder_id: folderId,
        is_global: false,
      });
      onSaved();
      onClose();
    } catch (err) {
      setError(
        extractErrorMessage(err, "No se pudo guardar la plantilla."),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-dialog small">
        <div className="modal-header">
          <h2>Guardar como plantilla</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          <form className="modal-form" onSubmit={handleSubmit}>
            <label>
              <span>Nombre</span>
              <input
                type="text"
                required
                maxLength={200}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </label>
            <label>
              <span>Carpeta</span>
              <select
                value={folderId ?? ""}
                onChange={(e) => setFolderId(e.target.value || null)}
              >
                <option value="">Sin carpeta</option>
                {flat.map((f) => (
                  <option key={f.id} value={f.id}>
                    {"— ".repeat(f.depth)}
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            {error ? <p className="modal-error">{error}</p> : null}
            <div className="modal-footer">
              <button
                type="button"
                className="button secondary"
                onClick={onClose}
              >
                Cancelar
              </button>
              <button type="submit" className="button" disabled={saving}>
                {saving ? "Guardando…" : "Guardar plantilla"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
