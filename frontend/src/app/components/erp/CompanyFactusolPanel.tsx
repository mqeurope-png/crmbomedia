"use client";

import { useCallback, useEffect, useState } from "react";
import type { Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolCustomer,
  linkFactusolCustomer,
  searchFactusolCustomers,
  type FactusolCustomer,
} from "../../lib/erpApi";

type Diff = { field: string; crm: string; factusol: string };

const DIFF_FIELDS: { label: string; crm: keyof Company; fac: keyof FactusolCustomer }[] = [
  { label: "Nombre", crm: "name", fac: "nomcli" },
  { label: "NIF", crm: "tax_id", fac: "cifcli" },
  { label: "Dirección", crm: "address_line", fac: "dircli" },
  { label: "Ciudad", crm: "city", fac: "pobcli" },
  { label: "CP", crm: "postal_code", fac: "cpocli" },
  { label: "Provincia", crm: "state", fac: "procli" },
];

function diffOf(company: Company, cust: FactusolCustomer): Diff[] {
  return DIFF_FIELDS.flatMap(({ label, crm, fac }) => {
    const a = String(company[crm] ?? "").trim();
    const b = String(cust[fac] ?? "").trim();
    return a.toLowerCase() === b.toLowerCase()
      ? []
      : [{ field: label, crm: a, factusol: b }];
  });
}

/** Sección «FACTUSOL» de la ficha de empresa (C-3).
 *
 *  Solo vínculo: si los datos difieren se muestran las diferencias, pero
 *  **nunca se auto-sincroniza** — ambas versiones pueden ser legítimas y pisar
 *  una perdería información. */
export function CompanyFactusolPanel({
  company,
  onLinked,
}: {
  company: Company;
  onLinked?: (codcli: string) => void;
}) {
  const [customer, setCustomer] = useState<FactusolCustomer | null>(null);
  const [diffs, setDiffs] = useState<Diff[] | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const code = company.factusol_company_id;

  // Con vínculo: carga el cliente para detectar divergencias.
  const load = useCallback(() => {
    if (!code) return;
    searchFactusolCustomers(code, "nif")
      .then((hits) => {
        const hit = hits.find((h) => h.codcli === code) ?? hits[0] ?? null;
        setCustomer(hit);
        setDiffs(hit ? diffOf(company, hit) : null);
      })
      .catch(() => undefined);
  }, [code, company]);

  useEffect(() => {
    if (!code || !company.tax_id) return;
    // El vínculo se busca por NIF (match primario de C-3).
    searchFactusolCustomers(company.tax_id, "nif")
      .then((hits) => {
        const hit = hits.find((h) => h.codcli === code) ?? null;
        setCustomer(hit);
        setDiffs(hit ? diffOf(company, hit) : null);
      })
      .catch(() => undefined);
  }, [code, company, load]);

  async function buscarEnFactusol() {
    if (!company.tax_id) {
      setError("La empresa no tiene NIF: búscala por nombre desde un pedido.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const hits = await searchFactusolCustomers(company.tax_id, "nif");
      if (!hits.length || !hits[0].codcli) {
        setNotice("No está en FACTUSOL. Puedes crearlo.");
        return;
      }
      await linkFactusolCustomer({
        crm_type: "company", crm_id: company.id,
        factusol_codcli: hits[0].codcli,
      });
      setCustomer(hits[0]);
      setNotice(`Vinculado al cliente FACTUSOL nº ${hits[0].codcli}.`);
      onLinked?.(hits[0].codcli);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo buscar en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  async function crearEnFactusol() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await createFactusolCustomer({
        crm_type: "company", crm_id: company.id, nombre: company.name,
        nif: company.tax_id ?? "", direccion: company.address_line ?? "",
        ciudad: company.city ?? "", cp: company.postal_code ?? "",
        provincia: company.state ?? "",
      });
      setNotice(r.created
        ? `Creado en FACTUSOL con el nº ${r.factusol_codcli}.`
        : `Ya existía en FACTUSOL (nº ${r.factusol_codcli}) — vinculado.`);
      onLinked?.(r.factusol_codcli);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="erp-card" aria-label="FACTUSOL">
      <h3>FACTUSOL</h3>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      {code ? (
        <>
          <p>
            <span className="badge ok">Cliente FACTUSOL nº {code}</span>
          </p>
          {diffs && diffs.length > 0 ? (
            <>
              <p className="form-error" role="status">
                Los datos difieren de FACTUSOL.{" "}
                <button type="button" className="button small secondary"
                        onClick={() => setShowDiff((v) => !v)}>
                  {showDiff ? "Ocultar diferencias" : "Ver diferencias"}
                </button>
              </p>
              {showDiff ? (
                <table className="data-table">
                  <thead>
                    <tr><th>Campo</th><th>CRM</th><th>FACTUSOL</th></tr>
                  </thead>
                  <tbody>
                    {diffs.map((d) => (
                      <tr key={d.field}>
                        <td>{d.field}</td>
                        <td><span className="badge muted">CRM</span> {d.crm || "—"}</td>
                        <td><span className="badge active">FACTUSOL</span> {d.factusol || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              <p className="muted small">
                El ERP <strong>no sincroniza automáticamente</strong>: ambas
                versiones pueden ser correctas. Corrige la que proceda en su
                sistema de origen.
              </p>
            </>
          ) : customer ? (
            <p className="muted small">Los datos coinciden con FACTUSOL.</p>
          ) : null}
        </>
      ) : (
        <>
          <p className="muted small">Esta empresa no está vinculada a FACTUSOL.</p>
          <div className="erp-exc-actions">
            <button type="button" className="button small secondary"
                    disabled={busy} onClick={buscarEnFactusol}>
              Buscar en FACTUSOL
            </button>
            <button type="button" className="button small"
                    disabled={busy} onClick={crearEnFactusol}>
              Crear en FACTUSOL
            </button>
          </div>
        </>
      )}
    </section>
  );
}
