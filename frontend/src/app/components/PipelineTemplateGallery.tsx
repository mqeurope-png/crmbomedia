"use client";

import { useEffect, useMemo, useState } from "react";
import {
  listPipelineTemplates,
  type PipelineTemplate,
} from "../lib/api";

type Props = {
  onPick: (template: PipelineTemplate) => void;
  onError: (message: string) => void;
};

export function PipelineTemplateGallery({ onPick, onError }: Props) {
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [category, setCategory] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    listPipelineTemplates()
      .then(setTemplates)
      .catch((err) =>
        onError(
          err instanceof Error ? err.message : "No se pudieron cargar las plantillas.",
        ),
      )
      .finally(() => setLoading(false));
  }, [onError]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    templates.forEach((tmpl) => set.add(tmpl.category));
    return Array.from(set).sort();
  }, [templates]);

  const visible = category
    ? templates.filter((tmpl) => tmpl.category === category)
    : templates;

  if (loading) {
    return <p className="muted">Cargando plantillas…</p>;
  }

  return (
    <div className="template-gallery">
      <div className="template-filters">
        <button
          type="button"
          className={`template-filter${!category ? " is-active" : ""}`}
          onClick={() => setCategory("")}
        >
          Todas
        </button>
        {categories.map((cat) => (
          <button
            key={cat}
            type="button"
            className={`template-filter${category === cat ? " is-active" : ""}`}
            onClick={() => setCategory(cat)}
          >
            {cat}
          </button>
        ))}
      </div>
      <div className="template-grid">
        {visible.map((tmpl) => (
          <button
            key={tmpl.id}
            type="button"
            className="template-card"
            onClick={() => onPick(tmpl)}
          >
            <span
              className="template-color"
              style={{ background: tmpl.color || "#cdd5e1" }}
              aria-hidden
            />
            <div className="template-card-body">
              <strong>{tmpl.name}</strong>
              <p className="muted small">{tmpl.description}</p>
              <span className="muted small">
                {tmpl.stages.length} etapas · {tmpl.category}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
