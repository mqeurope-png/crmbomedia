"use client";

import { useState } from "react";
import {
  segmentAIGenerate,
  type SegmentAIGenerateResponse,
} from "../lib/api";

type Props = {
  onProposal: (proposal: SegmentAIGenerateResponse) => void;
};

const SAMPLE_PROMPTS = [
  "Clientes que han comprado en los últimos 30 días",
  "Leads con score > 70 y consentimiento concedido",
  "Contactos inactivos en España",
];

export function SegmentAIGenerator({ onProposal }: Props) {
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!description.trim()) {
      setError("Describe el segmento que necesitas.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await segmentAIGenerate(description.trim());
      onProposal(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Error al generar. Intenta describir el segmento de otra forma.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="ai-generator stacked-form">
      <label>
        <span>Describe el segmento</span>
        <textarea
          rows={3}
          maxLength={2000}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Sector, tipo de cliente, criterios concretos…"
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
    </form>
  );
}
