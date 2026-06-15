"use client";

import { Building2, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import {
  type Company,
  type CompanyListFilters,
  createCompany,
  listCompanies,
} from "../lib/companiesApi";
import { formatBackendDateTime } from "../lib/dates";
import { extractErrorMessage } from "../lib/errors";

const SOURCE_LABELS: Record<string, string> = {
  manual: "Manual",
  brevo: "Brevo",
  agilecrm: "Agile",
  "auto-domain": "Auto (dominio)",
};

export default function CompaniesPage() {
  const [items, setItems] = useState<Company[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [country, setCountry] = useState("");
  const [source, setSource] = useState("");
  const [hasContacts, setHasContacts] = useState<boolean | undefined>(
    undefined,
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDomain, setNewDomain] = useState("");

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const filters: CompanyListFilters = {
        q: debounced || undefined,
        country: country || undefined,
        source: source || undefined,
        has_contacts: hasContacts,
        limit: 100,
      };
      const page = await listCompanies(filters);
      setItems(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar las empresas."));
    } finally {
      setLoading(false);
    }
  }, [debounced, country, source, hasContacts]);

  useEffect(() => {
    void load();
  }, [load]);

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreateBusy(true);
    try {
      await createCompany({
        name: newName.trim(),
        domain: newDomain.trim() || null,
      });
      setCreateOpen(false);
      setNewName("");
      setNewDomain("");
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear la empresa."));
    } finally {
      setCreateBusy(false);
    }
  };

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Empresas"
        eyebrow="CRM"
        description="Empresas vinculadas a contactos."
        actions={
          <button
            type="button"
            className="button"
            onClick={() => setCreateOpen(true)}
          >
            <Plus size={11} aria-hidden /> Nueva empresa
          </button>
        }
      />

      <div className="email-toolbar">
        <div className="email-search">
          <Search size={13} aria-hidden />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar nombre, dominio, CIF…"
            aria-label="Buscar empresas"
          />
        </div>
        <select
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          aria-label="Filtrar por país"
        >
          <option value="">Todos los países</option>
          <option value="España">España</option>
          <option value="Francia">Francia</option>
          <option value="Portugal">Portugal</option>
          <option value="Reino Unido">Reino Unido</option>
        </select>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          aria-label="Filtrar por fuente"
        >
          <option value="">Cualquier fuente</option>
          <option value="manual">Manual</option>
          <option value="brevo">Brevo</option>
          <option value="agilecrm">Agile</option>
          <option value="auto-domain">Auto (dominio)</option>
        </select>
        <select
          value={
            hasContacts === undefined
              ? ""
              : hasContacts
                ? "with"
                : "without"
          }
          onChange={(e) =>
            setHasContacts(
              e.target.value === ""
                ? undefined
                : e.target.value === "with",
            )
          }
          aria-label="Con o sin contactos"
        >
          <option value="">Con / sin contactos</option>
          <option value="with">Solo con contactos</option>
          <option value="without">Solo sin contactos</option>
        </select>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : items.length === 0 ? (
        <p className="muted">
          <Building2 size={14} aria-hidden /> No hay empresas que coincidan.
        </p>
      ) : (
        <>
          <p className="muted small">
            {total} empresa{total === 1 ? "" : "s"} ·{" "}
            {items.length} mostrada{items.length === 1 ? "" : "s"}
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Dominio</th>
                <th>CIF</th>
                <th>País</th>
                <th>Fuente</th>
                <th># Contactos</th>
                <th>Actualizada</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/companies/${c.id}`}>{c.name}</Link>
                  </td>
                  <td className="muted small">{c.domain || "—"}</td>
                  <td className="muted small">{c.tax_id || "—"}</td>
                  <td className="muted small">{c.country || "—"}</td>
                  <td className="muted small">
                    {SOURCE_LABELS[c.source] || c.source}
                  </td>
                  <td>{c.contacts_count}</td>
                  <td className="muted small">
                    {formatBackendDateTime(c.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {createOpen ? (
        <div
          className="email-compose-overlay"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setCreateOpen(false);
          }}
        >
          <form
            className="modal-backdrop"
            onSubmit={onCreate}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h2>Nueva empresa</h2>
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
            <div className="form-actions">
              <button
                type="button"
                className="btn"
                onClick={() => setCreateOpen(false)}
                disabled={createBusy}
              >
                Cancelar
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={createBusy || !newName.trim()}
              >
                Crear
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}
