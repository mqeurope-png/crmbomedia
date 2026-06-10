"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ErrorState } from "../../components/ErrorState";
import { SegmentAIExplainPanel } from "../../components/SegmentAIExplainPanel";
import { SegmentLivePreview } from "../../components/SegmentLivePreview";
import { SegmentRuleBuilder } from "../../components/SegmentRuleBuilder";
import {
  getSegment,
  segmentContacts,
  segmentCount,
  updateSegment,
  type ContactListPage,
  type Segment,
} from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

type Tab = "rules" | "contacts";

export default function SegmentDetailPage() {
  const params = useParams<{ id: string }>();
  const [segment, setSegment] = useState<Segment | null>(null);
  const [draftRules, setDraftRules] = useState<Record<string, unknown>>({});
  const [tab, setTab] = useState<Tab>("rules");
  const [page, setPage] = useState<ContactListPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const fresh = await getSegment(params.id);
      setSegment(fresh);
      setDraftRules(fresh.rules || {});
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el segmento."));
    } finally {
      setIsLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (tab !== "contacts" || !segment) return;
    segmentContacts(segment.id, { limit: 25 })
      .then(setPage)
      .catch((err) =>
        setError(
          extractErrorMessage(err, "No se pudieron cargar los contactos."),
        ),
      );
  }, [tab, segment]);

  async function handleSaveRules() {
    if (!segment) return;
    try {
      const updated = await updateSegment(segment.id, { rules: draftRules });
      setSegment(updated);
      setDraftRules(updated.rules || {});
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el segmento."));
    }
  }

  async function handleForceRefresh() {
    if (!segment) return;
    try {
      const total = await segmentCount(segment.id, true);
      setSegment({
        ...segment,
        cached_count: total.total,
        last_evaluated_at: new Date().toISOString(),
      });
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo refrescar el count."));
    }
  }

  if (isLoading && !segment) {
    return (
      <main className="shell shell-wide">
        <p className="muted">Cargando…</p>
      </main>
    );
  }
  if (error || !segment) {
    return (
      <main className="shell narrow">
        <PageHeader
          title="Segmento"
          crumbs={[{ label: "Segmentos", href: "/segments" }]}
        />
        <ErrorState
          title="No se pudo cargar el segmento"
          message={error ?? "Segmento no encontrado"}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={segment.name}
        eyebrow="Segmento"
        description={segment.description ?? undefined}
        crumbs={[
          { label: "Segmentos", href: "/segments" },
          { label: segment.name },
        ]}
        actions={
          <>
            <span className="muted small">
              {segment.cached_count ?? "?"} contactos · Última evaluación:{" "}
              {segment.last_evaluated_at
                ? new Date(segment.last_evaluated_at).toLocaleString("es-ES")
                : "—"}
            </span>
            <button
              type="button"
              className="button secondary small"
              onClick={handleForceRefresh}
            >
              Refrescar count
            </button>
          </>
        }
      />

      <section className="panel">
        <div className="tab-bar">
          <button
            type="button"
            className={`tab${tab === "rules" ? " is-active" : ""}`}
            onClick={() => setTab("rules")}
          >
            Reglas
          </button>
          <button
            type="button"
            className={`tab${tab === "contacts" ? " is-active" : ""}`}
            onClick={() => setTab("contacts")}
          >
            Contactos
          </button>
        </div>

        {tab === "rules" ? (
          <div className="segment-rules-layout">
            <div className="segment-builder-pane">
              <SegmentRuleBuilder
                initialRules={segment.rules}
                onChange={setDraftRules}
              />
              <div className="form-actions">
                <button
                  type="button"
                  className="button"
                  onClick={handleSaveRules}
                  disabled={!segment.is_owner}
                >
                  Guardar reglas
                </button>
                <SegmentAIExplainPanel rules={draftRules} />
              </div>
            </div>
            <SegmentLivePreview rules={draftRules} />
          </div>
        ) : page ? (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>Email</th>
                  <th>Lead score</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((contact) => (
                  <tr key={contact.id}>
                    <td>
                      <Link href={`/contacts/${contact.id}`}>
                        {[contact.first_name, contact.last_name]
                          .filter(Boolean)
                          .join(" ") || "(Sin nombre)"}
                      </Link>
                    </td>
                    <td>{contact.email}</td>
                    <td>{contact.lead_score ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Cargando contactos…</p>
        )}
      </section>
    </main>
  );
}
