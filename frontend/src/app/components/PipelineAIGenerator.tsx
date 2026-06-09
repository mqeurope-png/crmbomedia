"use client";

import { useState } from "react";
import {
  generatePipelineWithAI,
  type PipelineProposal,
} from "../lib/api";

type Props = {
  onProposal: (proposal: PipelineProposal, originalDescription: string) => void;
};

const SAMPLE_PROMPTS = [
  "Vendo impresoras UV a empresas textiles en España",
  "Necesito un pipeline para gestionar renovaciones de licencias de software",
  "Proceso de selección de comerciales junior",
];

export function PipelineAIGenerator({ onProposal }: Props) {
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!description.trim()) {
      setError("Describe el caso de uso para que la IA pueda proponer un pipeline.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const proposal = await generatePipelineWithAI(description.trim());
      onProposal(proposal, description.trim());
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Error al generar. Intenta describir tu caso de otra forma, o usa una plantilla.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="ai-generator stacked-form">
      <label>
        <span>Describe tu caso de uso</span>
        <textarea
          rows={4}
          maxLength={2000}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Sector, tipo de cliente, objetivo del pipeline…"
          disabled={submitting}
        />
      </label>
      <div className="ai-generator-samples">
        <span className="muted small">Ejemplos:</span>
        {SAMPLE_PROMPTS.map((sample) => (
          <button
            key={sample}
            type="button"
            className="button secondary small"
            onClick={() => setDescription(sample)}
            disabled={submitting}
          >
            {sample}
          </button>
        ))}
      </div>
      {error ? <p className="danger-text">{error}</p> : null}
      <div className="form-actions">
        <button type="submit" className="button" disabled={submitting}>
          {submitting ? "Generando…" : "✨ Generar propuesta"}
        </button>
      </div>
      {submitting ? (
        <p className="muted small">
          La IA está pensando. Esto puede tardar 5-10 segundos.
        </p>
      ) : null}
    </form>
  );
}
