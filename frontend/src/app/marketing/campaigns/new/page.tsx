"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../../components/ErrorState";
import { HtmlPreview } from "../../../components/HtmlPreview";
import { PageHeader } from "../../../components/PageHeader";
import {
  createBrevoCampaign,
  getBrevoTemplate,
  listBrevoLists,
  listBrevoSenders,
  listBrevoTemplates,
  resolvePrimaryBrevoAccount,
  sendBrevoCampaignTest,
  type BrevoList,
  type BrevoSender,
  type BrevoTemplate,
} from "../../../lib/brevoApi";
import { listSegments, type Segment } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";

type Step = 1 | 2 | 3 | 4 | 5;

const STEP_LABELS = [
  "Básico",
  "Contenido",
  "Destinatarios",
  "Programación",
  "Revisión",
];

export default function NewCampaignWizard() {
  const router = useRouter();
  const [accountId, setAccountId] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);
  const [step, setStep] = useState<Step>(1);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Step 1 — basics.
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [senderEmail, setSenderEmail] = useState("");
  const [senderName, setSenderName] = useState("");
  const [replyTo, setReplyTo] = useState("");
  const [senders, setSenders] = useState<BrevoSender[]>([]);

  // Step 2 — content.
  const [contentMode, setContentMode] = useState<"template" | "scratch">(
    "scratch",
  );
  const [templates, setTemplates] = useState<BrevoTemplate[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [html, setHtml] = useState("");

  // Step 3 — recipients.
  const [recipientMode, setRecipientMode] = useState<"segment" | "list">(
    "segment",
  );
  const [segments, setSegments] = useState<Segment[]>([]);
  const [segmentId, setSegmentId] = useState("");
  const [lists, setLists] = useState<BrevoList[]>([]);
  const [listId, setListId] = useState("");

  // Step 4 — scheduling.
  const [sendMode, setSendMode] = useState<"now" | "schedule" | "draft">(
    "draft",
  );
  const [scheduledAt, setScheduledAt] = useState("");

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (!account) return;
        const [senderRows, templateRows, listRows, segmentRows] =
          await Promise.all([
            listBrevoSenders(account).catch(() => []),
            listBrevoTemplates(account).catch(() => []),
            listBrevoLists(account).catch(() => []),
            listSegments().catch(() => []),
          ]);
        setSenders(senderRows.filter((sender) => sender.active));
        setTemplates(templateRows);
        setLists(listRows);
        setSegments(segmentRows);
      })
      .catch(() => setError("No se pudo resolver la cuenta Brevo."))
      .finally(() => setResolved(true));
  }, []);

  const selectedSegment = useMemo(
    () => segments.find((segment) => segment.id === segmentId),
    [segments, segmentId],
  );
  const selectedList = useMemo(
    () => lists.find((list) => String(list.id) === listId),
    [lists, listId],
  );

  async function pickTemplate(id: string) {
    setTemplateId(id);
    if (!id) return;
    try {
      const template = await getBrevoTemplate(id);
      if (template.html_content) setHtml(template.html_content);
      if (template.subject && !subject) setSubject(template.subject);
      if (template.sender_email && !senderEmail) {
        setSenderEmail(template.sender_email);
        setSenderName(template.sender_name ?? "");
      }
    } catch {
      // Selección sigue siendo válida sin previsualización.
    }
  }

  const canContinue: Record<Step, boolean> = {
    1: Boolean(name.trim() && subject.trim() && senderEmail),
    2:
      contentMode === "template"
        ? Boolean(templateId)
        : Boolean(html.trim()),
    3:
      recipientMode === "segment"
        ? Boolean(segmentId)
        : Boolean(listId),
    4:
      sendMode !== "schedule" ||
      (Boolean(scheduledAt) &&
        new Date(scheduledAt).getTime() > Date.now() + 60 * 60 * 1000),
    5: true,
  };

  async function handleConfirm() {
    if (!accountId) return;
    setSubmitting(true);
    setError(null);
    try {
      const picked = templates.find((t) => t.id === templateId);
      const campaign = await createBrevoCampaign({
        brevo_account_id: accountId,
        name: name.trim(),
        subject: subject.trim(),
        sender_name: senderName,
        sender_email: senderEmail,
        reply_to: replyTo.trim() || null,
        html_content:
          contentMode === "scratch" ? html : picked?.html_content ?? html,
        template_id:
          contentMode === "template" && picked
            ? picked.brevo_template_id
            : null,
        list_ids: recipientMode === "list" && listId ? [Number(listId)] : null,
        segment_id: recipientMode === "segment" ? segmentId : null,
        scheduled_at:
          sendMode === "schedule" && scheduledAt
            ? new Date(scheduledAt).toISOString()
            : null,
      });
      if (sendMode === "now") {
        const { sendBrevoCampaignNow } = await import("../../../lib/brevoApi");
        await sendBrevoCampaignNow(campaign.id);
      }
      router.push(`/marketing/campaigns/${campaign.id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la campaña."));
      setSubmitting(false);
    }
  }

  async function handleSendTestDraft() {
    const emails = window.prompt(
      "Emails para el test (máx. 3, separados por coma):",
    );
    if (!emails || !accountId) return;
    setError(null);
    try {
      // A test needs a campaign — create it as draft first, send the
      // test, and continue editing from the detail page.
      const picked = templates.find((t) => t.id === templateId);
      const campaign = await createBrevoCampaign({
        brevo_account_id: accountId,
        name: `${name.trim()} (borrador test)`,
        subject: subject.trim(),
        sender_name: senderName,
        sender_email: senderEmail,
        html_content:
          contentMode === "scratch" ? html : picked?.html_content ?? html,
        template_id:
          contentMode === "template" && picked
            ? picked.brevo_template_id
            : null,
        list_ids: recipientMode === "list" && listId ? [Number(listId)] : null,
        segment_id: recipientMode === "segment" ? segmentId : null,
      });
      await sendBrevoCampaignTest(
        campaign.id,
        emails
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean)
          .slice(0, 3),
      );
      setMessage("Test enviado; la campaña quedó en borrador.");
      router.push(`/marketing/campaigns/${campaign.id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el test."));
    }
  }

  if (resolved && !accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Nueva campaña" eyebrow="Marketing" />
        <ErrorState
          title="Brevo no configurado"
          message="Configura una cuenta Brevo en /admin/integrations."
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Nueva campaña"
        eyebrow="Marketing"
        crumbs={[
          { label: "Campañas", href: "/marketing/campaigns" },
          { label: "Nueva" },
        ]}
      />

      <nav className="wizard-stepper" aria-label="Pasos">
        {STEP_LABELS.map((label, index) => {
          const number = (index + 1) as Step;
          return (
            <button
              key={label}
              type="button"
              className={`wizard-step${step === number ? " is-active" : ""}${
                step > number ? " is-done" : ""
              }`}
              onClick={() => setStep(number)}
              disabled={number > step && !canContinue[step]}
            >
              <span className="wizard-step-number">{number}</span> {label}
            </button>
          );
        })}
      </nav>

      {error ? <p className="danger-text">{error}</p> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {step === 1 ? (
        <section className="panel stacked-form">
          <label>
            <span>Nombre interno</span>
            <input
              type="text"
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Black Friday láser UV — ronda 1"
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
            {senders.length > 0 ? (
              <select
                value={senderEmail}
                onChange={(event) => {
                  const picked = senders.find(
                    (sender) => sender.email === event.target.value,
                  );
                  setSenderEmail(event.target.value);
                  if (picked) setSenderName(picked.name);
                }}
              >
                <option value="">— elige sender verificado —</option>
                {senders.map((sender) => (
                  <option key={sender.id} value={sender.email}>
                    {sender.name} &lt;{sender.email}&gt;
                  </option>
                ))}
              </select>
            ) : (
              <span className="muted small">
                Ningún sender configurado — ve a Brevo y verifica al menos un
                email.{" "}
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
            <span>Reply-to (opcional)</span>
            <input
              type="email"
              value={replyTo}
              onChange={(event) => setReplyTo(event.target.value)}
            />
          </label>
        </section>
      ) : null}

      {step === 2 ? (
        <section className="panel stacked-form">
          <div className="radio-row">
            <label className="checkbox">
              <input
                type="radio"
                name="content-mode"
                checked={contentMode === "template"}
                onChange={() => setContentMode("template")}
              />
              <span>Desde plantilla</span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="content-mode"
                checked={contentMode === "scratch"}
                onChange={() => setContentMode("scratch")}
              />
              <span>Desde cero (HTML)</span>
            </label>
          </div>

          {contentMode === "template" ? (
            <label>
              <span>Plantilla</span>
              <select
                value={templateId}
                onChange={(event) => pickTemplate(event.target.value)}
              >
                <option value="">— elige plantilla —</option>
                {templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <div className="template-editor-html">
            <label className="template-editor-html-input">
              <span>HTML</span>
              <textarea
                value={html}
                spellCheck={false}
                onChange={(event) => setHtml(event.target.value)}
                placeholder="<html>…</html>"
                readOnly={contentMode === "template" && !html}
              />
            </label>
            <div className="template-editor-preview">
              <span>Vista previa</span>
              <HtmlPreview html={html} />
            </div>
          </div>
        </section>
      ) : null}

      {step === 3 ? (
        <section className="panel stacked-form">
          <div className="radio-row">
            <label className="checkbox">
              <input
                type="radio"
                name="recipient-mode"
                checked={recipientMode === "segment"}
                onChange={() => setRecipientMode("segment")}
              />
              <span>Desde segmento del CRM</span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="recipient-mode"
                checked={recipientMode === "list"}
                onChange={() => setRecipientMode("list")}
              />
              <span>Desde lista Brevo existente</span>
            </label>
          </div>

          {recipientMode === "segment" ? (
            <label>
              <span>Segmento</span>
              <select
                value={segmentId}
                onChange={(event) => setSegmentId(event.target.value)}
              >
                <option value="">— elige segmento —</option>
                {segments.map((segment) => (
                  <option key={segment.id} value={segment.id}>
                    {segment.name}
                  </option>
                ))}
              </select>
              {selectedSegment ? (
                <span className="muted small">
                  {selectedSegment.cached_count ?? "?"} contactos cumplen. Al
                  guardar se creará una lista Brevo nueva con ellos.
                </span>
              ) : null}
            </label>
          ) : (
            <label>
              <span>Lista Brevo</span>
              <select
                value={listId}
                onChange={(event) => setListId(event.target.value)}
              >
                <option value="">— elige lista —</option>
                {lists.map((list) => (
                  <option key={list.id} value={String(list.id)}>
                    {list.name} ({list.total_subscribers})
                  </option>
                ))}
              </select>
              {selectedList ? (
                <span className="muted small">
                  {selectedList.total_subscribers} suscriptores en la lista.
                </span>
              ) : null}
            </label>
          )}
        </section>
      ) : null}

      {step === 4 ? (
        <section className="panel stacked-form">
          <div className="radio-row radio-row-vertical">
            <label className="checkbox">
              <input
                type="radio"
                name="send-mode"
                checked={sendMode === "now"}
                onChange={() => setSendMode("now")}
              />
              <span>Enviar ahora</span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="send-mode"
                checked={sendMode === "schedule"}
                onChange={() => setSendMode("schedule")}
              />
              <span>Programar</span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="send-mode"
                checked={sendMode === "draft"}
                onChange={() => setSendMode("draft")}
              />
              <span>Guardar como borrador</span>
            </label>
          </div>
          {sendMode === "schedule" ? (
            <label>
              <span>Fecha y hora (mínimo +1h)</span>
              <input
                type="datetime-local"
                value={scheduledAt}
                onChange={(event) => setScheduledAt(event.target.value)}
              />
            </label>
          ) : null}
        </section>
      ) : null}

      {step === 5 ? (
        <section className="panel">
          <h3>Revisión</h3>
          <ul className="campaign-review-list">
            <li>
              <strong>Nombre:</strong> {name}
            </li>
            <li>
              <strong>Asunto:</strong> {subject}
            </li>
            <li>
              <strong>Sender:</strong> {senderName} &lt;{senderEmail}&gt;
              {replyTo ? ` · reply-to ${replyTo}` : ""}
            </li>
            <li>
              <strong>Contenido:</strong>{" "}
              {contentMode === "template"
                ? `Plantilla "${templates.find((t) => t.id === templateId)?.name ?? ""}"`
                : `HTML manual (${html.length} caracteres)`}
            </li>
            <li>
              <strong>Destinatarios:</strong>{" "}
              {recipientMode === "segment"
                ? `Segmento "${selectedSegment?.name ?? ""}" (${selectedSegment?.cached_count ?? "?"} contactos)`
                : `Lista Brevo "${selectedList?.name ?? ""}" (${selectedList?.total_subscribers ?? "?"})`}
            </li>
            <li>
              <strong>Envío:</strong>{" "}
              {sendMode === "now"
                ? "Inmediato"
                : sendMode === "schedule"
                  ? `Programado para ${scheduledAt ? new Date(scheduledAt).toLocaleString("es-ES") : "—"}`
                  : "Borrador"}
            </li>
          </ul>
          <div className="form-actions">
            <button
              type="button"
              className="button"
              disabled={submitting}
              onClick={handleConfirm}
            >
              {submitting ? "Creando…" : "Confirmar"}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={submitting}
              onClick={handleSendTestDraft}
            >
              Enviar prueba a…
            </button>
          </div>
        </section>
      ) : null}

      {step < 5 ? (
        <div className="form-actions">
          {step > 1 ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => setStep((step - 1) as Step)}
            >
              ← Anterior
            </button>
          ) : null}
          <button
            type="button"
            className="button"
            disabled={!canContinue[step]}
            onClick={() => setStep((step + 1) as Step)}
          >
            Siguiente →
          </button>
        </div>
      ) : null}
    </main>
  );
}
