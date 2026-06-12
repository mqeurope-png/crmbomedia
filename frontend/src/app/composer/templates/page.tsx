"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { listTemplates, type ComposerTemplate } from "../lib/composerApi";

export default function ComposerTemplatesPage() {
  const [templates, setTemplates] = useState<ComposerTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const rows = await listTemplates();
      setTemplates(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Plantillas"
        eyebrow="Composer"
        description="Plantillas globales y propias devueltas por /api/composer/templates."
        actions={
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void load()}
            disabled={refreshing}
          >
            <RefreshCw size={14} aria-hidden /> Refrescar
          </button>
        }
      />

      {error ? (
        <div className="composer-placeholder" role="alert">
          <h2>No se pudieron cargar las plantillas</h2>
          <p>{error}</p>
        </div>
      ) : templates === null ? (
        <p>Cargando plantillas…</p>
      ) : templates.length === 0 ? (
        <div className="composer-placeholder">
          <h2>Aún no hay plantillas</h2>
          <p>
            Crea la primera desde el Canvas (Fase 2) o ejecuta el seed
            con <code>scripts/seed_composer_catalog.py</code>.
          </p>
        </div>
      ) : (
        <div className="composer-templates-list">
          {templates.map((tpl) => (
            <article key={tpl.id} className="composer-template-card">
              <h3>{tpl.name}</h3>
              <div className="badge-row">
                {tpl.is_global ? (
                  <span className="badge">Global</span>
                ) : (
                  <span className="badge">Propia</span>
                )}
                {tpl.brand_id ? (
                  <span className="badge">{tpl.brand_id}</span>
                ) : null}
              </div>
              {tpl.description ? <p className="meta">{tpl.description}</p> : null}
              <p className="meta">
                {/* The 20 seeded templates carry their structure in
                    `compositor_blocks_json`, not `blocks_json`. The
                    `blocks` array is for legacy templates that store
                    catalog item ids only; the inspector + canvas
                    hidrate from `compositor_blocks` when it exists. */}
                {(tpl.compositor_blocks?.length ?? tpl.blocks.length)} bloque
                {(tpl.compositor_blocks?.length ?? tpl.blocks.length) === 1
                  ? ""
                  : "s"}{" "}
                · Actualizada{" "}
                {new Date(tpl.updated_at).toLocaleDateString("es-ES")}
              </p>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
