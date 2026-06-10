"use client";

import { Copy, Play, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  deleteBrevoSyncTarget,
  getBrevoWebhookStats,
  listBrevoLists,
  listBrevoSyncTargets,
  runBrevoSyncTarget,
  updateBrevoSyncTarget,
  type BrevoList,
  type BrevoSyncTarget,
  type BrevoWebhookStats,
} from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";
import { BrevoBackfillSection } from "./BrevoBackfillSection";
import { BrevoSyncTargetModal } from "./BrevoSyncTargetModal";
import { ConfirmDialog } from "./ConfirmDialog";

const TARGET_STATUS_LABEL: Record<string, string> = {
  idle: "—",
  running: "Ejecutando…",
  success: "OK",
  partial_error: "Con errores",
  error: "Error",
};

type Props = {
  accountId: string;
  isAdmin: boolean;
};

/**
 * Brevo-specific block inside the expanded integration card: lists,
 * sync targets, webhook config + 24h counters. The generic SyncPanel
 * (sync history + run buttons) renders below it from the parent card.
 */
export function BrevoAccountPanel({ accountId, isAdmin }: Props) {
  const [lists, setLists] = useState<BrevoList[] | null>(null);
  const [targets, setTargets] = useState<BrevoSyncTarget[]>([]);
  const [webhookStats, setWebhookStats] = useState<BrevoWebhookStats | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [modalTarget, setModalTarget] = useState<
    { kind: "create" } | { kind: "edit"; target: BrevoSyncTarget } | null
  >(null);
  const [deleteTarget, setDeleteTarget] = useState<BrevoSyncTarget | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  const webhookUrl =
    typeof window !== "undefined"
      ? `${window.location.origin.replace(":3000", ":8000")}/api/webhooks/brevo`
      : "/api/webhooks/brevo";

  const reload = useCallback(async () => {
    try {
      const [targetRows, stats] = await Promise.all([
        listBrevoSyncTargets(accountId),
        getBrevoWebhookStats(accountId).catch(() => null),
      ]);
      setTargets(targetRows);
      setWebhookStats(stats);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el panel Brevo."));
    }
  }, [accountId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function refreshLists() {
    try {
      setLists(null);
      setLists(await listBrevoLists(accountId));
    } catch (err) {
      setLists([]);
      setError(extractErrorMessage(err, "No se pudieron cargar las listas."));
    }
  }

  async function runTarget(target: BrevoSyncTarget) {
    setError(null);
    setMessage(null);
    try {
      await runBrevoSyncTarget(target.id);
      setMessage(`Sync de "${target.name}" encolado.`);
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo lanzar el sync."));
    }
  }

  async function toggleTarget(target: BrevoSyncTarget) {
    try {
      await updateBrevoSyncTarget(target.id, {
        is_active: !target.is_active,
      });
      await reload();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cambiar el estado."));
    }
  }

  return (
    <div className="brevo-panel">
      {error ? <p className="danger-text">{error}</p> : null}
      {message ? <div className="success-state">{message}</div> : null}

      <section className="brevo-panel-section">
        <header>
          <h3>Listas Brevo</h3>
          <button
            type="button"
            className="button secondary small"
            onClick={refreshLists}
          >
            <RefreshCw size={12} aria-hidden /> Refrescar
          </button>
        </header>
        {lists === null ? (
          <p className="muted small">
            Pulsa Refrescar para consultar las listas de Brevo.
          </p>
        ) : lists.length === 0 ? (
          <p className="muted small">Sin listas en Brevo.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Nombre</th>
                <th>Suscriptores</th>
              </tr>
            </thead>
            <tbody>
              {lists.map((list) => (
                <tr key={list.id}>
                  <td className="muted small">{list.id}</td>
                  <td>{list.name}</td>
                  <td>{list.total_subscribers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="brevo-panel-section">
        <header>
          <h3>Sync targets</h3>
          {isAdmin ? (
            <button
              type="button"
              className="button small"
              onClick={() => setModalTarget({ kind: "create" })}
            >
              + Nuevo target
            </button>
          ) : null}
        </header>
        {targets.length === 0 ? (
          <p className="muted small">
            Sin targets. Un target empuja los contactos de un segmento del CRM
            a una lista Brevo de forma automática.
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Segmento</th>
                <th>Lista</th>
                <th>Último run</th>
                <th>Estado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {targets.map((target) => (
                <tr key={target.id}>
                  <td>
                    <strong>{target.name}</strong>
                    {!target.is_active ? (
                      <span className="muted small"> · pausado</span>
                    ) : null}
                  </td>
                  <td className="muted small">
                    {target.segment_name ?? target.segment_id}
                  </td>
                  <td className="muted small">
                    {target.brevo_list_id ?? "Sin lista"}
                  </td>
                  <td className="muted small">
                    {target.last_run_at
                      ? new Date(target.last_run_at).toLocaleString("es-ES")
                      : "Nunca"}
                  </td>
                  <td>
                    <span
                      className={`status-pill ${
                        target.last_run_status === "success"
                          ? "is-on"
                          : target.last_run_status === "running"
                            ? "is-pending"
                            : "is-off"
                      }`}
                    >
                      {TARGET_STATUS_LABEL[target.last_run_status] ??
                        target.last_run_status}
                    </span>
                  </td>
                  <td>
                    <div className="actions">
                      <button
                        type="button"
                        className="button secondary small"
                        onClick={() => runTarget(target)}
                        title="Ejecutar ahora"
                      >
                        <Play size={11} aria-hidden /> Run
                      </button>
                      {isAdmin ? (
                        <>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() =>
                              setModalTarget({ kind: "edit", target })
                            }
                          >
                            Editar
                          </button>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => toggleTarget(target)}
                          >
                            {target.is_active ? "Desactivar" : "Activar"}
                          </button>
                          <button
                            type="button"
                            className="button secondary small danger-text"
                            onClick={() => setDeleteTarget(target)}
                          >
                            Borrar
                          </button>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="brevo-panel-section">
        <header>
          <h3>Webhooks</h3>
        </header>
        <p className="muted small">
          Configura este endpoint en Brevo (Settings → Webhooks) con todos los
          eventos marcados para alimentar la actividad de email de cada
          contacto:
        </p>
        <div className="brevo-webhook-url">
          <code>{webhookUrl}</code>
          <button
            type="button"
            className="button secondary small"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(webhookUrl);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1500);
              } catch {
                // clipboard unavailable; the operator can select manually
              }
            }}
          >
            <Copy size={11} aria-hidden /> {copied ? "¡Copiada!" : "Copiar URL"}
          </button>
        </div>
        {webhookStats ? (
          <p className="muted small">
            Últimas 24h: <strong>{webhookStats.total}</strong> eventos
            {Object.keys(webhookStats.by_type).length > 0 ? (
              <>
                {" — "}
                {Object.entries(webhookStats.by_type)
                  .map(([type, count]) => `${type.replace("email.", "")}: ${count}`)
                  .join(" · ")}
              </>
            ) : null}
          </p>
        ) : null}
        <p className="muted small">
          Módulo completo en{" "}
          <Link href="/marketing/campaigns">/marketing/campaigns</Link> y{" "}
          <Link href="/marketing/templates">/marketing/templates</Link>.
        </p>
      </section>

      {isAdmin ? (
        <BrevoBackfillSection
          accountId={accountId}
          onError={setError}
          onMessage={setMessage}
        />
      ) : null}

      {modalTarget ? (
        <BrevoSyncTargetModal
          accountId={accountId}
          target={modalTarget.kind === "edit" ? modalTarget.target : null}
          onDone={async () => {
            setModalTarget(null);
            await reload();
          }}
          onClose={() => setModalTarget(null)}
        />
      ) : null}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Borrar sync target"
        message={`¿Borrar el target "${deleteTarget?.name}"? Los contactos ya empujados permanecen en Brevo.`}
        confirmLabel="Borrar"
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await deleteBrevoSyncTarget(deleteTarget.id);
            setDeleteTarget(null);
            await reload();
          } catch (err) {
            setError(extractErrorMessage(err, "No se pudo borrar."));
            setDeleteTarget(null);
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
