"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createBrevoTemplate,
  deleteBrevoTemplate,
  getBrevoTemplate,
  listBrevoSenders,
  sendBrevoTemplateTest,
  updateBrevoTemplate,
  type BrevoSender,
} from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";
import { ConfirmDialog } from "./ConfirmDialog";
import { HtmlPreview } from "./HtmlPreview";

type Props = {
  accountId: string;
  /** null → create mode. */
  templateId: string | null;
  onDone: () => void | Promise<void>;
  onCancel: () => void;
};

/**
 * Plain-HTML template editor: form fields + a big textarea with a
 * live iframe preview on the right. Deliberately NOT a WYSIWYG — the
 * operator writes/pastes HTML; visual editing happens in Brevo
 * native (link in the templates list).
 */
export function TemplateEditor({
  accountId,
  templateId,
  onDone,
  onCancel,
}: Props) {
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [senderName, setSenderName] = useState("");
  const [senderEmail, setSenderEmail] = useState("");
  const [tag, setTag] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [html, setHtml] = useState("");
  const [senders, setSenders] = useState<BrevoSender[]>([]);
  const [loading, setLoading] = useState(Boolean(templateId));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);
  const [testEmails, setTestEmails] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    listBrevoSenders(accountId)
      .then(setSenders)
      .catch(() => setSenders([]));
  }, [accountId]);

  useEffect(() => {
    if (!templateId) return;
    getBrevoTemplate(templateId)
      .then((template) => {
        setName(template.name);
        setSubject(template.subject ?? "");
        setSenderName(template.sender_name ?? "");
        setSenderEmail(template.sender_email ?? "");
        setTag(template.tag ?? "");
        setIsActive(template.is_active);
        setHtml(template.html_content ?? "");
      })
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar la plantilla.")),
      )
      .finally(() => setLoading(false));
  }, [templateId]);

  const senderOptions = useMemo(
    () => senders.filter((sender) => sender.active),
    [senders],
  );

  async function handleSave() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      if (templateId) {
        await updateBrevoTemplate(templateId, {
          name,
          subject,
          html_content: html,
          sender_name: senderName,
          sender_email: senderEmail,
          tag: tag || null,
          is_active: isActive,
        });
      } else {
        await createBrevoTemplate({
          brevo_account_id: accountId,
          name,
          subject,
          html_content: html,
          sender_name: senderName,
          sender_email: senderEmail,
          tag: tag || null,
          is_active: isActive,
        });
      }
      await onDone();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar la plantilla."));
    } finally {
      setSaving(false);
    }
  }

  async function handleSendTest() {
    const emails = testEmails
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 3);
    if (!emails.length || !templateId) return;
    setError(null);
    try {
      // Pass the editor's current sender: Brevo's sendTest uses the
      // sender stored ON the template, so the backend persists this
      // selection first when it differs from the saved one.
      const result = await sendBrevoTemplateTest(templateId, emails, {
        senderName: senderName || undefined,
        senderEmail: senderEmail || undefined,
      });
      setMessage(result.message);
      setTestOpen(false);
      setTestEmails("");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el test."));
    }
  }

  async function handleDelete() {
    if (!templateId) return;
    try {
      await deleteBrevoTemplate(templateId);
      await onDone();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar la plantilla."));
      setConfirmDelete(false);
    }
  }

  if (loading) return <p className="muted">Cargando plantilla…</p>;

  return (
    <div className="template-editor">
      {error ? <p className="danger-text">{error}</p> : null}
      {message ? <div className="success-state">{message}</div> : null}

      <div className="template-editor-fields">
        <label>
          <span>Nombre interno</span>
          <input
            type="text"
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          <span>Asunto</span>
          <input
            type="text"
            maxLength={500}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </label>
        <label>
          <span>Sender</span>
          {senderOptions.length > 0 ? (
            <select
              value={senderEmail}
              onChange={(event) => {
                const picked = senderOptions.find(
                  (sender) => sender.email === event.target.value,
                );
                setSenderEmail(event.target.value);
                if (picked) setSenderName(picked.name);
              }}
            >
              <option value="">— elige sender —</option>
              {senderOptions.map((sender) => (
                <option key={sender.id} value={sender.email}>
                  {sender.name} &lt;{sender.email}&gt;
                </option>
              ))}
            </select>
          ) : (
            <span className="muted small">
              Ningún sender verificado en Brevo.{" "}
              <a
                href="https://app.brevo.com/senders/list"
                target="_blank"
                rel="noreferrer"
              >
                Abrir Brevo Senders
              </a>
            </span>
          )}
        </label>
        <label>
          <span>Etiqueta (tag)</span>
          <input
            type="text"
            maxLength={100}
            value={tag}
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(event) => setIsActive(event.target.checked)}
          />
          <span>Plantilla activa</span>
        </label>
      </div>

      <div className="template-editor-html">
        <label className="template-editor-html-input">
          <span>HTML</span>
          <textarea
            value={html}
            spellCheck={false}
            onChange={(event) => setHtml(event.target.value)}
            placeholder="<html>…</html> — pega aquí el HTML generado en tu herramienta favorita"
          />
        </label>
        <div className="template-editor-preview">
          <span>Vista previa</span>
          <HtmlPreview html={html} />
        </div>
      </div>

      <div className="form-actions">
        <button
          type="button"
          className="button"
          disabled={saving || !name.trim() || !subject.trim() || !html.trim()}
          onClick={handleSave}
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
        {templateId ? (
          <button
            type="button"
            className="button secondary"
            onClick={() => setTestOpen(true)}
          >
            Enviar test a…
          </button>
        ) : null}
        {templateId ? (
          <button
            type="button"
            className="button secondary danger-text"
            onClick={() => setConfirmDelete(true)}
          >
            Borrar
          </button>
        ) : null}
        <button type="button" className="button secondary" onClick={onCancel}>
          Cancelar
        </button>
      </div>

      {testOpen ? (
        <div className="modal-overlay" role="dialog" aria-modal>
          <div className="modal-card">
            <h3>Enviar test</h3>
            <p className="muted small">
              El test sale con el sender seleccionado arriba
              {senderEmail ? (
                <>
                  {" "}
                  (<strong>{senderEmail}</strong>)
                </>
              ) : null}
              . El asunto y el HTML usados son los de la última versión
              guardada — guarda antes si los has cambiado.
            </p>
            <label>
              <span>Emails (máx. 3, separados por coma)</span>
              <input
                type="text"
                value={testEmails}
                onChange={(event) => setTestEmails(event.target.value)}
                placeholder="qa@mbolasers.com, jefe@mbolasers.com"
              />
            </label>
            <div className="form-actions">
              <button type="button" className="button" onClick={handleSendTest}>
                Enviar
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() => setTestOpen(false)}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmDelete}
        title="Borrar plantilla"
        message={`¿Borrar la plantilla "${name}"? Se elimina también en Brevo.`}
        confirmLabel="Borrar"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
