"use client";

import { useEffect, useState } from "react";
import { listSegments, type Segment } from "../lib/api";
import {
  createBrevoSyncTarget,
  listBrevoLists,
  runBrevoSyncTarget,
  updateBrevoSyncTarget,
  type BrevoList,
  type BrevoSyncTarget,
} from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  accountId: string;
  /** null → create. */
  target: BrevoSyncTarget | null;
  onDone: () => void | Promise<void>;
  onClose: () => void;
};

export function BrevoSyncTargetModal({
  accountId,
  target,
  onDone,
  onClose,
}: Props) {
  const [name, setName] = useState(target?.name ?? "");
  const [description, setDescription] = useState(target?.description ?? "");
  const [segmentId, setSegmentId] = useState(target?.segment_id ?? "");
  const [listId, setListId] = useState(target?.brevo_list_id ?? "");
  const [direction, setDirection] = useState(
    target?.sync_direction ?? "push_only",
  );
  const [autoSync, setAutoSync] = useState(target?.auto_sync_enabled ?? true);
  const [interval, setIntervalMinutes] = useState(
    target?.sync_interval_minutes ?? 60,
  );
  const [segments, setSegments] = useState<Segment[]>([]);
  const [lists, setLists] = useState<BrevoList[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listSegments()
      .then(setSegments)
      .catch(() => setSegments([]));
    listBrevoLists(accountId)
      .then(setLists)
      .catch(() => setLists([]));
  }, [accountId]);

  const selectedSegment = segments.find((s) => s.id === segmentId);

  async function save(runAfter: boolean) {
    setBusy(true);
    setError(null);
    try {
      let saved: BrevoSyncTarget;
      if (target) {
        saved = await updateBrevoSyncTarget(target.id, {
          name,
          description: description || null,
          segment_id: segmentId,
          brevo_list_id: listId || null,
          sync_direction: direction,
          auto_sync_enabled: autoSync,
          sync_interval_minutes: interval,
        });
      } else {
        saved = await createBrevoSyncTarget({
          brevo_account_id: accountId,
          name,
          description: description || null,
          segment_id: segmentId,
          brevo_list_id: listId || null,
          sync_direction: direction,
          auto_sync_enabled: autoSync,
          sync_interval_minutes: interval,
        });
      }
      if (runAfter) {
        await runBrevoSyncTarget(saved.id);
      }
      await onDone();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el target."));
      setBusy(false);
    }
  }

  async function dryRun() {
    setBusy(true);
    setError(null);
    setDryRunResult(null);
    try {
      // Dry runs need a persisted target; create/update first.
      let id = target?.id;
      if (!id) {
        const created = await createBrevoSyncTarget({
          brevo_account_id: accountId,
          name: name || "(sin nombre)",
          segment_id: segmentId,
          brevo_list_id: listId || null,
          sync_direction: direction,
          auto_sync_enabled: false,
          sync_interval_minutes: interval,
        });
        id = created.id;
      }
      const result = await runBrevoSyncTarget(id, { dryRun: true });
      setDryRunResult(result.stats ?? {});
    } catch (err) {
      setError(extractErrorMessage(err, "El dry-run falló."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal>
      <div className="modal-card modal-card-wide">
        <div className="wizard-header">
          <h2>{target ? "Editar sync target" : "Nuevo sync target"}</h2>
          <button
            type="button"
            className="button secondary small"
            onClick={onClose}
          >
            Cerrar
          </button>
        </div>

        {error ? <p className="danger-text">{error}</p> : null}

        <div className="stacked-form">
          <label>
            <span>Nombre</span>
            <input
              type="text"
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Clientes ES con consentimiento"
            />
          </label>
          <label>
            <span>Descripción</span>
            <textarea
              rows={2}
              maxLength={2000}
              value={description ?? ""}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <label>
            <span>Segmento origen</span>
            <select
              value={segmentId}
              onChange={(event) => setSegmentId(event.target.value)}
            >
              <option value="">— elige segmento —</option>
              {segments.map((segment) => (
                <option key={segment.id} value={segment.id}>
                  {segment.name}
                </option>
              ))}
            </select>
            {selectedSegment ? (
              <span className="muted small">
                {selectedSegment.cached_count ?? "?"} contactos cumplen
                actualmente.
              </span>
            ) : null}
          </label>
          <label>
            <span>Lista Brevo destino</span>
            <select
              value={listId ?? ""}
              onChange={(event) => setListId(event.target.value)}
            >
              <option value="">Sin lista (solo crear/actualizar contactos)</option>
              {lists.map((list) => (
                <option key={list.id} value={String(list.id)}>
                  {list.name} ({list.total_subscribers})
                </option>
              ))}
            </select>
          </label>
          <div className="radio-row">
            <label className="checkbox">
              <input
                type="radio"
                name="direction"
                checked={direction === "push_only"}
                onChange={() => setDirection("push_only")}
              />
              <span>Solo push (CRM → Brevo)</span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="direction"
                checked={direction === "bidirectional"}
                onChange={() => setDirection("bidirectional")}
              />
              <span>Bidireccional (prioridad CRM)</span>
            </label>
          </div>
          <div className="radio-row">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={autoSync}
                onChange={(event) => setAutoSync(event.target.checked)}
              />
              <span>Auto-sync</span>
            </label>
            <label>
              <span className="muted small">Intervalo (min)</span>
              <input
                type="number"
                min={5}
                max={1440}
                value={interval}
                disabled={!autoSync}
                onChange={(event) =>
                  setIntervalMinutes(Number(event.target.value) || 60)
                }
                style={{ width: 90 }}
              />
            </label>
          </div>
        </div>

        {dryRunResult ? (
          <div className="panel">
            <h3>Dry-run</h3>
            <p className="muted small">
              Matchean: <strong>{String(dryRunResult.matched ?? 0)}</strong> ·
              Saldrían de la lista:{" "}
              <strong>{String(dryRunResult.removed_from_list ?? 0)}</strong>
            </p>
            {Array.isArray(dryRunResult.would_push) &&
            dryRunResult.would_push.length > 0 ? (
              <p className="muted small">
                Se empujarían:{" "}
                {(dryRunResult.would_push as string[]).slice(0, 10).join(", ")}
                {(dryRunResult.would_push as string[]).length > 10 ? "…" : ""}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="form-actions">
          <button
            type="button"
            className="button"
            disabled={busy || !name.trim() || !segmentId}
            onClick={() => save(true)}
          >
            {busy ? "Guardando…" : "Crear y ejecutar"}
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={busy || !name.trim() || !segmentId}
            onClick={() => save(false)}
          >
            Solo guardar
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={busy || !segmentId}
            onClick={dryRun}
          >
            Probar (dry-run)
          </button>
        </div>
      </div>
    </div>
  );
}
