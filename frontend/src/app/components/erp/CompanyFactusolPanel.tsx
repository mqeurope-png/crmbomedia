"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import type { Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolCustomer,
  ERP_EDIT_ROLES,
  fixFactusolCustomerRegime,
  getFactusolPullPreview,
  getFactusolRegimePreview,
  linkFactusolCustomer,
  pullFactusolIntoCompany,
  searchFactusolCustomers,
  type FactusolCustomer,
  type FactusolPullPreview,
  type FactusolRegimePreview,
} from "../../lib/erpApi";

type Diff = { field: string; crm: string; factusol: string };

/** Mismo mapping que `customers.DIFF_FIELDS` del backend (la fuente de verdad
 *  del detector de divergencias); «Traer datos» añade además el país. */
const DIFF_FIELDS: { label: string; crm: keyof Company; fac: keyof FactusolCustomer }[] = [
  { label: "Nombre", crm: "name", fac: "nofcli" },
  { label: "NIF", crm: "tax_id", fac: "nifcli" },
  { label: "Dirección", crm: "address_line", fac: "domcli" },
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
 *  Solo vínculo: si los datos difieren se muestran las diferencias y **nunca se
 *  auto-sincroniza**. Lo que sí hay es «Traer datos de FACTUSOL»: a demanda,
 *  con confirmación (enseña qué cambia), sobrescribe la empresa CRM con los
 *  datos de FACTUSOL (fuente de verdad, decisión de Bart) y deja historial.
 *  Solo escribe en el CRM, jamás en FACTUSOL. */
export function CompanyFactusolPanel({
  company,
  onLinked,
  onPulled,
}: {
  company: Company;
  onLinked?: (codcli: string) => void;
  /** Tras «Traer datos»: el padre recarga la empresa. */
  onPulled?: () => void;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [customer, setCustomer] = useState<FactusolCustomer | null>(null);
  const [diffs, setDiffs] = useState<Diff[] | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // «Traer datos»: previsualización pendiente de confirmar.
  const [pullPreview, setPullPreview] = useState<FactusolPullPreview | null>(null);
  // Tarea C: régimen de IVA / tipo de documento de la ficha F_CLI, pendiente
  // de confirmar su corrección.
  const [regimePreview, setRegimePreview] = useState<FactusolRegimePreview | null>(null);

  const code = company.factusol_company_id;
  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  // Con vínculo: lee el cliente F_CLI por su CÓDIGO (el vínculo) para
  // detectar divergencias.
  useEffect(() => {
    if (!code) return;
    let alive = true;
    searchFactusolCustomers(code, "codcli")
      .then((hits) => {
        if (!alive) return;
        const hit = hits.find((h) => h.codcli === code) ?? hits[0] ?? null;
        setCustomer(hit);
        setDiffs(hit ? diffOf(company, hit) : null);
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [code, company]);

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
        // Tarea C: país real y NIF-IVA → PAICLI + régimen de IVA de la ficha.
        pais: company.country ?? undefined, vat: company.vat ?? undefined,
      });
      setNotice(r.created
        ? `Creado en FACTUSOL con el nº ${r.factusol_codcli}`
          + (r.regime_label ? ` (régimen de IVA: ${r.regime_label}).` : ".")
        : `Ya existía en FACTUSOL (nº ${r.factusol_codcli}) — vinculado.`);
      onLinked?.(r.factusol_codcli);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  /** Tarea C · paso 1: régimen que le toca por país + NIF-IVA vs la ficha F_CLI. */
  async function abrirRegimen() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setRegimePreview(await getFactusolRegimePreview(company.id));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo comprobar el régimen de IVA en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  /** Tarea C · paso 2: confirmar y corregir SOLO las columnas que cambian. */
  async function confirmarRegimen() {
    if (!regimePreview) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fixFactusolCustomerRegime(company.id);
      setRegimePreview(null);
      const cols = Object.keys(r.written).join(", ");
      setNotice(r.changed
        ? `Régimen corregido en FACTUSOL cliente nº ${r.codcli}: ${r.regime_label}`
          + ` (${cols}). Las facturas ya emitidas no cambian.`
        : `La ficha de FACTUSOL nº ${r.codcli} ya estaba bien (${r.regime_label}).`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo corregir el régimen en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  /** Paso 1: qué cambiaría (el backend calcula la diff con el mapping real). */
  async function abrirTraerDatos() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPullPreview(await getFactusolPullPreview(company.id));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo leer el cliente en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  /** Paso 2: confirmar y sobrescribir (solo CRM). */
  async function confirmarTraerDatos() {
    if (!pullPreview) return;
    setBusy(true);
    setError(null);
    try {
      const r = await pullFactusolIntoCompany(company.id);
      setPullPreview(null);
      setShowDiff(false);
      setNotice(
        `Datos traídos de FACTUSOL cliente nº ${r.codcli}: `
        + `${r.applied} campo(s) actualizado(s). FACTUSOL no cambia.`,
      );
      onPulled?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron traer los datos de FACTUSOL."));
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
                {canEdit ? (
                  <>
                    {" "}
                    <button type="button" className="button small" disabled={busy}
                            title="Sobrescribe los datos de la empresa CRM con los de FACTUSOL (pide confirmación; no toca FACTUSOL)"
                            onClick={abrirTraerDatos}>
                      Traer datos de FACTUSOL
                    </button>
                  </>
                ) : null}
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
                versiones pueden ser correctas. «Traer datos de FACTUSOL» pisa
                la del CRM con la de FACTUSOL solo cuando tú lo pidas.
              </p>
            </>
          ) : customer ? (
            <p className="muted small">
              Los datos coinciden con FACTUSOL.
              {canEdit ? (
                <>
                  {" "}
                  <button type="button" className="button small secondary" disabled={busy}
                          title="Vuelve a leer el cliente en FACTUSOL y sobrescribe la empresa CRM (pide confirmación)"
                          onClick={abrirTraerDatos}>
                    Traer datos de FACTUSOL
                  </button>
                </>
              ) : null}
            </p>
          ) : null}
          {canEdit ? (
            <p className="muted small">
              Régimen de IVA en FACTUSOL
              {customer?.regime_label ? <>: <strong>{customer.regime_label}</strong></> : null}
              .{" "}
              <button type="button" className="button small secondary" disabled={busy}
                      title="Comprueba el tipo de documento, el régimen de IVA y el país de la ficha F_CLI frente al país y NIF-IVA de la empresa; pide confirmación antes de corregir en FACTUSOL"
                      onClick={abrirRegimen}>
                Comprobar régimen de IVA
              </button>
            </p>
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

      {pullPreview ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Traer datos de FACTUSOL">
          <div className="modal-dialog">
            <h2>Traer datos de FACTUSOL</h2>
            <p className="form-error" role="alert">
              Esto sobrescribirá los datos de la empresa con los de FACTUSOL
              (cliente nº {pullPreview.codcli}). FACTUSOL es la fuente de verdad:
              se pisan todos los campos del mapping, no solo los vacíos. No se
              cambia nada en FACTUSOL.
            </p>
            {pullPreview.changes.length === 0 ? (
              <p className="muted small">No hay diferencias: nada que traer.</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr><th>Campo</th><th>CRM (ahora)</th><th>FACTUSOL (quedará)</th></tr>
                </thead>
                <tbody>
                  {pullPreview.changes.map((c) => (
                    <tr key={c.field}>
                      <td>{c.label}</td>
                      <td>{c.crm || "—"}</td>
                      <td><strong>{c.factusol || "—"}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={busy}
                      onClick={() => setPullPreview(null)}>
                Cancelar
              </button>
              <button type="button" className="button danger"
                      disabled={busy || pullPreview.changes.length === 0}
                      onClick={confirmarTraerDatos}>
                {busy ? "Trayendo…" : `Sobrescribir con FACTUSOL (${pullPreview.changes.length})`}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {regimePreview ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Régimen de IVA en FACTUSOL">
          <div className="modal-dialog">
            <h2>Régimen de IVA en FACTUSOL</h2>
            <p>
              Por la empresa: <strong>{regimePreview.regime_label}</strong>{" "}
              <span className="muted small">({regimePreview.reason})</span>
            </p>
            <p className="muted small">
              Ficha F_CLI nº {regimePreview.codcli} ahora:{" "}
              {regimePreview.current.regime_label ?? "régimen desconocido"} · tipo de
              documento {regimePreview.current.IFICLI ?? "—"} · país{" "}
              {regimePreview.current.PAICLI || "—"}.
            </p>
            {regimePreview.coherent ? (
              <p className="form-info" role="status">
                La ficha de FACTUSOL ya está bien: nada que corregir.
              </p>
            ) : (
              <>
                <p className="form-error" role="alert">
                  Esto escribirá en FACTUSOL (cliente nº {regimePreview.codcli}) SOLO las
                  columnas de abajo. Las facturas ya emitidas no cambian.
                </p>
                <table className="data-table">
                  <thead>
                    <tr><th>Campo</th><th>FACTUSOL (ahora)</th><th>Quedará</th></tr>
                  </thead>
                  <tbody>
                    {regimePreview.changes.map((c) => (
                      <tr key={c.column}>
                        <td>{c.label} <code>{c.column}</code></td>
                        <td>{c.current_label || "—"}</td>
                        <td><strong>{c.proposed_label}</strong></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={busy}
                      onClick={() => setRegimePreview(null)}>
                {regimePreview.coherent ? "Cerrar" : "Cancelar"}
              </button>
              {!regimePreview.coherent ? (
                <button type="button" className="button danger" disabled={busy}
                        onClick={confirmarRegimen}>
                  {busy ? "Corrigiendo…" : `Corregir en FACTUSOL (${regimePreview.changes.length})`}
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
