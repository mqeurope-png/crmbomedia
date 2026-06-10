"use client";

import { useState } from "react";
import { segmentAIExplain } from "../lib/api";

type Props = {
  rules?: Record<string, unknown>;
  segmentId?: string;
  /** When the consumer wraps the panel in a popover/modal, the
   *  parent renders the trigger; this component only owns the
   *  "Explicar con IA" button and the result area. */
  triggerLabel?: string;
};

export function SegmentAIExplainPanel({
  rules,
  segmentId,
  triggerLabel = "Explicar con IA",
}: Props) {
  const [explanation, setExplanation] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleExplain() {
    setLoading(true);
    setError(null);
    try {
      const response = await segmentAIExplain({
        rules,
        segment_id: segmentId,
      });
      setExplanation(response.explanation);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "La IA no pudo explicar este segmento.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="ai-explain-panel">
      <button
        type="button"
        className="button secondary small"
        onClick={handleExplain}
        disabled={loading || (!rules && !segmentId)}
      >
        {loading ? "Pidiendo a la IA…" : `✨ ${triggerLabel}`}
      </button>
      {error ? <p className="danger-text">{error}</p> : null}
      {explanation ? (
        <p className="ai-explain-text">{explanation}</p>
      ) : null}
    </div>
  );
}
