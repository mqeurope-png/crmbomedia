"use client";

import { useState } from "react";
import {
  createSegment,
  type Segment,
  type SegmentAIGenerateResponse,
  type SegmentTemplate,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";
import { SegmentAIGenerator } from "./SegmentAIGenerator";
import { SegmentTemplateGallery } from "./SegmentTemplateGallery";

type Props = {
  open: boolean;
  aiAvailable: boolean;
  onCreated: (segment: Segment) => void;
  onClose: () => void;
};

type Mode = "menu" | "scratch" | "template" | "ai" | "preview";

export function SegmentWizard({
  open,
  aiAvailable,
  onCreated,
  onClose,
}: Props) {
  const [mode, setMode] = useState<Mode>("menu");
  const [error, setError] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [pickedTemplate, setPickedTemplate] = useState<SegmentTemplate | null>(
    null,
  );
  const [proposal, setProposal] = useState<SegmentAIGenerateResponse | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  function reset() {
    setMode("menu");
    setError(null);
    setDraftName("");
    setDraftDescription("");
    setPickedTemplate(null);
    setProposal(null);
  }

  async function createFrom(
    name: string,
    rules: Record<string, unknown>,
    color?: string | null,
  ) {
    setSubmitting(true);
    setError(null);
    try {
      const segment = await createSegment({
        name,
        description: draftDescription || null,
        rules,
        color: color ?? null,
      });
      onCreated(segment);
      reset();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el segmento."));
    } finally {
      setSubmitting(false);
    }
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
            {mode === "menu" && "Nuevo segmento"}
            {mode === "scratch" && "Crear desde cero"}
            {mode === "template" && "Desde plantilla"}
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
              <span className="wizard-option-icon">✏️</span>
              <strong>Desde cero</strong>
              <span className="muted small">
                Builder visual vacío. Para cuando ya sabes qué reglas
                quieres.
              </span>
            </button>
            <button
              type="button"
              className="wizard-option"
              onClick={() => setMode("template")}
            >
              <span className="wizard-option-icon">📚</span>
              <strong>Desde plantilla</strong>
              <span className="muted small">
                &ldquo;Hot leads&rdquo;, &ldquo;Inactivos 90 días&rdquo;,
                &ldquo;Sin consentimiento&rdquo;, …
              </span>
            </button>
            {aiAvailable ? (
              <button
                type="button"
                className="wizard-option"
                onClick={() => setMode("ai")}
              >
                <span className="wizard-option-icon">✨</span>
                <strong>Generar con IA</strong>
                <span className="muted small">
                  Describe el segmento en una frase y la IA propone las
                  reglas.
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

        {mode === "scratch" ? (
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (!draftName.trim()) return;
              await createFrom(draftName.trim(), {});
            }}
            className="stacked-form"
          >
            <label>
              <span>Nombre</span>
              <input
                type="text"
                required
                maxLength={100}
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
              />
            </label>
            <label>
              <span>Descripción</span>
              <textarea
                rows={3}
                maxLength={2000}
                value={draftDescription}
                onChange={(event) => setDraftDescription(event.target.value)}
              />
            </label>
            <p className="muted small">
              Se creará un segmento vacío. Añade reglas desde el builder
              después de crearlo.
            </p>
            <div className="form-actions">
              <button type="submit" className="button" disabled={submitting}>
                {submitting ? "Creando…" : "Crear segmento"}
              </button>
            </div>
          </form>
        ) : null}

        {mode === "template" ? (
          pickedTemplate ? (
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
                <span>Nombre para tu segmento</span>
                <input
                  type="text"
                  maxLength={100}
                  value={draftName || pickedTemplate.name}
                  onChange={(event) => setDraftName(event.target.value)}
                />
              </label>
              <div className="form-actions">
                <button
                  type="button"
                  className="button"
                  disabled={submitting}
                  onClick={() =>
                    createFrom(
                      (draftName || pickedTemplate.name).trim(),
                      pickedTemplate.rules,
                      pickedTemplate.color,
                    )
                  }
                >
                  {submitting ? "Creando…" : "Usar esta plantilla"}
                </button>
              </div>
            </div>
          ) : (
            <SegmentTemplateGallery
              onPick={(tmpl) => {
                setPickedTemplate(tmpl);
                setDraftName(tmpl.name);
              }}
              onError={setError}
            />
          )
        ) : null}

        {mode === "ai" ? (
          <SegmentAIGenerator
            onProposal={(result) => {
              setProposal(result);
              setMode("preview");
            }}
          />
        ) : null}

        {mode === "preview" && proposal ? (
          <div className="wizard-preview">
            {proposal.error ? (
              <p className="danger-text">{proposal.error}</p>
            ) : null}
            {proposal.rules ? (
              <>
                <p className="muted">
                  La IA propuso reglas que matchean{" "}
                  <strong>{proposal.count}</strong> contacto
                  {proposal.count === 1 ? "" : "s"}.
                </p>
                <label>
                  <span>Nombre</span>
                  <input
                    type="text"
                    maxLength={100}
                    value={draftName}
                    onChange={(event) => setDraftName(event.target.value)}
                    placeholder="Da un nombre al segmento"
                  />
                </label>
                <ul className="segment-preview-list">
                  {proposal.sample.map((card) => (
                    <li key={card.id}>
                      <strong>
                        {[card.first_name, card.last_name]
                          .filter(Boolean)
                          .join(" ") || "(Sin nombre)"}
                      </strong>
                      <span className="muted small">{card.email}</span>
                    </li>
                  ))}
                </ul>
                <div className="form-actions">
                  <button
                    type="button"
                    className="button"
                    disabled={submitting || !draftName.trim()}
                    onClick={() =>
                      proposal.rules &&
                      createFrom(draftName.trim(), proposal.rules)
                    }
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
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
