"use client";

import { Plus, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  createBrevoList,
  listBrevoLists,
  resolvePrimaryBrevoAccount,
  type BrevoList,
} from "../../lib/brevoApi";
import { extractErrorMessage } from "../../lib/errors";

export default function MarketingListsPage() {
  const [accountId, setAccountId] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);
  const [lists, setLists] = useState<BrevoList[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async (account: string) => {
    try {
      const rows = await listBrevoLists(account);
      setLists(rows);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las listas."));
    }
  }, []);

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (account) await load(account);
      })
      .catch(() => setError("No se pudo resolver la cuenta Brevo."))
      .finally(() => {
        setResolved(true);
        setIsLoading(false);
      });
  }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return lists;
    return lists.filter((l) => l.name.toLowerCase().includes(needle));
  }, [lists, query]);

  async function handleCreate(payload: { name: string }) {
    if (!accountId) return;
    try {
      await createBrevoList(accountId, payload);
      await load(accountId);
      setShowCreate(false);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la lista."));
    }
  }

  if (isLoading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Listas Brevo" eyebrow="Marketing" />
        <p className="muted">Cargando…</p>
      </main>
    );
  }

  if (resolved && !accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Listas Brevo" eyebrow="Marketing" />
        <ErrorState
          title="Brevo no configurado"
          message="Configura una cuenta Brevo en /admin/integrations para gestionar listas."
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Listas Brevo"
        eyebrow="Marketing"
        description="Gestiona listas de contactos directamente en Brevo desde el CRM."
        actions={
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              onClick={async () => {
                if (!accountId) return;
                setRefreshing(true);
                await load(accountId);
                setRefreshing(false);
              }}
              disabled={refreshing}
            >
              <RefreshCw size={14} aria-hidden /> Refrescar
            </button>
            <button
              type="button"
              className="button"
              onClick={() => setShowCreate(true)}
            >
              <Plus size={14} aria-hidden /> Nueva lista
            </button>
          </div>
        }
      />

      {error ? (
        <div className="form-error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="card" style={{ marginBottom: 16 }}>
        <label className="field-inline">
          <Search size={14} aria-hidden />
          <input
            type="search"
            placeholder="Buscar por nombre…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>

      {filtered.length === 0 ? (
        <p className="muted">
          {query
            ? "Ninguna lista coincide con la búsqueda."
            : "Aún no hay listas en Brevo."}
        </p>
      ) : (
        <article className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th style={{ textAlign: "right" }}>Suscriptores</th>
                <th style={{ textAlign: "right" }}>Únicos</th>
                <th style={{ textAlign: "right" }}>Blacklist</th>
                <th>Folder</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((list) => (
                <tr key={list.id}>
                  <td>
                    <Link href={`/marketing/listas/${list.id}`}>{list.name}</Link>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {list.total_subscribers.toLocaleString("es-ES")}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {list.unique_subscribers != null
                      ? list.unique_subscribers.toLocaleString("es-ES")
                      : "—"}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {list.total_blacklisted != null
                      ? list.total_blacklisted.toLocaleString("es-ES")
                      : "—"}
                  </td>
                  <td>{list.folder_id ?? "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    <Link
                      href={`/marketing/listas/${list.id}`}
                      className="muted small"
                    >
                      Ver contactos →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      )}

      {showCreate ? (
        <CreateListModal
          onCancel={() => setShowCreate(false)}
          onSubmit={handleCreate}
        />
      ) : null}
    </main>
  );
}

function CreateListModal({
  onSubmit,
  onCancel,
}: {
  onSubmit: (payload: { name: string }) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <h2>Nueva lista Brevo</h2>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim() || submitting) return;
            setSubmitting(true);
            try {
              await onSubmit({ name: name.trim() });
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <label className="field">
            Nombre
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
              maxLength={200}
            />
          </label>
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              onClick={onCancel}
              disabled={submitting}
            >
              Cancelar
            </button>
            <button type="submit" className="button" disabled={submitting || !name.trim()}>
              {submitting ? "Creando…" : "Crear"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
