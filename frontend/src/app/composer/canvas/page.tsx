"use client";

import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { getDraft, type ComposerCatalog } from "../lib/composerApi";
import { getCatalog } from "../lib/composerApi";
import { useDraftAutosave } from "../lib/useDraftAutosave";

/** Fase-1 placeholder for the canvas editor. Shows the live catalog
 * counts so the operator can confirm the seed ran and exercises the
 * draft round-trip (load + autosave). The full drag-and-drop editor
 * lands in Fase 2.
 */
export default function ComposerCanvasPage() {
  const [catalog, setCatalog] = useState<ComposerCatalog | null>(null);
  const [draftLoadedAt, setDraftLoadedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autosave = useDraftAutosave(5000);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [cat, draft] = await Promise.all([getCatalog(), getDraft()]);
        if (cancelled) return;
        setCatalog(cat);
        setDraftLoadedAt(draft.updated_at);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const pillClass =
    autosave.status === "saving"
      ? "is-saving"
      : autosave.status === "saved"
        ? "is-saved"
        : autosave.status === "error"
          ? "is-error"
          : "";
  const pillLabel =
    autosave.status === "saving"
      ? "Guardando…"
      : autosave.status === "saved"
        ? "Guardado"
        : autosave.status === "error"
          ? `Error: ${autosave.lastError ?? "desconocido"}`
          : draftLoadedAt
            ? `Último: ${new Date(draftLoadedAt).toLocaleString("es-ES")}`
            : "Sin borrador";

  return (
    <>
      <PageHeader
        title="Canvas"
        eyebrow="Composer"
        description="Editor de plantillas. Fase 1: placeholder con autosave activo."
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`composer-autosave-pill ${pillClass}`}>
              {pillLabel}
            </span>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                autosave.schedule({
                  scratch: { touchedAt: new Date().toISOString() },
                })
              }
            >
              <Save size={14} aria-hidden /> Forzar guardado
            </button>
          </div>
        }
      />

      {error ? (
        <div className="composer-placeholder" role="alert">
          <h2>No se pudo cargar el catálogo</h2>
          <p>{error}</p>
        </div>
      ) : catalog === null ? (
        <p>Cargando catálogo…</p>
      ) : (
        <div className="composer-placeholder">
          <h2>El editor llega en Fase 2</h2>
          <p>
            El backend del Composer ya está conectado y el catálogo
            sembrado responde a este usuario.
          </p>
          <p>
            <strong>{catalog.brands.length}</strong> marcas ·{" "}
            <strong>{catalog.products.length}</strong> productos ·{" "}
            <strong>{catalog.prewritten_texts.length}</strong> textos ·{" "}
            <strong>{catalog.composed_blocks.length}</strong> bloques
            compuestos ·{" "}
            <strong>{catalog.standalone_blocks.length}</strong> bloques
            independientes.
          </p>
          <p>
            El botón <em>Forzar guardado</em> escribe un blob mínimo en
            <code> /api/composer/drafts</code> y se programa con un
            debounce de 5 s.
          </p>
        </div>
      )}
    </>
  );
}
