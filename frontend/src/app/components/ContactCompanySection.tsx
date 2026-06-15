"use client";

import { Building2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  type Company,
  assignContactCompany,
  getCompany,
} from "../lib/companiesApi";
import { extractErrorMessage } from "../lib/errors";
import { CompanyPickerModal } from "./CompanyPickerModal";

type Props = {
  contactId: string;
  companyId: string | null;
  /** Re-load the parent contact after the assignment changes. */
  onChanged: () => void;
};

export function ContactCompanySection({
  contactId,
  companyId,
  onChanged,
}: Props) {
  const [company, setCompany] = useState<Company | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const load = useCallback(async () => {
    if (!companyId) {
      setCompany(null);
      return;
    }
    setLoading(true);
    try {
      setCompany(await getCompany(companyId));
    } catch {
      setCompany(null);
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onPick = async (newCompanyId: string | null) => {
    try {
      await assignContactCompany(contactId, newCompanyId);
      onChanged();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo asignar la empresa."));
    }
  };

  const addressBits = company
    ? [
        company.address_line,
        company.postal_code,
        company.city,
        company.country,
      ].filter(Boolean)
    : [];

  return (
    <section className="contact-card">
      <h4>Empresa</h4>
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : company ? (
        <div className="contact-company-card">
          <p>
            <Building2 size={12} aria-hidden />{" "}
            <Link href={`/companies/${company.id}`}>
              <strong>{company.name}</strong>
            </Link>
            {company.domain ? (
              <span className="muted small"> ({company.domain})</span>
            ) : null}
          </p>
          {company.tax_id ? (
            <p className="muted small">CIF: {company.tax_id}</p>
          ) : null}
          {addressBits.length > 0 ? (
            <p className="muted small">{addressBits.join(", ")}</p>
          ) : null}
          <div className="form-actions">
            <button
              type="button"
              className="btn small"
              onClick={() => setPickerOpen(true)}
            >
              Cambiar
            </button>
            <button
              type="button"
              className="btn small"
              onClick={() => onPick(null)}
            >
              Quitar
            </button>
          </div>
        </div>
      ) : (
        <div>
          <p className="muted small">Sin empresa asignada.</p>
          <button
            type="button"
            className="btn small"
            onClick={() => setPickerOpen(true)}
          >
            Asignar empresa
          </button>
        </div>
      )}
      <CompanyPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(id) => void onPick(id)}
      />
    </section>
  );
}
