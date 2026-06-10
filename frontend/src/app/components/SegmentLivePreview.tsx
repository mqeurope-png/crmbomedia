"use client";

import { useEffect, useState } from "react";
import {
  previewSegmentRules,
  type SegmentPreviewContactCard,
} from "../lib/api";

type Props = {
  rules: Record<string, unknown>;
};

/**
 * Debounced live preview. The builder pushes new trees on every
 * change; we wait 500ms after the last edit before hitting the
 * backend so a click-storm doesn't produce 12 SQL plans.
 */
export function SegmentLivePreview({ rules }: Props) {
  const [count, setCount] = useState<number | null>(null);
  const [sample, setSample] = useState<SegmentPreviewContactCard[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!rules || Object.keys(rules).length === 0) {
      setCount(null);
      setSample([]);
      setError(null);
      return;
    }
    const handle = window.setTimeout(async () => {
      setLoading(true);
      try {
        const result = await previewSegmentRules(rules);
        setCount(result.count);
        setSample(result.sample);
        setError(null);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "No se pudo evaluar la regla.",
        );
        setCount(null);
        setSample([]);
      } finally {
        setLoading(false);
      }
    }, 500);
    return () => window.clearTimeout(handle);
  }, [rules]);

  return (
    <aside className="segment-preview">
      <h3>Vista previa</h3>
      {loading ? (
        <p className="muted small">Evaluando…</p>
      ) : error ? (
        <p className="danger-text">{error}</p>
      ) : count === null ? (
        <p className="muted small">Añade una regla para previsualizar.</p>
      ) : (
        <>
          <p>
            <strong>{count}</strong> contacto{count === 1 ? "" : "s"} match
            {count === 1 ? "ea" : "ean"} esta regla.
          </p>
          {sample.length > 0 ? (
            <ul className="segment-preview-list">
              {sample.map((card) => (
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
          ) : null}
        </>
      )}
    </aside>
  );
}
