"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import { WooStoreForm } from "../../../../components/erp/WooStoreForm";
import { WooWebhookModal } from "../../../../components/erp/WooWebhookModal";
import { extractErrorMessage } from "../../../../lib/errors";
import {
  bulkMarkExternallyProcessed,
  createWooStore,
  listWooStores,
  syncWooBackfill,
  testWooStore,
  updateWooStore,
  type WooStore,
  type WooStoreCreate,
} from "../../../../lib/erpApi";

/** «hace X min / X h / X d» a partir de un ISO; «N/A» si no hay valor. */
function timeAgo(iso: string | null): string {
  if (!iso) return "N/A";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "N/A";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 1) return "ahora mismo";
  if (mins < 60) return `hace ${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `hace ${hrs} h`;
  return `hace ${Math.floor(hrs / 24)} d`;
}

export default function WooCommerceAdminPage() {
  const [stores, setStores] = useState<WooStore[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  // Borradores de la fecha de corte por tienda (B-2-fix4).
  const [cutoffDrafts, setCutoffDrafts] = useState<Record<string, string>>({});
  // Tienda cuyo panel de webhook está abierto (B-3).
  const [webhookStore, setWebhookStore] = useState<WooStore | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listWooStores()
      .then(setStores)
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar las tiendas.")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function onCreate(data: WooStoreCreate) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const created = await createWooStore(data);
      // Prueba conexión inmediatamente (no bloquea el guardado; solo avisa).
      try {
        const t = await testWooStore(created.id);
        setNote(t.ok
          ? `Tienda ${created.account_id} creada — conexión OK.`
          : `Tienda ${created.account_id} creada — pero la conexión falló (${t.status ?? "?"}).`);
      } catch {
        setNote(`Tienda ${created.account_id} creada — no se pudo probar la conexión.`);
      }
      setShowForm(false);
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear la tienda."));
    } finally {
      setBusy(false);
    }
  }

  async function onTest(id: string) {
    setTesting(id);
    setError(null);
    setNote(null);
    try {
      const t = await testWooStore(id);
      setNote(t.ok ? "Conexión OK." : `Conexión FALLÓ (${t.status ?? "?"}): ${t.detail ?? ""}`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo probar la conexión."));
    } finally {
      setTesting(null);
    }
  }

  async function onSaveCutoff(store: WooStore) {
    const value = cutoffDrafts[store.id] ?? store.external_cutoff_date ?? "";
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await updateWooStore(store.id, { external_cutoff_date: value || null });
      setNote(value
        ? `Fecha de corte de ${store.account_id} guardada: ${value}.`
        : `Fecha de corte de ${store.account_id} borrada.`);
      setCutoffDrafts((d) => {
        const next = { ...d };
        delete next[store.id];
        return next;
      });
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la fecha de corte."));
    } finally {
      setBusy(false);
    }
  }

  async function onExternalizePrevious(store: WooStore) {
    const cutoff = store.external_cutoff_date;
    if (!cutoff) {
      setError("Guarda primero una fecha de corte para esta tienda.");
      return;
    }
    if (!window.confirm(
      `Marcar como «procesados externamente» todos los pedidos de ` +
      `${store.account_id} anteriores a ${cutoff}? Saldrán de las colas activas.`,
    )) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await bulkMarkExternallyProcessed({
        store_id: store.id, before_date: cutoff,
      });
      setNote(`${r.marked} pedido(s) marcados como externalizados en ${store.account_id}.`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo externalizar los pedidos previos."));
    } finally {
      setBusy(false);
    }
  }

  async function onBackfill(id: string) {
    const since = window.prompt(
      "Sincronizar pedidos desde (YYYY-MM-DD, vacío = todo desde el principio):",
      new Date(Date.now() - 30 * 24 * 3600_000).toISOString().slice(0, 10),
    );
    if (since === null) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await syncWooBackfill(id, since || undefined);
      setNote(r.queued
        ? `Backfill encolado (job ${r.job_id ?? "?"}).`
        : `Backfill ejecutado ya: ${JSON.stringify(r.outcome ?? {})}.`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo lanzar el backfill."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="WooCommerce"
        eyebrow="ERP · Integraciones"
        description="Tiendas conectadas. Consumer Key/Secret se cifran al guardar."
        crumbs={[
          { label: "ERP" }, { label: "Integraciones" }, { label: "WooCommerce" },
        ]}
        actions={
          <button type="button" className="button" onClick={() => setShowForm(true)}>
            + Añadir tienda
          </button>
        }
      />
      {error ? <p className="form-error">{error}</p> : null}
      {note ? <p className="form-success">{note}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : stores.length === 0 ? (
        <p className="muted">Aún no hay tiendas configuradas.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Slug</th><th>Nombre</th><th>Estado</th>
              <th>Último webhook</th><th>Webhooks 24h</th>
              <th>Fecha de corte</th><th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {stores.map((s) => (
              <tr key={s.id}>
                <td><code>{s.account_id}</code></td>
                <td>{s.display_name}</td>
                <td>
                  {s.enabled
                    ? <span className="badge ok">activa</span>
                    : <span className="badge muted">pausada</span>}
                </td>
                <td className="muted small">{timeAgo(s.webhook_summary.last_received_at)}</td>
                <td className="small">
                  {s.webhook_summary.count_24h} recibidos
                  {" · "}
                  {s.webhook_summary.errors_24h > 0
                    ? <span className="badge bad">{s.webhook_summary.errors_24h} errores</span>
                    : <span className="muted">0 errores</span>}
                </td>
                <td>
                  <div className="erp-exc-actions">
                    <input
                      type="date"
                      aria-label={`Fecha de corte ${s.account_id}`}
                      value={cutoffDrafts[s.id] ?? s.external_cutoff_date ?? ""}
                      onChange={(e) => setCutoffDrafts((d) => ({ ...d, [s.id]: e.target.value }))}
                    />
                    <button type="button" className="button small secondary"
                      onClick={() => onSaveCutoff(s)} disabled={busy}>
                      Guardar corte
                    </button>
                    <button type="button" className="button small"
                      onClick={() => onExternalizePrevious(s)}
                      disabled={busy || !s.external_cutoff_date}
                      title={s.external_cutoff_date
                        ? "Marca los pedidos anteriores al corte como externalizados"
                        : "Guarda una fecha de corte primero"}>
                      Externalizar previos
                    </button>
                  </div>
                </td>
                <td>
                  <div className="erp-exc-actions">
                    <button type="button" className="button small secondary"
                      onClick={() => onTest(s.id)} disabled={testing === s.id || busy}>
                      {testing === s.id ? "Probando…" : "Probar conexión"}
                    </button>
                    <button type="button" className="button small secondary"
                      onClick={() => setWebhookStore(s)}>
                      Webhooks
                    </button>
                    <button type="button" className="button small"
                      onClick={() => onBackfill(s.id)} disabled={busy}>
                      Sync backfill
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {showForm ? (
        <WooStoreForm onSubmit={onCreate} onCancel={() => setShowForm(false)} busy={busy} />
      ) : null}
      {webhookStore ? (
        <WooWebhookModal
          storeId={webhookStore.id}
          storeName={webhookStore.display_name}
          onClose={() => { setWebhookStore(null); load(); }}
        />
      ) : null}
    </main>
  );
}
