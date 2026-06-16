"use client";

import { Plus, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  createBrevoList,
  deleteBrevoList,
  listBrevoLists,
  resolvePrimaryBrevoAccount,
  updateBrevoList,
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
  // Deuda #2: Renombrar/Borrar inline aquí desde que el detalle de
  // /marketing/listas/[id] pasó a ser una redirección pura a /contacts.
  // Estado mínimo: id de la lista en modo "rename" + valor del input.
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [working, setWorking] = useState(false);

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

  async function handleRename(list: BrevoList) {
    if (!accountId || !renameValue.trim() || renameValue.trim() === list.name) {
      setRenamingId(null);
      return;
    }
    setWorking(true);
    try {
      await updateBrevoList(accountId, list.id, { name: renameValue.trim() });
      await load(accountId);
      setRenamingId(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo renombrar."));
    } finally {
      setWorking(false);
    }
  }

  async function handleDelete(list: BrevoList) {
    if (!accountId) return;
    if (
      !confirm(
        `Borrar la lista "${list.name}" de Brevo. Los contactos no se borran, solo la lista. ¿Continuar?`,
      )
    ) {
      return;
    }
    setWorking(true);
    try {
      await deleteBrevoList(accountId, list.id);
      await load(accountId);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
    } finally {
      setWorking(false);
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
                <th style={{ textAlign: "right" }}>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((list) => {
                const isRenaming = renamingId === list.id;
                return (
                  <tr key={list.id}>
                    <td>
                      {isRenaming ? (
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            handleRename(list);
                          }}
                          style={{ display: "flex", gap: 6 }}
                        >
                          <input
                            type="text"
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            maxLength={200}
                            autoFocus
                            disabled={working}
                          />
                          <button
                            type="submit"
                            className="button small"
                            disabled={working || !renameValue.trim()}
                          >
                            Guardar
                          </button>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => setRenamingId(null)}
                            disabled={working}
                          >
                            Cancelar
                          </button>
                        </form>
                      ) : (
                        <Link href={`/marketing/listas/${list.id}`}>
                          {list.name}
                        </Link>
                      )}
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
                      {!isRenaming ? (
                        <div
                          style={{
                            display: "inline-flex",
                            gap: 8,
                            alignItems: "center",
                          }}
                        >
                          <Link
                            href={`/marketing/listas/${list.id}`}
                            className="muted small"
                          >
                            Ver contactos →
                          </Link>
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => {
                              setRenamingId(list.id);
                              setRenameValue(list.name);
                            }}
                            disabled={working}
                          >
                            Renombrar
                          </button>
                          <button
                            type="button"
                            className="button secondary small danger"
                            onClick={() => handleDelete(list)}
                            disabled={working}
                          >
                            Borrar
                          </button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
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
