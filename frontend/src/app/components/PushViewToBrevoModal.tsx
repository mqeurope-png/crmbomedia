"use client";

import { useEffect, useMemo, useState } from "react";
import {
  listBrevoLists,
  resolvePrimaryBrevoAccount,
  type BrevoList,
} from "../lib/brevoApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  viewName: string;
  contactsCount: number;
  /** Submit handler — resolves to the count of contacts that landed
   * in the queue so the parent can show a confirmation toast. */
  onSubmit: (payload: {
    brevo_account_id: string;
    brevo_list_id?: number;
    new_list_name?: string;
  }) => Promise<void>;
  onClose: () => void;
};

/** Modal asking the operator which Brevo list to push the current
 * view's contacts to. They pick an existing list OR type a new name;
 * the parent calls the push endpoint with exactly-one-of. */
export function PushViewToBrevoModal({
  viewName,
  contactsCount,
  onSubmit,
  onClose,
}: Props) {
  const [accountId, setAccountId] = useState<string | null>(null);
  const [lists, setLists] = useState<BrevoList[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedListId, setSelectedListId] = useState<number | null>(null);
  const [createNew, setCreateNew] = useState(false);
  const [newListName, setNewListName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (account) {
          const rows = await listBrevoLists(account);
          setLists(rows);
        }
      })
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar Brevo.")),
      )
      .finally(() => setLoading(false));
  }, []);

  const sortedLists = useMemo(
    () =>
      [...lists].sort((a, b) =>
        a.name.localeCompare(b.name, "es", { sensitivity: "base" }),
      ),
    [lists],
  );

  const canSubmit =
    !!accountId &&
    !submitting &&
    ((createNew && newListName.trim().length > 0) ||
      (!createNew && selectedListId !== null));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!accountId || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(
        createNew
          ? { brevo_account_id: accountId, new_list_name: newListName.trim() }
          : { brevo_account_id: accountId, brevo_list_id: selectedListId! },
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar a Brevo."));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <header>
          <h2>Enviar contactos a lista Brevo</h2>
          <p className="muted small">
            Vista: <strong>{viewName}</strong> · {contactsCount} contacto
            {contactsCount === 1 ? "" : "s"}
          </p>
        </header>
        {error ? <p className="form-error">{error}</p> : null}
        {loading ? (
          <p className="muted">Cargando listas Brevo…</p>
        ) : !accountId ? (
          <p className="muted">
            No hay cuenta Brevo configurada en /admin/integrations.
          </p>
        ) : (
          <form onSubmit={handleSubmit}>
            <fieldset>
              <legend className="muted small">Destino</legend>
              <label className="field-inline">
                <input
                  type="radio"
                  name="destination"
                  checked={!createNew}
                  onChange={() => setCreateNew(false)}
                />
                Lista existente
              </label>
              <select
                value={selectedListId ?? ""}
                onChange={(e) =>
                  setSelectedListId(
                    e.target.value ? Number(e.target.value) : null,
                  )
                }
                disabled={createNew}
                required={!createNew}
              >
                <option value="">Selecciona una lista…</option>
                {sortedLists.map((list) => (
                  <option key={list.id} value={list.id}>
                    {list.name} ({list.total_subscribers.toLocaleString("es-ES")})
                  </option>
                ))}
              </select>
              <label className="field-inline">
                <input
                  type="radio"
                  name="destination"
                  checked={createNew}
                  onChange={() => setCreateNew(true)}
                />
                Crear lista nueva
              </label>
              <input
                type="text"
                placeholder="Nombre de la lista nueva"
                value={newListName}
                onChange={(e) => setNewListName(e.target.value)}
                disabled={!createNew}
                maxLength={200}
                required={createNew}
              />
            </fieldset>
            <div className="actions">
              <button
                type="button"
                className="button secondary"
                onClick={onClose}
                disabled={submitting}
              >
                Cancelar
              </button>
              <button type="submit" className="button" disabled={!canSubmit}>
                {submitting ? "Enviando…" : "Enviar"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
