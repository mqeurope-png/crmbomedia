"use client";

import { Building2, Save, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import {
  type Company,
  type CompanyContact,
  type CompanyWrite,
  deleteCompany,
  getCompany,
  listCompanies,
  listCompanyContacts,
  mergeCompanies,
  updateCompany,
} from "../../lib/companiesApi";
import { formatBackendDateTime } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";

type Tab = "data" | "contacts";

export default function CompanyDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [company, setCompany] = useState<Company | null>(null);
  const [contacts, setContacts] = useState<CompanyContact[]>([]);
  const [tab, setTab] = useState<Tab>("data");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, list] = await Promise.all([
        getCompany(params.id),
        listCompanyContacts(params.id),
      ]);
      setCompany(c);
      setContacts(list);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar la empresa."));
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!company) return;
    setSaving(true);
    const payload: CompanyWrite = {
      name: company.name,
      website: company.website,
      domain: company.domain,
      tax_id: company.tax_id,
      vat: company.vat,
      country: company.country,
      region: company.region,
      state: company.state,
      city: company.city,
      address_line: company.address_line,
      postal_code: company.postal_code,
      sector: company.sector,
      size_category: company.size_category,
      notes: company.notes,
      source: company.source,
    };
    try {
      const updated = await updateCompany(company.id, payload);
      setCompany(updated);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar."));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!company) return;
    if (
      !confirm(
        `¿Borrar la empresa "${company.name}"? Los contactos quedarán sin asignación.`,
      )
    )
      return;
    try {
      await deleteCompany(company.id);
      router.push("/companies");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
    }
  };

  if (loading) return <main className="shell"><p className="muted">Cargando…</p></main>;
  if (error || !company)
    return (
      <main className="shell"><p className="form-error">{error}</p></main>
    );

  const onChange = <K extends keyof Company>(
    key: K,
    value: Company[K],
  ) => setCompany((prev) => (prev ? { ...prev, [key]: value } : prev));

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={company.name}
        eyebrow="Empresa"
        crumbs={[
          { label: "Empresas", href: "/companies" },
          { label: company.name },
        ]}
        actions={
          <>
            <button
              type="button"
              className="button small secondary"
              onClick={() => setMergeOpen(true)}
            >
              Fusionar
            </button>
            <button
              type="button"
              className="button small secondary"
              onClick={onDelete}
            >
              <Trash2 size={11} aria-hidden /> Borrar
            </button>
          </>
        }
      />

      <div className="tab-bar">
        <button
          type="button"
          className={`tab${tab === "data" ? " is-active" : ""}`}
          onClick={() => setTab("data")}
        >
          <Building2 size={12} aria-hidden /> Datos
        </button>
        <button
          type="button"
          className={`tab${tab === "contacts" ? " is-active" : ""}`}
          onClick={() => setTab("contacts")}
        >
          <Users size={12} aria-hidden /> Contactos ({contacts.length})
        </button>
      </div>

      {tab === "data" ? (
        <form className="company-edit-form" onSubmit={onSave}>
          <div className="form-grid">
            <label className="field">
              Nombre
              <input
                type="text"
                value={company.name}
                onChange={(e) => onChange("name", e.target.value)}
                required
              />
            </label>
            <label className="field">
              Sitio web
              <input
                type="text"
                value={company.website ?? ""}
                onChange={(e) => onChange("website", e.target.value || null)}
                placeholder="https://bomedia.net"
              />
            </label>
            <label className="field">
              Dominio
              <input
                type="text"
                value={company.domain ?? ""}
                onChange={(e) => onChange("domain", e.target.value || null)}
                placeholder="bomedia.net"
              />
            </label>
            <label className="field">
              CIF
              <input
                type="text"
                value={company.tax_id ?? ""}
                onChange={(e) => onChange("tax_id", e.target.value || null)}
              />
            </label>
            <label className="field">
              VAT
              <input
                type="text"
                value={company.vat ?? ""}
                onChange={(e) => onChange("vat", e.target.value || null)}
              />
            </label>
            <label className="field">
              Sector
              <input
                type="text"
                value={company.sector ?? ""}
                onChange={(e) => onChange("sector", e.target.value || null)}
              />
            </label>
            <label className="field">
              País
              <input
                type="text"
                value={company.country ?? ""}
                onChange={(e) => onChange("country", e.target.value || null)}
              />
            </label>
            <label className="field">
              Región
              <input
                type="text"
                value={company.region ?? ""}
                onChange={(e) => onChange("region", e.target.value || null)}
              />
            </label>
            <label className="field">
              Provincia
              <input
                type="text"
                value={company.state ?? ""}
                onChange={(e) => onChange("state", e.target.value || null)}
              />
            </label>
            <label className="field">
              Ciudad
              <input
                type="text"
                value={company.city ?? ""}
                onChange={(e) => onChange("city", e.target.value || null)}
              />
            </label>
            <label className="field">
              Dirección
              <input
                type="text"
                value={company.address_line ?? ""}
                onChange={(e) =>
                  onChange("address_line", e.target.value || null)
                }
              />
            </label>
            <label className="field">
              Código postal
              <input
                type="text"
                value={company.postal_code ?? ""}
                onChange={(e) =>
                  onChange("postal_code", e.target.value || null)
                }
              />
            </label>
          </div>
          <label className="field">
            Notas
            <textarea
              rows={5}
              value={company.notes ?? ""}
              onChange={(e) => onChange("notes", e.target.value || null)}
            />
          </label>
          <p className="muted small">
            Fuente: {company.source} · Actualizada{" "}
            {formatBackendDateTime(company.updated_at)}
          </p>
          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={saving}
            >
              <Save size={11} aria-hidden /> {saving ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </form>
      ) : null}

      {tab === "contacts" ? (
        contacts.length === 0 ? (
          <p className="muted">No hay contactos vinculados.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Email</th>
                <th>Teléfono</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {contacts.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/contacts/${c.id}`}>
                      {c.first_name} {c.last_name ?? ""}
                    </Link>
                  </td>
                  <td className="muted small">{c.email || "—"}</td>
                  <td className="muted small">{c.phone || "—"}</td>
                  <td className="muted small">{c.commercial_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}

      {mergeOpen ? (
        <MergeDialog
          source={company}
          onClose={() => setMergeOpen(false)}
          onMerged={(target) => {
            setMergeOpen(false);
            router.push(`/companies/${target.id}`);
          }}
        />
      ) : null}
    </main>
  );
}

function MergeDialog({
  source,
  onClose,
  onMerged,
}: {
  source: Company;
  onClose: () => void;
  onMerged: (target: Company) => void;
}) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [matches, setMatches] = useState<Company[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (!debounced) {
      setMatches([]);
      return;
    }
    listCompanies({ q: debounced, limit: 10 })
      .then((p) => setMatches(p.items.filter((c) => c.id !== source.id)))
      .catch(() => setMatches([]));
  }, [debounced, source.id]);

  const onPick = async (target: Company) => {
    if (
      !confirm(
        `Fusionar "${source.name}" en "${target.name}"? Los contactos de "${source.name}" pasarán a "${target.name}" y "${source.name}" se borrará.`,
      )
    )
      return;
    setBusy(true);
    try {
      const merged = await mergeCompanies(source.id, target.id);
      onMerged(merged);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo fusionar."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="email-compose-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-backdrop" onMouseDown={(e) => e.stopPropagation()}>
        <h2>Fusionar &quot;{source.name}&quot; con otra empresa</h2>
        <p className="muted small">
          Busca la empresa destino. Los contactos de &quot;{source.name}&quot;
          se reasignarán y &quot;{source.name}&quot; se borrará.
        </p>
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
        {error ? <p className="form-error">{error}</p> : null}
        <ul className="company-merge-list">
          {matches.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="btn small"
                onClick={() => onPick(c)}
                disabled={busy}
              >
                {c.name}
                <span className="muted small"> {c.domain || c.tax_id || ""}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}
