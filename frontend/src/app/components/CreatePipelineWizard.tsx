"use client";

import { useState } from "react";
import {
  createPipeline,
  createPipelineFromTemplate,
  type Pipeline,
  type PipelineProposal,
  type PipelineTemplate,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";
import { PipelineAIGenerator } from "./PipelineAIGenerator";
import { PipelineTemplateGallery } from "./PipelineTemplateGallery";

type Props = {
  open: boolean;
  /** When false, the AI option stays hidden (no `ANTHROPIC_API_KEY`
   *  configured on the backend). */
  aiAvailable: boolean;
  onCreated: (pipeline: Pipeline) => void;
  onClose: () => void;
};

type Mode = "menu" | "scratch" | "template" | "ai" | "preview";

export function CreatePipelineWizard({
  open,
  aiAvailable,
  onCreated,
  onClose,
}: Props) {
  const [mode, setMode] = useState<Mode>("menu");
  const [error, setError] = useState<string | null>(null);
  // "Scratch" form state.
  const [scratchName, setScratchName] = useState("");
  const [scratchDescription, setScratchDescription] = useState("");
  // Template selection state.
  const [pickedTemplate, setPickedTemplate] = useState<PipelineTemplate | null>(null);
  const [templateName, setTemplateName] = useState("");
  // AI proposal state.
  const [proposal, setProposal] = useState<PipelineProposal | null>(null);
  const [proposalName, setProposalName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  function reset() {
    setMode("menu");
    setError(null);
    setScratchName("");
    setScratchDescription("");
    setPickedTemplate(null);
    setTemplateName("");
    setProposal(null);
    setProposalName("");
  }

  async function handleScratchSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scratchName.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createPipeline({
        name: scratchName.trim(),
        description: scratchDescription || null,
        stages: [],
      });
      onCreated(created);
      reset();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el pipeline."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTemplateConfirm() {
    if (!pickedTemplate) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createPipelineFromTemplate({
        template_id: pickedTemplate.id,
        name: templateName.trim() || undefined,
      });
      onCreated(created);
      reset();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el pipeline."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleProposalConfirm() {
    if (!proposal) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createPipeline({
        name: (proposalName.trim() || proposal.name).slice(0, 100),
        description: proposal.description ?? null,
        color: proposal.color ?? null,
        stages: proposal.stages.map((stage, index) => ({
          ...stage,
          position: index,
        })),
      });
      onCreated(created);
      reset();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el pipeline."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleProposal(proposal: PipelineProposal) {
    setProposal(proposal);
    setProposalName(proposal.name);
    setMode("preview");
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal>
      <div className="modal-card modal-card-wide">
        <div className="wizard-header">
          {mode !== "menu" ? (
            <button
              type="button"
              className="button secondary small"
              onClick={() => {
                setMode("menu");
                setError(null);
              }}
            >
              ← Volver
            </button>
          ) : null}
          <h2>
            {mode === "menu" && "Nuevo pipeline"}
            {mode === "scratch" && "Crear desde cero"}
            {mode === "template" && "Usar plantilla"}
            {mode === "ai" && "Generar con IA"}
            {mode === "preview" && "Revisar propuesta IA"}
          </h2>
          <button
            type="button"
            className="button secondary small"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cerrar
          </button>
        </div>

        {error ? <p className="danger-text">{error}</p> : null}

        {mode === "menu" ? (
          <div className="wizard-options">
            <button
              type="button"
              className="wizard-option"
              onClick={() => setMode("scratch")}
            >
              <span className="wizard-option-icon" aria-hidden>
                ✏️
              </span>
              <strong>Desde cero</strong>
              <span className="muted small">
                Formulario vacío para construir tu propio flujo paso a paso.
              </span>
            </button>
            <button
              type="button"
              className="wizard-option"
              onClick={() => setMode("template")}
            >
              <span className="wizard-option-icon" aria-hidden>
                📚
              </span>
              <strong>Usar plantilla</strong>
              <span className="muted small">
                Empieza con una plantilla pre-hecha (ventas, onboarding,
                soporte…). La puedes adaptar después.
              </span>
            </button>
            {aiAvailable ? (
              <button
                type="button"
                className="wizard-option"
                onClick={() => setMode("ai")}
              >
                <span className="wizard-option-icon" aria-hidden>
                  ✨
                </span>
                <strong>Generar con IA</strong>
                <span className="muted small">
                  Describe tu caso en una frase y la IA propone un pipeline
                  adecuado.
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

        {mode === "scratch" ? (
          <form onSubmit={handleScratchSubmit} className="stacked-form">
            <label>
              <span>Nombre</span>
              <input
                type="text"
                required
                maxLength={100}
                value={scratchName}
                onChange={(event) => setScratchName(event.target.value)}
              />
            </label>
            <label>
              <span>Descripción</span>
              <textarea
                rows={3}
                maxLength={2000}
                value={scratchDescription}
                onChange={(event) => setScratchDescription(event.target.value)}
              />
            </label>
            <p className="muted small">
              Pipeline sin etapas. Añádelas desde el editor después de crearlo.
            </p>
            <div className="form-actions">
              <button type="submit" className="button" disabled={submitting}>
                {submitting ? "Creando…" : "Crear pipeline"}
              </button>
            </div>
          </form>
        ) : null}

        {mode === "template" ? (
          <>
            {pickedTemplate ? (
              <div className="wizard-preview">
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => setPickedTemplate(null)}
                >
                  ← Otra plantilla
                </button>
                <h3>{pickedTemplate.name}</h3>
                <p className="muted">{pickedTemplate.description}</p>
                <label>
                  <span>Nombre para tu pipeline</span>
                  <input
                    type="text"
                    maxLength={100}
                    value={templateName}
                    onChange={(event) => setTemplateName(event.target.value)}
                    placeholder={pickedTemplate.name}
                  />
                </label>
                <ol className="pipeline-stage-summary">
                  {pickedTemplate.stages.map((stage) => (
                    <li key={stage.name}>
                      <span
                        className="tag-color-swatch"
                        style={{
                          background:
                            stage.color || pickedTemplate.color || "#cdd5e1",
                        }}
                        aria-hidden
                      />
                      <span>{stage.name}</span>
                      {stage.is_won ? (
                        <span className="status status-done">Ganado</span>
                      ) : null}
                      {stage.is_lost ? (
                        <span className="status status-denied">Perdido</span>
                      ) : null}
                      {stage.target_days ? (
                        <span className="muted small">
                          target {stage.target_days}d
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ol>
                <div className="form-actions">
                  <button
                    type="button"
                    className="button"
                    onClick={handleTemplateConfirm}
                    disabled={submitting}
                  >
                    {submitting ? "Creando…" : "Usar esta plantilla"}
                  </button>
                </div>
              </div>
            ) : (
              <PipelineTemplateGallery
                onPick={(tmpl) => {
                  setPickedTemplate(tmpl);
                  setTemplateName(tmpl.name);
                }}
                onError={setError}
              />
            )}
          </>
        ) : null}

        {mode === "ai" ? <PipelineAIGenerator onProposal={handleProposal} /> : null}

        {mode === "preview" && proposal ? (
          <div className="wizard-preview">
            <label>
              <span>Nombre del pipeline</span>
              <input
                type="text"
                maxLength={100}
                value={proposalName}
                onChange={(event) => setProposalName(event.target.value)}
              />
            </label>
            {proposal.description ? (
              <p className="muted">{proposal.description}</p>
            ) : null}
            <ol className="pipeline-stage-summary">
              {proposal.stages.map((stage) => (
                <li key={`${stage.position}-${stage.name}`}>
                  <span
                    className="tag-color-swatch"
                    style={{
                      background:
                        stage.color || proposal.color || "#cdd5e1",
                    }}
                    aria-hidden
                  />
                  <span>{stage.name}</span>
                  {stage.is_won ? (
                    <span className="status status-done">Ganado</span>
                  ) : null}
                  {stage.is_lost ? (
                    <span className="status status-denied">Perdido</span>
                  ) : null}
                  {stage.target_days ? (
                    <span className="muted small">
                      target {stage.target_days}d
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
            <div className="form-actions">
              <button
                type="button"
                className="button"
                onClick={handleProposalConfirm}
                disabled={submitting}
              >
                {submitting ? "Creando…" : "Crear directamente"}
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() => setMode("ai")}
                disabled={submitting}
              >
                Regenerar
              </button>
            </div>
            <p className="muted small">
              La IA propone, tú decides. Repasa los nombres y los SLA antes
              de crear.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
