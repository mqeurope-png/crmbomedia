"use client";

// Sprint-Push-CRM-Brevo — UI admin para la tabla owner ↔ lista Brevo.
//
// Por cada user activo del CRM (admin / manager / user) muestra un
// dropdown con las listas Brevo. "Sin asignar" = NULL → el contacto
// del owner no se sube hasta que el admin elija una lista. Botón
// "Refrescar listas" re-fetch /api/brevo/lists. Botón "Guardar"
// hace PUT del array completo. Botón "Backfill manual" encola
// push_contact para todo lo pendiente.

import { ArrowLeft, Play, RefreshCw, Save } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../../components/ErrorState";
import { PageHeader } from "../../../components/PageHeader";
import { getCurrentUser, type User } from "../../../lib/api";
import { listBrevoLists, type BrevoList } from "../../../lib/brevoApi";
import {
  getBrevoUserListMappings,
  putBrevoUserListMappings,
  triggerBrevoBackfillPush,
  type BrevoUserListMappingRow,
} from "../../../lib/brevoPushApi";
import { extractErrorMessage } from "../../../lib/errors";
import {
  listIntegrationAccounts,
  type IntegrationAccount,
} from "../../../lib/integrationSettings";

export default function BrevoMappingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [rows, setRows] = useState<BrevoUserListMappingRow[]>([]);
  const [brevoLists, setBrevoLists] = useState<BrevoList[]>([]);
  const [brevoAccounts, setBrevoAccounts] = useState<IntegrationAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshingLists, setRefreshingLists] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const me = await getCurrentUser();
        if (!mounted) return;
        setUser(me);
        await Promise.all([loadRows(), loadAccounts()]);
      } catch (err) {
        if (!mounted) return;
        setError(
          extractErrorMessage(err, "No se pudo cargar el mapping de listas."),
        );
      } finally {
        if (mounted) setIsLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedAccountId) return;
    void refreshBrevoLists();
  }, [selectedAccountId]);

  async function loadRows() {
    const data = await getBrevoUserListMappings();
    setRows(data.rows);
  }

  async function loadAccounts() {
    const accounts = await listIntegrationAccounts({ system: "brevo" });
    setBrevoAccounts(accounts);
    if (accounts.length && !selectedAccountId) {
      setSelectedAccountId(accounts[0].account_id);
    }
  }

  async function refreshBrevoLists() {
    if (!selectedAccountId) return;
    setRefreshingLists(true);
    try {
      // Cap alto: queremos ofrecer todas las listas mapeables al admin.
      const lists = await listBrevoLists(selectedAccountId, { limit: 200 });
      setBrevoLists(lists);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las listas."));
    } finally {
      setRefreshingLists(false);
    }
  }

  function onChangeRow(userId: string, value: string) {
    const list = brevoLists.find((l) => String(l.id) === value);
    setRows((prev) =>
      prev.map((r) =>
        r.user_id !== userId
          ? r
          : {
              ...r,
              brevo_list_id: list ? list.id : null,
              brevo_list_name: list ? list.name : null,
            },
      ),
    );
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const data = await putBrevoUserListMappings(
        rows.map((r) => ({
          user_id: r.user_id,
          brevo_list_id: r.brevo_list_id,
          brevo_list_name: r.brevo_list_name,
        })),
      );
      setRows(data.rows);
      setMessage("Mapeos guardados. Los próximos cambios de owner aplicarán.");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron guardar los mapeos."));
    } finally {
      setSaving(false);
    }
  }

  async function onBackfill() {
    if (
      !window.confirm(
        "Encola push a Brevo para TODOS los contactos del CRM con owner asignado que aún no estén sincronizados. El job corre en background — la página NO se queda esperando. ¿Continuar?",
      )
    ) {
      return;
    }
    setBackfilling(true);
    setError(null);
    setMessage(null);
    try {
      const res = await triggerBrevoBackfillPush();
      setMessage(
        `Backfill encolado: ${res.queued_count} contactos en cola. ` +
          `Tiempo estimado ~${res.estimated_minutes} min (límite Brevo 400 req/min).`,
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo encolar el backfill."));
    } finally {
      setBackfilling(false);
    }
  }

  const accountOptions = useMemo(
    () =>
      brevoAccounts.map((a) => ({
        value: a.account_id,
        label: `${a.display_name} (${a.account_id})`,
      })),
    [brevoAccounts],
  );

  if (!isAdmin && !isLoading) {
    return (
      <main className="shell">
        <PageHeader title="Mapeo listas Brevo" eyebrow="Administración" />
        <ErrorState
          title="Acceso restringido"
          message="Solo los admins pueden configurar el mapeo de listas Brevo."
        />
      </main>
    );
  }

  return (
    <main className="shell">
      <PageHeader
        title="Mapeo de listas Brevo por comercial"
        eyebrow="Administración"
        description="Cuando un contacto del CRM tiene un propietario asignado, se sube a Brevo en la lista indicada. Si cambia de propietario, se mueve de lista automáticamente. Si se le quita el propietario, se desuscribe de la lista (no se borra el contacto en Brevo)."
        actions={
          <div className="header-actions">
            {accountOptions.length > 1 ? (
              <select
                className="select small"
                value={selectedAccountId}
                onChange={(e) => setSelectedAccountId(e.target.value)}
              >
                {accountOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              type="button"
              className="button small secondary"
              onClick={refreshBrevoLists}
              disabled={refreshingLists || !selectedAccountId}
              title="Refrescar la lista de listas desde Brevo"
            >
              <RefreshCw
                size={12}
                aria-hidden
                className={refreshingLists ? "spin" : undefined}
              />{" "}
              Refrescar listas
            </button>
            <button
              type="button"
              className="button small"
              onClick={onSave}
              disabled={saving || isLoading}
            >
              <Save size={12} aria-hidden /> {saving ? "Guardando…" : "Guardar mapeos"}
            </button>
            <button
              type="button"
              className="button small secondary"
              onClick={onBackfill}
              disabled={backfilling}
              title="Encola un push a Brevo para todos los contactos con owner asignado que aún no están sincronizados"
            >
              <Play size={12} aria-hidden /> {backfilling ? "Encolando…" : "Backfill manual"}
            </button>
          </div>
        }
      />

      <p className="muted" style={{ marginBottom: "1rem" }}>
        <Link href="/admin/integrations">
          <ArrowLeft size={12} aria-hidden /> Volver a Integraciones
        </Link>
      </p>

      {isLoading ? <p className="muted">Cargando mapeos…</p> : null}
      {error ? <ErrorState title="Error" message={error} /> : null}
      {message ? <div className="success-state">{message}</div> : null}

      {!isLoading ? (
        <section>
          <table className="data-table">
            <thead>
              <tr>
                <th>Comercial</th>
                <th>Email</th>
                <th>Lista Brevo</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={3} className="muted">
                    Sin usuarios activos.
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.user_id}>
                    <td>{r.user_full_name}</td>
                    <td className="muted">{r.user_email}</td>
                    <td>
                      <select
                        className="select small"
                        value={
                          r.brevo_list_id !== null ? String(r.brevo_list_id) : ""
                        }
                        onChange={(e) => onChangeRow(r.user_id, e.target.value)}
                        disabled={brevoLists.length === 0}
                      >
                        <option value="">Sin asignar</option>
                        {/* PR-Eg: si el mapping apunta a una lista que ya no
                            existe en Brevo (admin la borró desde su panel),
                            la incluimos como opción huérfana para que el
                            admin la vea + elija una válida. */}
                        {r.brevo_list_id !== null &&
                        !brevoLists.some((l) => l.id === r.brevo_list_id) ? (
                          <option value={String(r.brevo_list_id)}>
                            ⚠ Lista borrada ({r.brevo_list_id})
                          </option>
                        ) : null}
                        {brevoLists.map((l) => (
                          <option key={l.id} value={String(l.id)}>
                            {l.name} ({l.total_subscribers})
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      ) : null}
    </main>
  );
}
