"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../components/ErrorState";
import { SegmentWizard } from "../components/SegmentWizard";
import {
  deleteSegment,
  duplicateSegment,
  getHealth,
  listSegments,
  type Segment,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

function relativeDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function SegmentsListPage() {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [aiAvailable, setAiAvailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      setSegments(await listSegments());
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los segmentos."));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    getHealth()
      .then((health) => setAiAvailable(health.ai_features_enabled))
      .catch(() => setAiAvailable(false));
  }, []);

  async function handleDuplicate(segment: Segment) {
    try {
      await duplicateSegment(segment.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo duplicar."));
    }
  }

  async function handleDelete(segment: Segment) {
    if (!window.confirm(`¿Eliminar el segmento "${segment.name}"?`)) return;
    try {
      await deleteSegment(segment.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo eliminar."));
    }
  }

  return (
    <main className="shell shell-wide">
      <Link href="/" className="back-link">
        ← Volver al dashboard
      </Link>
      <section className="hero compact">
        <p className="eyebrow">CRM</p>
        <h1>Segmentos</h1>
        <p className="lead">
          Grupos dinámicos de contactos definidos por reglas booleanas. Se
          re-evalúan solos conforme cambian los datos. Crea desde cero, de
          una plantilla, o deja que la IA traduzca tu descripción.
        </p>
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={() => setWizardOpen(true)}
          >
            + Nuevo segmento
          </button>
        </div>
      </section>

      <section className="panel">
        {error ? <ErrorState title="Error" message={error} /> : null}
        {isLoading && segments.length === 0 ? (
          <p className="muted">Cargando…</p>
        ) : segments.length === 0 ? (
          <p className="muted">
            No hay segmentos todavía. Pulsa &ldquo;Nuevo segmento&rdquo;.
          </p>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Color</th>
                  <th>Nombre</th>
                  <th>Descripción</th>
                  <th>Contactos</th>
                  <th>Última evaluación</th>
                  <th aria-label="Acciones" />
                </tr>
              </thead>
              <tbody>
                {segments.map((segment) => (
                  <tr key={segment.id}>
                    <td>
                      <span
                        className="tag-color-swatch"
                        style={{
                          background: segment.color || "#cdd5e1",
                        }}
                        aria-hidden
                      />
                    </td>
                    <td>
                      <Link href={`/segments/${segment.id}`}>
                        <strong>{segment.name}</strong>
                      </Link>
                      {segment.is_shared ? (
                        <span className="muted small"> · compartido</span>
                      ) : null}
                    </td>
                    <td className="muted">
                      {segment.description || "—"}
                    </td>
                    <td>{segment.cached_count ?? "?"}</td>
                    <td className="muted small">
                      {relativeDate(segment.last_evaluated_at)}
                    </td>
                    <td>
                      <Link
                        href={`/segments/${segment.id}`}
                        className="button secondary small"
                      >
                        Ver
                      </Link>
                      {segment.is_owner ? (
                        <>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => handleDuplicate(segment)}
                          >
                            Duplicar
                          </button>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => handleDelete(segment)}
                          >
                            Borrar
                          </button>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <SegmentWizard
        open={wizardOpen}
        aiAvailable={aiAvailable}
        onCreated={async (segment) => {
          setWizardOpen(false);
          await refresh();
          setSegments((current) =>
            current.some((s) => s.id === segment.id)
              ? current
              : [segment, ...current],
          );
        }}
        onClose={() => setWizardOpen(false)}
      />
    </main>
  );
}
