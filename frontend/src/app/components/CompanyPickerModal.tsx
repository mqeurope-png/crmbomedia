"use client";

import { Building2, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type Company,
  createCompany,
  listCompanies,
} from "../lib/companiesApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Called with the picked / created company id (or NULL to clear). */
  onPick: (companyId: string | null, label: string) => void;
};

export function CompanyPickerModal({ open, onClose, onPick }: Props) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [matches, setMatches] = useState<Company[]>([]);
  const [loading, setLoading] = useState(false);
  const [createMode, setCreateMode] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => setDebounced(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q, open]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    listCompanies({ q: debounced || undefined, limit: 10 })
      .then((p) => setMatches(p.items))
      .catch(() => setMatches([]))
      .finally(() => setLoading(false));
  }, [debounced, open]);

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const fresh = await createCompany({
        name: newName.trim(),
        domain: newDomain.trim() || null,
      });
      onPick(fresh.id, fresh.name);
      setCreateMode(false);
      setNewName("");
      setNewDomain("");
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear."));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;
  return (
    <div
      className="email-compose-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-backdrop"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2>Asignar empresa</h2>
        {createMode ? (
          <form onSubmit={onCreate}>
            <label className="field">
              Nombre *
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                autoFocus
              />
            </label>
            <label className="field">
              Dominio
              <input
                type="text"
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                placeholder="bomedia.net"
              />
            </label>
            {error ? <p className="form-error">{error}</p> : null}
            <div className="form-actions">
              <button
                type="button"
                className="btn"
                onClick={() => setCreateMode(false)}
                disabled={busy}
              >
                Volver
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy || !newName.trim()}
              >
                Crear y asignar
              </button>
            </div>
          </form>
        ) : (
          <>
            <label className="field">
              Buscar
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Nombre, dominio, CIF…"
                autoFocus
              />
            </label>
            {loading ? <p className="muted">Buscando…</p> : null}
            {!loading && matches.length === 0 && debounced ? (
              <p className="muted">Ninguna coincidencia.</p>
            ) : null}
            <ul className="company-merge-list">
              {matches.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => {
                      onPick(c.id, c.name);
                      onClose();
                    }}
                  >
                    <Building2 size={11} aria-hidden /> {c.name}
                    {c.domain ? (
                      <span className="muted small"> {c.domain}</span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
            <div className="form-actions">
              <button type="button" className="btn" onClick={onClose}>
                Cancelar
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setCreateMode(true)}
              >
                <Plus size={11} aria-hidden /> Crear nueva
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
